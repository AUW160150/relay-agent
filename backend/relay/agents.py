"""The model jobs. One function per job, one model per job (config/models.yaml).

Each function returns the wrapper dict from llm.py with the parsed result under "json"
(or "answers" for Decision 1). In fixture mode the hand-written doubles are returned
instead, with model = FIXTURE_MODEL so the UI and ledger can label them.
"""
from __future__ import annotations

import asyncio
import copy
import json

from . import config, fixtures, llm
from .casebook import OP_KINDS
from .ledger import canonical_json, sha256

S = lambda **p: {"type": "object", "properties": p, "required": list(p)}
STR, INT, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
ARR = lambda items: {"type": "array", "items": items}
OPT = lambda t: {"anyOf": [t, {"type": "null"}]}  # same form pydantic emits for Optional[...]
ENUM = lambda *v: {"type": "string", "enum": list(v)}


def _fx(job: str, payload, key="json") -> dict:
    payload = copy.deepcopy(payload)
    return {"job": job, "model": fixtures.FIXTURE_MODEL, "prompt_hash": sha256(canonical_json(payload)), key: payload,
            "usage": {}, "cost_usd": 0.0, "latency_s": 0.0, "fixture": True}


def fixture_mode() -> bool:
    return config.mode() == "fixture"


# ------------------------------------------------------------------ Neon: protocol diff
CHANGE_TYPES = ["schedule", "eligibility", "procedure", "endpoint", "safety", "admin"]
DIFF_SCHEMA = S(
    protocol_id=STR, version=STR, previous_version=STR, spoken_summary=STR,
    changes=ARR(S(id=STR, section=STR, page=INT, quote=STR, type=ENUM(*CHANGE_TYPES), summary=STR,
                  affects_edc=BOOL, edc_note=STR)),
)
DIFF_SYSTEM = """You compare versions of a clinical trial protocol and list what changed.

You are given either two PDFs (the previous version, then the new version) or one PDF of the new
version. With one PDF, find the changes from the document itself: the amendment history, a
"Summary of Changes" table, version notes, and text or schedule entries that the document marks
as new or revised. If a description of the current EDC build is supplied, treat it as the
complete, approved build for the previous protocol version: a whole visit in the protocol's
schedule that the build does not contain is a change, but do not report forms, fields or
procedures as changes merely because the build description is brief.

Rules:
- One entry per distinct change. Do not merge unrelated changes; do not split one change.
- `page` is the 1-based page index of the NEW version's PDF file (the first page of the file is 1),
  not the number printed on the page.
- `quote` is copied character for character from that page of the new version, 1 to 3 sentences,
  enough to prove the change. Never paraphrase a quote. Never invent one.
- `type` is one of: schedule, eligibility, procedure, endpoint, safety, admin.
- `affects_edc` is true when the data-collection build must change: visits or events, forms,
  fields, codelists, edit checks, visit windows, dynamic rules. Wording, contact details,
  version numbers and rationale text do not affect the build.
- `edc_note`: one sentence on what in the build is affected, or empty.
- If you are not sure something changed, leave it out. Listing a change that is not real is
  worse than missing a minor one.
- `spoken_summary`: exactly two short sentences, plain words, for reading aloud. Say what
  changed and who it affects. No lists, no section numbers.
- ids are CH-1, CH-2, ... in document order."""


async def neon_diff(new_pdf: bytes, old_pdf: bytes | None = None, casebook_brief: str | None = None, job: str = "protocol_diff",
                    model: str | None = None) -> dict:
    if fixture_mode():
        return _fx(job, fixtures.DIFF)
    parts = []
    if old_pdf:
        parts += [llm.text_part("PREVIOUS VERSION:"), llm.pdf_part(old_pdf)]
    parts += [llm.text_part("NEW VERSION:"), llm.pdf_part(new_pdf)]
    if casebook_brief:
        parts.append(llm.text_part("CURRENT EDC BUILD (casebook version in production):\n" + casebook_brief))
    parts.append(llm.text_part("List the changes."))
    return await llm.generate(job, parts, system=DIFF_SYSTEM, schema=DIFF_SCHEMA, model=model)


# ------------------------------------------------------------------ Argon: options and build
OPTIONS_SCHEMA = S(options=ARR(S(id=ENUM("A", "B", "C", "D"), name=STR, summary=STR, pros=ARR(STR), cons=ARR(STR),
                                 tradeoff=STR, recommended=BOOL)))
RULE_SCHEMA = S(type=ENUM("dynamic", "edit_check"), description=STR, when=STR,
                then_action=ENUM("add_events", "open_query"), then_events=ARR(STR), then_message=STR,
                scope_form=STR, scope_events=ARR(STR))
ITEM_SCHEMA = S(id=STR, label=STR, type=ENUM("text", "number", "date", "datetime", "codelist"), codelist=STR)
OP_SCHEMA = {
    "type": "object",
    "properties": {
        "op_id": STR, "kind": ENUM(*OP_KINDS), "target_id": STR, "label": STR, "group": STR, "day": OPT(INT),
        "window_before": OPT(INT), "window_after": OPT(INT), "dynamic": BOOL, "repeating": BOOL,
        "form_id": STR, "event_ids": ARR(STR), "entries": ARR(STR), "rule": OPT(RULE_SCHEMA), "item": OPT(ITEM_SCHEMA),
        "source_change_ids": ARR(STR), "source_page": OPT(INT), "source_quote": STR,
        "basis": ENUM("copied", "interpreted", "inferred", "unclear"), "rationale": STR, "uncertainty": STR,
    },
    "required": ["op_id", "kind", "target_id", "source_change_ids", "source_page", "source_quote", "basis", "rationale", "uncertainty"],
}
SUBJECT_SCHEMA = S(
    id=STR, description=STR, exercises=ARR(STR),
    visit_dates=ARR(S(event=STR, date=STR)),
    values=ARR(S(event=STR, form=STR, item=STR, value=STR)),
    expect_events_added=ARR(STR), expect_queries=ARR(STR),
)
BUILD_SCHEMA = S(option_id=STR, ops=ARR(OP_SCHEMA), subjects=ARR(SUBJECT_SCHEMA))

PLANNER_SYSTEM = """You are an EDC study builder. You turn protocol changes into changes to a casebook
(the electronic data capture build): events, forms, items, codelists, edit checks, dynamic rules.

You only change what the protocol changes require. Reuse existing forms, items and codelists
whenever they already hold the data. Every operation cites the protocol change it comes from."""

OPTIONS_PROMPT = """Propose between two and four genuinely different ways to build these protocol changes
into the casebook (for example: separate scheduled events, a repeating form on existing visits,
an unscheduled repeating event). Label them A, B, C, D. Mark exactly one as recommended.

For each: a short name, a one-sentence summary, pros, cons, and `tradeoff`: exactly two short
sentences for reading aloud that state what this option gains and what it costs.
If only one design is sensible, still give a second, simpler one and say why it is weaker.
Two or three options is usually right."""

BUILD_PROMPT = """Write the casebook diff for option {opt} as a list of operations, then synthetic test
subjects that exercise every rule and every visit window you add.

KEEP THE DIFF SMALL. A reviewer has to read and sign every operation. Aim for 10 to 20
operations and never more than 24. Implement what the protocol changes require and nothing
else: reuse existing forms and items; add a form or an item only when a change introduces a
data point the casebook has no place for; do not add items for data the changes merely
mention. Changes that are wording, eligibility text or procedure detail with no new data point
need no operation. Prefer the changes to the visit schedule, its rules and its windows.

OPERATION KINDS and the fields each uses (all other fields: empty string, empty list, false or null):
- add_event_group: target_id (new group id), label, repeating
- add_event: target_id (new event id), label, group (existing or added group id), day (study day
  relative to the anchor event, null if unscheduled), window_before, window_after (days; null when
  the protocol gives no day window), dynamic (true when a dynamic rule creates the event only for
  some subjects)
- set_event_window: target_id (existing event), window_before, window_after
- add_form: target_id (new form id), label
- add_item: target_id (form id), item {{id, label, type, codelist}}
- assign_form: target_id (form id), event_ids (events where the form is collected)
- add_codelist_entries: target_id (codelist id), entries
- add_rule: target_id (new rule id), rule
- modify_rule: target_id (existing rule id), rule (only the parts that change)
- update_review_plan: target_id (plan id), form_id, event_ids
Order matters: create things before you reference them.

RULE LANGUAGE (rule.when). The rule fires when the expression is true.
  references   $EVENT.FORM.ITEM   a value at a named event, e.g. $V2.RAND.PKSUB
               FORM.ITEM          a value on the form at the event the check runs on (edit checks only)
               $EVENT.DATE, DATE  visit dates
  literals     "text"   12   1.5   48h   2d     (durations are hours; 1d = 24h)
  operators    =  !=  <  <=  >  >=   +  -  *  /   and  or  not   ( )
  functions    abs(x)   is_blank(x)   hours_between(a, b)   days_between(a, b)
  A datetime minus a datetime is a number of hours.
- dynamic rule: then_action = add_events, then_events = the events to create. Use absolute
  references only. Mark those events dynamic = true.
- edit check: then_action = open_query, then_message = the query text; scope_form and
  scope_events say where it runs. Write the condition so that TRUE means "open the query".
  If the same check compares against a different reference event at different visits, write
  one rule per visit.
- Day windows on events are checked automatically by the engine as rule id WINDOW:<event id>;
  do not write rules for them.

SOURCES. For each operation: source_change_ids (the protocol change ids), source_page and
source_quote copied from that change. basis is:
  copied       values taken as written from the protocol
  interpreted  your reading of protocol text (rule wording, design choice)
  inferred     not in the protocol; carried over from how the existing build treats the same data
  unclear      the protocol is ambiguous or contradicts itself
For inferred operations use empty source_change_ids, null source_page, empty quote, and say so
in `uncertainty`. Put any doubt in `uncertainty`; leave it empty only when there is none.
op_id is OP-01, OP-02, ...

TEST SUBJECTS. 4 to 10 subjects with ids T-01, T-02, ... Each has visit_dates (event, date as
YYYY-MM-DD), values (event, form, item, value; datetimes as YYYY-MM-DDTHH:MM), the rule ids it
exercises, and what must happen: expect_events_added (every event a dynamic rule adds for this
subject, or empty) and expect_queries (every rule id that must open a query, including
WINDOW:<event> ids, or empty). Include for each new rule one subject where it fires and one
where it does not, and a boundary case for each time or day window. Always give the anchor
event a visit date. The engine will run these for real; a wrong expectation is a failed test."""


def casebook_brief(cb: dict) -> str:
    """Compact text description of a casebook for prompts."""
    lines = [f"Casebook version {cb['version']} for study {cb['study']}. Anchor event (Day 0): {cb['anchor_event']}."]
    lines.append("EVENT GROUPS: " + "; ".join(f"{g['id']} = {g['label']}" for g in cb["event_groups"]))
    lines.append("EVENTS (id | label | group | day | window -/+ days | dynamic):")
    for e in cb["events"]:
        lines.append(f"  {e['id']} | {e['label']} | {e['group']} | {e['day']} | {e['window_before']}/{e['window_after']} | {e.get('dynamic', False)}")
    lines.append("FORMS (id | label | items | collected at):")
    for f in cb["forms"]:
        items = ", ".join(f"{i['id']}:{i['type']}" + (f"[{i['codelist']}]" if i.get("codelist") else "") for i in f["items"])
        lines.append(f"  {f['id']} | {f['label']} | {items} | {', '.join(cb['schedule'].get(f['id'], []))}")
    lines.append("CODELISTS: " + "; ".join(f"{k} = {v}" for k, v in cb["codelists"].items()))
    lines.append("RULES:")
    for r in cb["rules"]:
        lines.append(f"  {r['id']} ({r['type']}): when {r['when']} then {json.dumps(r['then'])} scope {json.dumps(r.get('scope') or {})}")
    lines.append("REVIEW PLANS: " + "; ".join(f"{p['id']} = {p['label']}: {json.dumps(p['coverage'])}" for p in cb["review_plans"]))
    return "\n".join(lines)


def _changes_text(changes: list[dict]) -> str:
    return "\n".join(f"{c['id']} (p. {c['page']}, {c['section']}, {c['type']}): {c['summary']}\n   quote: \"{c['quote']}\"" for c in changes)


def normalise_build(raw: dict) -> dict:
    """Model output (flat, schema-friendly) -> the nested shapes casebook.py and rules.py use."""
    ops = []
    for o in raw.get("ops", []):
        op = {k: v for k, v in o.items() if k not in ("rule", "item")}
        if o.get("rule") and o["kind"] in ("add_rule", "modify_rule"):
            r = o["rule"]
            rule = {"type": r.get("type"), "description": r.get("description", ""), "when": r.get("when", "")}
            if r.get("then_action") == "add_events":
                rule["then"] = {"action": "add_events", "events": r.get("then_events") or []}
            else:
                rule["then"] = {"action": "open_query", "message": r.get("then_message", "")}
                rule["scope"] = {"form": r.get("scope_form", ""), "events": r.get("scope_events") or []}
            op["rule"] = rule
        if o.get("item") and o["kind"] == "add_item":
            op["item"] = {k: v for k, v in o["item"].items() if v}
        ops.append(op)
    subjects = []
    for s in raw.get("subjects", []):
        data: dict = {}
        for v in s.get("visit_dates", []):
            data.setdefault(v["event"], {})["date"] = v["date"]
        for v in s.get("values", []):
            data.setdefault(v["event"], {}).setdefault(v["form"], {})[v["item"]] = v["value"]
        subjects.append({"id": s["id"], "description": s.get("description", ""), "exercises": s.get("exercises", []), "data": data,
                         "expect": {"events_added": s.get("expect_events_added", []), "queries": s.get("expect_queries", [])}})
    return {"option_id": raw.get("option_id"), "ops": ops, "subjects": subjects}


async def argon_options(changes: list[dict], cb: dict) -> dict:
    if fixture_mode():
        return _fx("planner", {"options": fixtures.OPTIONS})
    parts = [llm.text_part("CURRENT CASEBOOK:\n" + casebook_brief(cb)),
             llm.text_part("PROTOCOL CHANGES THAT AFFECT THE BUILD:\n" + _changes_text(changes)),
             llm.text_part(OPTIONS_PROMPT)]
    return await llm.generate("planner", parts, system=PLANNER_SYSTEM, schema=OPTIONS_SCHEMA)


async def argon_build(changes: list[dict], cb: dict, option: dict, problems: list[dict] | None = None,
                      previous: dict | None = None, instruction: str | None = None) -> dict:
    """Casebook diff plus synthetic subjects for one option. With `problems`, this is a repair
    round: the engine's own error messages are fed back once."""
    if fixture_mode():
        out = _fx("planner", fixtures.BUILD_B if option["id"] == "B" else fixtures.BUILD_A)
        out["build"] = out["json"]
        return out
    parts = [llm.text_part("CURRENT CASEBOOK:\n" + casebook_brief(cb)),
             llm.text_part("PROTOCOL CHANGES THAT AFFECT THE BUILD:\n" + _changes_text(changes)),
             llm.text_part(f"CHOSEN DESIGN, option {option['id']}: {option['name']}. {option['summary']}"),
             llm.text_part(BUILD_PROMPT.format(opt=option["id"]))]
    if instruction:
        parts.append(llm.text_part("THE REVIEWER ASKED FOR THIS CHANGE TO THE DESIGN:\n" + instruction))
    if problems and previous:
        parts.append(llm.text_part(
            "YOUR PREVIOUS ANSWER:\n" + json.dumps(previous) + "\n\nThe rule engine rejected parts of it. Fix exactly these "
            "problems and return the complete corrected answer. If a test expectation was wrong, correct the expectation; "
            "if the rule was wrong, correct the rule.\n" + json.dumps(problems, indent=1)))
    out = await llm.generate("planner", parts, system=PLANNER_SYSTEM, schema=BUILD_SCHEMA)
    out["build"] = normalise_build(out["json"])
    return out


# ------------------------------------------------------------------ Carbon: independent audit
FINDING_KINDS = ["untraceable", "missing", "mismatch", "uncertain"]
AUDIT_SCHEMA = S(findings=ARR(S(id=STR, kind=ENUM(*FINDING_KINDS), op_id=OPT(STR), change_id=OPT(STR), page=OPT(INT),
                                severity=ENUM("low", "medium", "high"), detail=STR)), overall_uncertainty=STR)
AUDIT_SYSTEM = """You are an independent auditor of a clinical data-capture build. Someone else wrote a
casebook diff from a protocol amendment. You did not see how they reasoned and you owe them
nothing. Your job is to find what is wrong, unsupported or missing. You are given the protocol
PDF, the list of protocol changes with quotes, the current casebook and the diff.

Check, by reading the cited pages yourself:
1. untraceable: an operation whose content is not supported by the page and quote it cites, or
   that cites nothing. Day numbers, window sizes, visit names, scope conditions and timepoints
   must appear in the protocol.
2. mismatch: an operation that contradicts the protocol (wrong day, wrong window, wrong
   population, a rule whose condition is inverted or uses the wrong reference).
3. missing: something a listed protocol change requires in the build that no operation covers.
4. uncertain: places where the protocol itself is ambiguous or inconsistent, so that a person
   has to decide. Say what the two readings are.

Report only real problems; an empty list is a valid answer. One finding per problem, with the
op_id or change_id it concerns, the page (1-based index of the PDF file) where the evidence is,
and one or two plain sentences of detail. severity high = would collect wrong data or miss
required data; medium = a decision for the reviewer; low = cosmetic. ids are F-1, F-2, ...
`overall_uncertainty`: one sentence on what you could not verify."""


def diff_for_audit(build: dict) -> str:
    """The diff as the auditor sees it: content and citations only. The planner's rationale and
    self-reported uncertainty are stripped so the auditor never sees Argon's reasoning."""
    hidden = {"rationale", "uncertainty", "basis"}
    return json.dumps([{k: v for k, v in op.items() if k not in hidden and v not in (None, "", [], False)} for op in build["ops"]], indent=1)


async def carbon_audit(pdf: bytes, changes: list[dict], cb: dict, build: dict, option_id: str = "A") -> dict:
    if fixture_mode():
        return _fx("auditor", fixtures.AUDIT.get(option_id, {"findings": [], "overall_uncertainty": ""}))
    parts = [llm.pdf_part(pdf), llm.text_part("PROTOCOL CHANGES:\n" + _changes_text(changes)),
             llm.text_part("CURRENT CASEBOOK:\n" + casebook_brief(cb)),
             llm.text_part("CASEBOOK DIFF TO AUDIT:\n" + diff_for_audit(build)), llm.text_part("Audit the diff.")]
    return await llm.generate("auditor", parts, system=AUDIT_SYSTEM, schema=AUDIT_SCHEMA)


async def score_findings(findings: list[dict], build: dict) -> dict:
    """Decision 1 scores each audit finding so routing uses calibrated probabilities:
    P(a person must decide this before the build can be approved)."""
    if not findings:
        return {"answers": {}, "model": config.job("audit_scoring")["model"], "prompt_hash": None, "cost_usd": 0, "latency_s": 0}
    if fixture_mode():
        return _fx("audit_scoring", {f["id"]: {"type": "noul", "p": 0.9 if f["severity"] != "low" else 0.2, "confidence": 0.9} for f in findings}, key="answers")
    ops = {o["op_id"]: o for o in build["ops"]}
    ctx = "Audit findings on a clinical data-capture build, each with the operation it concerns.\n\n" + "\n\n".join(
        f"FINDING {f['id']} ({f['kind']}, severity {f['severity']}): {f['detail']}\nOperation: "
        + json.dumps({k: v for k, v in ops.get(f.get("op_id") or "", {}).items() if v not in (None, "", [], False)}) for f in findings)
    qs = {f["id"].replace(" ", "_"): {
        "type": "noul",
        "instructions": f"Consider only finding {f['id']}. Must a human reviewer look at this before the build is approved?",
        "criteria": {"true_description": "The finding points at content that is unsupported by the protocol, contradicts it, is missing, or needs a judgement call.",
                     "false_description": "The finding is cosmetic, a restatement, or clearly not a problem."}} for f in findings}
    return await llm.decide("audit_scoring", ctx, qs)


# ------------------------------------------------------------------ Decision 1: utterance gate
INTENTS = {
    "open_amendment": "Open, start or read the new protocol amendment.",
    "explain": "Asks why, or asks for an explanation or more detail about the current step or item.",
    "show_source": "Asks to see the source, the protocol page, the quote or the evidence.",
    "choose_option": "Picks a design option (A, B, C or D), or says to try a different option.",
    "approve": "Approves, accepts, says yes, says to keep it, or says to do it, start or go ahead with what is proposed or with the next task.",
    "sign": "Asks to sign or to open the signature.",
    "reject": "Rejects, declines, says no, or says to drop or remove what is proposed.",
    "undo": "Asks to go back or undo the last step.",
    "stop": "Asks the assistant to stop, pause or wait.",
    "resume": "Asks the assistant to continue or carry on after a pause.",
    "deploy": "Asks to deploy the casebook or roll it out to sites.",
    "open_transition": "Asks to open or show the transition plan for enrolled participants.",
    "other": "Anything else.",
}


async def utterance_gate(transcript: str, audio_b64: str | None, task_context: str, audio_mime: str = "audio/wav") -> dict:
    """Is this turn for the agent, what does it want, and is it about the task in hand?
    Decision 1 hears the audio chunk as well as the transcript, so tone and direction count."""
    if fixture_mode():
        t = transcript.lower()
        intent = next((k for k, kws in {
            "open_amendment": ["open"], "show_source": ["source", "show me"], "explain": ["why", "explain"],
            "choose_option": ["option"], "sign": ["sign"], "approve": ["approve", "go ahead", "do it", "yes", "keep"], "reject": ["reject", "drop", "no "],
            "undo": ["back", "undo"], "stop": ["stop", "wait"], "resume": ["continue", "carry on"], "deploy": ["deploy", "roll"],
            "open_transition": ["transition"]}.items() if any(w in t for w in kws)), "other")
        opt = next((o for o in "ABCD" if f"option {o.lower()}" in t), "none")
        return _fx("utterance_gate", {
            "addressed_to_agent": {"type": "noul", "p": 0.95, "confidence": 0.95},
            "relevant_to_current_task": {"type": "noul", "p": 0.9, "confidence": 0.9},
            "intent": {"type": "choice", "choice": intent, "confidence": 0.9, "probabilities": {intent: 0.9}},
            "option": {"type": "choice", "choice": opt, "confidence": 0.9, "probabilities": {opt: 0.9}}}, key="answers")
    parts = [llm.text_part(
        "A voice assistant called Relay helps one person at a computer carry out a clinical trial protocol amendment. "
        "The microphone is open in a shared room, so it also picks up other people, phone calls and a television.\n"
        f"What the assistant is doing now: {task_context}\n"
        f"Transcript of the turn just heard: \"{transcript}\"")]
    if audio_b64:
        parts.append({"type": "audio", "data": audio_b64, "mime_type": audio_mime})
    qs = {
        "addressed_to_agent": {"type": "noul", "instructions": "Is this turn spoken to the assistant, as a command or question for it?",
                               "criteria": {"true_description": "The speaker is talking to the assistant: a command, a question or an answer to what the assistant just asked. Saying its name is not required.",
                                            "false_description": "The speaker is talking to another person, on a call, thinking aloud, or it is background audio such as a television."}},
        "relevant_to_current_task": {"type": "noul", "instructions": "Is the turn about the amendment work described above?"},
        "intent": {"type": "choice", "instructions": "What does the speaker want the assistant to do?", "criteria": INTENTS},
        "option": {"type": "choice", "instructions": "Which design option does the speaker name?",
                   "criteria": {"A": "Option A.", "B": "Option B.", "C": "Option C.", "D": "Option D.", "none": "No option is named."}},
    }
    return await llm.decide("utterance_gate", parts, qs)


def route_utterance(answers: dict) -> dict:
    """Thresholds from config. Returns {"action": "act" | "ask" | "ignore", "intent", "option"}."""
    th = config.job("utterance_gate")["thresholds"]
    p_addr = answers["addressed_to_agent"]["p"]
    p_rel = answers["relevant_to_current_task"]["p"]
    intent = answers["intent"]
    opt = answers.get("option", {}).get("choice", "none")
    base = {"intent": intent["choice"], "option": None if opt == "none" else opt, "p_addressed": p_addr, "p_relevant": p_rel,
            "p_intent": intent["confidence"]}
    if p_addr < th["unsure_low"]:
        return {**base, "action": "ignore"}
    if p_addr < th["addressed"]:
        return {**base, "action": "ask" if p_rel >= th["relevant"] else "ignore"}
    if p_rel < th["relevant"] and intent["choice"] == "other":
        return {**base, "action": "ignore"}
    if intent["confidence"] < th["intent"]:
        return {**base, "action": "ask"}
    return {**base, "action": "act"}


# ------------------------------------------------------------------ Hydrogen: navigator
def _fn(name, desc, **props):
    return {"type": "function", "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(props)}}


NAV_TOOLS = [
    _fn("navigate", "Open a page of the application from the side menu.", page={"type": "string", "description": "Page id, as listed under PAGES."}),
    _fn("click", "Click a button, row, checkbox or cell.", agent_id=STR),
    _fn("type", "Replace the content of a text field.", agent_id=STR, text=STR),
    _fn("select", "Choose an option in a dropdown.", agent_id=STR, value=STR),
    _fn("scroll", "Scroll an element into view.", agent_id=STR),
    _fn("done", "The goal is complete and visible on screen.", summary=STR),
    _fn("fail", "The goal cannot be completed from here.", reason=STR),
]
NAV_SYSTEM = """You operate a clinical data-capture study design application on behalf of a user, one goal at
a time. Each turn you get the goal, the list of interactive elements on screen (with their
agent_id, role, label and current value), a screenshot, and what you already did for this goal.

Answer only with function calls. You may return several calls in one turn when they belong to
the same dialog or page (for example fill every field of an open dialog, then click Save); they
run in order. Do not call anything after a click that opens or closes a dialog or changes
page: wait for the next turn to see the new screen. Use only agent_ids from the element list.
Enter values exactly as the goal gives them. Leave a field empty when the goal says none.
When the goal is visibly complete, call done. If the last result reports an error, read it and
correct the input; if the goal is impossible, call fail with the reason."""


def tree_text(tree: list[dict]) -> str:
    return "\n".join(
        f"[{e['role']} agent_id={e['id']}] {e.get('label', '')}" + (f" = {e['value']!r}" if e.get("value") not in (None, "") else "")
        + (" (disabled)" if e.get("disabled") else "") + (f" options: {e['options']}" if e.get("options") else "") for e in tree)


async def navigator_step(goal: str, page: str, pages: list[str], tree: list[dict], screenshot_b64: str | None, history: list[str]) -> dict:
    parts = [llm.text_part(f"GOAL: {goal}\n\nPAGES: {', '.join(pages)}\nCURRENT PAGE: {page}\n\nELEMENTS ON SCREEN:\n{tree_text(tree)}\n\n"
                           "WHAT YOU DID SO FAR FOR THIS GOAL:\n" + ("\n".join(history) if history else "(nothing yet)"))]
    if screenshot_b64:
        parts.append(llm.image_part(screenshot_b64))
    return await llm.call_tools("navigator", parts, NAV_TOOLS, system=NAV_SYSTEM)


# ------------------------------------------------------------------ Decision 1: step verifier
async def verify_step(action_text: str, expected: str, screenshot_b64: str) -> dict:
    if fixture_mode():
        return _fx("step_verifier", {"action_succeeded": {"type": "noul", "p": 0.97, "confidence": 0.97},
                                     "screen_matches_expected_state": {"type": "noul", "p": 0.95, "confidence": 0.95}}, key="answers")
    parts = [llm.text_part(f"An automated agent just performed this action in a web application: {action_text}\n"
                           f"Expected result on screen: {expected}\nThe screenshot was taken right after the action."),
             llm.image_part(screenshot_b64)]
    qs = {"action_succeeded": {"type": "noul", "instructions": "Does the screenshot show that the action took effect?",
                               "criteria": {"true_description": "The typed value, selection, opened dialog, new row or changed page is visible.",
                                            "false_description": "Nothing changed, an error message is shown, or the wrong element changed."}},
          "screen_matches_expected_state": {"type": "noul", "instructions": "Does the screen match the expected result described above?"}}
    return await llm.decide("step_verifier", parts, qs)


async def gather(*aws):
    return await asyncio.gather(*aws, return_exceptions=True)
