# SOP — Roadmap Core (Module 05, Slice 1)

**What shipped** — The founder-facing Roadmap: a per-startup phase → milestone → task tree,
auto-generated from one of 6 stage templates (`idea`/`validation`/`build`/`launch`/`growth`/
`scale`) the moment onboarding completes, plus 11 endpoints to read the tree and manage
phases/milestones/tasks by hand. Milestone `progress` (0–100) is derived from its tasks'
`status` — never set directly — and both milestones and tasks carry a derived `overdue` flag.
Marking a milestone `done` via `PATCH` fires a `roadmap.milestone.completed` event. New
`roadmaps` / `roadmap_phases` / `roadmap_milestones` / `roadmap_tasks` /
`roadmap_task_dependencies` tables (migration `0006_roadmap`) — the last table is created but
deliberately unused in this slice (see Follow-ups). This is Slice 1 of 3 for Module 05
(Slice 2: dependencies + template gallery; Slice 3: AI re-plan) — the other two slices are
separately planned and not part of this shipment.

Commits: `58cc6a5`..`03f4299` (Tasks 1–10) + this task's SOP/FE-guide/checklist commit.

## Why

Onboarding (Module 01.6) already enqueued a `roadmap.generate` job at
`POST /onboarding/complete`, and the Assessment SOP's completion path enqueues
`roadmap.replan` — but nothing consumed either job and no roadmap ever actually existed. A
founder who just onboarded had a `stage` (from onboarding step 3) and a completed assessment,
but no concrete plan of what to do next. Slice 1's goal: turn "founder picked a stage" into an
immediately-visible, editable plan — a template-seeded tree the founder (and any invited
`team_member`) can check off, extend, and re-order — without yet building the two heavier
Slice 2/3 concerns (dependency graphs, AI re-planning).

## How

**Stage-only template catalog, not AI-generated.** `STAGE_TEMPLATES`
(`app/services/roadmap/templates.py`) is a frozen dict keyed by `StartupStage` value — 6 stage
templates, each a list of phases with `start_week`/`end_week` offsets and milestones with
`due_week` offsets and a fixed task list (title + `TaskEffort`). `ROADMAP_TEMPLATE_VERSION = 1`
is stamped on every generated roadmap so a future template-version bump doesn't silently
reshape existing roadmaps. This mirrors the Assessment module's versioned-bank pattern
(`ASSESSMENT_BANK`, `docs/sop/2026-08-16-assessment.md`) — a static, testable structure now,
swappable for Module 03's AI-driven version later without touching the endpoint contract.

**Create-once, race-safe generation.** `generate_roadmap()`
(`app/services/roadmap/service.py:33`) claims a roadmap row with a single
`INSERT ... ON CONFLICT (startup_id) DO NOTHING RETURNING id` (`pg_insert(...)
.on_conflict_do_nothing(index_elements=["startup_id"]).returning(Roadmap.id)`) against the
unique index on `roadmaps.startup_id`. Under two concurrent callers for the same startup,
Postgres serializes on that index — the loser's `INSERT` blocks until the winner commits, then
observes the conflict and returns no row, so it falls through to loading the winner's
already-built tree instead of racing to build a duplicate one. This is the same query-then-
build-vs-single-atomic-claim shape as `start_or_resume()` in Assessment and
`complete_assessment()`'s conditional `UPDATE`, applied to an `INSERT` instead of an `UPDATE`
since there's no existing row to condition on for the very first caller. A dedicated
concurrency test (`tests/services/test_roadmap_generate.py::test_generate_create_once_under_race`)
exercises this directly, not just the happy path.

**Inline generation replaces the onboarding stub.** `complete_onboarding()`
(`app/services/onboarding/complete.py`) previously only enqueued a `roadmap.generate` job that
nothing drained. It now calls `generate_roadmap(db, startup, actor=user)` synchronously in the
same transaction, then still enqueues the `roadmap.generate` job and immediately marks it
`succeeded` — same pattern Health Score used for `healthscore.recalculate` (see
`docs/sop/2026-08-19-health-score.md`): the job row exists for any FE code that polls
`GET /jobs/{id}` by an id it already has, but the actual work already happened by the time the
HTTP response returns. `GET /roadmap`'s own generate path (`app/api/v1/endpoints/
roadmap.py:get_roadmap`) additionally does a **lazy** generate — if a workspace member hits
`GET /roadmap` before a roadmap exists (shouldn't happen post-onboarding, but is not assumed),
it generates on that first read rather than 404ing. `POST /roadmap/generate` exists as an
explicit, idempotent re-trigger endpoint (returns `202` + an already-`succeeded` job) for any
FE flow that wants an explicit "build my roadmap" action rather than relying on the implicit
onboarding/lazy paths.

**Derived progress and overdue, never client-settable.** `recompute_milestone_progress()`
(`service.py:119`) is called after every task create/update/delete and after a milestone's own
`PATCH`: with no tasks, `progress` is `100` if the milestone's own `status == done` else `0`;
with tasks, `progress = round(100 * done_count / total_count)`. `milestone_overdue()` /
`task_overdue()` are pure functions of `due_on < today() and status != done` — computed fresh
on every serialize, never stored. Marking a milestone `done` directly (no tasks, or tasks not
all done) is honored as an explicit status override — `progress` snaps to `100` regardless of
task completion, matching "I'm calling this milestone done" as an authoritative founder action,
not a lie the system should reject.

**Explicit-null rejection on NOT-NULL `PATCH` fields.** `PhaseUpdate`, `MilestoneUpdate`,
`TaskUpdate` (`app/schemas/roadmap.py`) each carry a `field_validator` on their NOT-NULL-backed
fields (`name`/`order` on phase; `title`/`status`/`order` on milestone; `title`/`effort`/
`status`/`order` on task) that raises on an explicit `null` — Pydantic's `exclude_unset` already
lets an *omitted* field pass through untouched, but a client that sends `{"title": null}`
otherwise hits an `AttributeError`/constraint violation deep in the ORM flush rather than a
clean `422`. The validator turns that into `VALIDATION_ERROR` at the boundary, same shape as
every other field-level 422 in this API.

**Role gate: founder + team_member can write, mentor is read-only.**
`_editor = require_role(MembershipRole.founder, MembershipRole.team_member)`
(`app/api/v1/endpoints/roadmap.py`) gates every phase/milestone/task write route.
`GET /roadmap` gates on `require_workspace` (any active member, including mentor) — mentors
see the plan but can't touch it. `owner_id`/`assignee_id` on milestone/task create-or-update are
re-validated server-side via `_validate_member()` against active memberships of the caller's
own `startup_id` — a client cannot assign a milestone to an arbitrary user id from another
tenant; that 422s `VALIDATION_ERROR` the same way a bad `owner_id` on Health Score would.

**Cross-tenant lookups scope through the roadmap join, not just an id match.** `_phase()` /
`_milestone()` / `_task()` (`roadmap.py`) all resolve the caller's own `Roadmap` first via
`_require_roadmap()` (filtered by `membership.startup_id`), then join down to the requested
phase/milestone/task **filtered by that roadmap's id** — so a phase/milestone/task id that is
real but belongs to a different startup's roadmap 404s exactly like an id that doesn't exist at
all, matching the uniform-404 pattern already established in Health Score
(`docs/fe-integration-guide-health-score.md` §6) and Assessment `compare`.

## What's involved

**Data model / migration**
- `alembic/versions/0006_roadmap.py` — five new tables, no lock on any existing table
  (autogenerated from the Task 1 models below; only revision id/down_revision/docstring were
  hand-edited — see the migration's own docstring for full lock-duration reasoning).
  - `roadmaps` (+ unique `ix_roadmaps_startup_id`) — one roadmap per startup.
  - `roadmap_phases` (+ `ix_roadmap_phases_roadmap_id`).
  - `roadmap_milestones` (+ `ix_roadmap_milestones_phase_id`; `owner_id` optional FK to
    `users.id`, no `ondelete` — a deleted user blocks, not cascades, until reassigned).
  - `roadmap_tasks` (+ `ix_roadmap_tasks_milestone_id`; `assignee_id` same non-cascading FK
    shape as `owner_id`).
  - `roadmap_task_dependencies` — composite PK `(task_id, depends_on_task_id)`, both FK
    `ON DELETE CASCADE` to `roadmap_tasks.id`, plus `CHECK (task_id <> depends_on_task_id)`
    (real Postgres name `ck_roadmap_task_dependencies_ck_task_dep_not_self` — a SQLAlchemy
    naming-template artifact, not a naming bug; see migration docstring). Created now,
    written by nothing until Slice 2.
- `app/db/models/roadmap.py` — `Roadmap`, `RoadmapPhase`, `RoadmapMilestone`, `RoadmapTask`,
  `RoadmapTaskDependency`.
- `app/db/models/enums.py` — `RoadmapStatus` (`todo`/`in_progress`/`done`), `TaskEffort`
  (`small`/`medium`/`large`); reuses the existing `StartupStage` enum for `roadmaps.stage`.

**Endpoints** (all under `/api/v1/roadmap`, 11 total)

| Method | Path | Auth | File |
|---|---|---|---|
| GET | `/api/v1/roadmap` | any active member | `app/api/v1/endpoints/roadmap.py` |
| POST | `/api/v1/roadmap/generate` | founder or team_member | same |
| POST | `/api/v1/roadmap/phases` | founder or team_member | same |
| PATCH | `/api/v1/roadmap/phases/{phase_id}` | founder or team_member | same |
| DELETE | `/api/v1/roadmap/phases/{phase_id}` | founder or team_member | same |
| POST | `/api/v1/roadmap/milestones` | founder or team_member | same |
| PATCH | `/api/v1/roadmap/milestones/{milestone_id}` | founder or team_member | same |
| DELETE | `/api/v1/roadmap/milestones/{milestone_id}` | founder or team_member | same |
| POST | `/api/v1/roadmap/tasks` | founder or team_member | same |
| PATCH | `/api/v1/roadmap/tasks/{task_id}` | founder or team_member | same |
| DELETE | `/api/v1/roadmap/tasks/{task_id}` | founder or team_member | same |

All routes require `X-Workspace-Id` + Bearer token, same as every other tenant-scoped surface.
The three `DELETE` routes exist and are unit-tested (`tests/api/test_roadmap_phases.py` etc.)
but were not exercised by the live E2E journey — see the FE guide's verification table.

**Services / schemas**
- `app/services/roadmap/templates.py` — `ROADMAP_TEMPLATE_VERSION`, `STAGE_TEMPLATES`.
- `app/services/roadmap/service.py` — `generate_roadmap`, `recompute_milestone_progress`,
  `milestone_overdue`, `task_overdue`, `person_ref`, `serialize_tree`.
- `app/schemas/roadmap.py` — `PhaseCreate`/`PhaseUpdate`, `MilestoneCreate`/`MilestoneUpdate`,
  `TaskCreate`/`TaskUpdate` (the three `*Update` schemas carry the explicit-null-rejection
  validator described above).
- `app/services/onboarding/complete.py` — wiring change: `generate_roadmap` now called inline
  before the `roadmap.generate` job is enqueued-then-marked-succeeded.
- Errors: reuses existing `NotFound` (404 — unknown/cross-tenant phase/milestone/task/roadmap)
  and the existing ad-hoc `VALIDATION_ERROR` `AppError` shape (422 — bad `owner_id`/
  `assignee_id`, or explicit `null` on a NOT-NULL `PATCH` field). No new error codes.

**Tests**
- `tests/db/test_roadmap_models.py` — model/constraint tests (moved here per repo convention,
  commit `0b078a6`).
- `tests/services/test_roadmap_templates.py`, `test_roadmap_generate.py` (incl. the race test),
  `test_roadmap_progress.py`, `test_onboarding_generates_roadmap.py`.
- `tests/api/test_roadmap_get.py`, `test_roadmap_generate_endpoint.py`, `test_roadmap_phases.py`,
  `test_roadmap_milestones.py`, `test_roadmap_tasks.py`.
- `e2e/test_roadmap.py` (this task) — one live end-to-end journey against a real running
  server, capturing every response to `e2e/_captures/roadmap/*.json`.

## Verification

- **Live E2E: 25 passed** (`make e2e`) — 24 prior + the new `test_roadmap_journey`: founder
  onboards to stage `validation` (roadmap auto-generates inline) → invites a `team_member` and
  a `mentor` before completing onboarding → both accept → founder completes onboarding →
  `GET /roadmap` shows the `stage.validation` template tree → `POST /generate` on an
  already-generated roadmap returns `202` + an already-`succeeded` job (idempotent path) →
  create a custom phase → milestone → task → `PATCH` the task to `done` → re-`GET /roadmap`
  confirms the milestone's `progress` flipped to `100` from that one task → `PATCH` the
  milestone itself to `done` → malformed `PATCH {"title": null}` on the task → `422`
  `VALIDATION_ERROR` → the `team_member` can `PATCH` the task (in `_editor`) → the `mentor` can
  `GET` but a `POST /phases` from the mentor → `403 FORBIDDEN` → a second founder's own
  (separately generated) roadmap cannot be reached by the first founder's phase/milestone ids →
  `404 NOT_FOUND` on both. All 11 response bodies captured verbatim to
  `e2e/_captures/roadmap/*.json` and are the source for `docs/fe-integration-guide-roadmap.md`.
- **Unit suite: 304 passed, 98% coverage** (`poetry run pytest -q`).
- `poetry run black --check .`, `poetry run isort --check .`, `poetry run ruff check .`,
  `poetry run mypy app` — all clean.
- Migration verified via every `make e2e` run (fresh `cofoundaz_e2e` DB migrated from zero,
  `0005_health_score` → `0006_roadmap`, each run).

## Operate

- No new env vars or deploy steps beyond the existing `make e2e` / `alembic upgrade head` flow.
- **Rollback:** `alembic downgrade -1` drops `roadmap_task_dependencies`, `roadmap_tasks`,
  `roadmap_milestones`, `roadmap_phases`, `roadmaps` in that FK-safe order. This is **lossy** —
  any roadmap data (including founder-created custom phases/milestones/tasks) written while
  `0006` was applied is destroyed on downgrade, same as every other brand-new-table migration in
  this project (`0004`, `0005`).

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Slice 2 — dependencies + templates gallery.** `roadmap_task_dependencies` exists
  (migration `0006`) but nothing writes to it in Slice 1 — `depends_on` is always `[]` and
  `dependency_count` is always `0` on every task/milestone (see next item). Dependency
  creation, cycle detection, a dependency-graph read, a `GET /roadmap/templates` gallery, and
  industry template variants (Fintech/B2C/Nigeria overlays) are Slice 2's scope.
- **Slice 3 — AI re-plan.** Drift detection, `POST /roadmap/replan/preview` (before/after
  diff), `POST /roadmap/replan/apply {change_ids[]}` (consuming the `roadmap.replan` job
  Assessment already enqueues on completion), and a `roadmap.replanned` event — never
  auto-applied. Out of scope here.
- **`dependency_count` runs a per-milestone COUNT that is always 0** (`serialize_tree`,
  `service.py:163`) — harmless in Slice 1 (no dependency rows exist to count) but is a real
  query per milestone on every `GET /roadmap`, kept now for forward-compat with Slice 2 rather
  than adding it back later as a schema-shape change the FE would have to handle.
- **`person_ref` per-owner/assignee query is a no-op today** — every `owner_id`/`assignee_id`
  is currently `None` in the live journey (no assignment UI/flow exists yet), so `person_ref`
  never actually resolves a user row in practice. It's exercised by unit tests
  (`fd689c6`) and will start doing real work once an assignment feature lands.
- **`_task_out` (endpoint) vs the per-task dict inside `serialize_tree` (service) duplicate
  the same task shape** (`app/api/v1/endpoints/roadmap.py:157` vs
  `app/services/roadmap/service.py:183`) — one is used for single-task CRUD responses, the
  other for the nested tree. Candidate for a future dedup (e.g. a shared `_task_dict` helper)
  once a third caller needs the same shape; not worth the abstraction for two call sites yet.
- **`_next_order` (`max(order)+1`) has no DB uniqueness constraint on `order`** — under
  concurrent sibling creates (two `POST /phases` for the same roadmap racing), both could read
  the same `max` and land on the same `order`. Benign: `order` is purely a display/sort hint,
  never used as an identity or constraint elsewhere, so a duplicate just means two siblings
  render in an arbitrary relative position until one is manually re-ordered.
- **`roadmap.milestone.overdue` push event + due-soon/overdue notifications need a scheduler**
  (Module 20) — `overdue` is surfaced now as a derived, read-time flag (every `GET`
  recomputes it), but nothing proactively notifies a founder when a milestone crosses its
  `due_on`. That's a cron/scheduler concern for Module 20, same shape as the deferred
  quarterly-reassessment cron in Assessment.
- **Workspace-timezone base date** — `generate_roadmap`'s phase/milestone due-dates are computed
  from `date.today()` in the server's local/UTC date, not the workspace's configured timezone
  (no such setting exists yet). Fine at current scale; revisit once workspace-level timezone
  preferences exist.
