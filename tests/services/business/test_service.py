import pytest

from app.core.errors import AppError, CanvasVersionConflict
from app.db.models.enums import CanvasType, RecordKind
from app.services.business.service import (
    completion,
    get_or_create_canvas,
    overview,
    save_canvas,
    validate_blocks,
)
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def test_get_or_create_lazily_builds_full_scaffold(db):
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.business_model)
    assert c.version == 1
    assert set(c.blocks) == {
        b.key
        for b in __import__(
            "app.services.business.canvas_defs", fromlist=["CANVAS_BLOCKS"]
        ).CANVAS_BLOCKS[CanvasType.business_model]
    }
    assert get_or_create_canvas(db, s, CanvasType.business_model).id == c.id  # idempotent


def test_validate_rejects_unknown_key_and_wrong_kind(db):
    with pytest.raises(AppError):
        validate_blocks(CanvasType.swot, {"not_a_block": []})
    with pytest.raises(AppError):
        validate_blocks(CanvasType.swot, {"strengths": "should-be-a-list"})
    with pytest.raises(AppError):
        validate_blocks(CanvasType.mission_vision, {"mission": ["should-be-text"]})
    validate_blocks(CanvasType.swot, {"strengths": ["a", "b"]})  # ok, partial


def test_save_bumps_version_and_rejects_stale(db):
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.swot)
    saved = save_canvas(db, c, {"strengths": ["fast"]}, expected_version=1)
    assert saved.version == 2
    assert saved.blocks["strengths"] == ["fast"]
    with pytest.raises(CanvasVersionConflict):
        save_canvas(db, saved, {"weaknesses": ["slow"]}, expected_version=1)  # stale


def test_save_is_full_replace_not_partial_merge(db):
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.business_model)
    c = save_canvas(db, c, {"key_partners": ["Stripe"]}, expected_version=1)
    assert c.version == 2
    assert c.blocks["key_partners"] == ["Stripe"]
    c = save_canvas(db, c, {"channels": ["Web"]}, expected_version=2)
    assert c.version == 3
    assert c.blocks["channels"] == ["Web"]
    # key_partners was omitted from this save, so full-replace resets it to empty
    # rather than preserving the prior value (PUT semantics, not partial merge).
    assert c.blocks["key_partners"] == []


def test_completion_transitions_and_emits_once(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.business.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.mission_vision)
    assert completion(CanvasType.mission_vision, c.blocks)["status"] == "start"
    c = save_canvas(db, c, {"mission": "Do good"}, expected_version=1)
    assert completion(CanvasType.mission_vision, c.blocks)["status"] == "continue"
    assert events == []
    c = save_canvas(db, c, {"mission": "Do good", "vision": "World wins"}, expected_version=2)
    assert completion(CanvasType.mission_vision, c.blocks)["status"] == "complete"
    assert [e for e, _ in events] == ["business.artifact.completed"]
    save_canvas(db, c, {"mission": "Do more good", "vision": "World wins"}, expected_version=3)
    assert [e for e, _ in events] == ["business.artifact.completed"]  # not re-emitted


def test_overview_is_read_only_and_covers_all_types(db):
    s = _startup(db)
    rows = overview(db, s)
    # overview appends 4 record-kind rows (persona, revenue_stream, competitor,
    # pricing) after the canvas rows -- see app/services/business/service.py::overview.
    # Exact-set assertion re-catches duplicate/stray/misspelled rows.
    assert {r["type"] for r in rows} == {t.value for t in CanvasType} | {
        k.value for k in RecordKind
    }
    assert all(r["status"] == "start" for r in rows)
    # overview created no rows
    from app.db.models.business import BusinessCanvas, BusinessRecord

    assert db.query(BusinessCanvas).filter_by(startup_id=s.id).count() == 0
    assert db.query(BusinessRecord).filter_by(startup_id=s.id).count() == 0
