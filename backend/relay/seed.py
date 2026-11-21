"""Seed data. Study and site facilities are real registry data (ClinicalTrials.gov, NCT03235752).
Site staff, subjects, ethics committee dates and user accounts are synthetic."""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from .signatures import hash_password

TODAY = date(2017, 6, 12)  # fixed "today" for the seeded study, four weeks after protocol Ver. 1.1
DEMO_PASSWORD = "relay-demo-2026"  # demo accounts only; printed in the README
_SALT = bytes.fromhex("52454c41592d64656d6f2d73616c7421")

_SURNAMES = {"China": ["Lin", "Zhao", "Wang", "Huang", "Chen", "Liu", "Zhang", "Yang", "Wu", "Xu", "Sun", "Zhou"],
             "South Korea": ["Seo", "Kim", "Park", "Lee"], "Taiwan": ["Tsai", "Hsu", "Chang", "Lin"]}
_GIVEN = {"China": ["Meihua", "Lei", "Fang", "Bo", "Yu", "Jia", "Ning", "Hong", "Tao", "Xia", "Jun", "Min"],
          "South Korea": ["Min-jun", "Da-eun", "Ji-woo", "Su-bin"], "Taiwan": ["Yi-chen", "Wei-ting", "Chia-hao", "Pei-shan"]}
_PENDING_EC = {"CN-103": "2017-05-22", "CN-117": "2017-05-26", "TW-302": "2017-05-30"}


def registry() -> dict:
    return json.loads((Path(__file__).parent / "data" / "ctgov.json").read_text())


def build_sites() -> list[dict]:
    rng = random.Random(3235752)
    out = []
    for gi, c in enumerate(registry()["sites"]):
        cn = c["ctry"] == "China"
        sn, gn = _SURNAMES[c["ctry"]], _GIVEN[c["ctry"]]
        pi = f"{rng.choice(sn)} {rng.choice(gn)}"
        coord = f"{rng.choice(sn)} {rng.choice(gn)}"
        subjects = []
        for j in range(rng.randint(3, 6)):
            r = rng.random()
            if r < 0.15:
                status, day = "screening", None
            elif r > 0.95:
                status, day = "completed", 110
            else:
                status, day = "treatment", rng.randint(0, 100)
            # PK substudy exists only in Mainland China (protocol 6.2.5); about half of randomised subjects there
            pk = None if status == "screening" else (cn and rng.random() < 0.5)
            sid = f"{c['id'].replace('-', '')}-{j + 1:03d}"
            subjects.append({"id": sid, "status": status, "study_day": day, "pk_substudy": pk,
                             "randomised_on": None if day is None else (TODAY - timedelta(days=day)).isoformat()})
        ec = _PENDING_EC.get(c["id"])
        out.append({
            "id": c["id"], "name": c["name"], "city": c["city"], "country": c["ctry"],
            "pi": pi, "coordinator": coord, "coordinator_user": f"coord.{c['id'].lower()}@site.example",
            "ec_submitted": ec or "2017-05-19",
            "ec_approved": None if ec else (date(2017, 6, 1) + timedelta(days=(gi * 5) % 10)).isoformat(),
            "subjects": subjects,
        })
    return out


def build_users(sites: list[dict]) -> dict[str, dict]:
    pw = hash_password(DEMO_PASSWORD, _SALT)
    users = {
        "elena.marsh@sponsor.example": {"name": "Elena Marsh", "title": "Clinical Data Manager", "role": "sponsor", "org": "Sponsor"},
        "jordan.morgan@cro.example": {"name": "Jordan Morgan", "title": "CRO Study Manager", "role": "cro", "org": "CRO"},
    }
    for s in sites:
        users[s["coordinator_user"]] = {"name": s["coordinator"], "title": "Site Coordinator", "role": "site",
                                        "org": s["name"], "site_id": s["id"]}
    for u in users.values():
        u["password_hash"] = pw
    return users


def transition_plan(site: dict, v1: dict, v2: dict, today: date = TODAY) -> dict:
    """Fixed rule, no model: map the new events onto participants already enrolled.
    A new event applies to a participant when a dynamic rule in v2 would add it for them
    (here: PK substudy = Yes). It is 'missed' if its day is already past, else 'upcoming'."""
    old = {e["id"] for e in v1["events"]}
    new_events = [e for e in v2["events"] if e["id"] not in old and e["day"] is not None]
    rows = []
    for s in site["subjects"]:
        if s["status"] == "completed":
            rows.append({"subject": s["id"], "status": s["status"], "affected": False, "reason": "Completed the study", "visits": []})
            continue
        if s["status"] == "screening":
            rows.append({"subject": s["id"], "status": s["status"], "affected": False,
                         "reason": "Not randomised yet: starts directly on casebook version 2", "visits": []})
            continue
        if not s["pk_substudy"]:
            rows.append({"subject": s["id"], "status": s["status"], "affected": False,
                         "reason": "Not in the PK substudy: no new visits", "visits": []})
            continue
        rand = date.fromisoformat(s["randomised_on"])
        visits = []
        for e in new_events:
            due = rand + timedelta(days=e["day"])
            lo = due - timedelta(days=e["window_before"] or 0)
            hi = due + timedelta(days=e["window_after"] or 0)
            state = "missed" if hi < today else "upcoming"
            visits.append({"event": e["id"], "label": e["label"], "day": e["day"], "due": due.isoformat(),
                           "window": [lo.isoformat(), hi.isoformat()], "state": state,
                           "note": "Before the amendment took effect: not created, no deviation" if state == "missed"
                                   else ("Due within 7 days: re-consent first" if (lo - today).days <= 7 else "Schedule")})
        rows.append({"subject": s["id"], "status": s["status"], "study_day": s["study_day"], "affected": any(v["state"] == "upcoming" for v in visits),
                     "reason": "PK substudy participant", "visits": visits})
    aff = [r for r in rows if r["affected"]]
    return {"site": site["id"], "calculated_on": today.isoformat(), "participants": rows,
            "affected": len(aff), "upcoming_visits": sum(v["state"] == "upcoming" for r in aff for v in r["visits"]),
            "rule": "For each randomised PK-substudy participant: due = randomisation date + event day; missed if the window closed before today."}
