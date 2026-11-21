"""Store (all state for one study) and Agent (one per signed-in session).

The Agent runs the amendment through the state machine in state.py. Models only act
inside a state: Neon in diff, Argon in plan, Carbon in audit, Hydrogen in build/deploy/
transition, Decision 1 on every utterance and every navigator action. Every step lands
in the ledger and on the proof rail.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import agents, casebook, config, fixtures, llm, pdfutil, rules, seed
from .ledger import Ledger, record_hash
from .signatures import MEANINGS, Signer
from .state import Flow, TransitionError

RULE_ENGINE = "rule-engine"
MAX_QUESTIONS = 2   # audit findings asked aloud per build; the rest are listed, never dropped silently
PAGES = {
    "sponsor": ["amendment", "schedule", "events", "forms", "codelists", "rules", "review", "tests", "compare", "protocol", "record", "audit"],
    "cro": ["versions", "deployment", "rollout", "record", "audit"],
    "site": ["changes", "transition", "record", "audit"],
}


# The stages shown in the "Models at work" panel: (job, label, what it does). Jobs with a model
# take their model ID from config/models.yaml; the last two are deterministic code.
STAGES = [
    ("voice", "Voice", "Hears you and speaks the agent's lines"),
    ("utterance_gate", "Utterance gate", "Decides whether you were talking to the agent, and what you asked"),
    ("protocol_diff", "Protocol diff", "Reads the protocol PDF and lists what changed, with page and quote"),
    ("planner", "Build planner", "Writes the casebook diff, the rules and the test subjects"),
    ("auditor", "Independent auditor", "Checks the diff against the protocol without seeing the planner's reasoning"),
    ("audit_scoring", "Audit scoring", "Scores each audit finding: does a person need to decide?"),
    ("navigator", "Navigator", "Drives the application: clicks, types, selects"),
    ("step_verifier", "Step verifier", "Looks at a screenshot after each action"),
    ("rule_engine", "Rule engine", "Runs rules, tests and citation checks (fixed code, no model)"),
]


class Store:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or config.DATA_DIR
        self.reset()

    def reset(self):
        reg = seed.registry()
        self.study = reg["study"]
        self.registry_fetched = reg["fetched"]
        self.sites = seed.build_sites()
        self.users = seed.build_users(self.sites)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.ledger = Ledger(self.data_dir / f"ledger-{stamp}.jsonl")
        self.signer = Signer(self.ledger, self.users)
        self.v1 = casebook.seed_v1()
        self.casebooks = {1: self.v1}
        self.draft = copy.deepcopy(self.v1)
        self.pdf_path = config.ASSETS / "protocol.pdf"
        self.amendment = {"id": "AMD-001", "title": "Protocol Ver. 1.1 (16 May 2017)", "received": "2017-05-16",
                          "changes": [], "summary": None, "options": [], "builds": {}, "audits": {}, "chosen": None,
                          "tests": None, "build_check": None, "decisions": {}, "instruction": None}
        self.flow = Flow()
        self.site_flows: dict[str, Flow] = {}
        self.proofs: list[dict] = []
        self.signatures: dict = {"sponsor": None, "cro": None, "site": {}}
        self.deploy = {"production": None, "sites": {}}
        self.plans: dict[str, dict] = {}
        self.signed_record: dict | None = None
        self.costs: list[dict] = []
        self.pipeline = {j: {"status": "idle", "detail": "", "runs": 0, "calls": 0, "seconds": 0.0, "cost_usd": 0.0, "last_seq": None} for j, _, _ in STAGES}
        self.ledger.append(actor_type="rule", actor_id="system", role="system", action="study_seeded", record=self.study["num"],
                           after={"casebook_version": 1, "casebook_hash": casebook.content_hash(self.v1), "sites": len(self.sites)},
                           reason="Seeded from the ClinicalTrials.gov registry snapshot; staff and subjects are synthetic")

    def pdf(self) -> bytes:
        return self.pdf_path.read_bytes()

    def pipeline_view(self) -> list[dict]:
        fx = config.mode() == "fixture"
        out = []
        for job, label, what in STAGES:
            model = "fixed code" if job == "rule_engine" else (fixtures.FIXTURE_MODEL if fx and job != "voice" else config.job(job)["model"])
            out.append({"job": job, "label": label, "what": what, "model": model, **self.pipeline[job]})
        return out

    def on_model_call(self, out: dict):
        self.costs.append({"job": out.get("job"), "model": out.get("model"), "cost_usd": out.get("cost_usd", 0),
                           "latency_s": out.get("latency_s", 0), "usage": out.get("usage", {})})
        p = self.pipeline.get(out.get("job"))
        if p:
            p["calls"] += 1
            p["seconds"] = round(p["seconds"] + out.get("latency_s", 0), 2)
            p["cost_usd"] = round(p["cost_usd"] + out.get("cost_usd", 0), 5)

    def site(self, sid: str) -> dict:
        return next(s for s in self.sites if s["id"] == sid)

    def build(self) -> dict | None:
        return self.amendment["builds"].get(self.amendment["chosen"]) if self.amendment["chosen"] else None

    def record_for_signature(self, role: str, site_id: str | None = None) -> tuple[str, str, dict]:
        """(record_type, record_id, record) that a signature by this role binds to."""
        if role == "sponsor":
            b = self.build() or {}
            return "casebook", "CB-v2", {"study": self.study["num"], "amendment": self.amendment["id"], "option": self.amendment["chosen"],
                                         "casebook": casebook.content(self.draft), "tests": {k: (self.amendment["tests"] or {}).get(k) for k in ("total", "passed")},
                                         "ops": [o["op_id"] for o in b.get("ops", [])]}
        if role == "cro":
            return "rollout", "ROLLOUT-v2", {"casebook_hash": casebook.content_hash(self.casebooks[2]), "production": self.deploy["production"],
                                             "sites": {k: v for k, v in sorted(self.deploy["sites"].items())}}
        return "transition_plan", f"TP-{site_id}", {"casebook_hash": casebook.content_hash(self.casebooks[2]), "plan": self.plans.get(site_id)}

    def eligible_sites(self) -> list[str]:
        return [s["id"] for s in self.sites if s["ec_approved"]]


class Agent:
    """One per browser session. Talks to its UI through `send` (a coroutine taking a dict)."""

    def __init__(self, store: Store, hub, user_id: str, send):
        self.store, self.hub, self.user_id, self.send = store, hub, user_id, send
        self.user = store.users[user_id]
        self.role = self.user["role"]
        self.site_id = self.user.get("site_id")
        self.task: asyncio.Task | None = None
        self.running = asyncio.Event()
        self.running.set()
        self.pending_acts: dict[str, asyncio.Future] = {}
        self.current_goal: dict | None = None
        self.focus: dict | None = None        # what "why?" and "show me the source" refer to
        self.question: dict | None = None     # a yes/no the agent is waiting on (audit finding)
        self.dot_state = ("waiting", "")
        self.nav_target: str | None = None    # "env" while the agent works in the embedded environment
        self.tour_task: asyncio.Task | None = None
        self.nav_stats = {"steps": 0, "verified_ok": 0, "verify_failed": 0, "recoveries": 0, "goals": 0, "goals_ok": 0}

    # ---------------------------------------------------------------- UI plumbing
    @property
    def flow(self) -> Flow:
        if self.role == "site":
            return self.store.site_flows.get(self.site_id) or Flow("handoff_cro")
        return self.store.flow

    async def dot(self, state: str, caption: str = ""):
        self.dot_state = (state, caption)
        await self.send({"type": "dot", "state": state, "caption": caption})

    async def say(self, text: str, wait: bool = False):
        await self.send({"type": "say", "text": text})

    async def changed(self):
        await self.hub.broadcast({"type": "changed"})

    def log(self, action: str, **kw) -> dict:
        return self.store.ledger.append(actor_type="person", actor_id=self.user_id, role=self.role, action=action, **kw)

    async def proof(self, title: str, *, kind: str, actor: str, action: str, model_out: dict | None = None, page: int | None = None,
                    quote: str | None = None, check: dict | None = None, record: str | None = None, before=None, after=None,
                    reason: str | None = None, detail: str | None = None) -> dict:
        """One step on the proof rail = one ledger entry. kind is ai | rule | person."""
        fixture = bool(model_out and model_out.get("fixture"))
        entry = self.store.ledger.append(
            actor_type=kind, actor_id=actor, role=self.role, action=action, record=record, before=before, after=after, reason=reason,
            source={"page": page, "quote": quote} if page or quote else None,
            model=(model_out or {}).get("model") if kind == "ai" else None, prompt_hash=(model_out or {}).get("prompt_hash") if kind == "ai" else None,
            extra={"check": check, **({"replayed": True} if (model_out or {}).get("replayed") else {})} if check or (model_out or {}).get("replayed") else None)
        p = {"seq": entry["seq"], "hash": entry["hash"], "ts": entry["ts"], "title": title, "kind": kind, "actor": actor,
             "model": entry["model"], "page": page, "quote": quote, "check": check, "detail": detail, "state": self.flow.state,
             "role": self.role, "fixture": fixture}
        self.store.proofs.append(p)
        await self.hub.broadcast({"type": "proof", "proof": p})
        return p

    @contextlib.asynccontextmanager
    async def work(self, job: str, detail: str, done: str | None = None):
        """Mark a stage as running for the live panel, then done or failed."""
        p = self.store.pipeline[job]
        p.update(status="running", detail=detail)
        p["runs"] += 1
        await self.hub.broadcast({"type": "pipeline", "pipeline": self.store.pipeline_view()})
        note = {"detail": done}
        try:
            yield note
            p.update(status="done", detail=note["detail"] or detail)
        except asyncio.CancelledError:
            p.update(status="idle")
            raise
        except Exception as e:
            p.update(status="failed", detail=f"{type(e).__name__}: {e}"[:160])
            raise
        finally:
            p["last_seq"] = len(self.store.ledger.entries)
            await self.hub.broadcast({"type": "pipeline", "pipeline": self.store.pipeline_view()})

    async def checkpoint(self):
        """Pause point: 'stop' clears the event, 'continue' sets it."""
        if not self.running.is_set():
            await self.dot("waiting", "Paused. Say “continue” when you are ready.")
            await self.running.wait()

    def fire(self, event: str, by_human: bool = False):
        prev = self.flow.state
        self.flow.fire(event, self.role, by_human=by_human)
        self.store.ledger.append(actor_type="person" if by_human else "rule", actor_id=self.user_id if by_human else "state-machine",
                                 role=self.role, action="state_change", before=prev, after=self.flow.state, reason=event)

    def start(self, coro):
        if self.task and not self.task.done():
            self.task.cancel()
        self.running.set()
        self.task = asyncio.create_task(self._guard(coro))

    async def _quiet(self, coro):
        """Run a side task; a failure in it is logged and never stops the main flow."""
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.store.ledger.append(actor_type="rule", actor_id="system", role=self.role, action="side_task_failed", reason=f"{type(e).__name__}: {e}"[:200])

    async def _guard(self, coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never die silently: tell the person and log it
            self.store.ledger.append(actor_type="rule", actor_id="system", role=self.role, action="error", reason=f"{type(e).__name__}: {e}")
            await self.dot("waiting", f"Something went wrong: {e}")
            await self.say("I hit a problem and stopped. Details are on screen.")
            await self.changed()

    # ---------------------------------------------------------------- greeting
    def briefing(self) -> dict:
        """Catch-up for the person who just signed in: where the amendment stands with each of
        the three roles, and what is theirs to do next. Built from recorded state by a fixed
        template, so it cannot say anything that did not happen."""
        st, a, g = self.store, self.store.amendment, self.store.flow.state
        order = ["intake", "diff", "plan", "audit", "gate_option", "build", "verify", "gate_approve", "sign", "handoff_cro", "deploy", "rollout", "handoff_site"]
        at = order.index(g)
        day = lambda m: m["signed_at"][:10]
        n_edc = sum(bool(c.get("affects_edc")) for c in a["changes"])
        sig = st.signatures
        # sponsor
        if sig["sponsor"]:
            sp = ("done", f"{sig['sponsor']['printed_name']} signed casebook version 2 on {day(sig['sponsor'])}: option {a['chosen']}, "
                          f"{len(st.build()['ops'])} changes, {a['tests']['passed']} of {a['tests']['total']} test subjects passing.")
        elif g == "intake":
            sp = ("active", f"{a['title']} arrived on {a['received']}. Nobody has opened it yet.")
        elif at < order.index("gate_option"):
            sp = ("active", "The amendment is being read and planned.")
        elif g == "gate_option":
            sp = ("active", f"The amendment has been read: {n_edc} changes affect the build. {len(a['options'])} designs are planned and audited. None is chosen yet.")
        elif g in ("build", "verify"):
            sp = ("active", f"Option {a['chosen']} is being built in the casebook.")
        elif g == "gate_approve":
            sp = ("active", f"Option {a['chosen']} is built and verified, {a['tests']['passed']} of {a['tests']['total']} test subjects pass. It is not approved yet.")
        else:
            sp = ("active", f"Option {a['chosen']} is approved and waiting for the sponsor's signature.")
        # cro
        live, total = len(st.deploy["sites"]), len(st.sites)
        if sig["cro"]:
            cr = ("done", f"{sig['cro']['printed_name']} signed the rollout on {day(sig['cro'])}: version 2 is live at {live} of {total} sites, {total - live} are waiting for ethics approval.")
        elif not sig["sponsor"]:
            cr = ("waiting", "Nothing to deploy until the sponsor signs.")
        elif g == "handoff_cro":
            cr = ("active", "Deployment has not started.")
        elif g == "deploy":
            cr = ("active", "Deployment is in progress.")
        else:
            cr = ("active", f"Version 2 is in production and live at {live} of {total} sites. The rollout is not signed yet.")
        # sites
        signed = len(sig["site"])
        if not sig["cro"]:
            si = ("waiting", "Sites are still on casebook version 1.")
        else:
            si = ("done" if signed == live else "active", f"{signed} of {live} live sites have signed off their transition plan.")
        roles = [{"role": "sponsor", "label": "Sponsor", "state": sp[0], "text": sp[1]}, {"role": "cro", "label": "CRO", "state": cr[0], "text": cr[1]},
                 {"role": "site", "label": "Sites", "state": si[0], "text": si[1]}]

        f, task = self.flow, None
        if self.role == "sponsor":
            nxt = {"intake": "One amendment is waiting for you: open it and I will read it.",
                   "gate_option": "The plan is ready. I need you to choose a design.",
                   "gate_approve": "The build is verified and waiting for your approval.",
                   "sign": "The build is approved and waiting for your signature."}.get(f.state)
            task = nxt and {"intake": "Open the amendment", "gate_option": "Choose a design", "gate_approve": "Approve the build", "sign": "Sign"}[f.state]
            if f.owner != "sponsor":
                nxt = "Nothing is waiting for you."
            nxt = nxt or "I am working on the amendment."
        elif self.role == "cro":
            nxt = {"handoff_cro": "Casebook version 2 is signed and ready to deploy. That is yours.",
                   "rollout": "Deployment is done. The rollout is waiting for your signature."}.get(f.state)
            task = nxt and {"handoff_cro": "Deploy and roll out", "rollout": "Sign the rollout"}[f.state]
            if not nxt:
                nxt = "Nothing is waiting. The sponsor has not signed casebook version 2 yet." if st.flow.owner == "sponsor" else "Nothing is waiting for you."
        else:
            if self.site_id in st.deploy["sites"]:
                n = seed.transition_plan(st.site(self.site_id), st.v1, st.casebooks[2])["affected"]
                nxt = {"handoff_site": f"Casebook version 2 is live at your site. {n} participant{'s are' if n != 1 else ' is'} affected. Your task is the transition plan.",
                       "site_sign": "The transition plan is ready for your sign-off.", "done": "You have signed off. Nothing is waiting."}.get(f.state, "I am preparing your transition plan.")
                task = {"handoff_site": "Open the transition plan", "site_sign": "Sign off"}.get(f.state)
            else:
                nxt = "Nothing is waiting. Casebook version 2 has not been deployed to your site yet."
        # what the person hears: the other roles first (what they missed), then their own task
        mine = {"sponsor": 0, "cro": 1, "site": 2}[self.role]
        heard = [r["text"] for i, r in enumerate(roles) if i != mine and r["state"] != "waiting"]
        if roles[mine]["state"] == "done" or (task is None and roles[mine]["state"] == "active" and self.role != "site"):
            heard.insert(0, roles[mine]["text"])
        first = self.user["name"].split()[0]
        spoken = f"Hello {first}. " + " ".join(heard) + (" " if heard else "") + nxt + (" Say “do it” when you want me to start." if task else "")
        return {"roles": roles, "next": nxt, "task": task, "spoken": spoken, "name": self.user["name"], "title": self.user["title"]}

    async def greet(self):
        b = self.briefing()
        fx = " (Fixture mode: hand-written test data, not model output.)" if agents.fixture_mode() else ""
        await self.dot("waiting", b["next"] + fx)
        await self.say(b["spoken"])

    # ---------------------------------------------------------------- utterances
    def task_context(self) -> str:
        f, a = self.flow, self.store.amendment
        desc = {"intake": "waiting for the user to open the new protocol amendment",
                "diff": "reading the protocol", "plan": "planning the casebook changes", "audit": "auditing the plan",
                "gate_option": "waiting for the user to choose a design option (" + ", ".join(o["id"] for o in a["options"]) + ")",
                "build": "building the casebook in the EDC application", "verify": "running tests",
                "gate_approve": "waiting for the user to approve the verified build", "sign": "waiting for the user to sign",
                "handoff_cro": "waiting for the CRO user to start deployment", "deploy": "deploying the casebook",
                "rollout": "waiting for the CRO user to sign the rollout", "handoff_site": "waiting for the site user to open the transition plan",
                "transition": "preparing the transition plan", "site_sign": "waiting for the site user to sign off"}.get(f.state, f.state)
        if self.question:
            desc += f"; it just asked the user: “{self.question['text']}” (yes or no)"
        return f"Role: {self.role}. The assistant is {desc}."

    async def on_utterance(self, transcript: str, audio_b64: str | None = None, audio_mime: str = "audio/wav", typed: bool = False) -> dict:
        """Every user turn goes through the Decision 1 gate. Typed input skips only the
        'addressed to me' question, since typing into the agent's box is unambiguous."""
        t0 = time.time()
        async with self.work("utterance_gate", f"Heard: “{transcript[:60]}”") as w:
            g = await agents.utterance_gate(transcript, audio_b64, self.task_context(), audio_mime)
            route = agents.route_utterance(g["answers"])
            w["detail"] = f"“{transcript[:40]}” → {route['action']}" + (f": {route['intent']}" if route["action"] == "act" else "") + f" (P addressed {route['p_addressed']:.2f})"
        if typed and route["action"] != "act" and route["intent"] != "other":
            route["action"] = "act"
        self.store.ledger.append(actor_type="ai", actor_id="utterance-gate", role=self.role, action=f"utterance_{route['action']}",
                                 model=g["model"], prompt_hash=g["prompt_hash"],
                                 after={"transcript": transcript, **{k: route[k] for k in ("intent", "option", "p_addressed", "p_relevant", "p_intent")}})
        route["gate_latency_s"] = round(time.time() - t0, 3)
        if route["action"] == "ignore":
            return route  # say nothing at all
        if route["action"] == "ask":
            await self.say("Was that for me?")
            return route
        await self.handle_intent(route["intent"], route.get("option"), transcript)
        return route

    async def handle_intent(self, intent: str, option: str | None = None, transcript: str = ""):
        f, st = self.flow, self.store
        if intent == "stop":
            self.running.clear()
            self.log("pause", reason=transcript or "stop")
            await self.send({"type": "halt"})
            await self.dot("waiting", "Stopped. Say “continue” when you are ready.")
            return
        if intent == "resume":
            self.running.set()
            await self.dot("acting", "Continuing.")
            return
        if intent == "explain":
            return await self.explain()
        if intent == "show_source":
            return await self.show_source()
        if self.question and intent in ("approve", "reject"):
            return await self.answer_question(intent == "approve")
        if intent == "undo":
            return await self.go_back()
        if intent == "reject":
            if f.can("reject", self.role):
                self.fire("reject", by_human=True)
                st.draft = copy.deepcopy(st.v1)
                await self.changed()
                return await self.present_options("Rejected. Here are the options again.")
            return await self.say("There is nothing to reject right now.")
        if intent == "choose_option":
            if not f.can("choose_option", self.role):
                return await self.say("I can't change the option at this point.")
            opt = option or next((o["id"] for o in st.amendment["options"] if o["recommended"]), None)
            if opt not in st.amendment["builds"]:
                return await self.say(f"There is no option {opt}.")
            return self.start(self.choose(opt))
        # everything else means "go ahead with the next thing that is mine to decide"
        if intent in ("open_amendment", "approve", "sign", "deploy", "open_transition"):
            return await self.proceed(intent)
        await self.say("I can't do that here.")

    async def proceed(self, intent: str):
        f, st = self.flow, self.store
        if f.owner != self.role:
            return await self.say("Nothing is waiting for you right now.")
        s = f.state
        if s == "intake":
            return self.start(self.run_sponsor())
        if s == "gate_option":
            rec = next(o["id"] for o in st.amendment["options"] if o["recommended"])
            return self.start(self.choose(rec))
        if s == "gate_approve":
            self.fire("approve", by_human=True)
            await self.changed()
            return await self.open_signature()
        if s in ("sign", "rollout", "site_sign"):
            return await self.open_signature()
        if s == "handoff_cro":
            return self.start(self.run_cro())
        if s == "handoff_site":
            return self.start(self.run_site())
        await self.say("I am still working. I will ask when I need you.")

    async def open_signature(self):
        rtype, rid, rec = self.store.record_for_signature(self.role, self.site_id)
        await self.dot("waiting", "A spoken yes is not a signature. Sign with your user ID and password.")
        await self.say("A spoken yes is not a signature. Please sign with your user ID and password.")
        await self.send({"type": "open_sign", "meaning": MEANINGS[self.role], "record_id": rid, "record_type": rtype,
                         "record_hash": record_hash(rec), "user_id": self.user_id, "name": self.user["name"]})

    async def explain(self):
        fo = self.focus or {}
        text = fo.get("why") or {"gate_option": "I stopped because choosing between designs is a judgement about your study, not something the protocol decides.",
                                 "gate_approve": "I stopped because only you can approve a build for signature.",
                                 "sign": "Signing needs your user ID and password. Voice alone is not a valid electronic signature."}.get(self.flow.state, "I am following the steps for this amendment in order.")
        self.store.ledger.append(actor_type="rule", actor_id="orchestrator", role=self.role, action="explained", after=text,
                                 reason="Read back from the stored record; no new model output")
        await self.say(text)

    async def show_source(self):
        fo = self.focus or {}
        if not fo.get("page"):
            return await self.say("This step has no protocol source. It is marked as inferred.")
        await self.send({"type": "open_pdf", "page": fo["page"], "quote": fo.get("quote")})
        await self.say(f"This is page {fo['page']}, with the quote highlighted.")

    async def go_back(self):
        f = self.flow
        if f.can("undo", self.role):
            self.fire("undo", by_human=True)
            await self.send({"type": "close_sign"})
            await self.changed()
            return await self.dot("waiting", "Back at approval. The build is verified and waiting for you.")
        if f.can("reject", self.role):
            return await self.handle_intent("reject")
        await self.say("There is nothing to go back to here.")

    # ---------------------------------------------------------------- the environment, while the protocol is read
    async def tour(self):
        """While Neon reads the protocol, open the existing RELAY environment beside the orb and do
        one small task in it, so the person can watch the agent work. Hydrogen picks the clicks."""
        # RELAY_TOUR=0 switches it off (needed to replay a session recorded before the tour existed)
        if os.environ.get("RELAY_TOUR", "1") == "0" or not (config.ROOT / "reference" / "index.html").exists():
            return
        await self.send({"type": "env", "show": True})
        self.nav_target = "env"
        try:
            ok = await self.run_goal(
                "You are in the existing RELAY study environment, a different application from the casebook designer. "
                "It has no page ids, so never use navigate: only click. Do one small task so the reviewer can see the study "
                "as it stands today: first click the button that opens the Sponsor workspace (labelled “1 Sponsor”), then in "
                "the left menu click “Schedule” to show the current visit schedule. One click per turn. When the schedule is "
                "showing, call done.",
                check=lambda: [], pages=[],
                fixture_actions=[("click", {"label": "1 Sponsor"}), ("click", {"label": "Schedule"})])
            await self.proof("Opened the RELAY environment and showed the current visit schedule", kind="ai", actor="navigator", action="environment_tour",
                             model_out={"model": _nav_model(), "prompt_hash": None, "fixture": agents.fixture_mode()},
                             check={"label": "Shown beside the agent while the protocol was read", "ok": ok, "by": "orchestrator"})
        finally:
            self.nav_target = None

    async def end_tour(self):
        """The tour never holds up the real work: give it a moment, then stop it."""
        t = self.tour_task
        if t and not t.done():
            try:
                await asyncio.wait_for(asyncio.shield(t), 25)
            except (asyncio.TimeoutError, Exception):
                t.cancel()
        self.nav_target = None
        self.current_goal = None

    # ---------------------------------------------------------------- sponsor: intake to gate_option
    async def run_sponsor(self):
        st, a = self.store, self.store.amendment
        self.fire("open_amendment", by_human=True)
        pdf = st.pdf()
        n = pdfutil.page_count(pdf)
        self.tour_task = asyncio.create_task(self._quiet(self.tour()))
        await self.dot("thinking", f"Reading the protocol ({n} pages)")
        async with self.work("protocol_diff", f"Reading the protocol ({n} pages)") as w:
            r = await agents.neon_diff(pdf, None, agents.casebook_brief(st.v1))
            d = r["json"]
            w["detail"] = f"{len(d['changes'])} changes found, {sum(bool(c['affects_edc']) for c in d['changes'])} affect the build"
        if d.get("protocol_id") and _norm_id(d["protocol_id"]) != _norm_id(st.study["num"]):
            # a protocol from another study: start from an empty casebook instead of pretending
            st.v1 = casebook.blank(d["protocol_id"])
            st.casebooks = {1: st.v1}
            st.draft = copy.deepcopy(st.v1)
            await self.proof(f"Protocol is for {d['protocol_id']}, not {st.study['num']}: starting from an empty casebook", kind="rule",
                             actor="orchestrator", action="blank_casebook", after=d["protocol_id"])
        async with self.work("rule_engine", "Checking every quote against the PDF") as w:
            for c in d["changes"]:
                c["citation"] = pdfutil.locate(pdf, c["quote"], c["page"])
                if c["citation"]["moved"]:
                    c["page"] = c["citation"]["page"]  # the quote is real but on another page: use where it actually is
            ok_n = sum(c["citation"]["ok"] or c["citation"]["moved"] for c in d["changes"])
            w["detail"] = f"{ok_n} of {len(d['changes'])} quotes found in the PDF"
        a["changes"], a["summary"] = d["changes"], d["spoken_summary"]
        a["protocol"] = {k: d.get(k) for k in ("protocol_id", "version", "previous_version")}
        edc = [c for c in d["changes"] if c["affects_edc"]]
        for c in edc:
            cit = c["citation"]
            await self.proof(c["summary"], kind="ai", actor="protocol-diff", action="protocol_change", model_out=r, page=c["page"], quote=c["quote"],
                             record=c["id"], after={"type": c["type"], "affects_edc": True},
                             check={"label": "Quote found on the cited page" if cit["ok"] else ("Quote found on p. %s, citation corrected" % cit["page"] if cit["moved"] else "Quote NOT found in the PDF"),
                                    "ok": cit["ok"] or cit["moved"], "by": RULE_ENGINE})
        await self.end_tour()
        await self.dot("thinking", "The protocol is read")
        await self.say(d["spoken_summary"])
        self.fire("diff_done")
        await self.changed()
        await self.checkpoint()
        usable = [c for c in edc if c["citation"]["ok"] or c["citation"]["moved"]]
        if not usable:
            await self.dot("waiting", "I found no change that affects the EDC build.")
            return await self.say("I found no change that affects the build, so there is nothing to plan.")

        await self.dot("thinking", f"Planning the build for {len(usable)} changes")
        async with self.work("planner", "Proposing design options") as w:
            ro = await agents.argon_options(usable, st.v1)
            a["options"] = ro["json"]["options"]
            w["detail"] = f"{len(a['options'])} design options proposed"
        if not any(o["recommended"] for o in a["options"]):
            a["options"][0]["recommended"] = True
        await self.proof(f"Proposed {len(a['options'])} design options", kind="ai", actor="planner", action="options", model_out=ro,
                         after=[{"id": o["id"], "name": o["name"], "recommended": o["recommended"]} for o in a["options"]])
        await self.changed()
        await self.dot("thinking", "Writing the casebook diff and test subjects for each option")
        async with self.work("planner", f"Writing the diff and test subjects for {len(a['options'])} options") as w:
            built = await agents.gather(*[self.plan_option(o, usable) for o in a["options"]])
            w["detail"] = "; ".join(f"{k}: {len(b['ops'])} operations, {len(b['subjects'])} subjects" for k, b in a["builds"].items()) or "no valid diff"
        for o, b in zip(a["options"], built):
            if isinstance(b, Exception):
                o["unavailable"] = str(b)
        a["options"] = [o for o in a["options"] if o["id"] in a["builds"]] or a["options"]
        if not a["builds"]:
            raise RuntimeError("The planner could not produce a valid casebook diff for any option")
        if not any(o["recommended"] and o["id"] in a["builds"] for o in a["options"]):
            a["options"][0]["recommended"] = True
        self.fire("plan_done")
        await self.changed()
        await self.checkpoint()

        await self.dot("thinking", "Independent audit of each option against the protocol")
        async with self.work("auditor", "Auditing each option against the protocol") as w:
            await agents.gather(*[self.audit_option(o["id"], usable) for o in a["options"] if o["id"] in a["builds"]])
            w["detail"] = "; ".join(f"{k}: {len(v['findings'])} findings" for k, v in a["audits"].items())
        self.store.pipeline["audit_scoring"].update(status="done", detail="; ".join(f"{k}: {sum(f['routed'] for f in v['findings'])} need a person" for k, v in a["audits"].items()))
        self.fire("audit_done")
        await self.changed()
        await self.present_options()

    async def plan_option(self, option: dict, changes: list[dict], instruction: str | None = None):
        """Argon writes the diff and the subjects; the engine validates both; one repair round."""
        st, a = self.store, self.store.amendment
        problems, prev, out = None, None, None
        for attempt in range(2):
            out = await agents.argon_build(changes, st.v1, option, problems, prev, instruction)
            build = out["build"]
            problems = casebook.validate_diff(st.v1, build["ops"])
            tests = None
            if not problems:
                tests = rules.run_tests(casebook.apply_diff(st.v1, build["ops"]), build["subjects"])
                problems = [{"subject": r["subject"], "problem": "test failed", "checks": r["checks"], "errors": r["errors"]} for r in tests["results"] if not r["pass"]]
            if not problems:
                break
            prev = out["json"]
            await self.proof(f"Option {option['id']}: engine rejected the first draft ({len(problems)} problem{'s' if len(problems) != 1 else ''}), sent back to the planner",
                             kind="rule", actor=RULE_ENGINE, action="plan_rejected", record=f"OPT-{option['id']}", after=problems[:5], check={"label": "Diff applies and tests pass", "ok": False, "by": RULE_ENGINE})
        cid = {c["id"]: c for c in changes}
        pdf = st.pdf()
        for op in build["ops"]:
            # the citation on each operation is checked the same way as Neon's
            if op.get("source_quote"):
                op["citation"] = pdfutil.locate(pdf, op["source_quote"], op.get("source_page"))
                if op["citation"]["moved"]:
                    op["source_page"] = op["citation"]["page"]
            elif op.get("source_change_ids") and op["source_change_ids"][0] in cid:
                c = cid[op["source_change_ids"][0]]
                op["source_page"], op["source_quote"], op["citation"] = c["page"], c["quote"], c["citation"]
            else:
                op["citation"] = None
        build.update(model=out["model"], prompt_hash=out["prompt_hash"], fixture=bool(out.get("fixture")), valid=not problems,
                     problems=problems, tests_planned=tests, repaired=attempt > 0)
        if problems and any("op_id" in p for p in problems):
            raise RuntimeError(f"Option {option['id']}: the diff still does not apply after one repair round")
        a["builds"][option["id"]] = build
        await self.proof(f"Option {option['id']}: {len(build['ops'])} operations, {len(build['subjects'])} test subjects", kind="ai", actor="planner",
                         action="casebook_diff", model_out=out, record=f"OPT-{option['id']}", after=[o["op_id"] + " " + o["kind"] + " " + o["target_id"] for o in build["ops"]],
                         check={"label": f"Diff applies to v1; {tests['passed']}/{tests['total']} subjects pass in the engine" if tests else "Diff does not apply",
                                "ok": bool(tests) and tests["passed"] == tests["total"], "by": RULE_ENGINE})

    async def audit_option(self, oid: str, changes: list[dict]):
        st, a = self.store, self.store.amendment
        build = a["builds"][oid]
        r = await agents.carbon_audit(st.pdf(), changes, st.v1, build, oid)
        findings = r["json"]["findings"]
        sc = await agents.score_findings(findings, build)
        th = config.job("audit_scoring")["thresholds"]["route_to_human"]
        for f in findings:
            ans = sc["answers"].get(f["id"].replace(" ", "_"))
            f["p_needs_human"] = ans["p"] if ans else None
            f["routed"] = bool(ans and ans["p"] >= th)
        a["audits"][oid] = {"findings": findings, "overall_uncertainty": r["json"].get("overall_uncertainty", ""), "model": r["model"],
                            "scorer": sc.get("model"), "fixture": bool(r.get("fixture"))}
        routed = [f for f in findings if f["routed"]]
        await self.proof(f"Option {oid} audited: {len(findings)} finding{'s' if len(findings) != 1 else ''}, {len(routed)} need a person", kind="ai",
                         actor="auditor", action="audit", model_out=r, record=f"OPT-{oid}", after=[{k: f[k] for k in ("id", "kind", "op_id", "severity", "p_needs_human")} for f in findings],
                         check={"label": "Findings scored by Decision 1", "ok": True, "by": sc.get("model")}, detail="; ".join(f["detail"] for f in routed)[:400] or None)

    async def present_options(self, lead: str | None = None):
        a = self.store.amendment
        rec = next(o for o in a["options"] if o["recommended"])
        self.focus = {"why": rec["tradeoff"] + " I recommend it, but the choice is yours.", "page": None}
        ids = ", ".join(o["id"] for o in a["options"])
        await self.dot("waiting", f"Choose a design: {ids}. I recommend {rec['id']}.")
        await self.send({"type": "gate", "kind": "option"})
        await self.say(f"{lead + ' ' if lead else ''}{rec['tradeoff']} I recommend option {rec['id']}. Which one do you want?")

    # ---------------------------------------------------------------- sponsor: choose, build, verify
    async def choose(self, oid: str):
        st, a = self.store, self.store.amendment
        self.fire("choose_option", by_human=True)
        a["chosen"] = oid
        st.draft = copy.deepcopy(st.v1)
        a["tests"] = a["build_check"] = None
        self.log("option_chosen", record=f"OPT-{oid}", after=oid, reason="Chosen at the design gate")
        await self.send({"type": "gate", "kind": None})
        await self.changed()
        # audit findings that need a person are asked one at a time, before anything is built
        routed = sorted([f for f in a["audits"].get(oid, {}).get("findings", []) if f["routed"] and f["id"] not in a["decisions"].get(oid, {})],
                        key=lambda f: (-{"high": 2, "medium": 1, "low": 0}[f["severity"]], -(f.get("p_needs_human") or 0)))
        ask, listed = routed[:MAX_QUESTIONS], routed[MAX_QUESTIONS:]
        if listed:
            # not hidden: recorded, shown on the Amendment page, and said out loud
            for f in listed:
                a["decisions"].setdefault(oid, {})[f["id"]] = "listed for review"
            self.store.ledger.append(actor_type="rule", actor_id="orchestrator", role=self.role, action="findings_listed", record=f"OPT-{oid}",
                                     after=[f["id"] for f in listed], reason=f"More than {MAX_QUESTIONS} findings need a person; the rest are listed on the Amendment page for review before signing")
            await self.say(f"The auditor raised {len(routed)} points that need a person. I will ask you about the {len(ask)} most serious. The other {len(listed)} are listed on the Amendment page for you to review before you sign.")
        for f in ask:
            op = next((o for o in a["builds"][oid]["ops"] if o["op_id"] == f.get("op_id")), None)
            can_drop = bool(op) and f["kind"] in ("untraceable", "mismatch")
            text = (f"The auditor could not trace one item to the protocol. {f['detail']} Keep it?" if f["kind"] == "untraceable"
                    else f"The auditor flagged this. {f['detail']} " + ("Keep it as planned?" if can_drop else "Go ahead anyway?"))
            self.focus = {"why": f["detail"] + (" The planner's reason was: " + op["rationale"] if op else ""), "page": f.get("page") or (op or {}).get("source_page"),
                          "quote": (op or {}).get("source_quote")}
            self.question = {"text": text, "finding": f, "op": op, "can_drop": can_drop, "future": asyncio.get_event_loop().create_future()}
            await self.dot("waiting", text)
            await self.send({"type": "question", "text": text, "finding": f, "yes": "Keep it", "no": "Drop it" if can_drop else "Stop"})
            await self.say(text)
            keep = await self.question["future"]
            self.question = None
            await self.send({"type": "question", "text": None})
            a["decisions"].setdefault(oid, {})[f["id"]] = "kept" if keep else "dropped"
            self.log("finding_decided", record=f["id"], after="kept" if keep else "dropped", reason=f["detail"])
            if not keep and can_drop:
                a["builds"][oid]["ops"] = [o for o in a["builds"][oid]["ops"] if o["op_id"] != op["op_id"]]
            elif not keep:
                self.fire("build_aborted")
                return await self.present_options("Stopped.")
        await self.build_and_verify()

    async def answer_question(self, yes: bool):
        q = self.question
        if q and not q["future"].done():
            q["future"].set_result(yes)

    async def build_and_verify(self):
        st, a = self.store, self.store.amendment
        build = st.build()
        await self.send({"type": "env", "show": False})
        await self.send({"type": "canvas", "show": True})
        ok = True
        for op in build["ops"]:
            await self.checkpoint()
            self.focus = {"why": op["rationale"] + (" I am not sure about this: " + op["uncertainty"] if op.get("uncertainty") else ""),
                          "page": op.get("source_page"), "quote": op.get("source_quote")}
            if casebook.reflects(st.draft, op) == []:
                continue
            done = await self.run_goal(goal_for_op(op), op=op, check=lambda op=op: casebook.reflects(st.draft, op), fixture_actions=fixture_actions(op, st.draft))
            cit = op.get("citation")
            await self.proof(op_title(op), kind="ai", actor="navigator", action="op_built", record=op["op_id"],
                             model_out={"model": config.job("navigator")["model"] if not build.get("fixture") else fixtures.FIXTURE_MODEL, "prompt_hash": build["prompt_hash"], "fixture": build.get("fixture")},
                             page=op.get("source_page"), quote=op.get("source_quote"),
                             check={"label": "Casebook shows the planned change" if done else "Casebook does NOT show the planned change", "ok": done, "by": RULE_ENGINE},
                             detail=(None if cit and (cit["ok"] or cit["moved"]) else "No protocol source: " + (op.get("uncertainty") or "inferred by the planner")) if not (cit and cit["ok"]) else None)
            if not done:
                ok = False
                break
        if not ok:
            self.fire("build_aborted")
            await self.changed()
            return await self.present_options("I could not complete the build in the application, so I stopped. Nothing was approved.")
        self.fire("build_done")
        await self.changed()

        await self.dot("thinking", "Verifying: build against plan, then the test subjects")
        await self.act("navigate", {"page": "tests"})
        expected = casebook.apply_diff(st.v1, build["ops"])
        same = casebook.content_hash(expected) == casebook.content_hash(st.draft)
        a["build_check"] = {"ok": same, "expected_hash": casebook.content_hash(expected), "built_hash": casebook.content_hash(st.draft),
                            "differences": [] if same else casebook.differences(casebook.content(expected), casebook.content(st.draft))[:12]}
        await self.proof("Built casebook equals the planned diff applied to version 1", kind="rule", actor=RULE_ENGINE, action="build_check",
                         after=a["build_check"], check={"label": "Content hashes match" if same else "Content hashes differ", "ok": same, "by": RULE_ENGINE})
        async with self.work("rule_engine", "Running the synthetic subjects on the built casebook") as w:
            tests = rules.run_tests(st.draft, build["subjects"])
            w["detail"] = f"{tests['passed']} of {tests['total']} subjects pass; build {'matches' if same else 'does NOT match'} the plan"
        a["tests"] = tests
        await self.proof(f"{tests['passed']} of {tests['total']} synthetic subjects pass on the built casebook", kind="rule", actor=RULE_ENGINE,
                         action="tests_run", after={"passed": tests["passed"], "total": tests["total"], "rules_fired": tests["rules_fired"]},
                         check={"label": f"{tests['passed']}/{tests['total']} pass", "ok": tests["passed"] == tests["total"], "by": RULE_ENGINE})
        chain = st.ledger.verify()
        passed = same and tests["total"] > 0 and tests["passed"] == tests["total"] and chain["ok"]
        self.fire("verify_passed" if passed else "verify_failed")
        await self.changed()
        if not passed:
            return await self.present_options("Verification failed, so I did not ask for approval.")
        self.focus = {"why": "Only you can approve a build for signature. I checked that it matches the plan and that every test subject passes.", "page": None}
        line = f"Built and verified. {tests['passed']} of {tests['total']} test subjects pass and the build matches the plan. Do you approve?"
        await self.dot("waiting", line)
        await self.send({"type": "gate", "kind": "approve"})
        await self.say(line)

    # ---------------------------------------------------------------- navigator loop
    async def act(self, name: str, args: dict, timeout: float = 20) -> dict:
        """Ask the UI to perform one action; resolves with the result, the element tree and a screenshot."""
        aid = uuid.uuid4().hex[:8]
        fut = asyncio.get_event_loop().create_future()
        self.pending_acts[aid] = fut
        await self.send({"type": "act", "id": aid, "action": {"name": name, "args": args, "target": self.nav_target}})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "The application did not respond", "tree": [], "page": "?", "screenshot": None}
        finally:
            self.pending_acts.pop(aid, None)

    def on_act_result(self, msg: dict):
        fut = self.pending_acts.get(msg.get("id"))
        if fut and not fut.done():
            fut.set_result(msg)

    async def run_goal(self, goal: str, *, check, op: dict | None = None, fixture_actions: list | None = None, pages: list[str] | None = None) -> bool:
        """Drive the UI until `check()` returns no problems. Hydrogen picks the actions; Decision 1
        looks at a screenshot after every action; a failed verify or a failed check is fed back
        to Hydrogen as text so it can recover."""
        cfg = config.job("navigator")
        th = config.job("step_verifier")["thresholds"]
        self.current_goal = {"goal": goal, "op": op}
        self.nav_stats["goals"] += 1
        short = goal.split(". ")[0][:90]
        self.store.pipeline["navigator"].update(status="running", detail=short)
        if not agents.fixture_mode():
            self.store.pipeline["step_verifier"].update(status="running", detail="Watching each action")
        await self.hub.broadcast({"type": "pipeline", "pipeline": self.store.pipeline_view()})
        if self.nav_target != "env":
            await self.dot("acting", goal.split(". ")[0][:110])
        history: list[str] = []
        state = await self.act("observe", {})
        try:
            if agents.fixture_mode():
                for name, args in fixture_actions or []:
                    await self.checkpoint()
                    res = await self.act(name, args)
                    self.nav_stats["steps"] += 1
                    if not res.get("ok"):
                        return False
                ok = check() == []
                self.nav_stats["goals_ok"] += ok
                return ok
            verifies: list[asyncio.Task] = []
            for turn in range(cfg["max_steps_per_goal"]):
                await self.checkpoint()
                r = await agents.navigator_step(goal, state.get("page", "?"), PAGES[self.role] if pages is None else pages, state.get("tree", []), state.get("screenshot"), history)
                if not r["calls"]:
                    history.append("You answered without a function call. Answer only with function calls.")
                    continue
                finished = False
                for c in r["calls"]:
                    if c["name"] == "done":
                        finished = True
                        break
                    if c["name"] == "fail":
                        history.append(f"You reported failure: {c['args'].get('reason')}")
                        finished = True
                        break
                    await self.checkpoint()
                    res = await self.act(c["name"], c["args"])
                    self.nav_stats["steps"] += 1
                    desc = f"{c['name']}({', '.join(f'{k}={v!r}' for k, v in c['args'].items())})"
                    state = res
                    if not res.get("ok"):
                        history.append(f"{desc} -> ERROR: {res.get('error')}")
                        break  # let the model see the error before doing anything else
                    history.append(f"{desc} -> ok" + (f"; application message: {res['message']}" if res.get("message") else ""))
                    if res.get("screenshot"):
                        verifies.append(asyncio.create_task(self._verify(desc, res, th, history)))
                if verifies:
                    await asyncio.gather(*verifies)
                    verifies = []
                if finished:
                    problems = check()
                    if not problems:
                        self.nav_stats["goals_ok"] += 1
                        return True
                    self.nav_stats["recoveries"] += 1
                    history.append("The casebook does not show the goal yet: " + "; ".join(problems) + ". Fix this, then call done.")
                    state = await self.act("observe", {})
            ok = check() == []
            self.nav_stats["goals_ok"] += ok
            return ok
        finally:
            self.current_goal = None
            n = self.nav_stats
            self.store.pipeline["navigator"].update(status="done", detail=f"{n['goals_ok']} of {n['goals']} goals done, {n['steps']} actions", runs=n["goals"])
            if n["verified_ok"] + n["verify_failed"]:
                self.store.pipeline["step_verifier"].update(status="done", detail=f"{n['verified_ok']} actions confirmed on screen, {n['verify_failed']} sent back")
            await self.hub.broadcast({"type": "pipeline", "pipeline": self.store.pipeline_view()})

    async def _verify(self, desc: str, res: dict, th: dict, history: list[str]):
        v = await agents.verify_step(desc, res.get("expected") or "The action is visible on screen and no error is shown.", res["screenshot"])
        a = v["answers"]
        ok = a["action_succeeded"]["p"] >= th["action_succeeded"] and a["screen_matches_expected_state"]["p"] >= th["screen_matches"]
        self.nav_stats["verified_ok" if ok else "verify_failed"] += 1
        self.store.ledger.append(actor_type="ai", actor_id="step-verifier", role=self.role, action="step_verified" if ok else "step_verify_failed",
                                 model=v["model"], prompt_hash=v["prompt_hash"], record=(self.current_goal or {}).get("op", {}).get("op_id") if self.current_goal and self.current_goal.get("op") else None,
                                 after={"action": desc, "p_action_succeeded": a["action_succeeded"]["p"], "p_screen_matches": a["screen_matches_expected_state"]["p"]})
        if not ok:
            self.nav_stats["recoveries"] += 1
            history.append(f"VERIFIER: the screenshot after {desc} does not show the expected result "
                           f"(P(succeeded)={a['action_succeeded']['p']:.2f}). Look at the screen again and correct it.")

    # ---------------------------------------------------------------- after a signature
    async def on_signed(self, m: dict):
        st = self.store
        if self.role == "sponsor":
            v2 = copy.deepcopy(st.draft)
            v2["version"] = 2
            st.casebooks[2] = v2
            st.signatures["sponsor"] = m
            st.signed_record = st.record_for_signature("sponsor")[2]
            self.fire("signed", by_human=True)
            line = "Signed. Casebook version 2 is now with the CRO for deployment."
        elif self.role == "cro":
            st.signatures["cro"] = m
            st.signed_rollout = st.record_for_signature("cro")[2]
            self.fire("signed", by_human=True)
            for sid in st.deploy["sites"]:
                st.site_flows[sid] = Flow("handoff_site")
            line = f"Signed. {len(st.deploy['sites'])} sites can now see their transition plans."
        else:
            st.signatures["site"][self.site_id] = m
            self.fire("signed", by_human=True)
            line = "Signed. Your site's transition plan is acknowledged."
        await self.proof(f"Signed by {m['printed_name']}: {m['meaning']}", kind="person", actor=self.user_id, action="signature_recorded", record=m["record"],
                         after={"signature_seq": m["seq"], "signed_record_hash": m["signed_record_hash"]},
                         check={"label": "User ID and password re-entered; bound to the record hash", "ok": True, "by": "signature service"})
        await self.send({"type": "gate", "kind": None})
        await self.changed()
        await self.dot("waiting", line)
        await self.say(line)

    # ---------------------------------------------------------------- CRO
    async def run_cro(self):
        st = self.store
        self.fire("open_deploy", by_human=True)
        await self.send({"type": "canvas", "show": True})
        await self.dot("thinking", "Checking the sponsor signature and the audit chain before deploying")
        sig = st.signer.verify_signature(st.signatures["sponsor"]["seq"], st.record_for_signature("sponsor")[2])
        await self.proof("Sponsor signature is intact and still bound to casebook version 2", kind="rule", actor=RULE_ENGINE, action="predeploy_check",
                         after={"signature_ok": sig["ok"], "reason": sig.get("reason")}, check={"label": "Chain verified, record hash matches" if sig["ok"] else sig.get("reason"), "ok": sig["ok"], "by": RULE_ENGINE})
        if not sig["ok"]:
            await self.dot("waiting", "I will not deploy: " + sig["reason"])
            return await self.say("I will not deploy. The signature check failed.")
        ok = await self.run_goal("Deploy casebook version 2 to production. Open the Deployment page and press the button that deploys version 2 to production.",
                                 check=lambda: [] if st.deploy["production"] else ["version 2 is not deployed to production"],
                                 fixture_actions=[("navigate", {"page": "deployment"}), ("click", {"agent_id": "deploy-production"})])
        if ok:
            await self.proof("Casebook version 2 deployed to production", kind="ai", actor="navigator", action="deployed_production",
                             model_out={"model": _nav_model(), "prompt_hash": None, "fixture": agents.fixture_mode()}, after=st.deploy["production"],
                             check={"label": "Production shows version 2", "ok": True, "by": RULE_ENGINE})
        elig = st.eligible_sites()
        ok = ok and await self.run_goal("Roll casebook version 2 out to every site that has ethics committee approval. Open the Site Rollout page, "
                                        "press “Select eligible sites”, then press “Deploy to selected sites”. Do not select sites that are waiting for approval.",
                                        check=lambda: [] if set(st.deploy["sites"]) == set(elig) else [f"{len(set(elig) - set(st.deploy['sites']))} eligible sites are not deployed yet"]
                                        + ([f"sites without approval were deployed: {sorted(set(st.deploy['sites']) - set(elig))}"] if set(st.deploy["sites"]) - set(elig) else []),
                                        fixture_actions=[("navigate", {"page": "rollout"}), ("click", {"agent_id": "rollout-select-eligible"}), ("click", {"agent_id": "rollout-deploy"})])
        if not ok:
            self.fire("deploy_failed")
            await self.changed()
            await self.dot("waiting", "I could not finish the deployment, so I stopped.")
            return await self.say("I could not finish the deployment, so I stopped.")
        wait = len(st.sites) - len(elig)
        await self.proof(f"Rolled out to {len(elig)} of {len(st.sites)} sites; {wait} wait for ethics approval", kind="ai", actor="navigator", action="rollout",
                         model_out={"model": _nav_model(), "prompt_hash": None, "fixture": agents.fixture_mode()}, after=sorted(st.deploy["sites"]),
                         check={"label": "Only sites with ethics approval were deployed", "ok": True, "by": RULE_ENGINE})
        self.fire("deploy_done")
        await self.changed()
        self.focus = {"why": f"{wait} sites have not recorded ethics committee approval for protocol version 1.1, so they stay on version 1 until they do.", "page": None}
        line = f"Version 2 is live at {len(elig)} of {len(st.sites)} sites. {wait} are waiting for ethics approval. Sign to confirm the rollout."
        await self.dot("waiting", line)
        await self.send({"type": "gate", "kind": "sign"})
        await self.say(line)

    # ---------------------------------------------------------------- Site
    async def run_site(self):
        st, sid = self.store, self.site_id
        self.fire("open_transition", by_human=True)
        await self.send({"type": "canvas", "show": True})
        ok = await self.run_goal("Show the transition plan for this site. Open the Transition Plan page and press “Calculate plan”.",
                                 check=lambda: [] if sid in st.plans else ["the transition plan has not been calculated"],
                                 fixture_actions=[("navigate", {"page": "transition"}), ("click", {"agent_id": "transition-calculate"})])
        if not ok:
            self.fire("transition_failed")
            await self.dot("waiting", "I could not open the transition plan.")
            return
        plan = st.plans[sid]
        await self.proof(f"Transition plan: {plan['affected']} participant(s), {plan['upcoming_visits']} new visits to schedule", kind="rule", actor=RULE_ENGINE,
                         action="transition_plan", record=f"TP-{sid}", after={"affected": plan["affected"], "upcoming_visits": plan["upcoming_visits"]},
                         check={"label": "Calculated from randomisation dates by a fixed rule", "ok": True, "by": RULE_ENGINE}, detail=plan["rule"])
        self.fire("transition_done")
        await self.changed()
        self.focus = {"why": plan["rule"] + " Visits whose window closed before the amendment took effect are not created and are not deviations.", "page": 51,
                      "quote": "Sampling exceeding the specified time range mentioned above will be treated as a protocol deviation."}
        line = (f"{plan['affected']} participant{'s have' if plan['affected'] != 1 else ' has'} {plan['upcoming_visits']} new visits to schedule. Re-consent comes first. Sign to acknowledge."
                if plan["affected"] else "No enrolled participant at your site gets new visits. Sign to acknowledge.")
        await self.dot("waiting", line)
        await self.send({"type": "gate", "kind": "sign"})
        await self.say(line)


# -------------------------------------------------------------------- helpers
def _norm_id(s: str) -> str:
    return "".join(ch for ch in s.upper() if ch.isalnum())


def _nav_model() -> str:
    return fixtures.FIXTURE_MODEL if agents.fixture_mode() else config.job("navigator")["model"]


def _none(v):
    return "none (leave the field empty)" if v in (None, "") else str(v)


def op_title(op: dict) -> str:
    k, t = op["kind"], op["target_id"]
    if k == "add_event":
        return f"Event {t} added on Day {op.get('day')}" + (f", window ±{op['window_after']}" if op.get("window_after") is not None and op.get("window_before") == op.get("window_after") else "")
    if k == "add_event_group":
        return f"Event group {t} added"
    if k == "set_event_window":
        return f"Window of {t} set to -{op.get('window_before') or 0}/+{op.get('window_after') or 0} days"
    if k == "assign_form":
        return f"Form {t} assigned to {', '.join(op['event_ids'])}"
    if k == "add_codelist_entries":
        return f"Codelist {t}: added {', '.join(op['entries'])}"
    if k == "add_rule":
        return f"Rule {t} written ({op['rule']['type'].replace('_', ' ')})"
    if k == "modify_rule":
        return f"Rule {t} modified"
    if k == "update_review_plan":
        return f"Review plan {t}: {op.get('form_id')} at {len(op.get('event_ids') or [])} more events"
    if k == "add_form":
        return f"Form {t} added"
    if k == "add_item":
        return f"Item {op['item']['id']} added to form {t}"
    return f"{k} {t}"


def goal_for_op(op: dict) -> str:
    """Plain-language goal for the navigator. Built by a fixed template from the typed operation."""
    k, t = op["kind"], op["target_id"]
    if k == "add_event_group":
        return f"Add event group {t}. On the Events page create a new event group with ID {t}, label “{op.get('label') or t}”, repeating: {'yes' if op.get('repeating') else 'no'}. Save it."
    if k == "add_event":
        return (f"Add event {t}. On the Events page create a new event with ID {t}, label “{op.get('label') or t}”, event group {op.get('group')}, "
                f"study day {_none(op.get('day'))}, window before {_none(op.get('window_before'))}, window after {_none(op.get('window_after'))}, "
                f"dynamic (created by a rule): {'yes' if op.get('dynamic') else 'no'}. Save it.")
    if k == "set_event_window":
        return f"Set the visit window of event {t}. On the Events page edit event {t}: window before {_none(op.get('window_before'))}, window after {_none(op.get('window_after'))}. Save it."
    if k == "add_form":
        return f"Add form {t}. On the Forms page create a new form with ID {t} and label “{op.get('label') or t}”. Save it."
    if k == "add_item":
        i = op["item"]
        return (f"Add item {i['id']} to form {t}. On the Forms page open form {t} and add an item with ID {i['id']}, label “{i.get('label') or i['id']}”, "
                f"type {i.get('type') or 'text'}, codelist {_none(i.get('codelist'))}. Save it.")
    if k == "assign_form":
        return f"Assign form {t} to events {', '.join(op['event_ids'])}. On the Schedule page tick the cell in row {t} for each of these events: {', '.join(op['event_ids'])}. Tick nothing else."
    if k == "add_codelist_entries":
        return f"Add entries to codelist {t}. On the Codelists page open codelist {t} and add these entries one at a time, exactly as written: " + "; ".join(f"“{e}”" for e in op["entries"]) + "."
    if k in ("add_rule", "modify_rule"):
        r = op["rule"]
        then = r.get("then") or {}
        parts = [f"{'Create' if k == 'add_rule' else 'Edit'} rule {t} on the Rules page."]
        if k == "add_rule":
            parts.append(f"Rule ID {t}, type {r['type']}, description “{r.get('description', '')}”.")
        elif r.get("description"):
            parts.append(f"Set the description to exactly: {r['description']}")
        if r.get("when"):
            parts.append(f"Condition (type it exactly): {r['when']}")
        if then.get("events"):
            parts.append(f"Then add events: {', '.join(then['events'])}")
        if then.get("message"):
            parts.append(f"Then open query with the text: {then['message']}")
        if (r.get("scope") or {}).get("events"):
            parts.append(f"Scope form {r['scope'].get('form')}, scope events: {', '.join(r['scope']['events'])}")
        return " ".join(parts) + " Save it."
    if k == "update_review_plan":
        return f"Extend review plan {t}. On the Review Plans page tick the cell for form {op['form_id']} at each of these events: {', '.join(op['event_ids'])}. Tick nothing else."
    return f"{k} {t}"


def fixture_actions(op: dict, draft: dict) -> list[tuple[str, dict]]:
    """TEST DOUBLE for the navigator (fixture mode only): the UI actions for one operation,
    written by hand. In live mode Hydrogen chooses the actions from the screen."""
    k, t = op["kind"], op["target_id"]
    s = lambda v: "" if v is None else str(v)
    T = lambda i, v: ("type", {"agent_id": i, "text": s(v)})
    C = lambda i: ("click", {"agent_id": i})
    if k == "add_event_group":
        return [("navigate", {"page": "events"}), C("groups-new"), T("group-id", t), T("group-label", op.get("label") or t)] + ([C("group-repeating")] if op.get("repeating") else []) + [C("group-save")]
    if k == "add_event":
        return [("navigate", {"page": "events"}), C("events-new"), T("event-id", t), T("event-label", op.get("label") or t), ("select", {"agent_id": "event-group", "value": op["group"]}),
                T("event-day", op.get("day")), T("event-window-before", op.get("window_before")), T("event-window-after", op.get("window_after"))] + ([C("event-dynamic")] if op.get("dynamic") else []) + [C("event-save")]
    if k == "set_event_window":
        return [("navigate", {"page": "events"}), C(f"event-edit-{t}"), T("event-window-before", op.get("window_before")), T("event-window-after", op.get("window_after")), C("event-save")]
    if k == "add_form":
        return [("navigate", {"page": "forms"}), C("forms-new"), T("form-id", t), T("form-label", op.get("label") or t), C("form-save")]
    if k == "add_item":
        i = op["item"]
        return [("navigate", {"page": "forms"}), C(f"form-open-{t}"), C("item-new"), T("item-id", i["id"]), T("item-label", i.get("label") or i["id"]),
                ("select", {"agent_id": "item-type", "value": i.get("type") or "text"})] + ([("select", {"agent_id": "item-codelist", "value": i["codelist"]})] if i.get("codelist") else []) + [C("item-save")]
    if k == "assign_form":
        return [("navigate", {"page": "schedule"})] + [C(f"sched-{t}-{e}") for e in op["event_ids"] if e not in draft["schedule"].get(t, [])]
    if k == "add_codelist_entries":
        out = [("navigate", {"page": "codelists"}), C(f"codelist-open-{t}")]
        for e in op["entries"]:
            out += [T("codelist-entry", e), C("codelist-add")]
        return out
    if k in ("add_rule", "modify_rule"):
        r = op["rule"]
        then, scope = r.get("then") or {}, r.get("scope") or {}
        out = [("navigate", {"page": "rules"}), C("rules-new" if k == "add_rule" else f"rule-edit-{t}")]
        if k == "add_rule":
            out += [T("rule-id", t), ("select", {"agent_id": "rule-type", "value": r["type"]}), T("rule-description", r.get("description", ""))]
        elif r.get("description"):
            out.append(T("rule-description", r["description"]))
        if r.get("when"):
            out.append(T("rule-when", r["when"]))
        if then.get("events"):
            out.append(T("rule-then-events", ", ".join(then["events"])))
        if then.get("message"):
            out.append(T("rule-then-message", then["message"]))
        if scope.get("events"):
            out += [("select", {"agent_id": "rule-scope-form", "value": scope.get("form", "")}), T("rule-scope-events", ", ".join(scope["events"]))]
        return out + [C("rule-save")]
    if k == "update_review_plan":
        cur = next((p for p in draft["review_plans"] if p["id"] == t), {"coverage": {}})["coverage"].get(op["form_id"], [])
        return [("navigate", {"page": "review"})] + [C(f"review-{t}-{op['form_id']}-{e}") for e in op["event_ids"] if e not in cur]
    return []
