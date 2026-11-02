"""Inbound Mono webhooks: one URL for the whole Mono app, authenticated by a shared secret."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logger import log
from app.db.models.enums import ConnectionStatus
from app.db.models.finance_connection import FinanceConnection
from app.db.models.job import Job, JobStatus
from app.platform.jobs import job_dispatcher
from app.services.finance.payments.mono import MonoProvider

EVENT_UPDATED = "mono.events.account_updated"
EVENT_REAUTH = "mono.events.reauthorisation_required"
REAUTH_MESSAGE = (
    "Your bank needs you to re-authorise access. Reconnect your bank to resume syncing."
)


def _account_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    account = data.get("account")
    if isinstance(account, dict):
        # Mono's webhook bodies carry the account as `data.account._id`.
        account = account.get("_id") or account.get("id")
    if not isinstance(account, str):
        account = data.get("id")
    return account if isinstance(account, str) and account else None


def _sync_pending(db: Session, conn: FinanceConnection, job_type: str) -> bool:
    return (
        db.execute(
            select(Job.id)
            .where(
                Job.type == job_type,
                Job.status.in_((JobStatus.queued, JobStatus.running)),
                Job.payload["connection_id"].astext == str(conn.id),
            )
            .limit(1)
        ).first()
        is not None
    )


def process(db: Session, *, raw_body: bytes, headers: Mapping[str, str]) -> dict[str, bool]:
    """Verify, then react. The caller commits.

    The shared secret is checked before the body is parsed. A validly signed event for an
    account we do not know is acknowledged (nothing to do, and no retry storm).
    """
    if not MonoProvider.verify_webhook(settings.MONO_WEBHOOK_SECRET, headers=headers):
        raise AppError("INVALID_SIGNATURE", "Invalid signature.", 401)
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        raise AppError("INVALID_PAYLOAD", "The webhook body could not be read.", 400) from None
    if not isinstance(payload, dict):
        raise AppError("INVALID_PAYLOAD", "The webhook body could not be read.", 400)

    event = str(payload.get("event") or "")
    data = payload.get("data")
    account_id = _account_id(data)
    if event not in (EVENT_UPDATED, EVENT_REAUTH) or account_id is None:
        return {"received": True}

    conn = db.execute(
        select(FinanceConnection).where(
            FinanceConnection.provider == "mono",
            FinanceConnection.provider_account_hash
            == hashlib.sha256(account_id.encode("utf-8")).hexdigest(),
            FinanceConnection.status != ConnectionStatus.disconnected.value,
        )
    ).scalar_one_or_none()
    if conn is None:
        return {"received": True}

    if event == EVENT_REAUTH:
        conn.status = ConnectionStatus.needs_attention.value
        conn.last_error = REAUTH_MESSAGE
        db.flush()
    else:
        meta = data.get("meta") if isinstance(data, dict) else None
        status = meta.get("data_status") if isinstance(meta, dict) else None
        if status == "AVAILABLE" and conn.status == ConnectionStatus.active.value:
            # Imported by the worker, not in this request: Mono expects a quick 200.
            from app.worker.scheduler import SCHED_PAYMENTS_SYNC  # avoid a service->worker cycle

            if _sync_pending(db, conn, SCHED_PAYMENTS_SYNC):
                return {"received": True}  # a replay or burst: the queued sync covers it
            job_dispatcher.enqueue(
                db,
                SCHED_PAYMENTS_SYNC,
                {"connection_id": str(conn.id), "startup_id": str(conn.startup_id)},
                conn.startup_id,
            )
    log.info(f"[webhook] mono connection {conn.id}: {event}")
    return {"received": True}
