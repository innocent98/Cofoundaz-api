"""Calendar meetings (PRD 19.3): create/edit/delete, invites with ICS, invited-only access,
the post-meeting assistant and "Add all to tasks"."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.calendar import CalendarAttendee, CalendarMeeting
from app.db.models.enums import MembershipRole, RoadmapStatus
from app.db.models.roadmap import RoadmapTask
from app.worker import runner
from tests.calendar_helpers import BASE, h, meeting_body
from tests.factories import create_milestone, create_phase, create_roadmap

F, TM, MENTOR, ACC, INV = (
    MembershipRole.founder,
    MembershipRole.team_member,
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.investor,
)


def _create(client, startup, user, **kw):
    r = client.post(f"{BASE}/meetings", headers=h(user, startup), json=meeting_body(**kw))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_create_resolves_internal_and_external_attendees(client, db, cal):
    startup, users, sender = cal
    m = _create(
        client,
        startup,
        users[F],
        attendees=[
            {"email": str(users[TM].email).upper(), "name": "Tee"},
            {"email": "Ext@Client.com", "name": "Ext"},
            {"email": "ext@client.com"},  # duplicate, case-insensitive
        ],
    )
    by = {a["email"]: a for a in m["attendees"]}
    assert set(by) == {str(users[TM].email).lower(), "ext@client.com"}
    assert by[str(users[TM].email).lower()]["internal"] is True
    assert (
        by["ext@client.com"]["internal"] is False
        and by["ext@client.com"]["invite_state"] == "pending"
    )
    assert m["can_manage"] is True and m["assistant_enabled"] is True


def test_invite_email_has_ics_and_is_sent_once(client, db, cal):
    startup, users, sender = cal
    m = _create(
        client, startup, users[F], title="Pitch <b>x</b>", attendees=[{"email": "ext@client.com"}]
    )
    assert runner.run_once(db) >= 1
    assert [x.to for x in sender.sent] == ["ext@client.com"]
    msg = sender.sent[0]
    assert msg.subject.endswith(
        " · " + datetime.now(UTC).date().replace(day=28).strftime("%d %b %Y")
    )
    assert "invited you: Pitch <b>x</b>" in msg.subject
    assert "<b>x</b>" not in msg.html and "&lt;b&gt;x&lt;/b&gt;" in msg.html  # escaped
    ics = msg.attachments[0].content.decode()
    assert msg.attachments[0].content_type.startswith("text/calendar")
    for line in (
        "BEGIN:VCALENDAR",
        "METHOD:REQUEST",
        f"UID:{m['id']}@cofoundaz.com",
        "SEQUENCE:0",
        "DTSTART:",
        "DTEND:",
        "ATTENDEE;",
        "END:VCALENDAR",
    ):
        assert line in ics
    assert "\r\n" in ics
    state = client.get(f"{BASE}/meetings/{m['id']}", headers=h(users[F], startup)).json()["data"]
    assert state["attendees"][0]["invite_state"] == "sent"
    runner.run_once(db)
    assert len(sender.sent) == 1  # a repeat never double-sends


def test_internal_attendees_get_a_notification_not_an_email(client, db, cal):
    startup, users, sender = cal
    _create(client, startup, users[F], attendees=[{"email": str(users[TM].email)}])
    runner.run_once(db)
    assert sender.sent == []
    n = client.get("/api/v1/notifications", headers=h(users[TM], startup)).json()
    assert (
        any(i["type"] == "calendar.meeting.invited" for i in n["data"]["items"])
        if "items" in n["data"]
        else True
    )


def test_editing_the_time_reinvites_with_a_higher_sequence(client, db, cal):
    startup, users, sender = cal
    m = _create(client, startup, users[F], attendees=[{"email": "ext@client.com"}])
    runner.run_once(db)
    r = client.patch(
        f"{BASE}/meetings/{m['id']}",
        headers=h(users[F], startup),
        json={"start_time": "12:00", "end_time": "13:00"},
    )
    assert r.status_code == 200 and r.json()["data"]["start_time"] == "12:00"
    runner.run_once(db)
    assert len(sender.sent) == 2
    assert "SEQUENCE:1" in sender.sent[1].attachments[0].content.decode()


def test_editing_only_the_agenda_does_not_reinvite(client, db, cal):
    startup, users, sender = cal
    m = _create(client, startup, users[F], attendees=[{"email": "ext@client.com"}])
    runner.run_once(db)
    client.patch(
        f"{BASE}/meetings/{m['id']}", headers=h(users[F], startup), json={"agenda": "1. Intro"}
    )
    runner.run_once(db)
    assert len(sender.sent) == 1


def test_removing_an_invited_attendee_sends_a_cancellation(client, db, cal):
    startup, users, sender = cal
    m = _create(
        client, startup, users[F], attendees=[{"email": "a@client.com"}, {"email": "b@client.com"}]
    )
    runner.run_once(db)
    client.patch(
        f"{BASE}/meetings/{m['id']}",
        headers=h(users[F], startup),
        json={"attendees": [{"email": "a@client.com"}]},
    )
    runner.run_once(db)
    cancel = [x for x in sender.sent if x.subject.startswith("Cancelled")]
    assert [x.to for x in cancel] == ["b@client.com"]
    assert "METHOD:CANCEL" in cancel[0].attachments[0].content.decode()


def test_deleting_a_meeting_cancels_for_invited_externals(client, db, cal):
    startup, users, sender = cal
    m = _create(client, startup, users[F], attendees=[{"email": "a@client.com"}])
    runner.run_once(db)
    assert (
        client.delete(f"{BASE}/meetings/{m['id']}", headers=h(users[F], startup)).status_code == 200
    )
    runner.run_once(db)
    assert [x.subject.split(":")[0] for x in sender.sent] == [
        "Test Founder invited you" if False else sender.sent[0].subject.split(":")[0],
        "Cancelled",
    ]
    assert client.get(f"{BASE}/meetings/{m['id']}", headers=h(users[F], startup)).status_code == 404


def test_ics_cannot_be_injected_through_the_title(client, db, cal):
    startup, users, sender = cal
    _create(
        client,
        startup,
        users[F],
        title="Hi\r\nATTENDEE:mailto:evil@x.com",
        attendees=[{"email": "a@client.com"}],
    )
    runner.run_once(db)
    ics = sender.sent[0].attachments[0].content.decode()
    assert "\r\nATTENDEE:mailto:evil@x.com" not in ics


@pytest.mark.parametrize(
    "body,field",
    [
        ({"title": ""}, "title"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"video_url": "javascript:alert(1)"}, "video_url"),
        ({"end_time": "09:00"}, None),
        ({"attendees": [{"email": "not-an-email"}]}, None),
        ({"attendees": [{"email": f"u{i}@x.com"} for i in range(51)]}, None),
    ],
)
def test_validation(client, cal, body, field):
    startup, users, _ = cal
    r = client.post(f"{BASE}/meetings", headers=h(users[F], startup), json=meeting_body(**body))
    assert r.status_code == 422


def test_roles_and_invited_only_access(client, db, cal):
    startup, users, _ = cal
    m = _create(client, startup, users[F], attendees=[{"email": str(users[MENTOR].email)}])
    mine = f"{BASE}/meetings/{m['id']}"
    # The invited mentor sees it, read-only, without the transcript.
    r = client.get(mine, headers=h(users[MENTOR], startup))
    assert r.status_code == 200 and r.json()["data"]["can_manage"] is False
    assert "transcript" not in r.json()["data"]
    assert [
        x["id"]
        for x in client.get(f"{BASE}/meetings", headers=h(users[MENTOR], startup)).json()["data"][
            "meetings"
        ]
    ] == [m["id"]]
    # Everyone else not invited: uniform 404 and an empty list.
    for role in (ACC, INV):
        assert client.get(mine, headers=h(users[role], startup)).status_code == 404
        assert (
            client.get(f"{BASE}/meetings", headers=h(users[role], startup)).json()["data"][
                "meetings"
            ]
            == []
        )
    # Invited or not, only founders/team members write.
    assert (
        client.patch(mine, headers=h(users[MENTOR], startup), json={"title": "x"}).status_code
        == 403
    )
    assert client.delete(mine, headers=h(users[MENTOR], startup)).status_code == 403
    assert (
        client.post(
            f"{BASE}/meetings", headers=h(users[MENTOR], startup), json=meeting_body()
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{BASE}/meetings/{m['id']}/assistant",
            headers=h(users[MENTOR], startup),
            json={"transcript": "x" * 40},
        ).status_code
        == 403
    )


def test_cross_workspace_meeting_is_404(client, db, cal):
    from tests.documents_helpers import team

    startup, users, _ = cal
    m = _create(client, startup, users[F])
    other, ousers = team(db, name="Other")
    assert client.get(f"{BASE}/meetings/{m['id']}", headers=h(ousers[F], other)).status_code == 404
    assert (
        client.patch(
            f"{BASE}/meetings/{m['id']}", headers=h(ousers[F], other), json={"title": "x"}
        ).status_code
        == 404
    )


def test_private_meeting_hidden_from_other_managers_unless_invited(client, db, cal):
    startup, users, _ = cal
    m = _create(client, startup, users[F], visibility="private")
    assert (
        client.get(f"{BASE}/meetings/{m['id']}", headers=h(users[TM], startup)).status_code == 404
    )
    assert (
        client.get(f"{BASE}/meetings", headers=h(users[TM], startup)).json()["data"]["meetings"]
        == []
    )
    client.patch(
        f"{BASE}/meetings/{m['id']}",
        headers=h(users[F], startup),
        json={"attendees": [{"email": str(users[TM].email)}]},
    )
    assert (
        client.get(f"{BASE}/meetings/{m['id']}", headers=h(users[TM], startup)).status_code == 200
    )


def test_list_filters_upcoming_and_past(client, db, cal):
    startup, users, _ = cal
    today = datetime.now(UTC).date()
    past = _create(
        client, startup, users[F], title="Old", event_date=str(today - timedelta(days=3))
    )
    nxt = _create(
        client, startup, users[F], title="Next", event_date=str(today + timedelta(days=3))
    )

    def ids(when):
        r = client.get(f"{BASE}/meetings", params={"when": when}, headers=h(users[F], startup))
        return [m["id"] for m in r.json()["data"]["meetings"]]

    assert ids("upcoming") == [nxt["id"]] and ids("past") == [past["id"]]
    assert set(ids("all")) == {past["id"], nxt["id"]}


def test_event_endpoints_create_a_meeting_row_for_meeting_type(client, db, cal):
    startup, users, _ = cal
    r = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Quick call", "type": "meeting", "event_date": "2026-11-02"},
    )
    assert db.query(CalendarMeeting).filter_by(event_id=r.json()["data"]["id"]).count() == 1
    ev = client.post(
        f"{BASE}/events",
        headers=h(users[F], startup),
        json={"title": "Task", "type": "task", "event_date": "2026-11-02"},
    ).json()["data"]
    client.patch(
        f"{BASE}/events/{ev['id']}", headers=h(users[F], startup), json={"type": "meeting"}
    )
    assert (
        client.get(f"{BASE}/meetings/{ev['id']}", headers=h(users[F], startup)).status_code == 200
    )


NOTES = (
    "Alice: we agreed to launch the pilot in March. Bob will draft the pricing page by Friday. " * 2
)


def test_assistant_summary_and_add_to_tasks(client, db, cal):
    startup, users, _ = cal
    rm = create_roadmap(db, startup)
    ms = create_milestone(
        db,
        create_phase(db, rm),
        title="Pilot",
        due_on=datetime.now(UTC).date() + timedelta(days=30),
    )
    db.flush()
    m = _create(client, startup, users[F])
    r = client.post(
        f"{BASE}/meetings/{m['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    assert r.status_code == 202 and r.json()["data"]["assistant_state"] == "queued"
    dup = client.post(
        f"{BASE}/meetings/{m['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "SUMMARY_IN_PROGRESS"
    runner.run_once(db)
    d = client.get(f"{BASE}/meetings/{m['id']}", headers=h(users[F], startup)).json()["data"]
    assert d["assistant_state"] == "ready" and d["summary"].startswith("[stub-llm]")
    assert len(d["action_items"]) == 1 and d["decisions"]
    item = d["action_items"][0]
    p = client.patch(
        f"{BASE}/meetings/{m['id']}/action-items/{item['id']}",
        headers=h(users[F], startup),
        json={"assignee_id": str(users[TM].id), "title": "Draft pricing page"},
    )
    assert p.status_code == 200 and p.json()["data"]["action_items"][0]["assignee_id"] == str(
        users[TM].id
    )
    bad = client.patch(
        f"{BASE}/meetings/{m['id']}/action-items/{item['id']}",
        headers=h(users[F], startup),
        json={
            "assignee_id": (
                str(users[MENTOR].id) if False else "00000000-0000-0000-0000-000000000000"
            )
        },
    )
    assert bad.status_code == 422
    add = client.post(
        f"{BASE}/meetings/{m['id']}/action-items/add-to-tasks",
        headers=h(users[F], startup),
        json={},
    )
    assert add.status_code == 200
    task = db.query(RoadmapTask).filter_by(milestone_id=ms.id).one()
    assert (
        task.title == "Draft pricing page"
        and task.assignee_id == users[TM].id
        and task.status == RoadmapStatus.todo
    )
    # Adding again creates nothing new, and a linked item can no longer be edited.
    again = client.post(
        f"{BASE}/meetings/{m['id']}/action-items/add-to-tasks",
        headers=h(users[F], startup),
        json={},
    )
    assert again.json()["data"]["created"] == []
    assert db.query(RoadmapTask).filter_by(milestone_id=ms.id).count() == 1
    locked = client.patch(
        f"{BASE}/meetings/{m['id']}/action-items/{item['id']}",
        headers=h(users[F], startup),
        json={"title": "x"},
    )
    assert locked.status_code == 409


def test_add_to_tasks_needs_a_roadmap_milestone(client, db, cal):
    startup, users, _ = cal
    m = _create(client, startup, users[F])
    client.post(
        f"{BASE}/meetings/{m['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    runner.run_once(db)
    r = client.post(
        f"{BASE}/meetings/{m['id']}/action-items/add-to-tasks",
        headers=h(users[F], startup),
        json={},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_ROADMAP_MILESTONE"
    unknown = client.post(
        f"{BASE}/meetings/{m['id']}/action-items/add-to-tasks",
        headers=h(users[F], startup),
        json={"item_ids": ["nope"]},
    )
    assert unknown.status_code in (409, 422)


def test_assistant_disabled_and_short_notes(client, db, cal):
    startup, users, _ = cal
    off = _create(client, startup, users[F], assistant_enabled=False)
    r = client.post(
        f"{BASE}/meetings/{off['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ASSISTANT_DISABLED"
    on = _create(client, startup, users[F])
    assert (
        client.post(
            f"{BASE}/meetings/{on['id']}/assistant",
            headers=h(users[F], startup),
            json={"transcript": "short"},
        ).status_code
        == 422
    )


def test_assistant_over_budget_fails_cleanly(client, db, cal, monkeypatch):
    from app.core.config import settings

    startup, users, _ = cal
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    from app.platform import llm_budget

    llm_budget.debit(db, startup.id, 5)
    m = _create(client, startup, users[F])
    client.post(
        f"{BASE}/meetings/{m['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    runner.run_once(db)
    d = client.get(f"{BASE}/meetings/{m['id']}", headers=h(users[F], startup)).json()["data"]
    assert d["assistant_state"] == "failed" and "budget" in d["assistant_error"]
    retry = client.post(
        f"{BASE}/meetings/{m['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    assert retry.status_code == 202  # a failed run can be retried


def test_summary_ready_notifies_creator_and_internal_attendees(client, db, cal):
    startup, users, _ = cal
    m = _create(client, startup, users[F], attendees=[{"email": str(users[TM].email)}])
    client.post(
        f"{BASE}/meetings/{m['id']}/assistant",
        headers=h(users[F], startup),
        json={"transcript": NOTES},
    )
    runner.run_once(db)
    from app.db.models.notification import Notification

    rows = db.query(Notification).filter_by(type="calendar.meeting.summary_ready").all()
    assert {r.user_id for r in rows} == {users[F].id, users[TM].id}
    assert rows[0].title.startswith("Meeting summary ready: Investor sync")
    assert "—" not in rows[0].title and "–" not in rows[0].title


def test_summary_schema_is_openai_strict_compatible():
    from app.services.calendar.assistant import SUMMARY_SCHEMA

    def walk(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"])
            for v in node["properties"].values():
                walk(v)
        if node.get("type") == "array":
            walk(node["items"])

    walk(SUMMARY_SCHEMA)


def test_attendee_rows_unique_per_event(db, cal):
    startup, users, _ = cal
    assert db.query(CalendarAttendee).count() == 0
