"""Build the evaluation dataset from ClinicalTrials.gov (API v2). No model is involved.

Finds interventional studies with posted protocol documents and keeps those that either
post more than one protocol version, or whose protocol PDF contains a sponsor-written
"Summary of Changes". We do not choose studies by whether our system does well on them:
candidates are taken in NCT order from fixed registry queries, and the split is by hash.

  backend/.venv/bin/python eval/fetch_ctgov.py --target 45

Writes eval/manifest.json (frozen: refuses to overwrite without --force) and caches the
PDFs in eval/cache/ (not committed; the manifest holds their SHA-256 so they can be re-fetched
and checked).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx
from pypdf import PdfReader

HERE = Path(__file__).parent
CACHE = HERE / "cache"
MANIFEST = HERE / "manifest.json"
API = "https://clinicaltrials.gov/api/v2/studies"
CDN = "https://cdn.clinicaltrials.gov/large-docs/{last2}/{nct}/{filename}"
UA = {"User-Agent": "relay-agent-eval/0.1 (hackathon research script)"}
QUERIES = [
    "AREA[LargeDocHasProtocol]true AND AREA[StudyType]INTERVENTIONAL AND AREA[LargeDocLabel]amendment",
    "AREA[LargeDocHasProtocol]true AND AREA[StudyType]INTERVENTIONAL AND AREA[LargeDocLabel]\"summary of changes\"",
    "AREA[LargeDocHasProtocol]true AND AREA[StudyType]INTERVENTIONAL AND AREA[LargeDocLabel]version",
]
SOC = re.compile(r"^[ \t]*(?:(?:appendix|section)?\s*[\dA-Z]+(?:\.\d+)*[.:]?\s+)?(?:protocol\s+amendment\s+)?"
                 r"(summary\s+of\s+(?:protocol\s+)?(?:changes|amendments?|revisions?)|protocol\s+amendment\s+summary(?:\s+of\s+changes)?)\b",
                 re.I | re.M)
SEEDED = "NCT03235752"   # the demo study is never part of the eval set
MAX_BYTES, MAX_PAGES, MIN_PAGES = 15_000_000, 400, 20
DEV_N = 8


def cdn_url(nct: str, filename: str) -> str:
    return CDN.format(last2=nct[-2:], nct=nct, filename=filename)


def candidates(client: httpx.Client, limit: int) -> list[dict]:
    seen: dict[str, dict] = {}
    for q in QUERIES:
        token = None
        while len(seen) < limit:
            params = {"query.term": q, "pageSize": 100, "sort": "@relevance",
                      "fields": "protocolSection.identificationModule.nctId,protocolSection.identificationModule.briefTitle,"
                                "protocolSection.designModule.phases,protocolSection.conditionsModule.conditions,documentSection"}
            if token:
                params["pageToken"] = token
            r = client.get(API, params=params)
            r.raise_for_status()
            data = r.json()
            for s in data.get("studies", []):
                ps = s["protocolSection"]
                nct = ps["identificationModule"]["nctId"]
                docs = [d for d in s.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", []) if d.get("hasProtocol")]
                if nct != SEEDED and docs:
                    seen[nct] = {"nct": nct, "title": ps["identificationModule"].get("briefTitle", ""),
                                 "phases": ps.get("designModule", {}).get("phases", []),
                                 "conditions": ps.get("conditionsModule", {}).get("conditions", [])[:3], "docs": docs}
            token = data.get("nextPageToken")
            if not token:
                break
    return [seen[k] for k in sorted(seen)]  # NCT order: nothing here depends on our system


def fetch(client: httpx.Client, nct: str, filename: str) -> bytes | None:
    path = CACHE / nct / filename
    if path.exists():
        return path.read_bytes()
    r = client.get(cdn_url(nct, filename))
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    time.sleep(0.3)  # be polite to the CDN
    return r.content


def soc_pages(data: bytes) -> tuple[int, list[int]]:
    """(page count, 1-based pages with a summary-of-changes heading). Every page is scanned,
    because the summary is often an appendix. Table-of-contents pages are skipped."""
    reader = PdfReader(io.BytesIO(data))
    n = len(reader.pages)
    hits = []
    for i, p in enumerate(reader.pages):
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        if SOC.search(t) and len(re.findall(r"\.{6,}\s*\d+", t)) < 3:
            hits.append(i + 1)
    return n, hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=45)
    ap.add_argument("--no-soc-cap", type=int, default=10, help="max studies kept that have two versions but no summary of changes")
    ap.add_argument("--scan", type=int, default=700, help="max candidates to look at")
    ap.add_argument("--force", action="store_true", help="overwrite a frozen manifest")
    a = ap.parse_args()
    if MANIFEST.exists() and not a.force:
        print(f"{MANIFEST} exists and is frozen. Use --force only before any tuning has been done.")
        return 1
    kept, skipped = [], {"too_large": 0, "no_pdf": 0, "pages": 0, "no_signal": 0, "unreadable": 0}
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as client:
        cands = candidates(client, a.scan)
        print(f"{len(cands)} candidate studies with protocol documents")
        for c in cands:
            if len(kept) >= a.target:
                break
            prots = sorted(c["docs"], key=lambda d: (d.get("date") or "", d["filename"]))
            new = prots[-1]
            prev = prots[-2] if len(prots) > 1 else None
            if new.get("size", 0) > MAX_BYTES or (prev and prev.get("size", 0) > MAX_BYTES):
                skipped["too_large"] += 1
                continue
            data = fetch(client, c["nct"], new["filename"])
            if not data:
                skipped["no_pdf"] += 1
                continue
            try:
                pages, soc = soc_pages(data)
            except Exception:
                skipped["unreadable"] += 1
                continue
            if not MIN_PAGES <= pages <= MAX_PAGES:
                skipped["pages"] += 1
                continue
            prev_data = fetch(client, c["nct"], prev["filename"]) if prev else None
            if not soc and not prev_data:
                skipped["no_signal"] += 1
                continue
            if not soc and sum(not e["has_soc"] for e in kept) >= a.no_soc_cap:
                skipped["no_soc_over_cap"] = skipped.get("no_soc_over_cap", 0) + 1
                continue
            entry = {"nct": c["nct"], "title": c["title"], "phases": c["phases"], "conditions": c["conditions"],
                     "new": {"filename": new["filename"], "label": new.get("label"), "date": new.get("date"), "url": cdn_url(c["nct"], new["filename"]),
                             "sha256": hashlib.sha256(data).hexdigest(), "pages": pages, "bytes": len(data)},
                     "prev": None, "soc_pages": soc, "has_soc": bool(soc), "multi_version": bool(prev_data), "protocol_versions_posted": len(prots)}
            if prev_data:
                entry["prev"] = {"filename": prev["filename"], "label": prev.get("label"), "date": prev.get("date"), "url": cdn_url(c["nct"], prev["filename"]),
                                 "sha256": hashlib.sha256(prev_data).hexdigest(), "bytes": len(prev_data)}
            kept.append(entry)
            print(f"  keep {c['nct']}  pages={pages:<4} soc={'yes' if soc else 'no '} versions={len(prots)}  {c['title'][:60]}")
    # split by hash of the NCT id: fixed, and independent of anything the system does.
    # Dev is drawn only from studies with a summary of changes, since only those have gold labels.
    order = sorted(kept, key=lambda e: hashlib.sha256(e["nct"].encode()).hexdigest())
    dev = {e["nct"] for e in [e for e in order if e["has_soc"]][:DEV_N]}
    for e in order:
        e["split"] = "dev" if e["nct"] in dev else "test"
    kept.sort(key=lambda e: e["nct"])
    manifest = {"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": "ClinicalTrials.gov API v2", "queries": QUERIES,
                "selection": "NCT order from the queries; kept if a summary-of-changes heading was detected on any page, or (up to a cap) if more than "
                             "one protocol version is posted; "
                             f"PDF under {MAX_BYTES // 1_000_000} MB and {MIN_PAGES} to {MAX_PAGES} pages",
                "split_rule": f"sorted by sha256(nct); the first {DEV_N} studies that have a summary of changes are dev, the rest test", "excluded": [SEEDED], "skipped": skipped,
                "counts": {"total": len(kept), "dev": sum(e["split"] == "dev" for e in kept), "test": sum(e["split"] == "test" for e in kept),
                           "with_soc": sum(e["has_soc"] for e in kept), "multi_version": sum(e["multi_version"] for e in kept)},
                "studies": kept}
    MANIFEST.write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest["counts"]), "skipped:", skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
