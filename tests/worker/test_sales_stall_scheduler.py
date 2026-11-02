import uuid
from datetime import UTC, datetime, timedelta

from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.sales import SalesSettings
from app.platform import events as events_mod
from app.schemas.sales import DealCreate
from app.services.sales import deals as deals_svc
from app.services.sales import stages as stages_svc
from app.services.sales.activities import touch_deal
from app.worker import scheduler
from app.worker.scheduler import SCHED_SALES_STALL, scheduler_tick
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _ws(db):
    u = create_user(db, email_verified_at=NOW)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    return u, s, stages


def _deal(db, u, s, stages, *, days_ago, stage="Qualified", name="Acme"):
    d = deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=u.id,
        data=DealCreate(name=name, stage_id=stages[stage].id),
    )
    d.last_activity_at = NOW - timedelta(days=days_ago)
    db.flush()
    return d


def _stall_jobs(db, s):
    return db.query(Job).filter_by(type=SCHED_SALES_STALL, startup_id=s.id).all()


def test_14_days_found_13_not(db):
    u, s, stages = _ws(db)
    due14 = _deal(db, u, s, stages, days_ago=14, name="old")
    _deal(db, u, s, stages, days_ago=13, name="fresh")
    due = scheduler._due_sales_stalls(db, NOW)
    assert [d.scope_key for d in due] == [str(due14.id)]
    item = due[0]
    assert item.task_key == "sales.stall" and item.job_type == SCHED_SALES_STALL
    assert item.period_key == due14.last_activity_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")
    assert item.startup_id == s.id
    assert item.payload == {"startup_id": str(s.id), "deal_id": str(due14.id)}


def test_custom_stall_days(db):
    u, s, stages = _ws(db)
    db.add(SalesSettings(startup_id=s.id, stall_days=3))
    db.flush()
    d = _deal(db, u, s, stages, days_ago=3)
    _deal(db, u, s, stages, days_ago=2)
    assert [x.scope_key for x in scheduler._due_sales_stalls(db, NOW)] == [str(d.id)]


def test_closed_and_archived_stage_deals_not_found(db):
    u, s, stages = _ws(db)
    _deal(db, u, s, stages, days_ago=60, stage="Closed Won")
    lost = _deal(db, u, s, stages, days_ago=60)
    lost.stage_id = stages["Closed Lost"].id
    _deal(db, u, s, stages, days_ago=60, stage="Proposal")
    stages["Proposal"].archived_at = NOW
    db.flush()
    assert scheduler._due_sales_stalls(db, NOW) == []


def test_already_notified_and_deleted_workspace_not_found(db):
    u, s, stages = _ws(db)
    notified = _deal(db, u, s, stages, days_ago=60)
    notified.stalled_notified_at = NOW
    u2, s2, stages2 = _ws(db)
    _deal(db, u2, s2, stages2, days_ago=60)
    s2.deleted_at = NOW
    db.flush()
    assert scheduler._due_sales_stalls(db, NOW) == []


def test_batch_is_capped(db, monkeypatch):
    u, s, stages = _ws(db)
    monkeypatch.setattr(scheduler, "SALES_STALL_BATCH", 2)
    for i in range(3):
        _deal(db, u, s, stages, days_ago=30 + i, name=f"d{i}")
    assert len(scheduler._due_sales_stalls(db, NOW)) == 2


def test_tick_twice_enqueues_once(db):
    u, s, stages = _ws(db)
    _deal(db, u, s, stages, days_ago=20)
    scheduler_tick(db, now=NOW)
    scheduler_tick(db, now=NOW)
    assert len(_stall_jobs(db, s)) == 1


def test_new_episode_after_touch_enqueues_again(db):
    u, s, stages = _ws(db)
    d = _deal(db, u, s, stages, days_ago=20)
    scheduler_tick(db, now=NOW)
    assert len(_stall_jobs(db, s)) == 1
    touch_deal(db, d, at=NOW)
    scheduler_tick(db, now=NOW + timedelta(days=1))
    assert len(_stall_jobs(db, s)) == 1  # fresh activity: not stalled yet
    scheduler_tick(db, now=NOW + timedelta(days=14))
    assert len(_stall_jobs(db, s)) == 2  # new last_activity_at stamp -> new period key


def test_handler_notifies_once_and_ignores_foreign_workspace(db, monkeypatch):
    from app.worker.handlers import scheduled

    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u, s, stages = _ws(db)
    _u2, other, _ = _ws(db)
    d = _deal(db, u, s, stages, days_ago=400)

    def job(startup_id):
        return Job(
            type=SCHED_SALES_STALL,
            payload={"startup_id": str(startup_id), "deal_id": str(d.id)},
        )

    scheduled.handle_sales_stall(db, job(other.id))
    assert published == []
    scheduled.handle_sales_stall(db, job(s.id))
    scheduled.handle_sales_stall(db, job(s.id))
    assert [e for e, _ in published] == ["sales.deal.stalled"]
    assert uuid.UUID(published[0][1]["deal_id"]) == d.id
