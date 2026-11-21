"""PDF text per page, and the deterministic citation check: is this quote on this page?"""
from __future__ import annotations

import io
import re
from functools import lru_cache

from pypdf import PdfReader

from .ledger import sha256


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


@lru_cache(maxsize=16)
def _pages_cached(key: str, data: bytes) -> tuple[str, ...]:
    reader = PdfReader(io.BytesIO(data))
    return tuple((p.extract_text() or "") for p in reader.pages)


def pages(data: bytes) -> tuple[str, ...]:
    return _pages_cached(sha256(data.hex()[:4096] + str(len(data))), data)


def _score(quote_n: str, page_n: str) -> float:
    """1.0 for an exact (normalised) match; otherwise the share of the quote's 24-character
    windows that appear on the page, which tolerates line-break and table-cell reordering."""
    if not quote_n:
        return 0.0
    if quote_n in page_n:
        return 1.0
    if len(quote_n) < 24:
        return 0.0
    step = max(1, (len(quote_n) - 24) // 12)
    wins = [quote_n[i:i + 24] for i in range(0, len(quote_n) - 23, step)]
    return sum(w in page_n for w in wins) / len(wins)


def locate(data: bytes, quote: str, page: int | None, threshold: float = 0.6) -> dict:
    """Check a citation. Returns {"ok", "page" (where the quote actually is), "cited_page",
    "score", "moved"}; ok is True only when the quote is found on the cited page."""
    pg = pages(data)
    q = _norm(quote or "")
    cited = page if page and 1 <= page <= len(pg) else None
    here = _score(q, _norm(pg[cited - 1])) if cited else 0.0
    if here >= threshold:
        return {"ok": True, "page": cited, "cited_page": page, "score": round(here, 2), "moved": False}
    best_p, best = None, 0.0
    for i, t in enumerate(pg):
        s = _score(q, _norm(t))
        if s > best:
            best_p, best = i + 1, s
    if best >= threshold:
        return {"ok": False, "page": best_p, "cited_page": page, "score": round(best, 2), "moved": True}
    return {"ok": False, "page": None, "cited_page": page, "score": round(max(best, here), 2), "moved": False}


def page_count(data: bytes) -> int:
    return len(pages(data))
