import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models.enums import MembershipRole
from app.db.models.sales import Deal, PipelineStage, SalesSettings
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform import events as events_mod
from app.schemas.sales import DealCreate
from app.services.sales import deals as deals_svc
from app.services.sales import stages as stages_svc
from app.services.sales.activities import touch_deal
from app.services.sales.stall import notify_stalled
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _ws(db):
    u = create_user(db, email_verified_at=NOW)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    return u, s, stages


def _deal(db, u, s, stages, *, days_ago, stage="Qualified", owner=None):
    d = deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=u.id,
        data=DealCreate(name="Acme renewal", stage_id=stages[stage].id, owner_id=owner),
    )
    d.last_activity_at = NOW - timedelta(days=days_ago)
    db.flush()
    return d


def _capture(monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    return published


def test_notify_publishes_payload_and_marks_deal(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    d = _deal(db, u, s, stages, days_ago=15, owner=u.id)

    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is True

    assert d.stalled_notified_at == NOW
    [(event, payload)] = published
    assert event == "sales.deal.stalled"
    assert payload == {
        "startup_id": str(s.id),
        "deal_id": str(d.id),
        "name": "Acme renewal",
        "days": 15,
        "owner_id": str(u.id),
        "href": f"/sales?deal={d.id}&followup=1",
    }


def test_second_call_publishes_nothing(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    d = _deal(db, u, s, stages, days_ago=20)
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is True
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is False
    assert len(published) == 1


def test_skips_when_activity_landed_after_scan(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    d = _deal(db, u, s, stages, days_ago=20)
    touch_deal(db, d, at=NOW - timedelta(days=1))  # an activity the scan did not see
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is False
    assert published == [] and d.stalled_notified_at is None


def test_threshold_is_the_workspace_stall_days(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    db.add(SalesSettings(startup_id=s.id, stall_days=30))
    db.flush()
    d = _deal(db, u, s, stages, days_ago=20)
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is False
    d.last_activity_at = NOW - timedelta(days=31)
    db.flush()
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is True
    assert published[0][1]["days"] == 31


def test_skips_when_deal_moved_to_closed_or_stage_archived(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    won = _deal(db, u, s, stages, days_ago=30)
    won.stage_id = stages["Closed Won"].id
    arch = _deal(db, u, s, stages, days_ago=30, stage="Proposal")
    stages["Proposal"].archived_at = NOW
    db.flush()
    assert notify_stalled(db, deal_id=won.id, startup_id=s.id, now=NOW) is False
    assert notify_stalled(db, deal_id=arch.id, startup_id=s.id, now=NOW) is False
    assert published == []


def test_wrong_workspace_in_payload_is_ignored(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    d = _deal(db, u, s, stages, days_ago=30)
    _u2, other, _ = _ws(db)
    assert notify_stalled(db, deal_id=d.id, startup_id=other.id, now=NOW) is False
    assert published == [] and d.stalled_notified_at is None


def test_unknown_deal_is_ignored(db, monkeypatch):
    published = _capture(monkeypatch)
    _u, s, _stages = _ws(db)
    assert notify_stalled(db, deal_id=uuid.uuid4(), startup_id=s.id, now=NOW) is False
    assert published == []


def test_activity_ends_the_episode_so_it_can_notify_again(db, monkeypatch):
    published = _capture(monkeypatch)
    u, s, stages = _ws(db)
    d = _deal(db, u, s, stages, days_ago=20)
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW) is True
    touch_deal(db, d, at=NOW)
    assert d.stalled_notified_at is None
    later = NOW + timedelta(days=15)
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=later) is True
    assert len(published) == 2


def test_two_sessions_racing_notify_once(engine, monkeypatch):
    """A true race on committed rows: the second worker blocks on the row lock until the first
    commits, then sees `stalled_notified_at` set and publishes nothing."""
    published = _capture(monkeypatch)
    with Session(engine) as setup:
        u, s, stages = _ws(setup)
        d = _deal(setup, u, s, stages, days_ago=20)
        ids = (d.id, s.id, u.id)
        setup.commit()
    deal_id, startup_id, user_id = ids
    first_holds_lock = threading.Event()
    release_first = threading.Event()
    results: dict[str, bool] = {}

    def first() -> None:
        with Session(engine) as sess:
            results["first"] = notify_stalled(sess, deal_id=deal_id, startup_id=startup_id, now=NOW)
            first_holds_lock.set()
            release_first.wait(10)
            sess.commit()

    def second() -> None:
        first_holds_lock.wait(10)
        with Session(engine) as sess:
            results["second"] = notify_stalled(
                sess, deal_id=deal_id, startup_id=startup_id, now=NOW
            )
            sess.commit()

    try:
        t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
        t1.start()
        t2.start()
        first_holds_lock.wait(10)
        t2.join(timeout=1.0)
        assert t2.is_alive(), "second worker should be blocked on the row lock"
        release_first.set()
        t1.join(10)
        t2.join(10)
        assert results == {"first": True, "second": False}
        assert len(published) == 1
    finally:
        release_first.set()
        with Session(engine) as cleanup:
            cleanup.query(Deal).filter_by(id=deal_id).delete()
            cleanup.query(PipelineStage).filter_by(startup_id=startup_id).delete()
            cleanup.query(SalesSettings).filter_by(startup_id=startup_id).delete()
            cleanup.commit()
            for model, pk in ((Startup, startup_id), (User, user_id)):
                row = cleanup.get(model, pk)
                if row is not None:
                    cleanup.delete(row)
            cleanup.commit()
