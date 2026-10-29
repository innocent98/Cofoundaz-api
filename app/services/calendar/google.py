"""Google OAuth + Calendar v3 over httpx. Every call goes through `calendar_http_client()`, so
with CALENDAR_PROVIDER_STUB the SAME code runs against an in-process fake (never in production).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import settings
from app.core.errors import CalendarSyncNotConfigured

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105 - a URL, not a secret
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
)
TIMEOUT_SECONDS = 15.0
# The private extended property that marks an event we pushed, so a pull never re-imports it.
EXT_PROP = "cofoundazEventId"


class GoogleAuthError(Exception):
    """The refresh token (or code) was rejected: the user must reconnect."""


class GoogleGone(Exception):  # noqa: N818 - mirrors the HTTP 410 name
    """The incremental sync token expired (HTTP 410): do a full resync."""


class GoogleApiError(Exception):
    """Any other Google failure (5xx, 403, network)."""


class GoogleNotFound(GoogleApiError):  # noqa: N818
    """HTTP 404: the event or calendar is gone on Google's side."""


def calendar_http_client() -> httpx.Client:
    if settings.CALENDAR_PROVIDER_STUB and settings.ENVIRONMENT != "production":
        from app.services.calendar.stub_transport import stub_transport

        return httpx.Client(transport=stub_transport(), timeout=TIMEOUT_SECONDS)
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def _cal(calendar_id: str) -> str:
    """Calendar ids hold '#' and '@' (holiday calendars): never splice them in raw."""
    return quote(calendar_id, safe="")


def redirect_uri() -> str:
    base = settings.API_PUBLIC_URL.rstrip("/") or f"{settings.SERVER_HOST}:{settings.SERVER_PORT}"
    return f"{base}{settings.API_V1_STR}/calendar/sync/google/callback"


def configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _creds() -> tuple[str, str]:
    if not configured():
        raise CalendarSyncNotConfigured()
    return str(settings.GOOGLE_CLIENT_ID), str(settings.GOOGLE_CLIENT_SECRET)


def build_auth_url(state: str) -> str:
    client_id, _ = _creds()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # offline + consent: Google only returns a refresh token on the first consent otherwise.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _check(resp: httpx.Response, *, auth_ok: bool = True) -> dict[str, Any]:
    if resp.status_code == 410:
        raise GoogleGone()
    if resp.status_code in (400, 401) and auth_ok:
        body = _safe_json(resp)
        err = body.get("error") if isinstance(body, dict) else None
        if (
            err in ("invalid_grant", "invalid_token", "unauthorized_client")
            or resp.status_code == 401
        ):
            raise GoogleAuthError(str(err or "unauthorized"))
    if resp.status_code == 404:
        raise GoogleNotFound()
    if resp.status_code >= 400:
        raise GoogleApiError(f"google {resp.status_code}")
    return _safe_json(resp)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        with calendar_http_client() as http:
            return _check(http.post(url, data=data))
    except httpx.HTTPError as exc:
        raise GoogleApiError("network") from exc


def exchange_code(code: str) -> dict[str, Any]:
    client_id, secret = _creds()
    return _post_form(
        TOKEN_URL,
        {
            "code": code,
            "client_id": client_id,
            "client_secret": secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
    )


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    client_id, secret = _creds()
    return _post_form(
        TOKEN_URL,
        {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "refresh_token",
        },
    )


def revoke(token: str) -> None:
    """Best effort: a failed revoke must not block the local disconnect."""
    try:
        with calendar_http_client() as http:
            http.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        return


def _get(access: str, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        with calendar_http_client() as http:
            return _check(
                http.get(url, params=params, headers={"Authorization": f"Bearer {access}"})
            )
    except httpx.HTTPError as exc:
        raise GoogleApiError("network") from exc


def _send(method: str, access: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        with calendar_http_client() as http:
            resp = http.request(
                method, url, json=body, headers={"Authorization": f"Bearer {access}"}
            )
            if resp.status_code in (404, 410) and method == "DELETE":
                return {}
            return _check(resp) if resp.content else {}
    except httpx.HTTPError as exc:
        raise GoogleApiError("network") from exc


def userinfo(access: str) -> dict[str, Any]:
    return _get(access, USERINFO_URL)


def list_calendars(access: str) -> list[dict[str, Any]]:
    data = _get(access, f"{API}/users/me/calendarList", {"minAccessRole": "reader"})
    return [i for i in data.get("items", []) if isinstance(i, dict)]


def list_events(
    access: str,
    calendar_id: str,
    *,
    sync_token: str | None,
    time_min: str | None,
    time_max: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """All pages of events; returns (items, next_sync_token). Raises GoogleGone on a dead token."""
    params: dict[str, Any] = {"singleEvents": "true", "maxResults": 250, "showDeleted": "true"}
    if sync_token:
        params["syncToken"] = sync_token
    else:
        params["timeMin"], params["timeMax"] = time_min, time_max
    items: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(20):  # hard page cap: a runaway calendar must not spin the worker
        if token:
            params["pageToken"] = token
        data = _get(access, f"{API}/calendars/{_cal(calendar_id)}/events", params)
        items += [i for i in data.get("items", []) if isinstance(i, dict)]
        token = data.get("nextPageToken")
        if not token:
            return items, data.get("nextSyncToken")
    return items, None


def insert_event(access: str, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
    # sendUpdates=none: Cofoundaz sends its own invites (D-CAL-4); never double-email.
    return _send(
        "POST", access, f"{API}/calendars/{_cal(calendar_id)}/events?sendUpdates=none", body
    )


def patch_event(
    access: str, calendar_id: str, event_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    return _send(
        "PATCH",
        access,
        f"{API}/calendars/{_cal(calendar_id)}/events/{event_id}?sendUpdates=none",
        body,
    )


def delete_event(access: str, calendar_id: str, event_id: str) -> None:
    _send(
        "DELETE", access, f"{API}/calendars/{_cal(calendar_id)}/events/{event_id}?sendUpdates=none"
    )
