"""Live Marketing Hub journeys.

`test_marketing_journey`: a founder onboards, checks the marketing overview,
lists the 8 lazy-seeded channels, activates one, schedules a calendar entry,
lists entries in a date range, publishes the entry, then re-checks the overview.

`test_marketing_campaigns_journey`: a founder creates an audience segment, then
a campaign targeting it, lists campaigns, walks the campaign through its full
lifecycle (draft -> active -> paused -> active -> completed, asserting
`launched_at`/`completed_at`), then confirms the segment's used-by view shows
the campaign.

Every response body along the way is captured to `e2e/_captures/marketing/*.json`
-- those files are the verbatim source for `docs/fe-integration-guide-marketing.md`.
They must be REAL bodies from this live run, complete and untrimmed.

Marketing is founder/team_member RBAC (app/db/tenancy.py:require_role), so these
journeys need nothing but a freshly onboarded founder + workspace -- no roadmap,
no assessment, no mission.
"""

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
        assert any(
            row["id"] == campaign_id for row in campaigns_list.json()["data"]["campaigns"]
        )
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
        assert any(
            row["id"] == campaign_id for row in segment_used_by.json()["data"]["campaigns"]
        )
        capture("marketing", "segment_used_by", segment_used_by)
