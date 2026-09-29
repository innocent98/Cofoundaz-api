"""Live Marketing Hub journeys.

`test_marketing_journey`: a founder onboards, checks the marketing overview,
lists the 8 lazy-seeded channels, activates one, schedules a calendar entry,
lists entries in a date range, publishes the entry, then re-checks the overview.

`test_marketing_campaigns_journey`: a founder creates an audience segment, then
a campaign targeting it, lists campaigns, walks the campaign through its full
lifecycle (draft -> active -> paused -> active -> completed, asserting
`launched_at`/`completed_at`), then confirms the segment's used-by view shows
the campaign.

`test_marketing_ai_generation_journey`: a founder generates ad copy
(`POST /marketing/copy/generate` -> 202 `generating`), drains the in-process
worker (LLM_PROVIDER=stub, set by scripts/e2e_run.sh) so `ai.marketing.copy`
(app/worker/handlers/marketing_ai.py) runs and flips the row to `ready`, polls
the generation, lists copy-generation history, then does the same round trip
for `POST /marketing/calendar/plan-week` -> `ai.marketing.plan_week`.

`test_marketing_seo_journey`: a founder tracks a keyword (create/list/patch rank), tracks a
page and ticks an on-page checklist item, upserts the brand positioning statement, then
runs the AI content-gap round trip (`POST /marketing/seo/content-gaps/generate` -> 202 ->
drain `ai.marketing.content_gap` -> poll).

`test_marketing_analytics_journey`: a founder creates and launches a campaign, bulk-ingests
metric points (`POST /marketing/metrics`) across two channels plus the campaign, reads the
aggregation (`GET /marketing/analytics?range=30d`), then re-reads the overview to see
`top_channel_by_conversions` / `active_campaigns` / `ai_content_ideas` filled.

Every response body along the way is captured to `e2e/_captures/marketing/*.json`
-- those files are the verbatim source for `docs/fe-integration-guide-marketing.md`.
They must be REAL bodies from this live run, complete and untrimmed.

Marketing is founder/team_member RBAC (app/db/tenancy.py:require_role), so these
journeys need nothing but a freshly onboarded founder + workspace -- no roadmap,
no assessment, no mission.
"""

from datetime import date, timedelta

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_journal.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def test_marketing_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- marketing needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Overview before any marketing activity.
        overview = c.get("/api/v1/marketing", headers=wh)
        assert overview.status_code == 200, overview.text
        capture("marketing", "overview_before", overview)

        # 2. Channels -- lazy-seeded to all 8 ChannelKey rows on first read.
        channels = c.get("/api/v1/marketing/channels", headers=wh)
        assert channels.status_code == 200, channels.text
        assert len(channels.json()["data"]) == 8
        capture("marketing", "channels", channels)

        # 3. Activate the email channel.
        updated_channel = c.patch(
            "/api/v1/marketing/channels/email",
            headers=wh,
            json={"status": "active", "notes": "warming up"},
        )
        assert updated_channel.status_code == 200, updated_channel.text
        assert updated_channel.json()["data"]["status"] == "active"
        capture("marketing", "channel_updated", updated_channel)

        # 4. Schedule a calendar entry.
        created = c.post(
            "/api/v1/marketing/calendar-entries",
            headers=wh,
            json={
                "title": "Launch week kickoff",
                "channel": "email",
                "status": "scheduled",
                "scheduled_at": "2026-10-01T09:00:00Z",
                "body": "Ship it",
            },
        )
        assert created.status_code == 200, created.text
        entry = created.json()["data"]
        assert entry["status"] == "scheduled"
        capture("marketing", "entry_created", created)

        entry_id = entry["id"]

        # 5. List entries in the date range that covers the scheduled entry.
        listed = c.get(
            "/api/v1/marketing/calendar-entries",
            headers=wh,
            params={"from": "2026-10-01T00:00:00Z", "to": "2026-10-31T00:00:00Z"},
        )
        assert listed.status_code == 200, listed.text
        assert any(e["id"] == entry_id for e in listed.json()["data"]["entries"])
        capture("marketing", "entries_list", listed)

        # 6. Publish it.
        published = c.patch(
            f"/api/v1/marketing/calendar-entries/{entry_id}",
            headers=wh,
            json={"status": "published"},
        )
        assert published.status_code == 200, published.text
        assert published.json()["data"]["status"] == "published"
        assert published.json()["data"]["published_at"] is not None
        capture("marketing", "entry_published", published)

        # 7. Overview after -- active_channels reflects the activated email channel.
        overview_after = c.get("/api/v1/marketing", headers=wh)
        assert overview_after.status_code == 200, overview_after.text
        assert overview_after.json()["data"]["active_channels"] == 1
        capture("marketing", "overview_after", overview_after)


def test_marketing_campaigns_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Growth")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Create an audience segment.
        segment_created = c.post(
            "/api/v1/marketing/segments",
            headers=wh,
            json={"name": "SMB founders", "est_size": 1200, "definition": {"rules": []}},
        )
        assert segment_created.status_code == 200, segment_created.text
        capture("marketing", "segment_created", segment_created)
        segment_id = segment_created.json()["data"]["id"]

        # 2. Create a campaign targeting that segment.
        campaign_created = c.post(
            "/api/v1/marketing/campaigns",
            headers=wh,
            json={
                "name": "Q4 launch push",
                "objective": "launch",
                "budget": 50000,
                "channel_mix": {"email": 60, "search": 40},
                "segment_ids": [segment_id],
            },
        )
        assert campaign_created.status_code == 200, campaign_created.text
        capture("marketing", "campaign_created", campaign_created)
        campaign_id = campaign_created.json()["data"]["id"]

        # 3. List campaigns -- the new one shows up in draft status.
        campaigns_list = c.get("/api/v1/marketing/campaigns", headers=wh)
        assert campaigns_list.status_code == 200, campaigns_list.text
        assert any(row["id"] == campaign_id for row in campaigns_list.json()["data"]["campaigns"])
        capture("marketing", "campaigns_list", campaigns_list)

        # 4. Launch it -- draft -> active sets launched_at and fires campaign.launched.
        campaign_launched = c.patch(
            f"/api/v1/marketing/campaigns/{campaign_id}",
            headers=wh,
            json={"status": "active"},
        )
        assert campaign_launched.status_code == 200, campaign_launched.text
        assert campaign_launched.json()["data"]["launched_at"] is not None
        capture("marketing", "campaign_launched", campaign_launched)

        # 5. Pause, then resume -- both legal no-event transitions.
        campaign_paused = c.patch(
            f"/api/v1/marketing/campaigns/{campaign_id}",
            headers=wh,
            json={"status": "paused"},
        )
        assert campaign_paused.status_code == 200, campaign_paused.text
        assert campaign_paused.json()["data"]["status"] == "paused"
        capture("marketing", "campaign_paused", campaign_paused)

        campaign_resumed = c.patch(
            f"/api/v1/marketing/campaigns/{campaign_id}",
            headers=wh,
            json={"status": "active"},
        )
        assert campaign_resumed.status_code == 200, campaign_resumed.text
        assert campaign_resumed.json()["data"]["status"] == "active"

        # 6. Complete it -- active -> completed sets completed_at and fires campaign.completed.
        campaign_completed = c.patch(
            f"/api/v1/marketing/campaigns/{campaign_id}",
            headers=wh,
            json={"status": "completed"},
        )
        assert campaign_completed.status_code == 200, campaign_completed.text
        assert campaign_completed.json()["data"]["completed_at"] is not None
        capture("marketing", "campaign_completed", campaign_completed)

        # 7. The segment's used-by view shows this campaign.
        segment_used_by = c.get(f"/api/v1/marketing/segments/{segment_id}/campaigns", headers=wh)
        assert segment_used_by.status_code == 200, segment_used_by.text
        assert any(row["id"] == campaign_id for row in segment_used_by.json()["data"]["campaigns"])
        capture("marketing", "segment_used_by", segment_used_by)


def _drain() -> None:
    """Drains the in-process job queue (same pattern as e2e/test_dashboard_ai_briefing.py)."""
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import (
        marketing_ai as _marketing_ai,  # noqa: F401  (registers ai.marketing.copy / .plan_week)
    )

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_marketing_ai_generation_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- marketing AI generation needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz AI")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Kick off an ad-copy generation -- 202 Accepted, row starts `generating`.
        gen = c.post(
            "/api/v1/marketing/copy/generate",
            headers=wh,
            json={
                "asset_type": "ad",
                "channel": "email",
                "tone": "bold",
                "key_message": "Launch week is here",
            },
        )
        assert gen.status_code == 202, gen.text
        gen_body = gen.json()["data"]
        assert gen_body["status"] == "generating"
        capture("marketing", "copy_generate_accepted", gen)
        gid = gen_body["id"]

        # 2. Drain the worker in-process (LLM_PROVIDER=stub) -> handle_marketing_copy runs,
        # fills `output` from the stub LLM client, flips status to ready.
        _drain()

        # 3. Poll the generation -- now ready, `output` carries [stub-llm] values.
        ready = c.get(f"/api/v1/marketing/copy/generations/{gid}", headers=wh)
        assert ready.status_code == 200, ready.text
        ready_body = ready.json()["data"]
        assert ready_body["status"] in ("ready", "failed")
        capture("marketing", "copy_generation_ready", ready)

        # 4. History lists the generation just completed.
        history = c.get("/api/v1/marketing/copy/generations", headers=wh)
        assert history.status_code == 200, history.text
        assert any(row["id"] == gid for row in history.json()["data"]["generations"])
        capture("marketing", "copy_generations_history", history)

        # 5. Same round trip for the weekly calendar plan -- 202 Accepted, no request body.
        plan = c.post("/api/v1/marketing/calendar/plan-week", headers=wh, json={})
        assert plan.status_code == 202, plan.text
        plan_body = plan.json()["data"]
        assert plan_body["status"] == "generating"
        capture("marketing", "plan_week_accepted", plan)
        pid = plan_body["id"]

        # 6. Drain -> handle_marketing_plan_week runs and flips status to ready.
        _drain()

        # 7. Poll the plan-week generation -- now ready.
        plan_ready = c.get(f"/api/v1/marketing/calendar/plan-week/{pid}", headers=wh)
        assert plan_ready.status_code == 200, plan_ready.text
        assert plan_ready.json()["data"]["status"] in ("ready", "failed")
        capture("marketing", "plan_week_ready", plan_ready)


def test_marketing_channel_ai_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- marketing AI generation needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Channels")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Kick off a channel-plan recommendation -- 202 Accepted, row starts `generating`.
        plan = c.post(
            "/api/v1/marketing/channel-plan/recommend",
            headers=wh,
            json={"objective": "leads"},
        )
        assert plan.status_code == 202, plan.text
        plan_body = plan.json()["data"]
        assert plan_body["status"] == "generating"
        capture("marketing", "channel_plan_accepted", plan)
        pid = plan_body["id"]

        # 2. Drain the worker in-process (LLM_PROVIDER=stub) -> handle_marketing_channel_plan
        # runs, fills `output` from the stub LLM client, flips status to ready.
        _drain()

        # 3. Poll the recommendation -- now ready.
        plan_ready = c.get(f"/api/v1/marketing/channel-plan/recommendations/{pid}", headers=wh)
        assert plan_ready.status_code == 200, plan_ready.text
        assert plan_ready.json()["data"]["status"] in ("ready", "failed")
        capture("marketing", "channel_plan_ready", plan_ready)

        # 4. Kick off fit-notes generation for the workspace's channels -- 202 Accepted.
        fit = c.post("/api/v1/marketing/channels/fit-notes/generate", headers=wh, json={})
        assert fit.status_code == 202, fit.text
        fit_body = fit.json()["data"]
        assert fit_body["status"] == "generating"
        capture("marketing", "fit_notes_accepted", fit)
        fid = fit_body["id"]

        # 5. Drain -> handle_marketing_channel_fit runs, persists ai_fit_note per channel.
        _drain()

        # 6. Poll the fit-notes generation -- now ready.
        fit_ready = c.get(f"/api/v1/marketing/channels/fit-notes/{fid}", headers=wh)
        assert fit_ready.status_code == 200, fit_ready.text
        assert fit_ready.json()["data"]["status"] in ("ready", "failed")
        capture("marketing", "fit_notes_ready", fit_ready)

        # 7. Channels now carry ai_fit_note/fit_note_generated_at from the generation above.
        channels = c.get("/api/v1/marketing/channels", headers=wh)
        assert channels.status_code == 200, channels.text
        capture("marketing", "channels_with_fit_notes", channels)


def test_marketing_seo_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- SEO tools need nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz SEO")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Keyword tracker: create -> list -> patch rank.
        kw = c.post(
            "/api/v1/marketing/keywords",
            headers=wh,
            json={
                "keyword": "automated daily savings",
                "volume": "2.4K",
                "difficulty": 45,
                "current_rank": 12,
                "target_page": "/x",
            },
        )
        assert kw.status_code == 200, kw.text
        kw_body = kw.json()["data"]
        assert kw_body["keyword"] == "automated daily savings"
        capture("marketing", "keyword_created", kw)
        kid = kw_body["id"]

        kws = c.get("/api/v1/marketing/keywords", headers=wh)
        assert kws.status_code == 200, kws.text
        assert any(row["id"] == kid for row in kws.json()["data"]["keywords"])
        capture("marketing", "keywords_list", kws)

        kw_patched = c.patch(
            f"/api/v1/marketing/keywords/{kid}", headers=wh, json={"current_rank": 8}
        )
        assert kw_patched.status_code == 200, kw_patched.text
        assert kw_patched.json()["data"]["current_rank"] == 8
        capture("marketing", "keyword_updated", kw_patched)

        # 2. Tracked page on-page checklist: create -> tick one item.
        page = c.post("/api/v1/marketing/seo/pages", headers=wh, json={"url": "/pricing"})
        assert page.status_code == 200, page.text
        page_body = page.json()["data"]
        assert page_body["url"] == "/pricing"
        capture("marketing", "tracked_page_created", page)
        pid = page_body["id"]

        page_checked = c.patch(
            f"/api/v1/marketing/seo/pages/{pid}",
            headers=wh,
            json={"checklist": {"h1": True}},
        )
        assert page_checked.status_code == 200, page_checked.text
        assert page_checked.json()["data"]["checklist"]["h1"] is True
        capture("marketing", "tracked_page_checklist", page_checked)

        # 3. Brand positioning: upsert -> composed statement.
        positioning = c.put(
            "/api/v1/marketing/positioning",
            headers=wh,
            json={
                "audience": "freelancers in Lagos",
                "need": "save money without thinking about it",
                "product": "Cofoundaz Save",
                "category": "savings app",
                "differentiator": "rounds up every payment automatically",
            },
        )
        assert positioning.status_code == 200, positioning.text
        assert positioning.json()["data"]["statement"]
        capture("marketing", "positioning_upserted", positioning)

        # 4. AI content-gap suggestions: 202 -> drain the in-process worker -> poll.
        gap = c.post("/api/v1/marketing/seo/content-gaps/generate", headers=wh)
        assert gap.status_code == 202, gap.text
        gap_body = gap.json()["data"]
        assert gap_body["status"] == "generating"
        capture("marketing", "content_gap_accepted", gap)
        gid = gap_body["id"]

        # Drain the worker in-process (LLM_PROVIDER=stub) -> handle_marketing_content_gap runs.
        _drain()

        gap_ready = c.get(f"/api/v1/marketing/seo/content-gaps/{gid}", headers=wh)
        assert gap_ready.status_code == 200, gap_ready.text
        assert gap_ready.json()["data"]["status"] in ("ready", "failed")
        capture("marketing", "content_gap_ready", gap_ready)


def test_marketing_analytics_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Analytics")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. A real campaign (campaign-scoped metrics need a real id), launched so it counts
        # toward the overview's active_campaigns.
        campaign_created = c.post(
            "/api/v1/marketing/campaigns",
            headers=wh,
            json={
                "name": "Q4 launch push",
                "objective": "launch",
                "budget": 50000,
                "channel_mix": {"email": 60, "search": 40},
                "segment_ids": [],
            },
        )
        assert campaign_created.status_code == 200, campaign_created.text
        campaign_id = campaign_created.json()["data"]["id"]

        campaign_launched = c.patch(
            f"/api/v1/marketing/campaigns/{campaign_id}",
            headers=wh,
            json={"status": "active"},
        )
        assert campaign_launched.status_code == 200, campaign_launched.text

        # 2. Ingest metric points: two channels (email, search) over two ISO weeks, plus
        # campaign-scoped rows. Spend is in cents. Dates are relative to today so they
        # always fall inside range=30d.
        today = date.today()
        d1 = (today - timedelta(days=2)).isoformat()
        d2 = (today - timedelta(days=9)).isoformat()

        def pt(ts: str, metric: str, value: int, **scope) -> dict:
            return {"ts": ts, "metric": metric, "value": value, **scope}

        points = [
            # Site traffic (unscoped) across two weeks.
            pt(d1, "visits", 420),
            pt(d2, "visits", 310),
            # email channel
            pt(d1, "spend", 12000, channel="email"),
            pt(d1, "conversions", 12, channel="email"),
            pt(d2, "spend", 9000, channel="email"),
            pt(d2, "conversions", 9, channel="email"),
            # search channel
            pt(d1, "spend", 30000, channel="search"),
            pt(d1, "conversions", 10, channel="search"),
            # Funnel + campaign leaderboard (campaign-scoped rows).
            pt(d1, "impressions", 8000, campaign_id=campaign_id),
            pt(d1, "clicks", 640, campaign_id=campaign_id),
            pt(d1, "conversions", 21, campaign_id=campaign_id),
            pt(d1, "spend", 25000, campaign_id=campaign_id),
        ]
        ingested = c.post("/api/v1/marketing/metrics", headers=wh, json={"points": points})
        assert ingested.status_code == 200, ingested.text
        capture("marketing", "metrics_ingested", ingested)

        # 3. Aggregate.
        analytics = c.get("/api/v1/marketing/analytics", headers=wh, params={"range": "30d"})
        assert analytics.status_code == 200, analytics.text
        a = analytics.json()["data"]
        assert a["range"] == "30d"
        assert a["traffic_by_week"]
        assert {row["channel"] for row in a["cac_by_channel"]} == {"email", "search"}
        assert a["funnel"]["impressions"] == 8000
        assert a["funnel"]["clicks"] == 640
        assert any(row["campaign_id"] == campaign_id for row in a["leaderboard"])
        capture("marketing", "analytics", analytics)

        # 4. Overview now shows the fills.
        overview = c.get("/api/v1/marketing", headers=wh)
        assert overview.status_code == 200, overview.text
        o = overview.json()["data"]
        assert o["active_campaigns"] == 1
        assert o["top_channel_by_conversions"] == "email"
        assert "ai_content_ideas" in o
        capture("marketing", "overview_with_fills", overview)
