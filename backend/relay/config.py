from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("RELAY_DATA_DIR", ROOT / "data"))
REPLAY_DIR = ROOT / "replays"
ASSETS = ROOT / "assets"


@lru_cache
def models() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def job(name: str) -> dict:
    return models()["jobs"][name]


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def mode() -> str:
    """live (default) | replay | fixture. Fixture mode uses hand-written test doubles
    and is labelled as such everywhere it shows up; it never counts as model output."""
    return os.environ.get("RELAY_MODE", "live")
