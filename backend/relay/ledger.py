"""Append-only audit ledger with a SHA-256 hash chain (ALCOA+ style).

hash = sha256(prev_hash + canonical_json(entry))   where entry excludes "hash".
Entries are never updated or deleted; a correction is a new entry.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
ACTOR_TYPES = ("person", "ai", "rule")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(record: Any) -> str:
    """Hash of a record (for example a casebook version) that a signature binds to."""
    return sha256(canonical_json(record))


def entry_hash(prev_hash: str, entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return sha256(prev_hash + canonical_json(body))


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: list[dict] = []
        self._lock = threading.Lock()
        if path and path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    self.entries.append(json.loads(line))

    @property
    def head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else GENESIS

    def append(self, *, actor_type: str, actor_id: str, role: str, action: str,
               record: str | None = None, before: Any = None, after: Any = None,
               reason: str | None = None, source: dict | None = None,
               model: str | None = None, prompt_hash: str | None = None,
               extra: dict | None = None) -> dict:
        if actor_type not in ACTOR_TYPES:
            raise ValueError(f"actor_type must be one of {ACTOR_TYPES}")
        if actor_type == "ai" and not model:
            raise ValueError("AI entries must name the model")
        with self._lock:
            entry = {
                "seq": len(self.entries) + 1,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "actor_type": actor_type, "actor_id": actor_id, "role": role,
                "action": action, "record": record, "before": before, "after": after,
                "reason": reason, "source": source, "model": model, "prompt_hash": prompt_hash,
                "prev_hash": self.head,
            }
            if extra:
                entry["extra"] = extra
            # Freeze the values now. Callers pass live objects (a form that later gains items, for
            # example); an entry must record what was true when it was written and never change.
            entry = json.loads(canonical_json(entry))
            entry["hash"] = entry_hash(entry["prev_hash"], entry)
            self.entries.append(entry)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(canonical_json(entry) + "\n")
            return entry

    def verify(self) -> dict:
        """Recompute the whole chain. Reports the first broken entry, if any."""
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e.get("seq") != i + 1:
                return {"ok": False, "checked": i, "broken_at": i + 1, "reason": "sequence gap"}
            if e.get("prev_hash") != prev:
                return {"ok": False, "checked": i, "broken_at": e["seq"], "reason": "prev_hash does not match"}
            if entry_hash(prev, e) != e.get("hash"):
                return {"ok": False, "checked": i, "broken_at": e["seq"], "reason": "entry content was changed"}
            prev = e["hash"]
        return {"ok": True, "checked": len(self.entries), "head": prev}
