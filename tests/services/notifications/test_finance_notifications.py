from datetime import UTC, datetime

import pytest

from app.core.money import format_money
from app.db.models.enums import FINANCE_ROLES, MembershipRole
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.services.finance import invoices as inv_svc
from app.services.finance import runway as runway_svc
from app.services.notifications import registry
from app.services.notifications.audience import AUDIENCE
from app.services.notifications.service import list_notifications, unread_count
from tests.factories import create_membership, create_startup, create_user
from tests.services.finance.payments_helpers import make_invoice

EVENTS = ("finance.invoice.overdue", "finance.invoice.paid", "finance.runway.updated")


def _team(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    users = {}
    for role in MembershipRole:
        u = owner if role == MembershipRole.founder else create_user(db)
        create_membership(db, u, s, role=role)
        users[role] = u
    db.flush()
    return s, users


def _bus(monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(inv_svc, "event_bus", bus)
    monkeypatch.setattr(runway_svc, "event_bus", bus)
    return bus


def _visible(db, s, users, role):
    kw = {"user_id": users[role].id, "startup_id": s.id, "role": role}
    items, _cur = list_notifications(db, unread=False, limit=50, cursor=None, **kw)
    return items


def test_format_money():
    assert format_money(150_000, "NGN") == "NGN 1,500.00"
    assert format_money(5, "NGN") == "NGN 0.05"


def test_audience_rows_are_finance_roles():
    for ev in (*EVENTS, "finance.runway.low"):
        assert AUDIENCE[ev] == FINANCE_ROLES


def test_overdue_copy_from_payload():
    spec = registry.SPECS["finance.invoice.overdue"]
    payload = {
        "number": "INV-2026-007",
        "client_name": "Globex Ltd",
        "amount_minor": 150_000,
        "currency": "NGN",
    }
    assert spec.title(payload) == "Invoice overdue: INV-2026-007 · Globex Ltd · NGN 1,500.00"
    assert spec.body(payload) == "Send reminder"


def test_paid_and_runway_copy():
    paid = registry.SPECS["finance.invoice.paid"]
    assert paid.title({"number": "INV-1"}) == "Invoice paid: INV-1 🎉"
    assert paid.body({}) == "View invoice"
    upd = registry.SPECS["finance.runway.updated"]
    assert upd.title({"runway_months": 4.26}) == "Your runway is now 4.3 months"
    assert upd.title({"runway_months": 12}) == "Your runway is now 12.0 months"
    assert upd.title({"runway_months": None}) == "Your runway is healthy"
    assert upd.body({}) == "Open Finance"
    low = registry.SPECS["finance.runway.low"]
    assert low.title({}) == "Runway below 6 months, review recommended"
    assert low.body({}) == "Open Finance"


@pytest.mark.parametrize("event", EVENTS)
def test_fan_out_and_read_filter_agree(db, monkeypatch, event):
    s, users = _team(db)
    bus = _bus(monkeypatch)
    payload = {
        "startup_id": str(s.id),
        "number": "INV-9",
        "client_name": "Acme",
        "amount_minor": 1000,
        "currency": "NGN",
        "runway_months": 3.0,
        "href": "/finance/invoices",
    }
    bus.publish(db, event, payload)
    for role in MembershipRole:
        rows = [n for n in _visible(db, s, users, role) if n.type == event]
        if role in FINANCE_ROLES:
            assert len(rows) == 1, role
        else:
            assert rows == [], role
        # A mentor/consultant must not be delivered the row at all.
        stored = (
            db.query(Notification)
            .filter_by(startup_id=s.id, user_id=users[role].id, type=event)
            .count()
        )
        assert stored == (1 if role in FINANCE_ROLES else 0), role


def test_paid_end_to_end_includes_actor_less_fan_out(db, monkeypatch):
    s, users = _team(db)
    _bus(monkeypatch)
    inv = make_invoice(db, s, total=150_000)
    inv_svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    accountant = users[MembershipRole.accountant]
    [n] = [
        x
        for x in _visible(db, s, users, MembershipRole.accountant)
        if x.type.startswith("finance.invoice")
    ]
    assert n.title == "Invoice paid: INV-2026-001 🎉"
    assert n.body == "View invoice"
    assert n.data["href"] == "/finance/invoices"
    assert (
        unread_count(db, user_id=accountant.id, startup_id=s.id, role=MembershipRole.accountant)
        >= 1
    )
    assert _visible(db, s, users, MembershipRole.mentor) == []
