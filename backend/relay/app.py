"""FastAPI server: REST for state and decisions, one WebSocket per signed-in session."""
from __future__ import annotations

import asyncio
import copy
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, casebook, config, llm, pdfutil, rules, seed
from .orchestrator import PAGES, Agent, Store
from .signatures import MEANINGS, SignatureError, check_password, manifestation

app = FastAPI(title="RELAY Agent")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])


class Hub:
    def __init__(self):
        self.store = Store()
        llm.on_call = lambda out: self.store.on_model_call(out)
        self.tokens: dict[str, str] = {}      # token -> user id
        self.agents: dict[str, Agent] = {}    # token -> agent
        self.sockets: dict[str, WebSocket] = {}

    async def broadcast(self, msg: dict):
        for ws in list(self.sockets.values()):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    def agent(self, token: str | None) -> Agent:
        if not token or token not in self.tokens:
            raise HTTPException(401, "Sign in first")
        if token not in self.agents:
            async def send(msg, token=token):
                ws = self.sockets.get(token)
                if ws:
                    try:
                        await ws.send_json(msg)
                    except Exception:
                        pass
            self.agents[token] = Agent(self.store, self, self.tokens[token], send)
        return self.agents[token]


hub = Hub()


def auth(authorization: str | None) -> Agent:
    return hub.agent((authorization or "").removeprefix("Bearer ").strip() or None)


# ------------------------------------------------------------------ session
class Login(BaseModel):
    user_id: str
    password: str


@app.get("/api/accounts")
def accounts():
    st = hub.store
    return {"mode": config.mode(), "has_key": bool(config.api_key()) or llm.recorder.replaying, "replaying": llm.recorder.session if llm.recorder.replaying else None,
            "demo_password": seed.DEMO_PASSWORD,
            "study": st.study, "accounts": [{"user_id": k, "name": u["name"], "title": u["title"], "role": u["role"], "org": u["org"],
                                              "site_id": u.get("site_id")} for k, u in st.users.items()]}


@app.post("/api/login")
def login(body: Login):
    u = hub.store.users.get(body.user_id)
    # Demo sign-in: any non-empty password opens a demo account. Signing a record is different:
    # /api/sign always checks the real password (the Part 11 re-authentication).
    if not u or not body.password:
        hub.store.ledger.append(actor_type="person", actor_id=body.user_id, role="unknown", action="login_failed")
        raise HTTPException(401, "User ID or password is not correct.")
    token = secrets.token_urlsafe(24)
    hub.tokens[token] = body.user_id
    hub.store.ledger.append(actor_type="person", actor_id=body.user_id, role=u["role"], action="login")
    return {"token": token, "user": {"user_id": body.user_id, **{k: u[k] for k in ("name", "title", "role", "org")}, "site_id": u.get("site_id")}}


@app.post("/api/reset")
async def reset():
    for a in hub.agents.values():
        if a.task and not a.task.done():
            a.task.cancel()
    hub.store.reset()
    hub.agents.clear()
    await hub.broadcast({"type": "reset"})
    return {"ok": True}


# ------------------------------------------------------------------ snapshot
def ai_record(st: Store) -> list[dict]:
    """The five answers for each change in the chosen build."""
    b = st.build()
    if not b:
        return []
    tests = st.amendment["tests"] or b.get("tests_planned") or {"results": []}
    findings = st.amendment["audits"].get(st.amendment["chosen"], {}).get("findings", [])
    sig = st.signatures["sponsor"]
    out = []
    for op in b["ops"]:
        ids = {op["target_id"]} | set((op.get("rule") or {}).get("then", {}).get("events", []) if op["kind"] == "add_rule" else [])
        ids |= {f"WINDOW:{op['target_id']}"} if op["kind"] in ("add_event", "set_event_window") else set()
        rel = [r for r in tests["results"] if ids & set(r["exercises"]) or any(t["rule"] in ids for t in r["trace"])
               or (op["kind"] == "add_event" and any(op["target_id"] in c["expected"] for c in r["checks"]))]
        built = next((p for p in st.proofs if p["title"] and p.get("check") and p["kind"] == "ai" and p["hash"] and p.get("state") == "build"
                      and p["page"] == op.get("source_page") and p["title"] == _title(op)), None)
        out.append({
            "op_id": op["op_id"], "title": _title(op), "kind": op["kind"], "basis": op.get("basis"),
            "source": {"page": op.get("source_page"), "quote": op.get("source_quote"), "citation": op.get("citation"), "changes": op.get("source_change_ids")},
            "what": op.get("rationale"),
            "tested": {"subjects": [{"id": r["subject"], "description": r["description"], "pass": r["pass"]} for r in rel],
                       "ran_on_built_casebook": st.amendment["tests"] is not None, "built_ok": (built or {}).get("check", {}).get("ok")},
            "uncertain": {"planner": op.get("uncertainty") or None,
                          "auditor": [{"id": f["id"], "kind": f["kind"], "detail": f["detail"], "p_needs_human": f.get("p_needs_human"),
                                       "decision": st.amendment["decisions"].get(st.amendment["chosen"], {}).get(f["id"])} for f in findings if f.get("op_id") == op["op_id"]]},
            "reviewed": {"by": sig["printed_name"], "at": sig["signed_at"], "meaning": sig["meaning"]} if sig else None,
            "ledger": (built or {}).get("hash"),
        })
    return out


def _title(op):
    from .orchestrator import op_title
    return op_title(op)


@app.get("/api/snapshot")
def snapshot(authorization: str | None = Header(None)):
    ag = auth(authorization)
    st, a = hub.store, hub.store.amendment
    current = st.casebooks.get(2) if 2 in st.casebooks else st.draft
    f = ag.flow
    out = {
        "mode": config.mode(), "fixture": agents.fixture_mode(), "replaying": llm.recorder.session if llm.recorder.replaying else None, "today": seed.TODAY.isoformat(),
        "user": {"user_id": ag.user_id, **{k: ag.user[k] for k in ("name", "title", "role", "org")}, "site_id": ag.site_id},
        "study": st.study, "registry_fetched": st.registry_fetched,
        "flow": {"state": f.state, "owner": f.owner, "allowed": f.allowed(ag.role), "global": st.flow.state, "history": st.flow.history[-30:]},
        "pages": PAGES[ag.role], "meaning": MEANINGS[ag.role],
        "v1": st.v1, "draft": st.draft, "casebook": current, "has_v2": 2 in st.casebooks,
        "compare": casebook.compare(st.v1, current),
        "amendment": {**{k: a[k] for k in ("id", "title", "received", "changes", "summary", "options", "chosen", "tests", "build_check", "decisions")},
                      "protocol": a.get("protocol"), "pdf_pages": pdfutil.page_count(st.pdf()),
                      "builds": {k: {kk: b[kk] for kk in ("ops", "subjects", "valid", "tests_planned", "repaired", "model", "fixture")} for k, b in a["builds"].items()},
                      "audits": a["audits"]},
        "proofs": st.proofs, "signatures": st.signatures, "deploy": st.deploy,
        "sites": [{**{k: s[k] for k in ("id", "name", "city", "country", "pi", "coordinator", "ec_submitted", "ec_approved")},
                   "subjects": len(s["subjects"]), "deployed": st.deploy["sites"].get(s["id"]), "signed_off": st.signatures["site"].get(s["id"])} for s in st.sites],
        "env_available": (config.ROOT / "reference" / "index.html").exists(), "briefing": ag.briefing(), "pipeline": st.pipeline_view(), "ledger": {"entries": len(st.ledger.entries), "head": st.ledger.head}, "record": ai_record(st), "dot": {"state": ag.dot_state[0], "caption": ag.dot_state[1]},
        "question": ({"text": ag.question["text"], "finding": ag.question["finding"], "yes": "Keep it", "no": "Drop it" if ag.question["can_drop"] else "Stop"} if ag.question else None),
        "costs": {"total_usd": round(sum(c["cost_usd"] for c in st.costs), 4), "calls": len(st.costs)},
        "nav_stats": ag.nav_stats,
    }
    if ag.role == "site":
        site = st.site(ag.site_id)
        out["site"] = {**site, "deployed": st.deploy["sites"].get(site["id"])}
        out["plan"] = st.plans.get(ag.site_id)
    return out


@app.get("/api/ledger")
def ledger(authorization: str | None = Header(None)):
    auth(authorization)
    return {"entries": hub.store.ledger.entries, "head": hub.store.ledger.head}


@app.get("/api/ledger/verify")
def ledger_verify(authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    res = st.ledger.verify()
    sigs = []
    if st.signatures["sponsor"]:
        sigs.append({"role": "sponsor", **st.signer.verify_signature(st.signatures["sponsor"]["seq"], st.record_for_signature("sponsor")[2])})
    if st.signatures["cro"]:
        sigs.append({"role": "cro", **st.signer.verify_signature(st.signatures["cro"]["seq"], st.record_for_signature("cro")[2])})
    for sid, m in st.signatures["site"].items():
        sigs.append({"role": f"site {sid}", **st.signer.verify_signature(m["seq"], st.record_for_signature("site", sid)[2])})
    st.ledger.append(actor_type="person", actor_id=ag.user_id, role=ag.role, action="chain_verified", after={"ok": res["ok"], "checked": res["checked"]})
    return {**res, "signatures": sigs}


@app.get("/api/protocol.pdf")
def protocol_pdf():
    return FileResponse(hub.store.pdf_path, media_type="application/pdf")


@app.get("/api/protocol/find")
def protocol_find(quote: str, page: int | None = None):
    return pdfutil.locate(hub.store.pdf(), quote, page)


@app.post("/api/amendment/upload")
async def upload(file: UploadFile = File(...), authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    if ag.role != "sponsor" or st.flow.state != "intake":
        raise HTTPException(409, "A protocol can only be dropped by the sponsor before the amendment is opened.")
    data = await file.read()
    if not data.startswith(b"%PDF") or len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "That is not a PDF under 50 MB.")
    path = st.data_dir / "uploads" / (secrets.token_hex(4) + ".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    st.pdf_path = path
    st.amendment["title"] = file.filename or "Uploaded protocol"
    ag.log("protocol_uploaded", record=st.amendment["id"], after={"file": file.filename, "bytes": len(data), "pages": pdfutil.page_count(data)})
    ag.start(ag.run_sponsor())
    return {"ok": True, "pages": pdfutil.page_count(data)}


# ------------------------------------------------------------------ casebook edits (the EDC application's own API)
@app.post("/api/draft/op")
async def draft_op(op: dict, authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    if ag.role != "sponsor":
        raise HTTPException(403, "Only the sponsor can change the casebook design.")
    if st.flow.state != "build":
        raise HTTPException(409, "The casebook is locked. It can only be changed while a build is in progress.")
    goal = ag.current_goal
    try:
        res = casebook.apply_op(st.draft, op)
    except (casebook.DiffError, KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, str(e))
    src = (goal or {}).get("op") or {}
    st.ledger.append(
        actor_type="ai" if goal else "person", actor_id="navigator" if goal else ag.user_id, role=ag.role,
        action=op["kind"], record=op.get("target_id"), before=res["before"], after=res["after"],
        reason=src.get("rationale") if goal else "Entered by hand",
        source={"page": src.get("source_page"), "quote": src.get("source_quote"), "op_id": src.get("op_id")} if src else None,
        model=(agents.fixtures.FIXTURE_MODEL if agents.fixture_mode() else config.job("navigator")["model"]) if goal else None,
        prompt_hash=st.build().get("prompt_hash") if goal and st.build() else None)
    await hub.broadcast({"type": "changed"})
    return {"ok": True, **res}


@app.post("/api/deploy/production")
async def deploy_production(authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    if ag.role != "cro" or st.flow.state != "deploy":
        raise HTTPException(409, "Deployment is not open.")
    sig = st.signer.verify_signature(st.signatures["sponsor"]["seq"], st.record_for_signature("sponsor")[2])
    if not sig["ok"]:
        raise HTTPException(409, "Refused: " + sig["reason"])
    st.deploy["production"] = {"version": 2, "casebook_hash": casebook.content_hash(st.casebooks[2]), "sponsor_signature_seq": st.signatures["sponsor"]["seq"]}
    _log_ui(ag, "deploy_production", "production", None, st.deploy["production"])
    await hub.broadcast({"type": "changed"})
    return {"ok": True}


class SiteIds(BaseModel):
    site_ids: list[str]


@app.post("/api/deploy/sites")
async def deploy_sites(body: SiteIds, authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    if ag.role != "cro" or st.flow.state != "deploy" or not st.deploy["production"]:
        raise HTTPException(409, "Deploy version 2 to production first.")
    bad = [s for s in body.site_ids if s not in st.eligible_sites()]
    if bad:
        raise HTTPException(422, f"No ethics committee approval recorded for: {', '.join(bad)}")
    for sid in body.site_ids:
        if sid not in st.deploy["sites"]:
            st.deploy["sites"][sid] = {"version": 2, "on": seed.TODAY.isoformat()}
            _log_ui(ag, "deploy_site", sid, {"version": 1}, {"version": 2})
    await hub.broadcast({"type": "changed"})
    return {"ok": True, "deployed": len(st.deploy["sites"])}


@app.post("/api/transition/calculate")
async def transition_calculate(authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    if ag.role != "site" or ag.site_id not in st.deploy["sites"]:
        raise HTTPException(409, "Casebook version 2 is not live at this site.")
    st.plans[ag.site_id] = seed.transition_plan(st.site(ag.site_id), st.v1, st.casebooks[2])
    st.ledger.append(actor_type="rule", actor_id="rule-engine", role="site", action="transition_plan_calculated", record=f"TP-{ag.site_id}",
                     after={"affected": st.plans[ag.site_id]["affected"]}, reason=st.plans[ag.site_id]["rule"])
    await hub.broadcast({"type": "changed"})
    return st.plans[ag.site_id]


def _log_ui(ag: Agent, action: str, record: str, before, after):
    goal = ag.current_goal
    hub.store.ledger.append(actor_type="ai" if goal else "person", actor_id="navigator" if goal else ag.user_id, role=ag.role, action=action,
                            record=record, before=before, after=after, reason=(goal or {}).get("goal", "Entered by hand")[:200],
                            model=(agents.fixtures.FIXTURE_MODEL if agents.fixture_mode() else config.job("navigator")["model"]) if goal else None)


# ------------------------------------------------------------------ signature
class Sign(BaseModel):
    user_id: str
    password: str
    meaning: str


@app.post("/api/sign")
async def sign(body: Sign, authorization: str | None = Header(None)):
    ag = auth(authorization)
    st = hub.store
    if not ag.flow.can("signed", ag.role):
        raise HTTPException(409, "Nothing is waiting for your signature.")
    rtype, rid, rec = st.record_for_signature(ag.role, ag.site_id)
    try:
        m = st.signer.sign(session_user_id=ag.user_id, user_id=body.user_id, password=body.password, meaning=body.meaning,
                           record_type=rtype, record_id=rid, record=rec)
    except SignatureError as e:
        raise HTTPException(401, str(e))
    await ag.on_signed(m)
    return m


# ------------------------------------------------------------------ voice
@app.get("/api/live-token")
def live_token(authorization: str | None = Header(None)):
    auth(authorization)
    if agents.fixture_mode() or not config.api_key():
        raise HTTPException(503, "Voice needs GEMINI_API_KEY and live mode.")
    try:
        return llm.mint_live_token()
    except Exception as e:
        raise HTTPException(502, f"Could not mint a Live token: {e}")


# ------------------------------------------------------------------ record / replay
class Replay(BaseModel):
    session: str
    replay: bool = False
    speed: float = 1.0


@app.post("/api/replay")
def replay(body: Replay):
    llm.recorder.start(body.session, body.replay, body.speed)
    return {"ok": True, "session": body.session, "replaying": body.replay}


@app.get("/api/replays")
def replays():
    d = config.REPLAY_DIR
    return {"sessions": sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else [], "active": llm.recorder.session, "replaying": llm.recorder.replaying}


# ------------------------------------------------------------------ websocket
@app.websocket("/ws")
async def ws(sock: WebSocket, token: str):
    if token not in hub.tokens:
        await sock.close(code=4401)
        return
    await sock.accept()
    hub.sockets[token] = sock
    ag = hub.agent(token)
    try:
        while True:
            msg = await sock.receive_json()
            t = msg.get("type")
            if t == "hello":
                if not (ag.task and not ag.task.done()):
                    await ag.greet()
                else:
                    await ag.dot(*ag.dot_state)
            elif t == "act_result":
                ag.on_act_result(msg)
            elif t == "utterance":
                # run in the background so a long step never blocks "stop"
                asyncio.create_task(_utter(ag, sock, msg))
            elif t == "decision":
                # a click on a gate button is an unambiguous decision by the signed-in person
                ag.log("gate_click", after={"intent": msg.get("intent"), "option": msg.get("option")})
                asyncio.create_task(ag._guard(ag.handle_intent(msg["intent"], msg.get("option"), "clicked")))
    except WebSocketDisconnect:
        pass
    finally:
        if hub.sockets.get(token) is sock:
            hub.sockets.pop(token, None)


async def _utter(ag: Agent, sock: WebSocket, msg: dict):
    try:
        route = await ag.on_utterance(msg.get("transcript", ""), msg.get("audio"), msg.get("mime", "audio/wav"), typed=bool(msg.get("typed")))
    except Exception as e:
        route = {"action": "error", "error": str(e)}
        await ag.dot("waiting", f"I could not process that: {e}")
    try:
        await sock.send_json({"type": "utterance_result", "id": msg.get("id"), **{k: v for k, v in route.items()}})
    except Exception:
        pass


# RELAY_RECORD=<name> saves every model response of this run under replays/<name>/;
# RELAY_REPLAY=<name> plays that recording back with the same timing and no network.
import os as _os

if _os.environ.get("RELAY_REPLAY"):
    llm.recorder.start(_os.environ["RELAY_REPLAY"], True, float(_os.environ.get("RELAY_REPLAY_SPEED", "1")))
elif _os.environ.get("RELAY_RECORD"):
    llm.recorder.start(_os.environ["RELAY_RECORD"], False)

# The existing RELAY environment (the audit-amendment-demo site), served from our own origin so
# the agent can act inside it. A browser will not let a page script a frame from another site.
_env = config.ROOT / "reference"
if (_env / "index.html").exists():
    app.mount("/env", StaticFiles(directory=_env, html=True), name="env")

_dist = config.ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
