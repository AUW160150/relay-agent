"""End-to-end orchestrator test in fixture mode (hand-written model doubles, no network).

A fake UI stands in for the browser: it keeps dialog fields, and on a save click it calls
the same endpoint functions the real frontend calls. This tests the state machine, the
ledger, signatures and role handoffs together; the real UI is tested with Playwright.
"""
import asyncio

import pytest
from fastapi import HTTPException

from relay import app as appmod
from relay import casebook, config, seed
from relay.signatures import MEANINGS


class FakeUI:
    def __init__(self, hub, token):
        self.hub, self.token, self.agent = hub, token, hub.agent(token)
        self.fields, self.page, self.msgs, self.said, self.selected = {}, None, [], [], []
        self.auth = f"Bearer {token}"
        self.delay = 0
        self.agent.send = self.on_msg

    async def on_msg(self, m):
        self.msgs.append(m)
        if m["type"] == "say":
            self.said.append(m["text"])
        if m["type"] == "act":
            asyncio.get_event_loop().create_task(self.perform(m))

    async def perform(self, m):
        a, res = m["action"], {"ok": True}
        await asyncio.sleep(self.delay)
        try:
            if a.get("target") == "env":      # the embedded environment tour: nothing to change in the casebook
                self.env_actions = getattr(self, "env_actions", []) + [a]
            else:
                await self.do(a["name"], a["args"])
        except HTTPException as e:
            res = {"ok": False, "error": e.detail}
        self.agent.on_act_result({"id": m["id"], **res, "tree": [], "page": self.page, "screenshot": None})

    async def op(self, **op):
        await appmod.draft_op(op, authorization=self.auth)

    async def do(self, name, args):
        f = self.fields
        if name == "navigate":
            self.page = args["page"]
        elif name in ("type", "select"):
            f[args["agent_id"]] = args.get("text", args.get("value"))
        elif name == "click":
            i = args["agent_id"]
            num = lambda k: int(f[k]) if f.get(k) not in (None, "") else None
            if i in ("events-new", "rules-new", "groups-new") or i.startswith(("event-edit-", "rule-edit-")):
                f.clear()
                f["_edit"] = i.split("-edit-")[1] if "-edit-" in i else None
            elif i in ("event-dynamic", "group-repeating"):
                f[i] = True
            elif i == "event-save" and f.get("_edit"):
                await self.op(kind="set_event_window", target_id=f["_edit"], window_before=num("event-window-before"), window_after=num("event-window-after"))
            elif i == "event-save":
                await self.op(kind="add_event", target_id=f["event-id"], label=f["event-label"], group=f["event-group"], day=num("event-day"),
                              window_before=num("event-window-before"), window_after=num("event-window-after"), dynamic=bool(f.get("event-dynamic")))
            elif i == "group-save":
                await self.op(kind="add_event_group", target_id=f["group-id"], label=f["group-label"], repeating=bool(f.get("group-repeating")))
            elif i == "rule-save":
                csv = lambda k: [x.strip() for x in f.get(k, "").split(",") if x.strip()]
                rule = {"type": f.get("rule-type"), "description": f.get("rule-description", ""), "when": f.get("rule-when", "")}
                if csv("rule-then-events"):
                    rule["then"] = {"action": "add_events", "events": csv("rule-then-events")}
                else:
                    rule["then"] = {"action": "open_query", "message": f.get("rule-then-message", "")}
                    rule["scope"] = {"form": f.get("rule-scope-form", ""), "events": csv("rule-scope-events")}
                await self.op(kind="modify_rule" if f.get("_edit") else "add_rule", target_id=f.get("_edit") or f["rule-id"], rule=rule)
            elif i.startswith("sched-"):
                _, form, ev = i.split("-", 2)
                await self.op(kind="assign_form", target_id=form, event_ids=[ev])
            elif i.startswith("review-"):
                _, plan, form, ev = i.split("-", 3)
                await self.op(kind="update_review_plan", target_id=plan, form_id=form, event_ids=[ev])
            elif i.startswith("codelist-open-"):
                f["_codelist"] = i.removeprefix("codelist-open-")
            elif i == "codelist-add":
                await self.op(kind="add_codelist_entries", target_id=f["_codelist"], entries=[f["codelist-entry"]])
            elif i == "deploy-production":
                await appmod.deploy_production(authorization=self.auth)
            elif i == "rollout-select-eligible":
                self.selected = self.hub.store.eligible_sites()
            elif i == "rollout-deploy":
                await appmod.deploy_sites(appmod.SiteIds(site_ids=self.selected), authorization=self.auth)
            elif i == "transition-calculate":
                await appmod.transition_calculate(authorization=self.auth)

    async def until(self, pred, timeout=10):
        for _ in range(int(timeout / 0.01)):
            if pred():
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed out in state {self.agent.flow.state}; said={self.said[-3:]}")

    def gate(self, kind):
        return lambda: any(m["type"] == "gate" and m.get("kind") == kind for m in self.msgs[-6:])


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_MODE", "fixture")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    h = appmod.Hub()
    h.store.data_dir = tmp_path
    monkeypatch.setattr(appmod, "hub", h)
    return h


def login(hub, user):
    r = appmod.login(appmod.Login(user_id=user, password=seed.DEMO_PASSWORD))
    return FakeUI(hub, r["token"])


SPONSOR, CRO = "elena.marsh@sponsor.example", "jordan.morgan@cro.example"


async def sign(ui, role, password=seed.DEMO_PASSWORD):
    return await appmod.sign(appmod.Sign(user_id=ui.agent.user_id, password=password, meaning=MEANINGS[role]), authorization=ui.auth)


async def sponsor_to_approval(hub, option=None):
    ui = login(hub, SPONSOR)
    await ui.agent.greet()
    assert "One amendment is waiting" in ui.said[-1]
    await ui.agent.on_utterance("open the new amendment", typed=True)
    await ui.until(ui.gate("option"))
    assert hub.store.flow.state == "gate_option"
    # while the protocol was read, the agent did one small task in the RELAY environment
    assert [x["args"]["label"] for x in ui.env_actions if x["name"] == "click"] == ["1 Sponsor", "Schedule"]
    assert any(m["type"] == "env" and m["show"] for m in ui.msgs)
    await ui.agent.on_utterance(f"go with option {option}" if option else "go ahead", typed=True)
    return ui


async def test_full_flow_three_roles(hub):
    st = hub.store
    ui = await sponsor_to_approval(hub)
    # Carbon flagged an untraceable item: the agent asks and waits
    await ui.until(lambda: ui.agent.question is not None)
    assert "could not trace" in ui.agent.question["text"] and st.flow.state == "build"
    await ui.agent.on_utterance("why?", typed=True)
    assert "not stated" in ui.said[-1].lower()
    await ui.agent.handle_intent("reject")  # drop the untraceable SDV op
    await ui.until(lambda: ui.agent.question is not None and "OP-05" in str(ui.agent.question["finding"]))
    await ui.agent.handle_intent("approve")  # keep the uncertain V7.2 window as planned
    await ui.until(ui.gate("approve"), timeout=20)
    assert st.flow.state == "gate_approve"
    assert st.amendment["build_check"]["ok"] and st.amendment["tests"]["passed"] == st.amendment["tests"]["total"] == 6
    assert "SDV100" not in [o["target_id"] for o in st.build()["ops"]]
    assert st.draft["review_plans"][0]["coverage"]["PK"] == st.v1["review_plans"][0]["coverage"]["PK"]
    assert [e["id"] for e in st.draft["events"]].count("V7.2") == 1

    # a spoken yes approves but does not sign
    await ui.agent.on_utterance("approve", typed=True)
    assert st.flow.state == "sign" and st.signatures["sponsor"] is None
    assert any(m["type"] == "open_sign" and m["meaning"] == "Approved as Sponsor Data Manager" for m in ui.msgs)
    with pytest.raises(HTTPException) as e:
        await sign(ui, "sponsor", password="wrong")
    assert e.value.status_code == 401 and st.flow.state == "sign"
    m = await sign(ui, "sponsor")
    assert m["printed_name"] == "Elena Marsh" and st.flow.state == "handoff_cro" and st.casebooks[2]["version"] == 2

    # casebook is locked after signing
    with pytest.raises(HTTPException) as e:
        await ui.op(kind="add_codelist_entries", target_id="PKTPT", entries=["999 h"])
    assert e.value.status_code == 409

    # CRO
    cro = login(hub, CRO)
    await cro.agent.greet()
    assert "ready to deploy" in cro.said[-1] and "Elena Marsh signed casebook version 2" in cro.said[-1]  # caught up on the sponsor's work
    await cro.agent.on_utterance("do it", typed=True)
    await cro.until(cro.gate("sign"))
    elig = st.eligible_sites()
    assert st.flow.state == "rollout" and len(elig) == 24 and set(st.deploy["sites"]) == set(elig)
    await sign(cro, "cro")
    assert st.flow.state == "handoff_site" and st.signatures["cro"]["meaning"] == MEANINGS["cro"]

    # Site
    site_id = next(s["id"] for s in st.sites if s["id"] in elig and seed.transition_plan(s, st.v1, st.casebooks[2])["affected"])
    su = login(hub, st.site(site_id)["coordinator_user"])
    await su.agent.greet()
    assert "live at your site" in su.said[-1] and "Jordan Morgan signed the rollout" in su.said[-1] and su.said[-1].startswith("Hello ")
    b = su.agent.briefing()
    assert [r["state"] for r in b["roles"]] == ["done", "done", "active"] and b["task"] == "Open the transition plan"
    await su.agent.on_utterance("open the transition plan", typed=True)
    await su.until(su.gate("sign"))
    assert st.plans[site_id]["affected"] >= 1
    await sign(su, "site")
    assert st.site_flows[site_id].state == "done"

    # a site without approval has nothing waiting
    late = login(hub, st.site("CN-103")["coordinator_user"])
    await late.agent.greet()
    assert "has not been deployed" in late.said[-1]

    # ledger: chain intact, all three signatures bound, actor types present
    v = appmod.ledger_verify(authorization=ui.auth)
    assert v["ok"] and len(v["signatures"]) == 3 and all(s["ok"] for s in v["signatures"])
    kinds = {e["actor_type"] for e in st.ledger.entries}
    assert kinds == {"person", "ai", "rule"}
    assert all(e["model"] for e in st.ledger.entries if e["actor_type"] == "ai")
    built = [e for e in st.ledger.entries if e["action"] == "add_event"]
    assert len(built) == 7 and all(e["actor_id"] == "navigator" and e["source"]["page"] == 51 for e in built)
    # tampering with the signed casebook is caught
    st.casebooks[2]["events"][0]["label"] = "tampered"
    st.draft["events"][0]["label"] = "tampered"
    assert not appmod.ledger_verify(authorization=ui.auth)["signatures"][0]["ok"]


async def test_option_b_then_back_to_a(hub):
    st = hub.store
    ui = await sponsor_to_approval(hub, "B")
    await ui.until(lambda: ui.agent.question is not None)  # B's audit finding: missing 48 h check
    await ui.agent.handle_intent("approve")
    await ui.until(ui.gate("approve"), timeout=20)
    assert st.amendment["chosen"] == "B" and any(g["id"] == "PKU" for g in st.draft["event_groups"])
    await ui.agent.on_utterance("try option A instead", typed=True)
    await ui.until(lambda: ui.agent.question is not None and st.amendment["chosen"] == "A")
    assert not any(g["id"] == "PKU" for g in st.draft["event_groups"])  # draft was reset to v1 first
    await ui.agent.handle_intent("approve")
    await ui.until(lambda: ui.agent.question is not None and "OP-05" in str(ui.agent.question["finding"]))
    await ui.agent.handle_intent("approve")
    await ui.until(ui.gate("approve"), timeout=20)
    assert casebook.content_hash(st.draft) == st.amendment["build_check"]["expected_hash"]
    assert len(st.draft["review_plans"][0]["coverage"]["PK"]) == 15  # kept this time


async def test_stop_pauses_and_resume_continues(hub):
    st = hub.store
    ui = await sponsor_to_approval(hub)
    ui.delay = 0.02  # a UI that takes time, like the real one
    await ui.until(lambda: ui.agent.question is not None)
    await ui.agent.handle_intent("approve")
    await ui.until(lambda: ui.agent.question is not None and "OP-05" in str(ui.agent.question["finding"]))
    await ui.agent.handle_intent("approve")
    await ui.until(lambda: len(st.draft["events"]) > len(st.v1["events"]))
    await ui.agent.on_utterance("stop", typed=True)
    await asyncio.sleep(0.15)
    n = len(st.ledger.entries)
    await asyncio.sleep(0.2)
    assert len(st.ledger.entries) == n and st.flow.state == "build"  # nothing happens while paused
    await ui.agent.on_utterance("continue", typed=True)
    await ui.until(ui.gate("approve"), timeout=20)


async def test_roles_cannot_act_out_of_turn(hub):
    st = hub.store
    cro = login(hub, CRO)
    await cro.agent.greet()
    assert "has not signed" in cro.said[-1]
    await cro.agent.on_utterance("deploy it", typed=True)
    assert st.flow.state == "intake" and "Nothing is waiting" in cro.said[-1]
    with pytest.raises(HTTPException):
        await sign(cro, "cro")
    with pytest.raises(HTTPException):
        await appmod.deploy_production(authorization=cro.auth)
