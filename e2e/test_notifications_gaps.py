"""Live Notifications PRD-gap journey (N1 to N9): inbox filters + counts, priority and module on
every row, archive / restore / archived list, the Preferences grid (in-app + email per category,
critical alerts locked on), digest settings, quiet hours, and the digest email a worker really
sends. Events are published in-process into the same database the server reads (the same pattern
as e2e/test_journal.py), so every HTTP body is from the live server.

Captures land in `e2e/_captures/notifications_gaps/*.json` and are the verbatim source of
`docs/fe-integration-guide-notifications-inbox.md`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

_CAPTURE_DIR = Path(__file__).parent / "_captures" / "notifications_gaps"


def _write(name: str, data) -> None:
    _CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    (_CAPTURE_DIR / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Acme Labs"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]}
    )
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def _publish(startup_id: str, event: str, extra: dict | None = None) -> None:
    from app.db.session import SessionLocal
    from app.platform.events import event_bus
    from app.services.notifications import registry

    registry.register()
    db = SessionLocal()
    try:
        event_bus.publish(db, event, {"startup_id": startup_id, **(extra or {})})
        db.commit()
    finally:
        db.close()


def _tick_and_drain(now: datetime) -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import email as _e  # noqa: F401
    from app.worker.handlers import scheduled as _s  # noqa: F401
    from app.worker.scheduler import scheduler_tick

    db = SessionLocal()
    try:
        scheduler_tick(db, now=now)
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_notifications_gaps_journey(base_url, make_verified_user, mailbox, capture):
    g = "notifications_gaps"
    n = "/api/v1/notifications"
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(token)
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wid = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": wid}

        # 1. Six notifications of different kinds land in the founder's inbox.
        _publish(wid, "mission.ready", {"first_task_title": "Run 10 customer interviews"})
        _publish(wid, "dashboard.briefing.ready", {"briefing_id": "b1"})
        _publish(
            wid,
            "roadmap.milestone.due_soon",
            {"milestone_id": "m1", "title": "Validate demand", "relative": "tomorrow"},
        )
        _publish(wid, "roadmap.milestone.overdue", {"milestone_id": "m2"})
        _publish(wid, "finance.runway.low", {})
        _publish(wid, "finance.invoice.paid", {"number": "INV-2026-001"})

        r = c.get(f"{n}?limit=3", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["next_cursor"]
        capture(g, "inbox_page1", r)
        r = c.get(f"{n}/counts", headers=wh)
        counts = r.json()["data"]
        assert counts["all"] == 6 and counts["urgent"] == 2 and counts["deadlines"] == 2
        capture(g, "counts", r)
        for chip in ("urgent", "deadlines", "ai", "mentions", "billing", "unread"):
            r = c.get(f"{n}?filter={chip}", headers=wh)
            assert r.status_code == 200, r.text
            capture(g, f"filter_{chip}", r)
        r = c.get(f"{n}?filter=nonsense", headers=wh)
        assert r.status_code == 422
        capture(g, "filter_invalid", r)

        # 2. Archive one, see it leave the inbox and the unread count, restore it.
        rows = c.get(f"{n}?limit=50", headers=wh).json()["data"]["notifications"]
        target = next(x for x in rows if x["type"] == "finance.invoice.paid")
        r = c.post(f"{n}/{target['id']}/archive", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["archived"] is True
        capture(g, "archive", r)
        r = c.get(f"{n}?archived=true", headers=wh)
        assert [x["id"] for x in r.json()["data"]["notifications"]] == [target["id"]]
        capture(g, "archived_list", r)
        r = c.get(f"{n}/unread-count", headers=wh)
        assert r.json()["data"]["unread"] == 5
        capture(g, "unread_after_archive", r)
        r = c.post(f"{n}/{target['id']}/restore", headers=wh)
        assert r.json()["data"]["archived"] is False
        capture(g, "restore", r)

        # 3. Preferences: the grid, in-app off for a category, critical stays on.
        r = c.get(f"{n}/preferences", headers=wh)
        assert r.status_code == 200
        capture(g, "preferences_default", r)
        r = c.put(f"{n}/preferences", headers=wh, json={"in_app": {"roadmap_missions": False}})
        assert r.json()["data"]["in_app"]["roadmap_missions"] is False
        capture(g, "preferences_in_app_off", r)
        r = c.put(f"{n}/preferences", headers=wh, json={"in_app": {"nope": True}})
        assert r.status_code == 422
        capture(g, "preferences_unknown_category", r)
        before = c.get(f"{n}/counts", headers=wh).json()["data"]["all"]
        _publish(wid, "mission.completed", {})  # roadmap_missions category: now switched off
        _publish(
            wid, "roadmap.milestone.overdue", {"milestone_id": "m3"}
        )  # critical: still arrives
        r = c.get(f"{n}/counts", headers=wh)
        assert r.json()["data"]["all"] == before + 1
        capture(g, "counts_after_in_app_off", r)

        # 4. Digest + quiet hours settings, then the worker sends the digest.
        r = c.put(
            f"{n}/preferences",
            headers=wh,
            json={
                "categories": {"finance": True},
                "digest_mode": "daily",
                "digest_time": "23:59",
                "quiet_enabled": True,
                "quiet_from": "20:00",
                "quiet_until": "06:00",
                "timezone": "UTC",
            },
        )
        assert r.status_code == 200, r.text
        capture(g, "preferences_digest_quiet", r)
        # Flush the live emails queued so far (a tick before 23:59 UTC is not digest time yet).
        _tick_and_drain(datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0))
        before_mail = mailbox.count_for(u["email"])
        _publish(wid, "finance.invoice.paid", {"number": "INV-2026-001"})  # held for the digest, not emailed live
        _tick_and_drain(datetime.now(UTC).replace(hour=23, minute=59, second=30, microsecond=0))
        assert mailbox.count_for(u["email"]) == before_mail + 1
        msg = mailbox.latest_for(u["email"])
        assert msg["subject"] == "Acme Labs: your daily Cofoundaz digest"
        _write(
            "digest_email",
            {k: msg[k] for k in ("subject", "to") if k in msg}
            | {"body": msg.get("html") or msg.get("body")},
        )
