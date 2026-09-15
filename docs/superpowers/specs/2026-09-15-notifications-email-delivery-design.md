# Module 20 Notifications — Slice 2 Design: Email Delivery + Preferences + a Minimal Job Worker

**Date:** 2026-09-15
**Module:** 20 Notifications (Slice 2 of ~4)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)
**Depends on:** Slice 1 (in-app feed + same-transaction event fan-out, merged PR #58). Reuses the
existing email seam (`app/platform/email.py`, `get_email_sender()`, `ResendEmailSender`).

---

## 1. Context & Scope

Slice 1 turned `event_bus` into a synchronous dispatcher and fans ~15 domain events out to per-user
**in-app notification rows**, created in the **same transaction** as the triggering action. Slice 1
deferred every *other* delivery channel. Slice 2 delivers the **email** half: when a notification is
created, and the recipient has opted in, an email is sent — **asynchronously**, through a real (if
minimal) background **job worker** that this slice introduces.

**Module 20 slices** (agreed 2026-09-14): 1 In-app feed + fan-out (done), **2 Email delivery +
preferences + job worker (this)**, 3 Scheduler/cron (mission 06:00, roadmap overdue, quarterly
re-assessment — enqueues *into the worker this slice builds*), 4 Real-time (websocket) + push.

### Why a worker, and why now (decided 2026-09-15)
`JobDispatcher` (`app/platform/jobs.py`) is a **stub**: `enqueue` persists a `queued` row and
**nothing drains it**. Enqueued jobs (certificate PDFs, etc.) never execute. Two rejected
alternatives and why:

- **Inline best-effort send** (send in the handler, in-txn): adds Resend latency to the triggering
  request, and sending before the outer transaction commits risks a **phantom email** if the action
  rolls back — the exact failure Slice 1's same-transaction design avoids for in-app rows.
- **Enqueue now, send in Slice 3:** email would not actually be delivered until Slice 3 — not
  "email delivery".

**Chosen:** enqueue an email job **in the same transaction** (commits iff the action commits → no
phantom), and build a **minimal, production-shaped worker** that drains the queue. This unlocks
*every* deferred job (certificate PDFs and, later, Slice 3's scheduled work), not just email.

### Non-goals / deferred (later slices)
Digest / batching / grouping of emails; quiet hours; **per-type** (vs per-category) toggles;
provider-level idempotency keys; websocket / push (Slice 4); scheduler / cron **triggers** (Slice 3).
In-app delivery is unchanged (always on).

---

## 2. Architecture & data flow

Builds on Slice 1's in-transaction handler. Two additions: the handler enqueues email jobs, and a
new process drains them.

```
action → event_bus.publish(db, event, payload)
         └─ notification handler (savepoint, in the action's txn)
            1. create in-app rows           [Slice 1, unchanged — never gated]
            2. for each recipient:
                 if email_enabled(recipient, workspace, category_of(event)):
                     job_dispatcher.enqueue(db, "email.notification",
                                            {"notification_id": <row id>}, startup_id)
action COMMIT  → in-app rows AND email jobs commit atomically (rollback ⇒ neither)

worker process (separate container, loop):
   claim queued jobs (FOR UPDATE SKIP LOCKED) → dispatch by type → JOB_HANDLERS[job.type]
   "email.notification" → load Notification + recipient User → send via get_email_sender()
   mark succeeded | retry with backoff | failed
```

**Gating is at enqueue time** — it reflects the recipient's preference at the moment of the event
and avoids creating jobs for opted-out users. In-app rows are **never** gated: the feed always gets
the row; only the *email* channel is preference-controlled. One workspace event fans out to N
recipients → up to N in-app rows and up to N email jobs (one per opted-in recipient).

---

## 3. Data model

### 3.1 `notification_preferences` (new table + migration)

One row per `(user, workspace)`; absent row ⇒ defaults.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |
| `user_id` | UUID FK→`users.id` CASCADE, `index=True` | the member |
| `startup_id` | UUID FK→`startups.id` CASCADE, `index=True` | workspace scope |
| `master_email` | `Boolean`, NOT NULL, `server_default 'true'` | master email switch |
| `categories` | `JSONB`, NOT NULL, `server_default '{}'` | map `category → bool`; missing key ⇒ category default |

Constraint: `UniqueConstraint(user_id, startup_id, name="uq_notification_preferences_user_startup")`
(one prefs row per member per workspace) + the standalone FK indexes (FK-index convention). JSONB
means adding a category later needs no migration.

### 3.2 `jobs` — worker columns (migration, ALTER)

The worker needs retry accounting and backoff scheduling. Add two columns to the existing `jobs`
table (`status` already carries `queued/running/succeeded/failed/cancelled`; `error`/`result`
already exist and are reused):

| Column | Type | Notes |
|---|---|---|
| `attempts` | `Integer`, NOT NULL, `server_default '0'` | incremented each claim |
| `run_after` | timestamptz, nullable | backoff gate; `NULL` ⇒ runnable now |

**Migration** (single revision covering both tables), chained onto the current develop head
`0021_notifications`. ⚠️ **Coordination:** Module 17 (junior's PR #59) also adds the next migration
after `0021`. Whoever merges second renumbers to keep **one alembic head**; settle the exact prefix
against the live head at build time.

### 3.3 Category catalog (`app/services/notifications/categories.py`, new)

The 5 categories and their event → category map (single source of truth, imported by the handler
and the prefs service):

| Category key | Events |
|---|---|
| `documents` | `document.shared`, `document.signature.requested`, `document.signature.signed`, `document.signature.completed` |
| `business` | `business.suggestion.created`, `business.suggestion.approved`, `business.suggestion.rejected`, `business.artifact.completed` |
| `roadmap_missions` | `roadmap.replanned`, `roadmap.milestone.completed`, `mission.completed`, `mission.streak.milestone` |
| `health_assessment` | `healthscore.dropped`, `assessment.completed` |
| `team` | `workspace.member.joined` |

`CATEGORY_DEFAULTS: dict[str, bool]` — **all `True`** (opt-out model); `master_email` default `True`.
An event whose type is absent from the map is **in-app only** (no email) — a safe default for events
added later before they are categorized.

---

## 4. Preferences service + endpoints

### `app/services/notifications/preferences.py` (new)
- `CATEGORIES: tuple[str, ...]` — the 5 keys (from §3.3).
- `effective_preferences(db, *, user_id, startup_id) -> dict` — the stored row merged over defaults;
  returns `{"master_email": bool, "categories": {<key>: bool for every key}}` (every category key
  always present, filled from `CATEGORY_DEFAULTS` when unset).
- `set_preferences(db, *, user_id, startup_id, master_email, categories) -> dict` — upsert the row
  (`master_email` and any subset of category keys); returns the new effective prefs. `flush`, no
  commit (endpoint commits).
- `email_enabled(db, *, user_id, startup_id, category) -> bool` — `master_email AND
  categories.get(category, CATEGORY_DEFAULTS[category])`; used by the handler at enqueue time. A
  `category` of `None` (uncategorized event) ⇒ `False`.

### `app/api/v1/endpoints/notifications.py` (extend the Slice 1 router)
Auth = verified user + `require_workspace`; scoped to `membership.user_id` + `membership.startup_id`.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/notifications/preferences` | effective prefs (defaults filled) → `{master_email, categories:{…}}` |
| `PUT` | `/notifications/preferences` | upsert; body `{master_email?: bool, categories?: {<key>: bool}}`; returns new effective prefs; `db.commit()` |

`PUT` request schema (`app/schemas/notification.py`, extend): `master_email: bool | None = None`,
`categories: dict[str, bool] | None = None`. **Unknown category keys → 422** (validated against
`CATEGORIES`); non-bool values → 422 (Pydantic).

---

## 5. Notification handler change (`app/services/notifications/registry.py`)

`_handle(db, event, payload)` gains, after creating the in-app rows (Slice 1 behavior unchanged):

```python
category = category_for(event)            # from categories.py; None ⇒ in-app only
for n in created_rows:                     # create_notifications returns the rows
    if email_enabled(db, user_id=n.user_id, startup_id=n.startup_id, category=category):
        job_dispatcher.enqueue(db, "email.notification", {"notification_id": str(n.id)}, n.startup_id)
```

`create_notifications` (Slice 1) already returns the inserted rows, so per-recipient enqueue needs
their ids and nothing more. All of this runs inside the handler's existing savepoint, i.e. the
action's transaction — so the email jobs are committed iff the action commits. A failing
`email_enabled`/`enqueue` is contained by the handler's existing try/except (a notification bug
never breaks the action), exactly as in Slice 1.

---

## 6. The worker

### 6.1 Module layout (`app/worker/`, new)
- `runner.py` — `JOB_HANDLERS: dict[str, Handler]`, `register()` (idempotent, mirrors the
  notifications registry), `claim_batch(db)`, `run_once(db)` (claim → dispatch → finalize),
  `main_loop()` (poll + graceful SIGTERM).
- `__main__.py` — entrypoint: build a `Session`, `register()`, run `main_loop()`. Started as
  `python -m app.worker`.
- `handlers/email.py` — `handle_email_notification(db, job)` (§7), registered under
  `"email.notification"`.

`Handler = Callable[[Session, Job], None]`. A handler raising ⇒ the job is retried/failed by the
runner; a handler returning ⇒ `succeeded`.

### 6.2 Claim + finalize (at-least-once)
Claim inside one short transaction:
```sql
SELECT * FROM jobs
 WHERE status = 'queued' AND (run_after IS NULL OR run_after <= now())
 ORDER BY created_at
 FOR UPDATE SKIP LOCKED
 LIMIT :batch
```
Set claimed rows to `status='running'`, `attempts = attempts + 1`, commit the claim (so a crash
mid-handler leaves the row `running`, not lost). Then per job, in its own transaction:
- handler succeeds → `status='succeeded'`, `error=NULL`.
- handler raises → if `attempts < WORKER_MAX_ATTEMPTS`: `status='queued'`,
  `run_after = now() + backoff(attempts)`, `error=<str>`; else `status='failed'`, `error=<str>`.

`backoff(attempts)` = `min(BASE * 2**(attempts-1), CAP)` seconds (e.g. BASE=30s, CAP=1h).

**Stale-running reaper:** the claim query also re-queues jobs stuck in `running` past
`WORKER_STALE_SECONDS` (a crashed worker): the runner, before claiming, resets
`status='running' AND updated_at < now() - WORKER_STALE_SECONDS` back to `queued`. This gives
**at-least-once** delivery. Consequence (waiver §11): a job whose email *sent* but whose
finalize did not commit (worker killed in the gap) is re-run and the email is sent twice. Accepted
for a minimal worker; a provider idempotency key is deferred.

Unknown `job.type` (no handler) → `status='failed'`, `error="no handler for <type>"` (never left
spinning).

### 6.3 Deploy
A new `worker` service in `docker-compose.prod.yml` (and the dev compose): the **same image**,
same env/`.env`, `command: ["python", "-m", "app.worker"]`, `depends_on: [db]`, `restart: unless-stopped`.
No new port. Graceful `SIGTERM`: finish the in-flight job, then exit 0.

### 6.4 Config (`app/core/config.py`)
`WORKER_POLL_INTERVAL: float = 2.0`, `WORKER_BATCH_SIZE: int = 10`, `WORKER_MAX_ATTEMPTS: int = 5`,
`WORKER_STALE_SECONDS: int = 300`. Email backend/config already exist.

---

## 7. Email job handler + content

`handle_email_notification(db, job)`:
1. `notification = db.get(Notification, uuid(job.payload["notification_id"]))`; if `None`
   (deleted since) → return (no-op success).
2. `user = db.get(User, notification.user_id)`; if `None`/no email → return (no-op success).
3. Build `EmailMessage(to=user.email, subject=notification.title, html=render(notification))` and
   `get_email_sender().send(msg)`.

Preferences are **not** re-checked at send time (gated at enqueue). A missing `RESEND_API_KEY`
(the sender raises) ⇒ the job fails and retries; the misconfiguration surfaces in `jobs.error`.

**Deep link.** New setting `APP_BASE_URL: str = ""` (empty ⇒ fall back to `SERVER_HOST`). A helper
`deep_link(notification) -> str` maps the notification's `type` (via its category) to an FE path
(`documents → /documents`, `business → /business-builder`, `roadmap_missions → /roadmap`,
`health_assessment → /health`, `team → /team`) appended to `APP_BASE_URL`. `APP_BASE_URL` is the
**frontend origin** and must be set at deploy (folds in the existing `SERVER_HOST`→FE deploy debt).

**Template** (`render`): a small inline-styled HTML string — heading (`title`), paragraph (`body`),
a CTA button (`deep_link`), and a footer line linking to notification preferences. No template
engine; an f-string helper in `app/worker/handlers/email.py` (or `app/services/notifications/email_render.py`).

---

## 8. Errors

- **Prefs `PUT`**: unknown category key or non-bool → `422` (uniform validation envelope).
- **Prefs `GET`/`PUT`**: `403` for a non-member (via `require_workspace`).
- **Worker**: every job runs in its own try/except; one bad job (raise, missing key, unknown type)
  is retried/failed and logged — the loop never dies. `error` carries the last failure string.

---

## 9. Testing

**Unit (real Postgres):**
- **Preferences:** `GET` returns all-defaults when no row exists; `PUT` upserts a subset and
  round-trips; unknown category key → 422; a second workspace's prefs are independent.
- **Handler gating:** a handled event with the recipient's category ON enqueues exactly one
  `email.notification` job carrying that notification's id; **master off** ⇒ no job; **category
  off** ⇒ no job; **in either off case the in-app row is still created** (in-app never gated); an
  uncategorized event enqueues no job.
- **Same-txn:** fire the event in a transaction, roll back → **zero** notification rows **and zero**
  email jobs.
- **Worker runner:** `claim_batch` claims only `queued` + due rows and skips locked ones
  (`FOR UPDATE SKIP LOCKED`); a succeeding handler → `succeeded`; a raising handler under the limit
  → `queued` with `run_after` set and `attempts` incremented; at the limit → `failed` with `error`;
  unknown type → `failed`; a `running` row older than the stale window is re-queued.
- **Email handler:** with `ConsoleEmailSender` (captured), `handle_email_notification` sends one
  message whose `to` = recipient email, `subject` = title, and whose html contains the body and the
  `deep_link`; a deleted notification id → no send, no error.

**Live e2e (`scripts/e2e_run.sh`, `EMAIL_BACKEND=file`):** two members in one workspace; member A
shares a document → member B (Documents category ON by default) → assert an `email.notification`
job row exists; **run one worker drain pass in-process** (`run_once`) → assert B's email file
appears (subject/body via the `mailbox` fixture). Then B `PUT`s `categories.documents=false`; A
shares again → a new in-app row but **no** new email file. Capture the prefs `GET`/`PUT` bodies and
the drained email verbatim.

**FE integration guide** — extend `docs/fe-integration-guide-notifications.md`: the prefs `GET`/`PUT`
shapes (verbatim captures), the **category catalog + defaults**, the `422` on an unknown category,
and an explicit note that **email is asynchronous and best-effort** (delivered by the worker, not
inline) while the **in-app feed is immediate** — and that the email CTA base is the app origin
(`APP_BASE_URL`).

---

## 10. File structure

| File | Change |
|---|---|
| `app/db/models/notification_preference.py` | new `NotificationPreference` model |
| `alembic/versions/00NN_notifications_email.py` | new: `notification_preferences` table + `jobs.attempts`/`jobs.run_after` |
| `app/services/notifications/categories.py` | new: category keys, event→category map, defaults |
| `app/services/notifications/preferences.py` | new: effective/set/`email_enabled` |
| `app/services/notifications/registry.py` | change `_handle` to enqueue email jobs (gated) |
| `app/api/v1/endpoints/notifications.py` | add `GET`/`PUT /notifications/preferences` |
| `app/schemas/notification.py` | add the prefs `PUT` request model |
| `app/worker/__main__.py`, `app/worker/runner.py`, `app/worker/handlers/email.py` | new worker |
| `app/db/models/job.py` | add `attempts`, `run_after` mapped columns |
| `app/core/config.py` | `WORKER_*` + `APP_BASE_URL` settings |
| `docker-compose.prod.yml`, `docker-compose.yml` | new `worker` service |
| `tests/…/notifications/`, `tests/worker/`, `e2e/` | unit + e2e |
| `docs/fe-integration-guide-notifications.md`, `docs/sop/…`, `docs/checklist/PROJECT_CHECKLIST.md` | docs |

---

## 11. Decisions & waivers

- **D1 — Email jobs enqueued in the action's transaction** (commit iff action commits ⇒ no phantom
  email), drained by a worker. Chosen over inline send and over defer-to-Slice-3. (User, 2026-09-15.)
- **D2 — Preferences = category toggles + master switch**, per-`(user, workspace)`, JSONB-backed;
  **in-app never gated**, email is the toggleable channel; **enqueue-time** gating.
- **D3 — 5 categories** (§3.3), **all default ON** (opt-out); uncategorized events are in-app only.
- **D4 — A minimal, production-shaped worker**: separate container, `FOR UPDATE SKIP LOCKED`,
  `JOB_HANDLERS` registry, bounded retries + exponential backoff, stale-running reaper.
- **D5 — At-least-once delivery.** Waiver: a worker killed between a successful send and its
  finalize re-runs the job and sends a duplicate email. Accepted for a minimal worker; provider
  idempotency keys deferred.
- **D6 — `APP_BASE_URL`** is the FE origin for email deep links (defaults to `SERVER_HOST`); set at
  deploy (folds in the existing `SERVER_HOST`→FE debt).
- **D7 — Migration** adds `notification_preferences` + the two `jobs` columns in one revision after
  `0021_notifications`; coordinate the number with Module 17 (PR #59) for a single head.
- **D8 — In-app delivery, feed, and the Slice 1 event set are unchanged**; this slice adds only the
  email channel and the worker that carries it.
