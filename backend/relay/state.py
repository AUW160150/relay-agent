"""Explicit state machine for one amendment. Models act inside a state; only this
module decides which state may follow and which role may cause it."""
from __future__ import annotations

STATES = [
    "intake", "diff", "plan", "audit", "gate_option", "build", "verify", "gate_approve", "sign",
    "handoff_cro", "deploy", "rollout", "handoff_site", "transition", "site_sign", "done",
]
OWNER = {
    **{s: "sponsor" for s in ["intake", "diff", "plan", "audit", "gate_option", "build", "verify", "gate_approve", "sign"]},
    **{s: "cro" for s in ["handoff_cro", "deploy", "rollout"]},
    **{s: "site" for s in ["handoff_site", "transition", "site_sign"]},
    "done": None,
}
# Gates are the only states where the machine waits for a person.
GATES = {"intake", "gate_option", "gate_approve", "sign", "handoff_cro", "rollout", "handoff_site", "site_sign"}

# (state, event) -> next state
TRANSITIONS: dict[tuple[str, str], str] = {
    ("intake", "open_amendment"): "diff",
    ("diff", "diff_done"): "plan",
    ("plan", "plan_done"): "audit",
    ("audit", "audit_done"): "gate_option",
    ("gate_option", "choose_option"): "build",
    ("build", "build_done"): "verify",
    ("build", "build_aborted"): "gate_option",
    ("verify", "verify_passed"): "gate_approve",
    ("verify", "verify_failed"): "gate_option",
    ("gate_approve", "approve"): "sign",
    ("gate_approve", "reject"): "gate_option",
    ("gate_approve", "choose_option"): "build",
    ("sign", "signed"): "handoff_cro",
    ("sign", "undo"): "gate_approve",
    ("handoff_cro", "open_deploy"): "deploy",
    ("deploy", "deploy_done"): "rollout",
    ("deploy", "deploy_failed"): "handoff_cro",
    ("rollout", "signed"): "handoff_site",
    ("handoff_site", "open_transition"): "transition",
    ("transition", "transition_done"): "site_sign",
    ("transition", "transition_failed"): "handoff_site",
    ("site_sign", "signed"): "done",
}
# Events that only a person may raise (never a model on its own).
HUMAN_EVENTS = {"open_amendment", "choose_option", "approve", "reject", "signed", "undo", "open_deploy", "open_transition"}


class TransitionError(Exception):
    pass


class Flow:
    def __init__(self, state: str = "intake"):
        self.state = state
        self.history: list[dict] = []

    def allowed(self, role: str | None = None) -> list[str]:
        if role is not None and OWNER[self.state] != role:
            return []
        return [ev for (st, ev) in TRANSITIONS if st == self.state]

    def can(self, event: str, role: str) -> bool:
        return event in self.allowed(role)

    def fire(self, event: str, role: str, by_human: bool = False) -> str:
        if (self.state, event) not in TRANSITIONS:
            raise TransitionError(f"'{event}' is not allowed in state '{self.state}'")
        if OWNER[self.state] != role:
            raise TransitionError(f"State '{self.state}' belongs to the {OWNER[self.state]} role, not {role}")
        if event in HUMAN_EVENTS and not by_human:
            raise TransitionError(f"'{event}' needs a decision from a person")
        prev, self.state = self.state, TRANSITIONS[(self.state, event)]
        self.history.append({"from": prev, "event": event, "to": self.state, "role": role})
        return self.state

    @property
    def owner(self) -> str | None:
        return OWNER[self.state]

    @property
    def waiting_for_human(self) -> bool:
        return self.state in GATES
