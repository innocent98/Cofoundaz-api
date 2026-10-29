"""Shared setup for the Calendar BE tests: a workspace with a user per role, the Google client
pointed at the in-process fake (never a real endpoint), and an email sender that records."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import app.worker.handlers.calendar  # noqa: F401  (registers the calendar job handlers)
from tests.documents_helpers import headers

BASE = "/api/v1/calendar"


def h(user, startup):
    return headers(user, startup)


def connect_google(client, db, user, startup, *, code="good-code"):
    """Run connect + callback end to end against the fake; returns the callback response."""
    r = client.post(f"{BASE}/sync/google/connect", headers=h(user, startup))
    assert r.status_code == 200, r.text
    state = parse_qs(urlparse(r.json()["data"]["auth_url"]).query)["state"][0]
    cb = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )
    db.expire_all()
    return cb, state


def meeting_body(**kw):
    day = datetime.now(UTC).date().replace(day=28)
    body = {
        "title": "Investor sync",
        "event_date": day.isoformat(),
        "start_time": "10:00",
        "end_time": "11:00",
        "timezone": "Africa/Lagos",
        "attendees": [],
    }
    body.update(kw)
    return body
