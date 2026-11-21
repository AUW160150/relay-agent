"""Deterministic rule engine for edit checks and dynamic rules.

Argon writes rules as data; this module executes them. Nothing here calls a model.

Rule shape (JSON):
  {"id": "DYN_PK_EVENTS", "type": "dynamic",
   "when": '$V2.RAND.PKSUB = "Yes"',
   "then": {"action": "add_events", "events": ["V2.1", ...]}}
  {"id": "PK_48H_V2_1", "type": "edit_check",
   "when": 'abs((PK.PKDTC - $V2.EX.EXSTDTC) - 48h) > 1h',
   "then": {"action": "open_query", "message": "..."},
   "scope": {"form": "PK", "events": ["V2.1"]}}

Expression language:
  refs      $EVENT.FORM.ITEM  (absolute)   FORM.ITEM (form at the event in scope)
            $EVENT.DATE       (visit date) DATE      (visit date of event in scope)
  literals  "text"  12  1.5  48h  2d  (durations are hours; 1d = 24h)
  ops       = != < <= > >=  + - * /  and or not  ( )
  funcs     abs(x)  is_blank(x)  hours_between(a, b)  days_between(a, b)
A datetime minus a datetime is a number of hours. A comparison with a missing
value is "not evaluable" and the rule does not fire.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class RuleError(ValueError):
    pass


class Missing:
    """Marker for a referenced value that has not been entered."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = Missing()

_TOKEN = re.compile(
    r"""\s*(?:
      (?P<dur>\d+(?:\.\d+)?\s*(?:h|d)\b)
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<str>"[^"]*"|'[^']*')
    | (?P<ref>\$?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)
    | (?P<op><=|>=|!=|<>|==|=|<|>|\+|-|−|\*|/|\(|\)|,)
    )""",
    re.X,
)
_KEYWORDS = {"and", "or", "not"}
_FUNCS = {"abs", "is_blank", "hours_between", "days_between"}
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")


def tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    src = src.strip()
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise RuleError(f"Cannot parse rule at: {src[pos:pos + 20]!r}")
        kind = m.lastgroup
        text = m.group(kind).strip()
        if kind == "ref" and text.lower() in _KEYWORDS:
            kind, text = "kw", text.lower()
        if kind == "op" and text == "−":
            text = "-"
        out.append((kind, text))
        pos = m.end()
    return out


# ---- AST ----
@dataclass
class Node:
    kind: str
    value: Any = None
    args: tuple = ()


class Parser:
    def __init__(self, src: str):
        self.toks = tokenize(src)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, text=None):
        k, t = self.peek()
        if k is None or (text is not None and t != text):
            raise RuleError(f"Expected {text or 'a value'}, found {t!r}")
        self.i += 1
        return k, t

    def parse(self) -> Node:
        node = self.or_()
        if self.peek()[0] is not None:
            raise RuleError(f"Unexpected {self.peek()[1]!r}")
        return node

    def or_(self):
        n = self.and_()
        while self.peek() == ("kw", "or"):
            self.take()
            n = Node("or", args=(n, self.and_()))
        return n

    def and_(self):
        n = self.not_()
        while self.peek() == ("kw", "and"):
            self.take()
            n = Node("and", args=(n, self.not_()))
        return n

    def not_(self):
        if self.peek() == ("kw", "not"):
            self.take()
            return Node("not", args=(self.not_(),))
        return self.cmp()

    def cmp(self):
        n = self.add()
        k, t = self.peek()
        if k == "op" and t in ("=", "==", "!=", "<>", "<", "<=", ">", ">="):
            self.take()
            op = {"==": "=", "<>": "!="}.get(t, t)
            n = Node("cmp", op, (n, self.add()))
        return n

    def add(self):
        n = self.mul()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            _, t = self.take()
            n = Node("arith", t, (n, self.mul()))
        return n

    def mul(self):
        n = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            _, t = self.take()
            n = Node("arith", t, (n, self.unary()))
        return n

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return Node("neg", args=(self.unary(),))
        return self.atom()

    def atom(self):
        k, t = self.take()
        if k == "num":
            return Node("lit", float(t))
        if k == "dur":
            n = float(re.match(r"[\d.]+", t).group())
            return Node("lit", n * (24 if t.endswith("d") else 1))
        if k == "str":
            return Node("lit", t[1:-1])
        if k == "op" and t == "(":
            n = self.or_()
            self.take(")")
            return n
        if k == "ref":
            if t.lower() in _FUNCS and self.peek() == ("op", "("):
                self.take("(")
                args = [self.or_()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.or_())
                self.take(")")
                return Node("call", t.lower(), tuple(args))
            return Node("ref", t)
        raise RuleError(f"Unexpected {t!r}")


def parse(src: str) -> Node:
    if not isinstance(src, str) or not src.strip():
        raise RuleError("Rule condition is empty")
    return Parser(src).parse()


def refs_in(src: str) -> list[str]:
    """All references used by an expression (for traceability checks)."""
    out: list[str] = []

    def walk(n: Node):
        if n.kind == "ref":
            out.append(n.value)
        for a in n.args:
            walk(a)

    walk(parse(src))
    return out


# ---- evaluation ----
def coerce(v: Any) -> Any:
    if v is None or v == "":
        return MISSING
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and _DT_RE.match(v.strip()):
        s = v.strip().replace(" ", "T")
        return datetime.fromisoformat(s)
    return v


def split_ref(ref: str, event_ids) -> tuple[str | None, list[str]]:
    """'$V2.1.PK.PKDTC' -> ('V2.1', ['PK', 'PKDTC']). Event ids may contain a dot,
    so the event is the longest prefix that is a known event id."""
    parts = ref.lstrip("$").split(".")
    if not ref.startswith("$"):
        return None, parts
    for n in range(len(parts) - 1, 0, -1):
        if ".".join(parts[:n]) in event_ids:
            return ".".join(parts[:n]), parts[n:]
    n = len(parts) - (1 if parts[-1].upper() == "DATE" and len(parts) == 2 else 2)
    return ".".join(parts[:max(n, 1)]), parts[max(n, 1):]


class Ctx:
    def __init__(self, data: dict, event: str | None, event_ids=()):
        self.data = data
        self.event = event
        self.event_ids = set(event_ids) | set(data)

    def lookup(self, ref: str) -> Any:
        ev, rest = split_ref(ref, self.event_ids)
        if ev is None:
            ev = self.event
        if ev is None:
            raise RuleError(f"{ref!r} has no event; write it as $EVENT.FORM.ITEM")
        visit = self.data.get(ev) or {}
        if len(rest) == 1 and rest[0].upper() == "DATE":
            return coerce(visit.get("date"))
        if len(rest) != 2:
            raise RuleError(f"Reference {ref!r} must be FORM.ITEM or $EVENT.FORM.ITEM")
        form = visit.get(rest[0]) or {}
        return coerce(form.get(rest[1]))


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    raise RuleError(f"Expected a number, got {v!r}")


def evaluate(node: Node, ctx: Ctx) -> Any:
    k = node.kind
    if k == "lit":
        return node.value
    if k == "ref":
        return ctx.lookup(node.value)
    if k == "neg":
        v = evaluate(node.args[0], ctx)
        return MISSING if v is MISSING else -_num(v)
    if k == "not":
        v = evaluate(node.args[0], ctx)
        return MISSING if v is MISSING else (not v)
    if k in ("and", "or"):
        a, b = (evaluate(x, ctx) for x in node.args)
        if k == "and":
            if a is False or b is False:
                return False
            return MISSING if MISSING in (a, b) else bool(a and b)
        if a is True or b is True:
            return True
        return MISSING if MISSING in (a, b) else bool(a or b)
    if k == "call":
        args = [evaluate(a, ctx) for a in node.args]
        if node.value == "is_blank":
            return args[0] is MISSING
        if any(a is MISSING for a in args):
            return MISSING
        if node.value == "abs":
            return abs(_num(args[0]))
        if node.value in ("hours_between", "days_between"):
            if len(args) != 2 or not all(isinstance(a, datetime) for a in args):
                raise RuleError(f"{node.value} needs two dates")
            h = (args[1] - args[0]).total_seconds() / 3600
            return h if node.value == "hours_between" else h / 24
    if k == "arith":
        a, b = (evaluate(x, ctx) for x in node.args)
        if MISSING in (a, b):
            return MISSING
        if isinstance(a, datetime) and isinstance(b, datetime):
            if node.value != "-":
                raise RuleError("Dates can only be subtracted")
            return (a - b).total_seconds() / 3600
        a, b = _num(a), _num(b)
        return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else MISSING}[node.value]
    if k == "cmp":
        a, b = (evaluate(x, ctx) for x in node.args)
        if MISSING in (a, b):
            return MISSING
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.strip().lower(), b.strip().lower()
        elif type(a) is not type(b):
            raise RuleError(f"Cannot compare {a!r} with {b!r}")
        return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[node.value]
    raise RuleError(f"Unknown node {k}")


def check_rule(rule: dict, casebook: dict | None = None) -> list[str]:
    """Static validation. Returns a list of problems (empty when the rule is well formed)."""
    problems = []
    try:
        refs = refs_in(rule.get("when", ""))
    except RuleError as e:
        return [str(e)]
    then = rule.get("then") or {}
    if rule.get("type") == "dynamic":
        if then.get("action") != "add_events" or not then.get("events"):
            problems.append("A dynamic rule must add at least one event")
    elif rule.get("type") == "edit_check":
        if then.get("action") != "open_query" or not then.get("message"):
            problems.append("An edit check must open a query with a message")
        if not (rule.get("scope") or {}).get("events"):
            problems.append("An edit check needs a scope (form and events)")
    else:
        problems.append(f"Unknown rule type {rule.get('type')!r}")
    if casebook:
        ev_ids = {e["id"] for e in casebook["events"]}
        forms = {f["id"]: {i["id"] for i in f["items"]} for f in casebook["forms"]}
        for r in refs:
            ev, rest = split_ref(r, ev_ids)
            if r.startswith("$") and ev not in ev_ids:
                problems.append(f"{r}: event {ev} does not exist")
            if not r.startswith("$") and rule.get("type") == "dynamic":
                problems.append(f"{r}: a dynamic rule must name the event, as $EVENT.FORM.ITEM")
            if len(rest) == 1 and rest[0].upper() == "DATE":
                continue
            if len(rest) != 2:
                problems.append(f"{r}: must be FORM.ITEM or $EVENT.FORM.ITEM")
            elif rest[0] not in forms:
                problems.append(f"{r}: form {rest[0]} does not exist")
            elif rest[1] not in forms[rest[0]]:
                problems.append(f"{r}: item {rest[1]} does not exist on form {rest[0]}")
        for e in then.get("events", []) + (rule.get("scope") or {}).get("events", []):
            if e not in ev_ids:
                problems.append(f"Event {e} does not exist")
    return problems


def run_subject(casebook: dict, subject: dict) -> dict:
    """Run every rule and visit window in the casebook against one subject's data."""
    data = subject.get("data", {})
    ev_ids = [e["id"] for e in casebook["events"]]
    events = [e["id"] for e in casebook["events"] if not e.get("dynamic")]
    added: dict[str, list[str]] = {}
    errors: list[dict] = []
    trace: list[dict] = []
    for r in casebook["rules"]:
        if r.get("type") != "dynamic" or r.get("inactive"):
            continue
        try:
            v = evaluate(parse(r["when"]), Ctx(data, None, ev_ids))
        except RuleError as e:
            errors.append({"rule": r["id"], "error": str(e)})
            continue
        trace.append({"rule": r["id"], "event": None, "result": "not evaluable" if v is MISSING else bool(v)})
        if v is True:
            added[r["id"]] = list(r["then"]["events"])
            events += [e for e in r["then"]["events"] if e not in events]

    queries: list[dict] = []
    for r in casebook["rules"]:
        if r.get("type") != "edit_check" or r.get("inactive"):
            continue
        scope = r.get("scope") or {}
        for ev in scope.get("events", []):
            if ev not in events or ev not in data:
                continue
            if scope.get("form") and scope["form"] not in data[ev]:
                continue
            try:
                v = evaluate(parse(r["when"]), Ctx(data, ev, ev_ids))
            except RuleError as e:
                errors.append({"rule": r["id"], "event": ev, "error": str(e)})
                continue
            trace.append({"rule": r["id"], "event": ev, "result": "not evaluable" if v is MISSING else bool(v)})
            if v is True:
                queries.append({"rule": r["id"], "event": ev, "form": scope.get("form"), "message": r["then"]["message"]})

    # visit windows are event properties, checked by one fixed rule
    anchor = coerce((data.get(casebook.get("anchor_event", "V2")) or {}).get("date"))
    by_id = {e["id"]: e for e in casebook["events"]}
    for ev, visit in data.items():
        e = by_id.get(ev)
        if e is None or ev not in events:
            queries.append({"rule": "UNEXPECTED_EVENT", "event": ev, "form": None,
                            "message": f"Data entered at {ev}, which is not in this subject's casebook."})
            continue
        d = coerce(visit.get("date"))
        if not isinstance(anchor, datetime) or not isinstance(d, datetime) or e.get("day") is None:
            continue
        if e.get("window_before") is None and e.get("window_after") is None:
            continue
        off = (d.date() - anchor.date()).days - e["day"]
        if off < -(e.get("window_before") or 0) or off > (e.get("window_after") or 0):
            queries.append({"rule": f"WINDOW:{ev}", "event": ev, "form": None,
                            "message": f"{ev} is {off:+d} day(s) from Day {e['day']}, outside the visit window."})
    return {"events": events, "added": added, "queries": queries, "errors": errors, "trace": trace}


def run_tests(casebook: dict, subjects: list[dict]) -> dict:
    """Execute synthetic subjects and compare with what each one expects."""
    results = []
    for s in subjects:
        out = run_subject(casebook, s)
        exp = s.get("expect", {})
        got_added = sorted({e for evs in out["added"].values() for e in evs})
        got_q = sorted({q["rule"] for q in out["queries"]})
        checks = []
        if "events_added" in exp:
            checks.append({"what": "events added", "expected": sorted(exp["events_added"]), "actual": got_added})
        if "queries" in exp:
            checks.append({"what": "queries opened", "expected": sorted(set(exp["queries"])), "actual": got_q})
        for c in checks:
            c["pass"] = c["expected"] == c["actual"]
        results.append({
            "subject": s.get("id"), "description": s.get("description", ""), "exercises": s.get("exercises", []),
            "checks": checks, "errors": out["errors"], "trace": out["trace"],
            "pass": bool(checks) and all(c["pass"] for c in checks) and not out["errors"],
        })
    rules = [r["id"] for r in casebook["rules"]]
    covered = {x for r in results for x in r["exercises"]}
    fired = {t["rule"] for r in results for t in r["trace"] if t["result"] is True}
    return {
        "total": len(results), "passed": sum(r["pass"] for r in results), "results": results,
        "rules_exercised": sorted(covered & set(rules)), "rules_fired": sorted(fired),
    }
