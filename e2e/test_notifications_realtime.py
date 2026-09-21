"""Live Module 20 Slice 4: real-time SSE delivery over the Redis backplane.

A founder opens the SSE stream against the e2e server -- a REAL uvicorn process,
separate from this pytest run (see `scripts/e2e_run.sh`: the server is launched as its
own background process, pytest runs in its own). This test process then creates a
notification directly and commits it: `create_notifications`'s `after_commit` listener
(`app/db/session.py`) publishes the payload to Redis
(`app/platform/realtime.py::publish_notification`), and the SERVER process's
`GET /notifications/stream` generator -- already subscribed on that channel -- forwards
it down the open connection back to this test's client.

This is the one path in this slice that a fake-pubsub unit test cannot prove (Tasks 1-3's
unit suites already cover the seam, the publish-on-commit contract, and the endpoint's
SSE framing in isolation, each with an in-process fake). Only a live run across two real
OS processes proves Redis is actually the backplane between them, not just a shared
Python object under test.

Every response/frame along the way is captured to
`e2e/_captures/notifications_realtime/*.json` -- the verbatim source for
`docs/fe-integration-guide-notifications-realtime.md`.
"""

import json
import threading
import time
import uuid
from pathlib import Path

import httpx

# NOTE: app-internal imports (SessionLocal, create_notifications) are done LAZILY inside
# the test, not at module level -- same reasoning as e2e/test_notifications_email.py's
# module header: importing them here would instantiate `app.core.config.Settings` at
# pytest COLLECTION time, which the E2E_REMOTE=1 gate's environment cannot satisfy before
# conftest gets a chance to deselect this module (it needs `make_verified_user`, which
# depends on `mailbox`, so it IS remote-deselected -- but only if collection succeeds).

_CAPTURE_DIR = Path(__file__).parent / "_captures" / "notifications_realtime"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    """Walks onboarding steps 1-4 + complete (mirrors e2e/test_assessment.py's flow),
    then returns workspace-scoped headers built from a real `GET /auth/me` response."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz Realtime"}
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )
    onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
    assert onboarded.status_code == 200, onboarded.text

    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    startup_id = me["active_workspace_id"]
    return {**auth, "X-Workspace-Id": startup_id}


def _write_capture(name: str, data: dict) -> None:
    """Write a raw (non-httpx.Response) captured payload verbatim, like `capture` does for responses."""
    _CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    (_CAPTURE_DIR / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")


def test_realtime_notification_delivery(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)

        # /auth/me's `id` key lives under `user`, not at the top level --
        # {"user": {"id": ..., "email": ..., "status": ...}, "profile": ...,
        # "memberships": [...], "active_workspace_id": ...}. Confirmed against
        # app/api/v1/endpoints/auth/me.py rather than assumed.
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        user_id = me["user"]["id"]
        startup_id = me["active_workspace_id"]

        ticket_resp = c.post("/api/v1/notifications/stream-ticket", headers=wh)
        assert ticket_resp.status_code == 200, ticket_resp.text
        ticket = ticket_resp.json()["data"]["ticket"]
        # A SECOND, separate ticket purely for the capture -- the first one above gets
        # consumed (one-time GETDEL, app/platform/realtime.py::consume_stream_ticket)
        # the moment the stream below opens, so capturing THAT same response would no
        # longer read back as a currently-valid ticket for a reader of the FE guide.
        capture(
            "notifications_realtime",
            "stream_ticket",
            c.post("/api/v1/notifications/stream-ticket", headers=wh),
        )

        got: dict = {"frame": None, "error": None}

        def read_stream() -> None:
            try:
                with httpx.Client(base_url=base_url, timeout=15.0) as s:
                    with s.stream("GET", f"/api/v1/notifications/stream?ticket={ticket}") as resp:
                        if resp.status_code != 200:
                            body = resp.read()
                            raise AssertionError(
                                f"GET /notifications/stream -> {resp.status_code}: {body!r}"
                            )
                        event = None
                        for line in resp.iter_lines():
                            if line.startswith("event:"):
                                event = line.split(":", 1)[1].strip()
                            elif line.startswith("data:") and event == "notification.created":
                                got["frame"] = json.loads(line.split(":", 1)[1].strip())
                                return
            except Exception as exc:  # noqa: BLE001 - re-raised on the main thread below
                got["error"] = exc

        t = threading.Thread(target=read_stream, daemon=True)
        t.start()
        time.sleep(1.0)  # let the stream subscribe (past the initial `unread` frame)

        # Create a notification IN THIS (test) PROCESS and commit -- the `after_commit`
        # listener (app/db/session.py) publishes to Redis; the SERVER process's
        # subscribed stream (opened above, in its own thread) forwards it down the open
        # connection. This is the cross-process assertion the whole task exists to make.
        from app.db.session import SessionLocal
        from app.services.notifications.service import create_notifications

        db = SessionLocal()
        try:
            create_notifications(
                db,
                user_ids=[uuid.UUID(user_id)],
                startup_id=uuid.UUID(startup_id),
                type="x.realtime.test",
                title="Live!",
                body="",
                data={"hello": "world"},
            )
            db.commit()
        finally:
            db.close()

        # Hard timeout: a stalled stream must fail the test loudly, never hang the suite.
        t.join(timeout=8.0)
        assert not t.is_alive(), "SSE read thread did not finish -- stream stalled"
        if got["error"] is not None:
            raise got["error"]
        assert got["frame"] is not None, "no SSE notification.created frame received"
        assert got["frame"]["type"] == "x.realtime.test"
        assert got["frame"]["data"] == {"hello": "world"}

        _write_capture("stream_frame", got["frame"])
