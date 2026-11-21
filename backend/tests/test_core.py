import copy
import json

import pytest

from relay import casebook, fixtures, rules, seed
from relay.ledger import GENESIS, Ledger, canonical_json, entry_hash
from relay.signatures import MEANINGS, SignatureError, Signer
from relay.state import Flow, TransitionError


# ---------- rule engine ----------
def ev(expr, data, event=None):
    return rules.evaluate(rules.parse(expr), rules.Ctx(data, event, ["V2", "V2.1", "V7"]))


D = {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "Yes"}, "EX": {"EXSTDTC": "2017-06-01T09:00"}},
     "V2.1": {"PK": {"PKDTC": "2017-06-03T09:30"}}}


def test_refs_and_comparisons():
    assert ev('$V2.RAND.PKSUB = "Yes"', D) is True
    assert ev('$V2.RAND.PKSUB = "yes"', D) is True  # case-insensitive text
    assert ev('$V2.RAND.PKSUB != "Yes"', D) is False
    assert ev("$V2.1.PK.PKDTC > $V2.EX.EXSTDTC", D) is True  # dotted event id


def test_datetime_arithmetic_in_hours():
    assert ev("PK.PKDTC - $V2.EX.EXSTDTC", D, "V2.1") == 48.5
    assert ev("abs((PK.PKDTC - $V2.EX.EXSTDTC) - 48h) > 1h", D, "V2.1") is False
    assert ev("abs((PK.PKDTC − $V2.EX.EXSTDTC) − 48h) > 0.25h", D, "V2.1") is True  # unicode minus
    assert ev("2d", D) == 48


def test_missing_values_are_not_evaluable():
    assert ev('$V7.RAND.PKSUB = "Yes"', D) is rules.MISSING
    assert ev("is_blank($V7.RAND.PKSUB)", D) is True
    assert ev('$V7.RAND.PKSUB = "Yes" or $V2.RAND.PKSUB = "Yes"', D) is True
    assert ev('$V7.RAND.PKSUB = "Yes" and $V2.RAND.PKSUB = "No"', D) is False


@pytest.mark.parametrize("bad", ["", "$V2.RAND.PKSUB =", "abs(", "1 +* 2", "a ~ b"])
def test_bad_expressions_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse(bad)


def test_type_mismatch_raises():
    with pytest.raises(rules.RuleError):
        ev('$V2.EX.EXSTDTC > "Yes"', D)


def test_check_rule_catches_unknown_objects():
    cb = casebook.seed_v1()
    r = {"id": "X", "type": "dynamic", "when": '$V2.RAND.NOPE = "Yes"', "then": {"action": "add_events", "events": ["V99"]}}
    problems = rules.check_rule(r, cb)
    assert any("NOPE" in p for p in problems) and any("V99" in p for p in problems)


def test_fixture_subjects_pass_on_built_casebook():
    v2 = casebook.apply_diff(casebook.seed_v1(), fixtures.BUILD_A["ops"])
    out = rules.run_tests(v2, fixtures.BUILD_A["subjects"])
    assert out["passed"] == out["total"] == 6, json.dumps(out["results"], indent=1)
    assert "DYN_PK_EVENTS" in out["rules_fired"] and "PK_48H_V2_1" in out["rules_fired"]


def test_tests_fail_when_rule_is_wrong():
    ops = copy.deepcopy(fixtures.BUILD_A["ops"])
    ops[7]["rule"]["when"] = '$V2.RAND.PKSUB = "No"'  # inverted
    out = rules.run_tests(casebook.apply_diff(casebook.seed_v1(), ops), fixtures.BUILD_A["subjects"])
    assert out["passed"] < out["total"]


def test_v1_subject_gets_no_dynamic_events_and_unexpected_data_is_flagged():
    v2 = casebook.apply_diff(casebook.seed_v1(), fixtures.BUILD_A["ops"])
    out = rules.run_subject(v2, {"data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "No"}}, "V2.1": {"date": "2017-06-03"}}})
    assert "V2.1" not in out["events"]
    assert [q["rule"] for q in out["queries"]] == ["UNEXPECTED_EVENT"]


# ---------- casebook ----------
def test_apply_diff_is_pure_and_orders_events():
    v1 = casebook.seed_v1()
    before = copy.deepcopy(v1)
    v2 = casebook.apply_diff(v1, fixtures.BUILD_A["ops"])
    assert v1 == before and v2["version"] == 2
    ids = [e["id"] for e in v2["events"]]
    assert ids.index("V2") < ids.index("V2.1") < ids.index("V2.3") < ids.index("V3")
    assert ids.index("V8") < ids.index("V8.1") < ids.index("V9")
    assert len(v2["schedule"]["PK"]) == 15 and len(v2["codelists"]["PKTPT"]) == 11
    assert any(r["type"] == "Rule" and r["name"] == "DYN_PK_EVENTS" for r in casebook.compare(v1, v2))


def test_one_cell_at_a_time_equals_planned_diff():
    """The navigator ticks schedule cells one by one; the result must equal the planned diff."""
    v1 = casebook.seed_v1()
    ops = []
    for op in fixtures.BUILD_A["ops"]:
        if op["kind"] in ("assign_form", "update_review_plan"):
            ops += [{**op, "event_ids": [e]} for e in op["event_ids"]]
        elif op["kind"] == "add_codelist_entries":
            ops += [{**op, "entries": [e]} for e in op["entries"]]
        else:
            ops.append(op)
    assert casebook.content_hash(casebook.apply_diff(v1, ops)) == casebook.content_hash(casebook.apply_diff(v1, fixtures.BUILD_A["ops"]))


def test_invalid_ops_are_reported():
    v1 = casebook.seed_v1()
    bad = [{"op_id": "X1", "kind": "add_event", "target_id": "V2", "group": "TRT", "day": 1},
           {"op_id": "X2", "kind": "assign_form", "target_id": "NOPE", "event_ids": ["V2"]},
           {"op_id": "X3", "kind": "add_rule", "target_id": "R", "rule": {"type": "dynamic", "when": "((", "then": {}}}]
    assert [p["op_id"] for p in casebook.validate_diff(v1, bad)] == ["X1", "X2", "X3"]
    assert casebook.validate_diff(v1, fixtures.BUILD_A["ops"]) == []
    assert casebook.validate_diff(v1, fixtures.BUILD_B["ops"]) == []


# ---------- ledger ----------
def mk(tmp_path=None):
    lg = Ledger(tmp_path / "ledger.jsonl" if tmp_path else None)
    lg.append(actor_type="ai", actor_id="planner", role="sponsor", action="plan", model="m", prompt_hash="p", after={"a": 1})
    lg.append(actor_type="rule", actor_id="rule-engine", role="sponsor", action="tests", after={"passed": 6})
    lg.append(actor_type="person", actor_id="elena", role="sponsor", action="approve", reason="ok")
    return lg


def test_chain_links_and_verifies(tmp_path):
    lg = mk(tmp_path)
    assert lg.entries[0]["prev_hash"] == GENESIS
    assert lg.entries[1]["prev_hash"] == lg.entries[0]["hash"]
    e = lg.entries[1]
    assert e["hash"] == entry_hash(e["prev_hash"], e)
    assert lg.verify() == {"ok": True, "checked": 3, "head": lg.head}
    assert Ledger(tmp_path / "ledger.jsonl").verify()["ok"]  # survives a reload from disk


def test_entries_do_not_change_when_the_recorded_object_changes_later():
    """Found in a live run: a new form was logged by reference, then gained items, and the chain broke."""
    lg = Ledger()
    form = {"id": "ADA", "items": []}
    lg.append(actor_type="person", actor_id="x", role="sponsor", action="add_form", after=form)
    form["items"].append({"id": "ADADTC"})
    assert lg.entries[0]["after"] == {"id": "ADA", "items": []}
    assert lg.verify()["ok"]


def test_tampering_is_detected():
    lg = mk()
    lg.entries[1]["after"] = {"passed": 7}
    assert lg.verify()["broken_at"] == 2
    lg = mk()
    del lg.entries[1]
    assert not lg.verify()["ok"]
    lg = mk()
    lg.entries[0]["after"] = {"a": 2}
    lg.entries[0]["hash"] = entry_hash(GENESIS, lg.entries[0])  # re-hash one entry: the next link breaks
    assert lg.verify()["broken_at"] == 2


def test_ai_entries_need_a_model_and_canonical_json_is_stable():
    with pytest.raises(ValueError):
        Ledger().append(actor_type="ai", actor_id="x", role="sponsor", action="a")
    with pytest.raises(ValueError):
        Ledger().append(actor_type="robot", actor_id="x", role="sponsor", action="a")
    assert canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}) == '{"a":[2,{"c":2,"d":1}],"b":1}'


# ---------- signatures ----------
@pytest.fixture(scope="module")
def users():
    return seed.build_users(seed.build_sites())


U = "elena.marsh@sponsor.example"


def test_signature_binds_to_record(users):
    lg = mk()
    s = Signer(lg, users)
    rec = {"casebook": 2, "events": ["V2.1"]}
    m = s.sign(session_user_id=U, user_id=U, password=seed.DEMO_PASSWORD, meaning=MEANINGS["sponsor"],
               record_type="casebook", record_id="CB-v2", record=rec)
    assert m["printed_name"] == "Elena Marsh" and m["meaning"] == "Approved as Sponsor Data Manager" and m["signed_at"]
    assert s.verify_signature(m["seq"], rec)["ok"]
    assert s.verify_signature(m["seq"], {**rec, "events": ["V2.1", "V9.9"]})["reason"] == "The record was changed after it was signed"
    lg.entries[0]["after"] = "tampered"
    assert "chain is broken" in s.verify_signature(m["seq"], rec)["reason"]


def test_signature_needs_both_components_and_right_person(users):
    lg = mk()
    s = Signer(lg, users)
    kw = dict(record_type="casebook", record_id="CB-v2", record={}, meaning=MEANINGS["sponsor"])
    with pytest.raises(SignatureError):
        s.sign(session_user_id=U, user_id=U, password="wrong", **kw)
    with pytest.raises(SignatureError):
        s.sign(session_user_id=U, user_id=U, password="", **kw)
    with pytest.raises(SignatureError):  # someone else's valid credentials in my session
        s.sign(session_user_id=U, user_id="jordan.morgan@cro.example", password=seed.DEMO_PASSWORD, **kw)
    with pytest.raises(SignatureError):  # wrong meaning for the role
        s.sign(session_user_id=U, user_id=U, password=seed.DEMO_PASSWORD, record_type="c", record_id="r", record={}, meaning=MEANINGS["cro"])
    assert [e["action"] for e in lg.entries[3:]] == ["signature_failed"] * 4
    assert not any(e["action"] == "signature" for e in lg.entries)


def test_lockout_after_three_failures(users):
    s = Signer(mk(), users)
    kw = dict(session_user_id=U, user_id=U, record_type="c", record_id="r", record={}, meaning=MEANINGS["sponsor"])
    for _ in range(3):
        with pytest.raises(SignatureError):
            s.sign(password="nope", **kw)
    with pytest.raises(SignatureError, match="locked"):
        s.sign(password=seed.DEMO_PASSWORD, **kw)


# ---------- state machine ----------
def test_happy_path_and_role_ownership():
    f = Flow()
    with pytest.raises(TransitionError):
        f.fire("open_amendment", "cro", by_human=True)  # wrong role
    with pytest.raises(TransitionError):
        f.fire("open_amendment", "sponsor")  # a model cannot open it on its own
    f.fire("open_amendment", "sponsor", by_human=True)
    for e in ("diff_done", "plan_done", "audit_done"):
        f.fire(e, "sponsor")
    assert f.state == "gate_option" and f.waiting_for_human
    with pytest.raises(TransitionError):
        f.fire("approve", "sponsor", by_human=True)  # cannot approve before the build
    f.fire("choose_option", "sponsor", by_human=True)
    f.fire("build_done", "sponsor")
    f.fire("verify_passed", "sponsor")
    with pytest.raises(TransitionError):
        f.fire("signed", "sponsor", by_human=True)  # cannot sign before approving
    f.fire("approve", "sponsor", by_human=True)
    with pytest.raises(TransitionError):
        f.fire("signed", "sponsor")  # signing is always a person
    f.fire("signed", "sponsor", by_human=True)
    assert f.state == "handoff_cro" and f.owner == "cro" and f.allowed("sponsor") == []
    f.fire("open_deploy", "cro", by_human=True)
    f.fire("deploy_done", "cro")
    f.fire("signed", "cro", by_human=True)
    assert f.owner == "site"
    f.fire("open_transition", "site", by_human=True)
    f.fire("transition_done", "site")
    f.fire("signed", "site", by_human=True)
    assert f.state == "done"


def test_go_back_paths():
    f = Flow("gate_approve")
    f.fire("reject", "sponsor", by_human=True)
    assert f.state == "gate_option"
    f = Flow("gate_approve")
    f.fire("choose_option", "sponsor", by_human=True)  # "try option B instead"
    assert f.state == "build"
    f = Flow("verify")
    f.fire("verify_failed", "sponsor")
    assert f.state == "gate_option"
    f = Flow("sign")
    f.fire("undo", "sponsor", by_human=True)
    assert f.state == "gate_approve"


# ---------- transition plan ----------
def test_transition_plan_is_deterministic_and_scoped():
    sites = seed.build_sites()
    assert sites == seed.build_sites() and len(sites) == 27
    v1 = casebook.seed_v1()
    v2 = casebook.apply_diff(v1, fixtures.BUILD_A["ops"])
    for site in sites:
        plan = seed.transition_plan(site, v1, v2)
        for row, subj in zip(plan["participants"], site["subjects"]):
            if not subj["pk_substudy"] or subj["status"] != "treatment":
                assert not row["visits"]
            else:
                assert len(row["visits"]) == 7
                for v in row["visits"]:
                    assert (v["state"] == "missed") == (v["window"][1] < seed.TODAY.isoformat())
        if site["country"] != "China":
            assert plan["affected"] == 0
    assert sum(seed.transition_plan(s, v1, v2)["affected"] for s in sites) > 0
