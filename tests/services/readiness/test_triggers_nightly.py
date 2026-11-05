from datetime import UTC, datetime
from types import SimpleNamespace

from app.db.models.enums import MembershipRole
from app.db.models.readiness import ReadinessScore
from app.platform.events import DispatchingEventBus
from app.services.readiness.triggers import REFRESH_EVENTS, register
from app.worker.handlers.scheduled import handle_health_nightly
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return s


def test_events_trigger_a_refresh_and_ignore_payloads_without_a_workspace(db):
    s = _ws(db)
    bus = DispatchingEventBus()
    register(bus)
    bus.publish(db, "document.created", {})
    assert db.query(ReadinessScore).filter_by(startup_id=s.id).count() == 0
    bus.publish(db, "document.created", {"startup_id": str(s.id)})
    row = db.query(ReadinessScore).filter_by(startup_id=s.id).one()
    assert row.trigger == "document.created"


def test_every_trigger_event_is_subscribed():
    bus = DispatchingEventBus()
    register(bus)
    assert set(REFRESH_EVENTS) <= set(bus._handlers)


def test_default_bus_registration_is_idempotent():
    register()
    register()


def test_nightly_pass_refreshes_readiness_too(db):
    s = _ws(db)
    handle_health_nightly(db, SimpleNamespace(payload={"startup_id": str(s.id)}))
    assert db.query(ReadinessScore).filter_by(startup_id=s.id).one().trigger == "nightly"


def test_a_readiness_failure_cannot_fail_the_health_nightly_job(db, monkeypatch, caplog):
    s = _ws(db)
    from app.services.readiness import service as svc

    def boom(*_a, **_k):
        raise RuntimeError("readiness exploded")

    monkeypatch.setattr(svc, "refresh_readiness", boom)
    handle_health_nightly(db, SimpleNamespace(payload={"startup_id": str(s.id)}))  # must not raise
    assert "nightly readiness refresh failed" in caplog.text
