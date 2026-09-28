from app.services.marketing.ai_prompts import (
    build_copy_messages,
    build_plan_week_messages,
    copy_schema,
    plan_week_schema,
)


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
