import uuid
from datetime import date

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import InterviewVerdict, RiskLevel
from app.services.validation.service import (
    create_assumption,
    create_interview,
    get_interview,
    list_interviews,
    serialize_interview,
    update_interview,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, s, **kw):
    kw.setdefault("interviewee", "Ada")
    kw.setdefault("held_on", date(2026, 9, 20))
    kw.setdefault("verdict", InterviewVerdict.supports)
    return create_interview(db, s.id, **kw)


def test_defaults_are_empty_not_null(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    assert row.notes == "" and row.key_quotes == [] and row.assumption_ids == []
    assert row.segment is None


def test_list_is_most_recent_first_and_filters(db):
    _u, s = _ctx(db)
    assumption = create_assumption(db, s.id, statement="Pay", risk=RiskLevel.high)
    _new(db, s, interviewee="Older", held_on=date(2026, 9, 1), segment="fintech")
    newer = _new(
        db,
        s,
        interviewee="Newer",
        held_on=date(2026, 9, 18),
        segment="retail",
        verdict=InterviewVerdict.contradicts,
        assumption_ids=[str(assumption.id)],
    )
    assert [i.id for i in list_interviews(db, s.id)][0] == newer.id
    assert len(list_interviews(db, s.id, segment="fintech")) == 1
    assert len(list_interviews(db, s.id, verdict=InterviewVerdict.contradicts)) == 1
    assert len(list_interviews(db, s.id, assumption_id=str(assumption.id))) == 1


def test_quotes_must_be_a_list_of_text(db):
    _u, s = _ctx(db)
    with pytest.raises(AppError) as exc:
        _new(db, s, key_quotes="just one quote")
    assert exc.value.http_status == 422
    with pytest.raises(AppError):
        _new(db, s, key_quotes=["fine", 7])
    row = _new(db, s, key_quotes=["  I would pay  "])
    assert row.key_quotes == ["I would pay"]


def test_cross_workspace_is_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = _new(db, s)
    with pytest.raises(NotFound):
        get_interview(db, other.id, mine.id)
    with pytest.raises(NotFound):
        get_interview(db, s.id, uuid.uuid4())


def test_update_changes_only_what_is_given(db):
    _u, s = _ctx(db)
    row = _new(db, s, notes="First pass")
    update_interview(db, row, verdict=InterviewVerdict.neutral, segment="fintech")
    assert row.verdict == InterviewVerdict.neutral and row.segment == "fintech"
    assert row.notes == "First pass" and row.interviewee == "Ada"


def test_serialize_shape(db):
    _u, s = _ctx(db)
    row = _new(db, s, notes="Talked pricing", key_quotes=["Take my money"])
    out = serialize_interview(row)
    assert out["id"] == str(row.id) and out["interviewee"] == "Ada"
    assert out["held_on"] == "2026-09-20" and out["verdict"] == "supports"
    assert out["notes"] == "Talked pricing" and out["key_quotes"] == ["Take my money"]
    assert out["segment"] is None and out["assumption_ids"] == []
