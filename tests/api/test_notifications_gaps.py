"""Notifications PRD gaps (N1 to N9): archive/restore, server-side filters and counts, priority
and module on every row, in-app switches, critical alerts, quiet hours, digest."""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.db.models.notification_preference import NotificationPreference
from app.platform import email as email_mod
from app.platform.events import event_bus
from app.services.notifications import digest, registry
from app.services.notifications.preferences import (
    email_route,
    in_quiet_hours,
    set_preferences,
)
from app.services.notifications.service import create_notifications
from app.worker import runner, scheduler
from app.worker.handlers import scheduled as _scheduled  # noqa: F401
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _note(db, u, s, type_="mission.ready", title="N", **kw):
    (n,) = create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type=type_, title=title, body="", data={}
    )
    for k, v in kw.items():
        setattr(n, k, v)
    db.flush()
    return n


def _api(path):
    return f"/api/v1/notifications{path}"


def _titles(client, h, qs=""):
    r = client.get(_api(f"?{qs}"), headers=h)
    assert r.status_code == 200, r.text
    return [n["title"] for n in r.json()["data"]["notifications"]]


# ---------------------------------------------------------------- archive / restore


def test_archive_hides_from_inbox_and_unread_and_restore_returns_it(client, db):
    u, s, h = _member(db)
    n = _note(db, u, s, title="A")
    _note(db, u, s, title="B")
    db.commit()
    r = client.post(_api(f"/{n.id}/archive"), headers=h)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["archived"] is True and body["read"] is True  # archiving also reads it
    assert _titles(client, h) == ["B"]
    assert _titles(client, h, "archived=true") == ["A"]
    assert client.get(_api("/unread-count"), headers=h).json()["data"]["unread"] == 1
    back = client.post(_api(f"/{n.id}/restore"), headers=h).json()["data"]
    assert back["archived"] is False
    assert sorted(_titles(client, h)) == ["A", "B"]
    assert _titles(client, h, "archived=true") == []


def test_archived_rows_are_skipped_by_read_all(client, db):
    u, s, h = _member(db)
    n = _note(db, u, s, title="A")
    _note(db, u, s, title="B")
    db.commit()
    client.post(_api(f"/{n.id}/archive"), headers=h)
    n.read_at = None  # even if it were unread, read-all must leave the archive alone
    db.commit()
    marked = client.post(_api("/read-all"), headers=h).json()["data"]["marked"]
    assert marked == 1


def test_cannot_archive_someone_elses_or_a_hidden_audience_row(client, db):
    u, s, h = _member(db)
    other = create_user(db)
    create_membership(db, other, s)
    theirs = _note(db, other, s, title="Theirs")
    db.commit()
    assert client.post(_api(f"/{theirs.id}/archive"), headers=h).status_code == 404
    assert client.post(_api(f"/{uuid.uuid4()}/restore"), headers=h).status_code == 404
    # A mentor must not reach a founder-only type even if a row exists for them.
    m, ms, mh = _member(db, role=MembershipRole.mentor)
    hidden = _note(db, m, ms, type_="assessment.quarterly.due", title="Founder only")
    db.commit()
    assert client.post(_api(f"/{hidden.id}/archive"), headers=mh).status_code == 404


# ---------------------------------------------------------------- filters, counts, fields


def test_rows_carry_priority_and_module(client, db):
    u, s, h = _member(db)
    _note(db, u, s, type_="finance.runway.low", title="Runway")
    _note(db, u, s, type_="mission.ready", title="Mission")
    db.commit()
    rows = {n["title"]: n for n in client.get(_api(""), headers=h).json()["data"]["notifications"]}
    assert rows["Runway"]["priority"] == "critical" and rows["Runway"]["module"] == "finance"
    assert rows["Mission"]["priority"] == "normal" and rows["Mission"]["module"] == "missions"
    assert rows["Mission"]["archived"] is False


def test_filters_and_counts(client, db):
    u, s, h = _member(db)
    _note(db, u, s, type_="team.comment.mentioned", title="Mention")
    _note(db, u, s, type_="dashboard.briefing.ready", title="Briefing")
    _note(db, u, s, type_="roadmap.milestone.due_soon", title="Due")
    _note(db, u, s, type_="finance.runway.low", title="Runway")
    _note(db, u, s, type_="billing.plan.changed", title="Plan")
    _note(db, u, s, type_="mission.ready", title="Read one", read_at=datetime.now(UTC))
    db.commit()
    assert _titles(client, h, "filter=mentions") == ["Mention"]
    assert _titles(client, h, "filter=ai") == ["Briefing"]
    assert _titles(client, h, "filter=deadlines") == ["Due"]
    assert _titles(client, h, "filter=urgent") == ["Runway"]
    assert _titles(client, h, "filter=billing") == ["Plan"]
    assert "Read one" not in _titles(client, h, "filter=unread")
    c = client.get(_api("/counts"), headers=h).json()["data"]
    assert c == {
        "all": 6,
        "unread": 5,
        "urgent": 1,
        "urgent_unread": 1,
        "mentions": 1,
        "ai": 1,
        "deadlines": 1,
        "billing": 1,
        "archived": 0,
    }
    assert client.get(_api("?filter=nonsense"), headers=h).status_code == 422


def test_filter_composes_with_cursor_paging(client, db):
    u, s, h = _member(db)
    for i in range(3):
        _note(db, u, s, type_="finance.runway.low", title=f"R{i}")
    _note(db, u, s, title="plain")
    db.commit()
    r = client.get(_api("?filter=urgent&limit=2"), headers=h).json()["data"]
    assert len(r["notifications"]) == 2 and r["next_cursor"]
    r2 = client.get(_api(f"?filter=urgent&limit=2&cursor={r['next_cursor']}"), headers=h).json()[
        "data"
    ]
    assert len(r2["notifications"]) == 1 and r2["next_cursor"] is None


def test_counts_hide_audience_restricted_types(client, db):
    m, ms, mh = _member(db, role=MembershipRole.mentor)
    _note(db, m, ms, type_="assessment.quarterly.due", title="Founder only")
    _note(db, m, ms, type_="mission.ready", title="Also hidden (mission is F+TM)")
    db.commit()
    c = client.get(_api("/counts"), headers=mh).json()["data"]
    assert c["all"] == 0 and c["unread"] == 0


# ---------------------------------------------------------------- preferences


def test_preferences_expose_groups_in_app_and_lock_metadata(client, db):
    _u, _s, h = _member(db)
    p = client.get(_api("/preferences"), headers=h).json()["data"]
    keys = [g["key"] for g in p["groups"]]
    assert keys == list(p["categories"]) == list(p["in_app"])
    fin = next(g for g in p["groups"] if g["key"] == "finance")
    assert fin["title"] == "Finance"
    assert "finance.runway.low" in fin["critical_types"]
    r = client.put(_api("/preferences"), json={"in_app": {"sales": False}}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["in_app"]["sales"] is False
    assert r.json()["data"]["in_app"]["finance"] is True
    bad = client.put(_api("/preferences"), json={"in_app": {"nope": True}}, headers=h)
    assert bad.status_code == 422


def test_in_app_off_drops_non_critical_but_never_critical(db):
    registry.register()
    u, s, _h = _member(db)
    set_preferences(
        db,
        user_id=u.id,
        startup_id=s.id,
        master_email=None,
        categories=None,
        in_app={"finance": False, "roadmap_missions": False},
    )
    event_bus.publish(db, "finance.runway.low", {"startup_id": str(s.id)})
    event_bus.publish(db, "finance.invoice.paid", {"startup_id": str(s.id)})
    event_bus.publish(db, "mission.ready", {"startup_id": str(s.id)})
    types = {n.type for n in db.query(Notification).filter_by(user_id=u.id).all()}
    assert types == {"finance.runway.low"}  # critical stays; the switched-off categories are gone


# ---------------------------------------------------------------- email routing


def _prefs(db, u, s, **kw):
    row = db.query(NotificationPreference).filter_by(user_id=u.id, startup_id=s.id).first()
    if row is None:
        row = NotificationPreference(
            user_id=u.id, startup_id=s.id, master_email=True, categories={}
        )
        db.add(row)
    for k, v in kw.items():
        setattr(row, k, v)
    db.flush()
    return row


def test_email_route_matrix(db):
    u, s, _h = _member(db)
    noon = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    def r(event):
        return email_route(db, user_id=u.id, startup_id=s.id, event=event, now=noon)

    assert r("document.shared") == "now"  # ON by default
    assert r("sales.deal.won") == "skip"  # new rows are opt-in
    assert r("journal.reminder") == "skip"  # no category: in-app only
    assert r("finance.runway.low") == "now"  # critical, even with email "off" by default
    row = _prefs(db, u, s, master_email=False)
    assert r("document.shared") == "skip"
    assert r("finance.runway.low") == "now"  # master switch does not mute critical alerts
    row.master_email = True
    row.categories = {"sales": True}
    row.digest_mode = "daily"
    db.flush()
    assert r("sales.deal.won") == "digest"
    assert r("finance.runway.low") == "now"  # critical bypasses the digest


def test_quiet_hours_hold_back_non_critical_email_only(db):
    u, s, _h = _member(db)
    row = _prefs(
        db,
        u,
        s,
        quiet_enabled=True,
        quiet_from=time(20, 0),
        quiet_until=time(6, 0),
        timezone="Africa/Lagos",
    )
    # 22:00 UTC is 23:00 in Lagos: inside the window; 08:00 UTC is 09:00: outside.
    late = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)
    early_morning = datetime(
        2026, 10, 29, 4, 0, tzinfo=UTC
    )  # 05:00 Lagos, still quiet (wraps midnight)
    day = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
    assert (
        in_quiet_hours(row, late)
        and in_quiet_hours(row, early_morning)
        and not in_quiet_hours(row, day)
    )
    assert (
        email_route(db, user_id=u.id, startup_id=s.id, event="document.shared", now=late) == "skip"
    )
    assert (
        email_route(db, user_id=u.id, startup_id=s.id, event="finance.runway.low", now=late)
        == "now"
    )
    assert email_route(db, user_id=u.id, startup_id=s.id, event="document.shared", now=day) == "now"
    row.quiet_enabled = False
    db.flush()
    assert not in_quiet_hours(row, late)
    assert not in_quiet_hours(None, late)


def test_quiet_window_that_does_not_wrap(db):
    u, s, _h = _member(db)
    row = _prefs(
        db,
        u,
        s,
        quiet_enabled=True,
        quiet_from=time(13, 0),
        quiet_until=time(15, 0),
        timezone="UTC",
    )
    assert in_quiet_hours(row, datetime(2026, 10, 29, 14, 0, tzinfo=UTC))
    assert not in_quiet_hours(row, datetime(2026, 10, 29, 16, 0, tzinfo=UTC))


def test_publish_enqueues_email_by_route(db):
    registry.register()
    u, s, _h = _member(db)
    _prefs(db, u, s, digest_mode="daily")
    event_bus.publish(db, "document.shared", {"startup_id": str(s.id)})
    event_bus.publish(db, "finance.runway.low", {"startup_id": str(s.id)})
    mail = db.query(Job).filter_by(type="email.notification").count()
    assert mail == 1  # the digest user only gets the critical alert live


# ---------------------------------------------------------------- digest


class _Sender(email_mod.ConsoleEmailSender):
    pass


@pytest.fixture()
def sender(monkeypatch):
    s = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(digest, "get_email_sender", lambda: s)
    return s


def test_digest_lists_modules_skips_critical_and_archived(db, sender):
    u, s, _h = _member(db)
    s.name = "Acme"
    _prefs(db, u, s, digest_mode="daily")
    now = datetime.now(UTC)
    _note(db, u, s, type_="document.shared", title="Doc <b>shared</b>")
    _note(db, u, s, type_="document.version.created", title="New version")
    _note(db, u, s, type_="finance.runway.low", title="Critical runway")  # emailed live, not here
    _note(db, u, s, type_="team.comment.mentioned", title="Archived one", archived_at=now)
    _note(db, u, s, type_="sales.deal.won", title="Sales is opt-in")  # category off
    assert digest.send_digest(db, user_id=u.id, startup_id=s.id, now=now) is True
    (msg,) = sender.sent
    assert msg.subject == "Acme: your daily Cofoundaz digest"
    assert "Documents (2)" in msg.html and "Doc &lt;b&gt;shared&lt;/b&gt;" in msg.html
    assert "Critical runway" not in msg.html and "Archived one" not in msg.html
    assert "Sales is opt-in" not in msg.html
    # The cursor moved: an immediate second run has nothing new and sends nothing.
    assert (
        digest.send_digest(db, user_id=u.id, startup_id=s.id, now=now + timedelta(minutes=1))
        is False
    )
    assert len(sender.sent) == 1


def test_digest_not_sent_when_empty_or_realtime_or_master_off(db, sender):
    u, s, _h = _member(db)
    now = datetime.now(UTC)
    assert digest.send_digest(db, user_id=u.id, startup_id=s.id, now=now) is False  # no row
    row = _prefs(db, u, s, digest_mode="daily")
    assert digest.send_digest(db, user_id=u.id, startup_id=s.id, now=now) is False  # nothing to say
    _note(db, u, s, type_="document.shared")
    row.digest_mode = "realtime"
    db.flush()
    assert digest.send_digest(db, user_id=u.id, startup_id=s.id, now=now) is False
    row.digest_mode, row.master_email = "daily", False
    db.flush()
    assert digest.send_digest(db, user_id=u.id, startup_id=s.id, now=now) is False
    assert sender.sent == []


def test_weekly_digest_subject(db, sender):
    u, s, _h = _member(db)
    s.name = "Acme"
    _prefs(db, u, s, digest_mode="weekly")
    _note(db, u, s, type_="document.shared")
    assert digest.send_digest(db, user_id=u.id, startup_id=s.id, now=datetime.now(UTC))
    assert sender.sent[0].subject == "Acme: your weekly Cofoundaz digest"


def test_scheduler_claims_a_digest_once_at_the_users_time_and_weekday(db, sender):
    registry.register()
    u, s, _h = _member(db)
    s.name = "Acme"
    row = _prefs(db, u, s, digest_mode="daily", digest_time=time(8, 0), timezone="Africa/Lagos")
    _note(db, u, s, type_="document.shared", title="Hello")
    # 06:30 UTC is 07:30 in Lagos: before 08:00, not due. 07:30 UTC is 08:30: due.
    before = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)
    due = datetime(2026, 10, 29, 7, 30, tzinfo=UTC)
    assert scheduler.scheduler_tick(db, now=before) >= 0
    assert db.query(Job).filter_by(type=scheduler.SCHED_NOTIF_DIGEST).count() == 0
    scheduler.scheduler_tick(db, now=due)
    assert db.query(Job).filter_by(type=scheduler.SCHED_NOTIF_DIGEST).count() == 1
    scheduler.scheduler_tick(db, now=due + timedelta(minutes=5))
    assert db.query(Job).filter_by(type=scheduler.SCHED_NOTIF_DIGEST).count() == 1  # once a day
    for _ in range(20):
        if runner.run_once(db) == 0:
            break
    assert any("digest" in m.subject for m in sender.sent)
    # Weekly: only on the chosen weekday (2026-10-29 is a Thursday = 3).
    row.digest_mode, row.digest_day = "weekly", 0
    db.flush()
    next_day = datetime(2026, 10, 30, 7, 30, tzinfo=UTC)
    before_jobs = db.query(Job).filter_by(type=scheduler.SCHED_NOTIF_DIGEST).count()
    scheduler.scheduler_tick(db, now=next_day)
    assert db.query(Job).filter_by(type=scheduler.SCHED_NOTIF_DIGEST).count() == before_jobs
    row.digest_day = 4  # Friday
    db.flush()
    scheduler.scheduler_tick(db, now=next_day)
    assert db.query(Job).filter_by(type=scheduler.SCHED_NOTIF_DIGEST).count() == before_jobs + 1
