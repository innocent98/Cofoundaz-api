import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models.activity import ActivityLog
from app.db.models.enums import ActivityKind, MembershipRole
from app.schemas.sales import ActivityCreate, ActivityUpdate, DealCreate
from app.services.sales import activities as svc
from app.services.sales import deals as deals_svc
from app.services.sales import stages as stages_svc
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    stages_svc.ensure_stages(db, s.id)
    return u, s


def _deal(db, u, s):
    return deals_svc.create_deal(db, startup_id=s.id, actor_id=u.id, data=DealCreate(name="D"))


def _mk(db, u, s, **kw):
    return svc.create_activity(
        db, s.id, ActivityCreate(kind=ActivityKind.note, body="hi", **kw), author_id=u.id
    )


def _actions(db, startup_id):
    return set(db.scalars(select(ActivityLog.action).where(ActivityLog.startup_id == startup_id)))


def test_requires_a_target(db):
    u, s = _ws(db)
    with pytest.raises(AppError) as ei:
        _mk(db, u, s)
    assert ei.value.http_status == 422


def test_foreign_deal_rejected(db):
    u, s = _ws(db)
    ou, os_ = _ws(db)
    foreign = _deal(db, ou, os_)
    with pytest.raises(AppError) as ei:
        _mk(db, u, s, deal_id=foreign.id)
    assert ei.value.http_status == 422
    with pytest.raises(AppError):
        _mk(db, u, s, account_id=uuid.uuid4())
    with pytest.raises(AppError):
        _mk(db, u, s, contact_id=uuid.uuid4())


def test_create_on_deal_touches_and_clears_stall(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    d.last_activity_at = datetime.now(UTC) - timedelta(days=30)
    d.stalled_notified_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()
    row = _mk(db, u, s, deal_id=d.id)
    assert row.author_id == u.id
    assert d.stalled_notified_at is None
    assert d.last_activity_at > datetime.now(UTC) - timedelta(minutes=1)
    a = db.scalar(select(ActivityLog).where(ActivityLog.action == "sales.activity.created"))
    assert a.entity_type == "sales_activity" and a.entity_id == row.id


def test_future_occurred_at_caps_touch_at_now(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    _mk(db, u, s, deal_id=d.id, occurred_at=datetime.now(UTC) + timedelta(days=5))
    assert d.last_activity_at <= datetime.now(UTC)


def test_past_occurred_at_moves_a_staler_deal_forward(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    d.last_activity_at = datetime.now(UTC) - timedelta(days=30)
    db.flush()
    past = datetime.now(UTC) - timedelta(days=3)
    _mk(db, u, s, deal_id=d.id, occurred_at=past)
    assert d.last_activity_at == past


def test_past_occurred_at_never_moves_last_activity_backwards(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    before = d.last_activity_at
    _mk(db, u, s, deal_id=d.id, occurred_at=datetime.now(UTC) - timedelta(days=3))
    assert d.last_activity_at == before


def test_edit_and_delete_never_touch_deal(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    row = _mk(db, u, s, deal_id=d.id)
    old = datetime.now(UTC) - timedelta(days=30)
    d.last_activity_at = old
    d.stalled_notified_at = datetime.now(UTC)
    db.flush()
    svc.update_activity(db, s.id, row.id, ActivityUpdate(body="new"), actor_id=u.id)
    assert d.last_activity_at == old and d.stalled_notified_at is not None
    svc.delete_activity(db, s.id, row.id, actor_id=u.id)
    assert d.last_activity_at == old and d.stalled_notified_at is not None
    assert {"sales.activity.updated", "sales.activity.deleted"} <= _actions(db, s.id)


def test_non_author_forbidden(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    row = _mk(db, u, s, deal_id=d.id)
    other = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, other, s, role=MembershipRole.team_member)
    db.flush()
    with pytest.raises(AppError) as ei:
        svc.update_activity(db, s.id, row.id, ActivityUpdate(body="x"), actor_id=other.id)
    assert ei.value.http_status == 403
    with pytest.raises(AppError) as ei:
        svc.delete_activity(db, s.id, row.id, actor_id=other.id)
    assert ei.value.http_status == 403


def test_list_requires_filter_and_orders_newest_first(db):
    u, s = _ws(db)
    d = _deal(db, u, s)
    with pytest.raises(AppError) as ei:
        svc.list_activities(
            db, s.id, account_id=None, contact_id=None, deal_id=None, limit=10, offset=0
        )
    assert ei.value.http_status == 422
    now = datetime.now(UTC)
    old = _mk(db, u, s, deal_id=d.id, occurred_at=now - timedelta(days=2))
    new = _mk(db, u, s, deal_id=d.id, occurred_at=now - timedelta(days=1))
    rows, total = svc.list_activities(
        db, s.id, account_id=None, contact_id=None, deal_id=d.id, limit=10, offset=0
    )
    assert total == 2 and [r.id for r in rows] == [new.id, old.id]
