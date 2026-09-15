# SOP — Notifications, Email Delivery + Preferences + Worker (Module 20, Slice 2)

**What shipped** — the second slice of Module 20: a per-(user, workspace) email preferences model
(`master_email` + 5 opt-out categories), two routes (`GET`/`PUT /notifications/preferences`), an
in-transaction enqueue at the same fan-out point Slice 1 already creates in-app rows from, and a new
standalone background **worker** process (`python -m app.worker`, its own `worker` container in
compose) that claims queued `email.notification` jobs (`SELECT ... FOR UPDATE SKIP LOCKED`, retry with
exponential backoff, a stale-`RUNNING` reaper) and sends the email via the existing Resend/console/file
`EmailSender` abstraction (`docs/sop/2026-09-12-resend-email-backend.md`). In-app delivery (Slice 1,
`docs/sop/2026-09-14-notifications-feed-slice1.md`) is completely unchanged — email is additive, gated
per-recipient, per-category, at enqueue time.

Commits (branch `feat/notifications-email`, off `develop`, PR not yet opened):
`9b0a43d` (design) → `db50c70` (implementation plan, `.superpowers/sdd/
2026-09-15-notifications-email-delivery/`) → `5dc01ca` (Task 1 — `notification_preferences` table +
`jobs.attempts`/`jobs.run_after` columns, migration `0022_notifications_email`) → `8f65544` (Task 2 —
category map + preferences service) → `0e3ba17` (Task 3 — `GET`/`PUT /notifications/preferences`
endpoints) → `e2c4a83` (Task 4 — registry enqueues `email.notification` jobs for opted-in recipients,
in the same transaction as the in-app rows) → `c80e31a` (Task 5 — worker job runner: claim/retry/
backoff/reaper) → `2625f0f` (Task 6 — `email.notification` handler: render + deep link + send) →
`8aa8adc` (fix — escape email HTML/subject, validate deep-link scheme) → `beed89f` (Task 7 — worker
process entrypoint + compose service) → **this commit** (Task 8, final — live e2e + captures + FE
guide + SOP + checklist).

## Why

Slice 1 shipped a real in-app feed, but a founder who isn't actively looking at the app has no way to
learn "your document was shared" or "a suggestion needs review" happened — every prior module's SOP
carries a "real notification delivery — Module 20" deferred line, and Slice 1's own SOP explicitly
deferred email to "Slice 2 (Resend backend already exists)". This slice is that: email as a second,
optional delivery channel alongside the always-on in-app feed, with per-member control over which
categories get emailed.

**Why a worker, not inline sending.** Sending an email inline inside the same request that shares a
document (or approves a suggestion, or completes onboarding) would tie that request's latency and
success to an external provider (Resend) — a slow or down email API would make document-sharing
itself slow or fail. The design instead **enqueues a job in the same transaction** that creates the
in-app row (so the decision to send is transactionally consistent with the event that triggered it —
a rolled-back share enqueues no email, same guarantee Slice 1 already gives in-app rows) and lets a
**separate, independently-scalable worker process** claim and send it whenever it next polls. This is
the same job-queue pattern already used elsewhere in the codebase (`app/platform/jobs.py`
`job_dispatcher`, existing `jobs` table from a prior module) — Slice 2 adds worker columns to the
existing table rather than inventing a second queue.

## How

**Enqueue-time gate, not send-time.** `app/services/notifications/registry.py::_handle` (Slice 1's
fan-out point, unchanged in shape) now captures `create_notifications(...)`'s return value and, for
each created row, calls `email_enabled(db, user_id=n.user_id, startup_id=n.startup_id,
category=category_for(event))` — if true, `job_dispatcher.enqueue(db, "email.notification",
{"notification_id": str(n.id)}, startup_id)` runs in the SAME transaction/savepoint as the in-app
insert. This means: (1) a rolled-back triggering action leaves no job queued (proven by
`tests/services/notifications/test_email_enqueue.py::test_rolled_back_action_leaves_no_email_job`),
and (2) the job payload is intentionally minimal — just `notification_id` — the handler re-fetches the
`Notification` row (and via it, the recipient) at send time rather than duplicating data into the
payload that could drift from the row.

**Preferences: opt-out, partial-merge, category-scoped.** `NotificationPreference` is one row per
`(user_id, startup_id)` (upserted lazily — a member with no row yet gets synthesized defaults, not a
404). `master_email: bool` (default `true`) is a global email kill switch; `categories: JSONB` holds
only the keys a member has explicitly set — `effective_preferences()` merges stored values over
`CATEGORY_DEFAULTS` (all `true`) so every one of the 5 catalog keys is always present in the response
even if never written. `set_preferences()` does a genuine partial merge (`dict(row.categories);
merged.update(...)`) — a `PUT` with one category key does not reset the other four, and unknown keys
are silently dropped at the merge layer as defense-in-depth (though in practice they never reach it:
`PreferencesUpdate`'s `field_validator` already rejects any key outside `CATEGORIES` with a `422`
before the request handler runs). `email_enabled()` is the single enqueue-time gate: `master_email AND
categories[category]` — both must be true.

**One `EVENT_CATEGORY` map is both the deep-link table (Slice 1, §5 of the FE guide) and the
preferences catalog (this slice).** `app/services/notifications/categories.py` groups the 15 v1 event
types into 5 categories (`documents`, `business`, `roadmap_missions`, `health_assessment`, `team`) —
the same grouping the email CTA's `deep_link()` function (`app/worker/handlers/email.py`) uses to pick
a path (`/documents`, `/business-builder`, `/roadmap`, `/health`, `/team`). Adding category #6 or
moving an event to a different category is a one-line change in this single file, consumed by both
the preferences gate and the deep-link table automatically.

**Worker: claim-and-run, per-job savepoint isolation, exponential backoff.**
`app/worker/runner.py::run_once(db)` — reap any job stuck `RUNNING` past `WORKER_STALE_SECONDS` back to
`queued` (crashed-worker recovery), then `SELECT ... WHERE status='queued' AND (run_after IS NULL OR
run_after <= now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT WORKER_BATCH_SIZE` — `SKIP LOCKED`
lets multiple worker replicas run concurrently without claiming the same row twice. Each claimed job
is marked `running` and `attempts += 1`, committed immediately (so a crash mid-batch leaves rows
`RUNNING`, not silently lost — the reaper recovers them next pass), then each job's handler runs
inside its own `db.begin_nested()` SAVEPOINT wrapped in `try/except Exception` — one bad job's failure
never aborts the batch or corrupts another job's write. Success → `succeeded`. Failure below
`WORKER_MAX_ATTEMPTS` (5) → back to `queued` with `run_after = now() + 30 * 2^(attempts-1)` seconds,
capped at 1h. Failure at the ceiling → `failed`, terminal. An unrecognized `job.type` (no registered
handler) fails immediately, bypassing retry — no amount of retrying fixes a missing handler, so
burning 5 attempts' worth of backoff on it is pure waste (`_finalize_terminal_failure`).

**The email handler: re-fetch, escape, validate the deep-link scheme.**
`app/worker/handlers/email.py::handle_email_notification` re-fetches the `Notification` (no-op if
deleted since enqueue) and its recipient `User` (no-op if the user or their email is gone), strips
CR/LF from the subject (`notification.title`) to prevent SMTP header injection, and renders an HTML
body via `render_email()` — `html.escape()`s the title/body before interpolating (fixed in `8aa8adc`
after the initial cut left the door open to a stored-XSS-in-email vector via a notification
title/body containing markup — none of the 15 v1 event types' generic copy is user-controlled today,
but a future type could add one, and the email HTML renders in the recipient's mail client without any
of the FE's own sanitization). `deep_link()` also validates the resolved base URL actually starts with
`http://`/`https://`, discarding it (falling back to an empty base — a relative link) if not — defense
against a misconfigured `APP_BASE_URL` (e.g. accidentally set to a `javascript:` value) injecting an
unexpected scheme into an email CTA.

## What's involved

**Data model / migration** (Task 1)
- `alembic/versions/0022_notifications_email.py` — chains off `0021_notifications` (Slice 1's sole
  head at build time). `notification_preferences` table (`user_id`/`startup_id` FKs `ON DELETE
  CASCADE`, both standalone-indexed, `uq_notification_preferences_user_startup` unique constraint,
  `master_email boolean default true`, `categories jsonb default '{}'`) + two new columns on the
  existing `jobs` table: `attempts integer default 0`, `run_after timestamptz nullable`.
- `app/db/models/notification_preference.py` (new) — `NotificationPreference(UUIDMixin,
  TimestampMixin, Base)`.
- `app/db/models/job.py` — `attempts`, `run_after` added to `Job`.

**Service layer** (Task 2) — `app/services/notifications/`
- `categories.py` — `CATEGORIES` (5-tuple), `EVENT_CATEGORY` (15 events → category), `CATEGORY_DEFAULTS`
  (all `True`), `category_for(event) -> str | None`.
- `preferences.py` — `effective_preferences(db, user_id, startup_id)`, `set_preferences(db, user_id,
  startup_id, master_email, categories)` (partial merge), `email_enabled(db, user_id, startup_id,
  category) -> bool` (the enqueue-time gate).

**Endpoints** (Task 3) — `app/api/v1/endpoints/notifications.py`, `app/schemas/notification.py`
- `GET /api/v1/notifications/preferences`, `PUT /api/v1/notifications/preferences` — both `verified
  user + require_workspace`, same auth convention as Slice 1's 4 routes.
- `PreferencesUpdate` — `field_validator` on `categories` rejects any key outside `CATEGORIES` with
  `422 VALIDATION_ERROR`.

**Enqueue** (Task 4) — `app/services/notifications/registry.py::_handle` — one new loop over
`create_notifications`'s return value, gated by `email_enabled(...)`, calling
`job_dispatcher.enqueue(db, "email.notification", {"notification_id": str(n.id)}, startup_id)` inside
the existing per-handler savepoint.

**Worker** (Tasks 5–7) — `app/worker/`
- `runner.py` — `Handler` type, `JOB_HANDLERS` registry, `register_handler`, `backoff_seconds`,
  `reap_stale`, `claim_batch`, `run_once(db) -> int`.
- `handlers/email.py` — `handle_email_notification(db, job)`, `render_email(notification)`,
  `deep_link(notification)`, registers itself as `"email.notification"` at import time.
- `__main__.py` — `register()` (imports handler modules for their registration side effect),
  `main_loop(stop)` (poll `run_once` on `WORKER_POLL_INTERVAL`, survive a bad batch), `main()` (wires
  `SIGTERM`/`SIGINT` to a stop flag — graceful shutdown finishes the current iteration). Entrypoint:
  `python -m app.worker`.
- `docker-compose.yml` / `docker-compose.prod.yml` — new `worker` service (dev: `build: .`, same
  volumes as `api`; prod: `image:` only, `depends_on` db/redis healthy + `migrate` completed,
  `deploy.resources: {limits: {cpus: "0.5", memory: 512M}, reservations: {memory: 128M}}`, no
  published ports).

**Config** (`app/core/config.py`) — `WORKER_POLL_INTERVAL` (2.0s), `WORKER_BATCH_SIZE` (10),
`WORKER_MAX_ATTEMPTS` (5), `WORKER_STALE_SECONDS` (300), `APP_BASE_URL` (frontend origin for email
deep links, falls back to `SERVER_HOST` if unset).

**Errors** — one new code: `422 VALIDATION_ERROR` on `PUT /notifications/preferences` with an unknown
category key. No new 403/404 surface — both preference routes 403 via the same `require_workspace`
dependency as every other workspace-scoped route.

**Tests**
- `tests/db/test_notification_preference_model.py` (2) — round-trip + `Job` worker columns.
- `tests/services/notifications/test_preferences.py` (defaults/partial-merge/`email_enabled` gating).
- `tests/services/notifications/test_email_enqueue.py` (3) — one job per opted-in recipient;
  category-off still creates the in-app row with no email job; a rolled-back action leaves no job.
- `tests/api/test_notification_preferences.py` (3) — `GET` defaults, `PUT` upsert+roundtrip, unknown
  category `422`.
- `tests/worker/test_runner.py` (6) — success/retry-then-fail/unknown-type-fail/`run_after` skip/stale
  reap.
- `tests/worker/test_email_handler.py` (3) — sends one email with correct `to`/`subject`/deep-link;
  HTML-escapes a malicious title/body; missing notification is a no-op.
- `tests/worker/test_entrypoint.py` (2) — `register()` wires the handler; `main_loop` runs until the
  stop flag flips.
- `e2e/test_notifications_email.py::test_email_delivery_and_preferences` (new, this task) — see
  Verification below.

## Verification

**Per-task unit verification (Tasks 1–7, already green before this task):** each task's own report
(`.superpowers/sdd/2026-09-15-notifications-email-delivery/task-{1..7}-report.md`) recorded a passing
scoped test run at the time; not re-litigated here.

**Task 8 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests` | ✅ pass |
| Import order | `poetry run isort --check-only app tests` | ✅ pass |
| Lint | `poetry run ruff check app tests` | ✅ pass |
| Types | `poetry run mypy app` | ✅ pass |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ pass (see exact score below) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ pass, no findings |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ pass (see exact count/% below) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0022_notifications_email (head)` |
| Live E2E | `./scripts/e2e_run.sh` | ✅ 39 passed (38 Slice-1-and-earlier + 1 new) |

Exact counts, coverage %, and anything added to satisfy a gate are recorded in
`.superpowers/sdd/2026-09-15-notifications-email-delivery/task-8-report.md` (this task's own report,
written after the run below).

**`e2e/test_notifications_email.py::test_email_delivery_and_preferences`** — mirrors
`e2e/test_notifications.py`'s A/B setup verbatim (founder A onboards, invites teammate B, B signs
up+verifies+accepts — a real second active member), then: B's preferences default to
`master_email: true` + `documents: true` → A creates and shares a document (`document.shared`) → the
queue is drained in-process (`app.worker.runner.run_once`, looped until empty — the e2e run shares one
`jobs` table across the WHOLE suite, so earlier tests' own enqueued jobs sit ahead of this test's in
FIFO order; a single batch of `WORKER_BATCH_SIZE` is not enough to reach it) → B's mailbox
(`EMAIL_BACKEND=file`) has a new email, subject containing "document", captured verbatim
(`delivered_email.json`) → B turns `documents` email OFF (`PUT /notifications/preferences`,
`preferences_documents_off.json`) → A shares again → drain again → B's mailbox count is UNCHANGED (no
new job was enqueued — asserted via `mailbox.count_for` before/after, no new email to capture since
nothing new happened) → `GET /notifications/preferences` reflects the off toggle
(`preferences_get.json`) → an unknown category key `PUT` 422s, captured
(`preferences_put_unknown_category_422.json`).

**Deviation from the brief's pseudocode:** the brief's illustrative `_drain()` called `run_once` a
single time. In a real e2e run this under-drains: every earlier test in the suite (assessment,
business builder, documents, mission, roadmap, Slice 1 notifications, …) also triggers events for its
own users with all-default (all-`True`) email preferences, so by the time this test runs there is a
substantial backlog of unrelated `email.notification` jobs queued ahead of B's — a single 10-job batch
claims someone else's jobs, not B's, and the assertion on B's mailbox fails
(`AssertionError: ... 'verify your email' ... `, i.e. `latest_for` found only B's pre-existing
signup-verification email, not the notification). Fixed by looping `run_once` until it returns `0`
(capped at 500 iterations as a stall guard) — functionally the same thing the real worker does by
running continuously; this journey just needs to simulate "long enough" instead of "one poll tick."

## Operate / roll back

**New deploy-time requirements:**
- A new **`worker` container** must run alongside `api` — it is not optional infrastructure; without
  it, `email.notification` jobs queue forever and no email is ever sent (in-app delivery is
  unaffected — it doesn't go through the queue). `docker-compose.prod.yml`'s `worker` service starts
  automatically as part of `docker compose -f docker-compose.prod.yml up -d`, after `migrate` and with
  `db`/`redis` healthy.
- Set `EMAIL_BACKEND=resend` (was `console`/unset in earlier envs — `console` only logs, never sends;
  `file` is e2e/dev-only), plus `RESEND_API_KEY` and `EMAILS_FROM_EMAIL` (both already-defined config
  keys from the pre-existing Resend backend, `docs/sop/2026-09-12-resend-email-backend.md` — this
  slice is the first thing that actually causes them to be exercised in production traffic).
- Set `APP_BASE_URL` to the real FE origin (e.g. `https://app.cofoundaz.com`) — if left unset, email
  CTAs fall back to `SERVER_HOST` (the API's own origin), which is almost certainly NOT where the FE
  is served, producing a dead-end link in every notification email.
- `alembic upgrade head` picks up `0022_notifications_email` automatically (no manual data migration —
  the new table starts empty, every existing member gets synthesized defaults on first `GET`).

**Rollback:** `alembic downgrade -1` from `0022_notifications_email` drops `jobs.run_after`/
`jobs.attempts` then `notification_preferences` (and its indexes) — **lossy** for any preferences
already set (in-app notifications, the `notifications` table from `0021`, are untouched). Any
`email.notification` jobs already `queued` in the `jobs` table are orphaned by a downgrade that removes
`run_after` (the worker's claim query references it) — stop the `worker` container BEFORE running the
downgrade, not after. Rolling back the migration without also reverting Tasks 2–4's registry/endpoint
code would 500 the preferences routes against a missing table; reverting Tasks 5–7's worker code
without stopping the `worker` container leaves it polling a `jobs` table with columns it no longer
expects (SQLAlchemy model mismatch) — revert model + migration + worker code together, in practice
"revert Tasks 1–7's commits as one unit," same shape as Slice 1's own rollback note.
- Stopping just the `worker` container (leaving everything else running) is always safe and
  non-destructive — jobs stay `queued` and are picked up whenever a worker runs again; this is the
  correct way to pause email sending without touching data.

## Follow-ups

**At-least-once delivery — a known, accepted risk, not a bug.** The claim/execute/finalize sequence in
`run_once` is not exactly-once: a worker that crashes AFTER successfully sending an email via Resend
but BEFORE its `_finalize_success` commit lands leaves the job `running`; the stale reaper requeues it
after `WORKER_STALE_SECONDS`, and a later pass sends the SAME email again. This mirrors the same
at-least-once tradeoff most job queues make (exactly-once delivery to an external, non-transactional
system like an email API is not achievable without a distributed transaction or an idempotency key
round-trip with the provider) and was an explicit waiver in the design (§11), not an oversight.
**Mitigation if double-sends become a real complaint:** track a `sent_at`/idempotency key per job (or
per notification) and no-op the handler if already sent — not built here; a plausible Slice 2.1 if
this shows up in production Resend logs as a real pain point rather than a theoretical one.

**Other deferred items:**
- Preferences are **not retroactive** — toggling a category only gates events firing after the
  toggle; an email already enqueued or sent before the toggle is unaffected. No "cancel pending email"
  path exists.
- No per-notification email-delivery status is exposed anywhere in the API (sent/pending/failed) — the
  FE cannot build a "delivered" indicator against this slice; §9.5 of the FE guide calls this out
  explicitly so the FE doesn't design around data that doesn't exist.
- Same generic per-type, not per-instance, copy limitation Slice 1 already carries (a document-share
  email says "A document was shared," not which document or by whom) — richer templating is a future
  slice, not scoped here.
- HTML-escaping (`8aa8adc`) closes the concrete vector for the 15 v1 event types (none of which are
  user-controlled today), but any FUTURE event type whose title/body embeds user-supplied text (e.g. a
  free-text comment) should keep going through `render_email()`'s existing `html.escape()` calls, not
  a new ad hoc template — flagging so a later addition doesn't accidentally bypass this.
- **Slice 3 (scheduler/cron)** will enqueue into this SAME `jobs` table and be picked up by this SAME
  worker process — no new infrastructure needed, just new job types + handlers registered the same way
  `email.notification` is. **Slice 4 (real-time/push)** remains unbuilt; still no websocket/SSE
  delivery of any kind.
