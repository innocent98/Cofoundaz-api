import uuid

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import CanvasType, RecordKind, SuggestionOp, SuggestionStatus
from app.db.models.membership import Membership
from app.services.business.canvas_defs import empty_blocks
from app.services.business.records import create_record
from app.services.business.suggestions import (
    create_suggestion,
    list_suggestions,
    serialize_suggestion,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db, role=None):
    from app.db.models.enums import MembershipRole

    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s, role=role or MembershipRole.business_consultant)
    db.flush()
    return u, s, m


def test_create_canvas_update_captures_base_version(db):
    _u, _s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "business_model"},
        {"blocks": {"key_partners": ["Acme"]}},
        "tighten this",
    )
    assert sug.status == SuggestionStatus.pending
    assert sug.base_version == 1  # lazily-created canvas starts at version 1
    assert sug.author_id == m.user_id


def test_create_canvas_update_bad_block_422(db):
    _u, _s, m = _ctx(db)
    with pytest.raises(AppError) as e:
        create_suggestion(
            db,
            m,
            SuggestionOp.canvas_update,
            {"canvas_type": "business_model"},
            {"blocks": {"not_a_block": "x"}},
            None,
        )
    assert e.value.http_status == 422


def test_create_record_update_unknown_target_404(db):
    _u, _s, m = _ctx(db)
    with pytest.raises(NotFound):
        create_suggestion(
            db,
            m,
            SuggestionOp.record_update,
            {"kind": "competitor", "record_id": str(uuid.uuid4())},
            {"data": {"name": "X"}},
            None,
        )


def test_create_record_create_bad_data_422(db):
    _u, _s, m = _ctx(db)
    with pytest.raises(AppError) as e:
        create_suggestion(
            db,
            m,
            SuggestionOp.record_create,
            {"kind": "persona"},
            {"data": {"goals": "no"}},
            None,
        )
    assert e.value.http_status == 422


def test_serialize_includes_current_for_record_update(db):
    _u, s, m = _ctx(db)
    rec = create_record(db, s, RecordKind.competitor, {"name": "Acme"})
    db.flush()
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.record_update,
        {"kind": "competitor", "record_id": str(rec.id)},
        {"data": {"name": "Acme2"}},
        None,
    )
    out = serialize_suggestion(db, sug)
    assert out["current"]["data"]["name"] == "Acme"
    assert out["payload"]["data"]["name"] == "Acme2"
    assert out["author"]["id"] == str(m.user_id)


def test_serialize_includes_current_for_canvas_update(db):
    _u, _s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "swot"},
        {"blocks": {}},
        None,
    )
    out = serialize_suggestion(db, sug)
    # get_or_create_canvas lazily creates the row (version=1, empty blocks) as a
    # side effect of create_suggestion capturing base_version -- current reflects
    # that live row, not the (empty) suggestion payload.
    assert out["current"]["blocks"] == empty_blocks(CanvasType.swot)
    assert out["current"]["version"] == 1


def test_serialize_current_is_none_for_record_create(db):
    _u, _s, m = _ctx(db)
    sug = create_suggestion(
        db,
        m,
        SuggestionOp.record_create,
        {"kind": "persona"},
        {"data": {"name": "P1"}},
        None,
    )
    out = serialize_suggestion(db, sug)
    assert out["current"] is None


def test_list_filters_by_status(db):
    _u, _s, m = _ctx(db)
    create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "swot"},
        {"blocks": {}},
        None,
    )
    startup = db.query(Membership).filter_by(id=m.id).one().startup_id
    from app.db.models.startup import Startup

    s_obj = db.query(Startup).filter_by(id=startup).one()
    assert len(list_suggestions(db, s_obj, SuggestionStatus.pending)) == 1
    assert list_suggestions(db, s_obj, SuggestionStatus.approved) == []
