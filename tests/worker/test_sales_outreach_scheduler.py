import importlib
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.services.notifications import registry
from app.services.notifications.audience import AUDIENCE
from app.services.sales import suppression as suppression_svc
from app.worker.runner import JOB_HANDLERS
from app.worker.scheduler import (
    SCHED_SALES_BOUNCES,
    SCHED_SALES_SEND,
    _due_sales_outreach,
    scheduler_tick,
)
from tests.factories import create_membership, create_startup, create_user
from tests.sales_outreach_helpers import NOW, configure


def _types(db):
    return sorted(j.type for j in db.query(Job).filter(Job.type.like("scheduled.sales.outreach%")))


def test_nothing_is_scheduled_while_the_mailbox_is_unconfigured(db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    assert _due_sales_outreach(db, NOW) == []
    scheduler_tick(db, now=NOW)
    assert _types(db) == []


def test_send_and_bounce_jobs_fire_once_per_five_minute_bucket(db, monkeypatch):
    configure(monkeypatch, provider="smtp")
    scheduler_tick(db, now=NOW)
    assert _types(db) == [SCHED_SALES_BOUNCES, SCHED_SALES_SEND]
    scheduler_tick(db, now=NOW + timedelta(minutes=2))  # same bucket: claimed already
    assert len(_types(db)) == 2
    scheduler_tick(db, now=NOW + timedelta(minutes=6))
    assert len(_types(db)) == 4


def test_console_provider_schedules_sends_but_no_bounce_poll(db, monkeypatch):
    configure(monkeypatch, provider="console")
    scheduler_tick(db, now=NOW)
    assert _types(db) == [SCHED_SALES_SEND]


def test_handlers_are_registered():
    # Registration is an import side effect, and other tests clear the registry, so import afresh.
    sys.modules.pop("app.worker.handlers.sales_outreach", None)
    importlib.import_module("app.worker.handlers.sales_outreach")
    for t in (SCHED_SALES_SEND, SCHED_SALES_BOUNCES, "ai.sales.draft"):
        assert t in JOB_HANDLERS


def test_sending_paused_notifies_founders_only(db):
    bus = DispatchingEventBus()
    registry.register(bus)
    owner = create_user(db, email_verified_at=NOW)
    s = create_startup(db, owner=owner)
    users = {}
    for role in (MembershipRole.founder, MembershipRole.team_member, MembershipRole.mentor):
        u = owner if role == MembershipRole.founder else create_user(db)
        create_membership(db, u, s, role=role)
        users[role] = u
    db.flush()
    bus.publish(
        db,
        "sales.sending.paused",
        {"startup_id": str(s.id), "reason": "bounces", "href": "/sales/sequences"},
    )
    rows = db.query(Notification).filter(Notification.type == "sales.sending.paused").all()
    assert {r.user_id for r in rows} == {users[MembershipRole.founder].id}
    assert rows[0].title == "Sales emails paused: too many bounces"
    assert AUDIENCE["sales.sending.paused"] == frozenset({MembershipRole.founder})
    assert suppression_svc.AUTO_PAUSE_RATE == 0.10 and datetime.now(UTC)
