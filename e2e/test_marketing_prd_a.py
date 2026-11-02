"""Live Marketing PRD gap build (a): the scheduler publishes due posts, campaign stats and
performance, campaign links, metric list/delete + single scope, CSV export, marketing media.

One journey. A founder onboards, creates a campaign, schedules a post that is already due (linked
to the campaign, with an uploaded image), the scheduler tick + worker drain publish it and the
founder is notified, then metrics are recorded/rejected/listed/deleted and the campaign list,
performance, CAC and CSV export read them back.

Every body is captured to `e2e/_captures/marketing_prd_a/*.json`; those files are the verbatim
source of `docs/fe-integration-guide-marketing-prd.md`.

Not exercised live (covered by unit tests, labelled in the guide): the 403s for mentor and
consultant (a second invited user is needed), the "Post published:" copy for a manual publish
(the actor is not notified, so a second user is needed), the 5 MB and 200-image limits.

Keep app-internal imports LAZY (inside helpers): the remote live-e2e gate has no app env.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

# A real 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)
_CAPTURES = Path(__file__).parent / "_captures" / "marketing_prd_a"


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


def _tick_and_drain(now: datetime) -> None:
    """One scheduler tick, then drain the worker queue, in-process (same as the real loop)."""
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import scheduled  # noqa: F401  (registers the job handlers)
    from app.worker.scheduler import scheduler_tick

    register_notifications()
    db = SessionLocal()
    try:
        scheduler_tick(db, now=now)
    finally:
        db.close()
    db2 = SessionLocal()
    try:
        for _ in range(500):  # the jobs table is shared with every earlier journey
            if runner.run_once(db2) == 0:
                break
    finally:
        db2.close()


def test_marketing_prd_a_journey(base_url, make_verified_user, capture, capture_json):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="NovaPay")
        resp = c.post("/api/v1/onboarding/complete", headers=auth)
        assert resp.status_code == 200
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}
        m = "/api/v1/marketing"

        # 1. A campaign to attach things to.
        camp = c.post(
            f"{m}/campaigns",
            headers=wh,
            json={
                "name": "Waitlist push",
                "objective": "launch",
                "budget": 50000,
                "channel_mix": {"email": 60, "search": 40},
                "period_start": date.today().isoformat(),
                "period_end": (date.today() + timedelta(days=14)).isoformat(),
            },
        )
        assert camp.status_code == 200, camp.text
        cid = camp.json()["data"]["id"]
        capture("marketing_prd_a", "campaign_created", camp)

        # 2. Marketing media: upload an image (marketing-scoped, not Documents).
        up = c.post(f"{m}/media", headers=wh, files={"file": ("hero.png", _PNG, "image/png")})
        assert up.status_code == 201, up.text
        media = up.json()["data"]
        capture("marketing_prd_a", "media_uploaded", up)
        bad = c.post(f"{m}/media", headers=wh, files={"file": ("x.png", b"not a png", "image/png")})
        assert bad.status_code == 422
        capture("marketing_prd_a", "media_rejected_not_an_image", bad)
        svg = c.post(
            f"{m}/media", headers=wh, files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}
        )
        assert svg.status_code == 422
        capture("marketing_prd_a", "media_rejected_type", svg)
        listed = c.get(f"{m}/media", headers=wh)
        assert [x["id"] for x in listed.json()["data"]["media"]] == [media["id"]]
        capture("marketing_prd_a", "media_list", listed)
        documents = c.get("/api/v1/documents/files", headers=wh)
        assert documents.status_code == 200
        assert media["filename"] not in documents.text  # never in the all-roles Documents list
        capture("marketing_prd_a", "documents_files_after_media_upload", documents)

        # 3. A post that is already due: scheduled in the past, linked to the campaign + image.
        due_at = datetime.now(UTC) - timedelta(minutes=3)
        post = c.post(
            f"{m}/calendar-entries",
            headers=wh,
            json={
                "title": "Waitlist teaser",
                "channel": "organic_social",
                "status": "scheduled",
                "scheduled_at": due_at.isoformat(),
                "body": "Something is coming.",
                "campaign_id": cid,
                "media_id": media["id"],
            },
        )
        assert post.status_code == 200, post.text
        pid = post.json()["data"]["id"]
        assert post.json()["data"]["status"] == "scheduled"
        capture("marketing_prd_a", "post_scheduled_before_tick", post)

        # 4. The scheduler tick + worker publish it. Status only: nothing is sent anywhere.
        _tick_and_drain(datetime.now(UTC))
        after = c.get(f"{m}/calendar-entries/{pid}", headers=wh)
        assert after.status_code == 200, after.text
        body = after.json()["data"]
        assert body["status"] == "published"
        assert body["published_at"][:19] == due_at.isoformat()[:19]
        capture("marketing_prd_a", "post_after_tick", after)
        notes = c.get("/api/v1/notifications", headers=wh).json()["data"]["notifications"]
        mine = [n for n in notes if n["type"] == "marketing.post.published"]
        assert len(mine) == 1
        assert mine[0]["title"] == "Scheduled post published: Waitlist teaser"
        assert mine[0]["body"] == "See results"
        assert mine[0]["data"]["href"] == f"/marketing/calendar?entry={pid}"
        capture_json("marketing_prd_a", "notification_post_published", mine[0])

        # 5. Posts filter by campaign.
        by_campaign = c.get(f"{m}/calendar-entries?campaign_id={cid}", headers=wh)
        assert [e["id"] for e in by_campaign.json()["data"]["entries"]] == [pid]
        capture("marketing_prd_a", "posts_filtered_by_campaign", by_campaign)

        # 6. Metrics: both scopes at once is rejected; one scope each is fine.
        today = date.today().isoformat()
        both = c.post(
            f"{m}/metrics",
            headers=wh,
            json={
                "points": [
                    {
                        "ts": today,
                        "channel": "email",
                        "campaign_id": cid,
                        "metric": "spend",
                        "value": 100,
                    }
                ]
            },
        )
        assert both.status_code == 422
        capture("marketing_prd_a", "metric_two_scopes_rejected", both)
        points = [
            {"ts": today, "campaign_id": cid, "metric": "impressions", "value": 2000},
            {"ts": today, "campaign_id": cid, "metric": "clicks", "value": 100},
            {"ts": today, "campaign_id": cid, "metric": "conversions", "value": 10},
            {"ts": today, "campaign_id": cid, "metric": "spend", "value": 20000},
            {"ts": today, "channel": "email", "metric": "spend", "value": 5000},
            {"ts": today, "channel": "email", "metric": "conversions", "value": 5},
            {"ts": today, "metric": "visits", "value": 300},
            {"ts": today, "metric": "visits", "value": 999},  # a typo to delete
        ]
        ing = c.post(f"{m}/metrics", headers=wh, json={"points": points})
        assert ing.status_code == 200, ing.text
        capture("marketing_prd_a", "metrics_ingested", ing)

        # 7. List recorded entries, delete the typo.
        rec = c.get(f"{m}/metrics?range=30d&limit=3", headers=wh)
        assert rec.status_code == 200, rec.text
        assert rec.json()["data"]["total"] == 8 and len(rec.json()["data"]["metrics"]) == 3
        capture("marketing_prd_a", "metrics_list_page", rec)
        every = c.get(f"{m}/metrics?range=30d", headers=wh).json()["data"]["metrics"]
        typo = next(x for x in every if x["value"] == 999)
        gone = c.delete(f"{m}/metrics/{typo['id']}", headers=wh)
        assert gone.status_code == 200, gone.text
        capture("marketing_prd_a", "metric_deleted", gone)
        resp = c.delete(f"{m}/metrics/{typo['id']}", headers=wh)
        assert resp.status_code == 404

        # 8. Campaign list stats, performance, and the estimated CAC.
        camps = c.get(f"{m}/campaigns", headers=wh)
        row = camps.json()["data"]["campaigns"][0]
        assert row["metrics"]["click_through_rate"] == 5.0
        assert row["metrics"]["conversion_rate"] == 10.0
        capture("marketing_prd_a", "campaigns_list_with_stats", camps)
        perf = c.get(f"{m}/campaigns/{cid}/performance", headers=wh)
        assert perf.status_code == 200, perf.text
        assert perf.json()["data"]["totals"]["clicks"] == 100
        capture("marketing_prd_a", "campaign_performance", perf)
        badrange = c.get(f"{m}/campaigns/{cid}/performance?range=1y", headers=wh)
        assert badrange.status_code == 422
        capture("marketing_prd_a", "campaign_performance_bad_range", badrange)
        ana = c.get(f"{m}/analytics?range=30d", headers=wh)
        cac = {r["channel"]: r for r in ana.json()["data"]["cac_by_channel"]}
        assert cac["email"]["estimated"] is True
        assert cac["email"]["spend"] == 5000 + 12000
        capture("marketing_prd_a", "analytics_with_estimated_cac", ana)

        # 9. CSV export (not the JSON envelope).
        exp = c.get(f"{m}/analytics/export?range=30d", headers=wh)
        assert exp.status_code == 200 and exp.headers["content-type"].startswith("text/csv")
        assert exp.text.splitlines()[0] == "date,scope,channel,campaign,metric,value"
        _CAPTURES.mkdir(parents=True, exist_ok=True)
        (_CAPTURES / "analytics_export.json").write_text(
            json.dumps(
                {
                    "status": exp.status_code,
                    "content-type": exp.headers["content-type"],
                    "content-disposition": exp.headers["content-disposition"],
                    "body": exp.text,
                },
                indent=2,
            )
            + "\n"
        )

        # 10. Deleting the media leaves the post, minus the image.
        resp = c.delete(f"{m}/media/{media['id']}", headers=wh)
        assert resp.status_code == 200
        post_again = c.get(f"{m}/calendar-entries/{pid}", headers=wh)
        assert post_again.json()["data"]["media"] is None
        capture("marketing_prd_a", "post_after_media_deleted", post_again)
