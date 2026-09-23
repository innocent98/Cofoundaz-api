from app.db.models.enums import StartupStage
from app.services.learning.ai_reason import build_learning_reason_messages, learning_reason_schema


def test_schema_shape():
    schema = learning_reason_schema()
    assert schema["properties"]["reason"]["type"] == "string"
    assert schema["required"] == ["reason"]
    assert schema["additionalProperties"] is False


def test_messages_include_stage_and_titles():
    msgs = build_learning_reason_messages(
        stage=StartupStage.build, course_titles=["Pricing 101", "Sales Basics"]
    )
    assert msgs[0].role == "system"
    user = msgs[1].content
    assert "build" in user
    assert "Pricing 101" in user and "Sales Basics" in user


def test_messages_tolerate_no_stage_and_empty_titles():
    msgs = build_learning_reason_messages(stage=None, course_titles=[])
    assert len(msgs) == 2  # no crash, still a valid 2-message prompt
