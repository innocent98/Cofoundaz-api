"""Live Marketing PRD gap build (b): AI context, copy platform limits + refine, fit level and
channel stats, weekly campaign readout, channel auto-status on launch, keyword stamps, segment
definition validation, one channel per post.

AI jobs run with the STUB model (LLM_PROVIDER=stub), so AI text reads `[stub-llm] ...` and the
stub ignores the prompt: what the live run proves is the wiring (routes, jobs, stored shapes,
the notification), not the quality of real-model text. Prompt content (context, rules, limits,
retry on placeholders) is covered by unit tests that capture the prompts.

Every body is captured to `e2e/_captures/marketing_prd_b/*.json` (source of
`docs/fe-integration-guide-marketing-prd.md` §7 onwards).

Keep app-internal imports LAZY: the remote live-e2e gate has no app env.
"""

from datetime import UTC, date, datetime, timedelta

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
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


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import scheduled  # noqa: F401
    from app.worker.handlers import (  # noqa: F401  (registers ai.marketing.* and the readout job)
        marketing_ai,
    )

    register_notifications()
    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _tick(now: datetime) -> None:
    from app.db.session import SessionLocal
    from app.worker.scheduler import scheduler_tick

    db = SessionLocal()
    try:
        scheduler_tick(db, now=now)
    finally:
        db.close()


def test_marketing_prd_b_journey(base_url, make_verified_user, capture, capture_json):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="NovaPay")
        done = c.post("/api/v1/onboarding/complete", headers=auth)
        assert done.status_code == 200, done.text
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}
        m = "/api/v1/marketing"

        # 1. Copy: the platform table, then a generation with a platform limit.
        plats = c.get(f"{m}/copy/platforms", headers=wh)
        assert plats.status_code == 200, plats.text
        capture("marketing_prd_b", "copy_platforms", plats)
        gen = c.post(
            f"{m}/copy/generate",
            headers=wh,
            json={
                "asset_type": "ad",
                "channel": "organic_social",
                "platform": "x",
                "tone": "bold",
                "key_message": "Settle the same day",
            },
        )
        assert gen.status_code == 202, gen.text
        gid = gen.json()["data"]["id"]
        _drain()
        ready = c.get(f"{m}/copy/generations/{gid}", headers=wh)
        body = ready.json()["data"]
        assert body["status"] == "ready"
        assert body["output"]["platform"] == "x" and body["output"]["limit"] == 280
        assert len(body["output"]["lengths"]) == len(body["output"]["variants"])  # stub returns one
        capture("marketing_prd_b", "copy_generation_ready_with_platform", ready)

        # 2. Refine variant 1.
        ref = c.post(
            f"{m}/copy/generations/{gid}/refine",
            headers=wh,
            json={"variant_index": 0, "instruction": "Make it shorter"},
        )
        assert ref.status_code == 202, ref.text
        capture("marketing_prd_b", "refine_accepted", ref)
        rid = ref.json()["data"]["id"]
        _drain()
        refined = c.get(f"{m}/copy/generations/{rid}", headers=wh)
        rbody = refined.json()["data"]
        assert rbody["status"] == "ready"
        assert rbody["inputs"]["key_message"] == "Settle the same day"
        assert rbody["inputs"]["refine"]["parent_generation_id"] == gid
        capture("marketing_prd_b", "refine_generation_ready", refined)
        history = c.get(f"{m}/copy/generations", headers=wh).json()["data"]["generations"]
        assert {gid, rid} <= {g["id"] for g in history}
        pending = c.post(
            f"{m}/copy/generate",
            headers=wh,
            json={"asset_type": "ad", "tone": "bold", "key_message": "Another"},
        )
        early = c.post(
            f"{m}/copy/generations/{pending.json()['data']['id']}/refine",
            headers=wh,
            json={"variant_index": 0, "instruction": "x"},
        )
        assert early.status_code == 422
        capture("marketing_prd_b", "refine_not_ready_422", early)
        bad_platform = c.post(
            f"{m}/copy/generate",
            headers=wh,
            json={"asset_type": "ad", "tone": "bold", "key_message": "k", "platform": "myspace"},
        )
        assert bad_platform.status_code == 422
        capture("marketing_prd_b", "copy_unknown_platform_422", bad_platform)

        # 3. Fit notes with levels + channel stats.
        c.get(f"{m}/channels", headers=wh)
        today = date.today().isoformat()
        pts = [
            {"ts": today, "channel": "email", "metric": "clicks", "value": 40},
            {"ts": today, "channel": "email", "metric": "spend", "value": 900},
        ]
        resp = c.post(f"{m}/metrics", headers=wh, json={"points": pts})
        assert resp.status_code == 200
        fit = c.post(f"{m}/channels/fit-notes/generate", headers=wh)
        assert fit.status_code == 202, fit.text
        fid = fit.json()["data"]["id"]
        _drain()
        fit_ready = c.get(f"{m}/channels/fit-notes/{fid}", headers=wh)
        assert fit_ready.json()["data"]["status"] == "ready"
        capture("marketing_prd_b", "fit_notes_ready", fit_ready)
        chans = c.get(f"{m}/channels", headers=wh)
        by = {x["key"]: x for x in chans.json()["data"]}
        assert by["email"]["fit_level"] == "high"  # the stub picks the first enum value
        assert by["email"]["stats_30d"]["clicks"] == 40 and by["email"]["stats_30d"]["has_data"]
        assert by["search"]["stats_30d"]["has_data"] is False
        capture("marketing_prd_b", "channels_after_fit_notes", chans)

        # 4. Launching a campaign moves its channels to Testing.
        camp = c.post(
            f"{m}/campaigns",
            headers=wh,
            json={
                "name": "Waitlist push",
                "objective": "launch",
                "channel_mix": {"email": 60, "search": 40, "events": 0},
            },
        )
        cid = camp.json()["data"]["id"]
        c.patch(f"{m}/channels/search", headers=wh, json={"status": "active"})
        launched = c.patch(f"{m}/campaigns/{cid}", headers=wh, json={"status": "active"})
        assert launched.status_code == 200, launched.text
        after = {x["key"]: x["status"] for x in c.get(f"{m}/channels", headers=wh).json()["data"]}
        assert after["email"] == "testing" and after["search"] == "active"
        assert after["events"] == "not_started"
        capture_json("marketing_prd_b", "channel_statuses_after_launch", after)

        # 5. Weekly readout: scheduler tick (a Monday-or-later 09:00 UTC) + worker.
        # The readout reads the 7 days before today against the 7 before those (today excluded).
        d0 = date.fromisoformat(today)
        yesterday, week_ago = d0 - timedelta(days=1), d0 - timedelta(days=8)
        pts = [
            {"ts": d.isoformat(), "campaign_id": cid, "metric": mt, "value": v}
            for d, vals in (
                (yesterday, (2000, 100, 4, 150000)),
                (week_ago, (1600, 64, 2, 120050)),
            )
            for mt, v in zip(("impressions", "clicks", "conversions", "spend"), vals, strict=True)
        ]
        resp = c.post(f"{m}/metrics", headers=wh, json={"points": pts})
        assert resp.status_code == 200
        now = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
        _tick(now)
        _drain()
        readouts = c.get(f"{m}/campaigns/{cid}/readouts", headers=wh)
        assert readouts.status_code == 200, readouts.text
        rows = readouts.json()["data"]["readouts"]
        assert len(rows) == 1 and rows[0]["status"] == "ready"
        fig = rows[0]["figures"]
        assert fig["this_week"]["spend"] == {"value": 150000, "display": "₦1,500.00"}
        assert fig["this_week"]["ctr"]["display"] == "5.0%"
        assert fig["this_week"]["cac"]["display"] == "₦375.00"
        assert fig["change"]["clicks"]["display"] == "+56.3%"
        assert rows[0]["output"]["source"] == "ai" and "figures" not in rows[0]["output"]
        capture("marketing_prd_b", "campaign_readouts", readouts)
        notes = c.get("/api/v1/notifications", headers=wh).json()["data"]["notifications"]
        mine = [n for n in notes if n["type"] == "marketing.campaign.readout.ready"]
        assert len(mine) == 1 and mine[0]["body"] == "See results"
        assert mine[0]["title"] == "Campaign performance summary ready: Waitlist push"
        capture_json("marketing_prd_b", "notification_readout_ready", mine[0])
        _tick(now)
        _drain()
        again = c.get(f"{m}/campaigns/{cid}/readouts", headers=wh).json()["data"]["readouts"]
        assert len(again) == 1  # once per week

        # 6. Keywords: manual estimates, stamped when typed.
        kw = c.post(
            f"{m}/keywords", headers=wh, json={"keyword": "same day settlement", "volume": "1.2k"}
        )
        assert kw.status_code == 200, kw.text
        assert kw.json()["data"]["source"] == "manual"
        assert kw.json()["data"]["metrics_updated_at"] is not None
        capture("marketing_prd_b", "keyword_with_manual_metrics", kw)
        bare = c.post(f"{m}/keywords", headers=wh, json={"keyword": "payments"})
        assert bare.json()["data"]["metrics_updated_at"] is None
        capture("marketing_prd_b", "keyword_without_metrics", bare)

        # 7. Segments: definition attributes are validated.
        attrs = c.get(f"{m}/segments/attributes", headers=wh)
        assert attrs.status_code == 200, attrs.text
        capture("marketing_prd_b", "segment_attributes", attrs)
        seg = c.post(
            f"{m}/segments",
            headers=wh,
            json={
                "name": "Lagos merchants",
                "definition": {
                    "location": " Lagos ",
                    "channel_acquired": "email",
                    "behaviour_tags": ["trial", "trial", "mobile first"],
                },
                "est_size": 1200,
            },
        )
        assert seg.status_code == 200, seg.text
        capture("marketing_prd_b", "segment_created_cleaned", seg)
        bad = c.post(
            f"{m}/segments",
            headers=wh,
            json={
                "name": "Bad",
                "definition": {"favourite_colour": "blue", "channel_acquired": "tiktok"},
            },
        )
        assert bad.status_code == 422
        capture("marketing_prd_b", "segment_definition_422", bad)

        # 8. One channel per post.
        multi = c.post(
            f"{m}/calendar-entries", headers=wh, json={"title": "T", "channel": ["email", "search"]}
        )
        assert multi.status_code == 422
        capture("marketing_prd_b", "post_two_channels_422", multi)
