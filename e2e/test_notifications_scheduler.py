"""Live Module 20 Slice 3: scheduler fires time-based notifications.

Drives the scheduler + worker in-process (no separate container in the harness), the
same pattern as `e2e/test_notifications_email.py`. A founder onboards to stage
"validation" (which synchronously generates the roadmap -- precondition for mission
generation, `app.services.mission.service.get_or_generate_today`), we run
`scheduler_tick` at a controlled `now` past `MISSION_GEN_HOUR` (06:00 workspace-tz,
default `SCHEDULER_TIMEZONE=UTC`), drain the worker queue in-process, and assert a
`mission.ready` notification lands in the founder's own feed.

Only `mission.ready` is naturally reachable from a fresh e2e journey: a just-onboarded
founder has a roadmap and no mission generated yet today. The other two scheduled
events are NOT exercised live here, and are not faked either:
- `roadmap.milestone.overdue` needs a roadmap milestone with a past `due_on` that is
  not `done` -- no e2e journey in this suite backdates a milestone.
- `assessment.quarterly.due` needs a COMPLETED assessment whose `completed_at` is
  more than `QUARTERLY_REASSESS_DAYS` (default 90) in the past -- unreachable from a
  fresh signup without manipulating the clock or the DB directly, which would not be
  a live journey at that point.
Both are covered by handler-level unit tests (`tests/worker/test_scheduled_handlers.py`)
and a registry-level unit test (`tests/services/notifications/test_scheduled_events.py`)
that assert the recipient/category wiring instead -- see the FE guide's §5/§8 and the
SOP's Verification section for the exact (unit-sourced, explicitly labelled) `data`
shapes.

NOTE (mirrors the header note in `e2e/test_notifications_email.py`): keep ALL
app-internal imports (`app.db.session`, `app.worker.*`, `app.services.notifications.
registry`) LAZY, inside `_tick_and_drain`, never at module scope. Importing them at
collection time instantiates `app.core.config.Settings`, which needs the full app env
-- the remote live-e2e gate (E2E_REMOTE=1) has no such env and would fail to COLLECT
this module before conftest could deselect it (it needs the local `mailbox`-adjacent
`make_verified_user` fixture, so it is remote-deselected anyway).
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


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


def _wh(c: httpx.Client, auth: dict) -> dict:
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _tick_and_drain(now):
    """Run one scheduler tick at `now`, then drain the worker queue in-process.

    Two separate sessions, same shape as the real worker loop (`app/worker/__main__.py`
    ::main_loop -- `run_once` and `scheduler_tick` each get their own iteration/session,
    never share one). Registering the notifications registry here (not just importing
    the scheduled-job handlers) is the make-or-break step: the app only subscribes it to
    `event_bus` at import of `app/api/v1/api.py` (`register_notifications()`), which runs
    in the SERVER process -- this test's tick + drain run in the TEST process, where
    `handle_mission_generate` publishes `mission.ready` in-process. Without registering
    here too, no notification row is ever created and the feed assertion below would
    silently fail (0 rows, not an error).
    """
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import scheduled  # noqa: F401  (registers scheduled job handlers)
    from app.worker.scheduler import scheduler_tick

    register_notifications()  # idempotent for the default bus -- see module header note

    db = SessionLocal()
    try:
        scheduler_tick(db, now=now)
    finally:
        db.close()

    db2 = SessionLocal()
    try:
        # Generous cap (500 batches) -- the same reasoning as `_drain` in
        # test_notifications_email.py: this e2e run shares one `jobs` table across the
        # WHOLE suite, and a scheduler tick this late in the run enqueues a
        # `scheduled.mission.generate` job for EVERY active startup created by every
        # earlier e2e journey too (mission generation is lazy, not just for us), not
        # only our own -- a single WORKER_BATCH_SIZE batch would starve on someone
        # else's jobs before ever reaching ours.
        for _ in range(500):
            if runner.run_once(db2) == 0:
                break
    finally:
        db2.close()


def test_scheduled_mission_ready_notification(base_url, make_verified_user, unique_email, capture):
    from datetime import UTC, datetime

    with httpx.Client(base_url=base_url, timeout=10.0) as a:
        u_a = make_verified_user(a)
        access_a = a.post("/api/v1/auth/login", json=u_a).json()["data"]["access_token"]
        auth_a = _auth(access_a)

        _onboard_steps(a, auth_a, stage="validation", name="Cofoundaz Scheduler")

        # Founder completes onboarding -- synchronously generates the roadmap
        # (precondition for mission generation: `get_or_generate_today` returns None
        # without one).
        done = a.post("/api/v1/onboarding/complete", headers=auth_a)
        assert done.status_code == 200, done.text

        wh_a = _wh(a, auth_a)

        tree = a.get("/api/v1/roadmap", headers=wh_a)
        assert tree.status_code == 200, tree.text
        assert tree.json()["data"]["phases"], "expected a generated roadmap before the tick"

        # Sanity: a fresh founder's feed has no mission.ready row yet -- nothing else
        # in this journey has generated today's mission.
        before = a.get("/api/v1/notifications", headers=wh_a)
        assert before.status_code == 200, before.text
        before_types = [n["type"] for n in before.json()["data"]["notifications"]]
        assert "mission.ready" not in before_types

        # Run the scheduler at 07:00 UTC today -- past MISSION_GEN_HOUR=06:00 in the
        # default SCHEDULER_TIMEZONE=UTC -- then drain the worker queue.
        _tick_and_drain(datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0))

        feed = a.get("/api/v1/notifications", headers=wh_a)
        assert feed.status_code == 200, feed.text
        types = [n["type"] for n in feed.json()["data"]["notifications"]]
        assert "mission.ready" in types
        capture("notifications_scheduler", "mission_ready_feed", feed)

        mission_notif = next(
            n for n in feed.json()["data"]["notifications"] if n["type"] == "mission.ready"
        )
        assert set(mission_notif["data"]) == {"startup_id", "mission_id"}
        assert mission_notif["data"]["startup_id"] == wh_a["X-Workspace-Id"]

        # The scheduled_runs claim ledger makes this once-per-day: a second tick later
        # the same day must not re-fire mission.ready for the same startup.
        _tick_and_drain(datetime.now(UTC).replace(hour=7, minute=30, second=0, microsecond=0))
        feed_after_retick = a.get("/api/v1/notifications", headers=wh_a)
        assert feed_after_retick.status_code == 200, feed_after_retick.text
        retick_types = [n["type"] for n in feed_after_retick.json()["data"]["notifications"]]
        assert retick_types.count("mission.ready") == 1
