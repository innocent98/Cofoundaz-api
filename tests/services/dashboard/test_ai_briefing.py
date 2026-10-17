from app.platform.llm import StubLLMClient
from app.services.dashboard.ai_briefing import (
    build_dashboard_briefing_messages,
    dashboard_briefing_schema,
)


def test_schema_shape():
    s = dashboard_briefing_schema()
    assert s["type"] == "object"
    assert s["additionalProperties"] is False
    assert s["required"] == ["briefing", "risks", "opportunities", "actions"]
    assert s["properties"]["briefing"]["type"] == "string"
    risk = s["properties"]["risks"]["items"]
    assert risk["required"] == ["severity", "text", "module"]
    assert risk["additionalProperties"] is False
    action = s["properties"]["actions"]
    assert (action["minItems"], action["maxItems"]) == (1, 3)
    assert action["items"]["properties"]["kind"]["enum"] == ["task", "navigate"]


def test_schema_is_stub_fillable():
    out = StubLLMClient().complete_json([], schema=dashboard_briefing_schema(), max_tokens=10)
    assert "[stub-llm]" in out["briefing"]
    assert out["risks"][0]["severity"] == "high"
    assert "[stub-llm]" in out["risks"][0]["text"]
    assert out["actions"][0]["kind"] == "task"


def test_builder_includes_finance_context_when_known():
    msgs = build_dashboard_briefing_messages(
        name="N",
        industry=None,
        stage=None,
        health_score=None,
        health_band=None,
        mission_total=0,
        mission_done=0,
        upcoming_count=0,
        tasks_done_week=0,
        runway_months=4.5,
        runway_status="ok",
        monthly_revenue="10,000 NGN",
    )
    assert "Runway: 4.5 months" in msgs[1].content
    assert "10,000 NGN" in msgs[1].content


def test_builder_says_not_tracked_without_finance():
    msgs = build_dashboard_briefing_messages(
        name="N",
        industry=None,
        stage=None,
        health_score=None,
        health_band=None,
        mission_total=0,
        mission_done=0,
        upcoming_count=0,
        tasks_done_week=0,
        runway_status="no_data",
    )
    assert "Runway: no_data" in msgs[1].content
    assert "not tracked" in msgs[1].content


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
