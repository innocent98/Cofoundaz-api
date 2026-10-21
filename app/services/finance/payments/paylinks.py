from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.core.logger import log
from app.db.models.enums import InvoiceStatus
from app.db.models.invoice import Invoice
from app.services.finance.payments import connections as conn_svc
from app.services.finance.payments.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderUnavailable,
)
from app.services.finance.payments.registry import get_provider

LINK_CURRENCY = "NGN"


def _redirect_url() -> str:
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    return f"{base}/finance/invoices"


def ensure_pay_link(db: Session, *, invoice: Invoice) -> bool:
    """Create (or keep) a provider pay link for a sent NGN invoice.

    Never raises for provider or configuration problems: a missing link must not fail `send`.
    Returns True when the invoice has a link afterwards. Log lines carry only ids and the
    exception type, never keys, headers or provider bodies.
    """
    if invoice.status != InvoiceStatus.sent or invoice.currency != LINK_CURRENCY:
        return False
    if invoice.pay_url:
        return True
    conn = conn_svc.get_active(db, invoice.startup_id)
    if conn is None or conn.status != "active":
        return False

    reference = f"{invoice.number}-{uuid.uuid4().hex[:8]}"
    try:
        creds = conn_svc.credentials(conn)
        link = get_provider(conn.provider).create_payment_link(
            creds,
            reference=reference,
            amount_minor=invoice.total_minor,
            email=invoice.client_email,
            currency=invoice.currency,
            description=f"Invoice {invoice.number}",
            redirect_url=_redirect_url(),
        )
    except (ProviderAuthError, ProviderUnavailable, ProviderBadResponse, AppError, NotFound) as exc:
        log.warning(
            f"[invoice] pay link not created for invoice {invoice.id} via {conn.provider}: "
            f"{type(exc).__name__}"
        )
        return False

    invoice.pay_reference = reference
    invoice.pay_url = link.url
    invoice.pay_provider = conn.provider
    db.flush()
    return True
