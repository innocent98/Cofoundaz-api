import pytest

from app.core.errors import CanvasVersionConflict, NotFound, SuggestionNotPending
from app.db.models.enums import (
    CanvasType,
    MembershipRole,
    RecordKind,
    SuggestionOp,
    SuggestionStatus,
)
from app.services.business.records import create_record
from app.services.business.service import get_or_create_canvas, save_canvas
from app.services.business.suggestions import (
    approve_suggestion,
    create_suggestion,
    reject_suggestion,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s, m


def test_approve_canvas_update_applies_and_marks_approved(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "business_model"},
        {"blocks": {"key_partners": ["Acme"]}},
        None,
    )
    approve_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.approved
    assert sug.resolved_by_id == m.user_id and sug.resolved_at is not None
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    assert canvas.blocks["key_partners"] == ["Acme"]


def test_approve_record_create_inserts(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.record_create,
        {"kind": "persona"},
        {"data": {"name": "P1"}},
        None,
    )
    approve_suggestion(db, m, sug.id)
    from app.db.models.business import BusinessRecord

    assert db.query(BusinessRecord).filter_by(startup_id=s.id, kind=RecordKind.persona).count() == 1


def test_approve_record_delete_removes(db):
    _u, s, m = _ctx(db)
    rec = create_record(db, s, RecordKind.competitor, {"name": "Gone"})
    db.flush()
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.record_delete,
        {"kind": "competitor", "record_id": str(rec.id)},
        None,
        None,
    )
    approve_suggestion(db, m, sug.id)
    from app.db.models.business import BusinessRecord

    assert db.query(BusinessRecord).filter_by(id=rec.id).first() is None


def test_reject_leaves_target_untouched(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "swot"},
        {"blocks": {"strengths": ["x"]}},
        None,
    )
    reject_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.rejected
    canvas = get_or_create_canvas(db, s, CanvasType.swot)
    assert canvas.blocks.get("strengths") in (None, [], "")


def test_approve_twice_409(db):
    _u, _s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "lean"},
        {"blocks": {}},
        None,
    )
    approve_suggestion(db, m, sug.id)
    with pytest.raises(SuggestionNotPending):
        approve_suggestion(db, m, sug.id)


def test_approve_stale_canvas_conflict_leaves_pending(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "business_model"},
        {"blocks": {"key_partners": ["A"]}},
        None,
    )  # base_version captured = 1
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    save_canvas(db, canvas, {"key_partners": ["moved"]}, 1)  # bumps to version 2
    with pytest.raises(CanvasVersionConflict):
        approve_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.pending  # still open


def test_approve_deleted_target_404_leaves_pending(db):
    _u, s, m = _ctx(db)
    rec = create_record(db, s, RecordKind.competitor, {"name": "X"})
    db.flush()
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.record_update,
        {"kind": "competitor", "record_id": str(rec.id)},
        {"data": {"name": "Y"}},
        None,
    )
    db.delete(rec)
    db.flush()
    with pytest.raises(NotFound):
        approve_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.pending
