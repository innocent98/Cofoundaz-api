from __future__ import annotations

import secrets
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.models.enums import InvoiceStatus, InvoiceTerms
from app.db.models.finance_connection import FinanceConnection
from app.db.models.invoice import Invoice
from app.services.finance.payments import crypto
from app.services.finance.payments.base import ProviderTransaction
from tests.factories import create_membership, create_startup, create_user


def make_workspace(db: Session):
    user = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=user)
    create_membership(db, user, startup)
    db.flush()
    return user, startup


def make_connection(
    db: Session, startup, *, provider: str = "paystack", status: str = "active"
) -> FinanceConnection:
    conn = FinanceConnection(
        startup_id=startup.id,
        public_id=secrets.token_urlsafe(32),
        provider=provider,
        status=status,
        key_ciphertext=(
            crypto.encrypt_secret("sk_test_fake_fake_fake1234")
            if crypto.encryption_configured()
            else "gAAAA-placeholder-not-decryptable"
        ),
        key_last4="1234",
        mode="test",
    )
    db.add(conn)
    db.flush()
    return conn


def make_invoice(
    db: Session, startup, *, reference: str | None = "inv_ref_1", total: int = 500000, **kw
) -> Invoice:
    inv = Invoice(
        startup_id=startup.id,
        number=kw.pop("number", "INV-2026-001"),
        client_name="Acme Ltd",
        client_email="billing@acme-client.com",
        line_items=[{"description": "Work", "quantity": 1, "unit_price_minor": total}],
        subtotal_minor=total,
        tax_percent=0,
        tax_minor=0,
        total_minor=total,
        currency="NGN",
        terms=InvoiceTerms.net_30,
        status=kw.pop("status", InvoiceStatus.sent),
        issued_on=date(2026, 10, 1),
        due_on=date(2026, 10, 31),
        pay_reference=reference,
        pay_provider="paystack",
        **kw,
    )
    db.add(inv)
    db.flush()
    return inv


def charge(ref: str, amount: int, *, day: int = 1, currency: str = "NGN") -> ProviderTransaction:
    return ProviderTransaction(
        "charge",
        ref,
        amount,
        currency,
        datetime(2026, 10, day, 9, 30, tzinfo=UTC),
        f"pay {ref}",
        None,
    )


def fee(ref: str, amount: int, *, day: int = 1, currency: str = "NGN") -> ProviderTransaction:
    return ProviderTransaction(
        "fee",
        f"{ref}:fee",
        amount,
        currency,
        datetime(2026, 10, day, 9, 30, tzinfo=UTC),
        f"fee {ref}",
        ref,
    )


def refund(rid: str, amount: int, *, parent: str | None, day: int = 3) -> ProviderTransaction:
    return ProviderTransaction(
        "refund",
        rid,
        amount,
        "NGN",
        datetime(2026, 10, day, 8, 0, tzinfo=UTC),
        f"refund {rid}",
        parent,
    )
