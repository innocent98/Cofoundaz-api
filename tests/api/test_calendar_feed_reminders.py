"""Calendar feed (PRD 19.1), reminder rules (19.4) and the scheduler scan that fires them."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models.calendar import CalendarEvent
from app.db.models.enums import CalendarEventType, MembershipRole
from app.db.models.marketing import ContentCalendarEntry
from app.db.models.notification import Notification
from app.services.calendar import reminders
from app.worker import runner, scheduler
from tests.calendar_helpers import BASE, h, meeting_body
from tests.factories import create_milestone, create_phase, create_roadmap

F, TM, MENTOR = MembershipRole.founder, MembershipRole.team_member, MembershipRole.mentor
TODAY = datetime.now(UTC).date()


def _feed(client, startup, user, d0=None, d1=None, **extra):
    params = {
        "date_from": str(d0 or TODAY),
        "date_to": str(d1 or TODAY + timedelta(days=30)),
        **extra,
    }
    return client.get(f"{BASE}/feed", params=params, headers=h(user, startup))


def test_feed_merges_every_layer(client, db, cal):
    from app.db.models.enums import ChannelKey, ContentStatus

    startup, users, _ = cal
    day = TODAY + timedelta(days=3)
    ms = create_milestone(
        db, create_phase(db, create_roadmap(db, startup)), title="Launch", due_on=day
    )
    db.add(
        ContentCalendarEntry(
            startup_id=startup.id,
            title="Post",
            channel=next(iter(ChannelKey)),
            status=ContentStatus.scheduled,
            scheduled_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=10),
        )
    )
    db.flush()
    client.post(
        f"{BASE}/meetings", headers=h(users[F], startup), json=meeting_body(event_date=str(day))
    )
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Pay VAT", "type": "deadline", "event_date": str(day)},
    )
    r = _feed(client, startup, users[F])
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["counts"]["milestone"] == 1 and d["counts"]["meeting"] == 1
    assert (
        d["counts"]["team"] == 1
        and d["counts"]["marketing"] == 1
        and d["counts"]["compliance"] == 0
    )
    m = next(i for i in d["items"] if i["layer"] == "milestone")
    assert (
        m["title"] == "Launch"
        and m["ref"] == {"type": "milestone", "id": str(ms.id)}
        and m["status"] == "not_started"
    )
    only = _feed(client, startup, users[F], layers="meeting").json()["data"]["items"]
    assert [i["layer"] for i in only] == ["meeting"]
    assert d["items"] == sorted(
        d["items"], key=lambda i: (i["date"], i["start_time"] or "", i["title"])
    )


def test_feed_range_rules(client, cal):
    startup, users, _ = cal
    assert _feed(client, startup, users[F], d1=TODAY - timedelta(days=1)).status_code == 422
    assert _feed(client, startup, users[F], d1=TODAY + timedelta(days=400)).status_code == 422
    assert _feed(client, startup, users[F], layers="nonsense").status_code == 422
    assert (
        client.get(
            f"{BASE}/feed", headers=h(users[F], startup[0] if False else startup)
        ).status_code
        == 422
    )
    assert _feed(client, startup, users[MENTOR]).status_code == 403


def test_feed_includes_multi_day_events_overlapping_the_range(client, cal):
    startup, users, _ = cal
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={
            "title": "Offsite",
            "type": "task",
            "event_date": str(TODAY - timedelta(days=2)),
            "end_date": str(TODAY + timedelta(days=1)),
            "all_day": True,
        },
    )
    items = _feed(client, startup, users[F], d0=TODAY, d1=TODAY + timedelta(days=2)).json()["data"][
        "items"
    ]
    assert [i["title"] for i in items] == ["Offsite"]


def test_end_date_cannot_precede_start(client, cal):
    startup, users, _ = cal
    r = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "x", "type": "task", "event_date": "2026-11-05", "end_date": "2026-11-04"},
    )
    assert r.status_code == 422


def test_private_events_stay_private(client, cal):
    startup, users, _ = cal
    day = str(TODAY + timedelta(days=1))
    p = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Therapy", "type": "task", "event_date": day, "visibility": "private"},
    ).json()["data"]
    assert [i["title"] for i in _feed(client, startup, users[F]).json()["data"]["items"]] == [
        "Therapy"
    ]
    assert _feed(client, startup, users[TM]).json()["data"]["items"] == []
    assert (
        client.get(f"{BASE}/events", headers=h(users[TM], startup)).json()["data"]["events"] == []
    )
    assert client.get(f"{BASE}/events/{p['id']}", headers=h(users[TM], startup)).status_code == 404
    assert (
        client.patch(
            f"{BASE}/events/{p['id']}", headers=h(users[TM], startup), json={"title": "x"}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{BASE}/events/{p['id']}", headers=h(users[TM], startup)).status_code == 404
    )


def test_event_roundtrips_new_fields(client, cal):
    startup, users, _ = cal
    r = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={
            "title": "Call",
            "type": "meeting",
            "event_date": "2026-11-03",
            "start_time": "09:30",
            "timezone": "Europe/London",
            "location": "Zoom",
            "video_url": "https://meet.example/x",
        },
    )
    d = r.json()["data"]
    assert (d["timezone"], d["location"], d["visibility"], d["end_date"]) == (
        "Europe/London",
        "Zoom",
        "team",
        None,
    )
    assert (
        client.post(
            f"{BASE}/events",
            headers=h(users[F], startup),
            json={
                "title": "x",
                "type": "task",
                "event_date": "2026-11-03",
                "timezone": "Nope/Zone",
            },
        ).status_code
        == 422
    )


def test_milestones_have_days_left_and_task_counts(client, db, cal):
    from app.db.models.enums import RoadmapStatus
    from app.db.models.roadmap import RoadmapTask

    startup, users, _ = cal
    m = create_milestone(
        db,
        create_phase(db, create_roadmap(db, startup)),
        title="M",
        due_on=TODAY + timedelta(days=5),
    )
    db.add_all(
        [
            RoadmapTask(milestone_id=m.id, title="a", status=RoadmapStatus.done),
            RoadmapTask(milestone_id=m.id, title="b", status=RoadmapStatus.todo),
        ]
    )
    db.flush()
    d = client.get(f"{BASE}/milestones", headers=h(users[F], startup)).json()["data"]
    assert d["milestones"][0]["days_left"] == 5
    assert (d["milestones"][0]["tasks_done"], d["milestones"][0]["tasks_total"]) == (1, 2)
    assert d["summary"] == {"total": 1, "complete": 0}


# ---------- reminder rules ----------


def test_rules_default_edit_and_reset(client, cal):
    startup, users, _ = cal
    r = client.get(f"{BASE}/reminder-rules", headers=h(users[TM], startup)).json()["data"]
    assert r["can_edit"] is False
    by = {x["kind"]: x for x in r["rules"]}
    assert by["milestone"]["offsets_minutes"] == [10080, 1440] and by["meeting"][
        "offsets_minutes"
    ] == [15]
    assert (
        by["compliance"]["offsets_minutes"] == [20160, 10080, 1440]
        and by["meeting"]["is_default"] is True
    )
    # Only the founder edits.
    assert (
        client.put(
            f"{BASE}/reminder-rules/meeting",
            headers=h(users[TM], startup),
            json={"offsets_minutes": [5]},
        ).status_code
        == 403
    )
    put = client.put(
        f"{BASE}/reminder-rules/meeting",
        headers=h(users[F], startup),
        json={"offsets_minutes": [5, 60, 15]},
    )
    assert put.status_code == 200
    meeting = next(x for x in put.json()["data"]["rules"] if x["kind"] == "meeting")
    assert meeting["offsets_minutes"] == [60, 15, 5] and meeting["is_default"] is False
    reset = client.delete(f"{BASE}/reminder-rules/meeting", headers=h(users[F], startup)).json()[
        "data"
    ]["rules"]
    assert next(x for x in reset if x["kind"] == "meeting")["offsets_minutes"] == [15]


@pytest.mark.parametrize(
    "body",
    [
        {"offsets_minutes": []},
        {"offsets_minutes": [0]},
        {"offsets_minutes": [15, 15]},
        {"offsets_minutes": [20161]},
        {"offsets_minutes": list(range(1, 8))},
        {"offsets_minutes": ["x"]},
    ],
)
def test_rule_validation(client, cal, body):
    startup, users, _ = cal
    assert (
        client.put(
            f"{BASE}/reminder-rules/meeting", headers=h(users[F], startup), json=body
        ).status_code
        == 422
    )


def test_unknown_rule_kind_is_422(client, cal):
    startup, users, _ = cal
    assert (
        client.put(
            f"{BASE}/reminder-rules/nonsense",
            headers=h(users[F], startup),
            json={"offsets_minutes": [5]},
        ).status_code
        == 422
    )


# ---------- the scan ----------


def _meeting_at(client, startup, user, when: datetime, **kw):
    # Africa/Lagos is UTC+1 all year, so a UTC instant maps to a fixed local wall clock.
    local = when + timedelta(hours=1)
    return client.post(
        f"{BASE}/meetings",
        headers=h(user, startup),
        json=meeting_body(
            event_date=str(local.date()), start_time=local.strftime("%H:%M"), end_time=None, **kw
        ),
    ).json()["data"]


def test_meeting_reminder_fires_once_at_15_minutes_to_the_right_people(client, db, cal):
    startup, users, _ = cal
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    m = _meeting_at(
        client,
        startup,
        users[F],
        now + timedelta(minutes=14),
        attendees=[{"email": str(users[TM].email)}],
    )
    assert scheduler.scheduler_tick(db, now=now) >= 1
    runner.run_once(db)
    rows = db.query(Notification).filter_by(type="calendar.reminder").all()
    assert {r.user_id for r in rows} == {users[F].id, users[TM].id}
    assert rows[0].title == "Meeting starting in 15 minutes: Investor sync"
    # A second tick in the same window claims nothing new.
    before = scheduler.scheduler_tick(db, now=now + timedelta(minutes=1))
    runner.run_once(db)
    assert db.query(Notification).filter_by(type="calendar.reminder").count() == 2
    assert before >= 0 and m["id"]


def test_no_reminder_too_early_or_after_the_start(client, db, cal):
    startup, users, _ = cal
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _meeting_at(client, startup, users[F], now + timedelta(minutes=120))
    _meeting_at(client, startup, users[F], now - timedelta(minutes=5), title="Started")
    scheduler.scheduler_tick(db, now=now)
    runner.run_once(db)
    assert db.query(Notification).filter_by(type="calendar.reminder").count() == 0


def test_edited_offsets_are_honoured(client, db, cal):
    startup, users, _ = cal
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    client.put(
        f"{BASE}/reminder-rules/meeting",
        headers=h(users[F], startup),
        json={"offsets_minutes": [60]},
    )
    _meeting_at(client, startup, users[F], now + timedelta(minutes=59))
    scheduler.scheduler_tick(db, now=now)
    runner.run_once(db)
    assert (
        db.query(Notification).filter_by(type="calendar.reminder").one().title
        == "Meeting starting in 1 hour: Investor sync"
    )


def test_milestone_and_deadline_reminders_go_to_founders_and_team(client, db, cal):
    startup, users, _ = cal
    tomorrow = TODAY + timedelta(days=1)
    # 09:00 Lagos the day before = 08:00 UTC today; tick just after it.
    now = datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC) + timedelta(hours=8, minutes=5)
    create_milestone(
        db, create_phase(db, create_roadmap(db, startup)), title="Beta", due_on=tomorrow
    )
    db.flush()
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "File VAT", "type": "deadline", "event_date": str(tomorrow)},
    )
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Just a task", "type": "task", "event_date": str(tomorrow)},
    )
    scheduler.scheduler_tick(db, now=now)
    runner.run_once(db)
    titles = {r.title for r in db.query(Notification).filter_by(type="calendar.reminder")}
    assert titles == {"Milestone deadline tomorrow: Beta", "Deadline tomorrow: File VAT"}
    who = {r.user_id for r in db.query(Notification).filter_by(type="calendar.reminder")}
    assert who == {users[F].id, users[TM].id}  # not the mentor, accountant or investor


def test_private_event_reminders_do_not_leak_to_the_team(client, db, cal):
    startup, users, _ = cal
    tomorrow = TODAY + timedelta(days=1)
    now = datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC) + timedelta(hours=8, minutes=5)
    client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={
            "title": "Secret deadline",
            "type": "deadline",
            "event_date": str(tomorrow),
            "visibility": "private",
        },
    )
    scheduler.scheduler_tick(db, now=now)
    runner.run_once(db)
    assert {r.user_id for r in db.query(Notification).filter_by(type="calendar.reminder")} == {
        users[F].id
    }


def test_stale_reminders_are_not_sent_late():
    # The catch-up bound refuses a reminder more than 12 hours past its fire time (a worker
    # that was down for a day must not announce yesterday's "tomorrow").
    anchor = datetime.now(UTC) + timedelta(days=2)
    assert reminders._due(anchor, [1440], anchor - timedelta(hours=13)) == [1440]  # 11h late
    assert reminders._due(anchor, [1440], anchor - timedelta(hours=11)) == []  # 13h late
    assert reminders._due(anchor, [15], anchor - timedelta(hours=11)) == []  # not yet due
    assert reminders._due(anchor, [1440], anchor + timedelta(minutes=1)) == []  # already started


def test_calendar_syncs_are_scheduled_per_interval(client, db, cal):
    from tests.calendar_helpers import connect_google

    startup, users, _ = cal
    connect_google(client, db, users[F], startup)
    runner.run_once(db)
    now = datetime.now(UTC)
    first = scheduler.scheduler_tick(db, now=now)
    again = scheduler.scheduler_tick(db, now=now)
    assert first >= 1 and again == 0  # one claim per connection per interval window


def test_human_offsets():
    assert [reminders.human_offset(m) for m in (1, 15, 60, 120, 1440, 10080)] == [
        "1 minute",
        "15 minutes",
        "1 hour",
        "2 hours",
        "1 day",
        "7 days",
    ]
    assert date.today() and CalendarEvent and CalendarEventType
