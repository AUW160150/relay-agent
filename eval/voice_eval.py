"""Voice robustness: spoken commands mixed with noise, through the same path the app uses.

Each clip is streamed into a Gemini Live session (which gives the transcript, as in the
app), then the clip and its transcript go to the Decision 1 utterance gate, and the routing
thresholds from config/models.yaml decide: act, ask "Was that for me?", or ignore.

Inputs (see eval/audio/README.md):
  eval/audio/raw/<speaker>/<label>__<anything>.wav   label = an intent name, or "none" for speech
                                                     that is not addressed to the agent
  eval/audio/noise/cafe.wav, eval/audio/noise/talker.wav

  backend/.venv/bin/python eval/voice_eval.py --snr clean,20,10,5,0,-5

Metrics per condition: correct action rate (commands that led to the right intent), false
action rate (non-commands that led to any action), and latency from end of speech to the
routing decision. Writes eval/results/voice.json and voice_curve.png.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import statistics as st
import sys
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "backend"))
from relay import agents, config, llm  # noqa: E402

RATE = 16000
CONTEXT = {"open_amendment": "Role: sponsor. The assistant is waiting for the user to open the new protocol amendment.",
           "choose_option": "Role: sponsor. The assistant is waiting for the user to choose a design option (A, B).",
           "approve": "Role: sponsor. The assistant is waiting for the user to approve the verified build.",
           "default": "Role: sponsor. The assistant is building the casebook in the EDC application."}


def read_wav(path: Path) -> np.ndarray:
    """Mono float32 at 16 kHz. Record at 16 kHz mono to avoid resampling; other rates are linearly resampled."""
    with wave.open(str(path)) as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path}: expected 16-bit PCM")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != RATE:
        x = np.interp(np.arange(0, len(x), sr / RATE), np.arange(len(x)), x).astype(np.float32)
    return x


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2)) + 1e-9)


def mix(speech: np.ndarray, noise: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """Add noise at the given signal-to-noise ratio (speech RMS over noise RMS), with 0.5 s of noise before and after."""
    pad = RATE // 2
    total = len(speech) + 2 * pad
    start = int(rng.integers(0, max(1, len(noise) - total)))
    seg = np.resize(noise[start:start + total], total)
    seg = seg * (rms(speech) / (rms(seg) * 10 ** (snr_db / 20)))
    out = seg.copy()
    out[pad:pad + len(speech)] += speech
    return np.clip(out, -1, 1)


def to_pcm(x: np.ndarray) -> bytes:
    return (x * 32767).astype(np.int16).tobytes()


def to_wav_b64(x: np.ndarray) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(to_pcm(x))
    return base64.b64encode(buf.getvalue()).decode()


async def transcribe(x: np.ndarray) -> tuple[str, float]:
    """Stream the clip in real time into a Live session; return (input transcript, wall time when the audio ended)."""
    from google import genai
    c = genai.Client(api_key=config.api_key(), http_options={"api_version": config.models()["api_version"]})
    text = ""
    cfg = {"response_modalities": ["AUDIO"], "input_audio_transcription": {},
           "system_instruction": "You only listen. Never speak."}
    async with c.aio.live.connect(model=config.job("voice")["model"], config=cfg) as s:
        pcm = to_pcm(x)
        chunk = RATE * 2 // 10  # 100 ms
        for i in range(0, len(pcm), chunk):
            await s.send_realtime_input(audio={"data": pcm[i:i + chunk], "mime_type": f"audio/pcm;rate={RATE}"})
            await asyncio.sleep(0.1)
        ended = time.time()
        await s.send_realtime_input(audio_stream_end=True)
        try:
            async with asyncio.timeout(6):
                async for msg in s.receive():
                    sc = msg.server_content
                    if sc and sc.input_transcription and sc.input_transcription.text:
                        text += sc.input_transcription.text
                    if sc and (sc.turn_complete or sc.generation_complete):
                        break
        except TimeoutError:
            pass
    return text.strip(), ended


async def one(clip: dict, x: np.ndarray, condition: str) -> dict:
    try:
        transcript, ended = await transcribe(x)
        g = await agents.utterance_gate(transcript, to_wav_b64(x), CONTEXT.get(clip["label"], CONTEXT["default"])) if transcript else None
        route = agents.route_utterance(g["answers"]) if g else {"action": "ignore", "intent": None}
        latency = time.time() - ended
    except Exception as e:
        return {**clip, "condition": condition, "error": f"{type(e).__name__}: {e}"}
    is_cmd = clip["label"] != "none"
    return {**clip, "condition": condition, "transcript": transcript, "action": route["action"], "intent": route["intent"],
            "p_addressed": route.get("p_addressed"), "latency_s": round(latency, 2),
            "correct": (route["action"] == "act" and route["intent"] == clip["label"]) if is_cmd else route["action"] == "ignore",
            "false_action": (not is_cmd) and route["action"] == "act"}


async def main(a) -> int:
    if not config.api_key():
        print("GEMINI_API_KEY is not set.")
        return 2
    raw, noise_dir = HERE / "audio" / "raw", HERE / "audio" / "noise"
    clips = [{"speaker": p.parent.name, "label": p.stem.split("__")[0], "file": str(p.relative_to(HERE))} for p in sorted(raw.glob("*/*.wav"))]
    speakers = sorted({c["speaker"] for c in clips})
    if len(speakers) < 3:
        print(f"Found {len(speakers)} speaker(s) in {raw}. At least 3 are needed; see eval/audio/README.md.")
        return 2
    noises = {p.stem: read_wav(p) for p in sorted(noise_dir.glob("*.wav"))}
    if not noises:
        print(f"No noise files in {noise_dir}.")
        return 2
    rng = np.random.default_rng(7)
    rows, sem = [], asyncio.Semaphore(a.parallel)
    conditions = [("clean", None, None)] + [(f"{n} {s:+d} dB", n, s) for s in [int(v) for v in a.snr.split(",") if v != "clean"] for n in noises]

    async def run(clip, cond, noise, snr):
        x = read_wav(HERE / clip["file"])
        y = x if noise is None else mix(x, noises[noise], snr, rng)
        async with sem:
            r = await one(clip, y, cond)
            r.update(noise=noise, snr_db=snr)
            rows.append(r)

    await asyncio.gather(*[run(c, *cond) for cond in conditions for c in clips])
    curve = []
    for cond, noise, snr in conditions:
        rs = [r for r in rows if r["condition"] == cond and "error" not in r]
        cmds, non = [r for r in rs if r["label"] != "none"], [r for r in rs if r["label"] == "none"]
        curve.append({"condition": cond, "noise": noise, "snr_db": snr, "n": len(rs),
                      "correct_action_rate": sum(r["correct"] for r in cmds) / len(cmds) if cmds else None,
                      "false_action_rate": sum(r["false_action"] for r in non) / len(non) if non else None,
                      "median_latency_s": st.median([r["latency_s"] for r in cmds]) if cmds else None})
    breaks = next((c["condition"] for c in sorted([c for c in curve if c["snr_db"] is not None], key=lambda c: -c["snr_db"])
                   if c["correct_action_rate"] is not None and c["correct_action_rate"] < 0.8), None)
    out = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "speakers": len(speakers), "clips": len(clips), "thresholds": config.job("utterance_gate")["thresholds"],
           "curve": curve, "breaks_at": breaks, "errors": sum("error" in r for r in rows), "rows": rows}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "voice.json").write_text(json.dumps(out, indent=1))
    plot(curve, noises)
    print(json.dumps(curve, indent=1))
    return 0


def plot(curve, noises):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    colors = {"cafe": "#2b6cc4", "talker": "#f5921b"}
    for n in noises:
        pts = sorted([c for c in curve if c["noise"] == n], key=lambda c: c["snr_db"])
        ax.plot([c["snr_db"] for c in pts], [100 * (c["correct_action_rate"] or 0) for c in pts], marker="o", color=colors.get(n), label=f"Correct action, {n} noise")
        ax.plot([c["snr_db"] for c in pts], [100 * (c["false_action_rate"] or 0) for c in pts], marker="x", linestyle="--", color=colors.get(n), label=f"False action on non-commands, {n} noise")
    clean = next((c for c in curve if c["noise"] is None), None)
    if clean and clean["correct_action_rate"] is not None:
        ax.axhline(100 * clean["correct_action_rate"], color="#3a9b3a", linewidth=1, label="Correct action, no added noise")
    ax.set_xlabel("Signal-to-noise ratio (dB); lower is noisier"); ax.set_ylabel("Share of clips (%)"); ax.set_ylim(0, 102); ax.invert_xaxis()
    ax.set_title("Voice commands under noise"); ax.grid(alpha=.3); ax.legend(fontsize=7)
    fig.tight_layout(); fig.savefig(HERE / "results" / "voice_curve.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--snr", default="20,10,5,0,-5")
    ap.add_argument("--parallel", type=int, default=3)
    sys.exit(asyncio.run(main(ap.parse_args())))
