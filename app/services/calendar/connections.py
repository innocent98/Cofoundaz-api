"""A user's Google Calendar connection: OAuth connect/callback, token lifecycle, disconnect.

Per USER (D-CAL-2). Tokens are Fernet-encrypted and never returned. The OAuth `state` is a
short-lived signed JWT that is ALSO bound to a single-use nonce stored on a `pending`
connection row, so a captured callback URL cannot be replayed or used by another user.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AppError,
    CalendarSyncNotConfigured,
    FeatureNotEnabled,
    NotFound,
    TokenInvalid,
)
from app.core.logger import log
from app.db.models.calendar import (
    CalendarConnection,
    CalendarEventLink,
    CalendarExternalEvent,
)
from app.db.models.enums import MembershipStatus
from app.db.models.membership import Membership
from app.platform.audit import write_audit
from app.platform.jobs import job_dispatcher
from app.services.calendar import google
from app.services.calendar.crypto import decrypt_token, encrypt_token
from app.services.calendar.errors import _validation
from app.services.finance.payments.crypto import encryption_configured

PROVIDER = "google"
STATE_TTL_SECONDS = 600
STATE_TYP = "calendar_oauth"
DIRECTIONS = ("two_way", "pull_only", "push_only")
RECONNECT_MESSAGE = "Google no longer allows access. Reconnect to resume syncing."
JOB_SYNC = "calendar.sync"
# Error codes the callback hands back to the FE in `?error=`.
ERR_DENIED, ERR_STATE, ERR_EXCHANGE, ERR_NO_REFRESH, ERR_MEMBER = (
    "access_denied",
    "invalid_state",
    "exchange_failed",
    "no_refresh_token",
    "not_a_member",
)


def _now() -> datetime:
    return datetime.now(UTC)


def check_provider(provider: str) -> None:
    if provider == "outlook":
        # Reserved path (HANDOFF 0b-18); Microsoft is a later decision (0b-20).
        raise FeatureNotEnabled()
    if provider != PROVIDER:
        raise NotFound()


def sign_state(user_id: uuid.UUID, startup_id: uuid.UUID, nonce: str) -> str:
    claims = {
        "typ": STATE_TYP,
        "sub": str(user_id),
        "sid": str(startup_id),
        "nonce": nonce,
        "exp": _now() + timedelta(seconds=STATE_TTL_SECONDS),
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def read_state(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenInvalid() from exc
    if claims.get("typ") != STATE_TYP or not claims.get("nonce"):
        raise TokenInvalid()
    return claims


def get_connection(
    db: Session, *, user_id: uuid.UUID, lock: bool = False
) -> CalendarConnection | None:
    q = db.query(CalendarConnection).filter_by(user_id=user_id, provider=PROVIDER)
    if lock:
        q = q.with_for_update()
    return q.one_or_none()


def serialize(conn: CalendarConnection | None) -> dict[str, Any]:
    """Never includes tokens, ciphertext, the sync token or the OAuth nonce."""
    live = conn is not None and conn.status in ("active", "needs_attention")
    return {
        "provider": PROVIDER,
        "connected": live,
        "status": conn.status if conn and conn.status != "pending" else "disconnected",
        "account_email": conn.account_email if live and conn else None,
        "direction": conn.direction if conn else "two_way",
        "calendar_id": conn.calendar_id if conn else "primary",
        "last_sync_at": conn.last_sync_at.isoformat() if conn and conn.last_sync_at else None,
        "last_sync_summary": conn.last_sync_summary if conn else None,
        "last_error": conn.last_error if live and conn else None,
        "needs_attention": bool(conn and conn.status == "needs_attention"),
    }


def status(db: Session, *, user_id: uuid.UUID) -> dict[str, Any]:
    return {
        "configured": google.configured() and encryption_configured(),
        "google": serialize(get_connection(db, user_id=user_id)),
        "outlook": {"provider": "outlook", "available": False},
    }


PROOF_TYP = "calendar_oauth_proof"


def nonce_cookie_value(nonce: str) -> str:
    """What the browser must present at the callback: binds the consent to the browser that
    started it (stops login-CSRF, where an attacker's connect URL is completed by a victim).
    A signed, short-lived token over the nonce, so it cannot be minted without the server secret."""
    claims = {
        "typ": PROOF_TYP,
        "nonce": nonce,
        "exp": _now() + timedelta(seconds=STATE_TTL_SECONDS),
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def proof_matches(proof: str | None, nonce: str) -> bool:
    if not proof:
        return False
    try:
        claims = jwt.decode(proof, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError:
        return False
    return claims.get("typ") == PROOF_TYP and secrets.compare_digest(
        str(claims.get("nonce", "")), nonce
    )


def start_connect(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, provider: str
) -> tuple[dict[str, Any], str]:
    check_provider(provider)
    if not (google.configured() and encryption_configured()):
        raise CalendarSyncNotConfigured()
    conn = get_connection(db, user_id=user_id, lock=True)
    if conn is None:
        conn = CalendarConnection(
            user_id=user_id, startup_id=startup_id, provider=PROVIDER, status="pending"
        )
        db.add(conn)
    nonce = secrets.token_urlsafe(24)
    conn.oauth_nonce = nonce
    conn.nonce_expires_at = _now() + timedelta(seconds=STATE_TTL_SECONDS)
    conn.startup_id = startup_id
    db.flush()
    result = {
        "auth_url": google.build_auth_url(sign_state(user_id, startup_id, nonce)),
        "expires_in": STATE_TTL_SECONDS,
    }
    return result, nonce_cookie_value(nonce)


def complete_callback(
    db: Session,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    browser_proof: str | None,
) -> str | None:
    """Finish the OAuth round trip. Returns None on success, or an error code for `?error=`.

    The route is public (Google redirects the browser here), so everything is derived from
    the signed state and the single-use nonce, never from a session.
    """
    if error or not code:
        return ERR_DENIED
    try:
        claims = read_state(state or "")
        user_id, startup_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except (TokenInvalid, ValueError, KeyError):
        return ERR_STATE
    conn = get_connection(db, user_id=user_id, lock=True)
    if (
        conn is None
        or not conn.oauth_nonce
        or not secrets.compare_digest(conn.oauth_nonce, str(claims["nonce"]))
        or conn.nonce_expires_at is None
        or conn.nonce_expires_at < _now()
        or conn.startup_id != startup_id
    ):
        return ERR_STATE
    proof_ok = proof_matches(browser_proof, conn.oauth_nonce)
    conn.oauth_nonce = None  # single use, whatever happens next
    conn.nonce_expires_at = None
    if not proof_ok:
        return ERR_STATE
    member = (
        db.query(Membership)
        .filter_by(user_id=user_id, startup_id=startup_id, status=MembershipStatus.active)
        .first()
    )
    if member is None:
        return ERR_MEMBER
    try:
        tokens = google.exchange_code(code)
        access = str(tokens["access_token"])
        info = google.userinfo(access)
    except (google.GoogleAuthError, google.GoogleApiError, KeyError):
        return ERR_EXCHANGE
    refresh = tokens.get("refresh_token")
    new_email = str(info.get("email", ""))[:254] or None
    if not refresh and (not conn.refresh_ciphertext or conn.account_email != new_email):
        return ERR_NO_REFRESH
    if conn.account_email and new_email != conn.account_email:
        # A different Google account: the old account's event ids and sync token are meaningless.
        db.query(CalendarExternalEvent).filter_by(connection_id=conn.id).delete()
        db.query(CalendarEventLink).filter_by(connection_id=conn.id).delete()
        conn.refresh_ciphertext = None
    conn.access_ciphertext = encrypt_token(access)
    if refresh:
        conn.refresh_ciphertext = encrypt_token(str(refresh))
    conn.access_expires_at = _now() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    conn.scope = str(tokens.get("scope", ""))[:500]
    conn.account_email = new_email
    conn.status = "active"
    conn.sync_token = None
    conn.last_error = None
    db.flush()
    write_audit(
        db,
        "calendar.sync.connected",
        actor_user_id=user_id,
        startup_id=startup_id,
        entity_type="calendar_connection",
        entity_id=conn.id,
    )
    job_dispatcher.enqueue(db, JOB_SYNC, {"connection_id": str(conn.id)}, startup_id)
    return None


def mark_needs_attention(conn: CalendarConnection) -> None:
    conn.status = "needs_attention"
    conn.last_error = RECONNECT_MESSAGE


def access_token(db: Session, conn: CalendarConnection) -> str:
    """A valid access token, refreshed under the row lock when it is about to expire.

    Raises GoogleAuthError after flagging the connection `needs_attention` when Google rejects
    the refresh token (consent withdrawn, or the 7-day expiry of an app in Testing, D-CAL-3).
    """
    if conn.refresh_ciphertext is None or conn.access_ciphertext is None:
        mark_needs_attention(conn)
        raise google.GoogleAuthError("no_token")
    exp = conn.access_expires_at
    if exp is not None and exp - _now() > timedelta(seconds=60):
        return decrypt_token(conn.access_ciphertext)
    try:
        data = google.refresh_access_token(decrypt_token(conn.refresh_ciphertext))
    except google.GoogleAuthError:
        mark_needs_attention(conn)
        db.flush()
        raise
    new_access = str(data["access_token"])
    conn.access_ciphertext = encrypt_token(new_access)
    conn.access_expires_at = _now() + timedelta(seconds=int(data.get("expires_in", 3600)))
    db.flush()
    return new_access


def update_settings(
    db: Session, *, user_id: uuid.UUID, direction: str | None, calendar_id: str | None
) -> CalendarConnection:
    conn = get_connection(db, user_id=user_id, lock=True)
    if conn is None or conn.status not in ("active", "needs_attention"):
        raise NotFound()
    if direction is not None and direction not in DIRECTIONS:
        raise _validation("direction", "Pick two-way, pull only or push only.")
    new_direction = direction or conn.direction
    new_calendar = calendar_id or conn.calendar_id
    if calendar_id is not None or (direction is not None and direction != "pull_only"):
        known = {c["id"]: c for c in list_calendars(db, conn)}
        if new_calendar not in known:
            raise _validation("calendar_id", "That calendar isn't on your Google account.")
        if new_direction != "pull_only" and not known[new_calendar]["writable"]:
            raise _validation(
                "calendar_id",
                "That calendar is read only. Pick another calendar or choose pull only.",
            )
    if direction is not None:
        conn.direction = direction
    if calendar_id is not None and calendar_id != conn.calendar_id:
        # A different calendar means a different event set and sync token: start clean.
        conn.calendar_id = calendar_id
        conn.sync_token = None
        db.query(CalendarExternalEvent).filter_by(connection_id=conn.id).delete()
        db.query(CalendarEventLink).filter_by(connection_id=conn.id).delete()
    db.flush()
    return conn


def list_calendars(db: Session, conn: CalendarConnection) -> list[dict[str, Any]]:
    try:
        items = google.list_calendars(access_token(db, conn))
    except google.GoogleAuthError as exc:
        raise AppError("CALENDAR_RECONNECT_NEEDED", RECONNECT_MESSAGE, 409) from exc
    except google.GoogleApiError as exc:
        raise AppError(
            "CALENDAR_PROVIDER_ERROR", "Google is not responding. Try again.", 502
        ) from exc
    return [
        {
            "id": c["id"],
            "name": c.get("summary") or c["id"],
            "primary": bool(c.get("primary")),
            "writable": c.get("accessRole", "reader") in ("owner", "writer"),
        }
        for c in items
        if c.get("id")
    ]


def wipe(db: Session, conn: CalendarConnection) -> int:
    """Revoke at Google (best effort: a dead token must not trap the user), drop every token and
    imported row, mark disconnected. Returns the number of imported events removed."""
    try:
        if conn.refresh_ciphertext:
            google.revoke(decrypt_token(conn.refresh_ciphertext))
    except AppError as exc:
        # Unreadable or unconfigured token: nothing to revoke, and the wipe below still proceeds.
        log.info(f"[calendar] revoke skipped for connection {conn.id}: {exc.code}")
    removed = db.query(CalendarExternalEvent).filter_by(connection_id=conn.id).delete()
    db.query(CalendarEventLink).filter_by(connection_id=conn.id).delete()
    conn.status = "disconnected"
    conn.access_ciphertext = conn.refresh_ciphertext = None
    conn.access_expires_at = conn.sync_token = conn.scope = conn.account_email = None
    conn.oauth_nonce = conn.nonce_expires_at = None
    conn.last_error = None
    conn.last_sync_summary = None
    db.flush()
    return int(removed)


def disconnect(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID) -> dict[str, Any]:
    conn = get_connection(db, user_id=user_id, lock=True)
    if conn is None or conn.status in ("pending", "disconnected"):
        raise NotFound()
    removed = wipe(db, conn)
    write_audit(
        db,
        "calendar.sync.disconnected",
        actor_user_id=user_id,
        startup_id=startup_id,
        entity_type="calendar_connection",
        entity_id=conn.id,
    )
    return {"disconnected": True, "imported_events_removed": removed}
