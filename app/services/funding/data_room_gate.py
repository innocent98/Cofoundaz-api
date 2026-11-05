"""The investor's door: link -> emailed 6-digit code -> short session (D-FUND-3, no accounts).

Security properties (each has a test):
- The link token is unguessable (256-bit HMAC) and only its hash is stored; unknown, revoked and
  expired links are one uniform 404.
- Asking for a code never reveals whether an email is the invited one: the answer is identical,
  and the mail is sent after the response.
- A code is 6 digits, single use, 10 minutes, 5 tries, stored as a keyed hash. Wrong codes also
  count against the share (10 in a row locks it for 15 minutes) and the route is rate limited per
  IP in the endpoint. At most 5 codes per share per hour.
- The session is an HMAC-signed claim bound to the share and the email, 2 hours at most. It proves
  who the viewer is; it never grants access by itself: every serve re-reads the share.
"""

import base64
import binascii
import hashlib
import hmac
import html
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import false, func, true
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.core.logger import log
from app.db.models.data_room import DataRoomChallenge, DataRoomShare
from app.db.models.startup import Startup
from app.platform.email import EmailMessage, get_email_sender
from app.services.documents.links import matches, plausible, token_hash
from app.services.funding.data_room_shares import status_of

CODE_TTL = timedelta(minutes=10)
CODE_MAX_TRIES = 5
CODES_PER_HOUR = 5
LOCK_AFTER_FAILURES = 10
LOCK_FOR = timedelta(minutes=15)
SESSION_TTL = timedelta(hours=2)
TICKET_TTL_SECONDS = 120


class InvalidClaim(Exception):  # noqa: N818
    """A session or ticket that is malformed, tampered with, expired or for another purpose."""


# ---- signed claims (session, ticket) ----


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(purpose: str, body: str) -> bytes:
    key = hmac.new(
        settings.SECRET_KEY.encode(), f"cofoundaz.data-room.{purpose}.v1".encode(), hashlib.sha256
    ).digest()
    return hmac.new(key, body.encode(), hashlib.sha256).digest()


def sign_claims(purpose: str, claims: dict[str, Any], *, expires_at: float) -> str:
    body = _b64(json.dumps({**claims, "e": int(expires_at)}, separators=(",", ":")).encode())
    return f"{body}.{_b64(_mac(purpose, body))}"


def verify_claims(purpose: str, token: str, *, now: float | None = None) -> dict[str, Any]:
    try:
        body, mac = token.split(".", 1)
        if not hmac.compare_digest(_unb64(mac), _mac(purpose, body)):
            raise InvalidClaim
        claims = json.loads(_unb64(body))
        if not isinstance(claims, dict) or int(claims["e"]) <= (now or time.time()):
            raise InvalidClaim
    except InvalidClaim:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidClaim from exc
    return claims


def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    key = f"cofoundaz-dr-ip:{settings.SECRET_KEY}".encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()


def _code_hash(challenge_id: uuid.UUID, code: str) -> str:
    key = f"cofoundaz-dr-code:{settings.SECRET_KEY}".encode()
    return hmac.new(key, f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


# ---- resolving a link ----


def resolve_share(db: Session, token: str, *, lock: bool = False) -> DataRoomShare:
    """The live share behind a link, else the one uniform 404 (unknown, tampered, revoked, expired
    all look the same)."""
    if not plausible(token):
        raise NotFound()
    q = db.query(DataRoomShare).filter_by(token_hash=token_hash(token))
    share = (q.with_for_update() if lock else q).first()
    if share is None or not matches(share.token_hash, token) or status_of(share) != "active":
        raise NotFound()
    return share


def _too_many() -> AppError:
    return AppError("TOO_MANY_ATTEMPTS", "Too many attempts. Try again in a few minutes.", 429)


# ---- asking for a code ----


def request_code(
    db: Session, share: DataRoomShare, email: str, *, ip: str | None
) -> tuple[str, str, str] | None:
    """Record one code request and return (to, code, company) for the mail to send after the
    response, or None when nothing should be sent. The caller answers 202 identically in every
    case. The share must have been loaded with a row lock.

    Every request, invited address or not, inserts exactly one challenge row and counts against
    the hourly cap, so the work done and the cap do not depend on the email. An uninvited address
    gets an already-consumed decoy row; over the cap nothing is sent and the answer is unchanged.
    Only a locked share answers differently (429), and that does not depend on the email either.
    """
    now = datetime.now(UTC)
    if share.locked_until is not None and share.locked_until > now:
        raise _too_many()
    recent = (
        db.query(func.count(DataRoomChallenge.id))
        .filter(
            DataRoomChallenge.share_id == share.id,
            DataRoomChallenge.created_at > now - timedelta(hours=1),
        )
        .scalar()
    )
    if recent >= CODES_PER_HOUR:
        return None
    invited = hmac.compare_digest(email.strip().lower().encode(), share.email.encode())
    # One live code at a time: a code for the invited address replaces the previous one.
    db.query(DataRoomChallenge).filter(
        DataRoomChallenge.share_id == share.id,
        DataRoomChallenge.consumed_at.is_(None),
        true() if invited else false(),  # same statement either way; only the invited one matches
    ).update({"consumed_at": now}, synchronize_session=False)
    code = f"{secrets.randbelow(1_000_000):06d}"
    cid = uuid.uuid4()
    db.add(
        DataRoomChallenge(
            id=cid,
            share_id=share.id,
            code_hash=_code_hash(cid, code),
            expires_at=now + CODE_TTL,
            consumed_at=None if invited else now,
            ip_hash=ip_hash(ip),
        )
    )
    company = db.query(Startup.name).filter(Startup.id == share.startup_id).scalar() or "A founder"
    db.flush()
    return (share.email, code, company) if invited else None


def send_code_email(to: str, code: str, company: str) -> None:
    try:
        get_email_sender().send(
            EmailMessage(
                to=to,
                subject="Your data room code",
                html=(
                    f"<p>Your code to open the <strong>{html.escape(company)}</strong> data room "
                    f"is <strong>{code}</strong>.</p>"
                    "<p>It works once and expires in 10 minutes. If you didn't ask for it, "
                    "ignore this email.</p>"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - never surfaces: the response is identical anyway
        log.warning(f"data room code email failed: {exc!r}")


# ---- proving the email ----


def verify_code(db: Session, share: DataRoomShare, email: str, code: str) -> tuple[str, datetime]:
    """Exchange email + code for a session. Every failure is the same 401 and counts against the
    share. `share` must have been loaded with a row lock so guesses are serialised."""
    now = datetime.now(UTC)
    if share.locked_until is not None and share.locked_until > now:
        raise _too_many()
    challenge = (
        db.query(DataRoomChallenge)
        .filter(
            DataRoomChallenge.share_id == share.id,
            DataRoomChallenge.consumed_at.is_(None),
            DataRoomChallenge.expires_at > now,
        )
        .order_by(DataRoomChallenge.created_at.desc())
        .first()
    )
    invalid = AppError("INVALID_CODE", "That code didn't work. Check it or ask for a new one.", 401)
    if challenge is None:
        # Nothing to guess against: not counted, so a stranger holding only the link cannot lock
        # the real investor out.
        raise invalid
    challenge.attempts += 1
    right_code = hmac.compare_digest(
        challenge.code_hash.encode(), _code_hash(challenge.id, code).encode()
    )
    right_email = hmac.compare_digest(email.strip().lower().encode(), share.email.encode())
    ok = right_code and right_email
    if ok:
        challenge.consumed_at = now
    elif challenge.attempts >= CODE_MAX_TRIES:
        challenge.consumed_at = now
    if not ok:
        share.failed_attempts += 1
        if share.failed_attempts >= LOCK_AFTER_FAILURES:
            share.locked_until = now + LOCK_FOR
            share.failed_attempts = 0
        db.commit()  # the failure must persist even though we raise
        raise invalid
    share.failed_attempts = 0
    share.locked_until = None
    expires = now + SESSION_TTL
    if share.expires_at is not None:
        expires = min(expires, share.expires_at)
    token = sign_claims(
        "session", {"sid": str(share.id), "em": share.email}, expires_at=expires.timestamp()
    )
    db.flush()
    return token, expires


def session_email(share: DataRoomShare, session_token: str | None) -> str:
    """The verified viewer email for a request, or 401. The link itself is already valid (the
    share was resolved), so a 401 here leaks nothing."""
    unauth = AppError("SESSION_REQUIRED", "Enter the code we emailed you to continue.", 401)
    if not session_token:
        raise unauth
    try:
        claims = verify_claims("session", session_token)
    except InvalidClaim:
        raise unauth from None
    if claims.get("sid") != str(share.id) or claims.get("em") != share.email:
        raise unauth
    return share.email


# ---- short-lived content tickets ----


def issue_ticket(share: DataRoomShare, item_id: uuid.UUID, view_id: uuid.UUID) -> str:
    return sign_claims(
        "ticket",
        {"sid": str(share.id), "iid": str(item_id), "vid": str(view_id), "em": share.email},
        expires_at=time.time() + TICKET_TTL_SECONDS,
    )


def read_ticket(share: DataRoomShare, item_id: uuid.UUID, ticket: str) -> uuid.UUID | None:
    """The view id a valid ticket for this share + item carries, else None."""
    try:
        claims = verify_claims("ticket", ticket)
        if (
            claims.get("sid") != str(share.id)
            or claims.get("iid") != str(item_id)
            or claims.get("em") != share.email
        ):
            return None
        return uuid.UUID(claims["vid"])
    except (InvalidClaim, ValueError, KeyError):
        return None
