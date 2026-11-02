from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.enums import InvoiceStatus
from app.db.models.finance import Transaction
from app.db.models.finance_connection import FinanceConnection
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.finance.payments import sync as sync_mod
from app.services.finance.payments.base import ProviderAuthError, ProviderUnavailable
from tests.services.finance.payments_helpers import (
    charge,
    fee,
    make_connection,
    make_invoice,
    make_workspace,
    refund,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


class FakeProvider:
    name = "paystack"

    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.calls = []

    def list_transactions(self, creds, *, since, max_pages):
        self.calls.append((since, max_pages))
        if self.error:
            raise self.error
        return list(self.items)


@pytest.fixture(autouse=True)
def _enc(monkeypatch):
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


def _use(monkeypatch, fake):
    monkeypatch.setattr(sync_mod, "get_sync_source", lambda name: fake)
    return fake


def _conn(db):
    from app.services.finance.payments import crypto

    _u, s = make_workspace(db)
    conn = make_connection(db, s)
    conn.key_ciphertext = crypto.encrypt_secret("sk_test_fake_fake_fake1234")
    db.flush()
    return s, conn


def test_sync_imports_updates_cursor_and_summary(db, monkeypatch):
    s, conn = _conn(db)
    fake = _use(
        monkeypatch,
        FakeProvider([charge("a", 1000, day=2), fee("a", 15, day=2), charge("b", 2000, day=5)]),
    )
    out = sync_mod.sync_connection(db, conn.id, now=NOW)
    assert out is not None
    assert out.summary["imported"] == 2 and out.summary["fees"] == 1
    assert conn.sync_cursor == datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
    assert conn.last_sync_at == NOW
    assert conn.last_sync_summary["imported"] == 2
    assert conn.last_error is None
    # first run backfills FINANCE_SYNC_BACKFILL_DAYS
    assert fake.calls[0] == (
        NOW - timedelta(days=settings.FINANCE_SYNC_BACKFILL_DAYS),
        settings.FINANCE_SYNC_MAX_PAGES,
    )

    sync_mod.sync_connection(db, conn.id, now=NOW + timedelta(hours=1))
    # later runs start at cursor minus 1 day overlap
    assert fake.calls[1][0] == datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
    assert db.query(Transaction).filter_by(connection_id=conn.id).count() == 3  # idempotent


def test_sync_runs_runway_after_import_only(db, monkeypatch):
    s, conn = _conn(db)
    calls = []
    monkeypatch.setattr(
        sync_mod.runway_svc, "evaluate_runway_alert", lambda d, **k: calls.append("alert")
    )
    monkeypatch.setattr(
        sync_mod.runway_svc, "upsert_runway_signal", lambda d, **k: calls.append("signal")
    )
    _use(monkeypatch, FakeProvider([]))
    sync_mod.sync_connection(db, conn.id, now=NOW)
    assert calls == []
    _use(monkeypatch, FakeProvider([charge("a", 1000)]))
    sync_mod.sync_connection(db, conn.id, now=NOW + timedelta(hours=1))
    assert calls == ["alert", "signal"]


def test_sync_pays_matching_invoice_and_refunds_unknown_parent(db, monkeypatch):
    s, conn = _conn(db)
    inv = make_invoice(db, s, reference="inv_ref_1")
    _use(
        monkeypatch,
        FakeProvider([charge("inv_ref_1", 500000), refund("77", 100, parent="ghost")]),
    )
    out = sync_mod.sync_connection(db, conn.id, now=NOW)
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid
    assert out.summary["invoice_matched"] == 1 and out.summary["refunds"] == 1


def test_auth_error_sets_needs_attention(db, monkeypatch):
    s, conn = _conn(db)
    _use(monkeypatch, FakeProvider(error=ProviderAuthError("no")))
    out = sync_mod.sync_connection(db, conn.id, now=NOW)
    assert out is not None
    assert conn.status == "needs_attention"
    assert conn.last_error == "The provider rejected the saved key. Replace it to resume syncing."
    # needs_attention connections are not synced again
    assert sync_mod.sync_connection(db, conn.id, now=NOW + timedelta(hours=1)) is None


def test_unavailable_keeps_status_active_and_records_error(db, monkeypatch):
    s, conn = _conn(db)
    _use(monkeypatch, FakeProvider(error=ProviderUnavailable("down")))
    sync_mod.sync_connection(db, conn.id, now=NOW)
    assert conn.status == "active" and conn.last_error
    assert "sk_test" not in conn.last_error


def test_manual_sync_too_soon_is_429(db, monkeypatch):
    s, conn = _conn(db)
    _use(monkeypatch, FakeProvider([]))
    sync_mod.sync_connection(db, conn.id, now=NOW, manual=True)
    with pytest.raises(AppError) as exc:
        sync_mod.sync_connection(db, conn.id, now=NOW + timedelta(seconds=2), manual=True)
    assert exc.value.code == "SYNC_TOO_SOON" and exc.value.http_status == 429
    # scheduled runs are not rate limited
    assert sync_mod.sync_connection(db, conn.id, now=NOW + timedelta(seconds=2)) is not None
    ok = sync_mod.sync_connection(
        db,
        conn.id,
        now=NOW + timedelta(seconds=settings.FINANCE_SYNC_MIN_INTERVAL_SECONDS + 5),
        manual=True,
    )
    assert ok is not None


def test_unknown_or_disconnected_returns_none(db, monkeypatch):
    s, conn = _conn(db)
    _use(monkeypatch, FakeProvider([]))
    conn.status = "disconnected"
    db.flush()
    assert sync_mod.sync_connection(db, conn.id, now=NOW) is None


def test_locked_connection_is_skipped_by_second_session(engine, monkeypatch):
    """Two real sessions: while one holds the row lock, the other skips (SKIP LOCKED)."""
    _use(monkeypatch, FakeProvider([charge("a", 1000)]))
    a = Session(bind=engine)
    b = Session(bind=engine)
    try:
        user, startup = make_workspace(a)
        conn = make_connection(a, startup)
        a.commit()
        conn_id, startup_id, user_id = conn.id, startup.id, user.id

        a.query(FinanceConnection).filter_by(id=conn_id).with_for_update().one()
        assert sync_mod.sync_connection(b, conn_id, now=NOW) is None
        b.rollback()
        a.rollback()  # release the lock

        out = sync_mod.sync_connection(b, conn_id, now=NOW)
        assert out is not None and out.summary["imported"] == 1
        b.commit()
    finally:
        a.rollback()
        b.rollback()
        a.query(Startup).filter_by(id=startup_id).delete()
        a.query(User).filter_by(id=user_id).delete()
        a.commit()
        a.close()
        b.close()
