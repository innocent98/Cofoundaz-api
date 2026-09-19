from app.db.models.enums import CanvasType
from app.services.business.ai_fill import build_canvas_fill_messages
from app.services.business.canvas_defs import CANVAS_BLOCKS, canvas_json_schema


def test_canvas_json_schema_is_strict_and_typed():
    schema = canvas_json_schema(CanvasType.business_model)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    keys = [b.key for b in CANVAS_BLOCKS[CanvasType.business_model]]
    assert set(schema["properties"]) == set(keys)
    assert schema["required"] == list(schema["properties"])
    # list blocks -> array of strings; text blocks -> string
    for b in CANVAS_BLOCKS[CanvasType.business_model]:
        prop = schema["properties"][b.key]
        if b.kind == "list":
            assert prop == {"type": "array", "items": {"type": "string"}}
        else:
            assert prop == {"type": "string"}


def test_build_canvas_fill_messages_has_context_no_pii():
    msgs = build_canvas_fill_messages(
        name="Acme", industry="Fintech", stage="validation",
        blocks=CANVAS_BLOCKS[CanvasType.business_model],
    )
    assert [m.role for m in msgs] == ["system", "user"]
    user = msgs[1].content
    assert "Acme" in user and "Fintech" in user and "validation" in user
    assert "Key Partners" in user  # a block label is present
    assert "@" not in " ".join(m.content for m in msgs)  # no emails/PII


from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.services.business.service import get_or_create_canvas
from app.worker.handlers.ai import handle_canvas_ai_fill
from tests.factories import create_startup, create_user


def _job(startup_id, canvas_type):
    return Job(
        type="business.canvas.ai_fill",
        payload={"startup_id": str(startup_id), "canvas_type": canvas_type.value},
        status=JobStatus.running,
    )


def test_ai_fill_fills_empty_canvas(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db); s = create_startup(db, owner=u)
    handle_canvas_ai_fill(db, _job(s.id, CanvasType.business_model))
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    # every block now filled with the stub marker
    for b in CANVAS_BLOCKS[CanvasType.business_model]:
        val = canvas.blocks[b.key]
        assert (val and "[stub-llm]" in (val if isinstance(val, str) else val[0]))
    assert canvas.version >= 2  # bumped from the create's version 1


def test_ai_fill_preserves_user_filled_blocks(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db); s = create_startup(db, owner=u)
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    canvas.blocks = {**canvas.blocks, "value_propositions": ["MY OWN VALUE"]}
    db.flush()
    handle_canvas_ai_fill(db, _job(s.id, CanvasType.business_model))
    db.refresh(canvas)
    assert canvas.blocks["value_propositions"] == ["MY OWN VALUE"]  # preserved
    assert "[stub-llm]" in canvas.blocks["key_partners"][0]  # empty one filled


def test_ai_fill_noop_when_startup_missing(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    import uuid
    handle_canvas_ai_fill(db, _job(uuid.uuid4(), CanvasType.business_model))  # must not raise


def test_ai_fill_fails_loud_when_llm_errors(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db); s = create_startup(db, owner=u)
    import pytest
    with pytest.raises(RuntimeError):
        handle_canvas_ai_fill(db, _job(s.id, CanvasType.business_model))
