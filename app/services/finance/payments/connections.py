from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.enums import ConnectionStatus
from app.db.models.finance_connection import FinanceConnection
from app.platform.audit import write_audit
from app.schemas.finance_connection import ConnectionResponse
from app.services.finance.payments import crypto
from app.services.finance.payments.base import (
    Credentials,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderUnavailable,
)
from app.services.finance.payments.registry import get_provider

LABELS = {"paystack": "Paystack", "flutterwave": "Flutterwave"}
KEY_HINTS = {
    "paystack": "Starts with sk_test_ or sk_live_",
    "flutterwave": "Starts with FLWSECK_TEST- or FLWSECK-",
}
PROVIDER_NAMES = ("paystack", "flutterwave")

_DISCONNECTED = ConnectionStatus.disconnected.value


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Please check the highlighted fields.",
        422,
        field_errors=[{"field": field, "message": message}],
    )


def get_active(db: Session, startup_id: uuid.UUID) -> FinanceConnection | None:
    """The workspace's live (not disconnected) connection, if any."""
    return db.execute(
        select(FinanceConnection).where(
            FinanceConnection.startup_id == startup_id,
            FinanceConnection.status != _DISCONNECTED,
        )
    ).scalar_one_or_none()


def get_for_provider(db: Session, startup_id: uuid.UUID, provider: str) -> FinanceConnection | None:
    return db.execute(
        select(FinanceConnection).where(
            FinanceConnection.startup_id == startup_id,
            FinanceConnection.provider == provider,
            FinanceConnection.status != _DISCONNECTED,
        )
    ).scalar_one_or_none()


def credentials(conn: FinanceConnection) -> Credentials:
    if not conn.key_ciphertext:
        raise NotFound()
    return Credentials(
        secret_key=crypto.decrypt_secret(conn.key_ciphertext),
        secret_hash=(
            crypto.decrypt_secret(conn.secret_hash_ciphertext)
            if conn.secret_hash_ciphertext
            else None
        ),
    )


def webhook_url(conn: FinanceConnection) -> str:
    base = settings.API_PUBLIC_URL.rstrip("/") or f"{settings.SERVER_HOST}:{settings.SERVER_PORT}"
    return f"{base}{settings.API_V1_STR}/webhooks/payments/{conn.provider}/{conn.public_id}"


def serialize(conn: FinanceConnection) -> ConnectionResponse:
    return ConnectionResponse(
        id=conn.id,
        provider=conn.provider,
        status=conn.status,
        mode=conn.mode,
        key_last4=conn.key_last4,
        account_name=conn.account_name,
        last_sync_at=conn.last_sync_at,
        last_error=conn.last_error,
        last_sync_summary=conn.last_sync_summary,
        webhook_url=webhook_url(conn),
        created_at=conn.created_at,
    )


def _already_connected(other: str, this: str) -> AppError:
    return AppError(
        "PAYMENT_PROVIDER_ALREADY_CONNECTED",
        f"Disconnect {LABELS[other]} before connecting {LABELS[this]}.",
        409,
    )


def connect(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: str,
    secret_key: str,
    secret_hash: str | None,
) -> FinanceConnection:
    adapter = get_provider(provider)

    # Cheap local checks first: nothing below here has touched the network.
    if not secret_key.startswith(adapter.key_prefixes):
        raise _validation(
            "secret_key", f"Use your {LABELS[provider]} secret key ({KEY_HINTS[provider].lower()})."
        )
    if provider == "flutterwave" and not secret_hash:
        raise _validation("secret_hash", "Enter your Flutterwave secret hash.")

    key_ciphertext = crypto.encrypt_secret(secret_key)  # 503 when not configured
    hash_ciphertext = crypto.encrypt_secret(secret_hash) if secret_hash else None

    live = get_active(db, startup_id)
    if live is not None and live.provider != provider:
        raise _already_connected(live.provider, provider)

    try:
        info = adapter.verify_key(Credentials(secret_key=secret_key, secret_hash=secret_hash))
    except ProviderAuthError:
        raise AppError(
            "PAYMENT_KEY_REJECTED",
            f"{LABELS[provider]} did not accept that key. Check it and try again.",
            422,
        ) from None
    except (ProviderUnavailable, ProviderBadResponse):
        raise AppError(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            f"We could not reach {LABELS[provider]}. Try again in a few minutes.",
            502,
        ) from None

    row = db.execute(
        select(FinanceConnection).where(
            FinanceConnection.startup_id == startup_id,
            FinanceConnection.provider == provider,
        )
    ).scalar_one_or_none()

    if row is not None and row.status != _DISCONNECTED:
        action = "replaced"
    else:
        action = "connected"

    fields = {
        "status": ConnectionStatus.active.value,
        "key_ciphertext": key_ciphertext,
        "secret_hash_ciphertext": hash_ciphertext,
        "key_last4": secret_key[-4:],
        "mode": adapter.mode_for(secret_key),
        "account_name": info.account_name,
        "sync_cursor": None,
        "last_error": None,
        "connected_by_user_id": user_id,
    }

    try:
        with db.begin_nested():
            if row is None:
                row = FinanceConnection(
                    startup_id=startup_id,
                    provider=provider,
                    public_id=secrets.token_urlsafe(32),
                    **fields,
                )
                db.add(row)
            else:
                if row.status == _DISCONNECTED:
                    row.public_id = secrets.token_urlsafe(32)
                    row.last_sync_at = None
                    row.last_sync_summary = None
                for k, v in fields.items():
                    setattr(row, k, v)
            db.flush()
    except IntegrityError:
        other = get_active(db, startup_id)
        raise _already_connected(other.provider if other else "paystack", provider) from None

    write_audit(
        db,
        f"finance.connection.{action}",
        actor_user_id=user_id,
        startup_id=startup_id,
        entity_type="finance_connection",
        entity_id=row.id,
    )
    return row


def disconnect(
    db: Session, *, startup_id: uuid.UUID, user_id: uuid.UUID, provider: str
) -> FinanceConnection:
    get_provider(provider)
    row = get_for_provider(db, startup_id, provider)
    if row is None:
        raise NotFound()
    row.status = ConnectionStatus.disconnected.value
    row.key_ciphertext = None
    row.secret_hash_ciphertext = None
    db.flush()
    write_audit(
        db,
        "finance.connection.disconnected",
        actor_user_id=user_id,
        startup_id=startup_id,
        entity_type="finance_connection",
        entity_id=row.id,
    )
    return row
