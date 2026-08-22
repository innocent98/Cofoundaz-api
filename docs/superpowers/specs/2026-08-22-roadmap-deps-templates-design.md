# Design — Module 05 Roadmap · Slice 2 (Dependencies + Templates)

> **Status:** Approved (brainstorm) · **Date:** 2026-08-22 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 05 §05.4 Dependencies, §05.5 Templates),
> the UI handoff `../cofoundaz/app/(dashboard)/roadmap/page.tsx` (Dependencies + Templates views),
> and Slice 1 (`docs/superpowers/specs/2026-08-21-roadmap-core-design.md`, merged PR #7).
>
> Module 05 is 3 slices: **Slice 1 — Core** (merged), **Slice 2 — Dependencies + Templates** (this doc),
> **Slice 3 — AI Re-plan**. Each ships its own spec → plan → PR.

---

## 1. Scope (Slice 2)

**In scope** — (a) **task dependencies**: create/delete task→task edges with write-time cycle
detection, a dependency graph read, and populating the Slice-1 tree's `depends_on` /
`dependency_count` seams; (b) **template gallery**: a static curated catalog of named,
industry-tagged packs, browsable + previewable, with a non-destructive **apply** that appends a
pack to the existing roadmap and dedupes by template id.

**Deferred:**

| Deferred | To |
|---|---|
| AI Re-plan (drift → preview/apply diff), `roadmap.replan` consumer, `roadmap.replanned` | Slice 3 |
| Dependencies affecting scheduling (auto-shift a task when its dependency moves) | Slice 3 |
| Admin-editable templates (DB-backed) | future admin module |
| `roadmap.milestone.overdue` push event + notifications | Module 20 |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Gallery catalog model | **Separate static versioned `GALLERY_TEMPLATES` config**, distinct from Slice-1 `STAGE_TEMPLATES` (which is the auto-generation seed). Gallery packs are curated, named, industry-tagged, sometimes cross-stage. |
| 2 | Apply semantics | **Append + dedup by template id.** Apply adds the pack's phases/milestones/tasks (ordered after existing), dates from `apply_date + offsets`, nothing deleted. The roadmap records applied template ids; **re-applying the same template is a no-op `200 {already_applied: true}`** — no duplication. |
| 3 | Dependencies | task→task edges; `POST`/`DELETE` edge + dedicated `GET /roadmap/dependencies` graph; **write-time DFS cycle detection** (`409 DEPENDENCY_CYCLE`); **duplicate edge is idempotent `200`**; self-edge blocked by the table CHECK; populate the tree's `depends_on`/`dependency_count`. **No scheduling impact** in Slice 2. |

## 3. Data model (migration `0007_roadmap_applied_templates`)

- **Alter `roadmaps`** — add `applied_template_keys: JSONB` (nullable=False, server_default `'[]'`),
  the list of gallery template ids this roadmap has applied (dedup source for Decision #2).
- **`roadmap_task_dependencies`** — already created in Slice 1 (`0006`), never written until now.
  No schema change: `(task_id, depends_on_task_id)` composite PK, both FK→`roadmap_tasks`
  `ondelete=CASCADE`, CHECK `task_id <> depends_on_task_id`.
- **Templates are static config** — no table. `app/services/roadmap/gallery.py`:
  `GALLERY_TEMPLATE_VERSION`, `GALLERY_TEMPLATES`.

**Gallery catalog shape** (mirrors the FE gallery cards + reuses the stage-template phase shape):

```python
GALLERY_TEMPLATE_VERSION = 1
GALLERY_TEMPLATES = {
  "validation-sprint": {"id": "validation-sprint", "title": "Validation sprint",
     "stage": "validation", "category": "Fintech", "phases": [
        {"name": "Validation sprint", "start_week": 0, "end_week": 4, "milestones": [
           {"title": "Problem interviews", "due_week": 1, "tasks": [
              {"title": "Recruit 10 target users", "effort": "medium"},
              {"title": "Run and synthesize interviews", "effort": "medium"}]}, …]}]},
  "mvp-build":        {"id": "mvp-build", "title": "MVP build", "stage": "build", "category": "Fintech", "phases": [ … ]},
  "go-to-market":     {"id": "go-to-market", "title": "Go-to-market", "stage": "launch", "category": "B2C", "phases": [ … ]},
  "pre-seed-raise":   {"id": "pre-seed-raise", "title": "Pre-seed raise", "stage": None, "category": "General", "phases": [ … ]},
  "company-formation":{"id": "company-formation", "title": "Company formation", "stage": None, "category": "Nigeria", "phases": [ … ]},
  "scale-playbook":   {"id": "scale-playbook", "title": "Scale playbook", "stage": "scale", "category": "General", "phases": [ … ]},
}
```

`stage` may be `None` (cross-stage packs). Milestone/task **counts** are derived, not stored.

## 4. Dependencies

### 4.1 Cycle detection — `app/services/roadmap/dependencies.py`

`would_create_cycle(db, roadmap_id, task_id, depends_on_task_id) -> bool`: adding `task_id →
depends_on_task_id` (read "task depends on depends_on") closes a loop iff `depends_on_task_id`
can already reach `task_id` following existing edges in the **dependent → dependency** direction.
DFS/BFS from `depends_on_task_id` over `roadmap_task_dependencies` (scoped to the roadmap's tasks);
if it reaches `task_id`, reject. The graph is kept acyclic by construction (every accepted edge
preserves the DAG), so Slice 3's re-planner can assume acyclicity.

`add_dependency(db, dependent, depends_on) -> RoadmapTaskDependency | None`: returns the existing
row if the edge already exists (idempotent), else inserts. Callers do cycle + tenancy checks first.

### 4.2 Endpoints (`/api/v1/roadmap`)

| Route | Access | Behaviour |
|---|---|---|
| `POST /tasks/{task_id}/dependencies` `{depends_on_task_id}` | editor | Both tasks resolved to the caller's roadmap (foreign/unknown → `404`). `task_id == depends_on_task_id` → `422 VALIDATION_ERROR`. Cycle → `409 DEPENDENCY_CYCLE`. The rejected edge is *dependent depends on dependency*; it's rejected precisely because the **dependency already (transitively) depends on the dependent**, so the message names that existing relationship: *"That would create a loop — {dependency_title} already depends on {dependent_title}."* Existing edge → idempotent `200`. Else create → `201`. Returns `{task_id, depends_on_task_id}`. |
| `DELETE /tasks/{task_id}/dependencies/{depends_on_task_id}` | editor | Removes the edge (both scoped to the roadmap); unknown edge → `404`. `200 {deleted: true}`. |
| `GET /roadmap/dependencies` | member | `{ nodes: [{task_id, title, milestone_id, milestone_title, phase_id, phase_name}], edges: [{task_id, depends_on_task_id}], list: [{task, depends_on}] }` (`list` carries titles for the FE "X → depends on → Y" rows). |

### 4.3 Tree seam populated

`serialize_tree` (Slice 1) now fills, per one dependency query over the roadmap's tasks:
- each task's `depends_on: [depends_on_task_id, …]`;
- each milestone's `dependency_count` = number of edges whose **dependent** task belongs to that
  milestone. Done with a single grouped query, not per-task/per-milestone COUNTs (retires the
  Slice-1 no-op `dependency_count` query flagged in that SOP).

## 5. Templates

### 5.1 Endpoints (`/api/v1/roadmap`)

| Route | Access | Behaviour |
|---|---|---|
| `GET /roadmap/templates` | member | Gallery list: `[{id, title, stage, category, milestone_count, task_count}]` from `GALLERY_TEMPLATES` (+ an `applied: bool` flag per entry against the caller's roadmap). No filtering — all packs returned, labeled. |
| `GET /roadmap/templates/{id}` | member | Preview: `{id, title, stage, category, milestone_count, task_count, phases: [{name, milestones: [{title, tasks: [{title, effort}]}]}]}`. Unknown id → `404`. |
| `POST /roadmap/templates/{id}/apply` | editor | Ensure a roadmap exists (lazy-generate if missing, per Slice 1). If `id` already in `roadmaps.applied_template_keys` → **no-op** `200 {already_applied: true, added: {phases:0, milestones:0, tasks:0}}`. Else append the pack's phases (order after existing), milestones, tasks; dates from `today() + offsets`; append `id` to `applied_template_keys`; emit `roadmap.template.applied`; `201 {already_applied: false, added: {phases, milestones, tasks}}`. Unknown id → `404`. |

### 5.2 Apply merge mechanics

Same date scheme as generation: phases `starts_on/ends_on = today + week·7`, milestones
`due_on = today + due_week·7`, `status=todo`, `progress=0`, tasks `status=todo`, `effort` from
pack. New phases get `order = max(existing order) + 1, +2, …`. The pack's tasks carry **no
dependencies** (dependencies are user-authored, §4). Nothing existing is modified or deleted.

## 6. Events, errors

### 6.1 Events (via `event_bus.publish`)

| Event | When | Payload |
|---|---|---|
| `roadmap.template.applied` | a template is applied (not on the dedup no-op) | `{startup_id, roadmap_id, template_id, added: {phases, milestones, tasks}}` |

Dependency add/remove emit no events (too granular; not in the PRD's event list). `roadmap.replanned`
is Slice 3.

### 6.2 Errors (reuse taxonomy; one new code)

| Code | HTTP | When |
|---|---|---|
| `DEPENDENCY_CYCLE` | 409 | an edge would close a dependency loop |
| `NOT_FOUND` | 404 | unknown/cross-tenant task, edge, or template id |
| `VALIDATION_ERROR` | 422 | `depends_on_task_id == task_id` (self-edge); malformed body |
| `FORBIDDEN` | 403 | mentor (non-editor) attempting a write |
| `EMAIL_NOT_VERIFIED` | 403 | unverified user (shared guard) |

Uniform-404 for cross-workspace ids (no enumeration leak), consistent with Slice 1.

## 7. Access

Reads (`GET /roadmap/dependencies`, `GET /roadmap/templates`, `GET /roadmap/templates/{id}`) =
any active member. Writes (dependency `POST`/`DELETE`, template `apply`) = founder or team_member;
mentor read-only (per-route `403`). All require verified users. Same `require_role`/`require_workspace`
primitives as Slice 1.

## 8. Testing

- **TDD**, real Postgres + per-test rollback; add `create_dependency(db, dependent, depends_on)` factory.
- **Cycle detection:** direct cycle (A→B then B→A) rejected `409`; transitive cycle (A→B→C then C→A)
  rejected; a valid DAG (diamond A→B, A→C, B→D, C→D) accepted; self-edge `422`; duplicate edge
  idempotent `200`.
- **Graph read:** nodes/edges/list correct; tree `depends_on`/`dependency_count` populated after edges added.
- **Templates:** gallery list + counts + `applied` flag; preview shape; unknown id `404`.
- **Apply:** appends the right phase/milestone/task counts; `applied_template_keys` recorded;
  **re-apply is a no-op** `200 {already_applied:true}` (no duplication); emits `roadmap.template.applied`
  once (not on the no-op); lazy-generates if no roadmap.
- **Tenancy/access:** cross-workspace task/edge/apply → `404`; mentor write `403`; verified gate.
- **Live E2E** (`e2e/test_roadmap.py` extension): add a dependency between two real tasks → attempt a
  cycle (`409`) → `GET /roadmap/dependencies` shows the edge → apply a gallery template (tree grows by
  the pack's counts) → re-apply (`already_applied:true`, tree unchanged) → `GET /roadmap/templates`
  shows that pack `applied:true`.
- **FE integration guide** — update `docs/fe-integration-guide-roadmap.md` from live captures: the
  dependency endpoints (incl. the `409` cycle body), the graph shape, the gallery list + preview, the
  apply response (both fresh and `already_applied`), and the now-populated `depends_on`/`dependency_count`
  in the tree. Verification table.

## 9. Plan shape

One implementation plan (`writing-plans`), ~9 TDD tasks, subagent-driven (fresh implementer +
independent review + fix loop per task, then whole-branch review):

1. `GALLERY_TEMPLATES` catalog + counts helper + validation test
2. Migration `0007_roadmap_applied_templates` (`applied_template_keys` JSONB)
3. `dependencies.py` — `would_create_cycle` + `add_dependency` (+ cycle unit tests)
4. `POST`/`DELETE /roadmap/tasks/{id}/dependencies` endpoints
5. `GET /roadmap/dependencies` graph + populate `serialize_tree` `depends_on`/`dependency_count`
6. `GET /roadmap/templates` gallery + `GET /roadmap/templates/{id}` preview
7. `POST /roadmap/templates/{id}/apply` (append + dedup + `roadmap.template.applied`)
8. Live E2E extension + smoke surface
9. SOP + FE integration guide update + checklist reconcile
