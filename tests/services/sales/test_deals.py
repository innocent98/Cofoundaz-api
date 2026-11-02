import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import AppError, NotFound
from app.db.models.activity import ActivityLog
from app.db.models.enums import ClosedReason, MembershipRole
from app.db.models.sales import Deal, DealStageChange, SalesAccount, SalesContact
from app.platform.events import event_bus
from app.schemas.sales import DealCreate, DealUpdate
from app.services.sales import deals as svc
from app.services.sales import stages as stages_svc
from tests.factories import create_membership, create_startup, create_user


def _ws(db, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=role)
    db.flush()
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    return u, s, stages


def _fields(exc: AppError) -> list[str]:
    return [e["field"] for e in exc.field_errors]


def _mk(db, u, s, **kw):
    return svc.create_deal(
        db, startup_id=s.id, actor_id=u.id, data=DealCreate(name=kw.pop("name", "Acme"), **kw)
    )


def _actions(db, startup_id):
    return set(db.scalars(select(ActivityLog.action).where(ActivityLog.startup_id == startup_id)))


def _history(db, deal):
    return list(
        db.scalars(
            select(DealStageChange)
            .where(DealStageChange.deal_id == deal.id)
            .order_by(DealStageChange.changed_at, DealStageChange.id)
        )
    )


def _events(before, name):
    return [p for n, p in event_bus.published[before:] if n == name]


def test_create_defaults_to_first_stage_with_initial_history(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s, value_minor=500_000, next_step="Call", next_step_due=date(2030, 1, 1))
    assert d.stage_id == st["New"].id
    assert d.currency == "NGN" and d.closed_at is None
    assert d.last_activity_at is not None
    h = _history(db, d)
    assert len(h) == 1 and h[0].from_stage_id is None and h[0].to_stage_id == st["New"].id
    assert h[0].changed_by == u.id
    assert "sales.deal.created" in _actions(db, s.id)
    row = db.scalar(select(ActivityLog).where(ActivityLog.action == "sales.deal.created"))
    assert row.entity_type == "deal" and row.entity_id == d.id


def test_create_directly_into_won_sets_closed_at(db):
    u, s, st = _ws(db)
    before = len(event_bus.published)
    d = _mk(db, u, s, stage_id=st["Closed Won"].id)
    assert d.closed_at is not None
    assert len(_events(before, "sales.deal.won")) == 1


def test_create_into_lost_rejected(db):
    u, s, st = _ws(db)
    with pytest.raises(AppError) as ei:
        _mk(db, u, s, stage_id=st["Closed Lost"].id)
    assert _fields(ei.value) == ["stage_id"]


def test_create_rejects_foreign_stage_account_contact_owner(db):
    u, s, st = _ws(db)
    u2, s2, st2 = _ws(db)
    acc2 = SalesAccount(startup_id=s2.id, name="Other")
    db.add(acc2)
    db.flush()
    con2 = SalesContact(startup_id=s2.id, first_name="Z")
    db.add(con2)
    db.flush()
    for kw, field in [
        ({"stage_id": st2["New"].id}, "stage_id"),
        ({"account_id": acc2.id}, "account_id"),
        ({"contact_id": con2.id}, "contact_id"),
        ({"owner_id": u2.id}, "owner_id"),
    ]:
        with pytest.raises(AppError) as ei:
            _mk(db, u, s, **kw)
        assert _fields(ei.value) == [field]
    # nothing leaked into the follow-up read
    assert db.scalar(select(Deal.id).where(Deal.startup_id == s.id)) is None


def test_create_rejects_archived_stage(db):
    u, s, st = _ws(db)
    st["Proposal"].archived_at = datetime.now(UTC)
    db.flush()
    with pytest.raises(AppError) as ei:
        _mk(db, u, s, stage_id=st["Proposal"].id)
    assert _fields(ei.value) == ["stage_id"]


def test_contact_must_belong_to_account(db):
    u, s, _st = _ws(db)
    a1 = SalesAccount(startup_id=s.id, name="A1")
    a2 = SalesAccount(startup_id=s.id, name="A2")
    db.add_all([a1, a2])
    db.flush()
    c = SalesContact(startup_id=s.id, first_name="C", account_id=a1.id)
    db.add(c)
    db.flush()
    with pytest.raises(AppError) as ei:
        _mk(db, u, s, account_id=a2.id, contact_id=c.id)
    assert _fields(ei.value) == ["contact_id"]
    ok = _mk(db, u, s, account_id=a1.id, contact_id=c.id)
    assert ok.contact_id == c.id
    # contact alone is allowed
    assert _mk(db, u, s, name="solo", contact_id=c.id).account_id is None
    # moving the deal to another account while keeping the contact is rejected
    with pytest.raises(AppError):
        svc.update_deal(
            db,
            startup_id=s.id,
            deal_id=ok.id,
            actor_id=u.id,
            data=DealUpdate(account_id=a2.id),
        )


def test_move_stage_writes_history_and_touches(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s)
    old = datetime.now(UTC) - timedelta(days=20)
    d.last_activity_at = old
    d.stalled_notified_at = old
    db.flush()
    before = len(event_bus.published)
    d = svc.move_stage(
        db, startup_id=s.id, deal_id=d.id, stage_id=st["Qualified"].id, actor_id=u.id
    )
    assert d.stage_id == st["Qualified"].id
    assert d.last_activity_at > old and d.stalled_notified_at is None
    h = _history(db, d)
    assert len(h) == 2 and h[1].from_stage_id == st["New"].id
    assert h[1].to_stage_id == st["Qualified"].id
    assert "sales.deal.stage_changed" in _actions(db, s.id)
    assert len(_events(before, "sales.deal.stage_changed")) == 1
    assert _events(before, "sales.deal.won") == []


def test_move_same_stage_is_noop(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s)
    stamp = d.last_activity_at
    before = len(event_bus.published)
    svc.move_stage(db, startup_id=s.id, deal_id=d.id, stage_id=st["New"].id, actor_id=u.id)
    assert len(_history(db, d)) == 1
    assert d.last_activity_at == stamp
    assert event_bus.published[before:] == []


def test_move_to_lost_requires_reason_then_stores(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s)
    with pytest.raises(AppError) as ei:
        svc.move_stage(
            db, startup_id=s.id, deal_id=d.id, stage_id=st["Closed Lost"].id, actor_id=u.id
        )
    assert _fields(ei.value) == ["closed_reason"]
    assert d.stage_id == st["New"].id and len(_history(db, d)) == 1
    before = len(event_bus.published)
    d = svc.move_stage(
        db,
        startup_id=s.id,
        deal_id=d.id,
        stage_id=st["Closed Lost"].id,
        actor_id=u.id,
        closed_reason=ClosedReason.price,
        closed_note="too dear",
    )
    assert d.closed_reason == ClosedReason.price and d.closed_note == "too dear"
    assert d.closed_at is not None
    assert len(_events(before, "sales.deal.lost")) == 1
    assert "sales.deal.lost" in _actions(db, s.id)


def test_won_publishes_event_with_payload_and_activity(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s, name="Big one")
    before = len(event_bus.published)
    d = svc.move_stage(
        db, startup_id=s.id, deal_id=d.id, stage_id=st["Closed Won"].id, actor_id=u.id
    )
    [p] = _events(before, "sales.deal.won")
    assert p["startup_id"] == str(s.id) and p["deal_id"] == str(d.id)
    assert p["name"] == "Big one" and p["actor_id"] == str(u.id)
    assert p["href"] == f"/sales?deal={d.id}"
    assert d.closed_at is not None and d.closed_reason is None
    assert "sales.deal.won" in _actions(db, s.id)


def test_reopen_clears_closed_fields(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s)
    svc.move_stage(
        db,
        startup_id=s.id,
        deal_id=d.id,
        stage_id=st["Closed Lost"].id,
        actor_id=u.id,
        closed_reason=ClosedReason.timing,
        closed_note="later",
    )
    d = svc.move_stage(
        db, startup_id=s.id, deal_id=d.id, stage_id=st["Contacted"].id, actor_id=u.id
    )
    assert d.closed_at is None and d.closed_reason is None and d.closed_note is None
    svc.move_stage(db, startup_id=s.id, deal_id=d.id, stage_id=st["Closed Won"].id, actor_id=u.id)
    d = svc.move_stage(db, startup_id=s.id, deal_id=d.id, stage_id=st["New"].id, actor_id=u.id)
    assert d.closed_at is None


def test_move_rejects_foreign_and_archived_stage(db):
    u, s, st = _ws(db)
    _u2, _s2, st2 = _ws(db)
    d = _mk(db, u, s)
    with pytest.raises(AppError) as ei:
        svc.move_stage(db, startup_id=s.id, deal_id=d.id, stage_id=st2["New"].id, actor_id=u.id)
    assert _fields(ei.value) == ["stage_id"]
    st["Proposal"].archived_at = datetime.now(UTC)
    db.flush()
    with pytest.raises(AppError):
        svc.move_stage(db, startup_id=s.id, deal_id=d.id, stage_id=st["Proposal"].id, actor_id=u.id)


def test_move_foreign_deal_not_found(db):
    u, s, st = _ws(db)
    u2, s2, _st2 = _ws(db)
    d2 = _mk(db, u2, s2)
    with pytest.raises(NotFound):
        svc.move_stage(db, startup_id=s.id, deal_id=d2.id, stage_id=st["New"].id, actor_id=u.id)


def test_next_step_edit_touches_but_name_edit_does_not(db):
    u, s, _st = _ws(db)
    d = _mk(db, u, s)
    old = datetime.now(UTC) - timedelta(days=30)
    d.last_activity_at = old
    d.stalled_notified_at = old
    db.flush()
    svc.update_deal(
        db, startup_id=s.id, deal_id=d.id, actor_id=u.id, data=DealUpdate(name="Renamed")
    )
    assert d.last_activity_at == old and d.stalled_notified_at == old
    svc.update_deal(
        db, startup_id=s.id, deal_id=d.id, actor_id=u.id, data=DealUpdate(next_step="Send deck")
    )
    assert d.last_activity_at > old and d.stalled_notified_at is None
    assert "sales.deal.updated" in _actions(db, s.id)


def test_update_rejects_stage_change_and_nulls(db):
    u, s, st = _ws(db)
    d = _mk(db, u, s)
    with pytest.raises(AppError) as ei:
        svc.update_deal(
            db,
            startup_id=s.id,
            deal_id=d.id,
            actor_id=u.id,
            data=DealUpdate(stage_id=st["Qualified"].id),
        )
    assert ei.value.message == "Use the stage endpoint."
    with pytest.raises(AppError):
        svc.update_deal(
            db, startup_id=s.id, deal_id=d.id, actor_id=u.id, data=DealUpdate(name=None)
        )
    assert d.stage_id == st["New"].id


def test_update_rejects_foreign_owner_and_account(db):
    u, s, _st = _ws(db)
    u2, s2, _ = _ws(db)
    acc2 = SalesAccount(startup_id=s2.id, name="Other")
    db.add(acc2)
    db.flush()
    d = _mk(db, u, s)
    with pytest.raises(AppError):
        svc.update_deal(
            db, startup_id=s.id, deal_id=d.id, actor_id=u.id, data=DealUpdate(owner_id=u2.id)
        )
    with pytest.raises(AppError):
        svc.update_deal(
            db, startup_id=s.id, deal_id=d.id, actor_id=u.id, data=DealUpdate(account_id=acc2.id)
        )


def test_delete_deal_removes_history_and_logs(db):
    u, s, _st = _ws(db)
    d = _mk(db, u, s)
    did = d.id
    svc.delete_deal(db, startup_id=s.id, deal_id=did, actor_id=u.id)
    assert db.get(Deal, did) is None
    assert db.scalar(select(DealStageChange.id).where(DealStageChange.deal_id == did)) is None
    assert "sales.deal.deleted" in _actions(db, s.id)
    with pytest.raises(NotFound):
        svc.delete_deal(db, startup_id=s.id, deal_id=did, actor_id=u.id)


def test_items_names_and_stall_flags(db):
    u, s, st = _ws(db)
    acc = SalesAccount(startup_id=s.id, name="Acme Ltd")
    db.add(acc)
    db.flush()
    con = SalesContact(startup_id=s.id, first_name="Ada", last_name="Obi", account_id=acc.id)
    db.add(con)
    db.flush()
    stale = _mk(db, u, s, name="stale", account_id=acc.id, contact_id=con.id)
    stale.last_activity_at = datetime.now(UTC) - timedelta(days=15)
    fresh = _mk(db, u, s, name="fresh")
    won = _mk(db, u, s, name="won", stage_id=st["Closed Won"].id)
    won.last_activity_at = datetime.now(UTC) - timedelta(days=40)
    db.flush()
    items = {i.name: i for i in svc.deal_items(db, s.id, [stale, fresh, won])}
    assert items["stale"].account_name == "Acme Ltd" and items["stale"].contact_name == "Ada Obi"
    assert items["stale"].is_stalled and items["stale"].days_inactive == 15
    assert not items["fresh"].is_stalled and items["fresh"].days_inactive == 0
    assert not items["won"].is_stalled  # closed deals never stall
    stages_svc.update_settings(
        db,
        s.id,
        __import__("app.schemas.sales", fromlist=["SettingsUpdate"]).SettingsUpdate(stall_days=30),
    )
    items = {i.name: i for i in svc.deal_items(db, s.id, [stale])}
    assert not items["stale"].is_stalled


def test_list_filters_and_scoping(db):
    u, s, st = _ws(db)
    u2, s2, _ = _ws(db)
    _mk(db, u, s, name="Alpha Deal")
    _mk(db, u, s, name="Beta", stage_id=st["Closed Won"].id)
    _mk(db, u2, s2, name="Alpha Foreign")
    kw = {"stage_id": None, "account_id": None, "owner_id": None, "q": None, "open_only": False}
    items, total = svc.list_deals(db, s.id, limit=50, offset=0, **kw)
    assert total == 2 and {i.name for i in items} == {"Alpha Deal", "Beta"}
    items, total = svc.list_deals(db, s.id, limit=50, offset=0, **{**kw, "q": "alpha"})
    assert [i.name for i in items] == ["Alpha Deal"]
    items, total = svc.list_deals(db, s.id, limit=50, offset=0, **{**kw, "open_only": True})
    assert [i.name for i in items] == ["Alpha Deal"]
    items, total = svc.list_deals(
        db, s.id, limit=50, offset=0, **{**kw, "stage_id": st["Closed Won"].id}
    )
    assert [i.name for i in items] == ["Beta"]
    items, total = svc.list_deals(db, s.id, limit=1, offset=1, **kw)
    assert total == 2 and len(items) == 1


def test_account_deal_summary(db):
    u, s, st = _ws(db)
    acc = SalesAccount(startup_id=s.id, name="Acme")
    db.add(acc)
    db.flush()
    _mk(db, u, s, name="open1", account_id=acc.id, value_minor=100)
    _mk(db, u, s, name="won1", account_id=acc.id, value_minor=700, stage_id=st["Closed Won"].id)
    lost = _mk(db, u, s, name="lost1", account_id=acc.id, value_minor=50)
    svc.move_stage(
        db,
        startup_id=s.id,
        deal_id=lost.id,
        stage_id=st["Closed Lost"].id,
        actor_id=u.id,
        closed_reason=ClosedReason.other,
    )
    open_deals, won = svc.account_deal_summary(db, s.id, acc.id)
    assert [d.name for d in open_deals] == ["open1"] and won == 700


def test_stage_id_random_uuid_is_validation(db):
    u, s, _st = _ws(db)
    with pytest.raises(AppError):
        _mk(db, u, s, stage_id=uuid.uuid4())
