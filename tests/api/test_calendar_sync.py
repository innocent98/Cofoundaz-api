"""Google Calendar sync: OAuth connect/callback, token storage, refresh, pull/push, disconnect.

Every Google call here goes to the in-process fake (CALENDAR_PROVIDER_STUB); nothing reaches
Google. The real consent round trip is verified by hand, not here.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.db.models.calendar import (
    CalendarConnection,
    CalendarEvent,
    CalendarEventLink,
    CalendarExternalEvent,
)
from app.db.models.enums import MembershipRole
from app.services.calendar import stub_transport
from app.services.calendar import sync as sync_svc
from app.services.calendar.connections import read_state, sign_state
from app.worker import runner
from tests.calendar_helpers import BASE, connect_google, h

F, TM, MENTOR, ACC = (
    MembershipRole.founder,
    MembershipRole.team_member,
    MembershipRole.mentor,
    MembershipRole.accountant,
)


def _conn(db, user):
    return db.query(CalendarConnection).filter_by(user_id=user.id).one()


def test_status_before_connecting(client, cal):
    startup, users, _ = cal
    r = client.get(f"{BASE}/sync", headers=h(users[F], startup))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["configured"] is True
    assert d["google"]["connected"] is False and d["google"]["status"] == "disconnected"
    assert d["outlook"] == {"provider": "outlook", "available": False}


def test_connect_builds_google_url_with_signed_state(client, cal):
    startup, users, _ = cal
    r = client.post(f"{BASE}/sync/google/connect", headers=h(users[F], startup))
    assert r.status_code == 200
    url = urlparse(r.json()["data"]["auth_url"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert q["redirect_uri"] == ["https://api.example.test/api/v1/calendar/sync/google/callback"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert "calendar.events" in q["scope"][0]
    claims = read_state(q["state"][0])
    assert claims["sub"] == str(users[F].id) and claims["sid"] == str(startup.id)


def test_connect_requires_google_config(client, cal, monkeypatch):
    startup, users, _ = cal
    monkeypatch.setattr("app.core.config.settings.GOOGLE_CLIENT_SECRET", None)
    r = client.post(f"{BASE}/sync/google/connect", headers=h(users[F], startup))
    assert r.status_code == 503 and r.json()["error"]["code"] == "CALENDAR_SYNC_NOT_CONFIGURED"


def test_outlook_is_reserved_not_built(client, cal):
    startup, users, _ = cal
    r = client.post(f"{BASE}/sync/outlook/connect", headers=h(users[F], startup))
    assert r.status_code == 501 and r.json()["error"]["code"] == "FEATURE_NOT_ENABLED"
    cb = client.get(f"{BASE}/sync/outlook/callback", params={"code": "x", "state": "y"})
    assert cb.status_code == 501
    assert (
        client.post(f"{BASE}/sync/nonsense/connect", headers=h(users[F], startup)).status_code
        == 404
    )


@pytest.mark.parametrize("role", [MENTOR, ACC])
def test_other_roles_cannot_use_sync(client, cal, role):
    startup, users, _ = cal
    assert client.get(f"{BASE}/sync", headers=h(users[role], startup)).status_code == 403
    assert (
        client.post(f"{BASE}/sync/google/connect", headers=h(users[role], startup)).status_code
        == 403
    )


def test_callback_stores_encrypted_tokens_and_redirects(client, db, cal):
    startup, users, _ = cal
    cb, _ = connect_google(client, db, users[F], startup)
    assert cb.status_code == 302
    assert cb.headers["location"] == "https://app.example.test/calendar/sync?connected=google"
    c = _conn(db, users[F])
    assert c.status == "active" and c.account_email == "founder.stub@gmail.example"
    assert c.oauth_nonce is None  # single use
    # Tokens are ciphertext at rest, never the raw value.
    assert c.access_ciphertext and "stub-access" not in c.access_ciphertext
    assert c.refresh_ciphertext and "stub-refresh" not in c.refresh_ciphertext
    body = client.get(f"{BASE}/sync", headers=h(users[F], startup)).json()["data"]["google"]
    assert body["connected"] is True and body["account_email"] == "founder.stub@gmail.example"
    flat = str(body)
    for secret in ("stub-access", "stub-refresh", "ciphertext", "sync_token", "nonce"):
        assert secret not in flat


def test_callback_state_is_single_use(client, db, cal):
    startup, users, _ = cal
    _, state = connect_google(client, db, users[F], startup)
    replay = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert replay.headers["location"].endswith("error=invalid_state")


@pytest.mark.parametrize("state", ["", "garbage", "a.b.c"])
def test_callback_rejects_bad_state(client, cal, state):
    r = client.get(
        f"{BASE}/sync/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert r.status_code == 302 and r.headers["location"].endswith("error=invalid_state")


def test_callback_rejects_state_signed_for_another_purpose(client, cal, db):
    import jwt

    from app.core.config import settings

    startup, users, _ = cal
    forged = jwt.encode(
        {
            "sub": str(users[F].id),
            "sid": str(startup.id),
            "nonce": "n",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )  # an access-token-shaped JWT without the calendar typ
    r = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": "c", "state": forged},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("error=invalid_state")


def test_state_is_bound_to_the_user_who_started_it(client, db, cal):
    startup, users, _ = cal
    client.post(f"{BASE}/sync/google/connect", headers=h(users[F], startup))
    # Team member has no pending row, so a state naming them (even correctly signed) fails.
    forged = sign_state(users[TM].id, startup.id, "guess")
    r = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": "good-code", "state": forged},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("error=invalid_state")
    assert db.query(CalendarConnection).filter_by(user_id=users[TM].id).count() == 0


def test_expired_nonce_is_rejected(client, db, cal):
    startup, users, _ = cal
    r = client.post(f"{BASE}/sync/google/connect", headers=h(users[F], startup))
    state = parse_qs(urlparse(r.json()["data"]["auth_url"]).query)["state"][0]
    _conn(db, users[F]).nonce_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    cb = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("error=invalid_state")


def test_user_denied_consent(client, cal):
    cb = client.get(
        f"{BASE}/sync/google/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert cb.headers["location"].endswith("error=access_denied")


def test_bad_code_leaves_nothing_connected(client, db, cal):
    startup, users, _ = cal
    cb, _ = connect_google(client, db, users[F], startup, code="bad-code")
    assert cb.headers["location"].endswith("error=exchange_failed")
    assert _conn(db, users[F]).status == "pending"


def test_member_removed_mid_flow_cannot_connect(client, db, cal):
    from app.db.models.enums import MembershipStatus
    from app.db.models.membership import Membership

    startup, users, _ = cal
    r = client.post(f"{BASE}/sync/google/connect", headers=h(users[TM], startup))
    state = parse_qs(urlparse(r.json()["data"]["auth_url"]).query)["state"][0]
    db.query(Membership).filter_by(user_id=users[TM].id).update(
        {"status": MembershipStatus.suspended}
    )
    db.flush()
    cb = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("error=not_a_member")


def test_initial_sync_job_pulls_private_events(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    assert runner.run_once(db) >= 1
    c = _conn(db, users[F])
    rows = db.query(CalendarExternalEvent).filter_by(connection_id=c.id).all()
    assert {r.title for r in rows} == {
        "Investor coffee (from Google)",
        "Family wedding (from Google)",
    }
    assert c.sync_token and c.last_sync_at and c.last_error is None


def test_teammates_see_busy_blocks_never_titles(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = datetime.now(UTC).date()
    params = {"date_from": str(day), "date_to": str(day + timedelta(days=7))}
    mine = client.get(f"{BASE}/feed", params=params, headers=h(users[F], startup)).json()["data"][
        "items"
    ]
    theirs = client.get(f"{BASE}/feed", params=params, headers=h(users[TM], startup)).json()[
        "data"
    ]["items"]
    assert {i["title"] for i in mine if i["layer"] == "external"} == {
        "Investor coffee (from Google)",
        "Family wedding (from Google)",
    }
    busy = [i for i in theirs if i["layer"] == "busy"]
    assert len(busy) == 2 and all(i["title"] == "Busy" and i["ref"] is None for i in busy)
    assert "Investor" not in str(theirs) and "wedding" not in str(theirs)


def test_incremental_sync_applies_changes_and_deletions(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    c = _conn(db, users[F])
    day = datetime.now(UTC).date()
    stub_transport.stub_set_events(
        [
            {
                "id": "stubseed1",
                "status": "confirmed",
                "summary": "Investor coffee MOVED",
                "start": {
                    "dateTime": f"{day + timedelta(days=2)}T09:00:00",
                    "timeZone": "Africa/Lagos",
                },
                "end": {
                    "dateTime": f"{day + timedelta(days=2)}T09:30:00",
                    "timeZone": "Africa/Lagos",
                },
            },
            {"id": "stubseed2", "status": "cancelled"},
            {
                "id": "newone",
                "status": "confirmed",
                "summary": "New",
                "start": {"date": str(day + timedelta(days=5))},
                "end": {"date": str(day + timedelta(days=6))},
            },
        ]
    )
    sync_svc.sync_connection(db, c.id)
    titles = {r.title for r in db.query(CalendarExternalEvent).filter_by(connection_id=c.id)}
    assert titles == {"Investor coffee MOVED", "New"}


def test_expired_sync_token_falls_back_to_full_resync(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    c = _conn(db, users[F])
    c.sync_token = "stale-token"  # the fake answers 410 for tokens it did not issue
    db.flush()
    summary = sync_svc.sync_connection(db, c.id)
    assert "error" not in summary
    assert db.query(CalendarExternalEvent).filter_by(connection_id=c.id).count() == 2


def test_push_creates_google_copy_without_double_invites(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset()
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    r = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={
            "title": "Pricing review",
            "type": "task",
            "event_date": str(day),
            "start_time": "14:00",
        },
    )
    ev_id = r.json()["data"]["id"]
    runner.run_once(db)
    link = db.query(CalendarEventLink).filter_by(event_id=ev_id).one()
    pushed = stub_transport.STATE["events"][link.external_id]
    assert pushed["summary"] == "Pricing review"
    assert pushed["start"] == {"dateTime": f"{day}T14:00:00", "timeZone": "Africa/Lagos"}
    assert pushed["extendedProperties"]["private"]["cofoundazEventId"] == ev_id
    # D-CAL-4: Cofoundaz sends its own invites, so Google must be told not to.
    assert any(m == "POST" for m, _ in stub_transport.STATE["calls"])


def test_pushed_events_do_not_come_back_as_duplicates(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset()
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Echo test", "type": "task", "event_date": str(day)},
    )
    runner.run_once(db)
    c = _conn(db, users[F])
    sync_svc.sync_connection(db, c.id)  # pulls the copy we just pushed
    assert db.query(CalendarExternalEvent).filter_by(connection_id=c.id).count() == 0


def test_editing_and_deleting_propagate_to_google(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset()
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    ev = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Draft", "type": "task", "event_date": str(day)},
    ).json()["data"]
    runner.run_once(db)
    ext = db.query(CalendarEventLink).filter_by(event_id=ev["id"]).one().external_id
    client.patch(f"{BASE}/events/{ev['id']}", headers=h(users[F], startup), json={"title": "Final"})
    runner.run_once(db)
    assert stub_transport.STATE["events"][ext]["summary"] == "Final"
    client.delete(f"{BASE}/events/{ev['id']}", headers=h(users[F], startup))
    runner.run_once(db)
    assert stub_transport.STATE["events"][ext]["status"] == "cancelled"
    assert db.query(CalendarEvent).filter_by(id=ev["id"]).count() == 0


def test_pull_only_never_pushes_and_push_only_never_pulls(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Mine", "type": "task", "event_date": str(day)},
    )
    r = client.patch(
        f"{BASE}/sync/google", headers=h(users[F], startup), json={"direction": "pull_only"}
    )
    assert r.status_code == 200 and r.json()["data"]["direction"] == "pull_only"
    runner.run_once(db)
    assert db.query(CalendarEventLink).count() == 0
    client.patch(
        f"{BASE}/sync/google", headers=h(users[F], startup), json={"direction": "push_only"}
    )
    c = _conn(db, users[F])
    sync_svc.sync_connection(db, c.id)
    assert db.query(CalendarEventLink).count() == 1


def test_direction_and_calendar_validation(client, db, cal):
    startup, users, _ = cal
    connect_google(client, db, users[F], startup)
    bad = client.patch(
        f"{BASE}/sync/google", headers=h(users[F], startup), json={"direction": "sideways"}
    )
    assert bad.status_code == 422
    unknown = client.patch(
        f"{BASE}/sync/google", headers=h(users[F], startup), json={"calendar_id": "not-mine"}
    )
    assert unknown.status_code == 422
    ro = client.patch(
        f"{BASE}/sync/google",
        headers=h(users[F], startup),
        json={"calendar_id": "holidays#stub@group.v.calendar.google.com"},
    )
    assert ro.status_code == 422 and "read only" in ro.json()["error"]["field_errors"][0]["message"]
    pull = client.patch(
        f"{BASE}/sync/google",
        headers=h(users[F], startup),
        json={"direction": "pull_only", "calendar_id": "holidays#stub@group.v.calendar.google.com"},
    )
    assert pull.status_code == 200  # '#' in the id is URL-encoded, not a fragment
    client.patch(
        f"{BASE}/sync/google",
        headers=h(users[F], startup),
        json={"calendar_id": "primary", "direction": "two_way"},
    )
    cals = client.get(f"{BASE}/sync/google/calendars", headers=h(users[F], startup)).json()["data"]
    assert cals["selected"] == "primary"
    assert {c["id"] for c in cals["calendars"]} == {
        "primary",
        "team@group.calendar.stub",
        "holidays#stub@group.v.calendar.google.com",
    }
    ok = client.patch(
        f"{BASE}/sync/google",
        headers=h(users[F], startup),
        json={"calendar_id": "team@group.calendar.stub"},
    )
    assert ok.status_code == 200 and ok.json()["data"]["calendar_id"] == "team@group.calendar.stub"


def test_expired_access_token_is_refreshed(client, db, cal):
    startup, users, _ = cal
    connect_google(client, db, users[F], startup)
    c = _conn(db, users[F])
    c.access_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    before = stub_transport.STATE["refreshes"]
    assert "error" not in sync_svc.sync_connection(db, c.id)
    assert stub_transport.STATE["refreshes"] == before + 1


def test_revoked_refresh_token_flags_needs_attention_without_raising(client, db, cal):
    from app.services.calendar.crypto import encrypt_token

    startup, users, _ = cal
    connect_google(client, db, users[F], startup)
    c = _conn(db, users[F])
    c.refresh_ciphertext = encrypt_token("revoked-by-user")
    c.access_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    assert sync_svc.sync_connection(db, c.id) == {
        "pulled": 0,
        "removed": 0,
        "pushed": 0,
        "error": "reconnect",
    }
    st = client.get(f"{BASE}/sync", headers=h(users[F], startup)).json()["data"]["google"]
    assert st["needs_attention"] is True and st["status"] == "needs_attention"
    assert "Reconnect" in st["last_error"]
    # A dead grant is not retried by the scheduler.
    assert sync_svc.sync_connection(db, c.id) == {"skipped": True}


def test_sync_now_returns_summary_and_debounces(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    r = client.post(f"{BASE}/sync/google/sync", headers=h(users[F], startup))
    assert r.status_code == 200
    assert r.json()["data"]["summary"]["pulled"] == 2
    again = client.post(f"{BASE}/sync/google/sync", headers=h(users[F], startup))
    assert again.json()["data"]["summary"] == {"skipped": True}


def test_disconnect_revokes_wipes_tokens_and_imported_events(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    r = client.delete(f"{BASE}/sync/google", headers=h(users[F], startup))
    assert r.status_code == 200 and r.json()["data"] == {
        "disconnected": True,
        "imported_events_removed": 2,
    }
    c = _conn(db, users[F])
    assert (
        c.status == "disconnected" and c.access_ciphertext is None and c.refresh_ciphertext is None
    )
    assert stub_transport.STATE["revoked"] == ["stub-refresh-token"]
    assert db.query(CalendarExternalEvent).count() == 0
    assert client.delete(f"{BASE}/sync/google", headers=h(users[F], startup)).status_code == 404
    # Reconnecting after a disconnect works on the same row.
    cb, _ = connect_google(client, db, users[F], startup)
    assert cb.headers["location"].endswith("connected=google")


def test_connections_are_per_user_not_per_workspace(client, db, cal):
    startup, users, _ = cal
    connect_google(client, db, users[F], startup)
    tm = client.get(f"{BASE}/sync", headers=h(users[TM], startup)).json()["data"]["google"]
    assert tm["connected"] is False
    assert client.delete(f"{BASE}/sync/google", headers=h(users[TM], startup)).status_code == 404


def test_unreadable_ciphertext_is_a_clean_error(client, db, cal):
    startup, users, _ = cal
    connect_google(client, db, users[F], startup)
    c = _conn(db, users[F])
    c.access_ciphertext = c.refresh_ciphertext = "not-a-fernet-token"
    c.access_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    r = client.get(f"{BASE}/sync/google/calendars", headers=h(users[F], startup))
    assert r.status_code == 500 and r.json()["error"]["code"] == "CALENDAR_CONNECTION_UNREADABLE"


def test_callback_from_another_browser_is_refused(client, db, cal):
    """Login-CSRF: an attacker's connect URL completed by a victim's browser (no proof cookie)."""
    from fastapi.testclient import TestClient

    from app.main import app

    startup, users, _ = cal
    r = client.post(f"{BASE}/sync/google/connect", headers=h(users[F], startup))
    state = parse_qs(urlparse(r.json()["data"]["auth_url"]).query)["state"][0]
    with TestClient(app) as victim:  # a different cookie jar
        cb = victim.get(
            f"{BASE}/sync/google/callback",
            params={"code": "good-code", "state": state},
            follow_redirects=False,
        )
    assert cb.headers["location"].endswith("error=invalid_state")
    assert _conn(db, users[F]).status == "pending"  # nothing was attached
    # And the nonce is burned, so the attacker's own browser cannot finish it either.
    again = client.get(
        f"{BASE}/sync/google/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert again.headers["location"].endswith("error=invalid_state")


def test_connect_sets_a_httponly_proof_cookie(client, cal):
    startup, users, _ = cal
    r = client.post(f"{BASE}/sync/google/connect", headers=h(users[F], startup))
    cookie = r.headers["set-cookie"].lower()
    assert "cfz_cal_oauth=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/api/v1/calendar/sync" in cookie


def test_reconnecting_a_different_google_account_resets_links(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset()
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "X", "type": "task", "event_date": str(day)},
    )
    runner.run_once(db)
    assert db.query(CalendarEventLink).count() == 1
    stub_transport.STATE["email"] = "someone.else@gmail.example"
    connect_google(client, db, users[F], startup)
    assert db.query(CalendarEventLink).count() == 0
    assert _conn(db, users[F]).account_email == "someone.else@gmail.example"


def test_event_deleted_in_google_is_recreated_not_stuck(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset()
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    ev = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "A", "type": "task", "event_date": str(day)},
    ).json()["data"]
    runner.run_once(db)
    link = db.query(CalendarEventLink).filter_by(event_id=ev["id"]).one()
    stub_transport.STATE["events"].pop(link.external_id)  # the user deleted our copy in Google
    client.patch(f"{BASE}/events/{ev['id']}", headers=h(users[F], startup), json={"title": "B"})
    runner.run_once(db)
    db.expire_all()
    link = db.query(CalendarEventLink).filter_by(event_id=ev["id"]).one()
    assert stub_transport.STATE["events"][link.external_id]["summary"] == "B"
    assert _conn(db, users[F]).last_error is None


def test_event_turned_private_is_removed_from_teammates_google(client, db, cal):
    startup, users, _ = cal
    stub_transport.stub_reset()
    connect_google(client, db, users[TM], startup)
    runner.run_once(db)
    day = (datetime.now(UTC) + timedelta(days=2)).date()
    ev = client.post(
        f"{BASE}/meetings",
        headers=h(users[F], startup),
        json={
            "title": "Board prep",
            "event_date": str(day),
            "start_time": "10:00",
            "attendees": [{"email": str(users[TM].email)}],
        },
    ).json()["data"]
    runner.run_once(db)
    link = db.query(CalendarEventLink).filter_by(event_id=ev["id"]).one()
    ext = link.external_id
    assert stub_transport.STATE["events"][ext]["summary"] == "Board prep"
    client.patch(
        f"{BASE}/meetings/{ev['id']}", headers=h(users[F], startup), json={"attendees": []}
    )
    runner.run_once(db)
    sync_svc.sync_connection(db, _conn(db, users[TM]).id)
    assert stub_transport.STATE["events"][ext]["status"] == "cancelled"
    assert db.query(CalendarEventLink).filter_by(event_id=ev["id"]).count() == 0


def test_removed_member_stops_syncing_and_loses_their_tokens(client, db, cal):
    from app.db.models.enums import MembershipStatus
    from app.db.models.membership import Membership

    startup, users, _ = cal
    stub_transport.stub_reset(seed=True)
    connect_google(client, db, users[TM], startup)
    runner.run_once(db)
    c = _conn(db, users[TM])
    assert db.query(CalendarExternalEvent).filter_by(connection_id=c.id).count() == 2
    db.query(Membership).filter_by(user_id=users[TM].id).update(
        {"status": MembershipStatus.suspended}
    )
    db.flush()
    out = sync_svc.sync_connection(db, c.id)
    assert out["disconnected"] == "not_a_member"
    db.refresh(c)
    assert c.status == "disconnected" and c.refresh_ciphertext is None
    assert db.query(CalendarExternalEvent).filter_by(connection_id=c.id).count() == 0
