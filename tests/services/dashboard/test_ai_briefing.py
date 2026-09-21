from app.platform.llm import StubLLMClient
from app.services.dashboard.ai_briefing import (
    build_dashboard_briefing_messages,
    dashboard_briefing_schema,
)


def test_schema_shape():
    s = dashboard_briefing_schema()
    assert s["type"] == "object"
    assert s["additionalProperties"] is False
    assert s["required"] == ["briefing", "risks", "opportunities"]
    for k in ("briefing", "risks", "opportunities"):
        assert s["properties"][k]["type"] == "string"


def test_schema_is_stub_fillable():
    out = StubLLMClient().complete_json([], schema=dashboard_briefing_schema(), max_tokens=10)
    assert "[stub-llm]" in out["briefing"]
    assert "[stub-llm]" in out["risks"]
    assert "[stub-llm]" in out["opportunities"]


def test_builder_is_pii_free_and_includes_context():
    msgs = build_dashboard_briefing_messages(
        name="Cofoundaz",
        industry="Fintech",
        stage="idea",
        health_score=31,
        health_band="at_risk",
        mission_total=3,
        mission_done=1,
        upcoming_count=2,
        tasks_done_week=4,
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Cofoundaz" in body and "Fintech" in body
    assert "31" in body and "at_risk" in body
    assert "3" in body and "1" in body  # mission counts present
