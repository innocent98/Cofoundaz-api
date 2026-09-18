# SOP — Notifications, Scheduler / Cron (Module 20, Slice 3)

**What shipped** — the third slice of Module 20: a **time-based** trigger half for notifications,
alongside Slices 1–2's user-action-triggered half. A throttled tick inside the existing `worker`
process (no new container) detects three kinds of due work — daily mission generation past 06:00
workspace-tz, a roadmap milestone past its due date and still not done, a workspace whose last
completed assessment is ≥ 90 days old — claims each exactly once via a new `scheduled_runs` ledger,
and enqueues a job into the SAME `jobs` table Slice 2's worker already drains. Three job handlers do
the actual work and publish three new events (`mission.ready`, `roadmap.milestone.overdue`,
`assessment.quarterly.due`), which the Slice-1 registry turns into in-app notifications for every
active member, gated for email by the existing `roadmap_missions`/`health_assessment` categories
(Slice 2) — no new notification infrastructure, no new email infrastructure, no new API surface.

Commits (branch `feat/notifications-scheduler`, off `develop`, PR not yet opened):
`722ff0b` (design) → `2269698` (implementation plan, `.superpowers/sdd/
2026-09-18-notifications-scheduler/`) → `e25921b` (Task 1 — `scheduled_runs` ledger + once-per-period
`_claim`) → `fe7337a` (Task 2 — `SCHEDULER_*` config + the three due-detectors) → `bc0e169` (fix —
exclude soft-deleted startups from the detectors) → `db2ee71` (Task 3 — `scheduler_tick` claims +
enqueues due jobs) → `e6f31b8` (Task 4 — the three job handlers) → `1efdb65` (test — cover overdue
re-check suppression, done + missing milestone) → `bc35208` (Task 5 — three new registry `SPECS` rows
+ category mappings) → `c2f6411` (Task 6 — run the scheduler tick, throttled, inside the worker loop)
→ `f3eec55` (fix — scheduled-handlers registration assert under full-suite runs) → **this commit**
(Task 7, final — live e2e + captures + FE guide + SOP + checklist).

## Why

Nearly every already-shipped module (Dashboard, Roadmap, Mission, Health Score, Assessment,
onboarding) carries a "real notification delivery — Module 20" deferred line, and Slices 1–2 only
covered the reactive half: a notification exists because a *user did something*. Three concrete gaps
had no trigger at all until this slice: a founder who never opens the app has no way to know today's
mission is ready without checking; a roadmap milestone can silently slip past its due date with no
nudge; and Module 07's "quarterly re-assessment" concept (referenced in that module's own SOP as a
Module-20 deferral) had no cron to actually fire it. This slice retires all three deferred lines at
once, using the exact worker/job infrastructure Slice 2 already built rather than standing up a
second piece of infrastructure for "time-based" work.

## How

**Scheduler = a throttled tick inside the existing worker loop, not a new process.** `app/worker/
__main__.py::main_loop` already polls `run_once(db)` every `WORKER_POLL_INTERVAL` (2s default); this
slice adds one more check per iteration — `time.monotonic() - last_tick >= SCHEDULER_INTERVAL`
(default 60s) — and, when due, calls `scheduler_tick(db, now=datetime.now(UTC))` in the SAME
iteration, its own DB session/commit boundary. This means the worker process (already required
deploy-time infrastructure since Slice 2) is now also the scheduler — no new container, no new
health check, no new failure domain to operate. The monotonic-clock throttle (not wall-clock) is
deliberate: a system clock adjustment (NTP correction, DST) cannot cause a burst of missed or
duplicate ticks the way comparing wall-clock timestamps could.

**Once-per-period safety via a claim ledger, not a leader election.** `ScheduledRun` (migration
`0024_scheduled_runs`) is an append-only table with a unique constraint on
`(task_key, scope_key, period_key)` — e.g. `("mission.generate", "<startup_id>", "2026-09-18")` for a
daily mission, `("roadmap.overdue", "<milestone_id>", "once")` for a one-shot overdue nudge, or
`("assessment.quarterly", "<startup_id>", "2026-Q3")` for quarterly. `app/worker/scheduler.py::_claim`
inserts inside a `db.begin_nested()` SAVEPOINT; a concurrent/repeat insert raises `IntegrityError`,
caught and treated as "already claimed, skip" — this makes firing exactly-once-per-period safe even
if multiple worker replicas ran concurrently (none exist in this deploy today, but the design doesn't
assume a single worker), with no leader-election protocol needed.

**Detect → claim → enqueue → (later) handle → publish — a deliberate two-phase split.**
`scheduler_tick(db, *, now)` runs three detectors (`_due_missions`, `_due_overdue_milestones`,
`_due_quarterly`), each returning `Due` tuples; for each, it claims then calls `job_dispatcher.enqueue`
— the SAME job-queue path Slice 2's email jobs already use — and commits once at the end. The actual
work (generating the mission, re-checking the milestone, publishing the event) happens later, in a
handler, on a LATER worker poll iteration — this reuses Slice 2's retry/backoff/per-job-isolation for
free, rather than the tick doing the work inline and needing its own error-handling story. Per-item
isolation: each `Due` item is wrapped in its own `try/except`, so one bad item (e.g. a transient DB
error) logs a warning and does not stop the rest of the tick or corrupt the batch.

**Known non-atomicity between claim and enqueue — an accepted, low-probability gap.** `_claim`'s
SAVEPOINT is released (not rolled back) as soon as its `with` block exits; `job_dispatcher.enqueue`
runs AFTER that, outside the SAVEPOINT, still inside the per-item `try/except`. If `enqueue` itself
raised (it is a simple `INSERT`, so this is rare in practice), the claim would already be permanently
recorded with no job ever enqueued — that `(task, scope, period)` would never be retried. **This is
safe specifically for mission generation because mission generation is also lazy**: even if the
scheduled tick's own enqueue failed, `get_or_generate_today` (Module 04) still generates the mission
the first time any endpoint calls it (e.g. the founder opening the mission screen) — the founder never
loses their mission, only, in the worst case, the proactive "it's ready" notification for that one
day. The same gap exists in principle for overdue/quarterly, with no equivalent lazy fallback — judged
acceptable given how rarely a plain `INSERT` fails, not mitigated further in v1.

**Three handlers, one registered per job type, each re-checking before publishing.**
`app/worker/handlers/scheduled.py`:
- `handle_mission_generate` — calls `get_or_generate_today(db, startup)` (returns the existing
  mission if one was already created some other way, generates one otherwise) and publishes
  `mission.ready` if a mission resulted. Weekend-off workspaces still get an (empty) mission and thus
  still get notified — "today's mission is ready" is honest even when today's mission has zero tasks.
- `handle_roadmap_overdue` — RE-FETCHES the milestone (it may have been completed or deleted between
  enqueue and this handler actually running, since the job can sit `queued` for a while) and only
  publishes `roadmap.milestone.overdue` if it still exists and is still not `done` — proven by
  `test_overdue_suppresses_publish_when_milestone_already_done`/`_missing`.
- `handle_assessment_quarterly` — publishes `assessment.quarterly.due` unconditionally (no
  in-progress-assessment re-check needed at handle time; `_due_quarterly` already excludes workspaces
  with an assessment currently `in_progress` at detection time — see the "What's involved" detector
  note below).

**Registry + categories: three new rows, no new code shape.** `app/services/notifications/
registry.py::SPECS` gains `mission.ready`, `roadmap.milestone.overdue`, `assessment.quarterly.due`,
all using `_all_active_members` (same "no actor" recipient shape as the existing passive/system
events like `mission.completed`/`healthscore.dropped`) — there is no member who "did" a scheduled
event, so excluding an actor makes no sense here. `app/services/notifications/categories.py::
EVENT_CATEGORY` maps `mission.ready`/`roadmap.milestone.overdue` → `roadmap_missions` and
`assessment.quarterly.due` → `health_assessment` — both pre-existing categories from Slice 2, no 6th
category added, so a member who already opted out of `roadmap_missions` or `health_assessment` emails
is automatically opted out of the new scheduled emails too, with zero migration of existing
preference rows needed.

## What's involved

**Data model / migration**
- `alembic/versions/0024_scheduled_runs.py` — chains off `0023_learning`, sole head at build time.
  `scheduled_runs` table: `task_key varchar(60)`, `scope_key varchar(64)`, `period_key varchar(32)`,
  unique constraint `uq_scheduled_runs_task_scope_period` on the 3-tuple. No FK on `scope_key` — it's
  a generic string (a startup id or a milestone id depending on `task_key`), deliberately untyped so
  one ledger table serves all three task kinds.
- `app/db/models/scheduled_run.py` (new) — `ScheduledRun(UUIDMixin, TimestampMixin, Base)`.

**Scheduler** (`app/worker/scheduler.py`, new)
- `Due` (`NamedTuple`) — `task_key, scope_key, period_key, job_type, startup_id, payload`.
- `_claim(db, task_key, scope_key, period_key) -> bool` — savepoint insert, `IntegrityError` → False.
- `_local(now)` — `now.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE))`.
- `_active_startup_ids(db)` — active memberships joined to non-soft-deleted startups, distinct
  (fixed in `bc0e169`: a soft-deleted startup with a stale active membership row would otherwise still
  get scheduled work).
- `_due_missions(db, now)` — every active startup, IF `_local(now).hour >= MISSION_GEN_HOUR`; period
  key = the local calendar date (`isoformat()`).
- `_due_overdue_milestones(db, now)` — every `RoadmapMilestone` with `due_on < today` (local),
  `status != done`, joined through a non-soft-deleted `Startup`; period key = the constant `"once"`
  (D5 — no daily/weekly re-nudge in v1, roadmap TASK overdue also deferred, milestones only).
- `_due_quarterly(db, now)` — startups whose LATEST completed assessment's `completed_at <= now -
  QUARTERLY_REASSESS_DAYS days`, excluding any startup with an assessment currently `in_progress`;
  period key = `"{year}-Q{quarter}"`. Never targets a startup with no completed assessment at all
  (D6 — quarterly re-assessment is not the "do your first assessment" nudge).
- `scheduler_tick(db, *, now) -> int` — runs all three detectors, claims + enqueues each, commits
  once, returns the count actually enqueued (0 is a normal "nothing due" outcome, not an error).

**Handlers** (`app/worker/handlers/scheduled.py`, new) — `handle_mission_generate`,
`handle_roadmap_overdue`, `handle_assessment_quarterly` (see "How" above) — each calls
`register_handler(SCHED_*, handle_*)` at import time, same pattern as `handlers/email.py`.

**Worker wiring**
- `app/worker/__main__.py::main_loop` — added the throttled `scheduler_tick` call (see "How").
  `register()` now also imports `app.worker.handlers.scheduled` for its registration side effect.

**Notifications** — `app/services/notifications/registry.py` (3 new `SPECS` rows) and
`app/services/notifications/categories.py` (3 new `EVENT_CATEGORY` rows) — see "How" above.

**Config** (`app/core/config.py`) — `SCHEDULER_TIMEZONE: str = "UTC"` (IANA name for the daily
check), `MISSION_GEN_HOUR: int = 6` (local hour to pre-generate today's mission),
`SCHEDULER_INTERVAL: int = 60` (seconds; throttles the tick within the worker's poll loop, does NOT
change `WORKER_POLL_INTERVAL` itself), `QUARTERLY_REASSESS_DAYS: int = 90`.

**Errors / API surface** — none new. No new route, no new status code, no new error `code`. The only
externally-visible change is 3 new `type` values appearing in the existing `GET /notifications` feed
and the existing `unread-count`.

**Tests**
- `tests/worker/test_scheduler.py` — `_claim` once-vs-already-claimed, each detector's due/not-due
  boundary (hour gate, overdue date gate, quarterly cutoff + in-progress exclusion,
  soft-deleted-startup exclusion), `scheduler_tick`'s per-item isolation and total-enqueued count.
- `tests/worker/test_scheduled_handlers.py` (7) — each handler's publish/suppress behavior (see "How"
  above) + `test_handlers_registered` (the three job types are in `runner.JOB_HANDLERS` after import;
  fixed in `f3eec55` to re-import the module under an autouse fixture that evicts it from
  `sys.modules`, so the assertion doesn't silently pass off a PRIOR test's import in full-suite runs).
- `tests/services/notifications/test_scheduled_events.py` — all 3 events notify active members and
  map to the correct category, in one registry-level test.
- `e2e/test_notifications_scheduler.py::test_scheduled_mission_ready_notification` (new, this task) —
  see Verification below.

## Verification

**Per-task unit verification (Tasks 1–6, already green before this task):** each task's own report
(`.superpowers/sdd/2026-09-18-notifications-scheduler/task-{1..6}-report.md`) recorded a passing
scoped test run at the time; not re-litigated here.

**Task 7 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests e2e` | ✅ pass |
| Import order | `poetry run isort --check-only app tests e2e` | ✅ pass |
| Lint | `poetry run ruff check app tests e2e` | ✅ pass |
| Types | `poetry run mypy app` | ✅ pass |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ pass (see exact score below) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ pass, no findings |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ pass (see exact count/% below) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0024_scheduled_runs (head)` |
| Live E2E | `./scripts/e2e_run.sh` | ✅ 41 passed (40 Slice-1/2-and-earlier + 1 new) |

**Exact figures from this pass:** `black`/`isort`/`ruff` clean (5 pre-existing unformatted files from
Tasks 1–6 fixed in this pass — `app/db/models/scheduled_run.py`, `app/worker/scheduler.py`,
`tests/worker/test_entrypoint.py`, `tests/worker/test_scheduled_handlers.py`,
`tests/worker/test_scheduler.py` — plus one `ruff` `F401` unused-import finding in
`test_scheduled_handlers.py` and 4 `mypy` missing-parameter-annotation findings in
`app/worker/scheduler.py`'s detector functions, both fixed in this pass) · `mypy` clean · `pylint`
9.89/10 (≥ 9.5 floor, unchanged from Slice 2's last full sweep — no new findings in scheduler code)
· `bandit` clean, 0 findings · **1199 unit tests passed, 97.76% coverage** (≥ 95% floor, up from 1057
tests / 97.66% at the end of Slice 2) · exactly one alembic head (`0024_scheduled_runs`) · **41 e2e
passed** (up from 39 at the end of Slice 2).

Exact counts, coverage %, and anything added to satisfy a gate are recorded in
`.superpowers/sdd/2026-09-18-notifications-scheduler/task-7-report.md` (this task's own report,
written after the run below).

**`e2e/test_notifications_scheduler.py::test_scheduled_mission_ready_notification`** — a founder
onboards to stage `validation` (synchronously generates the roadmap — precondition for mission
generation) → asserts the fresh feed has no `mission.ready` row yet → runs `scheduler_tick` at 07:00
UTC (past the default `MISSION_GEN_HOUR=6` in the default `SCHEDULER_TIMEZONE=UTC`) → drains the
worker queue in-process (looped `runner.run_once`, same pattern as `test_notifications_email.py`'s
`_drain`, capped at 500 batches — a tick this late in the shared-DB e2e run enqueues a mission job for
EVERY active startup created by every earlier journey, not just this test's own) → the founder's feed
now has a `mission.ready` row with `data == {startup_id, mission_id}`, captured verbatim
(`mission_ready_feed.json`) → re-ticking the SAME day does NOT create a second `mission.ready` row
(proves the `scheduled_runs` claim ledger actually gates re-firing, not just first-time firing).

**Make-or-break interface fact, called out explicitly because it is easy to get silently wrong:** the
app only subscribes the notifications registry to `event_bus` at import of `app/api/v1/api.py`
(`register_notifications()`), which runs in the SERVER process. This e2e test's `_tick_and_drain`
runs `scheduler_tick` + `run_once` in the TEST process — `handle_mission_generate` publishes
`mission.ready` in-process, so without also calling `register_notifications()` inside
`_tick_and_drain` (idempotent — guarded by the registry's own `_registered_buses` set), no
notification row would ever be created and the feed assertion would fail with an empty list, not an
error. `e2e/test_notifications_email.py` already established this same requirement for Slice 2's
email enqueue path; this task's `_tick_and_drain` follows the identical pattern.

**Live vs. unit-verified, explicitly, per event (mirrors the FE guide's §10.1/§8):**

| Event | Verified live? |
|---|---|
| `mission.ready` | ✅ `e2e/test_notifications_scheduler.py`, `mission_ready_feed.json` |
| `roadmap.milestone.overdue` | ⚠️ unit only — `tests/worker/test_scheduled_handlers.py` (needs a backdated, not-done milestone; unreachable from a fresh e2e signup) |
| `assessment.quarterly.due` | ⚠️ unit only — `tests/worker/test_scheduled_handlers.py` (needs a completed assessment >90 days old; unreachable from a fresh e2e signup) |

This is a deliberate, honestly-labelled gap, not an oversight — see the FE guide's §10.3 for the
exact unit-sourced payload shapes and why neither event is reachable without backdating data outside
a live HTTP journey.

## Operate / roll back

**New deploy-time requirement: none beyond what Slice 2 already requires.** The scheduler runs INSIDE
the existing `worker` container/process — there is no new service to add to
`docker-compose.yml`/`docker-compose.prod.yml`, no new health check, no new resource limit. A deploy
that already has Slice 2's `worker` container running picks up the scheduler automatically on the next
image with this slice's code.

**New/changed config (all have safe defaults; only override if the defaults are wrong for
production):**
- `SCHEDULER_TIMEZONE` (default `UTC`) — the IANA timezone the 06:00 mission-generation check and the
  quarterly-period boundary are computed in. **This is a single global value for ALL workspaces** — a
  workspace in a very different timezone from this setting will see its mission "ready" at a
  wall-clock time that doesn't feel like 06:00 local to them (see Follow-ups).
- `MISSION_GEN_HOUR` (default `6`) — the local hour (0–23) past which the daily mission-generation
  check fires.
- `SCHEDULER_INTERVAL` (default `60` seconds) — how often the worker loop's throttle allows a
  scheduler tick to actually run detectors + claim + enqueue. Lower = more responsive scheduled
  triggers, at the cost of one more set of 3 detector queries per tick; the defaults were chosen as
  "responsive enough for a once-a-day/once-ever/once-a-quarter cadence without meaningfully loading
  the DB."
- `QUARTERLY_REASSESS_DAYS` (default `90`) — how old the last completed assessment must be before a
  workspace is flagged due for quarterly re-assessment.
- `alembic upgrade head` picks up `0024_scheduled_runs` automatically — the new table starts empty, no
  data migration needed (nothing to backfill: a workspace's first scheduler tick after deploy is its
  first-ever claim attempt for whatever period is current at that moment).

**Rollback:** `alembic downgrade -1` from `0024_scheduled_runs` drops the `scheduled_runs` table —
**lossy** for the claim history (any period already "fired" forgets that it fired). This is
**operationally safe to roll back forward again immediately after**: a workspace that already got its
`mission.ready` for today simply gets a duplicate one the next tick after the table is recreated (the
mission itself is NOT duplicated — `get_or_generate_today` is idempotent per calendar day regardless
of the ledger), so the only user-visible effect of a rollback-then-reapply is at most one duplicate
scheduled notification per workspace, not a data-integrity issue. Rolling back the migration without
also reverting the scheduler/handler code would 500 (or, more likely, silently except-and-log per the
worker loop's own `try/except`) every tick once it tries to `INSERT` into a table that no longer
exists — revert migration + scheduler code + handler code + the `__main__.py` wiring together, in
practice "revert this slice's commits as one unit," same shape as Slices 1–2's own rollback notes.
- Stopping the `worker` container (as Slice 2's SOP already documents) also fully stops the scheduler
  — no separate "pause just the scheduler" knob exists or is needed; scheduled work simply resumes
  claiming from wherever the ledger already stands whenever the worker restarts.

## Follow-ups

**Single global timezone, not per-workspace — an explicit v1 waiver (design doc §11 D3), not an
oversight.** `Startup` has no timezone field today; every workspace's "06:00" and quarter boundaries
are computed in the ONE `SCHEDULER_TIMEZONE` config value. A workspace far from that timezone will see
`mission.ready` fire at an odd local wall-clock hour. Adding a per-workspace timezone is a plausible
future slice if this becomes a real complaint — not built here.

**Overdue fires once per milestone, ever — no daily/weekly re-nudge (D5).** A milestone that stays
overdue for months generates exactly one notification, at the moment it first became overdue (or the
first tick after deploy, for a milestone already overdue when this slice ships). If founders want a
persistent "still overdue" signal, the FE must compute it client-side from the roadmap data itself
(§10.4 of the FE guide already calls this out) — there is no server-side recurring reminder to build
against.

**Quarterly re-assessment only applies to workspaces with a PRIOR completed assessment (D6).** A
workspace that has never finished an assessment at all is never targeted by this trigger — that's
Module 07's "do your first assessment" nudge, out of scope here, not built by this slice either.

**Roadmap TASK overdue is out of scope — milestones only.** The design doc's non-goals list this
explicitly; a roadmap task that is overdue but whose parent milestone isn't (e.g. one task slipped,
others on track) generates no notification in v1.

**The claim/enqueue non-atomicity gap (see "How" above) is an accepted risk, not mitigated further.**
Safe today because mission generation has a lazy fallback (the founder still gets their mission from
the normal "open the app" path even if the scheduled notification's enqueue silently failed);
overdue/quarterly have no equivalent fallback, so a failed enqueue there would permanently skip that
one occurrence. Given `job_dispatcher.enqueue` is a plain `INSERT` with no external dependency, this
is judged low-probability enough not to warrant a two-phase-commit-style fix in v1.

**Module 20 is now down to its last slice.** Slice 4 (real-time/push, websocket delivery) remains
completely unbuilt — the FE still has zero server-pushed events for ANY notification type, scheduled
or reactive; polling (§2 of the FE guide) is still the only way to learn about a new notification
before opening the panel.
