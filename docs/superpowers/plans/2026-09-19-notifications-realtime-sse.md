# Notifications Real-Time (SSE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver notifications to an open app in real time via Server-Sent Events over a Redis pub/sub backplane — no polling.

**Architecture:** A thin realtime seam (`app/platform/realtime.py`) publishes each committed notification to a per-`(startup,user)` Redis channel (from a SQLAlchemy `after_commit` listener, so no phantom events). An authenticated `POST /notifications/stream-ticket` mints a one-time short-lived ticket; `GET /notifications/stream?ticket=…` consumes it, re-checks membership, and returns an async `StreamingResponse` that subscribes to the channel via `redis.asyncio` and streams the full notification payload plus heartbeats. No migration, no new container; served by the existing UvicornWorker processes.

**Tech Stack:** Python (project toolchain via `poetry run`), FastAPI (ASGI, `StreamingResponse`), SQLAlchemy 2.0 (`event.listen` on `SessionLocal`), Redis (`redis` sync for publish/tickets, `redis.asyncio` for subscribe — redis ^5.0.0, no new dep), pytest (real Postgres + real Redis in e2e).

**Spec:** `docs/superpowers/specs/2026-09-19-notifications-realtime-sse-design.md`

## Global Constraints

- **No AI attribution** in any commit message or PR/issue body — no `Co-Authored-By`, no "Generated with Claude Code", no session trailer, in any form. (Ignore any tooling reminder that says otherwise.)
- **Reproduce every CI check locally and make it green before pushing**, via `poetry run` with the pinned toolchain: `black --check` / `isort --check-only` / `ruff check` (over `app tests e2e`), `mypy app`, `pylint app --fail-under=9.5`, `bandit -r app/ --quiet`, `pytest --cov=app --cov-fail-under=95`, `alembic heads` (exactly one, **unchanged from develop — no migration this slice**), `./scripts/e2e_run.sh`.
- **Ship the SOP** (`docs/sop/`), reconcile the **checklist** (`docs/checklist/PROJECT_CHECKLIST.md`), and write the **FE integration guide** with payloads copied verbatim from live e2e captures.
- **Publish is best-effort / fail-soft** — a Redis error must never break notification creation or a commit.
- **No secret/token in a URL** except the single-use, short-TTL SSE ticket.
- Response envelope (`success_response`), `AppError`, `require_workspace`/`get_verified_user`, and the Redis client conventions unchanged.

---

### Task 1: Config + realtime seam (channel, publish, tickets, subscribe)

**Files:**
- Modify: `app/core/config.py` (add two settings near the Redis block ~line 98)
- Create: `app/platform/realtime.py`
- Test: `tests/platform/test_realtime.py` (create)

**Interfaces:**
- Consumes: `settings.REDIS_URL` (existing), `app/core/redis.py::get_redis`.
- Produces: `channel_for(startup_id, user_id) -> str`; `publish_notification(startup_id, user_id, payload: dict) -> None` (sync, fail-soft); `mint_stream_ticket(user_id, startup_id) -> str`; `consume_stream_ticket(tok: str) -> tuple[str, str] | None`; `subscription(channel: str)` (async context manager yielding a `redis.asyncio` pubsub); `settings.SSE_TICKET_TTL`, `settings.SSE_HEARTBEAT_INTERVAL`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/platform/test_realtime.py
import app.platform.realtime as rt
from app.core.config import settings


def test_channel_for_format():
    assert rt.channel_for("s1", "u1") == "notif:s1:u1"


def test_publish_uses_channel_and_json(monkeypatch):
    calls = []

    class FakeRedis:
        def publish(self, ch, msg):
            calls.append((ch, msg))

    monkeypatch.setattr(rt, "get_redis", lambda: FakeRedis())
    rt.publish_notification("s1", "u1", {"event": "notification.created", "notification": {"id": "n1"}})
    assert calls[0][0] == "notif:s1:u1"
    assert '"id": "n1"' in calls[0][1]


def test_publish_is_fail_soft(monkeypatch):
    class BoomRedis:
        def publish(self, ch, msg):
            raise RuntimeError("redis down")

    monkeypatch.setattr(rt, "get_redis", lambda: BoomRedis())
    # must NOT raise
    rt.publish_notification("s1", "u1", {"x": 1})


def test_ticket_mint_and_consume_is_one_time(monkeypatch):
    store: dict[str, str] = {}

    class FakeRedis:
        def set(self, k, v, ex=None, nx=None):
            store[k] = v

        def getdel(self, k):
            return store.pop(k, None)

    monkeypatch.setattr(rt, "get_redis", lambda: FakeRedis())
    tok = rt.mint_stream_ticket("u1", "s1")
    assert rt.consume_stream_ticket(tok) == ("u1", "s1")
    assert rt.consume_stream_ticket(tok) is None  # one-time


def test_consume_unknown_ticket_returns_none(monkeypatch):
    monkeypatch.setattr(rt, "get_redis", lambda: type("R", (), {"getdel": lambda self, k: None})())
    assert rt.consume_stream_ticket("nope") is None


def test_settings_defaults():
    assert settings.SSE_TICKET_TTL == 30
    assert settings.SSE_HEARTBEAT_INTERVAL == 20
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/platform/test_realtime.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: app.platform.realtime` / missing settings.

- [ ] **Step 3: Add the config settings**

In `app/core/config.py`, near the Redis block:

```python
    # --- Realtime (SSE) delivery (Module 20 Slice 4) -----------------------
    SSE_TICKET_TTL: int = 30        # seconds a one-time stream ticket is valid
    SSE_HEARTBEAT_INTERVAL: int = 20  # seconds between SSE heartbeat comments
```

- [ ] **Step 4: Implement the seam**

```python
# app/platform/realtime.py
import json
import secrets
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.logger import log
from app.core.redis import get_redis


def channel_for(startup_id: str, user_id: str) -> str:
    return f"notif:{startup_id}:{user_id}"


def publish_notification(startup_id: str, user_id: str, payload: dict) -> None:
    """Best-effort live publish. Runs in an after_commit listener, so it must never raise:
    the DB row + email job are the durable path; SSE is a live optimization."""
    try:
        get_redis().publish(channel_for(startup_id, user_id), json.dumps(payload))
    except Exception as exc:  # noqa: BLE001 - live delivery is best-effort
        log.warning(f"[realtime] publish failed (startup={startup_id} user={user_id}): {exc}")


def mint_stream_ticket(user_id: str, startup_id: str) -> str:
    tok = secrets.token_urlsafe(32)
    get_redis().set(f"sse_ticket:{tok}", f"{user_id}:{startup_id}", ex=settings.SSE_TICKET_TTL, nx=True)
    return tok


def consume_stream_ticket(tok: str) -> tuple[str, str] | None:
    """One-time consume via GETDEL (Redis >= 6.2). Returns (user_id, startup_id) or None."""
    raw = get_redis().getdel(f"sse_ticket:{tok}")
    if not raw or ":" not in raw:
        return None
    user_id, _, startup_id = raw.partition(":")
    return user_id, startup_id


@asynccontextmanager
async def subscription(channel: str):
    """Async context manager yielding a subscribed redis.asyncio pubsub; cleans up on exit."""
    import redis.asyncio as aioredis  # lazy: keep asyncio client out of module import

    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        yield pubsub
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()
```

- [ ] **Step 5: Run to see them pass**

Run: `poetry run pytest tests/platform/test_realtime.py -v --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py app/platform/realtime.py tests/platform/test_realtime.py
git commit -m "feat(notifications): realtime seam — channel, fail-soft publish, SSE tickets, async subscribe"
```

---

### Task 2: Publish committed notifications (after_commit listener)

**Files:**
- Modify: `app/services/notifications/service.py` (`create_notifications` — stash payloads after flush)
- Modify: `app/db/session.py` (register `after_commit` / `after_rollback` listeners on `SessionLocal`)
- Test: `tests/services/notifications/test_realtime_publish.py` (create)

**Interfaces:**
- Consumes: `channel_for`, `publish_notification` (Task 1); `serialize_notification` (`app/services/notifications/service.py`).
- Produces: notifications created in a transaction are published (once each) after that transaction commits; nothing is published on rollback.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/notifications/test_realtime_publish.py
import uuid

import app.platform.realtime as rt
from app.db.session import SessionLocal
from app.services.notifications.service import create_notifications


def _make_pair(db):
    from tests.factories import create_membership, create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    return u, s


def test_publish_on_commit_one_per_recipient(monkeypatch):
    published = []
    monkeypatch.setattr(rt, "publish_notification", lambda sid, uid, payload: published.append((sid, uid, payload)))
    db = SessionLocal()
    try:
        u, s = _make_pair(db)
        db.commit()  # commit the fixtures first so the next commit only carries the notification
        published.clear()
        create_notifications(
            db, user_ids=[u.id], startup_id=s.id, type="x.test", title="T", body="", data={"k": "v"}
        )
        assert published == []  # nothing published before commit
        db.commit()
        assert len(published) == 1
        sid, uid, payload = published[0]
        assert str(sid) == str(s.id) and str(uid) == str(u.id)
        assert payload["event"] == "notification.created"
        assert payload["notification"]["type"] == "x.test"
    finally:
        db.rollback()
        db.close()


def test_no_publish_on_rollback(monkeypatch):
    published = []
    monkeypatch.setattr(rt, "publish_notification", lambda *a, **k: published.append(a))
    db = SessionLocal()
    try:
        u, s = _make_pair(db)
        db.commit()
        published.clear()
        create_notifications(db, user_ids=[u.id], startup_id=s.id, type="x.test", title="T", body="", data={})
        db.rollback()
        assert published == []
    finally:
        db.close()
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/services/notifications/test_realtime_publish.py -v --no-cov`
Expected: FAIL — nothing is published (no stash/listener yet).

- [ ] **Step 3: Stash payloads in `create_notifications`**

In `app/services/notifications/service.py`, after the rows are added and flushed (so `id`/`created_at` exist), stash one realtime item per row. Add the import at top: `from app.platform.realtime import channel_for`. Then, at the end of `create_notifications`, before `return rows`:

```python
    pending = db.info.setdefault("pending_realtime", [])
    for n in rows:
        pending.append(
            (
                str(n.startup_id),
                str(n.user_id),
                {"event": "notification.created", "notification": serialize_notification(n)},
            )
        )
    return rows
```
(Ensure the rows are flushed before this — `create_notifications` already flushes; if not, add `db.flush()` before the loop so `serialize_notification` sees `id`/`created_at`.)

- [ ] **Step 4: Register the commit/rollback listeners**

In `app/db/session.py`, after `SessionLocal = sessionmaker(...)`:

```python
from sqlalchemy import event  # add to imports

from app.platform.realtime import publish_notification  # add to imports


@event.listens_for(SessionLocal, "after_commit")
def _publish_pending_realtime(session):  # noqa: ANN001
    pending = session.info.pop("pending_realtime", None)
    if not pending:
        return
    for startup_id, user_id, payload in pending:
        publish_notification(startup_id, user_id, payload)  # fail-soft internally


@event.listens_for(SessionLocal, "after_rollback")
def _drop_pending_realtime(session):  # noqa: ANN001
    session.info.pop("pending_realtime", None)
```
(If a circular import arises, import `publish_notification` lazily inside `_publish_pending_realtime`. `app.platform.realtime` only imports `config`/`logger`/`redis`, so a top-level import should be fine.)

- [ ] **Step 5: Run to see them pass + no worker/notification regression**

Run: `poetry run pytest tests/services/notifications/test_realtime_publish.py tests/services/notifications/ tests/worker/ -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/notifications/service.py app/db/session.py tests/services/notifications/test_realtime_publish.py
git commit -m "feat(notifications): publish committed notifications to the realtime backplane"
```

---

### Task 3: SSE endpoints (stream-ticket + stream)

**Files:**
- Modify: `app/api/v1/endpoints/notifications.py` (add two routes + the async generator + helpers)
- Test: `tests/api/notifications/test_stream.py` (create; mirror the dir of existing notification API tests)

**Interfaces:**
- Consumes: `mint_stream_ticket`, `consume_stream_ticket`, `channel_for`, `subscription` (Task 1); `unread_count`, `serialize_notification` (service); `require_workspace`, `get_verified_user`, `success_response`, `AppError`, `Membership`, `MembershipStatus`, `SessionLocal`, `settings`.
- Produces: `POST /notifications/stream-ticket` → `{"ticket": str}`; `GET /notifications/stream?ticket=…` → `text/event-stream`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/notifications/test_stream.py
import app.api.v1.endpoints.notifications as notif_ep
from app.core.config import settings


def test_stream_ticket_requires_auth(client):
    # no auth -> 401/403 (same guard as the other notification endpoints)
    r = client.post("/api/v1/notifications/stream-ticket")
    assert r.status_code in (401, 403)


def test_stream_rejects_bad_ticket(client, monkeypatch):
    monkeypatch.setattr(notif_ep, "consume_stream_ticket", lambda t: None)
    r = client.get("/api/v1/notifications/stream?ticket=nope")
    assert r.status_code == 401


import asyncio


def test_event_stream_emits_unread_then_notification(monkeypatch):
    # Drive the async generator directly with fakes: no real Redis/DB.
    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid: 3)

    class FakePubSub:
        def __init__(self):
            self._msgs = [
                {"type": "message", "data": '{"event":"notification.created","notification":{"id":"n1","type":"x.test"}}'},
                None,  # timeout -> heartbeat
            ]

        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            return self._msgs.pop(0) if self._msgs else None

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_sub(channel):
        yield FakePubSub()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    class FakeReq:
        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 2  # allow two loop iterations then disconnect

    async def collect():
        out = []
        async for frame in notif_ep._event_stream(FakeReq(), "s1", "u1"):
            out.append(frame)
        return out

    frames = asyncio.run(collect())
    joined = "".join(frames)
    assert 'event: unread' in joined and '"unread": 3' in joined
    assert 'event: notification.created' in joined and '"id": "n1"' in joined
    assert ': heartbeat' in joined
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/api/notifications/test_stream.py -v --no-cov`
Expected: FAIL — routes/helpers not defined.

- [ ] **Step 3: Implement the endpoints + generator**

Add to `app/api/v1/endpoints/notifications.py` (add imports at top):

```python
import json

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.enums import MembershipStatus
from app.db.session import SessionLocal
from app.services.notifications.service import unread_count
import app.platform.realtime as realtime
from app.platform.realtime import (
    channel_for,
    consume_stream_ticket,
    mint_stream_ticket,
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # disable nginx buffering for this response (no nginx config change)
    "Connection": "keep-alive",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _active_membership(user_id: str, startup_id: str) -> bool:
    db = SessionLocal()
    try:
        return (
            db.query(Membership)
            .filter(
                Membership.user_id == user_id,
                Membership.startup_id == startup_id,
                Membership.status == MembershipStatus.active,
            )
            .first()
            is not None
        )
    finally:
        db.close()


def _unread(user_id: str, startup_id: str) -> int:
    db = SessionLocal()
    try:
        return unread_count(db, user_id=user_id, startup_id=startup_id)
    finally:
        db.close()


async def _event_stream(request: Request, startup_id: str, user_id: str):
    n = await run_in_threadpool(_unread, user_id, startup_id)
    yield _sse("unread", {"unread": n})
    async with realtime.subscription(channel_for(startup_id, user_id)) as pubsub:
        while not await request.is_disconnected():
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=settings.SSE_HEARTBEAT_INTERVAL
            )
            if msg is None:
                yield ": heartbeat\n\n"
                continue
            payload = json.loads(msg["data"])
            yield _sse(payload["event"], payload["notification"])


@router.post("/notifications/stream-ticket")
def stream_ticket_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"ticket": mint_stream_ticket(str(membership.user_id), str(membership.startup_id))}
    )


@router.get("/notifications/stream")
async def stream_endpoint(request: Request, ticket: str) -> StreamingResponse:
    pair = consume_stream_ticket(ticket)
    if pair is None:
        raise AppError("INVALID_TICKET", "Invalid or expired stream ticket.", 401)
    user_id, startup_id = pair
    if not await run_in_threadpool(_active_membership, user_id, startup_id):
        raise AppError("FORBIDDEN", "No active membership for this workspace.", 403)
    return StreamingResponse(
        _event_stream(request, startup_id, user_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
```
> Implementer note: confirm `AppError`'s constructor signature against `app/core/errors.py` (arg order/keywords for code/message/status) and match existing call sites; confirm `MembershipStatus.active` is the correct enum member. If `create_notifications` didn't already flush before the Task-2 stash, ensure it does. Keep the two new routes below the existing ones in the file.

- [ ] **Step 4: Run to see them pass**

Run: `poetry run pytest tests/api/notifications/test_stream.py -v --no-cov`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/notifications.py tests/api/notifications/test_stream.py
git commit -m "feat(notifications): SSE stream endpoint + one-time stream ticket"
```

---

### Task 4: Live e2e + captures + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_notifications_realtime.py`
- Create: `docs/fe-integration-guide-notifications-realtime.md`
- Create: `docs/sop/2026-09-19-notifications-realtime-sse.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`, `docs/fe-integration-guide-notifications.md` (add a pointer to the realtime guide)

**Interfaces:** consumes everything above; proves the cross-process Redis backplane by publishing from the test process and receiving on the server's SSE stream.

- [ ] **Step 1: Write the e2e journey**

```python
# e2e/test_notifications_realtime.py
"""Live Module 20 Slice 4: SSE real-time delivery over the Redis backplane.

A founder opens the SSE stream (server process). The test process then creates a
notification and commits it — the after_commit listener publishes to Redis, and the
server's subscribed stream forwards it to the open connection. This exercises the
cross-process backplane end to end (test proc -> Redis -> server proc -> client).
"""
import json
import threading
import time

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c, auth):  # mirror e2e/test_assessment.py's onboarding to get an active workspace
    ...  # walk onboarding steps 1-4 + POST /onboarding/complete; return X-Workspace-Id from /auth/me


def test_realtime_notification_delivery(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)  # {**auth, "X-Workspace-Id": <startup_id>}
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        user_id = me["id"]
        startup_id = me["active_workspace_id"]

        ticket = c.post("/api/v1/notifications/stream-ticket", headers=wh).json()["data"]["ticket"]
        capture("notifications_realtime", "stream_ticket", c.post("/api/v1/notifications/stream-ticket", headers=wh))

        got = {"frame": None}

        def read_stream():
            with httpx.Client(base_url=base_url, timeout=15.0) as s:
                with s.stream("GET", f"/api/v1/notifications/stream?ticket={ticket}") as resp:
                    assert resp.status_code == 200
                    event = None
                    for line in resp.iter_lines():
                        if line.startswith("event:"):
                            event = line.split(":", 1)[1].strip()
                        elif line.startswith("data:") and event == "notification.created":
                            got["frame"] = json.loads(line.split(":", 1)[1].strip())
                            return

        t = threading.Thread(target=read_stream, daemon=True)
        t.start()
        time.sleep(1.0)  # let the stream subscribe (past the initial unread frame)

        # Create a notification IN THE TEST PROCESS and commit -> after_commit publishes to Redis.
        from app.db.session import SessionLocal
        from app.services.notifications.service import create_notifications

        db = SessionLocal()
        try:
            create_notifications(
                db, user_ids=[user_id], startup_id=startup_id,
                type="x.realtime.test", title="Live!", body="", data={"hello": "world"},
            )
            db.commit()
        finally:
            db.close()

        t.join(timeout=8.0)
        assert got["frame"] is not None, "no SSE notification.created frame received"
        assert got["frame"]["type"] == "x.realtime.test"
        capture("notifications_realtime", "stream_frame", type("R", (), {"status_code": 200, "json": lambda self=None: {"data": got["frame"]}, "text": json.dumps(got["frame"])})())
```
> Implementer note: fill `_onboard` by mirroring `e2e/test_assessment.py`/`e2e/test_notifications_email.py` (signup→login→onboard→`X-Workspace-Id` from `/auth/me`). Confirm `/auth/me` exposes the user id key (`id`) and `active_workspace_id`. The `capture` fixture takes an httpx.Response; for the raw SSE frame, write it with a small helper like `e2e/test_notifications_email.py::_write_capture` instead of faking a Response. Keep a hard read timeout so a stall fails loudly rather than hanging the suite.

- [ ] **Step 2: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: all pass incl. the new journey; captures under `e2e/_captures/notifications_realtime/`.

- [ ] **Step 3: Docs (after the run — captures byte-accurate)**

- `docs/fe-integration-guide-notifications-realtime.md`: the connect flow (`POST /notifications/stream-ticket` → `EventSource("/notifications/stream?ticket=…")`), the two event types (`unread` first + authoritative, then `notification.created` carrying a full feed item), the **reconnect rules** (mint a fresh ticket per reconnect; re-fetch `GET /notifications` to reconcile; SSE is live-only, the feed is source of truth), and that the `notification.created` object is identical to a `GET /notifications` item. Paste the captured ticket response + a sample frame verbatim. Add a verification table (verified-live via the new e2e). Cross-link from `docs/fe-integration-guide-notifications.md`.
- `docs/sop/2026-09-19-notifications-realtime-sse.md`: what shipped, why (kill polling; 4-worker → Redis backplane mandatory), how (after_commit publish → Redis → async SSE subscribe; ticket auth; `X-Accel-Buffering: no`; fail-soft publish; reconcile-not-replay), files/config touched (**no migration**), the `SSE_TICKET_TTL`/`SSE_HEARTBEAT_INTERVAL` knobs, verification (unit + e2e), ops notes (nginx `proxy_read_timeout` generous; no new container), and the deferred follow-ups (device/web push; Last-Event-ID replay; shared per-node subscriber; read-state fan-out). Match the existing SOP style.
- `docs/checklist/PROJECT_CHECKLIST.md`: mark Module 20 Slice 4 shipped (2026-09-19) → **Module 20 fully complete (all 4 slices)**; move it from open to complete in the snapshot/tally and adjust counts.

- [ ] **Step 4: Full local CI reproduction**

Run:
```
poetry run black --check app tests e2e && poetry run isort --check-only app tests e2e && poetry run ruff check app tests e2e
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads   # exactly one, UNCHANGED from develop (no migration)
./scripts/e2e_run.sh
```
Expected: all green; one head. After a green e2e, `git checkout -- e2e/_captures/` for everything EXCEPT the new `e2e/_captures/notifications_realtime/` so the commit stays scoped (precedent: prior slices).

- [ ] **Step 5: Commit**

```bash
git add e2e/ docs/
git commit -m "test(notifications): SSE real-time live e2e + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:**
- Realtime seam (channel/publish/tickets/subscribe) + config → Task 1. ✓
- Publish-on-commit via after_commit/after_rollback listeners, no phantom events → Task 2. ✓
- `POST /stream-ticket` (one-time ticket) + `GET /stream` (SSE, membership re-check, initial unread, live frames, heartbeat, `X-Accel-Buffering: no`) → Task 3. ✓
- Full payload = `serialize_notification` (identical to a feed item) → Tasks 2 (payload) + 3 (frame). ✓
- Fail-soft publish → Task 1 (impl + test) + Task 2 (commit unaffected). ✓
- No migration → Global Constraints + Task 4 Step 4. ✓
- Ticket auth, no token-in-URL, membership re-check → Task 1 + Task 3. ✓
- Reconciliation-not-replay, reconnect rules → Task 4 FE guide. ✓
- Testing (unit seam/publish/endpoint + live e2e) → Tasks 1–4. ✓

**Placeholder scan:** the e2e's `_onboard` is delegated to mirroring existing e2e files (an existing, working flow, per the writing-plans rule against repeating ~60 lines), with the novel stream-read + cross-process publish given in full. The `AppError`/`MembershipStatus`/`/auth/me` key confirmations are labelled implementer checks against real code, not unwritten logic.

**Type consistency:** `channel_for(startup_id, user_id)->str`, `publish_notification(startup_id, user_id, payload)`, `mint_stream_ticket(user_id, startup_id)->str`, `consume_stream_ticket(tok)->tuple|None`, `subscription(channel)` async CM, `_event_stream(request, startup_id, user_id)`, `_unread(user_id, startup_id)`, `_active_membership(user_id, startup_id)`, payload `{"event":"notification.created","notification":<serialize_notification>}`, settings `SSE_TICKET_TTL`/`SSE_HEARTBEAT_INTERVAL` — consistent across Tasks 1–4. ✓
