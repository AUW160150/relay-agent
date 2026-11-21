"""Gold labels and scoring. No model is involved anywhere in this file.

Gold = the sponsor's own "Summary of Changes", parsed out of the protocol PDF text into a
list of changes. The sponsor wrote it, not us. The parser is heuristic and was hand-checked
on the dev split only (see eval/FAILURES.md for what it gets wrong).
"""
from __future__ import annotations

import io
import re

from pypdf import PdfReader, PdfWriter

SOC_HEAD = re.compile(r"summary\s+of\s+(protocol\s+)?(changes|amendments?|revisions?)|protocol\s+amendment\s+summary|"
                      r"changes\s+(from|to)\s+(the\s+)?(previous|prior|original)\s+(version|protocol)", re.I)
END_HEAD = re.compile(r"^\s*(table\s+of\s+contents|list\s+of\s+(tables|figures|abbreviations)|(protocol\s+)?synopsis|"
                      r"1\.?\s+(introduction|background|protocol\s+summary)|signature\s+page|investigator.{0,20}agreement)\s*$", re.I | re.M)
ITEM_START = re.compile(
    r"^\s*(?:[•\-–▪●]\s*)?(?P<sec>(?:section|sec\.?)\s*\d+(?:\.\d+)*|\d+(?:\.\d+){1,4}|appendix\s+[a-z0-9]+|synopsis|title\s+page|"
    r"throughout|global(?:ly)?|schedule\s+of\s+(?:activities|assessments|events)|table\s+\d+|figure\s+\d+|"
    r"inclusion\s+criteri\w+(?:\s+#?\d+)?|exclusion\s+criteri\w+(?:\s+#?\d+)?)\b", re.I)
STOP = set("""the a an and or of to in for on at by with from is are was were be been has have had this that these those as it its
will shall may can should would not no if than then into per also such any all each other more most new added add change changed
changes updated update revised revise section protocol study subjects subject patients patient amendment version table text clarify
clarified clarification following previous during after before which their they there been made within based""".split())


def page_texts(data: bytes) -> list[str]:
    out = []
    for p in PdfReader(io.BytesIO(data)).pages:
        try:
            out.append(p.extract_text() or "")
        except Exception:
            out.append("")
    return out


def soc_span(texts: list[str], hits: list[int], max_pages: int = 20) -> list[int]:
    """1-based pages of the summary of changes: from the first detected heading until a
    page that starts another front-matter or body section."""
    if not hits:
        return []
    start = hits[0]
    pages = [start]
    for p in range(start + 1, min(len(texts), start + max_pages) + 1):
        head = texts[p - 1][:400]
        if p not in hits and END_HEAD.search(head):
            break
        pages.append(p)
    return pages


def parse_changes(text: str) -> list[dict]:
    """Split summary-of-changes text into items, one per section reference."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    if len(re.findall(r"description\s+of\s+changes?\s*:", text, re.I)) >= 3:
        return _parse_numbered(lines)
    items, cur = [], None
    for l in lines:
        if not l or re.fullmatch(r"(page\s+)?\d+(\s+of\s+\d+)?|confidential.*", l, re.I):
            continue
        m = ITEM_START.match(l)
        if m:
            if cur:
                items.append(cur)
            cur = {"section": norm_section(m.group("sec")), "text": l}
        elif cur:
            cur["text"] += " " + l
    if cur:
        items.append(cur)
    out = []
    for it in items:
        if len(tokens(it["text"])) >= 5 and len(it["text"]) <= 2500:
            out.append({"id": f"G-{len(out) + 1}", **it})
    return out


def _parse_numbered(lines: list[str]) -> list[dict]:
    """Format used by some sponsors: a numbered list where each entry names the sections,
    then 'Description of Change: ...' and 'Purpose for Change: ...'. One item per number."""
    items, cur = [], None
    for l in lines:
        if not l or re.search(r"page\s+\d+\s+of\s+\d+|confidential|do not distribute", l, re.I):
            continue
        m = re.match(r"^(\d{1,3})\.\s+(\S.*)$", l)
        if m and int(m.group(1)) == (cur["n"] + 1 if cur else 1):
            if cur:
                items.append(cur)
            cur = {"n": int(m.group(1)), "section": norm_section(m.group(2))[:120], "text": m.group(2)}
        elif cur:
            cur["text"] += " " + l
    if cur:
        items.append(cur)
    out = []
    for it in items:
        text = re.split(r"purpose\s+for\s+change\s*:", it["text"], flags=re.I)[0]   # keep what changed, drop the rationale
        if len(tokens(text)) >= 5 and len(text) <= 2500:
            out.append({"id": f"G-{len(out) + 1}", "section": it["section"], "text": text.strip()})
    return out


def gold_for(data: bytes, hits: list[int]) -> dict:
    texts = page_texts(data)
    pages = soc_span(texts, hits)
    return {"soc_pages": pages, "changes": parse_changes("\n".join(texts[p - 1] for p in pages))}


def strip_pages(data: bytes, pages: list[int]) -> bytes:
    """The PDF without the given 1-based pages. Used so that, in the two-version setting,
    the system has to find the changes itself instead of reading the sponsor's summary."""
    r, w = PdfReader(io.BytesIO(data)), PdfWriter()
    drop = set(pages)
    for i, p in enumerate(r.pages):
        if i + 1 not in drop:
            w.add_page(p)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- scoring
def norm_section(s: str) -> str:
    s = re.sub(r"^(section|sec\.?)\s*", "", s.strip().lower())
    return re.sub(r"\s+", " ", s).rstrip(".")


def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}|\d+(?:\.\d+)?", s.lower()) if w not in STOP}


def sec_numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)+", s))


def similarity(pred: dict, gold: dict) -> float:
    """Token containment of the gold item in the predicted change (summary + quote), with a
    bonus when the section numbers agree. Thresholds were set on the dev split only."""
    g = tokens(gold["text"])
    p = tokens(pred.get("summary", "") + " " + pred.get("quote", "") + " " + pred.get("section", ""))
    if not g or not p:
        return 0.0
    contain = len(g & p) / min(len(g), 25)
    sec = bool(sec_numbers(gold["section"] + " " + gold["text"][:40]) & sec_numbers(pred.get("section", "")))
    return min(1.0, contain + (0.25 if sec else 0.0))


def match(preds: list[dict], gold: list[dict], threshold: float = 0.45) -> dict:
    """Greedy one-to-one matching by similarity."""
    pairs = sorted(((similarity(p, g), i, j) for i, p in enumerate(preds) for j, g in enumerate(gold)), reverse=True)
    used_p, used_g, matches = set(), set(), []
    for s, i, j in pairs:
        if s < threshold:
            break
        if i in used_p or j in used_g:
            continue
        used_p.add(i); used_g.add(j)
        matches.append({"pred": preds[i].get("id"), "gold": gold[j]["id"], "score": round(s, 2)})
    return {"matches": matches, "recall": len(used_g) / len(gold) if gold else None, "precision": len(used_p) / len(preds) if preds else None,
            "n_pred": len(preds), "n_gold": len(gold), "unmatched_gold": [g["id"] for j, g in enumerate(gold) if j not in used_g],
            "unmatched_pred": [p.get("id") for i, p in enumerate(preds) if i not in used_p]}
