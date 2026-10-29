"""Live Calendar journey #2 (Module 19): meetings, invites, the post-meeting summary, reminder
rules, the merged feed and Google sync (connect, pull, push, disconnect).

Google is the in-process fake (CALENDAR_PROVIDER_STUB): the server and this process both answer
from `app/services/calendar/stub_transport.py`, so the OAuth round trip against Google itself is
NOT exercised here -- only our side of it (signed state, callback, token storage, refresh path,
sync, revoke). Captures land in `e2e/_captures/calendar_v2/*.json`; the OAuth `state` JWT is
redacted before writing (it is a signed token).
"""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

G = "calendar_v2"
STATE_RE = re.compile(r"state=[^&\"\s]+")


def _redact(text: str) -> str:
    return STATE_RE.sub("state=<signed-state-redacted>", text)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _workspace(c: httpx.Client, make_verified_user) -> dict:
    u = make_verified_user(c)
    auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz Calendar V2"}
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]}
    )
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import calendar as _cal  # noqa: F401  (registers the calendar jobs)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _mail_to(addr: str) -> tuple[dict, str]:
    d = Path(os.environ.get("E2E_MAIL_DIR", "./var/mail-e2e"))
    for f in sorted(d.glob("*.json"), reverse=True):
        mail = json.loads(f.read_text())
        if mail.get("to") == addr:
            att = f.with_name(f.stem + ".attachments")
            ics = next(att.glob("*.ics")).read_text() if att.exists() else ""
            return mail, ics
    raise AssertionError(f"no mail to {addr}")


def test_calendar_meetings_and_sync_journey(base_url, make_verified_user, capture, capture_json):
    def cap(name: str, resp: httpx.Response) -> None:
        capture_json(G, name, {"status": resp.status_code, "body": json.loads(_redact(resp.text))})

    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        wh = _workspace(c, make_verified_user)
        today = datetime.now(UTC).date()
        day = today + timedelta(days=3)

        # ---- reminder rules (PRD 19.4) ----
        r = c.get("/api/v1/calendar/reminder-rules", headers=wh)
        assert r.status_code == 200, r.text
        assert {x["kind"]: x["offsets_minutes"] for x in r.json()["data"]["rules"]}["meeting"] == [
            15
        ]
        cap("reminder_rules_defaults", r)
        r = c.put(
            "/api/v1/calendar/reminder-rules/meeting",
            headers=wh,
            json={"offsets_minutes": [60, 15]},
        )
        assert r.status_code == 200, r.text
        cap("reminder_rule_updated", r)
        bad = c.put(
            "/api/v1/calendar/reminder-rules/meeting", headers=wh, json={"offsets_minutes": [0]}
        )
        assert bad.status_code == 422
        cap("reminder_rule_invalid", bad)

        # ---- meeting + invite (PRD 19.3) ----
        guest = f"guest-{os.urandom(3).hex()}@example.com"
        r = c.post(
            "/api/v1/calendar/meetings",
            headers=wh,
            json={
                "title": "Seed round intro",
                "event_date": day.isoformat(),
                "start_time": "10:00",
                "end_time": "11:00",
                "timezone": "Africa/Lagos",
                "location": "Lagos office",
                "video_url": "https://meet.example.com/seed-intro",
                "agenda": "1. Traction\n2. Ask",
                "attendees": [{"email": guest, "name": "Guest Investor"}],
                "assistant_enabled": True,
            },
        )
        assert r.status_code == 200, r.text
        meeting = r.json()["data"]
        assert meeting["attendees"][0]["invite_state"] == "pending"
        cap("meeting_created", r)
        bad = c.post(
            "/api/v1/calendar/meetings",
            headers=wh,
            json={
                "title": "",
                "event_date": day.isoformat(),
                "start_time": "10:00",
                "timezone": "Mars/Base",
            },
        )
        assert bad.status_code == 422
        cap("meeting_invalid", bad)

        _drain()
        mail, ics = _mail_to(guest)
        assert "invited you: Seed round intro" in mail["subject"] and "METHOD:REQUEST" in ics
        capture_json(
            G,
            "invite_email",
            {
                "to": "<guest placeholder>",
                "subject": mail["subject"],
                "html": mail["html"],
                "attachments": mail.get("attachments"),
            },
        )
        (Path(__file__).parent / "_captures" / G / "invite.ics").write_text(
            re.sub(r"mailto:\S+", "mailto:placeholder@example.invalid", ics)
        )
        r = c.get(f"/api/v1/calendar/meetings/{meeting['id']}", headers=wh)
        assert r.json()["data"]["attendees"][0]["invite_state"] == "sent"
        cap("meeting_after_invite", r)

        r = c.get("/api/v1/calendar/meetings", params={"when": "upcoming"}, headers=wh)
        assert [m["id"] for m in r.json()["data"]["meetings"]] == [meeting["id"]]
        cap("meetings_list_upcoming", r)

        # ---- assistant: notes in, summary + actions out; Add all to tasks ----
        notes = (
            "We agreed to send the data room Monday. Ada will update the cap table by Friday. " * 2
        )
        r = c.post(
            f"/api/v1/calendar/meetings/{meeting['id']}/assistant",
            headers=wh,
            json={"transcript": notes},
        )
        assert r.status_code == 202, r.text
        cap("assistant_started", r)
        dup = c.post(
            f"/api/v1/calendar/meetings/{meeting['id']}/assistant",
            headers=wh,
            json={"transcript": notes},
        )
        assert dup.status_code == 409
        cap("assistant_in_progress", dup)
        _drain()
        r = c.get(f"/api/v1/calendar/meetings/{meeting['id']}", headers=wh)
        done = r.json()["data"]
        assert done["assistant_state"] == "ready" and done["action_items"]
        cap("meeting_summarised", r)
        item = done["action_items"][0]
        r = c.post(
            f"/api/v1/calendar/meetings/{meeting['id']}/action-items/add-to-tasks",
            headers=wh,
            json={},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]["created"]) == len(done["action_items"])
        cap("action_items_added_to_tasks", r)
        assert item["id"]

        # ---- plain events + milestones + feed ----
        c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "VAT filing",
                "type": "deadline",
                "event_date": day.isoformat(),
                "all_day": True,
            },
        )
        c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "Private errand",
                "type": "task",
                "event_date": day.isoformat(),
                "visibility": "private",
            },
        )
        r = c.get(
            "/api/v1/calendar/feed",
            params={
                "date_from": today.isoformat(),
                "date_to": (today + timedelta(days=30)).isoformat(),
            },
            headers=wh,
        )
        assert r.status_code == 200, r.text
        counts = r.json()["data"]["counts"]
        assert counts["meeting"] == 1 and counts["team"] == 2
        cap("feed_all_layers", r)
        r = c.get(
            "/api/v1/calendar/feed",
            params={
                "date_from": today.isoformat(),
                "date_to": (today + timedelta(days=30)).isoformat(),
                "layers": "meeting,milestone",
            },
            headers=wh,
        )
        cap("feed_filtered", r)
        bad = c.get(
            "/api/v1/calendar/feed",
            params={
                "date_from": today.isoformat(),
                "date_to": (today + timedelta(days=500)).isoformat(),
            },
            headers=wh,
        )
        assert bad.status_code == 422
        cap("feed_range_too_long", bad)
        r = c.get("/api/v1/calendar/milestones", headers=wh)
        assert r.status_code == 200
        cap("milestones_with_days_left", r)

        # ---- Google sync (the fake) ----
        r = c.get("/api/v1/calendar/sync", headers=wh)
        assert (
            r.json()["data"]["google"]["connected"] is False
            and r.json()["data"]["configured"] is True
        )
        cap("sync_status_disconnected", r)
        out = c.post("/api/v1/calendar/sync/outlook/connect", headers=wh)
        assert out.status_code == 501
        cap("sync_outlook_not_built", out)

        r = c.post("/api/v1/calendar/sync/google/connect", headers=wh)
        assert r.status_code == 200, r.text
        auth_url = r.json()["data"]["auth_url"]
        q = parse_qs(urlparse(auth_url).query)
        assert q["redirect_uri"][0].endswith("/api/v1/calendar/sync/google/callback")
        cap("sync_connect_url", r)
        state = q["state"][0]
        proof = {
            "cfz_cal_oauth": r.cookies["cfz_cal_oauth"]
        }  # HttpOnly cookie: the browser carries it
        # The browser would visit Google, then be sent here. The callback is public.
        cb = httpx.get(
            f"{base_url}/api/v1/calendar/sync/google/callback",
            params={"code": "e2e-good-code", "state": state},
            cookies=proof,
            follow_redirects=False,
            timeout=15.0,
        )
        assert cb.status_code == 302 and cb.headers["location"].endswith(
            "/calendar/sync?connected=google"
        ), cb.headers
        capture_json(
            G,
            "sync_callback_success",
            {"status": cb.status_code, "location": cb.headers["location"]},
        )
        replay = httpx.get(
            f"{base_url}/api/v1/calendar/sync/google/callback",
            params={"code": "e2e-good-code", "state": state},
            cookies=proof,
            follow_redirects=False,
            timeout=15.0,
        )
        assert replay.headers["location"].endswith("error=invalid_state")
        capture_json(
            G,
            "sync_callback_replayed_state",
            {"status": replay.status_code, "location": replay.headers["location"]},
        )
        # Login-CSRF: a callback arriving from a browser that never started the flow is refused.
        r2 = c.post("/api/v1/calendar/sync/google/connect", headers=wh)
        state2 = parse_qs(urlparse(r2.json()["data"]["auth_url"]).query)["state"][0]
        foreign = httpx.get(
            f"{base_url}/api/v1/calendar/sync/google/callback",
            params={"code": "e2e-good-code", "state": state2},
            follow_redirects=False,
            timeout=15.0,
        )
        assert foreign.headers["location"].endswith("error=invalid_state")
        capture_json(
            G,
            "sync_callback_other_browser",
            {"status": foreign.status_code, "location": foreign.headers["location"]},
        )
        denied = httpx.get(
            f"{base_url}/api/v1/calendar/sync/google/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
            timeout=15.0,
        )
        capture_json(
            G,
            "sync_callback_denied",
            {"status": denied.status_code, "location": denied.headers["location"]},
        )

        _drain()  # the initial sync job queued by the callback
        r = c.get("/api/v1/calendar/sync", headers=wh)
        g = r.json()["data"]["google"]
        assert g["connected"] is True and g["last_sync_at"] and g["account_email"]
        assert "token" not in json.dumps(g).lower() and "ciphertext" not in json.dumps(g)
        cap("sync_status_connected", r)
        r = c.get("/api/v1/calendar/sync/google/calendars", headers=wh)
        assert r.status_code == 200
        cap("sync_calendars", r)
        r = c.patch("/api/v1/calendar/sync/google", headers=wh, json={"direction": "pull_only"})
        assert r.json()["data"]["direction"] == "pull_only"
        cap("sync_direction_changed", r)
        c.patch("/api/v1/calendar/sync/google", headers=wh, json={"direction": "two_way"})
        r = c.post("/api/v1/calendar/sync/google/sync", headers=wh)
        assert r.status_code == 200, r.text
        cap("sync_now", r)
        r = c.get(
            "/api/v1/calendar/feed",
            params={
                "date_from": today.isoformat(),
                "date_to": (today + timedelta(days=10)).isoformat(),
                "layers": "external,busy",
            },
            headers=wh,
        )
        titles = {i["title"] for i in r.json()["data"]["items"]}
        assert "Investor coffee (from Google)" in titles
        cap("feed_external_layer", r)
        r = c.delete("/api/v1/calendar/sync/google", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["disconnected"] is True
        cap("sync_disconnected", r)
        r = c.get("/api/v1/calendar/sync", headers=wh)
        assert r.json()["data"]["google"]["connected"] is False
        gone = c.delete("/api/v1/calendar/sync/google", headers=wh)
        assert gone.status_code == 404
        cap("sync_disconnect_when_not_connected", gone)
