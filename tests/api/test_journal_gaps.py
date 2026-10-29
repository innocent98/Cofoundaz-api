"""Journal PRD gaps (J1 to J15): by-date, richer list, stats, insights, prompts, passcode lock,
settings, export, permanent delete, retros, nudges."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token, get_password_hash
from app.db.models.enums import MembershipRole, RoadmapStatus
from app.db.models.job import Job
from app.db.models.journal import JournalEntry, JournalSettings, MoodLog
from app.db.models.notification import Notification
from app.db.models.roadmap import Roadmap
from app.platform.events import event_bus
from app.services.journal.encryption import encrypt_content
from app.services.journal.lock import MAX_ATTEMPTS, mint_token
from app.services.notifications import registry
from app.worker import runner, scheduler
from app.worker.handlers import scheduled as scheduled_handlers
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)

TODAY = date.today()
PASSWORD = "pw-" + uuid.uuid4().hex  # generated per run, never a fixed secret


def _founder(db):
    u = create_user(
        db,
        email=f"jg-{uuid.uuid4().hex[:8]}@example.com",
        email_verified_at=datetime.now(UTC),
        password_hash=get_password_hash(PASSWORD),
    )
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }
    return u, s, h


def _entry(db, u, s, day, *, mood=3, stress=5, text="a first line\nand more words here"):
    e = JournalEntry(
        startup_id=s.id,
        founder_id=u.id,
        date=day,
        content_encrypted=encrypt_content(text),
        mood=mood,
        stress=stress,
    )
    db.add(e)
    db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=day, mood=mood, stress=stress))
    db.flush()
    return e


def test_nudge_handler_is_registered():
    assert scheduled_handlers.handle_journal_nudge


def _api(path):
    return f"/api/v1/journal{path}"


# ---------------------------------------------------------------- entries


def test_by_date_and_list_fields(client, db):
    u, s, h = _founder(db)
    e = _entry(db, u, s, TODAY, text="Shipped it\nreally happy about the launch")
    r = client.get(_api(f"/entries/by-date/{TODAY.isoformat()}"), headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["id"] == str(e.id)
    assert client.get(_api("/entries/by-date/2001-01-01"), headers=h).status_code == 404

    item = client.get(_api("/entries"), headers=h).json()["data"]["entries"][0]
    assert item["stress"] == 5
    assert item["word_count"] == 7
    assert item["first_line"] == "Shipped it"
    assert item["preview"].startswith("Shipped it")


def test_month_filter_months_index_and_search(client, db):
    u, s, h = _founder(db)
    _entry(db, u, s, date(2026, 8, 31), text="august words")
    _entry(db, u, s, date(2026, 9, 1), text="september runway")
    _entry(db, u, s, date(2026, 9, 2), text="september hiring")
    r = client.get(_api("/entries?month=2026-09"), headers=h).json()["data"]
    assert r["total"] == 2
    r = client.get(_api("/entries?month=2026-09&search=hiring"), headers=h).json()["data"]
    assert [i["first_line"] for i in r["entries"]] == ["september hiring"]
    r = client.get(_api("/entries?month=2026-12"), headers=h).json()["data"]
    assert r["total"] == 0
    months = client.get(_api("/entries/months"), headers=h).json()["data"]["months"]
    assert months == [{"month": "2026-09", "count": 2}, {"month": "2026-08", "count": 1}]
    bad = client.get(_api("/entries?month=2026-13"), headers=h)
    assert bad.status_code == 422


def test_date_one_day_ahead_allowed_two_rejected(client, db):
    _u, _s, h = _founder(db)
    body = {"content": "x", "mood": "good", "stress": 3}
    ok = client.post(
        _api("/entries"), json={**body, "date": (TODAY + timedelta(days=1)).isoformat()}, headers=h
    )
    assert ok.status_code == 200
    bad = client.post(
        _api("/entries"), json={**body, "date": (TODAY + timedelta(days=2)).isoformat()}, headers=h
    )
    assert bad.status_code == 422


def test_patch_stress_null_names_stress(client, db):
    u, s, h = _founder(db)
    e = _entry(db, u, s, TODAY)
    r = client.patch(_api(f"/entries/{e.id}"), json={"stress": None}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["field_errors"][0]["field"] == "stress"


# ---------------------------------------------------------------- stats / mood / insights


def test_stats(client, db):
    u, s, h = _founder(db)
    for i in range(3):  # a 3-day streak ending today
        _entry(db, u, s, TODAY - timedelta(days=i), mood=4, stress=3)
    _entry(db, u, s, TODAY - timedelta(days=40), mood=1, stress=8)
    d = client.get(_api(f"/stats?today={TODAY.isoformat()}"), headers=h).json()["data"]
    assert d["total_entries"] == 4
    assert d["current_streak_days"] == 3
    assert d["longest_streak_days"] == 3
    assert d["wrote_today"] is True
    assert len(d["weeks"]) == 12
    assert d["average_mood"] in {"good", "okay", "meh", "rough", "great"}


def test_stats_empty(client, db):
    _u, _s, h = _founder(db)
    d = client.get(_api("/stats"), headers=h).json()["data"]
    assert d["total_entries"] == 0
    assert d["average_mood"] is None
    assert d["current_streak_days"] == 0
    assert d["wrote_today"] is False


def test_mood_range_and_annotations(client, db):
    u, s, h = _founder(db)
    _entry(db, u, s, TODAY, mood=4)
    ph = create_phase(db, create_roadmap(db, s))
    create_milestone(db, ph, title="Launch week", status=RoadmapStatus.done)
    r = client.get(_api("/mood?range=4w"), headers=h).json()["data"]
    assert len(r["points"]) == 1
    assert [a["label"] for a in r["annotations"]] == ["Completed: Launch week"]
    assert client.get(_api("/mood?range=9y"), headers=h).status_code == 422


def test_insights_need_entries_then_weekday_pattern(client, db):
    u, s, h = _founder(db)
    d = client.get(_api("/insights"), headers=h).json()["data"]
    assert d["insights"] == [] and d["entries_needed"] == 5
    # Mondays stress 9, other days 2: a clear weekday pattern.
    day = TODAY - timedelta(days=60)
    n = 0
    while n < 40:
        _entry(db, u, s, day, mood=3, stress=9 if day.weekday() == 0 else 2)
        day += timedelta(days=1)
        n += 1
    d = client.get(_api("/insights"), headers=h).json()["data"]
    assert any(i["kind"] == "weekday_stress" for i in d["insights"])
    assert d["supportive_card"] is None


def test_supportive_card_shows_then_dismissal_hides_it(client, db):
    u, s, h = _founder(db)
    for i in range(10):
        _entry(db, u, s, TODAY - timedelta(days=i), mood=1 if i % 2 else 2, stress=7)
    d = client.get(_api("/insights"), headers=h).json()["data"]
    assert d["supportive_card"]["title"].startswith("It looks like")
    assert client.post(_api("/insights/supportive/dismiss"), headers=h).status_code == 200
    d = client.get(_api("/insights"), headers=h).json()["data"]
    assert d["supportive_card"] is None


def test_supportive_card_absent_when_a_good_day_in_window(client, db):
    u, s, h = _founder(db)
    for i in range(10):
        _entry(db, u, s, TODAY - timedelta(days=i), mood=2, stress=7)
    _entry(db, u, s, TODAY - timedelta(days=12), mood=5)
    day5 = db.query(JournalEntry).filter_by(date=TODAY - timedelta(days=5)).one()
    day5.mood = 4
    db.flush()
    d = client.get(_api("/insights"), headers=h).json()["data"]
    assert d["supportive_card"] is None


# ---------------------------------------------------------------- prompts / settings


def test_prompt_dismiss_and_library(client, db):
    _u, _s, h = _founder(db)
    assert client.get(_api("/prompts/today"), headers=h).json()["data"]["dismissed"] is False
    assert client.post(_api("/prompts/today/dismiss"), headers=h).status_code == 200
    assert client.get(_api("/prompts/today"), headers=h).json()["data"]["dismissed"] is True
    lib = client.get(_api("/prompts/library"), headers=h).json()["data"]["themes"]
    assert set(lib) == {"decisions", "energy", "team", "wins"}
    one = client.get(_api("/prompts/library?theme=wins"), headers=h).json()["data"]["themes"]
    assert list(one) == ["wins"]
    assert client.get(_api("/prompts/library?theme=nope"), headers=h).status_code == 422


def test_settings_roundtrip_and_unknown_field_rejected(client, db):
    _u, _s, h = _founder(db)
    d = client.get(_api("/settings"), headers=h).json()["data"]
    assert d == {
        "passcode_enabled": False,
        "locked_until": None,
        "daily_prompt_notifications": False,
        "evening_reminder": False,
    }
    r = client.put(_api("/settings"), json={"evening_reminder": True}, headers=h).json()["data"]
    assert r["evening_reminder"] is True and r["daily_prompt_notifications"] is False
    assert client.put(_api("/settings"), json={"nope": 1}, headers=h).status_code == 422


# ---------------------------------------------------------------- passcode


def _set(client, h, passcode="4821", current=None):
    body = {"passcode": passcode}
    if current:
        body["current_passcode"] = current
    return client.put(_api("/passcode"), json=body, headers=h)


def test_passcode_locks_content_routes_until_unlocked(client, db):
    u, s, h = _founder(db)
    e = _entry(db, u, s, TODAY)
    token = _set(client, h).json()["data"]["token"]
    for method, path in [
        ("get", "/entries"),
        ("get", f"/entries/{e.id}"),
        ("get", "/stats"),
        ("get", "/export"),
        ("get", "/mood"),
        ("get", "/retros"),
        ("get", "/prompts/today"),
    ]:
        r = getattr(client, method)(_api(path), headers=h)
        assert r.status_code == 423, path
        assert r.json()["error"]["code"] == "JOURNAL_LOCKED"
    ok = client.get(_api("/entries"), headers={**h, "X-Journal-Token": token})
    assert ok.status_code == 200
    # Settings stay readable while locked, and show the lock.
    assert client.get(_api("/settings"), headers=h).json()["data"]["passcode_enabled"] is True


def test_unlock_wrong_then_right_then_lock_kills_token(client, db):
    _u, _s, h = _founder(db)
    _set(client, h)
    bad = client.post(_api("/unlock"), json={"passcode": "0000"}, headers=h)
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "PASSCODE_INCORRECT"
    token = client.post(_api("/unlock"), json={"passcode": "4821"}, headers=h).json()["data"][
        "token"
    ]
    th = {**h, "X-Journal-Token": token}
    assert client.get(_api("/entries"), headers=th).status_code == 200
    assert client.post(_api("/lock"), headers=h).json()["data"] == {"locked": True}
    assert client.get(_api("/entries"), headers=th).status_code == 423


def test_five_wrong_tries_lock_out_even_the_right_passcode(client, db):
    _u, _s, h = _founder(db)
    _set(client, h)
    for _ in range(MAX_ATTEMPTS):
        client.post(_api("/unlock"), json={"passcode": "0000"}, headers=h)
    r = client.post(_api("/unlock"), json={"passcode": "4821"}, headers=h)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "PASSCODE_LOCKED_OUT"
    d = client.get(_api("/settings"), headers=h).json()["data"]
    assert d["locked_until"] is not None


def test_change_needs_current_and_kills_old_token(client, db):
    _u, _s, h = _founder(db)
    old = _set(client, h).json()["data"]["token"]
    no_current = _set(client, h, passcode="9999")
    assert no_current.status_code == 403
    changed = _set(client, h, passcode="9999", current="4821")
    assert changed.status_code == 200
    assert client.get(_api("/entries"), headers={**h, "X-Journal-Token": old}).status_code == 423
    new = changed.json()["data"]["token"]
    assert client.get(_api("/entries"), headers={**h, "X-Journal-Token": new}).status_code == 200


@pytest.mark.parametrize("code", ["12", "abcd", "1234567890123", "12 34"])
def test_passcode_must_be_4_to_12_digits(client, db, code):
    _u, _s, h = _founder(db)
    assert _set(client, h, passcode=code).status_code == 422


def test_remove_passcode_needs_account_password(client, db):
    u, s, h = _founder(db)
    e = _entry(db, u, s, TODAY)
    _set(client, h)
    bad = client.request("DELETE", _api("/passcode"), json={"password": "nope"}, headers=h)
    assert bad.status_code == 403
    ok = client.request("DELETE", _api("/passcode"), json={"password": PASSWORD}, headers=h)
    assert ok.status_code == 200 and ok.json()["data"]["passcode_enabled"] is False
    assert client.get(_api(f"/entries/{e.id}"), headers=h).status_code == 200


def test_forged_and_foreign_tokens_rejected(client, db):
    u, s, h = _founder(db)
    _set(client, h)
    row = db.query(JournalSettings).filter_by(founder_id=u.id).one()
    good = mint_token(row)
    import jwt

    from app.core.config import settings as cfg
    from app.core.security import create_access_token

    forged_key = jwt.encode(
        {
            "sub": str(u.id),
            "sid": str(s.id),
            "ver": row.passcode_version,
            "purpose": "journal-unlock",
            "exp": 4102444800,
        },
        "not-the-server-key",
        algorithm="HS256",
    )
    wrong_purpose = jwt.encode(
        {
            "sub": str(u.id),
            "sid": str(s.id),
            "ver": row.passcode_version,
            "purpose": "other",
            "exp": 4102444800,
        },
        f"journal-unlock:{cfg.SECRET_KEY}",
        algorithm="HS256",
    )
    no_alg = jwt.encode({"sub": str(u.id), "exp": 4102444800}, "", algorithm="none")
    # A real login (access) token for the same user must not unlock the journal either.
    login_token = create_access_token(str(u.id))
    for token in [
        "garbage",
        good + "x",
        good[:-4],
        forged_key,
        wrong_purpose,
        no_alg,
        login_token,
        "",
    ]:
        r = client.get(_api("/entries"), headers={**h, "X-Journal-Token": token})
        assert r.status_code == 423
    expired = mint_token(row, now=0)
    assert (
        client.get(_api("/entries"), headers={**h, "X-Journal-Token": expired}).status_code == 423
    )
    # A second founder's token is useless for the first.
    u2, _s2, h2 = _founder(db)
    row2 = JournalSettings(
        startup_id=_s2.id, founder_id=u2.id, passcode_hash="x", passcode_version=1
    )
    db.add(row2)
    db.flush()
    r = client.get(_api("/entries"), headers={**h, "X-Journal-Token": mint_token(row2)})
    assert r.status_code == 423


def test_unlock_without_passcode_is_409(client, db):
    _u, _s, h = _founder(db)
    r = client.post(_api("/unlock"), json={"passcode": "1234"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PASSCODE_NOT_SET"


# ---------------------------------------------------------------- export / delete


def test_export_contains_plaintext_attachment_headers(client, db):
    u, s, h = _founder(db)
    _entry(db, u, s, TODAY, text="my private line")
    r = client.get(_api("/export"), headers=h)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    body = json.loads(r.text)["data"]
    assert body["entries"][0]["content"] == "my private line"
    assert body["retros"] == []


def test_delete_requires_confirm_then_destroys_everything(client, db):
    u, s, h = _founder(db)
    _entry(db, u, s, TODAY)
    client.put(_api("/settings"), json={"evening_reminder": True}, headers=h)
    wrong = client.request("DELETE", _api(""), json={"confirm": "delete"}, headers=h)
    assert wrong.status_code == 422
    r = client.request("DELETE", _api(""), json={"confirm": "DELETE"}, headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["entries"] == 1 and r.json()["data"]["deleted"] is True
    assert client.get(_api("/entries"), headers=h).json()["data"]["total"] == 0
    assert client.get(_api("/settings"), headers=h).json()["data"]["evening_reminder"] is False


def test_delete_does_not_touch_another_founder(client, db):
    u, s, h = _founder(db)
    u2 = create_user(db, email=f"jg-{uuid.uuid4().hex[:8]}@example.com")
    create_membership(db, u2, s, role=MembershipRole.founder)
    _entry(db, u2, s, TODAY)
    _entry(db, u, s, TODAY)
    client.request("DELETE", _api(""), json={"confirm": "DELETE"}, headers=h)
    assert db.query(JournalEntry).filter_by(founder_id=u2.id).count() == 1


# ---------------------------------------------------------------- roles


@pytest.mark.parametrize(
    "role",
    [MembershipRole.team_member, MembershipRole.mentor, MembershipRole.investor],
)
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/entries/months"),
        ("GET", "/entries/by-date/2026-01-01"),
        ("GET", "/stats"),
        ("GET", "/insights"),
        ("POST", "/insights/supportive/dismiss"),
        ("POST", "/prompts/today/dismiss"),
        ("GET", "/prompts/library"),
        ("GET", "/settings"),
        ("PUT", "/settings"),
        ("PUT", "/passcode"),
        ("POST", "/unlock"),
        ("POST", "/lock"),
        ("DELETE", "/passcode"),
        ("GET", "/export"),
        ("DELETE", ""),
        ("GET", "/retros/offers"),
        ("GET", "/retros"),
        ("POST", "/retros"),
    ],
)
def test_new_routes_are_founder_only(client, db, role, method, path):
    u = create_user(
        db, email=f"jg-{uuid.uuid4().hex[:8]}@example.com", email_verified_at=datetime.now(UTC)
    )
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }
    bodies = {
        "/settings": {"evening_reminder": True},
        "/passcode": {"password": "x"} if method == "DELETE" else {"passcode": "1234"},
        "/unlock": {"passcode": "1234"},
        "": {"confirm": "DELETE"},
        "/retros": {"milestone_id": str(uuid.uuid4()), "what_worked": "x"},
    }
    body = bodies.get(path)
    r = client.request(method, _api(path), json=body, headers=h)
    assert r.status_code == 403, (path, r.text)


# ---------------------------------------------------------------- retros


def _done_milestone(db, s, title="Launch", status=RoadmapStatus.done):
    roadmap = db.query(Roadmap).filter_by(startup_id=s.id).first() or create_roadmap(db, s)
    return create_milestone(db, create_phase(db, roadmap), title=title, status=status)


def test_retro_offer_create_conflict_update_delete(client, db):
    u, s, h = _founder(db)
    m = _done_milestone(db, s)
    offers = client.get(_api("/retros/offers"), headers=h).json()["data"]["offers"]
    assert [o["milestone_id"] for o in offers] == [str(m.id)]
    assert offers[0]["prompt"] == "Run a 5-minute retro on Launch?"

    body = {
        "milestone_id": str(m.id),
        "what_worked": "shipping fast",
        "what_didnt": "",
        "what_change": "test",
    }
    r = client.post(_api("/retros"), json=body, headers=h)
    assert r.status_code == 201
    retro = r.json()["data"]
    assert retro["what_worked"] == "shipping fast" and retro["milestone_title"] == "Launch"
    assert (
        client.post(_api("/retros"), json=body, headers=h).json()["error"]["code"] == "RETRO_EXISTS"
    )
    assert client.get(_api("/retros/offers"), headers=h).json()["data"]["offers"] == []
    assert len(client.get(_api("/retros"), headers=h).json()["data"]["retros"]) == 1

    upd = client.put(
        _api(f"/retros/{retro['id']}"),
        json={"what_worked": "a", "what_didnt": "b", "what_change": "c"},
        headers=h,
    )
    assert upd.json()["data"]["what_didnt"] == "b"
    assert (
        client.get(_api(f"/retros/{retro['id']}"), headers=h).json()["data"]["what_change"] == "c"
    )
    deleted = client.delete(_api(f"/retros/{retro['id']}"), headers=h)
    assert deleted.status_code == 200
    assert client.get(_api(f"/retros/{retro['id']}"), headers=h).status_code == 404


def test_retro_content_encrypted_at_rest(client, db):
    from app.db.models.journal import JournalRetro

    _u, s, h = _founder(db)
    m = _done_milestone(db, s)
    client.post(
        _api("/retros"), json={"milestone_id": str(m.id), "what_worked": "SECRETWORDS"}, headers=h
    )
    raw = db.query(JournalRetro).one().content_encrypted
    assert raw and "SECRETWORDS" not in raw


def test_retro_rules(client, db):
    _u, s, h = _founder(db)
    todo = _done_milestone(db, s, status=RoadmapStatus.todo)
    r = client.post(
        _api("/retros"), json={"milestone_id": str(todo.id), "what_worked": "x"}, headers=h
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "MILESTONE_NOT_DONE"
    done = _done_milestone(db, s, title="Done one")
    empty = client.post(_api("/retros"), json={"milestone_id": str(done.id)}, headers=h)
    assert empty.status_code == 422
    other_ws = _founder(db)[1]
    foreign = _done_milestone(db, other_ws, title="Not yours")
    r = client.post(
        _api("/retros"), json={"milestone_id": str(foreign.id), "what_worked": "x"}, headers=h
    )
    assert r.status_code == 404


def test_dismissed_offer_stays_gone_and_can_still_be_run(client, db):
    _u, s, h = _founder(db)
    m = _done_milestone(db, s)
    assert client.post(_api(f"/retros/offers/{m.id}/dismiss"), headers=h).status_code == 200
    assert client.get(_api("/retros/offers"), headers=h).json()["data"]["offers"] == []
    assert client.get(_api("/retros"), headers=h).json()["data"]["retros"] == []
    again = client.post(_api(f"/retros/offers/{m.id}/dismiss"), headers=h)
    assert again.status_code == 200
    ran = client.post(
        _api("/retros"), json={"milestone_id": str(m.id), "what_worked": "x"}, headers=h
    )
    assert ran.status_code == 201


def test_old_milestones_are_not_offered(client, db):
    _u, s, h = _founder(db)
    m = _done_milestone(db, s)
    m.updated_at = datetime.now(UTC) - timedelta(days=45)
    db.flush()
    assert client.get(_api("/retros/offers"), headers=h).json()["data"]["offers"] == []


# ---------------------------------------------------------------- nudges (scheduler + handler)


def _drain(db):
    for _ in range(50):
        if runner.run_once(db) == 0:
            break


def test_nudges_are_opt_in_and_reminder_skips_written_days(db):
    registry.register()
    u, s, _h = _founder(db)
    now = datetime.now(UTC).replace(hour=22, minute=0)
    # Nothing enabled: nothing scheduled.
    assert scheduler.scheduler_tick(db, now=now) >= 0
    assert db.query(Job).filter_by(type=scheduler.SCHED_JOURNAL_NUDGE).count() == 0

    db.add(
        JournalSettings(
            startup_id=s.id, founder_id=u.id, evening_reminder=True, daily_prompt_notifications=True
        )
    )
    db.flush()
    scheduler.scheduler_tick(db, now=now)
    assert db.query(Job).filter_by(type=scheduler.SCHED_JOURNAL_NUDGE).count() == 2
    assert scheduler.scheduler_tick(db, now=now) == 0  # once per day
    # Wrote today before the reminder runs: only the prompt notification is delivered.
    _entry(db, u, s, scheduler._local(now).date())
    _drain(db)
    types = {n.type for n in db.query(Notification).filter_by(user_id=u.id).all()}
    assert types == {"journal.prompt.daily"}


def test_reminder_delivered_when_no_entry_and_copy_is_generic(db):
    registry.register()
    u, s, _h = _founder(db)
    db.add(JournalSettings(startup_id=s.id, founder_id=u.id, evening_reminder=True))
    db.flush()
    scheduler.scheduler_tick(db, now=datetime.now(UTC).replace(hour=22, minute=0))
    _drain(db)
    n = db.query(Notification).filter_by(user_id=u.id, type="journal.reminder").one()
    assert n.title == "A quiet moment for your journal"
    assert n.data["href"] == "/journal"


def test_nudge_not_delivered_to_a_non_founder(db):
    registry.register()
    u, s, _h = _founder(db)
    tm = create_user(db, email=f"jg-{uuid.uuid4().hex[:8]}@example.com")
    create_membership(db, tm, s, role=MembershipRole.team_member)
    db.flush()
    event_bus.publish(
        db,
        "journal.reminder",
        {"startup_id": str(s.id), "founder_id": str(tm.id), "href": "/journal"},
    )
    assert db.query(Notification).filter_by(user_id=tm.id).count() == 0


def test_lockout_doubles_each_round_and_success_resets(client, db):
    u, s, h = _founder(db)
    _set(client, h)
    row = db.query(JournalSettings).filter_by(founder_id=u.id).one()
    spans = []
    for _round in range(2):
        for _ in range(MAX_ATTEMPTS):
            client.post(_api("/unlock"), json={"passcode": "0000"}, headers=h)
        db.refresh(row)
        spans.append(row.locked_until - datetime.now(UTC))
        row.locked_until = None  # time passes
        db.flush()
    assert spans[1] > spans[0] * 1.8
    ok = client.post(_api("/unlock"), json={"passcode": "4821"}, headers=h)
    assert ok.status_code == 200
    db.refresh(row)
    assert row.failed_attempts == 0


def test_token_from_one_workspace_does_not_open_another(client, db):
    u, s, h = _founder(db)
    s2 = create_startup(db, owner=u, name="Second")
    create_membership(db, u, s2, role=MembershipRole.founder)
    db.flush()
    h2 = {**h, "X-Workspace-Id": str(s2.id)}
    token_a = _set(client, h).json()["data"]["token"]
    _set(client, h2)  # same passcode version (1) in the second workspace
    r = client.get(_api("/entries"), headers={**h2, "X-Journal-Token": token_a})
    assert r.status_code == 423


def test_remove_passcode_password_guesses_are_throttled(client, db, monkeypatch):
    from app.services.auth import throttle

    _u, _s, h = _founder(db)
    _set(client, h)
    store: dict[str, int] = {}

    class _R:
        def get(self, k):
            return store.get(k)

        def incr(self, k):
            store[k] = store.get(k, 0) + 1

        def expire(self, *_a):
            pass

        def delete(self, k):
            store.pop(k, None)

    monkeypatch.setattr(throttle, "get_redis", lambda: _R())
    for _ in range(throttle.MAX_FAILURES):
        r = client.request("DELETE", _api("/passcode"), json={"password": "bad"}, headers=h)
        assert r.status_code == 403
    r = client.request("DELETE", _api("/passcode"), json={"password": PASSWORD}, headers=h)
    assert r.status_code == 429


def test_nudges_scheduled_per_workspace(db):
    registry.register()
    u, s, _h = _founder(db)
    s2 = create_startup(db, owner=u, name="Second")
    create_membership(db, u, s2, role=MembershipRole.founder)
    for st in (s, s2):
        db.add(JournalSettings(startup_id=st.id, founder_id=u.id, evening_reminder=True))
    db.flush()
    scheduler.scheduler_tick(db, now=datetime.now(UTC).replace(hour=22, minute=0))
    assert db.query(Job).filter_by(type=scheduler.SCHED_JOURNAL_NUDGE).count() == 2


def test_lockout_arithmetic_never_overflows(client, db):
    u, _s, h = _founder(db)
    _set(client, h)
    row = db.query(JournalSettings).filter_by(founder_id=u.id).one()
    row.failed_attempts = 10_000_000 - 1  # far past any realistic number of rounds
    db.flush()
    r = client.post(_api("/unlock"), json={"passcode": "0000"}, headers=h)
    assert r.status_code == 403
    db.refresh(row)
    assert row.locked_until - datetime.now(UTC) <= timedelta(hours=24, seconds=5)


def test_unlock_token_is_not_a_login_token(client, db):
    u, _s, h = _founder(db)
    token = _set(client, h).json()["data"]["token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
