# SOP — Today's Mission (Module 04)

**What shipped** — A daily 1–3 task mission for each workspace, generated **lazily on first
read** from the founder's existing roadmap: `GET /missions/today` materialises today's mission
(if none exists yet) by selecting the next incomplete roadmap tasks, snapshotting their title/
effort/reason into `mission_tasks`, and returning them with a **derived streak**. Founders (and
`team_member`s) can complete / snooze / reorder / reject each task, add their own custom tasks,
browse history with per-day `completed/total` counts and a rolling weekly completion %, and tune
generation via mission settings (`mission_size` 1–3, `delivery_time`, `weekend_missions`). Six
endpoints under `/api/v1/missions`. Three new tables (`missions`, `mission_tasks`,
`mission_settings`) via migration `0009_mission`. Task completion fires `mission.task.completed`;
finishing the last task fires `mission.completed` and, on a 7/30/100-day streak,
`mission.streak.milestone` — all emitted into the in-process event bus with no consumer yet
(Module 20). **Read-only against the roadmap**: this module never writes to any `roadmap_*` table.

Commits: `b3f22a8`..`b2bd408` (Tasks 1–6) + this task's SOP/FE-guide/checklist commit. Design
spec + plan: `c4d0c37`.

## Why

Onboarding produces a roadmap (Module 05) — a full phase → milestone → task tree — but a founder
opening the app each morning still faced the whole tree at once with no answer to "what are the
one-to-three things I should actually do *today*?" The PRD's Module 04 is that daily focus
surface: a short, ordered, roadmap-derived to-do list with completion tracking and a streak to
build the habit. The goal for v1 was to ship that focus loop end to end **without** taking on the
two heavier concerns it implies — a 06:00 cron that pushes the mission (needs the Module 20
scheduler) and an AI-authored "why this task matters" line (needs Module 03). Both are deferred by
design; v1 generates on read and templates the reason line.

## How

Key decisions, all locked in brainstorming (`docs/superpowers/specs/2026-08-26-todays-mission-design.md` §2):

**Inline + lazy-on-read generation, not a cron.** `get_or_generate_today(db, startup)`
(`app/services/mission/service.py:84`) is the whole generation path. On `GET /missions/today` it
returns today's mission if a `(startup_id, mission_date)` row already exists (the unique
constraint guarantees at most one), else builds it. This mirrors the roadmap's own lazy-generate
and Health Score's inline-recompute pattern — no worker, no job to poll. The real 06:00 scheduled
generation + push is Module 20's job; `delivery_time` is stored now purely as metadata for that
future cron.

**Deterministic candidate selection, read-only over the roadmap.** `_candidate_tasks`
(`service.py:46`) joins `RoadmapTask → RoadmapMilestone → RoadmapPhase`, filters to the caller's
roadmap and `status != done`, and orders by `(milestone.due_on asc nulls last, phase.order,
milestone.order, task.order)` — the same "soonest deadline, then plan order" a founder would read
top-to-bottom. It takes the first `mission_size` (default 3). Snoozed tasks from the most recent
prior mission are **carried forward first** (they jump the queue), then the candidate list fills
the remainder, skipping any roadmap task already carried this cycle so it can't appear twice.

**Soft, un-FK'd `roadmap_task_id`.** `mission_tasks.roadmap_task_id` is a nullable `UUID` column
**with no foreign key** to `roadmap_tasks` (the migration docstring spells out the intent). A
mission is a *snapshot*: it copies the roadmap task's `title`, `effort`, and a templated `reason`
at generation time. So if the founder later edits, reorders, regenerates, or deletes that roadmap
task, an already-materialised mission is unaffected — nothing cascades, nothing blocks. It also
keeps Module 04 fully decoupled from the roadmap tables (same rationale as `jobs.startup_id`).
Custom tasks carry `roadmap_task_id = null`.

**Derived streak, never stored.** `streak(db, startup)` (`service.py:162`) walks backwards from
today (or yesterday, if today's mission isn't complete yet) counting consecutive days whose
mission `status == complete`, stopping at the first gap. Nothing persists a streak counter, so it
can never drift from the missions themselves — and it naturally resets when a day is missed.

**Templated reason line.** Generation writes `reason = f"From your '{milestone.title}' milestone."`
(`service.py:145`) — a static template, not AI. Custom tasks get `reason = null`. Module 03 will
later author a real rationale; the field and its `null`-on-custom shape are already in the contract.

**Completion cascade + events.** `complete_task` (`service.py:316`) is idempotent (re-completing a
done task is a no-op — no duplicate event, `completed_at` not clobbered). It sets `done` +
`completed_at`, emits `mission.task.completed`, then checks whether any task remains that is
neither `done` nor `rejected`; if none, it flips the mission to `complete`, emits
`mission.completed`, recomputes the streak, and emits `mission.streak.milestone` when the streak
lands exactly on 7/30/100. **Rejected tasks are ignored** in the all-done check — a mission whose
only unfinished work was rejected still reads complete.

**History counts ignore rejected, and empty missions are excluded from the weekly %.**
`_history_counts` (`service.py:243`) computes `total` as non-rejected tasks and `completed` as
`done` tasks in one grouped query — keeping the per-day row coherent with the completion check
above. `_weekly_completion_pct` (`service.py:208`) counts only missions with ≥1 non-rejected task,
so the perpetually-`pending` empty mission a "weekends off" day materialises doesn't drag the
percentage down (that fix landed in `258b776`).

**Custom task on a no-roadmap workspace still works.** `POST /missions/tasks`
(`app/api/v1/endpoints/mission.py:136`) calls `get_or_generate_today`; if that returns `None` (no
roadmap), it starts today's mission itself with `generated_by="user"` rather than blocking the
user on roadmap generation.

**Access split matches the rest of the API.** Reads (`/today`, `/history`, `/settings`) gate on
`require_workspace` (any active member, mentor included); writes (`PATCH /settings`,
`POST /tasks`, `PATCH /tasks/{id}`) gate on `_editor = require_role(founder, team_member)` — a
mentor write is `403`. `_mission_task` resolves a task through its parent mission filtered by the
caller's `startup_id`, so a cross-workspace or unknown id both fall to a uniform `404`.

## What's involved

**Data model / migration**
- `alembic/versions/0009_mission.py` — three brand-new tables, no lock on any existing table
  (autogenerated from the Task 1 models; only revision id/`down_revision`/docstring/statement
  order hand-edited). `down_revision = '0007_roadmap_applied_templates'` — an intentional
  **sibling** of the parallel `0008_roadmap_replan` (Roadmap Slice 3) off the same parent, not a
  chain; a later merge revision reconciles the two heads (see the migration docstring).
  - `missions` (+ `ix_missions_startup_id`, + `UNIQUE uq_mission_startup_date` on
    `(startup_id, mission_date)`) — one mission per workspace per day.
  - `mission_tasks` (+ `ix_mission_tasks_mission_id`; `roadmap_task_id` bare UUID, **no FK**).
  - `mission_settings` (PK = `startup_id`, FK → `startups.id` `ON DELETE CASCADE`).
- `app/db/models/mission.py` — `Mission`, `MissionTask`, `MissionSettings` (registered in
  `app/db/models/__init__.py`).
- `app/db/models/enums.py` — new `MissionStatus` (`pending`/`complete`), `MissionTaskStatus`
  (`todo`/`done`/`snoozed`/`rejected`); reuses the existing `TaskEffort` for `mission_tasks.effort`.

**Service** — `app/services/mission/service.py`
- `get_or_generate_today` (lazy generation), `streak` (derived), `serialize_mission` /
  `serialize_task`, `mission_history` + `_history_counts` + `_weekly_completion_pct`,
  `add_custom_task`, and the action helpers `complete_task` / `snooze_task` / `reorder_task` /
  `reject_task`. `VALID_REJECT_REASONS = {"Already done", "Wrong priority", "Doesn't apply"}` is
  the single source of truth for the reject chips.

**Endpoints** (all under `/api/v1/missions`, 6 total) — `app/api/v1/endpoints/mission.py`,
registered in `app/api/v1/api.py`.

| Method | Path | Auth |
|---|---|---|
| GET | `/missions/today` | any active member |
| GET | `/missions/history` | any active member |
| GET | `/missions/settings` | any active member |
| PATCH | `/missions/settings` | founder or team_member |
| POST | `/missions/tasks` | founder or team_member |
| PATCH | `/missions/tasks/{task_id}` | founder or team_member |

All routes require `X-Workspace-Id` + Bearer token. Schemas: `app/schemas/mission.py`
(`MissionSettingsUpdate`, `MissionTaskCreate`, `MissionTaskAction`). Errors reuse `NotFound`
(404 — unknown/cross-tenant mission task) and the ad-hoc `VALIDATION_ERROR` `AppError` shape
(422 — bad `action`, `mission_size` outside 1–3, bad `reject_reason`, missing `order` on reorder).
No new error codes.

**Events (in-process `event_bus`, no consumer until Module 20)** — `mission.task.completed`,
`mission.completed`, `mission.streak.milestone`.

**Tests**
- `tests/db/test_mission_models.py` — model/constraint (one-mission-per-day, cascade).
- `tests/services/test_mission_generate.py` — selection order, `mission_size`, carry-forward,
  no-roadmap → `None`, idempotency, weekend-off empty mission, streak.
- `tests/api/test_mission_today.py`, `test_mission_tasks.py`, `test_mission_history.py` — endpoint
  happy paths, auth/tenancy (mentor `403`, cross-workspace `404`), settings clamp, events.
- `e2e/test_mission.py` — one live end-to-end journey; `e2e/test_smoke.py` extended to assert the
  5 mission paths in the OpenAPI surface. Six response bodies captured to
  `e2e/_captures/mission/*.json` — the source for `docs/fe-integration-guide-mission.md`.

## Verification

- **Unit + integration suite: 380 passed, 98% coverage** (`poetry run pytest -q`).
- **Live E2E: 26 passed** (`make e2e`, verified in Task 6) — the new `test_mission_journey`:
  founder onboards → roadmap auto-generates → `GET /missions/today` returns 3 roadmap-drawn tasks
  → complete each → mission flips to `complete` (`mission.completed` reachable) + streak `1` →
  `POST /missions/tasks` adds a custom task (appended `order 3`, `roadmap_task_id` null) →
  `GET /missions/history` shows `completed:3, total:4, status:complete` + `weekly_completion_pct:100`
  → `GET /missions/settings` returns the defaults. Every response captured verbatim.
- `poetry run black --check .`, `poetry run isort --check .`, `poetry run ruff check .`,
  `poetry run mypy app` — all clean.
- Migration verified via every `make e2e` run (fresh `cofoundaz_e2e` DB migrated `0001 → 0009`).

## Operate / roll back

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `make e2e` flow.
- **Rollback:** `alembic downgrade -1` drops `mission_tasks`, `missions`, `mission_settings` in
  FK-safe order. **Lossy** — any mission data written while `0009` was applied is destroyed on
  downgrade, inherent to dropping brand-new tables (same as `0004`–`0007`).

## Follow-ups

**Deferred by design (not oversights):**
- **06:00 cron generation + push notification → Module 20.** v1 generates lazily on the first
  `GET /missions/today` of the day; `delivery_time` is stored but nothing acts on it yet, and no
  push/notification fires when a mission is ready.
- **AI-authored "why" reason line → Module 03.** v1 uses the static
  `"From your '{milestone}' milestone."` template; custom tasks have `reason = null`.
- **Real event delivery → Module 20.** `mission.task.completed` / `mission.completed` /
  `mission.streak.milestone` are emitted into the in-process `LogEventBus` with no consumer.
  Like the roadmap events, they are published *before* the enclosing `db.commit()` — harmless with
  the current bus, but should move to an after-commit hook once an at-least-once bus lands.
- **`GET /missions/today` is member-readable but generates.** Gated on `require_workspace`, so a
  read-only mentor hitting a not-yet-generated day can trigger the write that materialises the
  mission (benign; same shape as roadmap's lazy generate). `GET /missions/history`, by contrast, is
  strictly read-only and never materialises today's mission — a mission only appears in history
  once `/today` or a task action has created it.
- **No workspace-timezone base date.** `date.today()` is the server's local/UTC date, not the
  workspace's configured timezone (no such setting exists yet) — so the weekend-off check and the
  day boundary follow server time. Revisit once workspace-level timezone preferences exist.
