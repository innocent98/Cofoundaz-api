from datetime import UTC, datetime

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.platform import events as events_mod
from app.services.finance import invoices as inv_svc
from app.services.finance import runway as runway_svc
from tests.services.finance.payments_helpers import make_invoice, make_workspace


def _events(monkeypatch):
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    return published


def _named(published, name):
    return [p for e, p in published if e == name]


def test_mark_paid_publishes_once_per_transition(db, monkeypatch):
    _u, s = make_workspace(db)
    inv = make_invoice(db, s, total=150_000)
    published = _events(monkeypatch)
    inv_svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    inv_svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    [payload] = _named(published, "finance.invoice.paid")
    assert payload == {
        "startup_id": str(s.id),
        "invoice_id": str(inv.id),
        "number": "INV-2026-001",
        "client_name": "Acme Ltd",
        "amount_minor": 150_000,
        "currency": "NGN",
        "paid_via": None,
        "href": "/finance/invoices",
    }


def test_provider_path_publishes_with_paid_via_and_replay_is_silent(db, monkeypatch):
    _u, s = make_workspace(db)
    inv = make_invoice(db, s)
    published = _events(monkeypatch)
    for provider in ("paystack", "paystack", "flutterwave"):
        inv_svc.mark_paid_via_provider(db, startup_id=s.id, invoice_id=inv.id, provider=provider)
    [payload] = _named(published, "finance.invoice.paid")
    assert payload["paid_via"] == "paystack"


def test_not_payable_invoice_publishes_nothing(db, monkeypatch):
    from app.db.models.enums import InvoiceStatus

    _u, s = make_workspace(db)
    inv = make_invoice(db, s, status=InvoiceStatus.draft)
    published = _events(monkeypatch)
    out = inv_svc.mark_paid_via_provider(
        db, startup_id=s.id, invoice_id=inv.id, provider="paystack"
    )
    assert out is None
    assert _named(published, "finance.invoice.paid") == []


def _txn(db, s, direction, amount):
    db.add(
        Transaction(
            startup_id=s.id,
            date=datetime.now(UTC).date(),
            description="x",
            category="Other",
            amount_minor=amount,
            currency="NGN",
            direction=direction,
            source=TransactionSource.manual,
        )
    )
    db.flush()


def test_runway_updated_only_on_band_flip(db, monkeypatch):
    _u, s = make_workspace(db)
    published = _events(monkeypatch)

    runway_svc.evaluate_runway_alert(db, startup_id=s.id)  # first evaluation, healthy
    assert _named(published, "finance.runway.updated") == []

    _txn(db, s, TransactionDirection.outflow, 600_000)
    runway_svc.evaluate_runway_alert(db, startup_id=s.id)  # healthy -> low
    runway_svc.evaluate_runway_alert(db, startup_id=s.id)  # still low
    [low] = _named(published, "finance.runway.updated")
    assert low["band"] == "low"
    assert low["href"] == "/finance/runway"
    assert low["currency"] == "NGN"
    assert len(_named(published, "finance.runway.low")) == 1

    _txn(db, s, TransactionDirection.inflow, 50_000_000)
    runway_svc.evaluate_runway_alert(db, startup_id=s.id)  # low -> healthy
    flips = _named(published, "finance.runway.updated")
    assert [p["band"] for p in flips] == ["low", "healthy"]
