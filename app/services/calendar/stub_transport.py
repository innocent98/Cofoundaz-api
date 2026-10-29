"""In-process fake of Google OAuth + Calendar for tests and live e2e (CALENDAR_PROVIDER_STUB).

Reachable only outside production (see google.calendar_http_client). Behaviours the tests drive:
  * an auth code containing "bad" -> 400 invalid_grant; any other code -> tokens;
  * a refresh token containing "revoked" -> 400 invalid_grant (the user withdrew consent);
  * `stub_set_events` seeds what the user's Google calendar holds; a list call with a
    syncToken returns only what changed since that token, an unknown token -> 410.
No real Google endpoint is ever called, and no real credential is read.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

STATE: dict[str, Any] = {}


def stub_reset(*, seed: bool = False) -> None:
    STATE.clear()
    STATE.update(
        {
            "events": {},  # id -> google event resource
            "version": 0,
            "changes": [],  # (version, event id)
            "seq": 0,
            "email": "founder.stub@gmail.example",
            "refreshes": 0,
            "revoked": [],
            "calls": [],
        }
    )
    if seed:
        _seed_defaults()


def _seed_defaults() -> None:
    """What a stub user's Google calendar holds on a fresh server: a timed and an all-day event."""
    day = datetime.now(UTC).date()
    stub_set_events(
        [
            {
                "id": "stubseed1",
                "status": "confirmed",
                "summary": "Investor coffee (from Google)",
                "start": {
                    "dateTime": f"{day + timedelta(days=1)}T10:00:00",
                    "timeZone": "Africa/Lagos",
                },
                "end": {
                    "dateTime": f"{day + timedelta(days=1)}T11:00:00",
                    "timeZone": "Africa/Lagos",
                },
            },
            {
                "id": "stubseed2",
                "status": "confirmed",
                "summary": "Family wedding (from Google)",
                "start": {"date": str(day + timedelta(days=3))},
                "end": {"date": str(day + timedelta(days=4))},
            },
        ]
    )


def stub_set_events(items: list[dict[str, Any]]) -> None:
    for item in items:
        _put(item)


def _put(item: dict[str, Any]) -> dict[str, Any]:
    STATE["version"] += 1
    item = {**item, "etag": f'"v{STATE["version"]}"'}
    STATE["events"][item["id"]] = item
    STATE["changes"].append((STATE["version"], item["id"]))
    return item


def _json(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


def _form(request: httpx.Request) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(request.content.decode()))


def _token(request: httpx.Request) -> httpx.Response:
    form = _form(request)
    if form.get("grant_type") == "refresh_token":
        if "revoked" in form.get("refresh_token", ""):
            return _json(400, {"error": "invalid_grant"})
        STATE["refreshes"] += 1
        return _json(
            200,
            {
                "access_token": f"stub-access-r{STATE['refreshes']}",
                "expires_in": 3600,
                "scope": "stub",
                "token_type": "Bearer",  # nosec B105 - fake token for the in-process Google stub
            },
        )
    if "bad" in form.get("code", ""):
        return _json(400, {"error": "invalid_grant"})
    return _json(
        200,
        {
            "access_token": "stub-access-0",  # nosec B105 - fake token for the in-process Google stub
            "refresh_token": "stub-refresh-token",  # nosec B105 - fake token for the in-process Google stub
            "expires_in": 3600,
            "scope": "openid email calendar.events calendar.calendarlist.readonly",
            "token_type": "Bearer",  # nosec B105 - fake token for the in-process Google stub
        },
    )


def _events(request: httpx.Request, path: str) -> httpx.Response:
    m = re.match(
        r"^/calendar/v3/calendars/([^/]+)/events(?:/([^/]+))?$",
        request.url.raw_path.decode().split("?")[0],
    )
    if m is None:
        return _json(404, {})
    ev_id = m.group(2)
    method = request.method
    if method == "GET":
        sync = request.url.params.get("syncToken")
        since = 0
        if sync:
            if not sync.startswith("sync-"):
                return _json(410, {"error": {"code": 410}})
            since = int(sync.split("-", 1)[1])
        ids = {i for v, i in STATE["changes"] if v > since}
        items = [STATE["events"][i] for i in STATE["events"] if i in ids]
        if not sync:
            items = [e for e in items if e.get("status") != "cancelled"]
        return _json(200, {"items": items, "nextSyncToken": f"sync-{STATE['version']}"})
    body = json.loads(request.content) if request.content else {}
    if method == "POST":
        STATE["seq"] += 1
        return _json(200, _put({**body, "id": f"stubgev{STATE['seq']}", "status": "confirmed"}))
    if method == "PATCH" and ev_id in STATE["events"]:
        return _json(200, _put({**STATE["events"][ev_id], **body}))
    if method == "DELETE":
        if ev_id in STATE["events"]:
            _put({**STATE["events"][ev_id], "status": "cancelled"})
        return httpx.Response(204)
    return _json(404, {"error": {"code": 404}})


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    STATE["calls"].append((request.method, path))
    if request.url.host == "oauth2.googleapis.com":
        if path == "/revoke":
            STATE["revoked"].append(_form(request).get("token"))
            return _json(200, {})
        return _token(request)
    if path == "/v1/userinfo":
        return _json(200, {"email": STATE["email"], "sub": "stub-sub"})
    if path == "/calendar/v3/users/me/calendarList":
        return _json(
            200,
            {
                "items": [
                    {
                        "id": "primary",
                        "summary": STATE["email"],
                        "primary": True,
                        "accessRole": "owner",
                    },
                    {
                        "id": "team@group.calendar.stub",
                        "summary": "Team calendar",
                        "primary": False,
                        "accessRole": "writer",
                    },
                    {
                        "id": "holidays#stub@group.v.calendar.google.com",
                        "summary": "Holidays",
                        "primary": False,
                        "accessRole": "reader",
                    },
                ]
            },
        )
    if path.startswith("/calendar/v3/calendars/"):
        if request.headers.get("authorization", "").endswith("expired"):
            return _json(401, {"error": {"code": 401}})
        return _events(request, path)
    return _json(404, {})


def stub_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)


stub_reset(seed=True)
