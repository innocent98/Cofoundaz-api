from app.platform.llm import StubLLMClient
from app.services.mission.ai_reason import build_mission_reason_messages, mission_reason_schema


def test_schema_shape():
    s = mission_reason_schema(3)
    assert s["type"] == "object"
    assert s["additionalProperties"] is False
    arr = s["properties"]["reasons"]
    assert arr["type"] == "array" and arr["maxItems"] == 3
    item = arr["items"]
    assert item["required"] == ["order", "reason"]
    assert item["additionalProperties"] is False
    assert item["properties"]["order"]["type"] == "integer"
    assert item["properties"]["reason"]["type"] == "string"


def test_schema_is_stub_fillable():
    # The stub returns a single-element array; order->0 (integer), reason->marker string.
    out = StubLLMClient().complete_json([], schema=mission_reason_schema(3), max_tokens=10)
    assert out["reasons"][0]["order"] == 0
    assert "[stub-llm]" in out["reasons"][0]["reason"]


def test_builder_is_pii_free_and_lists_tasks():
    msgs = build_mission_reason_messages(
        [(0, "Define MVP"), (1, "Talk to users")],
        name="Cofoundaz",
        industry="Fintech",
        stage="idea",
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Define MVP" in body and "Talk to users" in body
    assert "order 0" in body and "order 1" in body
    assert "Cofoundaz" in body and "Fintech" in body and "idea" in body
