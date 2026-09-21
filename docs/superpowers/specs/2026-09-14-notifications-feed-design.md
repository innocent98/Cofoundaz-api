# Module 20 Notifications — Slice 1 Design: In-App Notification Feed

**Date:** 2026-09-14
**Module:** 20 Notifications (Slice 1 of ~4)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)

---

## 1. Context & Scope

Nearly every shipped module already publishes domain events via `app/platform/events.py`'s
`event_bus`, but the bus is a no-op logger — **nothing consumes events**, and every module deferred
"real notification delivery" to Module 20. Slice 1 builds the **in-app notification feed**: consume
the notification-worthy events → per-user notification rows → a feed the founder's bell/inbox reads.

**Module 20 decomposes into ~4 slices** (agreed 2026-09-14): **1 In-app feed + fan-out** (this),
2 Email delivery + preferences (Resend exists), 3 Scheduler/cron (mission 06:00, roadmap overdue,
quarterly re-assessment), 4 Real-time (websocket) + push. This spec is **Slice 1 only** — the core
that retires the *in-app* half of every deferred notification item.

### Fan-out approach (decided 2026-09-14)
**Real event bus, same transaction.** `event_bus` becomes a synchronous dispatcher; `publish` gains
a `db` session so registered handlers INSERT notification rows in the **same transaction** as the
triggering action — a notification exists iff its action committed (no phantom "your document was
signed" if the sign rolled back). This also finally makes the bus a proper bus (future consumers —
analytics, webhooks — can subscribe).

### Non-goals / deferred (later slices)
- Email/push/websocket delivery (Slices 2/4); scheduler/cron triggers (Slice 3); per-user
  notification preferences (Slice 2); grouping/digest; the ~45 non-notification events (analytics/
  security tags) stay unhandled.

---

## 2. Event bus → synchronous dispatcher (`app/platform/events.py`)

- **Protocol/signature change:** `publish(db: Session, event: str, payload: dict) -> None` (was
  `publish(event, payload)`). Every publish site (~32, all inside `db`-scoped service functions)
  gains a leading `db,` — a mechanical pass (Task 1). Tests that monkeypatch `publish` update their
  lambda signature to `(db, event, payload)` / `*args`.
- **`subscribe(event: str, handler: Callable[[Session, dict], None]) -> None`** registers a handler.
- **`publish`** keeps the existing behavior — append to `.published` (test assertions) + `log.info`
  — **and** invokes each handler registered for `event`.
- **Robustness (required):** each handler runs inside its **own `db.begin_nested()` savepoint**
  wrapped in try/except; a handler that raises rolls back only its own writes and is logged
  (`log.warning`), and the triggering action proceeds unaffected. A notification bug must NEVER
  break a core action. (Mirrors the SAVEPOINT pattern in `get_or_create_canvas` /
  `_get_or_generate_today_race_safe`.)
- The default `event_bus` singleton becomes the dispatcher. Handlers are registered at app startup
  (an import-side-effect `register()` in the notifications module, called from app setup).

```python
class EventBus(Protocol):
    def publish(self, db: Session, event: str, payload: dict) -> None: ...
    def subscribe(self, event: str, handler: Callable[[Session, dict], None]) -> None: ...
```

---

## 3. Data model — `notifications` (new table + migration)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |
| `user_id` | UUID FK→`users.id` CASCADE, `index=True` | recipient |
| `startup_id` | UUID FK→`startups.id` CASCADE, `index=True` | workspace scope |
| `type` | `String(60)`, NOT NULL | the event type (e.g. `document.signature.completed`) |
| `title` | `String(255)`, NOT NULL | rendered copy |
| `body` | `String(500)`, NOT NULL, `server_default ''` | rendered copy |
| `data` | `JSONB`, NOT NULL, `server_default '{}'` | payload/deep-link context (entity ids) |
| `read_at` | timestamptz, nullable | null ⇒ unread |

Indexes: standalone `ix_notifications_user_id`, `ix_notifications_startup_id` (FK convention) +
composite `Index("ix_notifications_user_startup_created", "user_id", "startup_id", "created_at")`
for the feed. A notification is per-`(user, workspace)`; a workspace event fans out to N rows.

**Migration:** `0021_notifications`, chained onto the current develop head `0020_signatures`.
⚠️ Coordination: the junior's Module 17 is also in flight (was told to take `0019`, but develop has
since advanced to `0020_signatures`, so on rebase it takes the next free number too). Whoever of
Module 17 / this branch merges second renumbers to keep a **single alembic head**; settle the exact
prefix against the live head at build time.

---

## 4. Fan-out registry — `app/services/notifications/registry.py`

A single curated map `EVENT_HANDLERS: dict[str, Handler]`; `register()` subscribes each on the bus.
A handler `(db, payload)`:
1. computes recipient `user_ids` (a per-event **recipient rule**),
2. renders `title`/`body` from the payload,
3. inserts one `Notification` row per recipient (via the service in §5).

**Recipient rules:**
- **Default:** all **active** members of `payload["startup_id"]` (query `Membership` where
  `status == active`), **excluding the actor** when the payload carries an actor id
  (`actor_id`/`user_id`/`shared_by`/`created_by` — whichever the event provides).
- **Per-event overrides:** `document.signature.completed` → the request's `created_by`;
  `member.joined` → the workspace's existing active members (not the new joiner). Overrides live in
  the registry entry.

**v1 handled set (~15; the rest stay unhandled, extensible in this one file):**
`document.shared`, `document.signature.requested`, `document.signature.signed`,
`document.signature.completed`, `business.suggestion.created`, `business.suggestion.approved`,
`business.suggestion.rejected`, `business.artifact.completed`, `roadmap.replanned`,
`roadmap.milestone.completed`, `mission.completed`, `mission.streak.milestone`,
`healthscore.dropped`, `assessment.completed`, `member.joined`.

**Payload dependency:** handlers use ONLY the event payload (ids/values already in it) — they do not
read the just-created entity (which may be uncommitted in the same txn). Where a handler needs a
field a payload lacks (e.g. a title), it either reads a committed related row (safe) or renders a
generic copy. Any payloads that must be enriched to render good copy are enumerated in the plan and
the emitting `publish(...)` call is extended to include them (small, additive).

---

## 5. Service — `app/services/notifications/service.py`

- `create_notifications(db, *, user_ids, startup_id, type, title, body, data) -> list[Notification]`
  — bulk-insert one row per recipient user; `flush`.
- `list_notifications(db, *, user_id, startup_id, unread, limit, cursor) -> tuple[list, next_cursor]`
  — keyset pagination on `(created_at, id)` desc, filtered by user+startup (+ `read_at is null` when
  `unread`), mirroring `GET /dashboard/activity`.
- `unread_count(db, *, user_id, startup_id) -> int`.
- `mark_read(db, membership, notification_id) -> Notification` — scoped to `(user_id, startup_id)`;
  `NotFound` (404) if not the user's; sets `read_at = now` (idempotent).
- `mark_all_read(db, *, user_id, startup_id) -> int` — bulk `UPDATE … SET read_at = now WHERE
  user_id = … AND startup_id = … AND read_at IS NULL`; returns count.
- `serialize_notification(n) -> dict` — `{id, type, title, body, data, read, created_at}`.

---

## 6. Endpoints — new router `app/api/v1/endpoints/notifications.py`

Auth = verified user + `require_workspace`. A user only ever sees their **own** rows (scoped by
`membership.user_id` AND `membership.startup_id`) — cross-user access is impossible.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/notifications?unread=&limit=&cursor=` | the user's feed for the active workspace, newest-first, keyset pagination (`{notifications:[…], next_cursor}`) |
| `GET` | `/notifications/unread-count` | `{unread: N}` (the bell badge) |
| `POST` | `/notifications/{notification_id}/read` | mark one read → serialized notification; 404 if not the user's |
| `POST` | `/notifications/read-all` | `{marked: N}` |

Registered in `app/api/v1/api.py`. `notification_id` typed `uuid.UUID`. Every write endpoint calls
`db.commit()`.

---

## 7. Errors
- `NotFound` (404) — a notification id that isn't the current user's (uniform; no leak).
- `Forbidden` (403) — non-member on any (via `require_workspace`).

---

## 8. Testing
**Unit (real Postgres):**
- **Bus dispatcher:** `subscribe` + `publish(db, …)` invokes the handler with `(db, payload)`; a
  handler that raises is isolated by its savepoint and logged — the triggering `publish` returns
  normally and the caller's other writes still commit; unregistered events invoke no handler.
- **Registry:** a `document.signature.completed` payload creates a row for the request creator; a
  `business.suggestion.approved` payload creates rows for active members **minus the actor**; a
  non-member/actor gets none.
- **Service/endpoints:** feed lists only the caller's rows for the active workspace; `unread`
  filter; keyset pagination returns a working `next_cursor`; `unread-count`; mark-read (404 for
  another user's row); read-all returns the count and zeroes the unread count; a second workspace's
  notifications never appear.
- **Same-txn guarantee:** an action whose request rolls back leaves NO notification (fire the event
  in a txn, roll back, assert zero rows).

**Live e2e (`scripts/e2e_run.sh`):** two members in one workspace; member A performs an action that
fires a handled event (e.g. shares a document / creates a suggestion) → member B's
`GET /notifications` shows exactly one notification with the right `type`/`title`/`data`, unread;
B marks it read → `unread-count` drops. Capture every body.

**FE integration guide** `docs/fe-integration-guide-notifications.md` — verbatim captured bodies;
the feed shape + keyset pagination cadence; the `unread`/`read` semantics + the bell-badge count;
the notification `type` catalog (the v1 handled set) and the `data` deep-link fields per type; note
that delivery is **in-app only in this slice** (email/push/real-time are later slices).

---

## 9. File structure
| File | Change |
|---|---|
| `app/platform/events.py` | dispatcher: `publish(db, …)` + `subscribe` + per-handler savepoint/try-except |
| ~32 publish call sites (services/api) | prepend `db,` to each `event_bus.publish(...)` |
| tests that monkeypatch `event_bus.publish` | update lambda signature to `(db, event, payload)` |
| `app/db/models/notification.py` | `Notification` model |
| `alembic/versions/0021_notifications.py` | new migration |
| `app/services/notifications/service.py` | create/list/unread_count/mark_read/mark_all_read/serialize |
| `app/services/notifications/registry.py` | event→notification handlers + `register()` |
| `app/api/v1/endpoints/notifications.py` | 4 routes |
| `app/api/v1/api.py` | include the router; call `register()` at startup |
| `app/schemas/notification.py` (if needed) | any request models |
| `tests/…/notifications/`, `e2e/…` | unit + e2e |
| `docs/fe-integration-guide-notifications.md`, `docs/sop/…`, `docs/checklist/PROJECT_CHECKLIST.md` | docs |

---

## 10. Decisions & waivers
- **D1 — Same-txn synchronous bus dispatch** (`publish(db, …)`), per-handler savepoint + try/except
  so a notification failure never breaks a core action. (User decision, 2026-09-14.)
- **D2 — Recipient default = active members minus actor**; per-event overrides in the registry.
- **D3 — v1 handled set = the ~15 events in §4**; the rest stay unhandled (still logged/recorded).
- **D4 — Notifications scoped per-`(user, workspace)`**; the feed keys off the active
  `X-Workspace-Id` and the caller's `user_id`.
- **D5 — In-app delivery only** this slice; email/push/real-time/scheduler are Slices 2–4.
- **D6 — Migration `0021_notifications`**; coordinate the number with the junior's Module 17 at
  build/merge time to preserve a single head.
