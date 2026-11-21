"""Casebook (the EDC build): seed version 1, typed diff operations, and applying them.

A casebook diff is a list of flat operations. The planner (Argon) writes them; the
navigator enters them through the UI, which calls apply_op one at a time; and
apply_diff gives the expected result that the built draft is compared against.
"""
from __future__ import annotations

import copy

from .ledger import record_hash
from .rules import check_rule

OP_KINDS = [
    "add_event_group", "add_event", "set_event_window", "add_form", "add_item", "assign_form",
    "add_codelist_entries", "add_rule", "modify_rule", "update_review_plan",
]


class DiffError(ValueError):
    pass


def _items(*spec):
    return [{"id": i, "label": l, "type": t, **({"codelist": c[0]} if c else {})} for i, l, t, *c in spec]


def seed_v1() -> dict:
    """Casebook version 1 for CTJ301UC201. The visit schedule follows Table 1 of the public
    protocol; item and rule names are ours (the sponsor's real build is not public)."""
    ev = lambda id, label, group, day, w=None: {"id": id, "label": label, "group": group, "day": day,
                                                 "window_before": w, "window_after": w, "dynamic": False}
    events = [
        ev("V0", "Visit 0 Stabilization", "RUNIN", None), ev("V1", "Visit 1 Screening", "SCR", -28),
        ev("V1.1", "Visit 1.1 Baseline", "SCR", -5), ev("V2", "Visit 2 Baseline", "TRT", 0),
        ev("V3", "Visit 3", "TRT", 14, 1), ev("V4", "Visit 4", "TRT", 28, 1), ev("V5", "Visit 5", "TRT", 42, 2),
        ev("V6", "Visit 6", "TRT", 56, 2), ev("V7", "Visit 7", "TRT", 70, 2), ev("V8", "Visit 8 (EoT)", "TRT", 84, 2),
        ev("V9", "Visit 9 Safety Follow-up", "FU", 105, 2),
    ]
    allv = [e["id"] for e in events]
    dosing = ["V2", "V3", "V4", "V5", "V6", "V7"]
    yn = ("YN",)
    forms = [
        ("DSIC", "Informed Consent", ["V0", "V1"], _items(("ICDTC", "Date of consent", "date"))),
        ("IE", "Inclusion/Exclusion Criteria", ["V1", "V2"], _items(("IEMET", "All criteria met", "codelist", yn))),
        ("DM", "Demographics", ["V0", "V1"], _items(("BRTHDTC", "Date of birth", "date"), ("SEX", "Sex", "codelist", ("SEX",)))),
        ("MH", "Medical and Surgical History", ["V1"], _items(("MHTERM", "Condition", "text"))),
        ("UCHX", "UC History and Previous Therapy", ["V0", "V1"], _items(("UCDXDTC", "Date of UC diagnosis", "date"))),
        ("VIRO", "HIV, HBV, HCV, EBV, CMV Test", ["V1"], _items(("VIRORES", "Result", "text"))),
        ("TB", "TB Test", ["V1", "V9"], _items(("TBRES", "Result", "text"))),
        ("ENDO", "Endoscopy incl. Biopsy Sampling", ["V1", "V8"], _items(("ENDODTC", "Date of endoscopy", "date"))),
        ("RAND", "Randomisation", ["V2"], _items(
            ("RANDDTC", "Date of randomisation", "datetime"), ("STRATCS", "Prior corticosteroids", "codelist", yn),
            ("PKSUB", "Consent to PK substudy", "codelist", yn))),
        ("EX", "IMP Administration", dosing, _items(
            ("EXSTDTC", "Start of infusion", "datetime"), ("EXENDTC", "End of infusion", "datetime"), ("EXDOSE", "Dose (mg)", "number"))),
        ("MAYO", "Efficacy – Full Mayo Score", ["V2", "V8"], _items(("MAYOTOT", "Total score", "number"))),
        ("PMAYO", "Efficacy – 9-point Partial Mayo", ["V2", "V3", "V4", "V5", "V6", "V7", "V8"], _items(("PMAYOTOT", "Partial score", "number"))),
        ("PK", "Blood Sampling for PK", ["V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9"], _items(
            ("PKPERF", "Sample taken", "codelist", yn), ("PKDTC", "Actual sampling date and time", "datetime"),
            ("PKTPT", "Nominal timepoint", "codelist", ("PKTPT",)))),
        ("FCP", "Faeces Sampling for Calprotectin", ["V2", "V4", "V6", "V8"], _items(("FCPDTC", "Date of sample", "date"))),
        ("CM_STAB", "Stable Conventional Treatment", allv, _items(("CMTRT", "Treatment", "text"))),
        ("AE", "Adverse Events", allv[3:], _items(("AETERM", "Event", "text"), ("AESTDTC", "Start date", "date"))),
    ]
    return {
        "version": 1, "study": "CTJ301UC201", "anchor_event": "V2",
        "event_groups": [{"id": "RUNIN", "label": "Run-in"}, {"id": "SCR", "label": "Screening"},
                         {"id": "TRT", "label": "Treatment Period"}, {"id": "FU", "label": "Follow-up Period"}],
        "events": events,
        "forms": [{"id": f[0], "label": f[1], "items": f[3]} for f in forms],
        "schedule": {f[0]: f[2] for f in forms},
        "codelists": {
            "YN": ["Yes", "No"], "SEX": ["Female", "Male"],
            "PKTPT": ["Pre-dose", "0.5 h", "1 h", "2 h (EOI)", "6 h", "336 h", "840 h"],
        },
        "rules": [
            {"id": "CMP_PK_01", "type": "edit_check", "description": "PK sample cannot be before the first dose",
             "when": "PK.PKDTC < $V2.EX.EXSTDTC",
             "then": {"action": "open_query", "message": "PK sample date is before the first dose."},
             "scope": {"form": "PK", "events": ["V3", "V4", "V5", "V6", "V7", "V8", "V9"]}},
            {"id": "CMP_EX_01", "type": "edit_check", "description": "Infusion end cannot be before infusion start",
             "when": "EX.EXENDTC < EX.EXSTDTC",
             "then": {"action": "open_query", "message": "End of infusion is before start of infusion."},
             "scope": {"form": "EX", "events": dosing}},
        ],
        "review_plans": [{"id": "SDV100", "label": "Critical Data – 100% SDV",
                          "coverage": {"PK": ["V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9"], "RAND": ["V2"], "EX": dosing}}],
    }


def _event_order_key(cb: dict):
    """Events sort by study day; undated events keep their existing position."""
    pos = {e["id"]: i for i, e in enumerate(cb["events"])}
    return lambda e: (e["day"] if e["day"] is not None else -10_000 + pos[e["id"]], pos[e["id"]])


def _win(v):
    if v is None or v == "":
        return None
    v = int(v)
    if v < 0:
        raise DiffError("A visit window cannot be negative")
    return v


def apply_op(cb: dict, op: dict) -> dict:
    """Apply one operation in place. Returns {"before": ..., "after": ...} for the ledger."""
    kind = op.get("kind")
    events = {e["id"]: e for e in cb["events"]}
    forms = {f["id"]: f for f in cb["forms"]}
    rules = {r["id"]: r for r in cb["rules"]}
    tid = op.get("target_id")
    if kind not in OP_KINDS:
        raise DiffError(f"Unknown operation kind {kind!r}")
    if not tid:
        raise DiffError(f"{kind} needs a target_id")

    if kind == "add_event_group":
        if any(g["id"] == tid for g in cb["event_groups"]):
            raise DiffError(f"Event group {tid} already exists")
        g = {"id": tid, "label": op.get("label") or tid, **({"repeating": True} if op.get("repeating") else {})}
        cb["event_groups"].append(g)
        return {"before": None, "after": g}

    if kind == "add_event":
        if tid in events:
            raise DiffError(f"Event {tid} already exists")
        if op.get("group") not in {g["id"] for g in cb["event_groups"]}:
            raise DiffError(f"Event group {op.get('group')!r} does not exist")
        day = op.get("day")
        e = {"id": tid, "label": op.get("label") or tid, "group": op["group"],
             "day": None if day in (None, "") else int(day),
             "window_before": _win(op.get("window_before")), "window_after": _win(op.get("window_after")),
             "dynamic": bool(op.get("dynamic"))}
        cb["events"].append(e)
        cb["events"].sort(key=_event_order_key(cb))
        return {"before": None, "after": e}

    if kind == "set_event_window":
        if tid not in events:
            raise DiffError(f"Event {tid} does not exist")
        e = events[tid]
        before = {"window_before": e["window_before"], "window_after": e["window_after"]}
        e["window_before"], e["window_after"] = _win(op.get("window_before")), _win(op.get("window_after"))
        return {"before": before, "after": {"window_before": e["window_before"], "window_after": e["window_after"]}}

    if kind == "add_form":
        if tid in forms:
            raise DiffError(f"Form {tid} already exists")
        f = {"id": tid, "label": op.get("label") or tid, "items": list(op.get("items") or [])}
        cb["forms"].append(f)
        cb["schedule"][tid] = []
        return {"before": None, "after": f}

    if kind == "add_item":
        if tid not in forms:
            raise DiffError(f"Form {tid} does not exist")
        item = op.get("item") or {}
        if not item.get("id") or any(i["id"] == item["id"] for i in forms[tid]["items"]):
            raise DiffError(f"Item {item.get('id')!r} is missing or already on form {tid}")
        if item.get("codelist") and item["codelist"] not in cb["codelists"]:
            raise DiffError(f"Codelist {item['codelist']} does not exist")
        item = {"id": item["id"], "label": item.get("label") or item["id"], "type": item.get("type") or "text",
                **({"codelist": item["codelist"]} if item.get("codelist") else {})}
        forms[tid]["items"].append(item)
        return {"before": None, "after": item}

    if kind == "assign_form":
        if tid not in forms:
            raise DiffError(f"Form {tid} does not exist")
        missing = [e for e in op.get("event_ids") or [] if e not in events]
        if missing or not op.get("event_ids"):
            raise DiffError(f"assign_form: events missing or unknown: {missing}")
        before = list(cb["schedule"].get(tid, []))
        order = [e["id"] for e in cb["events"]]
        cb["schedule"][tid] = sorted(set(before) | set(op["event_ids"]), key=order.index)
        return {"before": before, "after": cb["schedule"][tid]}

    if kind == "add_codelist_entries":
        entries = [str(x) for x in op.get("entries") or []]
        if not entries:
            raise DiffError("add_codelist_entries needs entries")
        before = list(cb["codelists"].get(tid, []))
        cb["codelists"][tid] = before + [x for x in entries if x not in before]
        return {"before": before, "after": cb["codelists"][tid]}

    if kind in ("add_rule", "modify_rule"):
        rule = dict(op.get("rule") or {})
        rule["id"] = tid
        if kind == "add_rule":
            if tid in rules:
                raise DiffError(f"Rule {tid} already exists")
            before, new = None, rule
        else:
            if tid not in rules:
                raise DiffError(f"Rule {tid} does not exist")
            before = copy.deepcopy(rules[tid])
            new = {**rules[tid], **{k: v for k, v in rule.items() if v not in (None, "", {}, [])}}
        problems = check_rule(new, cb)
        if problems:
            raise DiffError(f"Rule {tid}: " + "; ".join(problems))
        if before is None:
            cb["rules"].append(new)
        else:
            cb["rules"][cb["rules"].index(rules[tid])] = new
        return {"before": before, "after": new}

    if kind == "update_review_plan":
        plan = next((p for p in cb["review_plans"] if p["id"] == tid), None)
        form = op.get("form_id")
        if not plan or form not in forms:
            raise DiffError(f"Review plan {tid} or form {form} does not exist")
        missing = [e for e in op.get("event_ids") or [] if e not in events]
        if missing:
            raise DiffError(f"update_review_plan: unknown events {missing}")
        before = list(plan["coverage"].get(form, []))
        order = [e["id"] for e in cb["events"]]
        plan["coverage"][form] = sorted(set(before) | set(op.get("event_ids") or []), key=order.index)
        return {"before": before, "after": plan["coverage"][form]}

    raise DiffError(kind)


def apply_diff(cb: dict, ops: list[dict]) -> dict:
    """Return a new casebook (version + 1) with every operation applied in order."""
    new = copy.deepcopy(cb)
    for op in ops:
        try:
            apply_op(new, op)
        except DiffError as e:
            raise DiffError(f"{op.get('op_id', '?')}: {e}") from e
    new["version"] = cb["version"] + 1
    return new


def validate_diff(cb: dict, ops: list[dict]) -> list[dict]:
    """Try the diff on a copy. Returns one problem per operation that cannot be applied."""
    work, problems = copy.deepcopy(cb), []
    for op in ops:
        try:
            apply_op(work, op)
        except (DiffError, KeyError, TypeError, ValueError) as e:
            problems.append({"op_id": op.get("op_id"), "problem": str(e)})
    return problems


def content(cb: dict) -> dict:
    """The part of a casebook that counts as its content (everything but the version number)."""
    return {k: v for k, v in cb.items() if k != "version"}


def content_hash(cb: dict) -> str:
    return record_hash(content(cb))


def compare(a: dict, b: dict) -> list[dict]:
    """Human-readable change report between two casebooks."""
    rows = []
    ea, eb = ({e["id"]: e for e in x["events"]} for x in (a, b))
    win = lambda e: "none" if e["window_before"] is None and e["window_after"] is None else f"-{e['window_before'] or 0}/+{e['window_after'] or 0} d"
    for i, e in eb.items():
        if i not in ea:
            rows.append({"type": "Event", "name": i, "change": "Added", "before": "—", "after": f"Day {e['day']} · {e['group']} · window {win(e)}"})
        elif win(ea[i]) != win(e) or ea[i]["day"] != e["day"]:
            rows.append({"type": "Event", "name": i, "change": "Modified", "before": f"Day {ea[i]['day']} · window {win(ea[i])}", "after": f"Day {e['day']} · window {win(e)}"})
    rows += [{"type": "Event", "name": i, "change": "Removed", "before": ea[i]["label"], "after": "—"} for i in ea if i not in eb]
    ga, gb = ({g["id"] for g in x["event_groups"]} for x in (a, b))
    rows += [{"type": "Event group", "name": g, "change": "Added", "before": "—", "after": g} for g in gb - ga]
    fa, fb = ({f["id"]: f for f in x["forms"]} for x in (a, b))
    for i, f in fb.items():
        if i not in fa:
            rows.append({"type": "Form", "name": i, "change": "Added", "before": "—", "after": f"{len(f['items'])} items"})
        else:
            new_items = [it["id"] for it in f["items"] if it["id"] not in {x["id"] for x in fa[i]["items"]}]
            if new_items:
                rows.append({"type": "Form", "name": i, "change": "Modified", "before": f"{len(fa[i]['items'])} items", "after": "+ " + ", ".join(new_items)})
    for f in b["schedule"]:
        old, new = a["schedule"].get(f, []), b["schedule"][f]
        if old != new:
            rows.append({"type": "Schedule", "name": f, "change": "Modified", "before": f"{len(old)} events", "after": f"{len(new)} events (+ {', '.join(e for e in new if e not in old)})"})
    for c in b["codelists"]:
        old, new = a["codelists"].get(c, []), b["codelists"][c]
        if old != new:
            rows.append({"type": "Codelist", "name": c, "change": "Modified" if old else "Added", "before": f"{len(old)} entries", "after": "+ " + ", ".join(x for x in new if x not in old)})
    ra, rb = ({r["id"]: r for r in x["rules"]} for x in (a, b))
    for i, r in rb.items():
        if i not in ra:
            rows.append({"type": "Rule", "name": i, "change": "Added", "before": "—", "after": r.get("description") or r["when"]})
        elif ra[i] != r:
            rows.append({"type": "Rule", "name": i, "change": "Modified", "before": rule_text(ra[i]), "after": rule_text(r)})
    for p in b["review_plans"]:
        old = next((x for x in a["review_plans"] if x["id"] == p["id"]), {"coverage": {}})
        for f, evs in p["coverage"].items():
            if old["coverage"].get(f, []) != evs:
                rows.append({"type": "Review plan", "name": p["label"], "change": "Modified", "before": f"{f} @ {len(old['coverage'].get(f, []))} events", "after": f"{f} @ {len(evs)} events"})
    return rows


def rule_text(r: dict) -> str:
    then = r.get("then") or {}
    if then.get("action") == "add_events":
        t = "Add events " + ", ".join(then.get("events", []))
    else:
        t = f'Open query "{then.get("message", "")}"'
    scope = r.get("scope") or {}
    s = f"\nScope:  {scope.get('form', '')} @ {', '.join(scope.get('events', []))}" if scope.get("events") else ""
    return f"If      {r.get('when', '')}\nThen    {t}{s}"


def blank(study: str) -> dict:
    """Empty starting casebook for a protocol from a study we hold no build for."""
    return {"version": 1, "study": study, "anchor_event": "DAY0",
            "event_groups": [{"id": "SCHED", "label": "Scheduled visits"}],
            "events": [{"id": "DAY0", "label": "Day 0 / Baseline", "group": "SCHED", "day": 0,
                        "window_before": None, "window_after": None, "dynamic": False}],
            "forms": [], "schedule": {}, "codelists": {"YN": ["Yes", "No"]}, "rules": [],
            "review_plans": [{"id": "SDV100", "label": "Critical Data – 100% SDV", "coverage": {}}]}


def reflects(cb: dict, op: dict) -> list[str]:
    """Does the casebook already show this operation? Returns what is still missing or different.
    This is the fixed-rule check that the navigator's work is compared against."""
    kind, tid, out = op["kind"], op["target_id"], []
    events = {e["id"]: e for e in cb["events"]}
    if kind == "add_event_group":
        if not any(g["id"] == tid for g in cb["event_groups"]):
            out.append(f"event group {tid} does not exist")
    elif kind == "add_event":
        e = events.get(tid)
        if not e:
            return [f"event {tid} does not exist"]
        want = {"label": op.get("label") or tid, "group": op.get("group"), "day": op.get("day"),
                "window_before": _win(op.get("window_before")), "window_after": _win(op.get("window_after")), "dynamic": bool(op.get("dynamic"))}
        out += [f"event {tid}: {k} is {e[k]!r}, expected {v!r}" for k, v in want.items() if e[k] != v]
    elif kind == "set_event_window":
        e = events.get(tid)
        if not e:
            return [f"event {tid} does not exist"]
        for k in ("window_before", "window_after"):
            if e[k] != _win(op.get(k)):
                out.append(f"event {tid}: {k} is {e[k]!r}, expected {_win(op.get(k))!r}")
    elif kind == "add_form":
        if not any(f["id"] == tid for f in cb["forms"]):
            out.append(f"form {tid} does not exist")
    elif kind == "add_item":
        f = next((f for f in cb["forms"] if f["id"] == tid), None)
        it = next((i for i in (f or {}).get("items", []) if i["id"] == (op.get("item") or {}).get("id")), None)
        if not it:
            out.append(f"item {(op.get('item') or {}).get('id')} is not on form {tid}")
        else:
            for k in ("type", "codelist"):
                if (op["item"].get(k) or None) != (it.get(k) or None) and not (k == "type" and not op["item"].get(k)):
                    out.append(f"item {it['id']}: {k} is {it.get(k)!r}, expected {op['item'].get(k)!r}")
    elif kind == "assign_form":
        miss = [e for e in op.get("event_ids") or [] if e not in cb["schedule"].get(tid, [])]
        if miss:
            out.append(f"form {tid} is not yet assigned to {', '.join(miss)}")
    elif kind == "add_codelist_entries":
        miss = [e for e in op.get("entries") or [] if e not in cb["codelists"].get(tid, [])]
        if miss:
            out.append(f"codelist {tid} is missing {', '.join(miss)}")
    elif kind in ("add_rule", "modify_rule"):
        r = next((r for r in cb["rules"] if r["id"] == tid), None)
        if not r:
            return [f"rule {tid} does not exist"]
        want = op.get("rule") or {}
        squash = lambda s: "".join(str(s).replace("−", "-").split())
        if want.get("when") and squash(r["when"]) != squash(want["when"]):
            out.append(f"rule {tid}: condition is {r['when']!r}, expected {want['when']!r}")
        if want.get("description") and want["description"].strip() != (r.get("description") or "").strip():
            out.append(f"rule {tid}: description is {r.get('description')!r}, expected {want['description']!r}")
        if want.get("type") and r["type"] != want["type"]:
            out.append(f"rule {tid}: type is {r['type']}, expected {want['type']}")
        wt, rt = want.get("then") or {}, r.get("then") or {}
        if wt.get("events") and sorted(wt["events"]) != sorted(rt.get("events", [])):
            out.append(f"rule {tid}: adds {rt.get('events')}, expected {wt['events']}")
        if wt.get("message") and wt["message"].strip() != rt.get("message", "").strip():
            out.append(f"rule {tid}: query text differs from the plan")
        ws, rs = want.get("scope") or {}, r.get("scope") or {}
        if ws.get("events") and (sorted(ws["events"]) != sorted(rs.get("events", [])) or (ws.get("form") or "") != (rs.get("form") or "")):
            out.append(f"rule {tid}: scope is {rs}, expected {ws}")
    elif kind == "update_review_plan":
        p = next((p for p in cb["review_plans"] if p["id"] == tid), None)
        miss = [e for e in op.get("event_ids") or [] if e not in ((p or {}).get("coverage", {}).get(op.get("form_id"), []))]
        if miss or not p:
            out.append(f"review plan {tid}: {op.get('form_id')} not yet covered at {', '.join(miss)}")
    return out


def differences(expected, built, path: str = "") -> list[str]:
    """Where two casebooks differ, as readable paths. Used when the build does not equal the plan."""
    out: list[str] = []
    if type(expected) is not type(built):
        return [f"{path}: expected {expected!r}, built {built!r}"]
    if isinstance(expected, dict):
        for k in sorted(set(expected) | set(built)):
            if k not in expected or k not in built:
                out.append(f"{path}/{k}: only in the {'built casebook' if k not in expected else 'plan'}")
            else:
                out += differences(expected[k], built[k], f"{path}/{k}")
    elif isinstance(expected, list):
        if len(expected) != len(built):
            out.append(f"{path}: {len(expected)} planned, {len(built)} built")
        for i, (x, y) in enumerate(zip(expected, built)):
            label = x.get("id", i) if isinstance(x, dict) else i
            out += differences(x, y, f"{path}[{label}]")
    elif expected != built:
        out.append(f"{path}: expected {expected!r}, built {built!r}")
    return out
