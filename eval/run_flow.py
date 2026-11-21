"""Drive the real UI through the amendment flow in a headless browser.

Used three ways:
  - as the UI end-to-end test (fixture mode, no key needed)
  - to take the screenshots in docs/
  - by eval/run_eval.py for navigator reliability (live mode, N runs)

  backend/.venv/bin/python eval/run_flow.py --base http://127.0.0.1:8017 --shots docs/shots --roles sponsor,cro,site

The human's part is played by typing into the agent's text box and filling the signature
dialog. Everything else is done by the agent through the DOM action layer.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PASSWORD = "relay-demo-2026"
SPONSOR, CRO = "elena.marsh@sponsor.example", "jordan.morgan@cro.example"


class Flow:
    def __init__(self, page: Page, base: str, shots: Path | None, timeout: float):
        self.page, self.base, self.shots, self.timeout, self.n = page, base, shots, timeout, 0
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: self.errors.append(f"console: {m.text}") if m.type == "error" and "favicon" not in m.text else None)

    def shot(self, name: str):
        if self.shots:
            self.n += 1
            self.page.wait_for_timeout(250)
            self.page.screenshot(path=str(self.shots / f"{self.n:02d}-{name}.png"))

    def snap(self) -> dict:
        return self.page.evaluate("""async () => { const t = sessionStorage.getItem('relay.token');
            const r = await fetch('/api/snapshot', {headers: {Authorization: 'Bearer ' + t}}); return r.json() }""")

    def login(self, user: str, role: str, site: str | None = None, fast: bool = True):
        self.page.goto(self.base + ("/?fast=1#signin" if fast else "/#signin"))
        self.page.evaluate("sessionStorage.clear()")
        self.page.reload()
        self.page.get_by_role("radio", name=role).click()
        if site:
            self.page.get_by_role("combobox").select_option(site)
        self.page.get_by_label("User ID").fill(user)
        self.page.get_by_label("Password").fill(PASSWORD)
        self.page.get_by_role("button", name="Sign in").click()
        self.page.get_by_label("Type to the agent").wait_for()
        self.page.wait_for_timeout(400)

    def say(self, text: str):
        box = self.page.get_by_label("Type to the agent")
        box.fill(text)
        box.press("Enter")

    def wait_state(self, pred, what: str, timeout: float | None = None) -> dict:
        end = time.time() + (timeout or self.timeout)
        s = {}
        while time.time() < end:
            s = self.snap()
            if pred(s):
                return s
            self.page.wait_for_timeout(200)
        raise TimeoutError(f"timed out waiting for {what}; state={s.get('flow', {}).get('state')} dot={s.get('dot')}")

    def sign(self, user: str, password: str = PASSWORD):
        dlg = self.page.get_by_role("dialog", name="Electronic signature")
        dlg.wait_for()
        dlg.get_by_label("User ID").fill(user)
        dlg.get_by_label("Password").fill(password)
        dlg.get_by_role("button", name="Sign:").click()

    # ---- roles ----
    def sponsor(self, option: str | None, answers: str = "keep") -> dict:
        t0 = time.time()
        self.login(SPONSOR, "Sponsor")
        self.shot("sponsor-presence")
        self.say("Open the new amendment")
        s = self.wait_state(lambda s: s["flow"]["state"] == "gate_option" and s["dot"]["state"] == "waiting", "the design gate")
        self.shot("sponsor-gate-option")
        t_plan = time.time() - t0
        self.say(f"Go with option {option}" if option else "Go ahead with your recommendation")
        seen_q = 0
        end = time.time() + self.timeout
        while time.time() < end:
            s = self.snap()
            if s["flow"]["state"] == "gate_approve" and s["dot"]["state"] == "waiting":
                break
            if s["flow"]["state"] == "gate_option" and s["amendment"]["chosen"] and s["dot"]["state"] == "waiting" and not s.get("question"):
                raise RuntimeError("build or verification failed: " + s["dot"]["caption"])
            if s.get("question"):
                seen_q += 1
                if seen_q == 1:
                    self.shot("sponsor-question")
                # "drop" only where dropping is offered; where the alternative is "Stop", keep going
                self.say("Drop it" if answers == "drop" and s["question"]["no"] == "Drop it" else "Keep it")
                self.wait_state(lambda x, q=s["question"]["text"]: (x.get("question") or {}).get("text") != q, "the question to clear", 30)
            elif seen_q and self.n < 5 and s["flow"]["state"] == "build" and len(s["draft"]["events"]) > len(s["v1"]["events"]) + 2:
                self.shot("sponsor-building")
            self.page.wait_for_timeout(200)
        else:
            raise TimeoutError("timed out waiting for the approval gate: " + json.dumps(s["dot"]))
        self.shot("sponsor-gate-approve")
        self.say("Approve")
        self.sign(SPONSOR)
        s = self.wait_state(lambda s: s["flow"]["global"] == "handoff_cro", "the sponsor signature")
        self.shot("sponsor-signed")
        self.page.get_by_role("dialog", name="Electronic signature").get_by_role("button", name="Close").click()
        return {"plan_seconds": round(t_plan, 1), "total_seconds": round(time.time() - t0, 1), "questions": seen_q,
                "tests": {k: s["amendment"]["tests"][k] for k in ("passed", "total")}, "build_matches_plan": s["amendment"]["build_check"]["ok"],
                "ops": len(s["amendment"]["builds"][s["amendment"]["chosen"]]["ops"]), "option": s["amendment"]["chosen"], "nav": s["nav_stats"], "costs": s["costs"]}

    def cro(self) -> dict:
        t0 = time.time()
        self.login(CRO, "CRO")
        self.shot("cro-presence")
        self.say("Deploy it")
        s = self.wait_state(lambda s: s["flow"]["global"] == "rollout" and s["dot"]["state"] == "waiting", "the rollout gate")
        self.shot("cro-rollout")
        self.say("Sign")
        self.sign(CRO)
        s2 = self.wait_state(lambda s: s["flow"]["global"] == "handoff_site", "the CRO signature")
        return {"total_seconds": round(time.time() - t0, 1), "sites_live": len(s["deploy"]["sites"]), "nav": s2["nav_stats"]}

    def site(self, site_id: str) -> dict:
        t0 = time.time()
        user = f"coord.{site_id.lower()}@site.example"
        self.login(user, "Site", site_id)
        self.shot("site-presence")
        self.say("Open the transition plan")
        s = self.wait_state(lambda s: s["flow"]["state"] == "site_sign" and s["dot"]["state"] == "waiting", "the site sign-off gate")
        self.shot("site-transition")
        self.say("Sign")
        self.sign(user)
        self.wait_state(lambda s: s["flow"]["state"] == "done", "the site signature")
        self.page.get_by_role("dialog", name="Electronic signature").get_by_role("button", name="Close").click()
        self.page.get_by_role("button", name="Audit Trail").click()
        self.page.get_by_role("button", name="Verify chain").first.click()
        self.page.get_by_text("Chain verified").wait_for()
        self.shot("site-audit-verified")
        return {"total_seconds": round(time.time() - t0, 1), "affected": s["plan"]["affected"], "nav": s["nav_stats"]}


def run(base: str, roles: list[str], shots: Path | None = None, option: str | None = None, timeout: float = 600, reset: bool = True,
        site_id: str | None = None, headed: bool = False, answers: str = "keep") -> dict:
    out: dict = {"ok": False}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": 1480, "height": 900})
        f = Flow(page, base, shots, timeout)
        try:
            if reset:
                page.request.post(base + "/api/reset")
            if "sponsor" in roles:
                out["sponsor"] = f.sponsor(option, answers)
            if "cro" in roles:
                out["cro"] = f.cro()
            if "site" in roles:
                if not site_id:
                    live = [s["id"] for s in f.snap()["sites"] if s["deployed"]]
                    site_id = next((s for s in ("CN-108", "CN-105", "CN-106") if s in live), live[0])
                out["site"] = f.site(site_id)
            out["ok"] = True
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            if shots:
                page.screenshot(path=str(shots / "error.png"))
        out["browser_errors"] = f.errors[:10]
        browser.close()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8017")
    ap.add_argument("--roles", default="sponsor,cro,site")
    ap.add_argument("--shots")
    ap.add_argument("--option")
    ap.add_argument("--answers", default="keep", choices=["keep", "drop"])
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-reset", action="store_true", help="keep the server state (run later roles after an earlier run)")
    a = ap.parse_args()
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    res = run(a.base, a.roles.split(","), shots, a.option, a.timeout, reset=not a.no_reset, headed=a.headed, answers=a.answers)
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["ok"] else 1)
