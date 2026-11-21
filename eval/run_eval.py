"""Evaluation runner. Every number in the README tables comes from the JSON this writes.

  backend/.venv/bin/python eval/run_eval.py gold    --split dev          # parse gold labels (no model)
  backend/.venv/bin/python eval/run_eval.py diff    --split test --arm system|b1|b2
  backend/.venv/bin/python eval/run_eval.py auditor --n 3                # seeded-error catch rate
  backend/.venv/bin/python eval/run_eval.py nav     --runs 20            # full sponsor flow, live UI
  backend/.venv/bin/python eval/run_eval.py report                       # README tables from results/*.json

Arms for the protocol-diff task:
  system  Neon with the engineered prompt, then the deterministic citation check: a change whose
          quote cannot be found in the PDF is dropped, a quote found on another page is re-paged.
  b1      one Neon call with a plain prompt, output used as is.
  b2      one Hydrogen call with the same plain prompt, output used as is.
Two settings, reported separately:
  two_version  previous and new PDF; the summary-of-changes pages are REMOVED from the new PDF,
               so the model must find the changes itself.
  single       only the new PDF, summary of changes visible (an easier reading task).
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import statistics as st
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))

import gold as G  # noqa: E402
from relay import agents, casebook, config, llm, pdfutil  # noqa: E402

RESULTS = HERE / "results"
CACHE = HERE / "cache"
PLAIN_PROMPT = ("Here is a clinical trial protocol (and, if two files are given, the previous version first). List the changes made in the new "
                "version and say which ones need updates to the EDC (electronic data capture) build. For each change give the section, the "
                "page of the new PDF, a quote, a type and a one-sentence summary.")


def manifest() -> dict:
    return json.loads((HERE / "manifest.json").read_text())


def studies(split: str) -> list[dict]:
    return [s for s in manifest()["studies"] if split == "all" or s["split"] == split]


def pdf(nct: str, which: dict) -> bytes:
    return (CACHE / nct / which["filename"]).read_bytes()


def save(name: str, obj) -> Path:
    RESULTS.mkdir(exist_ok=True)
    p = RESULTS / f"{name}.json"
    p.write_text(json.dumps(obj, indent=1))
    return p


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(st.mean(xs), 3) if xs else None


# ---------------------------------------------------------------- gold
def cmd_gold(a):
    out = {}
    for s in studies(a.split):
        if not s["has_soc"]:
            continue
        g = G.gold_for(pdf(s["nct"], s["new"]), s["soc_pages"])
        out[s["nct"]] = g
        print(f"{s['nct']} [{s['split']}] soc pages {g['soc_pages'][:1]}..{g['soc_pages'][-1:]}  {len(g['changes'])} gold changes")
    p = save(f"gold_{a.split}", out)
    usable = [n for n, g in out.items() if len(g["changes"]) >= 3]
    print(f"wrote {p}; {len(usable)}/{len(out)} studies have 3 or more parsed changes")


def load_gold(split: str) -> dict:
    p = RESULTS / f"gold_{split}.json"
    if not p.exists():
        raise SystemExit(f"run `run_eval.py gold --split {split}` first")
    return json.loads(p.read_text())


# ---------------------------------------------------------------- diff
async def one_diff(s: dict, gold: dict, arm: str, setting: str) -> dict:
    new = pdf(s["nct"], s["new"])
    old = None
    if setting == "two_version":
        new = G.strip_pages(new, gold["soc_pages"])
        old = pdf(s["nct"], s["prev"])
    t0 = time.time()
    try:
        if arm == "system":
            r = await agents.neon_diff(new, old)
        else:
            parts = ([llm.text_part("PREVIOUS VERSION:"), llm.pdf_part(old)] if old else []) + [llm.text_part("NEW VERSION:"), llm.pdf_part(new), llm.text_part(PLAIN_PROMPT)]
            r = await llm.generate("protocol_diff" if arm == "b1" else "navigator", parts, schema=agents.DIFF_SCHEMA)
    except Exception as e:
        return {"nct": s["nct"], "arm": arm, "setting": setting, "error": f"{type(e).__name__}: {e}"}
    changes = r["json"]["changes"]
    cites = [pdfutil.locate(new, c["quote"], c["page"]) for c in changes]
    invented = [c["id"] for c, ct in zip(changes, cites) if ct["page"] is None]
    if arm == "system":  # the system's own post-check: drop what cannot be found, re-page what moved
        kept = []
        for c, ct in zip(changes, cites):
            if ct["page"] is None:
                continue
            kept.append({**c, "page": ct["page"]})
        final = kept
    else:
        final = changes
    m = G.match(final, gold["changes"])
    final_cites = [pdfutil.locate(new, c["quote"], c["page"]) for c in final]
    return {"nct": s["nct"], "arm": arm, "setting": setting, "model": r["model"], "n_raw": len(changes), "n_final": len(final),
            "recall": m["recall"], "precision": m["precision"], "n_gold": m["n_gold"],
            "citation_accuracy": (sum(c["ok"] for c in final_cites) / len(final_cites)) if final_cites else None,
            "invented_raw": len(invented), "invented_final": sum(c["page"] is None for c in final_cites),
            "edc_flagged": sum(bool(c.get("affects_edc")) for c in final), "cost_usd": r.get("cost_usd"), "latency_s": round(time.time() - t0, 1),
            "usage": r.get("usage"), "changes": final, "match": m}


async def cmd_diff(a):
    gold = load_gold(a.split)
    rows = []
    sem = asyncio.Semaphore(a.parallel)
    todo = []
    for s in studies(a.split):
        g = gold.get(s["nct"])
        if not g or len(g["changes"]) < 3:
            continue
        for setting in (["two_version"] if s["multi_version"] else []) + ["single"]:
            todo.append((s, g, setting))
    if a.limit:
        todo = todo[: a.limit]

    async def run(s, g, setting):
        async with sem:
            r = await one_diff(s, g, a.arm, setting)
            print(f"{r['nct']} {setting:<11} {a.arm:<6} " + (r.get("error") or f"recall={r['recall']:.2f} precision={r['precision'] or 0:.2f} cite={r['citation_accuracy'] or 0:.2f} invented={r['invented_raw']} ${r['cost_usd']} {r['latency_s']}s"))
            rows.append(r)

    await asyncio.gather(*[run(*t) for t in todo])
    summary = {}
    for setting in ("two_version", "single"):
        ok = [r for r in rows if r["setting"] == setting and "error" not in r]
        summary[setting] = {"n": len(ok), "errors": sum(1 for r in rows if r["setting"] == setting and "error" in r),
                            "recall": mean([r["recall"] for r in ok]), "precision": mean([r["precision"] for r in ok]),
                            "citation_accuracy": mean([r["citation_accuracy"] for r in ok]),
                            "invented_per_study": mean([r["invented_final"] for r in ok]), "invented_raw_per_study": mean([r["invented_raw"] for r in ok]),
                            "cost_usd": mean([r["cost_usd"] for r in ok]), "latency_s": mean([r["latency_s"] for r in ok])}
    save(f"diff_{a.arm}_{a.split}", {"arm": a.arm, "split": a.split, "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": summary, "rows": rows})
    print(json.dumps(summary, indent=1))


# ---------------------------------------------------------------- auditor catch rate
def seed_errors(build: dict) -> list[dict]:
    """Known errors planted in a valid diff, one at a time. Each returns (name, mutated build, how to tell it was caught)."""
    out = []
    ops = build["ops"]
    ev = next((o for o in ops if o["kind"] == "add_event" and o.get("day") is not None), None)
    if ev:
        b = copy.deepcopy(build); o = next(x for x in b["ops"] if x["op_id"] == ev["op_id"]); o["day"] = o["day"] + 3
        out.append({"name": "wrong_day", "build": b, "op_id": ev["op_id"]})
    w = next((o for o in ops if o["kind"] == "add_event" and o.get("window_after") is not None), None)
    if w:
        b = copy.deepcopy(build); o = next(x for x in b["ops"] if x["op_id"] == w["op_id"]); o["window_before"] = o["window_after"] = (w["window_after"] or 0) + 5
        out.append({"name": "wrong_window", "build": b, "op_id": w["op_id"]})
    dyn = next((o for o in ops if o["kind"] == "add_rule" and o["rule"]["type"] == "dynamic"), None)
    if dyn and '"Yes"' in dyn["rule"]["when"]:
        b = copy.deepcopy(build); o = next(x for x in b["ops"] if x["op_id"] == dyn["op_id"]); o["rule"]["when"] = o["rule"]["when"].replace('"Yes"', '"No"')
        out.append({"name": "inverted_rule", "build": b, "op_id": dyn["op_id"]})
    chk = next((o for o in ops if o["kind"] == "add_rule" and o["rule"]["type"] == "edit_check" and "1h" in o["rule"]["when"]), None)
    if chk:
        b = copy.deepcopy(build); o = next(x for x in b["ops"] if x["op_id"] == chk["op_id"]); o["rule"]["when"] = o["rule"]["when"].replace("> 1h", "> 12h")
        out.append({"name": "loosened_check", "build": b, "op_id": chk["op_id"]})
    if ev:
        b = copy.deepcopy(build)
        b["ops"].append({**copy.deepcopy(ev), "op_id": "OP-90", "target_id": "V9.9", "label": "Extra safety visit", "day": 120})
        out.append({"name": "fabricated_event", "build": b, "op_id": "OP-90"})
        b = copy.deepcopy(build); b["ops"] = [x for x in b["ops"] if x["op_id"] != ev["op_id"]]
        out.append({"name": "dropped_event", "build": b, "op_id": None, "missing_target": ev["target_id"], "change_ids": ev.get("source_change_ids", [])})
    return out


def caught(e: dict, findings: list[dict]) -> bool:
    for f in findings:
        if e["op_id"] and f.get("op_id") == e["op_id"]:
            return True
        if e.get("missing_target") and f["kind"] == "missing" and (e["missing_target"] in f["detail"] or f.get("change_id") in e.get("change_ids", [])):
            return True
    return False


async def cmd_auditor(a):
    """Plan the seeded study for real (Neon, Argon), plant errors, ask Carbon. Carbon never sees which op was changed."""
    v1 = casebook.seed_v1()
    pdf_bytes = (config.ASSETS / "protocol.pdf").read_bytes()
    rows = []
    for rep in range(a.n):
        d = (await agents.neon_diff(pdf_bytes, None, agents.casebook_brief(v1)))["json"]
        changes = [c for c in d["changes"] if c["affects_edc"]]
        opts = (await agents.argon_options(changes, v1))["json"]["options"]
        opt = next((o for o in opts if o["recommended"]), opts[0])
        build = (await agents.argon_build(changes, v1, opt))["build"]
        if casebook.validate_diff(v1, build["ops"]):
            print(f"rep {rep}: planner diff invalid, skipped")
            continue
        clean = (await agents.carbon_audit(pdf_bytes, changes, v1, build))["json"]["findings"]
        for e in seed_errors(build):
            f = (await agents.carbon_audit(pdf_bytes, changes, v1, e["build"]))["json"]["findings"]
            hit = caught(e, f)
            rows.append({"rep": rep, "error": e["name"], "caught": hit, "findings": len(f), "clean_findings": len(clean)})
            print(f"rep {rep} {e['name']:<18} {'CAUGHT' if hit else 'missed'}  ({len(f)} findings; clean diff had {len(clean)})")
    by = {}
    for r in rows:
        by.setdefault(r["error"], []).append(r["caught"])
    summary = {"n": len(rows), "catch_rate": mean([float(r["caught"]) for r in rows]), "by_error": {k: {"n": len(v), "caught": sum(v)} for k, v in by.items()},
               "clean_findings_per_audit": mean([r["clean_findings"] for r in rows])}
    save("auditor", {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": summary, "rows": rows})
    print(json.dumps(summary, indent=1))


# ---------------------------------------------------------------- navigator reliability
def cmd_nav(a):
    import run_flow
    rows = []
    for i in range(a.runs):
        seed = a.seed + i
        answers = "keep" if seed % 2 == 0 else "drop"       # vary the human's answers so the builds differ
        option = None if seed % 3 else "B"
        t0 = time.time()
        r = run_flow.run(a.base, ["sponsor"], None, option, a.timeout, answers=answers)
        sp = r.get("sponsor", {})
        nav = sp.get("nav", {})
        rows.append({"seed": seed, "answers": answers, "option": sp.get("option"), "completed": r["ok"], "error": r.get("error"), "seconds": round(time.time() - t0, 1),
                     "steps": nav.get("steps"), "verified_ok": nav.get("verified_ok"), "verify_failed": nav.get("verify_failed"), "recoveries": nav.get("recoveries"),
                     "goals": nav.get("goals"), "goals_ok": nav.get("goals_ok"), "tests": sp.get("tests"), "build_matches_plan": sp.get("build_matches_plan"), "cost_usd": (sp.get("costs") or {}).get("total_usd")})
        print(json.dumps(rows[-1]))
    done = [r for r in rows if r["completed"]]
    steps = sum(r["steps"] or 0 for r in done)
    vt = sum((r["verified_ok"] or 0) + (r["verify_failed"] or 0) for r in done)
    summary = {"runs": len(rows), "completed": len(done), "completion_rate": round(len(done) / len(rows), 3) if rows else None,
               "steps": steps, "step_success_rate": round(sum(r["verified_ok"] or 0 for r in done) / vt, 3) if vt else None,
               "recoveries": sum(r["recoveries"] or 0 for r in done), "goal_success_rate": round(sum(r["goals_ok"] or 0 for r in done) / max(1, sum(r["goals"] or 0 for r in done)), 3),
               "median_seconds": st.median([r["seconds"] for r in done]) if done else None, "mean_cost_usd": mean([r["cost_usd"] for r in done])}
    mode = json.loads(__import__("urllib.request").request.urlopen(a.base + "/api/accounts").read())["mode"]
    save(f"nav_{mode}", {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mode": mode, "summary": summary, "rows": rows})
    print(json.dumps(summary, indent=1))


# ---------------------------------------------------------------- report
def _load(name):
    p = RESULTS / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def fmt(x, pct=False):
    if x is None:
        return "n/a"
    return f"{x * 100:.0f}%" if pct else (f"{x:.2f}" if isinstance(x, float) else str(x))


def cmd_report(a):
    L = []
    m = manifest() if (HERE / "manifest.json").exists() else None
    if m:
        c = m["counts"]
        L += [f"Dataset: {c['total']} studies from ClinicalTrials.gov ({c['test']} test, {c['dev']} dev; {c['with_soc']} with a sponsor summary of changes, "
              f"{c['multi_version']} with two posted protocol versions). Frozen {m['frozen_at'][:10]}.", ""]
    for setting, title in (("two_version", "Two versions, summary of changes removed (the model must find the changes)"), ("single", "Single PDF, summary of changes visible")):
        L += [f"**{title}**, test split", "", "| Arm | Studies | Change recall | Precision | Quote on cited page | Invented changes per study | Cost per amendment | Latency |", "|---|---|---|---|---|---|---|---|"]
        any_run = False
        for arm, label in (("system", "RELAY (Neon + citation check)"), ("b1", "B1: one Neon call"), ("b2", "B2: one Hydrogen call")):
            r = _load(f"diff_{arm}_test")
            if not r:
                L.append(f"| {label} | not run | | | | | | |")
                continue
            any_run = True
            s = r["summary"][setting]
            L.append(f"| {label} | {s['n']} | {fmt(s['recall'], 1)} | {fmt(s['precision'], 1)} | {fmt(s['citation_accuracy'], 1)} | {fmt(s['invented_per_study'])} | ${fmt(s['cost_usd'])} | {fmt(s['latency_s'])} s |")
        L += ["", "" if any_run else "_Not run yet: these need `GEMINI_API_KEY`._", ""]
    au = _load("auditor")
    L += ["**Auditor catch rate** (errors planted in Argon's diff; Carbon is not told where)", ""]
    if au:
        L += ["| Planted error | Caught |", "|---|---|"] + [f"| {k} | {v['caught']} of {v['n']} |" for k, v in au["summary"]["by_error"].items()]
        L += [f"| **All** | **{fmt(au['summary']['catch_rate'], 1)}** of {au['summary']['n']} |", "", f"Findings on the unmodified diff: {fmt(au['summary']['clean_findings_per_audit'])} per audit.", ""]
    else:
        L += ["_Not run yet._", ""]
    L += ["**Navigator reliability** (full sponsor flow in the real UI)", "", "| Mode | Runs | Completed end to end | Steps | Step success (verifier) | Goal success | Recoveries | Median time |", "|---|---|---|---|---|---|---|---|"]
    for mode, label in (("live", "Live: Hydrogen picks the actions"), ("fixture", "Fixture: hand-written actions (tests the UI and action layer only)")):
        r = _load(f"nav_{mode}")
        if r:
            s = r["summary"]
            L.append(f"| {label} | {s['runs']} | {s['completed']} ({fmt(s['completion_rate'], 1)}) | {s['steps']} | {fmt(s['step_success_rate'], 1)} | {fmt(s['goal_success_rate'], 1)} | {s['recoveries']} | {fmt(s['median_seconds'])} s |")
        else:
            L.append(f"| {label} | not run | | | | | | |")
    v = _load("voice")
    L += ["", "**Voice robustness**", ""]
    if v:
        L += ["| Condition | Clips | Correct action | False action on non-commands | Median latency, end of speech to action |", "|---|---|---|---|---|"]
        L += [f"| {r['condition']} | {r['n']} | {fmt(r['correct_action_rate'], 1)} | {fmt(r['false_action_rate'], 1)} | {fmt(r['median_latency_s'])} s |" for r in v["curve"]]
        L += ["", f"Speakers: {v['speakers']}. Breaks (correct action below 80%) at: {v.get('breaks_at') or 'not within the tested range'}.", "", "![Voice noise curve](eval/results/voice_curve.png)"]
    else:
        L += ["_Not run yet: needs recordings from at least three speakers (see `eval/audio/README.md`) and `GEMINI_API_KEY`._"]
    text = "\n".join(L) + "\n"
    (RESULTS / "tables.md").write_text(text) if RESULTS.exists() or not RESULTS.mkdir() else None
    readme = ROOT / "README.md"
    if readme.exists() and "<!-- EVAL:START -->" in readme.read_text():
        s = readme.read_text()
        a_, b_ = s.index("<!-- EVAL:START -->") + len("<!-- EVAL:START -->"), s.index("<!-- EVAL:END -->")
        readme.write_text(s[:a_] + "\n" + text + s[b_:])
        print("README tables updated")
    print(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gold"); g.add_argument("--split", default="dev")
    d = sub.add_parser("diff"); d.add_argument("--split", default="dev"); d.add_argument("--arm", default="system", choices=["system", "b1", "b2"])
    d.add_argument("--parallel", type=int, default=3); d.add_argument("--limit", type=int)
    au = sub.add_parser("auditor"); au.add_argument("--n", type=int, default=3)
    n = sub.add_parser("nav"); n.add_argument("--runs", type=int, default=20); n.add_argument("--base", default="http://127.0.0.1:8017")
    n.add_argument("--seed", type=int, default=0); n.add_argument("--timeout", type=float, default=900)
    sub.add_parser("report")
    a = ap.parse_args()
    fn = globals()[f"cmd_{a.cmd}"]
    if a.cmd in ("diff", "auditor") and not config.api_key():
        raise SystemExit("GEMINI_API_KEY is not set; this command makes real model calls.")
    asyncio.run(fn(a)) if asyncio.iscoroutinefunction(fn) else fn(a)
