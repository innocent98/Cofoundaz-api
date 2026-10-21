"""Marketing re-walk BE gaps: newest-run idea count, no past plan slots, item deep links in
search, stale content-gap runs, and a served media file."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.enums import (
    CampaignObjective,
    CampaignStatus,
    ContentStatus,
    MarketingGenerationKind,
    MarketingGenerationStatus,
    MembershipRole,
)
from app.db.models.marketing import (
    AudienceSegment,
    Campaign,
    ContentCalendarEntry,
    MarketingAiGeneration,
)
from app.worker.handlers.marketing_ai import (
    handle_marketing_content_gap,
    handle_marketing_plan_week,
)
from tests.api.test_marketing_prd_b import _gen, _job, _llm, _member

BASE = "/api/v1/marketing"


def _run(db, s, gaps, age_days, status=MarketingGenerationStatus.ready, **inputs):
    db.add(
        MarketingAiGeneration(
            startup_id=s.id,
            kind=MarketingGenerationKind.content_gap,
            status=status,
            inputs=inputs,
            output={"gaps": gaps},
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
    )
    db.flush()


# ---- gap 1: the overview counts the newest ready run only ----
def test_overview_ideas_count_is_the_newest_ready_run_not_the_sum(client, db):
    _u, s, h = _member(db)
    _run(db, s, [1] * 7, age_days=3)
    _run(db, s, [1] * 7, age_days=1)
    _run(db, s, [1] * 2, age_days=0, status=MarketingGenerationStatus.failed)
    assert client.get(BASE, headers=h).json()["data"]["ai_content_ideas"] == 7


# ---- gap 4: plan-week slots are never in the past ----
@pytest.mark.parametrize("hour", [3, 8, 9, 10, 23])
def test_plan_week_slots_start_after_now_and_never_repeat(db, monkeypatch, hour):
    u, s, _h = _member(db)
    now = datetime(2026, 10, 21, hour, 17, tzinfo=UTC)
    monkeypatch.setattr("app.worker.handlers.marketing_ai._now", lambda: now)
    entries = [
        {"title": f"t{i}", "channel": "email", "body": "b", "day_offset": o}
        for i, o in enumerate([0, 0, 1, 3, 3])
    ]
    _llm(monkeypatch, {"entries": entries})
    g = _gen(db, s, u, MarketingGenerationKind.plan_week, {})
    handle_marketing_plan_week(db, _job("plan_week", g))
    slots = [datetime.fromisoformat(e["scheduled_at"]) for e in g.output["entries"]]
    assert len(slots) == 5 and all(x > now for x in slots)
    assert slots == sorted(slots) and len(set(slots)) == 5
    assert all(x.hour == 9 and x.minute == 0 for x in slots)
    assert slots[0].date() == (now.date() if hour < 9 else now.date() + timedelta(days=1))


def test_plan_week_skips_slots_already_taken_on_the_calendar(db, monkeypatch):
    u, s, _h = _member(db)
    now = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
    monkeypatch.setattr("app.worker.handlers.marketing_ai._now", lambda: now)
    db.add(
        ContentCalendarEntry(
            startup_id=s.id,
            created_by=u.id,
            title="taken",
            channel="email",
            body="",
            status=ContentStatus.scheduled,
            scheduled_at=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        )
    )
    db.flush()
    _llm(
        monkeypatch,
        {"entries": [{"title": "a", "channel": "email", "body": "b", "day_offset": 0}]},
    )
    g = _gen(db, s, u, MarketingGenerationKind.plan_week, {})
    handle_marketing_plan_week(db, _job("plan_week", g))
    assert g.output["entries"][0]["scheduled_at"].startswith("2026-10-22T09:00")


# ---- gap 5: search hits link to the item ----
def test_search_links_campaign_and_post_to_the_item(client, db):
    u, s, h = _member(db)
    c = Campaign(
        startup_id=s.id,
        name="Zeppelin push",
        objective=CampaignObjective.launch,
        budget=0,
        channel_mix={},
        status=CampaignStatus.draft,
    )
    p = ContentCalendarEntry(
        startup_id=s.id,
        created_by=u.id,
        title="Zeppelin teaser",
        channel="email",
        body="",
        status=ContentStatus.draft,
    )
    seg = AudienceSegment(startup_id=s.id, name="Zeppelin fans", definition={})
    db.add_all([c, p, seg])
    db.flush()
    r = client.get("/api/v1/search", params={"q": "Zeppelin"}, headers=h)
    by_type = {x["type"]: x["href"] for x in r.json()["data"]["results"]}
    assert by_type["campaign"] == f"/marketing/campaigns/{c.id}"
    assert by_type["post"] == f"/marketing/calendar?entry={p.id}"
    assert by_type["segment"] == "/marketing/segments"  # no segment detail route exists


# ---- gap 9: a content-gap run says when the brand context has changed ----
def test_content_gap_run_is_fresh_then_stale_after_the_brand_context_changes(
    client, db, monkeypatch
):
    u, s, h = _member(db)
    _llm(monkeypatch, {"gaps": [{"title": "t", "target_keyword": "k", "angle": "a"}]})
    g = _gen(db, s, u, MarketingGenerationKind.content_gap, {})
    handle_marketing_content_gap(db, _job("content_gap", g))
    assert g.inputs["context_hash"]
    one = client.get(f"{BASE}/seo/content-gaps/{g.id}", headers=h).json()["data"]
    assert one["stale"] is False and "context_hash" not in one["inputs"]
    listed = client.get(f"{BASE}/seo/content-gaps", headers=h).json()["data"]["generations"]
    assert listed[0]["stale"] is False
    s.industry = "Agriculture"  # the brand context changed after the run
    db.flush()
    assert client.get(f"{BASE}/seo/content-gaps/{g.id}", headers=h).json()["data"]["stale"] is True


def test_a_run_without_a_stored_hash_is_reported_stale(client, db):
    _u, s, h = _member(db)
    _run(db, s, [1], age_days=1)
    listed = client.get(f"{BASE}/seo/content-gaps", headers=h).json()["data"]["generations"]
    assert listed[0]["stale"] is True


# ---- gap 7: a media file the browser can load ----
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_media_file_route_serves_the_image_to_marketing_roles_only(
    client, db, tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    u, s, h = _member(db)
    up = client.post(f"{BASE}/media", headers=h, files={"file": ("a.png", PNG, "image/png")})
    mid = up.json()["data"]["id"]
    r = client.get(f"{BASE}/media/{mid}/file", headers=h)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=3600"
    assert r.headers["x-content-type-options"] == "nosniff"
    _m, _s, mh = _member(db, role=MembershipRole.mentor, startup=s)
    assert client.get(f"{BASE}/media/{mid}/file", headers=mh).status_code == 403
    _u2, _s2, oh = _member(db)
    assert client.get(f"{BASE}/media/{mid}/file", headers=oh).status_code == 404
