# Module 20 Notifications — Slice 3 Design: Scheduler / Cron (time-based triggers)

**Date:** 2026-09-18
**Module:** 20 Notifications (Slice 3 of ~4)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)
**Depends on:** Slice 2 (the `jobs` table + the minimal worker `app/worker`, the notifications
registry + per-category email preferences). Reuses the mission service `get_or_generate_today`.

---

## 1. Context & Scope

Slices 1–2 made notifications reactive — a *user action* publishes an event that fans out to in-app
rows and (opt-in) email. Slice 3 adds the **time-based** half: a scheduler that fires periodic work
on a cadence, with no user in the loop. It retires the "06:00 cron / overdue / quarterly re-assess"
items that earlier modules deferred to Module 20.

**Module 20 slices:** 1 In-app feed (done), 2 Email + preferences + worker (done), **3
Scheduler/cron (this)**, 4 Real-time (websocket) + push.

**v1 delivers three scheduled triggers** (all agreed 2026-09-18):
1. **Daily mission generation** — pre-generate today's mission per active workspace at ~06:00
   local, so it's ready before the founder opens the app, and notify.
2. **Roadmap milestone overdue** — when a milestone passes its due date undone, nudge once.
3. **Quarterly re-assessment** — when a workspace's last completed assessment is ≥ 90 days old,
   remind them to re-assess.

### Decided approach (2026-09-18)
- **Scheduler = a throttled tick inside the existing worker loop** (no new container). The worker
  already runs exactly one process; a DB **claim ledger** (unique constraint) makes firing
  **once-per-period safe even under multiple workers**, leader-free.
- **The tick claims + enqueues jobs; the worker's handlers do the work and publish the event.** The
  ledger prevents duplicate enqueue; the job gives retry + per-scope isolation for free.
- **One config timezone** (`SCHEDULER_TIMEZONE`) for the 06:00 check — per-workspace timezone is
  deferred (no tz field exists on `Startup` today).

### Non-goals / deferred
Per-workspace timezone; daily/weekly re-nudge for overdue items (v1 fires **once** per item);
overdue for roadmap **tasks** (v1 does milestones only); digest/grouping; a separate scheduler
container; mission-streak reminders; websocket/push (Slice 4).

---

## 2. Architecture & data flow

```
worker main_loop (every WORKER_POLL_INTERVAL ≈ 2s):
  run_once(db)                       # Slice-2: drain queued jobs
  if now - last_scheduler_tick >= SCHEDULER_INTERVAL:   # throttle (≈60s)
      scheduler_tick(db, now=now)
      last_scheduler_tick = now

scheduler_tick(db, now):
  for (task_key, scope_key, period_key, payload) in due(db, now):   # per-task detection
      if _claim(db, task_key, scope_key, period_key):               # savepoint INSERT; IntegrityError→False
          job_dispatcher.enqueue(db, <job_type>, payload, startup_id)
  db.commit()

worker run_once → handler(db, job):        # a later poll iteration
  do the work + event_bus.publish(db, <event>, payload)   # registry fans out in-app + email
```

**Claim = the whole idempotency story.** `_claim` inserts a `scheduled_runs` row inside a
`db.begin_nested()`; the unique `(task_key, scope_key, period_key)` constraint means the first
worker to insert wins and every other caller (this tick or a concurrent worker) gets `IntegrityError`
→ `False` → skip. No leader election needed.

**Throttle** is efficiency only (don't scan workspaces every 2s); correctness rests on the claim.

---

## 3. Data model — `scheduled_runs` (new table + migration)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |
| `task_key` | `String(60)`, NOT NULL | e.g. `mission.generate`, `roadmap.overdue`, `assessment.quarterly` |
| `scope_key` | `String(64)`, NOT NULL | the workspace id (or the milestone id for overdue) as text |
| `period_key` | `String(32)`, NOT NULL | `YYYY-MM-DD` (daily), `YYYY-Qn` (quarterly), `once` (per-item) |

Constraint: `UniqueConstraint(task_key, scope_key, period_key,
name="uq_scheduled_runs_task_scope_period")`. No FK — `scope_key` is a generic string (workspace or
item id), and the ledger is an append-only audit of "this fired". Migration chained onto the current
develop head at build time (settle the number against the live head).

---

## 4. The scheduler tick (`app/worker/scheduler.py`)

- `scheduler_tick(db: Session, *, now: datetime) -> int` — runs all three detectors, claims +
  enqueues each due item, returns the count enqueued. Each `(task, scope)` is isolated in try/except
  so one failure does not stop the rest.
- `_claim(db, task_key: str, scope_key: str, period_key: str) -> bool` — `begin_nested()` INSERT;
  `IntegrityError` → `False`.
- **Detectors** (each takes `db, now` and yields `(scope_key, period_key, startup_id, payload)`):
  - `_due_missions(db, now)` — only when it is **≥ `MISSION_GEN_HOUR` in `SCHEDULER_TIMEZONE`**
    "today"; for every **active** workspace (a `Startup` with ≥1 active `Membership`), yield
    `(startup_id, today_local_isodate, startup_id, {"startup_id": …})`. Period = the local date.
  - `_due_overdue_milestones(db, now)` — milestones with `due_on < today_local AND status !=
    done`; yield `(milestone_id, "once", startup_id, {"startup_id":…, "milestone_id":…})`. Period
    `once` ⇒ fires exactly once per milestone, ever.
  - `_due_quarterly(db, now)` — active workspaces whose **latest** assessment `completed_at` is
    `≤ now - QUARTERLY_REASSESS_DAYS` AND which have **no** `in_progress` assessment; yield
    `(startup_id, "YYYY-Qn", startup_id, {"startup_id":…})`. Period = the calendar quarter.
    (Workspaces that never completed an assessment are **not** quarterly-due — that is the initial
    assessment's job, not this.)

Enqueue on a successful claim: `scheduled.mission.generate` / `scheduled.roadmap.overdue` /
`scheduled.assessment.quarterly`.

---

## 5. Job handlers (`app/worker/handlers/scheduled.py`)

Registered via `register_handler(...)`; each does the work then publishes its event. Handlers must
**not** call `db.commit()`/`rollback()` (they run inside the worker's per-job savepoint).

- `handle_mission_generate(db, job)` — load the `Startup`; `mission = get_or_generate_today(db,
  startup)`; if `mission is not None`, `event_bus.publish(db, "mission.ready", {"startup_id":…,
  "mission_id": str(mission.id)})`. (A `None` — off-day / no roadmap — is a no-op success.)
- `handle_roadmap_overdue(db, job)` — re-check the milestone is still overdue+undone (it may have
  been completed between enqueue and run); if so `event_bus.publish(db,
  "roadmap.milestone.overdue", {"startup_id":…, "milestone_id":…})`.
- `handle_assessment_quarterly(db, job)` — `event_bus.publish(db, "assessment.quarterly.due",
  {"startup_id":…})`.

---

## 6. New events + notification registry (`registry.py`, `categories.py`)

Three new events, each a `NotifSpec` with recipients = **all active members** (system-generated, no
actor exclusion — like the other passive events):

| Event | Title | Category (for email gating) |
|---|---|---|
| `mission.ready` | "Today's mission is ready" | `roadmap_missions` |
| `roadmap.milestone.overdue` | "A roadmap milestone is overdue" | `roadmap_missions` |
| `assessment.quarterly.due` | "Time for your quarterly startup assessment" | `health_assessment` |

Add the three rows to `SPECS` (recipients `_active_member_ids(...)`, no exclude), and the three
`EVENT_CATEGORY` mappings in `categories.py` so Slice-2 email preferences gate them (without a
mapping, `category_for` → `None` → in-app only).

---

## 7. Config (`app/core/config.py`)

`SCHEDULER_TIMEZONE: str = "UTC"` (IANA name) · `MISSION_GEN_HOUR: int = 6` · `SCHEDULER_INTERVAL:
int = 60` (throttle seconds) · `QUARTERLY_REASSESS_DAYS: int = 90`. All read at tick time.

`app/worker/__main__.py`: `main_loop` gains a `_last_scheduler_tick` gate calling
`scheduler_tick(db, now=datetime.now(UTC))` at most once per `SCHEDULER_INTERVAL`; `register()`
also imports `app.worker.handlers.scheduled`.

---

## 8. Errors

- A `scheduler_tick` exception is caught by `main_loop`'s existing try/except (loop survives).
- Per-`(task, scope)` claim+enqueue is wrapped — one workspace/item failing is logged and the rest
  proceed.
- A lost claim (`IntegrityError`) is a normal **skip**, not an error.
- Job handlers ride the worker's existing per-job savepoint + bounded-retry + stale-reaper.

---

## 9. Testing

**Unit (real Postgres, injected `now`):**
- `_claim`: first call `True` + a row; second call same `(task,scope,period)` → `False`, still one
  row; different period → `True`.
- `_due_missions`: nothing before `MISSION_GEN_HOUR` local; after the hour, yields each active
  workspace once; a second tick same day claims nothing (idempotent).
- `_due_overdue_milestones`: a milestone `due_on` in the past + not done is yielded once; a done or
  future one is not; a second tick does not re-yield (period `once`).
- `_due_quarterly`: a workspace whose latest `completed_at` is 91 days ago is due; 89 days is not; a
  workspace with an in-progress assessment is not; one that never completed is not.
- Handlers: `handle_mission_generate` calls `get_or_generate_today` and publishes `mission.ready`
  only when a mission results; overdue re-checks staleness before publishing; quarterly publishes.
- Registry: each new event creates in-app rows for active members; email gated by category.
- Throttle: `main_loop` does not call `scheduler_tick` twice within `SCHEDULER_INTERVAL` (drive with
  a fake clock).

**Live e2e (`scripts/e2e_run.sh`, in-process drive — same pattern as the Slice-2 email e2e):** set
up a workspace with a roadmap; `scheduler_tick(db, now=<today 06:05 in SCHEDULER_TIMEZONE>)` then
drain the worker → assert today's mission exists **and** a `mission.ready` notification is in the
member's feed. Backdate a completed assessment 100 days → tick → `assessment.quarterly.due`
notification. Add an overdue milestone → tick → `roadmap.milestone.overdue` notification. Capture
the feed bodies.

**FE integration guide** — extend `docs/fe-integration-guide-notifications.md`: the three new
notification `type`s, their `data` deep-link fields (`mission_id`, `milestone_id`), and that they
arrive with no user action (scheduled), in-app always + email per the existing category prefs
(`roadmap_missions`, `health_assessment`). SOP + checklist updated.

---

## 10. File structure

| File | Change |
|---|---|
| `app/db/models/scheduled_run.py` | new `ScheduledRun` ledger model |
| `alembic/versions/00NN_scheduled_runs.py` | new migration (number settled at build) |
| `app/worker/scheduler.py` | new: `scheduler_tick`, `_claim`, three detectors |
| `app/worker/handlers/scheduled.py` | new: three job handlers + `register_handler` |
| `app/worker/__main__.py` | throttled `scheduler_tick` call in `main_loop`; import scheduled handlers in `register()` |
| `app/services/notifications/registry.py` | three new `SPECS` rows (active members, no actor) |
| `app/services/notifications/categories.py` | three new `EVENT_CATEGORY` mappings |
| `app/core/config.py` | `SCHEDULER_TIMEZONE`, `MISSION_GEN_HOUR`, `SCHEDULER_INTERVAL`, `QUARTERLY_REASSESS_DAYS` |
| `tests/worker/test_scheduler.py`, `tests/worker/test_scheduled_handlers.py`, `e2e/test_notifications_scheduler.py` | tests |
| `docs/fe-integration-guide-notifications.md`, `docs/sop/…`, `docs/checklist/PROJECT_CHECKLIST.md` | docs |

---

## 11. Decisions & waivers

- **D1 — Scheduler is a throttled tick in the worker**, claim-ledger for once-per-period safety
  (leader-free); no new container. (User, 2026-09-18.)
- **D2 — Tick claims + enqueues jobs; handlers do the work + publish.** Reuses the Slice-2 worker's
  retry/isolation; the ledger prevents duplicate enqueue.
- **D3 — One config timezone** (`SCHEDULER_TIMEZONE`) for the 06:00 check; per-workspace tz deferred.
- **D4 — v1 = three triggers** (mission generation, milestone overdue, quarterly re-assessment).
- **D5 — Overdue fires once per milestone** (period `once`); daily/weekly re-nudge and roadmap
  **task** overdue are deferred.
- **D6 — Quarterly applies only to workspaces with a prior completed assessment**; never-assessed
  workspaces are the initial assessment's job, not this.
- **D7 — Three new events** (`mission.ready`, `roadmap.milestone.overdue`, `assessment.quarterly.due`)
  with registry specs + category mappings, so in-app + Slice-2 email gating both work.
- **D8 — Migration number + the exact IANA default tz** settled at build time.
