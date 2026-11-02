from app.db.models.enums import ChannelKey
from app.services.marketing.ai_prompts import (
    build_channel_fit_messages,
    build_channel_plan_messages,
    build_content_gap_messages,
    build_copy_messages,
    build_plan_week_messages,
    channel_fit_schema,
    channel_plan_schema,
    content_gap_schema,
    copy_schema,
    plan_week_schema,
)

_KEYS = [c.value for c in ChannelKey]


def test_copy_schema_requires_three_variants():
    s = copy_schema()
    v = s["properties"]["variants"]
    assert v["type"] == "array" and v["minItems"] == 3 and v["maxItems"] == 3
    assert s["required"] == ["variants"]


def test_copy_messages_include_inputs():
    msgs = build_copy_messages(
        asset_type="ad",
        channel="email",
        tone="bold",
        key_message="Launch week is here",
        cta="Sign up",
        segment_name="SMB founders",
    )
    user = msgs[1].content
    assert "ad" in user and "email" in user and "bold" in user
    assert "Launch week is here" in user and "Sign up" in user and "SMB founders" in user


def test_copy_messages_tolerate_optional_none():
    msgs = build_copy_messages(
        asset_type="social_post",
        channel=None,
        tone="friendly",
        key_message="Hi",
        cta=None,
        segment_name=None,
    )
    assert len(msgs) == 2


def test_plan_week_schema_shape():
    s = plan_week_schema()
    item = s["properties"]["entries"]["items"]["properties"]
    assert set(item) == {"title", "channel", "body", "day_offset"}


def test_plan_week_messages_include_context():
    msgs = build_plan_week_messages(stage="build", industry="fintech", name="Acme")
    assert msgs[0].role == "system"
    assert "build" in msgs[1].content and "fintech" in msgs[1].content


def test_channel_plan_schema_enumerates_the_eight_channels():
    schema = channel_plan_schema()
    mix = schema["properties"]["channel_mix"]["properties"]
    assert sorted(mix.keys()) == sorted(_KEYS)
    assert all(v["type"] == "integer" for v in mix.values())
    assert "rationale" in schema["properties"]


def test_channel_fit_schema_enumerates_the_eight_channels():
    notes = channel_fit_schema()["properties"]["notes"]["properties"]
    assert sorted(notes.keys()) == sorted(_KEYS)


def test_plan_week_channel_is_enum_constrained():
    item = plan_week_schema()["properties"]["entries"]["items"]
    assert sorted(item["properties"]["channel"]["enum"]) == sorted(_KEYS)


def test_channel_plan_messages_carry_objective_and_stage():
    msgs = build_channel_plan_messages(
        objective="leads", stage="mvp", industry="fintech", budget=50000
    )
    joined = " ".join(m.content for m in msgs)
    assert "leads" in joined and "mvp" in joined


def test_channel_fit_messages_carry_statuses():
    msgs = build_channel_fit_messages(
        stage="mvp", industry="fintech", statuses={"search": "active"}
    )
    joined = " ".join(m.content for m in msgs)
    assert "search" in joined and "active" in joined


def test_content_gap_schema_shape():
    schema = content_gap_schema()
    item = schema["properties"]["gaps"]["items"]
    assert set(item["properties"]) == {"title", "target_keyword", "angle"}
    assert schema["properties"]["gaps"]["maxItems"] == 7


def test_content_gap_messages_carry_keywords_and_stage():
    msgs = build_content_gap_messages(keywords=["daily savings"], stage="mvp", industry="fintech")
    joined = " ".join(m.content for m in msgs)
    assert "daily savings" in joined and "mvp" in joined
