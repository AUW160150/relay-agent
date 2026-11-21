"""The SDK wrapper against a mocked HTTP transport. This checks that our requests serialise
through the real SDK and that we parse SDK response objects correctly. It does NOT prove the
API accepts them: only scripts/smoke_models.py with a key does that."""
import json

import httpx
import pytest
from google import genai

from relay import agents, config, llm


@pytest.fixture
def api(monkeypatch, tmp_path):
    calls = []
    replies = []

    def handler(req: httpx.Request):
        calls.append({"url": str(req.url), "body": json.loads(req.content)})
        return httpx.Response(200, json=replies.pop(0))

    c = genai.Client(api_key="test", http_options={"api_version": config.models()["api_version"],
                                                   "httpx_async_client": httpx.AsyncClient(transport=httpx.MockTransport(handler))})
    monkeypatch.setattr(llm, "_client", c)
    monkeypatch.setattr(config, "REPLAY_DIR", tmp_path)
    monkeypatch.setenv("RELAY_MODE", "live")
    llm.recorder.session = None
    return calls, replies


def interaction(steps):
    return {"id": "i1", "status": "completed", "steps": steps, "usage": {"total_input_tokens": 1000, "total_output_tokens": 200, "total_thought_tokens": 50}}


def text_step(t):
    return {"type": "model_output", "content": [{"type": "text", "text": t}]}


async def test_generate_json_request_and_parse(api):
    calls, replies = api
    replies.append(interaction([text_step('{"options": []}')]))
    out = await llm.generate("planner", [llm.text_part("x"), llm.pdf_part(b"%PDF-1.4")], system="sys", schema=agents.OPTIONS_SCHEMA)
    body = calls[0]["body"]
    assert calls[0]["url"].endswith("/v1beta/interactions")
    assert body["model"] == config.job("planner")["model"] and body["system_instruction"] == "sys" and body["store"] is False
    assert body["generation_config"]["thinking_level"] == config.job("planner")["thinking_level"]
    assert body["response_format"]["mime_type"] == "application/json" and "schema" in body["response_format"]
    assert out["json"] == {"options": []} and out["usage"] == {"input_tokens": 1000, "output_tokens": 200, "thought_tokens": 50}
    assert out["cost_usd"] == pytest.approx((1000 * 2.0 + 250 * 10.0) / 1e6) and len(out["prompt_hash"]) == 64


async def test_navigator_function_calls_are_parsed_in_order(api):
    calls, replies = api
    replies.append(interaction([{"type": "function_call", "id": "c1", "name": "type", "arguments": {"agent_id": "event-id", "text": "V2.1"}},
                                {"type": "function_call", "id": "c2", "name": "click", "arguments": {"agent_id": "event-save"}}]))
    out = await agents.navigator_step("Add event V2.1", "events", ["events"], [{"id": "event-id", "role": "textbox", "label": "Event ID", "value": ""}], "AAAA", [])
    body = calls[0]["body"]
    assert [t["name"] for t in body["tools"]] == ["navigate", "click", "type", "select", "scroll", "done", "fail"]
    assert any(p.get("type") == "image" for p in body["input"][0]["content"])
    assert "[textbox agent_id=event-id] Event ID" in body["input"][0]["content"][0]["text"]
    assert [(c["name"], c["args"]) for c in out["calls"]] == [("type", {"agent_id": "event-id", "text": "V2.1"}), ("click", {"agent_id": "event-save"})]


async def test_invalid_json_from_the_model_is_an_error(api):
    _, replies = api
    replies.append(interaction([text_step("not json")]))
    with pytest.raises(llm.ModelError):
        await llm.generate("planner", [llm.text_part("x")], schema=agents.OPTIONS_SCHEMA)


async def test_record_then_replay_returns_the_same_response_without_the_network(api):
    calls, replies = api
    replies.append(interaction([text_step('{"options": [{"id": "A"}]}')]))
    llm.recorder.start("take1", replay=False)
    first = await llm.generate("planner", [llm.text_part("x")], schema=agents.OPTIONS_SCHEMA)
    llm.recorder.start("take1", replay=True, speed=1000)
    again = await llm.generate("planner", [llm.text_part("x")], schema=agents.OPTIONS_SCHEMA)
    assert len(calls) == 1 and again["json"] == first["json"] and again["replayed"] is True
    with pytest.raises(llm.ModelError, match="no recording"):
        await llm.generate("planner", [llm.text_part("y")], schema=agents.OPTIONS_SCHEMA)
    llm.recorder.session = None


def test_build_normalisation_and_auditor_blindness():
    raw = {"option_id": "A", "ops": [{"op_id": "OP-01", "kind": "add_rule", "target_id": "R1", "rationale": "secret reasoning", "uncertainty": "hmm", "basis": "interpreted",
                                      "rule": {"type": "edit_check", "description": "d", "when": "PK.PKDTC < $V2.EX.EXSTDTC", "then_action": "open_query",
                                               "then_events": [], "then_message": "m", "scope_form": "PK", "scope_events": ["V3"]},
                                      "source_change_ids": ["CH-1"], "source_page": 51, "source_quote": "q"}],
           "subjects": [{"id": "T-01", "description": "", "exercises": ["R1"], "visit_dates": [{"event": "V2", "date": "2017-06-01"}],
                         "values": [{"event": "V3", "form": "PK", "item": "PKDTC", "value": "2017-05-01T09:00"}], "expect_events_added": [], "expect_queries": ["R1"]}]}
    b = agents.normalise_build(raw)
    assert b["ops"][0]["rule"]["then"] == {"action": "open_query", "message": "m"} and b["ops"][0]["rule"]["scope"] == {"form": "PK", "events": ["V3"]}
    assert b["subjects"][0]["data"] == {"V2": {"date": "2017-06-01"}, "V3": {"PK": {"PKDTC": "2017-05-01T09:00"}}}
    seen = agents.diff_for_audit(b)
    assert "secret reasoning" not in seen and "hmm" not in seen and "interpreted" not in seen and "PK.PKDTC" in seen


def test_utterance_routing_thresholds():
    A = lambda addr, rel, intent, conf: {"addressed_to_agent": {"p": addr}, "relevant_to_current_task": {"p": rel},
                                         "intent": {"choice": intent, "confidence": conf}, "option": {"choice": "B"}}
    r = agents.route_utterance(A(0.95, 0.9, "choose_option", 0.9))
    assert (r["action"], r["intent"], r["option"]) == ("act", "choose_option", "B")
    assert agents.route_utterance(A(0.10, 0.9, "approve", 0.9))["action"] == "ignore"      # side conversation: say nothing
    assert agents.route_utterance(A(0.55, 0.9, "approve", 0.9))["action"] == "ask"         # unsure: "Was that for me?"
    assert agents.route_utterance(A(0.55, 0.1, "approve", 0.9))["action"] == "ignore"      # unsure and off-topic
    assert agents.route_utterance(A(0.95, 0.9, "approve", 0.30))["action"] == "ask"        # for me, but unclear what
    assert agents.route_utterance(A(0.95, 0.1, "other", 0.9))["action"] == "ignore"
