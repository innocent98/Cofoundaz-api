# SOP — Notifications, Real-Time SSE Delivery (Module 20, Slice 4)

**What shipped** — the fourth and final slice of Module 20: live, server-pushed delivery for
in-app notifications over **Server-Sent Events (SSE)**, backed by a **Redis pub/sub backplane**.
When any notification row commits — from a request handler, the email worker, or the scheduler,
all of which already funnel through `create_notifications` — a `SQLAlchemy` `after_commit`
listener publishes it to a per-`(startup_id, user_id)` Redis channel; a new
`GET /notifications/stream` endpoint, held open by the browser via `EventSource`, is subscribed on
that same channel and forwards the payload down the connection the instant it arrives. This
closes the last open item from Slices 1–3's FE guide ("there is still no real-time delivery — the
FE must poll") without adding any new infrastructure: no websocket, no new container, no
migration — Redis was already in the stack for Slices 2–3's job queue.

Commits (branch `feat/notifications-realtime-sse`, off `develop`):
`a3a7916` (design) → `15138ed` (implementation plan, `.superpowers/sdd/
2026-09-19-notifications-realtime-sse/`) → `c773d79` (Task 1 — realtime seam: `channel_for`,
fail-soft `publish_notification`, one-time SSE tickets, async `subscription`) → `cdf0024` (Task 2 —
publish-on-commit: `create_notifications` stashes payloads, `after_commit`/`after_rollback`
listeners on `SessionLocal`) → `db956f0` (Task 3 — `POST /notifications/stream-ticket` +
`GET /notifications/stream` endpoints) → `4643abc` (fix — annotate the two Task-2 listeners so
`mypy app` passes; a gap in Task 2's own review that Task 3's full-`app` mypy run caught) →
**this commit** (Task 4, final — live cross-process e2e + FE guide + SOP + checklist reconcile).

## Why

Slices 1–3 made notifications real (in-app feed, email, scheduler), but the FE's only way to learn
a new notification exists was **polling** `GET /notifications`/`/unread-count` (Slice 1's FE guide
§2 suggests a 30–60s cadence). Two things make that specifically worth fixing now rather than
later: production runs **4 gunicorn/UvicornWorker processes plus a separate `worker`
container**, so the process that *creates* a notification (a request handler in web worker #2,
say) is almost never the process holding the recipient's *own* open connection (web worker #4,
or nobody, if they're not connected at all) — a same-process in-memory pub/sub would silently miss
most notifications. A cross-process backplane is mandatory, and Redis — already required
infrastructure since Slice 2's job queue — is the natural fit, not a new dependency to justify.

## How

**Publish-on-commit, not publish-on-create.** `create_notifications`
(`app/services/notifications/service.py`) already `db.flush()`es before this slice (Slice 1); this
slice adds a stash step right after the flush — one
`(str(startup_id), str(user_id), {"event": "notification.created", "notification":
serialize_notification(n)})` tuple per created row, appended to `db.info["pending_realtime"]`.
Two new listeners registered once on `SessionLocal` (`app/db/session.py`, `sqlalchemy.event.
listens_for`) do the actual I/O: `after_commit` pops the stash and calls
`realtime.publish_notification(startup_id, user_id, payload)` once per item; `after_rollback` pops
(drops) the stash without publishing. **This is the same "notification exists iff the triggering
action committed" guarantee Slice 1 already gives the DB row** (design doc, this slice's own
scope) — a rolled-back request now also can't leak a phantom SSE event for a row that was never
actually written, and a reader never needs a "was this real" check on what arrives over the wire.
Because every notification-creating call path (request handlers, the email/scheduled-job workers)
already goes through `create_notifications`, this one stash+listener pair is the entire publish
surface — no call site elsewhere in the codebase had to change.

**Fail-soft publish — SSE is a live optimization, never a source of truth.**
`publish_notification` (`app/platform/realtime.py`) wraps its `get_redis().publish(...)` call in a
bare `try/except Exception`, logging a warning and swallowing any error. This runs inside
`after_commit`, where the transaction has already committed and cannot be rolled back on a Redis
failure anyway — the DB row (and, if applicable, the Slice-2 email job) is the durable path; a
Redis outage degrades SSE delivery to "the FE will see it on its next poll/reconnect fetch," not a
lost notification.

**One-time ticket auth, not a bearer token in the URL.** Native browser `EventSource` cannot set
an `Authorization` header, so a long-lived access token can't be used directly as SSE auth without
putting it in the URL (query strings end up in proxy/access logs, browser history, `Referer`
headers). Instead, `POST /notifications/stream-ticket` (normal Bearer + `X-Workspace-Id` auth, same
as every other notifications route) mints a random `secrets.token_urlsafe(32)` ticket, stored in
Redis as `sse_ticket:{tok} -> "{user_id}:{startup_id}"` with `ex=SSE_TICKET_TTL` (default 30s) and
`nx=True`. `GET /notifications/stream?ticket=...` consumes it via Redis `GETDEL` (one-time — a
replayed or guessed ticket value fails the second time even within the TTL window) and re-derives
`(user_id, startup_id)` from the stored value — the only credential ever visible in the URL/logs is
a single-use, half-minute-lived opaque token, never the actual session credential. The endpoint
then re-checks an **active** `Membership` still exists for that pair before opening the stream —
defense in depth against a membership revoked in the (short) gap between minting the ticket and
the client actually connecting.

**The stream itself: one authoritative snapshot, then live frames, then heartbeats.**
`_event_stream` (`app/api/v1/endpoints/notifications.py`) is an async generator:
1. A short-lived `SessionLocal()` (via `run_in_threadpool`, since the DB layer is sync) computes
   the current `unread_count` and yields it as `event: unread\ndata: {"unread": N}\n\n` — sent
   first and treated as authoritative, so the FE can render the bell badge correctly even for a
   fresh connection that missed everything before it opened.
2. `async with realtime.subscription(channel_for(startup_id, user_id))` opens a `redis.asyncio`
   pubsub (redis-py's async client, already shipped in the `redis` dependency — no new package) and
   loops: `pubsub.get_message(timeout=SSE_HEARTBEAT_INTERVAL)` — a real message is forwarded as
   `event: notification.created\ndata: {<serialize_notification>}\n\n`; a timeout (nothing arrived)
   yields `": heartbeat\n\n"` (an SSE comment line, ignored by `EventSource` but enough to keep
   idle proxies/load balancers from closing the connection). The loop's own exit condition is
   `request.is_disconnected()`, checked every iteration.
3. The long-lived generator never holds a request-scoped DB session open across the stream — only
   the two short DB calls above (`_unread`, `_active_membership`) open-and-close their own session;
   everything after that talks only to Redis.

`StreamingResponse` carries `X-Accel-Buffering: no` (disables nginx response buffering for this one
route, **no nginx config change required** — it's a response header, not a server directive),
`Cache-Control: no-cache`, `Connection: keep-alive`.

**Reconcile, don't replay.** There is no server-side event log and no `Last-Event-ID` support in
v1 (explicit design-doc non-goal). A client that was disconnected (page closed, network blip, ticket
expired mid-reconnect-storm) has no way to ask "what did I miss between timestamp X and now" over
SSE — the contract is that **`GET /notifications` is always the source of truth**, and the FE
re-fetches it on every (re)connect to reconcile. This is the same shape Slices 1–3 already
document (poll-based reconciliation), just now paired with a live push instead of a poll for the
common case.

## What's involved

**No migration, no schema change.** Tickets live entirely in Redis (ephemeral, TTL'd); notification
rows are unchanged; the SSE payload reuses Slice 1's existing `serialize_notification` verbatim —
`GET /notifications` and the SSE stream render the same object shape, one FE code path for both.

**Realtime seam** (`app/platform/realtime.py`, new)
- `channel_for(startup_id, user_id) -> str` — `f"notif:{startup_id}:{user_id}"`.
- `publish_notification(startup_id, user_id, payload)` — sync, fail-soft `PUBLISH` via the
  existing sync Redis client (`app/core/redis.py::get_redis`).
- `mint_stream_ticket(user_id, startup_id) -> str` / `consume_stream_ticket(tok) -> tuple[str, str]
  | None` — one-time ticket mint (`SET ... NX EX`) / consume (`GETDEL`).
- `subscription(channel)` — `@asynccontextmanager` over a lazily-imported `redis.asyncio` pubsub
  (kept out of module import scope so importing `app.platform.realtime` never requires the asyncio
  Redis client at collection/import time); subscribes on enter, unsubscribes + closes on exit.

**Publish-on-commit** (`app/services/notifications/service.py`, `app/db/session.py`)
- `create_notifications` — stashes `pending_realtime` after flush (see "How").
- `SessionLocal` gains `after_commit`/`after_rollback` listeners (`app/db/session.py:17-32`) that
  publish/drop the stash. `publish_notification` is imported **lazily inside** the `after_commit`
  listener body, not at module top — a deliberate, permanent choice (not a circular-import
  workaround): a top-level import binds `session.py`'s own name to the function object at first
  import time, which a test's `monkeypatch.setattr(app.platform.realtime, "publish_notification",
  ...)` would never see (the listener would keep calling the original, real, Redis-hitting
  function). The lazy import re-resolves the module attribute on every call.

**Endpoints** (`app/api/v1/endpoints/notifications.py`)
- `POST /notifications/stream-ticket` — `require_workspace` + `get_verified_user`, mints a ticket
  for the caller's own `(user_id, startup_id)`.
- `GET /notifications/stream?ticket=...` — consumes the ticket (401 `INVALID_TICKET` if absent/
  expired/already used), re-checks active membership (403 `FORBIDDEN` if not), returns the SSE
  `StreamingResponse` described in "How".

**Config** (`app/core/config.py:101-103`) — `SSE_TICKET_TTL: int = 30` (seconds a one-time stream
ticket is valid before it expires unused), `SSE_HEARTBEAT_INTERVAL: int = 20` (seconds between
heartbeat comments / the `pubsub.get_message` poll timeout).

**Errors / API surface** — two new routes, two new error paths: `401 INVALID_TICKET` (bad/expired/
reused ticket) and `403 FORBIDDEN` (ticket valid but caller no longer has an active membership for
that workspace) — same `AppError`/error-envelope convention as every other route in the codebase,
no new error-handling pattern introduced.

**Tests**
- `tests/platform/test_realtime.py` (Task 1, 6 tests) — `channel_for` format; `publish_notification`
  publishes the right channel+payload via a fake Redis and swallows a raised Redis error (fail-soft
  proof); ticket mint/consume is one-time (second consume → `None`) and TTL-bounded;
  `subscription` yields a working pubsub via a fake async client.
- `tests/services/notifications/test_realtime_publish.py` (Task 2, 2 tests) —
  `test_publish_on_commit_one_per_recipient` (nothing published before `commit()`, exactly one
  publish call per recipient after, correct payload shape) and `test_no_publish_on_rollback`
  (rollback publishes nothing).
- `tests/api/notifications/test_stream.py` (Task 3, 3 tests) — auth-required on
  `POST /stream-ticket`; a bad/expired ticket → 401 on `GET /stream`; a direct drive of
  `_event_stream` with a fake pubsub + fake `Request` proving the `unread` → `notification.created`
  → heartbeat → disconnect frame sequence.
- `e2e/test_notifications_realtime.py::test_realtime_notification_delivery` (Task 4, new, this
  commit) — see Verification below; the one path none of the above can prove, because a fake pubsub
  by construction cannot demonstrate that Redis is a real cross-process backplane.

## Verification

**Per-task unit verification (Tasks 1–3, already green before this task; commit `4643abc` closed
a `mypy app` gap Task 2's own review missed — see the commit list above).**

**Task 4 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests e2e` | ✅ pass (2 pre-existing unformatted files fixed in this pass — see below) |
| Import order | `poetry run isort --check-only app tests e2e` | ✅ pass |
| Lint | `poetry run ruff check app tests e2e` | ✅ pass |
| Types | `poetry run mypy app` | ✅ `Success: no issues found in 156 source files` |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ 9.89/10 (unchanged — no new findings, this task touched no `app/` code) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ clean, 0 findings |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ **1227 passed**, 97.36% coverage (≥ 95% floor) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0025_roadmap_milestone_due_idx (head)`, **unchanged from `develop`** (no migration in this slice) |
| Live E2E | `./scripts/e2e_run.sh` | ✅ **43 passed** (up from 42 — the 1 new realtime journey), run twice (once before, once fresh after the black reformat) |

**Exact figures from this pass:** `black` needed reformatting on two files —
`tests/api/notifications/test_stream.py` (a pre-existing unformatted file left over from Task 3,
which only ran `black`/`ruff` on its own touched files, not the whole-repo check this task runs)
and the new `e2e/test_notifications_realtime.py` — both fixed via `poetry run black <files>` before
committing; `isort`/`ruff` were already clean on both. No `mypy`/`pylint`/`bandit` findings in this
pass (this task added no `app/` code, only `e2e/` + docs). Full breakdown in `.superpowers/sdd/
2026-09-19-notifications-realtime-sse/task-4-report.md`.

**`e2e/test_notifications_realtime.py::test_realtime_notification_delivery`** — the one live,
cross-process proof this slice needs. A founder signs up, verifies, onboards, and gets a
`POST /notifications/stream-ticket` ticket. A background **thread** in the SAME test process opens
`GET /notifications/stream?ticket=...` against the **server process** (a real uvicorn process
`scripts/e2e_run.sh` starts separately from the pytest process) as a streaming httpx request and
reads past the initial `event: unread` frame. After a 1-second settle (so the server's pubsub
subscribe is definitely live), the **test process itself** opens a fresh `SessionLocal()`, calls
`create_notifications(...)` with a throwaway `type="x.realtime.test"` payload, and commits — this
fires the `after_commit` listener **in the test process**, which `PUBLISH`es to Redis; the
**server process's** already-subscribed stream picks it up and forwards it down the open HTTP
connection back to the reading thread, which asserts the frame's `type` and `data` match and writes
it to `e2e/_captures/notifications_realtime/stream_frame.json`. This is the only test in the whole
slice that could not be faked — it is the actual proof that two separate OS processes talk through
Redis, not just that two Python objects under a shared fake talk to each other. A hard
`t.join(timeout=8.0)` plus an `assert not t.is_alive()` makes a stalled stream fail the suite loudly
rather than hang it. `stream_ticket.json` (the ticket-mint response) is also captured, from a
second, separate ticket call (the first ticket is one-time-consumed by opening the stream itself).

**Make-or-break fact confirmed live, not assumed:** `GET /api/v1/auth/me`'s response nests the
user id under `data.user.id`, not `data.id` — `{"user": {"id": ..., "email": ..., "status": ...},
"profile": ..., "memberships": [...], "active_workspace_id": ...}` (confirmed against
`app/api/v1/endpoints/auth/me.py`, not assumed from the task brief's own snippet, which had it at
the top level).

## Operate / roll back

**New deploy-time requirement: none.** No new service, no new container, no new health check — the
two new routes live in the existing API process(es); the `after_commit`/`after_rollback` listeners
run inside every existing DB session; Redis was already required infrastructure since Slice 2.
`alembic upgrade head`/`downgrade -1` are both no-ops for this slice (no migration touches this
code at all — nothing to run, nothing to roll back at the DB layer). Reverting this slice's commits
as a unit removes the two routes and the listeners; any in-flight SSE connections simply get
whatever the reverted code serves next (a 404 on reconnect, since the route would no longer exist).

**New/changed config (both have safe defaults; only override if the defaults are wrong for
production):**
- `SSE_TICKET_TTL` (default `30` seconds) — how long a minted stream ticket stays valid if unused.
  Too short risks a slow client failing to connect before it expires (mint → 401 on connect,
  invisible to the FE unless it retries); too long widens the window an intercepted ticket URL
  stays exploitable. 30s was chosen as "long enough for `POST` response → immediate `EventSource`
  construction, short enough that a leaked URL is worthless within under a minute."
- `SSE_HEARTBEAT_INTERVAL` (default `20` seconds) — how often an idle connection gets a heartbeat
  comment. **Ops note:** nginx (or any reverse proxy in front of the API) needs a `proxy_read_timeout`
  comfortably larger than this (e.g. 60s+) for `/api/v1/notifications/stream` specifically — a
  timeout shorter than the heartbeat interval would close idle connections out from under
  `EventSource`, which would just reconnect (auto-reconnect is built into the browser API) but at
  the cost of a reconnect-storm-y experience under real traffic. `X-Accel-Buffering: no` is already
  sent per-response by the app, so no nginx config change is needed for buffering — only the
  timeout value is an infra-side setting worth confirming, not something this slice's code can set
  from the app side.

**Rollback:** revert this slice's commits as one unit (same shape as every prior Module 20 slice's
rollback note) — there is no data-integrity concern to roll back (nothing persisted to Postgres),
only Redis keys (`sse_ticket:*`, ephemeral, self-expiring) and in-flight pub/sub subscriptions,
neither of which survives a deploy anyway.

## Follow-ups

**Device/web push (VAPID/FCM/APNs) is still unbuilt** — this slice covers only "the app is open in
a tab/window with SSE support," not "the app is closed and the OS should show a notification."
Explicit v1 non-goal (design doc), the other half of "real-time or push" that Module 20's original
4-slice decomposition anticipated but deferred past this slice.

**No `Last-Event-ID` replay / server-side event log.** A client that misses events while
disconnected has no way to ask "what happened between X and now" over SSE — it must re-fetch
`GET /notifications` (see "How" — reconcile, not replay). If gap-filling ever becomes a real UX
complaint (e.g. very lossy mobile networks reconnecting constantly), a short per-user ring buffer
keyed by `Last-Event-ID` is the natural next step, not built here.

**One Redis pubsub connection per open SSE stream, not a shared per-node subscriber.** At current
scale (early-stage product, no concurrent-connection numbers yet to worry about) this is simple and
correct; if concurrent connections grow into the thousands-per-node range, a single shared
subscriber per web-worker process (fanning out in-process to each connection) would reduce Redis
connection count — a scaling optimization, not a correctness fix, deliberately deferred.

**No read-state fan-out across a user's own open tabs.** Marking a notification read in one tab
does not push that state to another tab the same user has open elsewhere — each tab's feed is only
as fresh as its own last fetch/reconnect-reconcile. Explicit v1 non-goal (design doc); YAGNI until a
real multi-tab complaint surfaces.

**Module 20 is now FULLY complete — all 4 slices shipped.** Every already-shipped module's "real
notification delivery — Module 20" deferred line (Dashboard, Roadmap, Mission, Health Score,
Documents, Business Builder, Assessment, onboarding — see those modules' own SOPs) is now
retired: in-app (Slice 1), email (Slice 2), scheduled/time-based triggers (Slice 3), and now
real-time push delivery for an open app (Slice 4, this SOP) are all live. The one remaining gap
across all four slices is device/web push for a *closed* app, tracked above, not scoped to any
existing module's deferred line.
