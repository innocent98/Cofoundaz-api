# SOP — Notifications, In-App Feed + Fan-Out (Module 20, Slice 1)

**What shipped** — the first slice of Module 20: the platform event bus (`app/platform/events.py`)
becomes a real, synchronous, same-transaction dispatcher instead of a no-op logger; a curated
registry fans ~15 domain events out to per-user `notifications` rows; a 4-route feed
(`GET /notifications`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`,
`POST /notifications/read-all`) lets a founder/teammate read and clear their own workspace-scoped
feed. This retires the *in-app* half of "real notification delivery" that every prior module
(Dashboard, Roadmap, Mission, Health Score, Documents, Business Builder, Assessment, onboarding)
deferred to Module 20.

Commits (branch `feat/notifications-feed`, off `develop`):
`32621a1` (design) → `bf51f0b` (implementation plan) → `ab48c0a` (Task 1 — `publish(db, …)` +
`subscribe` + per-handler savepoint dispatcher, ~32 call sites updated) → `0cce8c1` (Task 2 —
`notifications` table + model + migration `0021_notifications`) → `8d302b1` (Task 3 — service:
create/list-keyset/unread_count/mark_read/mark_all_read/serialize) → `6bd5ffc` (Task 4 — registry:
~15 event → notification handlers) → `72895e0` (Task 5 — 4 feed endpoints + `register()` called at
import time from `app/api/v1/api.py`) → this task's e2e/SOP/FE-guide/checklist commit (Task 6, final
task of the 6-task plan, `.superpowers/sdd/2026-09-14-notifications-feed-slice1/`).

## Why

Nearly every shipped module already calls `event_bus.publish(...)` — Roadmap re-plans, mission
completions, health-score drops, business suggestions, document shares/signatures, workspace
invites — but the bus was a no-op logger (`DispatchingEventBus.published.append(...)` + `log.info`,
nothing consumed it). Every one of those modules' SOPs carries the same deferred line: *"real
notification delivery — Module 20."* This slice is the first thing that actually reads those events
and turns them into something a founder sees: an in-app bell/inbox feed.

## How

**Real event bus, same transaction — not a queue.** The design (`docs/superpowers/specs/
2026-09-14-notifications-feed-design.md`, D1) rejected an async queue/worker for v1: a notification
should exist **iff** the triggering action actually committed — no "your document was signed"
notification for a signature that itself rolled back. So `publish` gained a `db: Session` parameter
and now invokes every handler subscribed to that event **synchronously, inside the caller's own
transaction**. `app/platform/events.py::DispatchingEventBus.publish` is the one-line summary:
append to `.published` (existing test-assertion hook) + `log.info` (existing behavior, unchanged)
+ call each handler.

**Per-handler `db.begin_nested()` savepoint, not a bare call.** A notification bug must never break
the action that triggered it — a founder signing a document should never 500 because a handler threw
a `KeyError` rendering copy. Each handler runs inside `with db.begin_nested(): handler(db, payload)`
wrapped in `try/except Exception`; on failure the SAVEPOINT rolls back *only that handler's writes*
and `log.warning`s, while the outer transaction (the actual signature/share/suggestion write) is
completely unaffected and proceeds to its own `db.commit()`. This mirrors the existing SAVEPOINT
pattern already in the codebase (`get_or_create_canvas`, `_get_or_generate_today_race_safe`) rather
than inventing a new isolation mechanism.

**Data-driven registry, one file, ~15 rows.** `app/services/notifications/registry.py::SPECS` is a
`dict[str, NotifSpec]` — `NotifSpec(recipients, title, body)` — one entry per handled event. Adding
event #16 is a one-line addition to `SPECS`, not new plumbing. `register(bus=event_bus)` subscribes
every key at import time (called from `app/api/v1/api.py`, right after the router imports, so it
runs once per process on app startup — idempotent via the `_registered` module flag so re-importing
in tests doesn't double-subscribe the default bus).

**Recipient rule: active members minus actor (default), overridable per event.**
`_members_minus_actor` queries `Membership` where `startup_id` matches and `status == active`, then
excludes the actor if the payload names one. `workspace.member.joined` gets its own
`_existing_members` override (exclude the *joiner*, not payload-resolved — see the Known Gap below
for why this distinction matters).

**Generic per-type copy, not per-instance rendering.** `title`/`body` in `SPECS` are fixed strings
per event type (e.g. *"A document was shared in your workspace"*), not templated from payload
fields — the design's payload-dependency rule (§4 of the spec) is that handlers use **only** the
event payload already in hand; they never read the just-published entity, which may still be
uncommitted in the same transaction. Richer, per-instance titles (e.g. naming the actual document)
are a follow-up, not built here.

**⚠️ KNOWN GAP surfaced by this task's live e2e run (found in Task 6, not fixed — see
"No app/ changes" below):** `_members_minus_actor` resolves the actor via
`payload.get(k) for k in ("actor_id", "shared_by", "created_by", "user_id", "shared_by_id")`. Of the
14 v1 events routed through `_members_minus_actor` (every handled event except
`workspace.member.joined`, which uses its own `_existing_members` helper), **none** of their
publish-site payloads carry a key from that list — checked directly against all 32
`event_bus.publish(...)` call sites. Concretely: `document.shared`'s publish site
(`app/services/documents/shares.py::create_share`) sends `{startup_id, document_id, share_id}` — no
actor field — so `_actor(payload)` returns `None` and `_active_member_ids(..., exclude=None)`
excludes nobody. `roadmap.replanned` comes closest (it sends `applied_by`), but `applied_by` isn't
in `_actor`'s key list either, so it too doesn't exclude. **The actor is notified of their own
action** for `document.shared` and, by the same mechanism, for all 14 `_members_minus_actor`-routed
events. The **one** event where exclusion genuinely works is `workspace.member.joined`, because its
handler bypasses `_actor()`'s payload-key guessing and reads `payload["user_id"]` (the joiner)
directly via the dedicated `_existing_members` helper. This is a real, confirmed gap in Tasks 1–5's
shipped code, not a hypothetical — `e2e/test_notifications.py` asserts and captures the actual
behavior (A also receives `document.shared` for A's own share) rather than silently asserting the
spec's aspirational exclusion. Flagged as a background task for a docs-plus-app follow-up rather
than fixed in this docs-only task — see Follow-ups.

**Why Tasks 1–5's own unit test didn't catch this:** `tests/services/notifications/
test_registry.py::test_members_minus_actor` calls `_handle` directly with a **hand-built** payload
that includes `"shared_by": str(owner.id)` — a key that happens to be in `_actor()`'s list — so the
unit test genuinely passes and genuinely proves `_members_minus_actor` excludes correctly *when given
the right key*. It never exercises the real publish site's actual payload shape — `create_share`'s
real `document.shared` fan-out sends no such key. This is exactly the class of bug live e2e testing
exists to catch (a unit correct in isolation, wired to a real caller that doesn't feed it what it
needs) — not a criticism of the unit test itself, just why this task's live run is the first place
the gap became visible.

**No app/ changes in this task, by design.** Task 6's brief scopes it to live e2e + docs only; the
known gap above is a Task 1–5 defect discovered while writing the live journey, and fixing it
(adding an actor key to ~14 publish-site payload dicts, or teaching `_actor()` a per-event key
mapping) is `app/` work that belongs in its own reviewed change, not folded silently into a
docs/e2e commit.

## What's involved

**Event bus** (Task 1)
- `app/platform/events.py` — `EventBus` protocol gains `db` on `publish`; `DispatchingEventBus`
  dispatches to subscribed handlers inside a per-handler savepoint + try/except.
- 32 `event_bus.publish(...)` call sites — 28 across `app/services/**` and 4 in
  `app/api/v1/endpoints/**` (2 in `roadmap.py`, 2 in `auth/registration.py`) — mechanical `db,`
  prepended to each.
- Tests that monkeypatch `event_bus.publish` updated to the `(db, event, payload)` signature.

**Data model / migration** (Task 2)
- `alembic/versions/0021_notifications.py` — `notifications` table, chains off `0020_signatures`
  (the live head at build time — `0019` was reserved for a concurrent Module 17 branch and
  intentionally skipped, matching the spec's D6 coordination note).
- `app/db/models/notification.py` — `Notification(UUIDMixin, TimestampMixin, Base)`: `user_id`/
  `startup_id` (both FK `ON DELETE CASCADE`, both standalone-indexed), `type` (`String(60)`),
  `title` (`String(255)`), `body` (`String(500)`, default `''`), `data` (`JSONB`, default `{}`),
  `read_at` (nullable — `NULL` ⇒ unread) + composite `ix_notifications_user_startup_created` on
  `(user_id, startup_id, created_at)` for the feed query.

**Service** (Task 3) — `app/services/notifications/service.py`
- `create_notifications(db, *, user_ids, startup_id, type, title, body, data)` — bulk-insert one row
  per recipient, `flush`.
- `list_notifications(db, *, user_id, startup_id, unread, limit, cursor)` — keyset pagination on
  `(created_at desc, id desc)`, `limit` clamped to `[1, 50]`, cursor is base64 of
  `"{created_at.isoformat()}|{id}"`; malformed cursor → `422 VALIDATION_ERROR`.
- `unread_count`, `mark_read` (404 if not the caller's row, idempotent re-mark), `mark_all_read`
  (bulk `UPDATE ... WHERE read_at IS NULL`, returns count), `serialize_notification` (`{id, type,
  title, body, data, read, created_at}`).

**Registry** (Task 4) — `app/services/notifications/registry.py`
- `SPECS: dict[str, NotifSpec]` — 15 events: `document.shared`, `document.signature.requested`,
  `document.signature.signed`, `document.signature.completed`, `business.suggestion.created`,
  `business.suggestion.approved`, `business.suggestion.rejected`, `business.artifact.completed`,
  `roadmap.replanned`, `roadmap.milestone.completed`, `mission.completed`,
  `mission.streak.milestone`, `healthscore.dropped`, `assessment.completed`,
  `workspace.member.joined` (**note:** the spec's brief called this `member.joined`; the real
  emitting site (`app/services/onboarding/invites.py::accept_invitation`) publishes
  `workspace.member.joined`, so the registry key matches the code, not the brief's shorthand).
- `_handle(db, event, payload)` — no-op if the event has no spec or the payload lacks `startup_id`;
  otherwise resolves recipients, creates one row per recipient via the service.
- `register(bus=event_bus)` — subscribes all 15 at import time; idempotent for the default bus via
  a module-level `_registered` flag (a fresh bus instance, e.g. in tests, always re-subscribes).

**Endpoints** (Task 5) — `app/api/v1/endpoints/notifications.py`, registered + `register()` called
in `app/api/v1/api.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/v1/notifications` | verified user + `require_workspace` |
| GET | `/api/v1/notifications/unread-count` | verified user + `require_workspace` |
| POST | `/api/v1/notifications/{notification_id}/read` | verified user + `require_workspace` |
| POST | `/api/v1/notifications/read-all` | verified user + `require_workspace` |

All four scope strictly to `(membership.user_id, membership.startup_id)` — cross-user/cross-tenant
access is structurally impossible, not just filtered out.

**Errors** — no new error codes. `NOT_FOUND` (404 — unknown notification id, or a real row that
belongs to a different user/workspace — uniform, no leak); `FORBIDDEN` (403 — non-member, via
`require_workspace`); `VALIDATION_ERROR` (422 — malformed pagination cursor).

**Tests**
- `tests/platform/test_events.py` (3) — dispatcher invokes `(db, payload)`; a raising handler is
  savepoint-isolated and the caller's other writes still commit; unregistered events invoke nothing.
- `tests/db/test_notification_model.py` (1) — `Notification` round-trips through the migrated table.
- `tests/services/notifications/test_service.py` (6) — create/list/unread/keyset-cursor/mark-read
  (404 cross-user)/mark-all-read.
- `tests/services/notifications/test_registry.py` (5) — `_members_minus_actor` excludes a synthetic
  payload's `shared_by` actor (`test_members_minus_actor` — note this test's hand-built payload
  *includes* a `shared_by` key, which is exactly why it passes; the real `document.shared` publish
  site does not include that key, which is the Known Gap below — the unit test alone does not catch
  it, only the live e2e run does); `workspace.member.joined` → existing members, not the joiner
  (`test_member_joined_notifies_existing_members_not_joiner`); every `SPECS` entry renders non-empty
  generic title copy without a `KeyError` (`test_all_specs_have_generic_copy`); an unregistered
  event is a no-op (`test_handle_unknown_event_noop`); `register()` actually subscribes onto a given
  bus (`test_register_subscribes_on_bus`).
- `tests/api/test_notifications.py` (5) — feed scoping, `unread` filter, `unread-count`, mark-read
  404 for another user's row, read-all.
- `e2e/test_notifications.py::test_notifications_journey` (new, this task) — see Verification below.

## Verification

- **Unit suite: 1035 passed** (`poetry run pytest -q`) — unchanged from the pre-Task-6 baseline
  (Tasks 1–5 landed all new unit tests already; this task is docs/e2e only, no `app/` or test
  changes, confirmed by re-running the full suite and getting the identical count). 98% coverage.
- **Live E2E: 38 passed** (`scripts/e2e_run.sh`) — up from 37, +1 for this task's new
  `test_notifications_journey`. Fresh `cofoundaz_e2e` migrated from zero through
  `0021_notifications` (verified single alembic head, sole chain off `0020_signatures` with `0019`
  skipped as designed), real uvicorn with `EMAIL_BACKEND=file`, all 38 green.
  - `test_notifications_journey`: founder A onboards through the wizard → invites teammate B
    (`POST /onboarding/invites`, **before** `onboarding/complete` — invites 409 once
    `onboarding_completed_at` is set) → B signs up, verifies, and accepts (`POST
    /invitations/accept`) — a REAL second active member, not an external share recipient → A
    completes onboarding → B's baseline feed/unread-count are both empty → A creates a document and
    shares it **twice** with two different external recipients (two real `document.shared` events)
    → B's `GET /notifications?unread=true` shows exactly 2 unread `document.shared` rows with
    `data.share_id` matching both shares → `unread-count` = 2 → keyset pagination exercised with
    `limit=1` (page 1: 1 row + non-null `next_cursor`; page 2, following the cursor: the other row +
    `next_cursor: null`) → mark one read (`read: true`) → `unread-count` drops to 1 → re-marking the
    same row read is idempotent (still 200, still `read: true`) → an unknown notification id 404s →
    **A's own `document.shared` row (a distinct per-recipient row id) 404s when B tries to mark it
    read** — cross-user scoping confirmed, not just cross-tenant → `read-all` marks the remaining
    row (`{marked: 1}`) → `unread-count` → 0 → the unread-filtered feed is now empty, but the full
    feed still shows both rows with `read: true` (read-all marks, never deletes). The journey also
    captures A's own feed (`actor_not_excluded_known_gap.json`) as live proof of the Known Gap above
    — A shows `document.shared` × 2 plus `workspace.member.joined` × 1 (from B's accept, correctly
    excluding B the joiner).
  - 19 new captures to `e2e/_captures/notifications/*.json` — every one is the verbatim source for
    `docs/fe-integration-guide-notifications.md`, re-read fresh after the final green
    `scripts/e2e_run.sh` run in this task (not reused from an earlier run).
- `poetry run ruff check app tests e2e`, `poetry run black --check app tests e2e`,
  `poetry run mypy app` — all clean.
- Migration round-trip verified via the `scripts/e2e_run.sh` run above (fresh `cofoundaz_e2e`
  migrated `0020_signatures` → `0021_notifications` from zero); this task made no migration changes
  (Task 2 already shipped `0021_notifications`), so no separate `upgrade head / downgrade -1 /
  upgrade head` round-trip was re-run here — that check belongs to Task 2's own verification, not
  repeated in a docs-only task.

## Operate / roll back

- No new deploy-time env vars. `alembic upgrade head` picks up `0021_notifications` automatically.
- The event bus becoming a real dispatcher is **behavior-preserving for every existing caller when
  there are no handlers registered** for an event — `publish` still appends to `.published` and logs
  exactly as before; the only new behavior is invoking subscribed handlers, and the only handlers
  registered as of this slice are the 15 notification ones. No other module's publish sites needed
  behavior changes, only the mechanical `db,` signature update.
- **Rollback:** `alembic downgrade -1` from `0021_notifications` drops
  `ix_notifications_user_startup_created`, `ix_notifications_user_id`,
  `ix_notifications_startup_id`, then the `notifications` table — **lossy**: every notification
  (read or unread) is destroyed. Rolling back the migration without also reverting Task 5's router
  registration (`app/api/v1/api.py`) and `register()` call would make all 4 feed endpoints 500
  against a missing table — revert both together. Reverting Task 1's bus-dispatcher signature change
  is separable but not free: it means reverting the `db,` prepend at all 32 publish sites too (the
  signature change is not opt-in per-caller), so in practice a full rollback of this slice is
  "revert Tasks 1–5's commits together," not a partial one.

## Follow-ups

**Deferred to later slices (by design, per the spec's decomposition):**
- **Slice 2 — Email delivery + per-user preferences** (Resend backend already exists, from
  `docs/sop/2026-09-12-resend-email-backend.md`; this slice is in-app only).
- **Slice 3 — Scheduler/cron** (mission 06:00 generation, roadmap-overdue nudges, quarterly
  re-assessment — several existing modules' SOPs already carry a "needs Module 20 scheduler" line).
- **Slice 4 — Real-time (websocket) + push.**
- Richer, per-type/per-instance titles (today: one fixed string per event type; no document/
  suggestion/milestone name interpolated in) and notification grouping/digest (today: one row per
  event per recipient, no collapsing of e.g. 10 rapid mission-streak notifications).
- `onboarding.completed` (published from `app/services/onboarding/complete.py` with
  `{startup_id, user_id}`) is a real, already-firing event that is **not** in the v1 `SPECS` handled
  set — a founder completing onboarding generates no notification today. Not a bug (onboarding
  completion has no other workspace member to notify in the common single-founder case, and the spec
  didn't scope it in), just unbuilt — a one-line `SPECS` addition whenever a "welcome" or
  "teammate finished setup" notification is wanted.

**Known gap from this task, tracked for a follow-up `app/` change (see "Known Gap" in How above,
and the live capture `e2e/_captures/notifications/actor_not_excluded_known_gap.json`):**
- `_members_minus_actor`'s actor-resolution key list (`actor_id`/`shared_by`/`created_by`/`user_id`/
  `shared_by_id`) does not match the keys any of the 14 `_members_minus_actor`-routed publish-site
  payloads actually use (most send no actor-identifying field at all; `roadmap.replanned` sends
  `applied_by`, which also isn't in the list). Net effect: the actor receives a notification for
  their own action on every v1 event except `workspace.member.joined` (which bypasses `_actor()` via
  a dedicated payload-keyed helper). Fixing this needs either (a) adding an actor-identifying key to
  each affected publish-site payload dict (~14 small, additive changes across `app/services/**`), or
  (b) teaching the registry a per-event actor-key mapping instead of one shared guess-list. Not fixed
  in this task — Task 6 is docs/e2e-only per the brief, and this is a Tasks-1–5 defect, not something
  introduced here.
