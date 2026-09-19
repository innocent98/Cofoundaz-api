# Module 20 — Notifications, Slice 4: real-time in-app delivery (SSE) — design

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 20 (Notifications), Slice 4 of 4
**Depends on:** Slices 1–3 (in-app feed, email+prefs+worker, scheduler — all shipped), Redis (in stack)

## Goal

Deliver notifications to an **open** app in real time, so the notification bell/feed updates
instantly with **no polling** — via **Server-Sent Events (SSE)** over a **Redis pub/sub backplane**.
When a notification row is created (from any source), the recipient's live SSE connection receives the
full notification payload immediately.

One sentence: *when a notification commits, publish it to the recipient's Redis channel; an SSE
endpoint the browser holds open streams it straight to the bell.*

## Why

- Slices 1–3 made notifications real (feed, email, scheduled), but the FE can only learn of a new
  in-app notification by **polling** `GET /notifications` / `/unread-count`. That is laggy and wasteful.
- Prod runs **4 gunicorn/UvicornWorker processes + a separate worker container**, so the process that
  *creates* a notification is almost never the one *holding* the recipient's connection. A
  cross-process **pub/sub backplane is mandatory** — Redis (already in the stack) is the natural fit.

## Scope

### In scope (Slice 4)
1. **Realtime seam** (`app/platform/realtime.py`): channel naming, a fail-soft sync `publish`, an async
   `redis.asyncio` subscribe helper, and one-time SSE ticket mint/consume.
2. **Publish on commit**: a SQLAlchemy `after_commit` listener publishes the notifications created in
   that transaction (payloads stashed by `create_notifications`) — so events fire only for
   *committed* rows, across every caller (request path, worker, scheduler).
3. **`POST /notifications/stream-ticket`** — mints a short-lived one-time ticket (Bearer-authed).
4. **`GET /notifications/stream?ticket=…`** — the SSE endpoint: validates+consumes the ticket,
   re-checks membership, streams an initial unread count, then live `notification.created` frames plus
   heartbeats.
5. **Config**, tests (unit + one live e2e), FE integration guide, SOP, checklist.

### Out of scope (deferred)
- **Device/web push** (VAPID / FCM / APNs, app-closed delivery) — its own later slice (per the scope
  decision).
- **Server-side replay / `Last-Event-ID`** — v1 reconciles missed events via the FE re-fetching the
  feed on (re)connect; no per-user event log.
- **Read-state fan-out** (pushing "marked read" across a user's own tabs), presence, typing — YAGNI.
- **A shared per-node Redis subscriber / connection caps** — a scaling optimization; v1 opens one
  pubsub per connection (fine at current scale).
- **WebSocket** — rejected; delivery is one-way server→client, and EventSource gives auto-reconnect
  for free.

## Architecture

```
create_notifications(...)  (flush; stash (channel,payload) per recipient in db.info)
        … caller commits the transaction …
  SQLAlchemy after_commit(session)  → for each stashed item: realtime.publish_notification(...)  (fail-soft)
        → Redis PUBLISH  notif:{startup_id}:{user_id}   {"event":"notification.created","notification":{…}}
                     … Redis pub/sub (spans all 4 web workers + the worker container) …
GET /notifications/stream?ticket=…   (one async SSE connection per client)
  → consume ticket → verify membership → StreamingResponse(text/event-stream):
       frame 1:  event: unread \n data: {"unread": N}
       then:     event: notification.created \n data: {<serialize_notification>}   (as they arrive)
       every ~20s: ": heartbeat\n\n"
  → EventSource.onmessage / addEventListener("notification.created", …) updates the bell
  → on (re)connect the FE re-fetches GET /notifications to reconcile anything missed
```

### 1. Realtime seam — `app/platform/realtime.py` (new)

Mirrors the email/llm seams (thin, fakeable). Uses the existing **sync** Redis client
(`app/core/redis.py::get_redis`) for publish + tickets, and **`redis.asyncio`** for the stream
subscribe (redis ^5.0.0 ships `redis.asyncio` — no new dependency).

- `channel_for(startup_id, user_id) -> str` → `f"notif:{startup_id}:{user_id}"`.
- `publish_notification(startup_id, user_id, payload: dict) -> None` — `get_redis().publish(channel, json.dumps(payload))`, **wrapped in try/except + log.warning** so a Redis failure never propagates (it runs in `after_commit`, which cannot roll back anyway; the DB row + email job are the durable path).
- `mint_stream_ticket(user_id, startup_id) -> str` — `tok = secrets.token_urlsafe(32)`; `get_redis().set(f"sse_ticket:{tok}", f"{user_id}:{startup_id}", ex=settings.SSE_TICKET_TTL, nx=True)`; returns `tok`.
- `consume_stream_ticket(tok) -> tuple[user_id, startup_id] | None` — one-time via `get_redis().getdel(f"sse_ticket:{tok}")` (Redis ≥6.2; the stack's Redis 7 supports it); returns the parsed pair or `None` if absent/expired/already used.
- `async def subscribe(channel) -> AsyncIterator[dict]` — opens a `redis.asyncio` pubsub, subscribes, and yields decoded messages; closes the pubsub on exit (caller uses it in a `try/finally`).

### 2. Publish on commit — `app/services/notifications/service.py`

- `create_notifications(...)` already flushes the rows (ids + `created_at` available). After the flush,
  for each created row append `(channel_for(startup_id, row.user_id), {"event": "notification.created", "notification": serialize_notification(row)})`
  to a per-session stash: `db.info.setdefault("pending_realtime", []).extend(...)`.
- Register **once** (module import) two SQLAlchemy listeners on `SessionLocal` (the sessionmaker in
  `app/db/session.py`):
  - `after_commit`: pop `session.info.get("pending_realtime")`; for each `(channel, payload)` call
    `realtime.publish_notification(...)` (already fail-soft); clear the stash.
  - `after_rollback` (and `after_soft_rollback`): clear the stash without publishing — no phantom
    events for rolled-back rows.
- This is the whole publish path — it automatically covers the request handler, the job worker, and
  the scheduler, since all notification creation flows through `create_notifications`.

### 3. `POST /notifications/stream-ticket`

- Deps: `require_workspace` (→ `Membership`) + `get_verified_user` (same as the other notification
  endpoints). Returns `success_response({"ticket": mint_stream_ticket(membership.user_id, membership.startup_id)})`.
- Rationale: browsers can't set `Authorization` on native `EventSource`; the ticket keeps the
  long-lived access token out of the URL — only a single-use, ~30s ticket ever appears there.

### 4. `GET /notifications/stream`

- Query param `ticket`. `consume_stream_ticket(ticket)` → if `None`, raise 401 (`AppError`). Re-derive
  `(user_id, startup_id)`; verify an **active** `Membership` still exists for the pair (defense in
  depth — membership may have been revoked between mint and connect); else 401/403.
- Return `StreamingResponse(_event_stream(request, startup_id, user_id, db), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})`.
  `X-Accel-Buffering: no` disables nginx response buffering for this route with **no nginx config
  change**.
- `_event_stream` (async generator):
  1. yield the initial authoritative count: `event: unread\ndata: {"unread": <unread_count(...)>}\n\n`.
  2. `async with realtime.subscribe(channel_for(startup_id, user_id))`: loop —
     `msg = await pubsub.get_message(timeout=settings.SSE_HEARTBEAT_INTERVAL)`; if a message, yield
     `event: notification.created\ndata: {payload["notification"] as json}\n\n`; if none (timeout),
     yield `": heartbeat\n\n"`. Break when `await request.is_disconnected()`.
  3. `finally`: the subscribe helper closes the pubsub.
- The initial unread read uses a short-lived DB session; the long-lived stream does **not** hold a DB
  transaction open (it only talks to Redis after the initial count).

### 5. Config — `app/core/config.py`

| Setting | Default | Purpose |
|---|---|---|
| `SSE_TICKET_TTL` | `30` | Seconds a stream ticket is valid (one-time). |
| `SSE_HEARTBEAT_INTERVAL` | `20` | Seconds between heartbeat comments (keeps nginx/proxies from idling the connection out). |

### Data model

**No migration, no schema change.** Tickets live in Redis; notification rows are unchanged; the SSE
payload reuses `serialize_notification`.

## Error handling & semantics

- **Publish is best-effort / fail-soft** — a Redis error in `after_commit` is logged and swallowed;
  notification creation, email enqueue, and the feed are unaffected. SSE is a live optimization; the
  feed remains the source of truth.
- **Reconciliation, not replay** — no server-side event log. Missed-while-disconnected notifications
  are caught by the FE re-fetching `GET /notifications` on (re)connect. EventSource auto-reconnects;
  the FE mints a fresh ticket per reconnect.
- **Ticket** invalid/expired/already-used → 401. **Membership** revoked between mint and connect →
  401/403. **Redis unavailable** for the stream subscribe → 503 (the endpoint fails cleanly; the FE
  falls back to polling).
- **No phantom events** — the `after_rollback` listener clears the stash, so a rolled-back transaction
  publishes nothing.
- **Cross-tenant isolation** — the channel is per-`(startup_id, user_id)`; a connection only ever
  receives its own user's notifications for the workspace the ticket was minted in.

## Testing

- **Unit (seam):** `channel_for` format; `publish_notification` publishes the right channel+payload
  (fake Redis) and **swallows** a raised Redis error; `mint`/`consume` ticket is one-time (second
  consume → `None`) and TTL'd; `subscribe` yields decoded messages (fake async pubsub).
- **Unit (publish-on-commit):** creating notifications then **committing** calls
  `publish_notification` once per recipient with `serialize_notification` payload (patch the seam);
  a **rollback** publishes nothing (no phantom); a Redis failure doesn't break the commit.
- **Unit (stream endpoint):** invalid/used ticket → 401; membership re-check; the generator emits the
  initial `unread` frame then a `notification.created` frame for a fed message and a heartbeat on
  timeout (drive with a fake pubsub); frames are well-formed SSE.
- **E2E (harness has Redis):** login → `POST /stream-ticket` → open `GET /stream?ticket=…` with an
  httpx **streaming** request → trigger a real notification in-process (e.g. via `create_notifications`
  + commit, or a document share) → assert a `notification.created` frame arrives carrying the expected
  `type`, then the feed reconciles. Capture the ticket response + a sample frame. Bounded to one
  journey; use a read timeout so a stall fails loudly.
- Coverage stays ≥ 95%.

## Security & privacy

- The stream ticket is a random, single-use, ~30s server-side token (Redis); the access token never
  appears in a URL. Membership is re-verified at connect.
- Payloads carry only the recipient's own notification (already scoped per-user per-workspace). No
  secrets/PII beyond what the feed already returns.
- No new inbound surface beyond the two authenticated routes; no change to RBAC/tenancy helpers.

## FE impact (integration guide)

- **Connect:** `POST /api/v1/notifications/stream-ticket` (Bearer + `X-Workspace-Id`) → `{ticket}`;
  then `new EventSource("/api/v1/notifications/stream?ticket=" + ticket)`.
- **Events:** `event: unread` (`{"unread": N}`, authoritative, sent first) and
  `event: notification.created` (a full feed-item object). Use `addEventListener` per event name.
- **Reconnect:** EventSource auto-reconnects; on each (re)open the FE must **mint a new ticket** (the
  old one is single-use/expired) and **re-fetch `GET /notifications`** to reconcile anything missed
  while disconnected — the SSE stream is live-only, the feed is the source of truth.
- The pushed `notification.created` object is byte-identical to a `GET /notifications` item (same
  `serialize_notification`), so the FE renders both with one code path.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit or PR/issue body.
- **Reproduce every CI check locally and make it green before pushing** (black/isort/ruff/mypy/pylint
  ≥ 9.5/bandit/pytest ≥ 95% cov/alembic single head **unchanged — no migration**/e2e), via `poetry run`.
- **Ship the SOP**, reconcile the **checklist**, and write the **FE integration guide** with payloads
  copied verbatim from live e2e captures.
- Response envelope, `AppError`, `require_workspace`/`get_verified_user`, Redis client conventions
  unchanged.

## Follow-ups (post-Slice-4)

- Device/web push (VAPID/FCM/APNs) for app-closed delivery — the other half of "push".
- `Last-Event-ID` replay / a short per-user event buffer, if reconnection gaps become a real UX issue.
- A shared per-process Redis subscriber (one pubsub multiplexed to many connections) if concurrent
  connection counts grow.
- Push read-state changes across a user's own open tabs.
- nginx: confirm a generous `proxy_read_timeout` for `/notifications/stream` (heartbeats keep it alive;
  `X-Accel-Buffering: no` already handles buffering) — a devops note, not app code.
```
