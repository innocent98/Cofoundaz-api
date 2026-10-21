from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.enums import ConnectionStatus
from app.db.models.finance_connection import FinanceConnection
from app.services.finance import runway as runway_svc
from app.services.finance.payments import connections as conn_svc
from app.services.finance.payments.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderUnavailable,
)
from app.services.finance.payments.importer import import_items
from app.services.finance.payments.registry import get_provider

AUTH_ERROR_MESSAGE = "The provider rejected the saved key. Replace it to resume syncing."
CURSOR_OVERLAP = timedelta(days=1)


@dataclass
class SyncOutcome:
    connection: FinanceConnection
    summary: dict[str, Any]


def _unavailable_message(provider: str) -> str:
    return f"We could not reach {conn_svc.LABELS[provider]}. We will retry automatically."


def sync_connection(
    db: Session,
    connection_id: uuid.UUID,
    *,
    now: datetime | None = None,
    manual: bool = False,
) -> SyncOutcome | None:
    """Pull the provider's recent rows into the ledger.

    Returns None when the connection is missing, not active, or locked by another worker
    (SELECT ... FOR UPDATE SKIP LOCKED), so concurrent syncs never double-run.
    """
    now = now or datetime.now(UTC)
    conn = db.execute(
        select(FinanceConnection)
        .where(
            FinanceConnection.id == connection_id,
            FinanceConnection.status == ConnectionStatus.active.value,
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if conn is None:
        return None

    if (
        manual
        and conn.last_sync_at is not None
        and (now - conn.last_sync_at).total_seconds() < settings.FINANCE_SYNC_MIN_INTERVAL_SECONDS
    ):
        raise AppError("SYNC_TOO_SOON", "Synced a moment ago. Try again shortly.", 429)

    creds = conn_svc.credentials(conn)  # 503 when encryption is not configured
    since = (
        conn.sync_cursor - CURSOR_OVERLAP
        if conn.sync_cursor is not None
        else now - timedelta(days=settings.FINANCE_SYNC_BACKFILL_DAYS)
    )
    try:
        items = get_provider(conn.provider).list_transactions(
            creds, since=since, max_pages=settings.FINANCE_SYNC_MAX_PAGES
        )
    except ProviderAuthError:
        conn.status = ConnectionStatus.needs_attention.value
        conn.last_error = AUTH_ERROR_MESSAGE
        db.flush()
        return SyncOutcome(conn, dict(conn.last_sync_summary or {}))
    except (ProviderUnavailable, ProviderBadResponse):
        conn.last_error = _unavailable_message(conn.provider)
        db.flush()
        return SyncOutcome(conn, dict(conn.last_sync_summary or {}))

    result = import_items(db, connection=conn, items=items)
    summary: dict[str, Any] = {**result.as_dict(), "fetched": len(items)}
    if items:
        newest = max(i.occurred_at for i in items)
        if conn.sync_cursor is None or newest > conn.sync_cursor:
            conn.sync_cursor = newest
    conn.last_sync_at = now
    conn.last_sync_summary = summary
    conn.last_error = None
    db.flush()

    if result.changed:
        runway_svc.evaluate_runway_alert(db, startup_id=conn.startup_id)
        runway_svc.upsert_runway_signal(db, startup_id=conn.startup_id)
    return SyncOutcome(conn, summary)
