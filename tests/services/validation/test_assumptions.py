import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.errors import NotFound
from app.db.models.enums import AssumptionStatus, ExperimentType, InterviewVerdict, RiskLevel
from app.db.models.validation import Experiment, Interview
from app.services.validation.service import (
    create_assumption,
    evidence_counts,
    get_assumption,
    list_assumptions,
    serialize_assumption,
    update_assumption,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, s, **kw):
    kw.setdefault("statement", "Founders will pay for this")
    kw.setdefault("risk", RiskLevel.high)
    return create_assumption(db, s.id, **kw)


def test_new_assumptions_start_untested(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    assert row.status == AssumptionStatus.untested and row.risk == RiskLevel.high


def test_list_is_newest_first_and_filters(db):
    _u, s = _ctx(db)
    first = _new(db, s, statement="First", risk=RiskLevel.low)
    second = _new(db, s, statement="Second", status=AssumptionStatus.testing)
    # Both rows are written inside one transaction, so the database stamps them with the same
    # time. Set them apart explicitly to exercise the ordering.
    first.created_at = datetime.now(UTC) - timedelta(hours=1)
    second.created_at = datetime.now(UTC)
    db.flush()
    assert [a.id for a in list_assumptions(db, s.id)][0] == second.id
    assert len(list_assumptions(db, s.id, status=AssumptionStatus.testing)) == 1
    assert len(list_assumptions(db, s.id, risk=RiskLevel.low)) == 1


def test_another_workspaces_assumption_is_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = _new(db, s)
    with pytest.raises(NotFound):
        get_assumption(db, other.id, mine.id)
    with pytest.raises(NotFound):
        get_assumption(db, s.id, uuid.uuid4())


def test_editing_statement_and_risk(db):
    u, s = _ctx(db)
    row = _new(db, s)
    update_assumption(db, row, actor_id=u.id, statement="  Reworded  ", risk=RiskLevel.medium)
    assert row.statement == "Reworded" and row.risk == RiskLevel.medium


def test_moving_into_validated_emits_exactly_one_event(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.validation.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    u, s = _ctx(db)
    row = _new(db, s)
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.validated)
    assert events == [
        (
            "validation.assumption.validated",
            {
                "startup_id": str(s.id),
                "assumption_id": str(row.id),
                "status": "validated",
                "actor_id": str(u.id),
            },
        )
    ]
    # Saving the same status again changes nothing and emits nothing.
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.validated)
    assert len(events) == 1


def test_invalidated_emits_its_own_event_and_other_moves_emit_nothing(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.validation.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    u, s = _ctx(db)
    row = _new(db, s)
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.testing)
    assert events == []
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.invalidated)
    assert [e for e, _p in events] == ["validation.assumption.invalidated"]
    # The board may drag a card back; that emits nothing.
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.testing)
    assert len(events) == 1


def test_evidence_count_is_derived_and_workspace_scoped(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    row = _new(db, s)
    db.add(
        Experiment(
            startup_id=s.id,
            name="Landing page",
            type=ExperimentType.smoke_test,
            assumption_ids=[str(row.id)],
        )
    )
    db.add(
        Interview(
            startup_id=s.id,
            interviewee="Ada",
            held_on=date(2026, 9, 20),
            verdict=InterviewVerdict.supports,
            assumption_ids=[str(row.id)],
        )
    )
    # A link from another workspace must not count.
    db.add(
        Experiment(
            startup_id=other.id,
            name="Theirs",
            type=ExperimentType.other,
            assumption_ids=[str(row.id)],
        )
    )
    db.flush()
    counts = evidence_counts(db, s.id, [row])
    assert counts[str(row.id)] == 2
    assert evidence_counts(db, s.id, []) == {}


def test_serialize_shape(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    out = serialize_assumption(row, 3)
    assert out["id"] == str(row.id)
    assert out["statement"] == "Founders will pay for this"
    assert out["risk"] == "high" and out["status"] == "untested"
    assert out["evidence_count"] == 3
    assert out["created_at"] and out["updated_at"]
