"""Sign in / sign up with Google. See app/services/auth/google_signin.py for the flow.

Mounted under /auth/oauth/google so maintenance mode keeps sign-in open (its allow-list covers
`/auth/oauth`); creating a NEW account during maintenance is refused inside the callback.
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.models.enums import MfaType, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import GoogleExchangeRequest, GoogleStartRequest
from app.services.auth import google_signin as svc
from app.services.auth.mfa import issue_mfa_ticket
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie

router = APIRouter(prefix="/oauth/google")

PROOF_COOKIE = "cfz_google_signin"
PROOF_COOKIE_PATH = f"{settings.API_V1_STR}/auth/oauth/google"


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _fe_url(query: dict[str, str | None]) -> str:
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    params = {k: v for k, v in query.items() if v}
    # The ticket rides in the fragment: it never reaches a server log, analytics or a Referer.
    ticket = params.pop("ticket", None)
    url = f"{base}/auth/google/callback"
    if params:
        url += f"?{urlencode(params)}"
    return f"{url}#ticket={ticket}" if ticket else url


@router.post("/start")
def start(
    payload: GoogleStartRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Returns the Google consent URL; the FE navigates the browser to it."""
    if svc.rate_limited(_ip(request)):
        raise AppError("TOO_MANY_ATTEMPTS", "Too many attempts. Try again in a few minutes.", 429)
    auth_url, proof = svc.start(db, invite_token=payload.invite_token, next_path=payload.next)
    response.set_cookie(
        PROOF_COOKIE,
        proof,
        max_age=svc.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite="lax",
        path=PROOF_COOKIE_PATH,
    )
    return success_response({"auth_url": auth_url, "expires_in": svc.STATE_TTL_SECONDS})


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    cfz_google_signin: str | None = Cookie(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Public: Google redirects the browser here. Always ends in a redirect to a FIXED FE page
    (`/auth/google/callback`), never to a caller-supplied URL."""
    ip = _ip(request)
    ticket: str | None
    failure: str | None
    next_path: str | None
    if svc.rate_limited(ip):
        ticket, failure, next_path = None, svc.ERR_RATE, None
    else:
        ticket, failure, next_path = svc.complete_callback(
            db, code=code, state=state, error=error, proof=cfz_google_signin, ip=ip
        )
    db.commit()
    redirect = RedirectResponse(
        _fe_url({"ticket": ticket, "error": failure, "next": next_path}), status_code=302
    )
    if (
        failure
    ):  # on success the cookie stays: /exchange needs it to bind the ticket to this browser
        redirect.delete_cookie(PROOF_COOKIE, path=PROOF_COOKIE_PATH)
    return redirect


@router.post("/exchange")
def exchange(
    payload: GoogleExchangeRequest,
    request: Request,
    response: Response,
    cfz_google_signin: str | None = Cookie(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Trade the one-time ticket for what a password login returns: a session, or the MFA step."""
    record = svc.read_ticket(payload.ticket, cfz_google_signin)
    user = (
        db.query(User).filter(User.id == uuid.UUID(record["user_id"])).first() if record else None
    )
    # A ticket issued before a suspension (or deletion) must not turn into a session.
    if (
        record is None
        or user is None
        or user.deleted_at is not None
        or user.status == UserStatus.disabled
    ):
        raise AppError("GOOGLE_TICKET_INVALID", "That sign-in has expired. Start again.", 400)
    response.delete_cookie(PROOF_COOKIE, path=PROOF_COOKIE_PATH)
    extras = {
        "is_new_user": bool(record["new_user"]),
        "invite_token": record.get("invite"),
        "next": record.get("next"),
    }
    ip = _ip(request)
    if user.mfa_type != MfaType.none:
        ticket = issue_mfa_ticket(user.id)
        db.commit()
        # nosec B105 - not a password: MFA is pending, so no access token is issued here.
        return success_response(
            {
                "mfa_required": True,
                "mfa_ticket": ticket,
                "access_token": None,
                **extras,
            }  # nosec B105
        )
    user.last_login_at = datetime.now(UTC)
    access, refresh = issue_token_pair(
        db, user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    write_audit(
        db, "auth.login.success", actor_user_id=user.id, ip=ip, details={"method": "google"}
    )
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response(
        {"access_token": access, "refresh_token": refresh, "mfa_required": False, **extras}
    )
