"""One thin wrapper around the Gemini SDK, with record and replay.

Every call goes through `generate`, `call_tools` or `decide`. Each returns a plain dict
that includes the model, a prompt hash (for the ledger), token usage and latency.
In record mode the dict is saved under replays/<session>/; in replay mode it is read
back in the same order with the same latency, so a demo can run without the network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

from . import config
from .ledger import canonical_json, sha256


class ModelError(RuntimeError):
    pass


class Recorder:
    """Records or replays model responses, keyed by job and call order."""

    def __init__(self):
        self.session: str | None = None
        self.replaying = False
        self.speed = 1.0
        self.counters: dict[str, int] = {}

    def start(self, session: str, replay: bool, speed: float = 1.0):
        self.session, self.replaying, self.speed, self.counters = session, replay, speed, {}

    def _path(self, job: str) -> Path:
        n = self.counters.get(job, 0)
        self.counters[job] = n + 1
        return config.REPLAY_DIR / self.session / f"{job}-{n:03d}.json"

    async def wrap(self, job: str, prompt_hash: str, fn):
        if not self.session:
            return await fn()
        path = self._path(job)
        if self.replaying:
            if not path.exists():
                raise ModelError(f"Replay {self.session} has no recording for {path.name}")
            out = json.loads(path.read_text())
            await asyncio.sleep(out.get("latency_s", 0) / max(self.speed, 0.01))
            out["replayed"] = True
            return out
        out = await fn()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1))
        return out


recorder = Recorder()
_client = None
on_call = None  # set by the app: called with every model response (for the live stage panel)


def _notify(out: dict) -> dict:
    if on_call:
        try:
            on_call(out)
        except Exception:
            pass
    return out



def client():
    global _client
    if _client is None:
        key = config.api_key()
        if not key:
            raise ModelError("GEMINI_API_KEY is not set. Put it in .env (see .env.example).")
        from google import genai
        _client = genai.Client(api_key=key, http_options={"api_version": config.models()["api_version"]})
    return _client


def pdf_part(data: bytes) -> dict:
    return {"type": "document", "data": base64.b64encode(data).decode(), "mime_type": "application/pdf"}


def image_part(png_b64: str, resolution: str = "medium") -> dict:
    return {"type": "image", "data": png_b64, "mime_type": "image/png", "resolution": resolution}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _hash_prompt(model: str, system: str | None, parts: list[dict], extra: Any = None) -> str:
    slim = [{**p, "data": sha256(p["data"])} if "data" in p else p for p in parts]
    return sha256(canonical_json({"model": model, "system": system, "input": slim, "extra": extra}))


def _usage(u) -> dict:
    if u is None:
        return {}
    g = lambda k: getattr(u, k, None) or 0
    return {"input_tokens": g("total_input_tokens"), "output_tokens": g("total_output_tokens"),
            "thought_tokens": g("total_thought_tokens")}


def cost_usd(model: str, usage: dict) -> float:
    p = config.models()["pricing"].get(model)
    if not p:
        return 0.0
    out = usage.get("output_tokens", 0) + usage.get("thought_tokens", 0)
    return round((usage.get("input_tokens", 0) * p["input"] + out * p["output"]) / 1e6, 6)


async def _interaction(job: str, parts: list[dict], system: str | None, **kw) -> dict:
    cfg = config.job(job)
    model = kw.pop("model", None) or cfg["model"]
    gen = {}
    if cfg.get("thinking_level"):
        gen["thinking_level"] = cfg["thinking_level"]
    ph = _hash_prompt(model, system, parts, {k: v for k, v in kw.items()})

    async def run():
        t0 = time.time()
        args = dict(model=model, input=parts, store=False, **kw)
        if system:
            args["system_instruction"] = system
        if gen:
            args["generation_config"] = gen
        last = None
        for attempt in range(3):
            try:
                r = await client().aio.interactions.create(**args)
                break
            except Exception as e:  # transient network or 5xx: retry with backoff
                last = e
                code = getattr(e, "status_code", None) or getattr(e, "code", None)
                if code and int(code) < 500 and int(code) != 429:
                    raise ModelError(f"{model}: {e}") from e
                await asyncio.sleep(1.5 * (attempt + 1))
        else:
            raise ModelError(f"{model}: {last}") from last
        calls = [{"id": s.id, "name": s.name, "args": dict(s.arguments or {})}
                 for s in (r.steps or []) if getattr(s, "type", None) == "function_call"]
        usage = _usage(getattr(r, "usage", None))
        return {"job": job, "model": model, "prompt_hash": ph, "text": getattr(r, "output_text", None) or "",
                "calls": calls, "usage": usage, "cost_usd": cost_usd(model, usage),
                "latency_s": round(time.time() - t0, 3), "status": getattr(r, "status", None)}

    return _notify(await recorder.wrap(job, ph, run))


async def generate(job: str, parts: list[dict], *, system: str | None = None, schema: dict | None = None,
                   model: str | None = None) -> dict:
    """Text or JSON generation. With a schema, out["json"] is the parsed object."""
    kw: dict = {}
    if schema:
        kw["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
    if model:
        kw["model"] = model
    out = await _interaction(job, parts, system, **kw)
    if schema:
        try:
            out["json"] = json.loads(out["text"])
        except json.JSONDecodeError as e:
            raise ModelError(f"{out['model']} returned text that is not valid JSON ({e}); status={out.get('status')}")
    return out


async def call_tools(job: str, parts: list[dict], tools: list[dict], *, system: str | None = None) -> dict:
    """One model turn that must answer with function calls (the navigator)."""
    return await _interaction(job, parts, system, tools=tools)


async def decide(job: str, input_parts: list[dict] | str, questions: dict) -> dict:
    """Decisions API. Returns {"answers": {key: {"type", "p"|"choice"|"score", "confidence", ...}}}."""
    cfg = config.job(job)
    model = cfg["model"]
    parts = input_parts if isinstance(input_parts, list) else [text_part(input_parts)]
    ph = _hash_prompt(model, None, parts, questions)

    async def run():
        t0 = time.time()
        try:
            r = await client().aio.decisions.create(model=model, input=parts, questions=questions)
        except Exception as e:
            raise ModelError(f"{model}: {e}") from e
        answers = {}
        for k, a in (r.answers or {}).items():
            d = {"type": a.type, "confidence": a.confidence}
            if a.type == "noul":
                d["p"] = a.noul
            elif a.type == "choice":
                d["choice"], d["probabilities"] = a.choice, dict(a.probabilities)
            elif a.type == "score":
                d["score"], d["probabilities"] = getattr(a, "score", None), dict(a.probabilities)
            answers[k] = d
        usage = _usage(getattr(r, "usage", None))
        return {"job": job, "model": model, "prompt_hash": ph, "answers": answers, "usage": usage,
                "cost_usd": cost_usd(model, usage), "latency_s": round(time.time() - t0, 3)}

    return _notify(await recorder.wrap(job, ph, run))


def mint_live_token() -> dict:
    """Ephemeral token for the browser's Live session, locked to the voice model."""
    import datetime
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    model = config.job("voice")["model"]
    tok = client().auth_tokens.create(config={
        "uses": 1,
        "expire_time": now + datetime.timedelta(minutes=30),
        "new_session_expire_time": now + datetime.timedelta(minutes=2),
        "live_connect_constraints": {"model": model, "config": {"session_resumption": {}, "response_modalities": ["AUDIO"]}},
    })
    return {"token": tok.name, "model": model, "api_version": config.models()["api_version"]}
