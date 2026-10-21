"""Marketing PRD gap build (a): scheduler publish, notification payloads, campaign stats and
performance, campaign links, metric list/delete + single scope, CSV export, marketing media."""

import io
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.document import DocumentFile
from app.db.models.enums import ChannelKey, ContentStatus, MembershipRole, StartupStage
from app.db.models.job import Job
from app.db.models.marketing import ContentCalendarEntry, MarketingMedia
from app.db.models.notification import Notification
from app.db.models.scheduled_run import ScheduledRun
from app.platform import storage as storage_mod
from app.services.notifications import registry
from app.worker import scheduler
from app.worker.runner import run_once
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod.settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(storage_mod.settings, "LOCAL_STORAGE_DIR", str(tmp_path))


def _entry(db, startup, user, *, status=ContentStatus.scheduled, at=None, title="Post"):
    e = ContentCalendarEntry(
        startup_id=startup.id,
        created_by=user.id,
        title=title,
        channel=ChannelKey.organic_social,
        status=status,
        scheduled_at=at,
    )
    db.add(e)
    db.flush()
    return e


# ---- scheduler: scheduled posts publish themselves (rows 3, 34) ----
def test_due_scheduler_scan_picks_only_due_scheduled_posts(db):
    u, s, _h = _member(db)
    due = _entry(db, s, u, at=NOW - timedelta(minutes=1))
    _entry(db, s, u, at=NOW + timedelta(hours=1))  # future
    _entry(db, s, u, status=ContentStatus.draft, at=NOW - timedelta(hours=1))
    _entry(db, s, u, status=ContentStatus.published, at=NOW - timedelta(hours=1))
    found = scheduler._due_marketing_posts(db, NOW)
    assert [d.scope_key for d in found] == [str(due.id)]
    assert found[0].task_key == "marketing.publish"
    assert found[0].payload == {"startup_id": str(s.id), "entry_id": str(due.id)}


def test_due_scan_skips_deleted_workspaces(db):
    from app.db.models.startup import Startup  # noqa: F401

    u, s, _h = _member(db)
    _entry(db, s, u, at=NOW - timedelta(minutes=1))
    s.deleted_at = NOW
    db.flush()
    assert scheduler._due_marketing_posts(db, NOW) == []


def test_tick_publishes_once_and_a_new_time_is_claimable_again(db):
    u, s, _h = _member(db)
    e = _entry(db, s, u, at=NOW - timedelta(minutes=1))
    assert scheduler.scheduler_tick(db, now=NOW) >= 1
    assert db.query(Job).filter(Job.type == scheduler.SCHED_MARKETING_PUBLISH).count() == 1
    # same tick again: already claimed, nothing new is enqueued
    scheduler.scheduler_tick(db, now=NOW)
    assert db.query(Job).filter(Job.type == scheduler.SCHED_MARKETING_PUBLISH).count() == 1
    # rescheduled to another past time: a new (post, time) claim
    e.scheduled_at = NOW - timedelta(seconds=30)
    db.flush()
    scheduler.scheduler_tick(db, now=NOW)
    assert db.query(Job).filter(Job.type == scheduler.SCHED_MARKETING_PUBLISH).count() == 2
    assert (
        db.query(ScheduledRun).filter_by(task_key="marketing.publish", scope_key=str(e.id)).count()
        == 2
    )


def test_worker_flips_status_and_notifies_the_whole_team_including_the_author(db):
    from app.worker import __main__ as worker_main

    worker_main.register()  # handlers + notification subscribers, as the real worker does
    registry.register()
    author, s, _h = _member(db)
    teammate, _s, _h2 = _member(db, role=MembershipRole.team_member, startup=s)
    mentor, _s, _h3 = _member(db, role=MembershipRole.mentor, startup=s)
    due_at = datetime.now(UTC) - timedelta(minutes=2)
    e = _entry(db, s, author, at=due_at, title="Launch teaser")
    scheduler.scheduler_tick(db, now=datetime.now(UTC))
    assert run_once(db) >= 1
    db.refresh(e)
    assert e.status == ContentStatus.published
    assert e.published_at == due_at  # D-MKT-8: the time it was due, nothing was posted anywhere
    notes = db.query(Notification).filter_by(type="marketing.post.published").all()
    assert {n.user_id for n in notes} == {author.id, teammate.id}  # not the mentor
    n = notes[0]
    assert n.title == "Scheduled post published: Launch teaser"
    assert n.body == "See results"
    assert n.data["entry_id"] == str(e.id)
    assert n.data["href"] == f"/marketing/calendar?entry={e.id}"
    assert n.data["was_scheduled"] is True


def test_publish_is_idempotent_and_respects_a_late_edit(db):
    from app.services.marketing.service import publish_due_entry

    u, s, _h = _member(db)
    e = _entry(db, s, u, at=NOW - timedelta(minutes=1))
    assert publish_due_entry(db, entry_id=e.id, now=NOW) is True
    assert publish_due_entry(db, entry_id=e.id, now=NOW) is False  # already published
    back = _entry(db, s, u, at=NOW - timedelta(minutes=1))
    back.status = ContentStatus.draft  # edited back to a draft after the scan
    db.flush()
    assert publish_due_entry(db, entry_id=back.id, now=NOW) is False
    later = _entry(db, s, u, at=NOW - timedelta(minutes=1))
    later.scheduled_at = NOW + timedelta(days=1)  # moved to the future after the scan
    db.flush()
    assert publish_due_entry(db, entry_id=later.id, now=NOW) is False
    assert publish_due_entry(db, entry_id=__import__("uuid").uuid4(), now=NOW) is False


def test_job_ignores_a_payload_workspace_that_does_not_own_the_post(db):
    import uuid

    from app.db.models.job import JobStatus
    from app.worker.handlers.scheduled import handle_marketing_publish

    u, s, _h = _member(db)
    e = _entry(db, s, u, at=datetime.now(UTC) - timedelta(minutes=1))
    other_u, other_s, _ = _member(db)
    job = Job(
        type=scheduler.SCHED_MARKETING_PUBLISH,
        payload={"startup_id": str(other_s.id), "entry_id": str(e.id)},
        status=JobStatus.running,
    )
    handle_marketing_publish(db, job)
    db.refresh(e)
    assert e.status == ContentStatus.scheduled
    gone = Job(
        type=scheduler.SCHED_MARKETING_PUBLISH,
        payload={"startup_id": str(s.id), "entry_id": str(uuid.uuid4())},
        status=JobStatus.running,
    )
    handle_marketing_publish(db, gone)  # unknown post: a no-op, not an error


def test_marking_a_never_scheduled_draft_published_says_post_published(client, db):
    registry.register()
    founder, s, h = _member(db)
    teammate, _s, _h2 = _member(db, role=MembershipRole.team_member, startup=s)
    r = client.post(
        f"{BASE}/calendar-entries", json={"title": "Quick note", "channel": "email"}, headers=h
    )
    eid = r.json()["data"]["id"]
    resp = client.patch(f"{BASE}/calendar-entries/{eid}", json={"status": "published"}, headers=h)
    assert resp.status_code == 200
    n = db.query(Notification).filter_by(type="marketing.post.published").one()
    assert n.user_id == teammate.id  # the actor is not notified
    assert n.title == "Post published: Quick note"
    assert n.data["was_scheduled"] is False and n.data["href"].endswith(f"entry={eid}")


def test_campaign_notifications_carry_a_cta_and_href(client, db):
    registry.register()
    founder, s, h = _member(db)
    teammate, _s, _h2 = _member(db, role=MembershipRole.team_member, startup=s)
    cid = client.post(
        f"{BASE}/campaigns", json={"name": "Push", "objective": "launch"}, headers=h
    ).json()["data"]["id"]
    client.patch(f"{BASE}/campaigns/{cid}", json={"status": "active"}, headers=h)
    n = db.query(Notification).filter_by(type="marketing.campaign.launched").one()
    assert n.body == "See results"
    assert n.data["href"] == f"/marketing/campaigns/{cid}"


# ---- campaign stats + performance (rows 6, 7) ----
def _campaign(client, h, **kw):
    body = {"name": "Waitlist", "objective": "launch", "channel_mix": {"email": 60, "search": 40}}
    body.update(kw)
    return client.post(f"{BASE}/campaigns", json=body, headers=h).json()["data"]["id"]


def _metrics(client, h, points):
    r = client.post(f"{BASE}/metrics", json={"points": points}, headers=h)
    assert r.status_code == 200, r.text


def test_campaign_list_and_detail_carry_ctr_and_conversion_stats(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h, period_start="2026-10-01", period_end="2026-10-31")
    empty = _campaign(client, h, name="Empty")
    today = date.today().isoformat()
    _metrics(
        client,
        h,
        [
            {"ts": today, "campaign_id": cid, "metric": "impressions", "value": 1000},
            {"ts": today, "campaign_id": cid, "metric": "clicks", "value": 50},
            {"ts": today, "campaign_id": cid, "metric": "conversions", "value": 5},
            {"ts": today, "campaign_id": cid, "metric": "spend", "value": 20000},
        ],
    )
    rows = {
        c["id"]: c for c in client.get(f"{BASE}/campaigns", headers=h).json()["data"]["campaigns"]
    }
    assert rows[cid]["metrics"] == {
        "impressions": 1000,
        "clicks": 50,
        "conversions": 5,
        "spend": 20000,
        "click_through_rate": 5.0,
        "conversion_rate": 10.0,
    }
    assert rows[cid]["period_start"] == "2026-10-01"
    assert (
        rows[empty]["metrics"]["impressions"] == 0
        and rows[empty]["metrics"]["click_through_rate"] == 0.0
    )
    one = client.get(f"{BASE}/campaigns/{cid}", headers=h).json()["data"]
    assert one["metrics"]["conversions"] == 5


def test_campaign_performance_series_totals_and_estimated_channel_split(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h)
    d1, d2 = (date.today() - timedelta(days=2)).isoformat(), date.today().isoformat()
    _metrics(
        client,
        h,
        [
            {"ts": d1, "campaign_id": cid, "metric": "clicks", "value": 10},
            {"ts": d2, "campaign_id": cid, "metric": "clicks", "value": 30},
            {"ts": d2, "campaign_id": cid, "metric": "spend", "value": 1000},
        ],
    )
    r = client.get(f"{BASE}/campaigns/{cid}/performance", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["range"] == "all" and d["totals"]["clicks"] == 40
    assert [p["date"] for p in d["series"]] == [d1, d2]
    assert d["series"][1] == {
        "date": d2,
        "impressions": 0,
        "clicks": 30,
        "conversions": 0,
        "spend": 1000,
    }
    by = {c["channel"]: c for c in d["by_channel"]}
    assert by["email"]["share_percent"] == 60.0 and by["email"]["clicks"] == 24
    assert by["search"]["clicks"] == 16 and by["email"]["estimated"] is True
    ranged = client.get(f"{BASE}/campaigns/{cid}/performance?range=7d", headers=h).json()["data"]
    assert ranged["totals"]["clicks"] == 40
    assert client.get(f"{BASE}/campaigns/{cid}/performance?range=1y", headers=h).status_code == 422


def test_performance_of_another_workspaces_campaign_is_404(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    cid = _campaign(client, h2)
    assert client.get(f"{BASE}/campaigns/{cid}/performance", headers=h).status_code == 404


# ---- campaign links on posts and copy (row 8) ----
def test_posts_and_copy_link_to_a_campaign_and_filter_by_it(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h)
    other = _campaign(client, h, name="Other")
    a = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "A", "channel": "email", "campaign_id": cid},
        headers=h,
    )
    assert a.status_code == 200 and a.json()["data"]["campaign_id"] == cid
    client.post(
        f"{BASE}/calendar-entries",
        json={"title": "B", "channel": "email", "campaign_id": other},
        headers=h,
    )
    got = client.get(f"{BASE}/calendar-entries?campaign_id={cid}", headers=h).json()["data"][
        "entries"
    ]
    assert [e["title"] for e in got] == ["A"]
    # re-link and unlink through PATCH
    eid = a.json()["data"]["id"]
    resp = client.patch(f"{BASE}/calendar-entries/{eid}", json={"campaign_id": None}, headers=h)
    assert resp.json()["data"]["campaign_id"] is None
    g = client.post(
        f"{BASE}/copy/generate",
        json={"asset_type": "ad", "tone": "bold", "key_message": "Hi", "campaign_id": cid},
        headers=h,
    )
    assert g.status_code == 202
    listed = client.get(f"{BASE}/copy/generations?campaign_id={cid}", headers=h).json()["data"][
        "generations"
    ]
    assert len(listed) == 1 and listed[0]["campaign_id"] == cid
    assert (
        client.get(f"{BASE}/copy/generations?campaign_id={other}", headers=h).json()["data"][
            "generations"
        ]
        == []
    )


def test_a_post_or_copy_cannot_link_to_another_workspaces_campaign(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    foreign = _campaign(client, h2)
    r = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "A", "channel": "email", "campaign_id": foreign},
        headers=h,
    )
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "campaign_id"
    eid = client.post(
        f"{BASE}/calendar-entries", json={"title": "A", "channel": "email"}, headers=h
    ).json()["data"]["id"]
    resp = client.patch(f"{BASE}/calendar-entries/{eid}", json={"campaign_id": foreign}, headers=h)
    assert resp.status_code == 422
    g = client.post(
        f"{BASE}/copy/generate",
        json={"asset_type": "ad", "tone": "bold", "key_message": "Hi", "campaign_id": foreign},
        headers=h,
    )
    assert g.status_code == 422


# ---- metrics: single scope, list, delete (rows 26, 27) ----
def test_a_metric_point_at_two_scopes_is_rejected(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h)
    r = client.post(
        f"{BASE}/metrics",
        json={
            "points": [
                {
                    "ts": "2026-10-15",
                    "channel": "email",
                    "campaign_id": cid,
                    "metric": "spend",
                    "value": 5,
                }
            ]
        },
        headers=h,
    )
    assert r.status_code == 422
    assert "not both" in r.text
    # either scope alone, or none, is fine
    _metrics(
        client,
        h,
        [
            {"ts": "2026-10-15", "channel": "email", "metric": "spend", "value": 5},
            {"ts": "2026-10-15", "campaign_id": cid, "metric": "spend", "value": 5},
            {"ts": "2026-10-15", "metric": "visits", "value": 5},
        ],
    )


def test_list_and_delete_recorded_metrics(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h)
    today = date.today().isoformat()
    _metrics(
        client,
        h,
        [
            {"ts": today, "channel": "email", "metric": "clicks", "value": 7},
            {"ts": today, "campaign_id": cid, "metric": "clicks", "value": 9},
            {
                "ts": (date.today() - timedelta(days=200)).isoformat(),
                "metric": "visits",
                "value": 1,
            },
        ],
    )
    d = client.get(f"{BASE}/metrics?range=30d", headers=h).json()["data"]
    assert d["total"] == 2 and d["limit"] == 100 and d["offset"] == 0
    by_value = {m["value"]: m for m in d["metrics"]}
    assert by_value[7]["channel"] == "email" and by_value[7]["campaign_id"] is None
    assert by_value[9]["campaign_name"] == "Waitlist"
    page = client.get(f"{BASE}/metrics?range=30d&limit=1&offset=1", headers=h).json()["data"]
    assert len(page["metrics"]) == 1 and page["total"] == 2
    mid = by_value[7]["id"]
    resp = client.delete(f"{BASE}/metrics/{mid}", headers=h)
    assert resp.json()["data"] == {"deleted": True}
    assert client.get(f"{BASE}/metrics?range=30d", headers=h).json()["data"]["total"] == 1
    resp = client.delete(f"{BASE}/metrics/{mid}", headers=h)
    assert resp.status_code == 404
    funnel = client.get(f"{BASE}/analytics?range=30d", headers=h).json()["data"]["funnel"]
    assert funnel["clicks"] == 9  # the deleted point no longer counts
    assert client.get(f"{BASE}/metrics?range=1y", headers=h).status_code == 422
    assert client.get(f"{BASE}/metrics?limit=0", headers=h).status_code == 422


def test_cannot_delete_another_workspaces_metric(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    _metrics(client, h2, [{"ts": date.today().isoformat(), "metric": "visits", "value": 3}])
    mid = client.get(f"{BASE}/metrics", headers=h2).json()["data"]["metrics"][0]["id"]
    resp = client.delete(f"{BASE}/metrics/{mid}", headers=h)
    assert resp.status_code == 404
    assert client.get(f"{BASE}/metrics", headers=h).json()["data"]["total"] == 0


def test_cac_by_channel_attributes_campaign_numbers_by_mix_and_says_estimated(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h)  # email 60 / search 40
    today = date.today().isoformat()
    _metrics(
        client,
        h,
        [
            {"ts": today, "channel": "email", "metric": "spend", "value": 1000},
            {"ts": today, "channel": "email", "metric": "conversions", "value": 2},
            {"ts": today, "campaign_id": cid, "metric": "spend", "value": 1000},
            {"ts": today, "campaign_id": cid, "metric": "conversions", "value": 10},
        ],
    )
    rows = {
        r["channel"]: r
        for r in client.get(f"{BASE}/analytics?range=30d", headers=h).json()["data"][
            "cac_by_channel"
        ]
    }
    assert rows["email"] == {
        "channel": "email",
        "spend": 1600,
        "conversions": 8,
        "cac": 200,
        "attributed_spend": 600,
        "attributed_conversions": 6,
        "estimated": True,
    }
    assert rows["search"]["spend"] == 400 and rows["search"]["conversions"] == 4
    assert rows["search"]["estimated"] is True


def test_channel_only_cac_is_not_marked_estimated(client, db):
    _u, _s, h = _member(db)
    _metrics(
        client,
        h,
        [{"ts": date.today().isoformat(), "channel": "search", "metric": "spend", "value": 90}],
    )
    row = client.get(f"{BASE}/analytics", headers=h).json()["data"]["cac_by_channel"][0]
    assert row["estimated"] is False and row["attributed_spend"] == 0 and row["cac"] is None


def test_campaign_with_no_channel_mix_adds_nothing_to_cac(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h, channel_mix={})
    _metrics(
        client,
        h,
        [{"ts": date.today().isoformat(), "campaign_id": cid, "metric": "spend", "value": 50}],
    )
    assert client.get(f"{BASE}/analytics", headers=h).json()["data"]["cac_by_channel"] == []


# ---- CSV export (row 25) ----
def test_csv_export_lists_the_range_and_neutralises_formulas(client, db):
    _u, _s, h = _member(db)
    cid = _campaign(client, h, name='=HYPERLINK("http://evil")')
    today = date.today().isoformat()
    _metrics(
        client,
        h,
        [
            {"ts": today, "channel": "email", "metric": "clicks", "value": 7},
            {"ts": today, "campaign_id": cid, "metric": "spend", "value": 9},
            {"ts": today, "metric": "visits", "value": 3},
            {
                "ts": (date.today() - timedelta(days=100)).isoformat(),
                "metric": "visits",
                "value": 1,
            },
        ],
    )
    r = client.get(f"{BASE}/analytics/export?range=30d", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="marketing-metrics-30d.csv"' in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0] == "date,scope,channel,campaign,metric,value"
    assert len(lines) == 4  # header + 3 in range; the 100-day-old point is out
    assert f"{today},channel,email,,clicks,7" in lines
    import csv as _csv

    cells = [row for row in _csv.reader(io.StringIO(r.text)) if row[1] == "campaign"][0]
    assert cells[3] == '\'=HYPERLINK("http://evil")'  # leading quote defuses the formula
    assert f"{today},workspace,,,visits,3" in lines
    assert client.get(f"{BASE}/analytics/export?range=1y", headers=h).status_code == 422


# ---- media (rows 2, 13) ----
def _upload(client, h, content=PNG, ctype="image/png", name="hero.png"):
    return client.post(
        f"{BASE}/media", files={"file": (name, io.BytesIO(content), ctype)}, headers=h
    )


def test_upload_attach_and_serialize_marketing_media(client, db):
    _u, s, h = _member(db)
    r = _upload(client, h)
    assert r.status_code == 201, r.text
    m = r.json()["data"]
    assert (
        m["filename"] == "hero.png"
        and m["content_type"] == "image/png"
        and m["size_bytes"] == len(PNG)
    )
    row = db.get(MarketingMedia, m["id"])
    assert row.storage_key.startswith(f"marketing/{s.id}/") and row.storage_key.endswith(".png")
    e = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "With image", "channel": "organic_social", "media_id": m["id"]},
        headers=h,
    ).json()["data"]
    assert e["media_id"] == m["id"] and e["media"]["url"] == m["url"]
    listed = client.get(f"{BASE}/calendar-entries", headers=h).json()["data"]["entries"]
    assert listed[0]["media"]["id"] == m["id"]
    assert [x["id"] for x in client.get(f"{BASE}/media", headers=h).json()["data"]["media"]] == [
        m["id"]
    ]
    # deleting the media leaves the post, minus the image
    resp = client.delete(f"{BASE}/media/{m['id']}", headers=h)
    assert resp.json()["data"] == {"deleted": True}
    after = client.get(f"{BASE}/calendar-entries/{e['id']}", headers=h).json()["data"]
    assert after["media_id"] is None and after["media"] is None
    resp = client.delete(f"{BASE}/media/{m['id']}", headers=h)
    assert resp.status_code == 404


def test_marketing_media_never_appears_in_documents_or_to_other_roles(client, db):
    founder, s, h = _member(db)
    mentor, _s, hm = _member(db, role=MembershipRole.mentor, startup=s)
    m = _upload(client, h).json()["data"]
    # not in the all-roles Documents storage
    assert db.query(DocumentFile).filter_by(startup_id=s.id).count() == 0
    docs = client.get("/api/v1/documents/files", headers=hm)
    assert (
        docs.status_code == 200
        and docs.json()["data"] == []
        or docs.json()["data"].get("files", []) == []
    )
    # and the mentor cannot reach the marketing routes at all
    assert client.get(f"{BASE}/media", headers=hm).status_code == 403
    resp = client.delete(f"{BASE}/media/{m['id']}", headers=hm)
    assert resp.status_code == 403
    assert _upload(client, hm).status_code == 403


@pytest.mark.parametrize(
    "content,ctype,why",
    [
        (b"not an image at all", "image/png", "bytes do not match the declared type"),
        (PNG, "image/svg+xml", "svg is not allowed"),
        (PNG, "application/pdf", "not an image"),
        (b"", "image/png", "empty"),
    ],
)
def test_media_rejects_bad_files(client, db, content, ctype, why):
    _u, _s, h = _member(db)
    assert _upload(client, h, content=content, ctype=ctype).status_code == 422, why


def test_media_rejects_oversize_and_enforces_the_workspace_cap(client, db, monkeypatch):
    from app.services.marketing import media as media_svc

    _u, _s, h = _member(db)
    big = PNG + b"\x00" * (media_svc.MAX_MEDIA_BYTES + 1)
    assert _upload(client, h, content=big).status_code == 422
    monkeypatch.setattr(media_svc, "MAX_MEDIA_PER_WORKSPACE", 1)
    assert _upload(client, h).status_code == 201
    assert _upload(client, h).status_code == 422


def test_each_image_type_is_accepted_when_the_bytes_match(client, db):
    _u, _s, h = _member(db)
    samples = {
        "image/jpeg": b"\xff\xd8\xff\xe0" + b"\x00" * 8,
        "image/gif": b"GIF89a" + b"\x00" * 8,
        "image/webp": b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8,
    }
    for ctype, data in samples.items():
        assert _upload(client, h, content=data, ctype=ctype, name="x").status_code == 201, ctype


def test_a_post_cannot_attach_another_workspaces_media(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    foreign = _upload(client, h2).json()["data"]["id"]
    r = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "A", "channel": "email", "media_id": foreign},
        headers=h,
    )
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "media_id"
    resp = client.delete(f"{BASE}/media/{foreign}", headers=h)
    assert resp.status_code == 404


# ---- role gating: every new route is MARKETING_ROLES only ----
@pytest.mark.parametrize(
    "role",
    [
        MembershipRole.mentor,
        MembershipRole.business_consultant,
        MembershipRole.investor,
        MembershipRole.accountant,
        MembershipRole.legal_advisor,
    ],
)
def test_every_new_route_is_403_outside_marketing_roles(client, db, role):
    import uuid

    founder, s, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=s)
    some = uuid.uuid4()
    calls = [
        ("get", f"{BASE}/metrics"),
        ("delete", f"{BASE}/metrics/{some}"),
        ("get", f"{BASE}/analytics/export"),
        ("get", f"{BASE}/campaigns/{some}/performance"),
        ("get", f"{BASE}/media"),
        ("delete", f"{BASE}/media/{some}"),
        ("get", f"{BASE}/calendar-entries?campaign_id={some}"),
    ]
    for method, url in calls:
        r = getattr(client, method)(url, headers=h)
        assert r.status_code == 403, (method, url, r.status_code)
    up = client.post(
        f"{BASE}/media", files={"file": ("a.png", io.BytesIO(PNG), "image/png")}, headers=h
    )
    assert up.status_code == 403
    assert db.query(MarketingMedia).count() == 0


def test_a_team_member_can_use_the_new_routes(client, db):
    founder, s, _h = _member(db)
    _tm, _s, h = _member(db, role=MembershipRole.team_member, startup=s)
    assert _upload(client, h).status_code == 201
    assert client.get(f"{BASE}/metrics", headers=h).status_code == 200
    assert client.get(f"{BASE}/analytics/export", headers=h).status_code == 200
