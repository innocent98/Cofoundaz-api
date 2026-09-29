from datetime import UTC, datetime

import pytest

from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.schemas.finance import TransactionCreate
from app.services.finance import runway as runway_svc
from app.services.finance import service as finance_svc
from app.services.notifications import registry
from tests.factories import create_membership, create_startup, create_user

EVENT = "finance.runway.low"


@pytest.fixture
def bus(monkeypatch):
    b = DispatchingEventBus()
    registry.register(b)
    monkeypatch.setattr(runway_svc, "event_bus", b)
    return b


def _startup(db):
    founder = create_user(db)
    startup = create_startup(db, owner=founder)
    create_membership(db, founder, startup, role=MembershipRole.founder)
    db.flush()
    return founder, startup


def _add(db, startup, amount, direction):
    finance_svc.create_transaction(
        db,
        startup_id=startup.id,
        data=TransactionCreate(
            date=datetime.now(UTC).date(),
            description="seed",
            amount_minor=amount,
            currency="NGN",
            direction=direction,
        ),
    )


def _fired(bus):
    return [p for e, p in bus.published if e == EVENT]


def test_fires_once_on_transition_rearms_on_recovery_and_refires(db, bus):
    founder, startup = _startup(db)

    # (a) burning with no cash left -> low -> fires once.
    _add(db, startup, 600_000, TransactionDirection.outflow)
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    row = runway_svc.get_or_default_settings(db, startup_id=startup.id)
    assert row is not None and row.alert_is_low is True
    assert row.alert_last_fired_at is not None
    fired = _fired(bus)
    assert len(fired) == 1
    assert fired[0]["startup_id"] == str(startup.id)
    assert fired[0]["monthly_burn"] == 200_000
    assert fired[0]["currency"] == "NGN"

    # (b) still low -> no new event.
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    assert len(_fired(bus)) == 1

    # (c) big inflow -> recovered -> no event, re-armed.
    _add(db, startup, 100_000_000, TransactionDirection.inflow)
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    db.refresh(row)
    assert row.alert_is_low is False
    assert len(_fired(bus)) == 1

    # (d) re-cross into low -> fires again.
    _add(db, startup, 400_000_000, TransactionDirection.outflow)
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    db.refresh(row)
    assert row.alert_is_low is True
    assert len(_fired(bus)) == 2


def test_healthy_startup_never_fires_and_row_stays_disarmed(db, bus):
    _founder, startup = _startup(db)
    _add(db, startup, 5_000_000, TransactionDirection.inflow)
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    row = runway_svc.get_or_default_settings(db, startup_id=startup.id)
    assert row is not None and row.alert_is_low is False
    assert _fired(bus) == []


def test_transition_creates_in_app_notification_for_active_members(db, bus):
    founder, startup = _startup(db)
    teammate = create_user(db)
    create_membership(db, teammate, startup, role=MembershipRole.team_member)
    _add(db, startup, 600_000, TransactionDirection.outflow)
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    runway_svc.evaluate_runway_alert(db, startup_id=startup.id)
    notifs = db.query(Notification).filter_by(startup_id=startup.id, type=EVENT).all()
    assert {n.user_id for n in notifs} == {founder.id, teammate.id}
    assert all(n.title == "Your runway is running low" for n in notifs)
