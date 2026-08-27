# SOP — Roadmap AI Re-plan (Module 05, Slice 3)

**What shipped** — The final slice of Module 05: drift detection (slipped, not-done
milestones), a stateless `preview` of a deterministic re-plan proposal, an `apply` that commits
selected changes with per-milestone markers + one history row + a `roadmap.replanned` event, a
`GET /replan/history` read, and two additions to the roadmap tree (`roadmap.drift.slipped_count`
+ per-milestone `replanned`). New `roadmap_replans` table + two nullable marker columns on
`roadmap_milestones` (migration `0008_roadmap_replan`). **The re-planner never auto-applies** —
it proposes, a human (founder/team_member) commits. This closes out Module 05; all three slices
(Core, Dependencies + Templates, AI Re-plan) are now shipped.

Commits: `d33f476`..`1667781` (Tasks 1–6) + this task's SOP/FE-guide/checklist commit.

## Why

Two seams already existed with nothing behind them: Assessment's completion path enqueues a
`roadmap.replan` job (see `docs/sop/2026-08-21-roadmap-core.md` Follow-ups), and a founder who
falls behind on their roadmap had no way to recover except manually editing every slipped
milestone's `due_on` by hand. Slice 3's goal: give a founder who has slipped a low-friction way
to see "here's how I'd re-plan this" and commit to it with one tap — without silently
auto-shifting dates behind their back (the co-pilot principle that also shaped Health Score's
recommendations and Assessment's adaptive engine: propose, never impose).

## How

**Slip-and-cascade along the dependency DAG, not just the slipped milestone itself.**
`detect_drift()` (`app/services/roadmap/replan.py:43`) finds milestones with `due_on < today`
and `status != done`. `compute_replan()` gives each slipped milestone a *base shift* — target
`today + REPLAN_BUFFER_DAYS` (7, a named module constant) — then propagates that shift downstream
through a **milestone-level precedence graph** projected from the existing
`roadmap_task_dependencies` table: `_milestone_precedence()` walks every dependency edge and
records "B's milestone depends on A's milestone" whenever some task in B depends on some task in
A. `shift[M] = max(base_shift[M], max(shift[U]) for each upstream U)` — **max, not sum** — a
milestone waits on the *latest* of its blockers, so a diamond (D depends on both B and C) shifts
by `max(shift[B], shift[C])`, never their total. A dedicated topological sort
(`_toposort`, `replan.py:73`) orders milestones so every upstream is resolved before its
downstream, with a defensive cycle guard (a stray cross-milestone cycle just gets its back-edge
skipped, never loops) even though the graph is acyclic by construction from Slice 2's own
cycle-preventing `add_dependency()`.

**Reason templating tracks which shift actually won, not just whether the milestone itself
slipped.** The first cut of this logic (commit `bd14da0`) wrote "N days overdue" whenever the
milestone had *any* own slip, even when the cascade from an upstream dependency was the larger
mover — wrong when both applied. Commit `7088445` fixed this by tracking `max_upstream[mid]`
(which upstream, if any, actually determined the final `shift[mid]`) during propagation and
templating on that: pure own-slip → *"{n} days overdue and not yet done."*; pure cascade →
*"Shifts {n} days with its dependency '{title}'."*; both (self-slipped **and** the cascade is the
bigger mover) → a combined sentence naming both. `tests/services/test_roadmap_replan_compute.py`
now has a dedicated case (`test_reason_names_dependency_when_cascade_dominates_own_slip`) for
exactly this scenario so it can't silently regress.

**Stateless preview, recompute-on-apply — never trust a client-held diff.** `compute_replan()` is
a pure function of *current* DB state (only `date.today()` is non-deterministic); nothing is
persisted between `preview` and `apply`, and `change_id == milestone_id` (a milestone has at most
one proposed shift, so no separate id space is needed). `apply_replan()`
(`replan.py:147`) **recomputes the proposal from scratch** and only commits `change_ids` that are
still present in that fresh proposal — a roadmap edited between `preview` and `apply` (task
completed, dependency removed, milestone manually re-dated) silently drops the now-stale ids into
`skipped` rather than applying a diff that no longer reflects reality. This mirrors the same
recompute-don't-trust-the-client shape used by Health Score's recommendation engine and
Assessment's scoring — the server is always the source of truth for "what would this change do
right now."

**Markers + one history row, no per-change audit table.** A successful apply stamps
`last_replanned_at`/`last_replan_reason` directly on each shifted `RoadmapMilestone` (powers the
tree's "↻ Re-planned" chip without a join) and writes **one** `roadmap_replans` row per apply
call with a JSONB `changes` snapshot (`[{milestone_id, title, old_due, new_due, reason}]`) plus a
templated `summary` ("Re-planned N milestone(s)"). Empty or all-stale `change_ids` → `{applied:
[], skipped: [...]}`, **no history row, no event** — matches the "no-drift is a 200 empty-state,
not an error" pattern already established for preview. This also makes apply naturally
idempotent: re-applying the same `change_ids` after they've already been committed finds those
milestones no longer shifting (their `due_on` is already at the target), so `compute_replan()`
omits them from the fresh proposal and they all come back `skipped`.

**`roadmap.replanned` is emitted, with no consumer — by design, same shape as every other Module
05 event.** Fired via `event_bus.publish` (fire-and-forget, in-process `LogEventBus`) after the
history row is flushed, carrying `{startup_id, roadmap_id, replan_id, change_count,
applied_by}`. Nothing drains it yet — Module 20 (Notifications) is the eventual consumer for the
push copy *"I've drafted a re-plan for your review"* named in the PRD. This retires the "Slice 3"
line from Slice 2's SOP Follow-ups the same way Slice 2's `roadmap.template.applied` event still
has no consumer either.

**The `roadmap.replan` job stays an unconsumed stub — on purpose.** Assessment's completion path
already enqueues this job (see Slice 1 SOP), and the spec explicitly rules out ever draining it
automatically: an auto-triggered re-plan would violate the co-pilot principle this whole slice is
built around (propose, never auto-commit). The job row exists and is marked `succeeded`
elsewhere in the codebase's job-lifecycle convention, but nothing reads its payload to trigger a
re-plan — a founder must explicitly call `POST /replan/preview` themselves.

**Phases and tasks are not shifted in v1 — only milestone `due_on`.** The spec scopes the
cascade to the scheduling unit the FE diff shows (milestone due dates); phase `starts_on`/
`ends_on` and task-level dates are untouched by a re-plan, even when every milestone in a phase
shifts. Revisit if the FE diff view needs phase-level date ranges to stay visually consistent
with their shifted milestones.

## What's involved

**Data model / migration**
- `alembic/versions/0008_roadmap_replan.py` — new table + two nullable columns, no lock
  contention with existing roadmap data (see the migration's own docstring for full
  lock-duration reasoning — both changes are metadata-only / new-table, sub-second on
  prod-sized data).
  - `roadmap_replans` (+ `ix_roadmap_replans_roadmap_id`) — `roadmap_id` FK→roadmaps
    (`ondelete=CASCADE`), `applied_by` FK→users, `change_count` (Integer), `changes` (JSONB
    snapshot), `summary` (String(200)), plus UUIDMixin/TimestampMixin.
  - `roadmap_milestones` gains `last_replanned_at` (timestamptz, nullable) and
    `last_replan_reason` (String(300), nullable) — both `ADD COLUMN ... NULL`, no default.
- `app/db/models/roadmap.py` — `RoadmapReplan` model + the two marker columns on
  `RoadmapMilestone`.

**Services**
- `app/services/roadmap/replan.py` (new) — `REPLAN_BUFFER_DAYS = 7`, `Change` dataclass,
  `detect_drift`, `_milestone_precedence`, `_toposort`, `compute_replan`, `apply_replan`.
- `app/services/roadmap/service.py` — `serialize_tree` additions: `roadmap.drift` (via a local
  import of `detect_drift` to avoid a module cycle, `service.py:202`) and each milestone's
  `replanned` (`service.py:235`).

**Endpoints** (`app/api/v1/endpoints/roadmap.py:616-680`, all under `/api/v1/roadmap`)

| Method | Path | Auth | Behaviour |
|---|---|---|---|
| POST | `/replan/preview` | any active member | Stateless compute → `{drift_count, changes:[...]}` |
| POST | `/replan/apply` | founder/team_member (`_editor`) | Commits still-valid `change_ids` → `{applied, skipped, replan_id, summary}` |
| GET | `/replan/history` | any active member | `[{id, change_count, summary, applied_by, created_at, changes}]`, newest first |

**Schemas** — `app/schemas/roadmap.py`: `ReplanApply {change_ids: list[uuid.UUID]}`.

**Errors** — reuses the existing `AppError` taxonomy, no new codes: `NOT_FOUND` (404, no
roadmap / cross-tenant), `FORBIDDEN` (403, mentor hitting `apply`), `EMAIL_NOT_VERIFIED` (403,
shared guard). No-drift preview and empty/all-stale apply are `200` empty-states, not errors.

**Events** — `roadmap.replanned` (fire-and-forget via `event_bus.publish`, no consumer).

**Tests**
- `tests/db/test_roadmap_replan_model.py` — marker/history persistence.
- `tests/services/test_roadmap_replan_compute.py` — single slip → `today+7`; downstream shift;
  diamond shifts by `max` not sum; no drift → `[]`; `done` milestone excluded from drift; reason
  templating (own vs. cascade vs. both).
- `tests/services/test_roadmap_replan_apply.py` — commits marker/history/event; stale id
  skipped; re-apply idempotent.
- `tests/api/test_roadmap_replan_api.py` — preview→apply happy path; mentor `403` on apply;
  no-drift empty preview.
- `tests/api/test_roadmap_replan_history.py` — history lists an applied replan.
- `e2e/test_roadmap_replan.py` (new) — live journey: onboard → force a slip via `PATCH
  .../milestones/{id} {due_on: today-10d}` → `preview` → `apply` a subset → `GET /roadmap`
  confirms new `due_on` + `replanned` marker + reduced `drift` → `history` lists the entry.
  Captures to `e2e/_captures/roadmap/replan_preview.json`, `replan_apply.json`,
  `replan_history.json`, `get_tree_replanned.json`.
- `e2e/test_smoke.py` — the three new routes added to the live OpenAPI path-presence assertion.

## Verification

- **Unit suite: 346 passed, 98% coverage** (`poetry run pytest -q`) — up from 304 at Slice 1
  (Slices 2+3 added the rest). `app/services/roadmap/replan.py` itself is 100% covered.
- `poetry run black --check .`, `poetry run isort --check .`, `poetry run ruff check .`,
  `poetry run mypy app` — all clean.
- **Live E2E: 26 passed** (`make e2e`, via Task 6's `1667781` — verified there, not re-run for
  this docs-only task per the task brief: another worktree may be using the shared e2e DB).
  `e2e/test_roadmap_replan.py::test_roadmap_replan_journey` exercises the full preview → apply →
  tree → history path against a real Postgres-backed server, plus the existing 25 prior journeys
  (Slice 1 + Slice 2 + smoke) still pass unchanged.
- Migration verified via that same `make e2e` run (fresh `cofoundaz_e2e` DB migrated
  `0001 → 0008`).

## Operate

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `make e2e` flow.
- **Rollback:** `alembic downgrade -1` drops the two `roadmap_milestones` marker columns and the
  `roadmap_replans` table entirely. This is **lossy** — any re-plan history and any milestone
  markers written while `0008` was applied are destroyed on downgrade (there is no meaningful
  "undo" for re-plan audit history once the schema storing it is removed), same lossy-by-design
  shape as every other brand-new-table migration in this project (`0004`, `0005`, `0006`).

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **AI-authored re-plan rationale** — v1 uses templated `reason` strings (own-slip / cascade /
  both). Natural-language, context-aware rationale is Module 03 (AI Co-Founder)'s scope, same
  deferral shape as Assessment's narrative and Health Score's recommendation copy.
- **Push notification on `roadmap.replanned`** — the event is emitted now with no consumer.
  Module 20 (Notifications) is the eventual subscriber for the PRD's *"I've drafted a re-plan for
  your review"* copy.
- **`roadmap.replan` job stays an unconsumed stub, permanently** — this is not a "not yet built"
  gap like the notification above; the spec explicitly rules out ever auto-draining it. Any
  future worker that processes `jobs` rows must skip this job type by design, not by oversight.
- **Phases and tasks are not shifted in v1** — only milestone `due_on` moves. A phase's own
  `starts_on`/`ends_on` can end up visually inconsistent with its shifted milestones until a
  future slice extends the cascade upward.
- **`_milestone_precedence()` queries all `roadmap_task_dependencies` rows unscoped by roadmap**
  (`replan.py:65`, `db.query(RoadmapTaskDependency).all()`) rather than filtering to the current
  roadmap's tasks first. Correctness is unaffected — the `task_ms` lookup (built from tasks
  scoped to *this* roadmap) silently drops any edge that isn't between two of this roadmap's own
  tasks — but it's a full-table scan on every `preview`/`apply` call that will not scale once
  many startups have large dependency graphs. Worth adding a `WHERE task_id IN (...)`-style scope
  once dependency graphs grow past trivial size.
- **`apply_replan`'s `assert m is not None`** (`replan.py:160`) is a defensive check, not proper
  error handling — it holds today because the proposal was just derived from a live query in the
  same call, but `assert` statements are stripped under Python's `-O` flag. Low risk at current
  scale; flag if this codebase ever runs with optimizations enabled, and consider replacing with
  an explicit internal-error raise if so.
- **`change_ids` in `POST /replan/apply` are not deduplicated** — `ReplanApply.change_ids` accepts
  a bare `list[uuid.UUID]`; a client that (accidentally, or via a double-tap race) sends the same
  id twice will see it appended to `applied` twice and get two identical entries in the
  `roadmap_replans` snapshot for one milestone, though the resulting `due_on`/marker state is
  still correct (idempotent at the data level, just not at the response/snapshot level). Cheap
  fix (`dict.fromkeys` dedup before the loop) if it turns out to matter for the FE's diff
  rendering; not exercised by any current test.
