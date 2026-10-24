from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, FinanceConnectionsNotConfigured, MonoNotConfigured, NotFound
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
from app.services.finance.payments.mono import SANDBOX_KEY_PREFIX, MonoProvider
from app.services.finance.payments.registry import get_provider, get_sync_source

LABELS = {"paystack": "Paystack", "flutterwave": "Flutterwave", "mono": "Mono"}
KEY_HINTS = {
    "paystack": "Starts with sk_test_ or sk_live_",
    "flutterwave": "Starts with FLWSECK_TEST- or FLWSECK-",
}
# The payment providers: one live at a time, founder pastes their own key.
PROVIDER_NAMES = ("paystack", "flutterwave")
# The bank link: linked in Mono Connect, sits beside the payment provider.
BANK_PROVIDER = "mono"

_DISCONNECTED = ConnectionStatus.disconnected.value


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Please check the highlighted fields.",
        422,
        field_errors=[{"field": field, "message": message}],
    )


def get_active(db: Session, startup_id: uuid.UUID) -> FinanceConnection | None:
    """The workspace's live (not disconnected) PAYMENT connection, if any (the bank link is
    separate, see get_for_provider)."""
    return db.execute(
        select(FinanceConnection).where(
            FinanceConnection.startup_id == startup_id,
            FinanceConnection.status != _DISCONNECTED,
            FinanceConnection.provider != BANK_PROVIDER,
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
    if conn.provider == BANK_PROVIDER:
        # The platform's Mono key, plus the founder's decrypted linked-account id.
        if not settings.MONO_SECRET_KEY:
            raise MonoNotConfigured()
        return Credentials(
            secret_key=settings.MONO_SECRET_KEY,
            account_id=crypto.decrypt_secret(conn.key_ciphertext),
        )
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
    if conn.provider == BANK_PROVIDER:
        # One URL for the whole Mono app (set once on the Mono dashboard), not one per founder.
        return f"{base}{settings.API_V1_STR}/webhooks/mono"
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
    if provider == BANK_PROVIDER:
        raise NotFound()  # linked through connect_mono, not by pasting a key
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
    get_sync_source(provider)  # unknown provider: 404
    row = get_for_provider(db, startup_id, provider)
    if row is None:
        raise NotFound()
    row.status = ConnectionStatus.disconnected.value
    row.key_ciphertext = None
    row.secret_hash_ciphertext = None
    row.provider_account_hash = None
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


def _bank_error(code: str, message: str, http_status: int) -> AppError:
    return AppError(code, message, http_status)


def mono_connect_config() -> dict[str, object]:
    """What the FE needs to open Mono Connect. The public key is public by design."""
    key = settings.MONO_SECRET_KEY
    return {
        "configured": bool(key and settings.MONO_PUBLIC_KEY and crypto.encryption_configured()),
        "public_key": settings.MONO_PUBLIC_KEY,
        "sandbox": not key or key.startswith(SANDBOX_KEY_PREFIX),
    }


def connect_mono(
    db: Session, *, startup_id: uuid.UUID, user_id: uuid.UUID, code: str
) -> FinanceConnection:
    """Exchange a Mono Connect code for the linked account and store its id, encrypted."""
    secret = settings.MONO_SECRET_KEY
    if not secret:
        raise MonoNotConfigured()
    if not secret.startswith(SANDBOX_KEY_PREFIX) and not settings.MONO_ALLOW_LIVE:
        raise _bank_error(
            "MONO_LIVE_DISABLED", "Bank connections are in sandbox mode only for now.", 503
        )
    if not crypto.encryption_configured():
        raise FinanceConnectionsNotConfigured()

    adapter = MonoProvider()
    try:
        account_id = adapter.exchange_code(Credentials(secret_key=secret), code)
        info = adapter.account_info(Credentials(secret_key=secret, account_id=account_id))
    except ProviderAuthError:
        # Mono refused OUR key: a server problem, not something the founder can fix.
        raise _bank_error(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            "We could not reach Mono. Try again in a few minutes.",
            502,
        ) from None
    except ProviderBadResponse:
        raise _bank_error(
            "BANK_LINK_REJECTED", "That bank link expired or was not accepted. Start again.", 422
        ) from None
    except ProviderUnavailable:
        raise _bank_error(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            "We could not reach Mono. Try again in a few minutes.",
            502,
        ) from None

    account_hash = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
    # An account id routes inbound webhooks, so one bank account can serve one workspace only.
    taken = db.execute(
        select(FinanceConnection.id).where(
            FinanceConnection.provider == BANK_PROVIDER,
            FinanceConnection.provider_account_hash == account_hash,
            FinanceConnection.status != _DISCONNECTED,
            FinanceConnection.startup_id != startup_id,
        )
    ).first()
    if taken is not None:
        raise _bank_error(
            "BANK_ACCOUNT_ALREADY_LINKED",
            "That bank account is already linked to another workspace.",
            409,
        )

    row = db.execute(
        select(FinanceConnection).where(
            FinanceConnection.startup_id == startup_id,
            FinanceConnection.provider == BANK_PROVIDER,
        )
    ).scalar_one_or_none()
    action = "replaced" if row is not None and row.status != _DISCONNECTED else "connected"
    fields = {
        "status": ConnectionStatus.active.value,
        "key_ciphertext": crypto.encrypt_secret(account_id),
        "key_last4": info.last4 or account_id[-4:],
        "mode": adapter.mode_for(secret),
        "account_name": info.label,
        "provider_account_hash": account_hash,
        "sync_cursor": None,
        "last_error": None,
        "connected_by_user_id": user_id,
    }
    try:
        with db.begin_nested():
            if row is None:
                row = FinanceConnection(
                    startup_id=startup_id,
                    provider=BANK_PROVIDER,
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
        # A double-submit (same workspace) or another workspace linking the same account at the
        # same moment: the unique indexes decide, we answer 409 instead of a 500.
        raise _bank_error(
            "BANK_LINK_CONFLICT",
            "That bank link is already being saved. Refresh and check the status.",
            409,
        ) from None
    write_audit(
        db,
        f"finance.connection.{action}",
        actor_user_id=user_id,
        startup_id=startup_id,
        entity_type="finance_connection",
        entity_id=row.id,
    )
    return row
