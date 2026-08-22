# SOP — Roadmap Dependencies + Templates (Module 05, Slice 2)

**What shipped** — Two additions layered onto Slice 1's phase → milestone → task tree, on
branch `feat/roadmap-deps-templates` (Tasks 1–9, not yet merged to `main`):

1. **Task dependencies.** `POST`/`DELETE /roadmap/tasks/{id}/dependencies` create/remove a
   directed edge ("this task depends on that task"), guarded by write-time DFS cycle detection
   so the dependency graph stays a DAG. `GET /roadmap/dependencies` returns the full graph
   (`nodes`/`edges`/`list`). `GET /roadmap`'s tree now actually populates `depends_on` (per
   task) and `dependency_count` (per milestone) — both were always-empty placeholders in
   Slice 1.
2. **Template gallery.** A separate, opt-in catalog of 6 named, industry-tagged templates
   (`GALLERY_TEMPLATES`) a founder can browse (`GET /roadmap/templates`, `GET
   /roadmap/templates/{id}`) and layer onto their *existing* roadmap on demand (`POST
   /roadmap/templates/{id}/apply`) — additive, append-only, and idempotent per template id via
   a new `applied_template_keys` JSONB column (migration `0007_roadmap_applied_templates`).

Commits: `dc4a75b`..`5354ffa` (Tasks 1–8) + this task's SOP/FE-guide/checklist commit (Task 9).

## Why

Slice 1 shipped the tree and created `roadmap_task_dependencies` but nothing wrote to it, and
every founder's roadmap came from exactly one auto-generated stage template with no way to add
more structure later without hand-building phases one at a time. Slice 2 closes both gaps:
founders can now express "this can't start until that's done" (blocking relationships that
matter for planning even though nothing *enforces* them yet — see How), and can layer a
named, curated template (e.g. "Pre-seed raise") onto a roadmap that already has custom content,
without the apply wiping or reordering anything that exists.

## How

**DFS reachability keeps the dependency graph a DAG, at write time, not via a DB constraint.**
`would_create_cycle()` (`app/services/roadmap/dependencies.py`) walks the *existing* edges in
`dependency_map()` — `{dependent_task_id: [dependency_task_id, ...]}`, one query per check,
scoped to the caller's own roadmap via a `RoadmapTask` → `RoadmapMilestone` → `RoadmapPhase`
join filtered by `roadmap_id` — starting from the proposed edge's `depends_on_task_id` and
searching for `task_id`. If found, adding the new edge would close a loop, so the endpoint
raises before the `INSERT`. This is a plain iterative DFS with an explicit stack and `seen` set
(no recursion depth risk, no library), and it's a check-then-insert — not a table constraint —
because a DAG-acyclicity invariant can't be expressed as a Postgres `CHECK` (see Follow-ups for
the race this implies under concurrent writers).

**Dependency direction is fixed and asymmetric everywhere.** The edge `(task_id,
depends_on_task_id)` always means "`task_id` depends on `depends_on_task_id`." `add_dependency()`
is idempotent by pre-check (`SELECT ... WHERE task_id=... AND depends_on_task_id=...` before
`INSERT`) — creating the same edge twice returns the existing row and `created=False`, and the
endpoint (`app/api/v1/endpoints/roadmap.py:create_dependency_ep`) turns that into `200` vs `201`
via `JSONResponse(status_code=(201 if created else 200), ...)` rather than raising. A task
depending on itself is rejected as a `422 VALIDATION_ERROR` shape error *before* the cycle check
runs (self-dependency is always a 1-node cycle, but it's cheap to catch directly rather than
paying for a DFS to discover the trivial case).

**`DependencyCycle` is raised with the `message=` keyword, not positional.** `AppError.__init__`
takes `code`, `message`, `http_status`, `field_errors` all as optional overrides of the
subclass's class attributes; `DependencyCycle` (`app/core/errors.py`) fixes `code =
"DEPENDENCY_CYCLE"` and `http_status = 409` as class attributes but leaves `message` generic.
The endpoint raises `DependencyCycle(message=f"That would create a loop — {dependency.title}
already depends on {dependent.title}.")` — passing `message=` by keyword means only the message
overrides, and `code`/`http_status` fall through to the class defaults untouched. Had this been
passed positionally as the first arg, it would have silently overwritten `code` instead (the
first positional param on `AppError.__init__` is `code`, not `message`) — a real footgun this
class's signature invites, worth flagging for any future subclass of `AppError`.

**`GET /roadmap/dependencies` and the tree both build on the same `dependency_map()` — the tree
does it in a single fetch, not per-milestone.** Slice 1's SOP flagged `dependency_count` as a
"per-milestone `COUNT` that is always 0" — a real N+1 query shape kept only for forward
compatibility. Slice 2 retires that: `serialize_tree()` (`app/services/roadmap/service.py:201`)
now calls `dependency_map()` exactly **once** per `GET /roadmap` and reuses the same dict for
every milestone's `dependency_count` (`sum(1 for t in tasks if t.id in dep_map)`) and every
task's `depends_on` (`[str(d) for d in dep_map.get(t.id, [])]`) — one query for the whole tree
regardless of how many milestones or tasks it has.

**Template apply is append-only, and dedup is a list-reassignment, not a mutation, because JSONB
dirty-tracking needs a new object.** `apply_template()` (`service.py:120`) always appends
unconditionally — the caller (`apply_template_ep`) checks `template_id in
roadmap.applied_template_keys` *before* calling it, short-circuiting to the idempotent `200`
response without touching the DB write path at all. On a genuine first apply, the new phases are
appended starting at `_max_phase_order(db, roadmap.id) + 1` (never before or between existing
phases), with milestone/task `due_on`/`starts_on`/`ends_on` computed from `date.today()` at
apply time (not baked into the static catalog). The applied-key write itself is `roadmap.
applied_template_keys = [*roadmap.applied_template_keys, tmpl["id"]]` — a **reassignment**
to a brand-new list, not `roadmap.applied_template_keys.append(tmpl["id"])`. SQLAlchemy's
change-tracking for a plain JSONB column watches for attribute *assignment*; an in-place
`.append()` on the existing Python list object would leave the attribute's identity unchanged
and the ORM would never flag the row dirty, so the write would silently vanish on `commit()`.

**Migration `0007` is a single `ADD COLUMN ... NOT NULL DEFAULT '[]'` on a low-row table.**
`applied_template_keys` (JSONB, `server_default='[]'`) is a metadata-only catalog change on
Postgres 11+ (no table rewrite for a constant default), further cheapened by `roadmaps` having
at most one row per startup. `ACCESS EXCLUSIVE` lock scoped to `roadmaps` only, sub-second in
practice — no `ADD-nullable → backfill → SET NOT NULL` split needed since the default isn't
computed from existing data.

**Access mirrors Slice 1's split exactly, no new role logic.** `GET /roadmap/dependencies`,
`GET /roadmap/templates`, `GET /roadmap/templates/{id}` all gate on `require_workspace` (any
active member, including mentor, read-only); the two write routes (`POST`/`DELETE
.../dependencies`, `POST .../apply`) gate on the same `_editor = require_role(founder,
team_member)` every other roadmap write route uses. Cross-tenant task ids on the dependency
routes 404 through the same `_task()` roadmap-scoped lookup Slice 1 established — no new
tenancy-guard code was needed.

## What's involved

**Data model / migration**
- `alembic/versions/0007_roadmap_applied_templates.py` — `ADD COLUMN roadmaps.
  applied_template_keys JSONB NOT NULL DEFAULT '[]'`. Autogenerated from the Task 1 model
  change; only revision id/down_revision/docstring hand-edited.
- `app/db/models/roadmap.py:42` — `Roadmap.applied_template_keys: Mapped[list]`.

**Endpoints** (all under `/api/v1/roadmap`, 6 new — 17 total for Module 05, ~57 project-wide)

| Method | Path | Auth | File |
|---|---|---|---|
| POST | `/api/v1/roadmap/tasks/{task_id}/dependencies` | founder or team_member | `app/api/v1/endpoints/roadmap.py:create_dependency_ep` |
| DELETE | `/api/v1/roadmap/tasks/{task_id}/dependencies/{depends_on_task_id}` | founder or team_member | `.../delete_dependency_ep` |
| GET | `/api/v1/roadmap/dependencies` | any active member | `.../get_dependencies` |
| GET | `/api/v1/roadmap/templates` | any active member | `.../list_templates` |
| GET | `/api/v1/roadmap/templates/{template_id}` | any active member | `.../preview_template` |
| POST | `/api/v1/roadmap/templates/{template_id}/apply` | founder or team_member | `.../apply_template_ep` |

**Services / schemas**
- `app/services/roadmap/dependencies.py` — `dependency_map`, `would_create_cycle`,
  `add_dependency`.
- `app/services/roadmap/gallery.py` — `GALLERY_TEMPLATES` (6 templates: `validation-sprint`,
  `mvp-build`, `go-to-market`, `pre-seed-raise`, `company-formation`, `scale-playbook`),
  `template_counts`.
- `app/services/roadmap/service.py` — `apply_template` (new); `serialize_tree` (modified to
  fetch `dependency_map` once and populate `depends_on`/`dependency_count`).
- `app/schemas/roadmap.py` — `DependencyCreate`.
- `app/core/errors.py` — `DependencyCycle` (`code="DEPENDENCY_CYCLE"`, `http_status=409`).
- Event: `roadmap.template.applied` published in `apply_template_ep` on a fresh (non-noop)
  apply, before `db.commit()` (see Follow-ups).

**Tests**
- `tests/services/test_roadmap_dependencies.py`, `test_roadmap_gallery.py`,
  `test_roadmap_templates.py`, `test_roadmap_apply_template.py`.
- `tests/api/test_roadmap_dependencies_api.py`, `test_roadmap_dependency_graph.py`,
  `test_roadmap_templates_gallery.py`, `test_roadmap_apply_api.py`.
- `tests/db/test_roadmap_applied_templates.py`.
- `e2e/test_roadmap.py` (extended) — dependency create/cycle/graph + template
  list/preview/apply/noop-apply folded into the same live journey as Slice 1, capturing to
  `e2e/_captures/roadmap/{dependency_create,dependency_cycle,dependencies_graph,
  get_tree_with_deps,templates_list,template_preview,template_apply,
  template_apply_noop}.json`.

## Verification

- **Unit suite: 328 passed** (`poetry run pytest -q`) — up from Slice 1's 304 (+24 across the
  8 new test files above).
- **Live E2E: 25 passed** (`make e2e`) — same 25 as Slice 1's count; the new dependency/template
  assertions were folded into the existing `test_roadmap_journey` rather than adding new test
  functions, per Task 8's scope.
- `poetry run black --check .`, `poetry run isort --check .`, `poetry run ruff check .`,
  `poetry run mypy app` — all clean.
- Migration verified via every `make e2e` run (fresh `cofoundaz_e2e` DB migrated from zero,
  `0006_roadmap` → `0007_roadmap_applied_templates`, each run).

## Operate

- No new env vars or deploy steps beyond the existing `make e2e` / `alembic upgrade head` flow.
- **Rollback:** `alembic downgrade -1` drops `applied_template_keys` from `roadmaps`. This is
  **lossy** — any workspace that applied one or more gallery templates loses the record of
  which ones, though the phases/milestones/tasks those applies created are untouched (they live
  in the Slice 1 tables, not this column) and will simply appear un-attributed to any template
  going forward. A workspace could re-apply an already-applied template post-downgrade/
  re-upgrade without the system recognizing it as a duplicate — cosmetic (harmless duplicate
  content), not destructive.

## Follow-ups

**Deferred, by design (not oversights):**
- **`add_dependency` check-then-insert, and the apply endpoint's check-then-append, both race
  under true concurrency** — neither takes a row lock. Two concurrent
  `POST .../dependencies` calls creating the *same* edge could both pass the pre-existence
  check and both `INSERT`, relying on nothing to prevent a duplicate row (there's no unique
  constraint on `(task_id, depends_on_task_id)` beyond the composite PK, which *would* catch an
  exact duplicate as a DB error rather than a silent double-write — but two concurrent
  `POST /templates/{id}/apply` calls for the *same* template on the *same* roadmap could both
  read `applied_template_keys` as not-yet-containing the id, both call `apply_template()`, and
  both append the full phase tree, doubling the content with no error at all). Benign under
  single-user editing (the actual usage pattern today — one founder, one tab), and the same
  class of gap as Slice 1's `_next_order` (`max(order)+1` with no uniqueness constraint) — worth
  a `SELECT ... FOR UPDATE` on the roadmap row if/when multi-editor concurrent use becomes real.
- **`roadmap.template.applied` has no consumer until Module 20.** Published exactly like Slice
  1's `roadmap.generated` / `roadmap.milestone.completed` — logged by the in-process
  `LogEventBus`, nothing subscribes yet.
- **Events publish before `db.commit()`** — `roadmap.template.applied` (`apply_template_ep`,
  `roadmap.py:342`) fires before the `db.commit()` on line 351, same known pattern flagged in
  Slice 1's SOP for `roadmap.generated`/`roadmap.milestone.completed`. Harmless with the current
  synchronous in-process bus; move to an after-commit hook once a real at-least-once bus lands
  (Module 20) so a failed commit can't emit a phantom event.
- **Slice 3 — AI Re-plan** remains: drift detection, `POST /roadmap/replan/preview`, `POST
  /roadmap/replan/apply {change_ids[]}` (consuming the `roadmap.replan` job Assessment already
  enqueues), `roadmap.replanned` event. Out of scope here.
- **Dependency-aware scheduling** (e.g. warning a founder when they mark a task `done` while an
  upstream dependency is still `todo`, or auto-suggesting an order that respects the graph) is
  not built — Slice 2 stores and exposes the graph but enforces nothing. A task's `status` can
  be flipped to `done` regardless of its `depends_on` state, and a milestone's own `status` is
  independent of both its tasks' completion and its `dependency_count`. This is a deliberate
  scope cut (the founder authors the graph; nothing polices it yet), not a bug.
- **`_task_out` (single-task CRUD responses) still hardcodes `depends_on: []`** — it was not
  wired to `dependency_map()` in this slice (only `serialize_tree` was). A `POST
  /roadmap/tasks` or `PATCH /roadmap/tasks/{id}` response will show `depends_on: []` even for a
  task with real dependency edges; only `GET /roadmap` and `GET /roadmap/dependencies` reflect
  the graph. Flagged prominently in the FE guide as a field-nesting trap; candidate for a fix
  once a caller actually needs the single-task shape to carry real dependencies.
