from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.core.logger import log
from app.db.models.enums import ConnectionStatus, InvoiceStatus
from app.db.models.finance_connection import FinanceConnection, ProviderEvent
from app.db.models.invoice import Invoice
from app.services.finance import invoices as invoices_svc
from app.services.finance import runway as runway_svc
from app.services.finance.payments import connections as conn_svc
from app.services.finance.payments.base import (
    Credentials,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderTransaction,
    ProviderUnavailable,
    WebhookEvent,
)
from app.services.finance.payments.flutterwave import FlutterwaveProvider
from app.services.finance.payments.importer import CURRENCY, ImportResult, import_items
from app.services.finance.payments.registry import get_provider
from app.services.finance.payments.sync import AUTH_ERROR_MESSAGE

MAX_BODY_BYTES = 256 * 1024
_EVENT_ID_MAX = 200
# needs_attention means the provider rejected the stored key (possibly revoked or leaked), so
# events signed with it are not trusted; sync catches the money up once the key is replaced.
_RECEIVABLE = (ConnectionStatus.active.value,)
UNVERIFIED = "unverified"


def _invalid_signature() -> AppError:
    return AppError("INVALID_SIGNATURE", "Invalid signature.", 401)


def _dedupe_key(event: WebhookEvent, raw_body: bytes) -> str:
    """The provider event id, made unique when the provider omitted its own id.

    Without this, every id-less event of one type would share `<type>:None` and the second
    legitimate payment would be dropped as a duplicate.
    """
    key = event.event_id
    if key.endswith(":None") or key.endswith(":"):
        key = f"{key}:{hashlib.sha256(raw_body).hexdigest()}"
    return key[:_EVENT_ID_MAX]


def _find_connection(db: Session, provider: str, public_id: str) -> FinanceConnection:
    conn = db.execute(
        select(FinanceConnection).where(
            FinanceConnection.provider == provider,
            FinanceConnection.public_id == public_id,
            FinanceConnection.status.in_(_RECEIVABLE),
        )
    ).scalar_one_or_none()
    if conn is None:
        raise NotFound()
    return conn


def _charge_items(conn: FinanceConnection, event: WebhookEvent) -> list[ProviderTransaction]:
    if event.reference is None or event.amount_minor is None:
        return []
    when = event.occurred_at or datetime.now(UTC)
    currency = event.currency or ""
    items = [
        ProviderTransaction(
            "charge",
            event.reference,
            event.amount_minor,
            currency,
            when,
            f"{conn_svc.LABELS[conn.provider]} payment {event.reference}",
            None,
        )
    ]
    if event.fee_minor:
        items.append(
            ProviderTransaction(
                "fee",
                f"{event.reference}:fee",
                event.fee_minor,
                currency,
                when,
                f"{conn_svc.LABELS[conn.provider]} fee {event.reference}",
                event.reference,
            )
        )
    return items


def _refund_items(
    conn: FinanceConnection, event: WebhookEvent, dedupe_key: str
) -> list[ProviderTransaction]:
    # The adapter encodes the refund id as the suffix of "<event>:<id>"; the sync listing keys
    # refunds by that same id, so a webhook and a later sync collapse into one row.
    refund_id = dedupe_key.split(":", 1)[1] if ":" in dedupe_key else ""
    if not refund_id or refund_id == "None" or event.amount_minor is None:
        return []
    return [
        ProviderTransaction(
            "refund",
            refund_id,
            event.amount_minor,
            event.currency or "",
            event.occurred_at or datetime.now(UTC),
            f"{conn_svc.LABELS[conn.provider]} refund {refund_id}",
            event.reference,
        )
    ]


def _outcome_of(result: ImportResult) -> str:
    return (
        "imported" if (result.imported or result.refunds or result.invoice_matched) else "ignored"
    )


def _apply_charge(db: Session, conn: FinanceConnection, event: WebhookEvent) -> tuple[str, bool]:
    """Returns (outcome, changed)."""
    items = _charge_items(conn, event)
    if not items:
        return "ignored", False
    charge = items[0]

    invoice = db.execute(
        select(Invoice).where(
            Invoice.startup_id == conn.startup_id, Invoice.pay_reference == charge.reference
        )
    ).scalar_one_or_none()
    if invoice is not None:
        # Row lock first (populate_existing): every later check sees the committed state and a
        # concurrent delivery of the same payment waits here, then finds the invoice paid.
        locked = invoices_svc.get_invoice_locked(
            db, startup_id=conn.startup_id, invoice_id=invoice.id
        )
        if locked.status == InvoiceStatus.paid:
            return "invoice_paid", False
        if locked.status == InvoiceStatus.sent:
            if charge.amount_minor != locked.total_minor or charge.currency != locked.currency:
                return "ignored", False
            if locked.currency != CURRENCY:
                return "ignored", False
            paid = invoices_svc.mark_paid_via_provider(
                db,
                startup_id=conn.startup_id,
                invoice_id=locked.id,
                provider=conn.provider,
                now=charge.occurred_at,
            )
            if paid is not None:
                import_items(db, connection=conn, items=items[1:])  # the fee row, if any
                return "invoice_paid", True
        # a draft falls through: the money is real, record it as a plain inflow

    result = import_items(db, connection=conn, items=items)
    return _outcome_of(result), result.changed


def _reverify_flutterwave(
    conn: FinanceConnection,
    adapter: FlutterwaveProvider,
    creds: Credentials,
    event: WebhookEvent,
) -> WebhookEvent | None:
    """The API's own view of a Flutterwave charge, or None when it does not back the webhook.

    `verif-hash` is a static secret, so whoever learns it can forge `charge.completed`; money is
    only ever applied from what the API confirms. Provider downtime raises a 502 so the
    delivery is retried; our rejected key is also "not confirmed" and flags the connection.
    """
    if event.transaction_id is None or event.reference is None:
        return None
    try:
        confirmed = adapter.verify_transaction(creds, transaction_id=event.transaction_id)
    except ProviderUnavailable:
        raise AppError(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            "Could not reach the payment provider to confirm this payment. Try again shortly.",
            502,
        ) from None
    except ProviderAuthError:
        conn.status = ConnectionStatus.needs_attention.value
        conn.last_error = AUTH_ERROR_MESSAGE
        return None
    except ProviderBadResponse:
        return None  # 404 / other 4xx / unreadable: the API does not know this transaction
    if (
        confirmed is None
        or confirmed.reference != event.reference
        or confirmed.amount_minor != event.amount_minor
        or confirmed.currency != event.currency
    ):
        return None
    return confirmed


def process(
    db: Session,
    *,
    provider: str,
    public_id: str,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> dict[str, bool]:
    """Verify, dedupe and apply one provider webhook. The caller commits.

    Order matters: the signature is checked over the raw bytes before anything is parsed, and the
    ProviderEvent row is written in the same transaction as the money effect, so a crash can
    never leave an event marked seen without its effect.
    """
    adapter = get_provider(provider)  # unknown provider: 404
    conn = _find_connection(db, provider, public_id)
    creds = conn_svc.credentials(conn)  # 503 when encryption is not configured

    if not adapter.verify_webhook(creds, raw_body=raw_body, headers=headers):
        raise _invalid_signature()

    try:
        event = adapter.parse_webhook(raw_body)
    except ProviderBadResponse:
        raise AppError("INVALID_PAYLOAD", "The webhook body could not be read.", 400) from None

    key = _dedupe_key(event, raw_body)
    # A forged delivery leaves an "unverified" row; it must not shadow the real one that
    # follows, so only that outcome may be processed again.
    row = db.execute(
        select(ProviderEvent)
        .where(ProviderEvent.connection_id == conn.id, ProviderEvent.event_id == key)
        .with_for_update()
    ).scalar_one_or_none()
    if row is not None and row.outcome != UNVERIFIED:
        return {"received": True, "duplicate": True}

    # Verify with the provider API BEFORE the event row or any invoice lock exists: an outage
    # raises 502 with nothing written, so the provider's retry can succeed.
    verified: WebhookEvent | None = event
    if isinstance(adapter, FlutterwaveProvider) and event.kind == "charge":
        verified = _reverify_flutterwave(conn, adapter, creds, event)

    if row is None:
        row = ProviderEvent(
            connection_id=conn.id, event_id=key, event_type=event.kind, outcome="received"
        )
        try:
            with db.begin_nested():
                db.add(row)  # inside the savepoint: a duplicate rolls back with it
        except IntegrityError:
            return {"received": True, "duplicate": True}

    if verified is None:
        outcome, changed = UNVERIFIED, False
    elif event.kind == "charge":
        outcome, changed = _apply_charge(db, conn, verified)
    elif event.kind == "refund":
        result = import_items(db, connection=conn, items=_refund_items(conn, event, key))
        outcome, changed = _outcome_of(result), result.changed
    else:
        outcome, changed = "ignored", False

    row.outcome = outcome
    db.flush()
    if changed:
        runway_svc.evaluate_runway_alert(db, startup_id=conn.startup_id)
        runway_svc.upsert_runway_signal(db, startup_id=conn.startup_id)
    log.info(f"[webhook] {provider} connection {conn.id}: {event.kind} -> {outcome}")
    return {"received": True}
