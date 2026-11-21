"""Smoke test for every model in config/models.yaml. Makes one tiny real call per model.

  backend/.venv/bin/python scripts/smoke_models.py

Exits non-zero if any model fails. Writes docs/smoke_result.json.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from relay import config, llm  # noqa: E402


async def text_model(job: str) -> str:
    out = await llm.generate(job, [llm.text_part("Reply with the single word: ready")])
    assert "ready" in out["text"].lower(), out["text"]
    return f"text ok, {out['usage']}"


async def json_model(job: str) -> str:
    schema = {"type": "object", "properties": {"visits": {"type": "array", "items": {"type": "string"}}}, "required": ["visits"]}
    out = await llm.generate(job, [llm.text_part("List these visit ids as JSON: V2.1 and V7.1")], schema=schema)
    assert out["json"]["visits"] == ["V2.1", "V7.1"], out["json"]
    return f"structured output ok, {out['usage']}"


async def pdf_model(job: str) -> str:
    pdf = (config.ASSETS / "protocol.pdf").read_bytes()
    out = await llm.generate(job, [llm.pdf_part(pdf), llm.text_part("What is the protocol number on the title page? Answer with the number only.")])
    assert "CTJ301UC201" in out["text"], out["text"]
    return f"97-page PDF read ok, {out['usage']}"


async def tool_model(job: str) -> str:
    tools = [{"type": "function", "name": "click", "description": "Click an element.",
              "parameters": {"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}}]
    out = await llm.call_tools(job, [llm.text_part("Elements: [button agent_id=save 'Save']. Goal: save the form.")], tools)
    assert out["calls"] and out["calls"][0]["args"].get("agent_id") == "save", out
    return f"function call ok, {out['usage']}"


async def decision_model(job: str) -> str:
    out = await llm.decide(job, "User said: 'Relay, open the new amendment.'", {
        "addressed": {"type": "noul", "instructions": "Is the speaker talking to the assistant called Relay?"},
        "intent": {"type": "choice", "instructions": "What does the speaker want?",
                   "criteria": {"open_amendment": "Open or start the amendment.", "stop": "Stop.", "other": "Anything else."}},
    })
    a = out["answers"]
    assert a["addressed"]["p"] > 0.5 and a["intent"]["choice"] == "open_amendment", a
    return f"noul p={a['addressed']['p']:.2f}, choice={a['intent']['choice']} ({a['intent']['confidence']:.2f})"


async def live_model(job: str) -> str:
    """Mint an ephemeral token, then open a Live session with it and get one audio reply."""
    from google import genai
    tok = llm.mint_live_token()
    c = genai.Client(api_key=tok["token"], http_options={"api_version": tok["api_version"]})
    got = 0
    async with c.aio.live.connect(model=tok["model"], config={"response_modalities": ["AUDIO"]}) as s:
        await s.send_client_content(turns={"role": "user", "parts": [{"text": "Say: ready."}]}, turn_complete=True)
        async for msg in s.receive():
            if msg.data:
                got += len(msg.data)
            if msg.server_content and msg.server_content.turn_complete:
                break
    assert got > 0, "no audio returned"
    return f"ephemeral token + live session ok, {got} bytes of 24 kHz PCM"


CHECKS = [
    ("voice", live_model), ("utterance_gate", decision_model),
    ("protocol_diff", pdf_model), ("planner", json_model),
    ("navigator", tool_model), ("auditor", text_model),
]


async def main() -> int:
    if not config.api_key():
        print("GEMINI_API_KEY is not set. Copy .env.example to .env and add the key.")
        return 2
    results, failed = [], 0
    for job, fn in CHECKS:
        model = config.job(job)["model"]
        t0 = time.time()
        try:
            detail, ok = await fn(job), True
        except Exception as e:
            detail, ok = f"{type(e).__name__}: {e}", False
            failed += 1
        dt = round(time.time() - t0, 2)
        print(f"{'PASS' if ok else 'FAIL'}  {model:<20} {job:<15} {dt:>6}s  {detail}")
        results.append({"job": job, "model": model, "ok": ok, "seconds": dt, "detail": detail})
    (ROOT / "docs" / "smoke_result.json").write_text(json.dumps({"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
