# Design — Module 05 Roadmap · Slice 1 (Core)

> **Status:** Approved (brainstorm) · **Date:** 2026-08-21 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 05, §2.2 async convention line 195,
> §5.2 RBAC matrix), the UI handoff `../cofoundaz/app/(dashboard)/roadmap/page.tsx` (FE interfaces),
> and the shipped Health Score (`docs/superpowers/specs/2026-08-19-health-score-design.md`).
>
> Module 05 (Roadmap) is decomposed into **3 slices**, each its own spec → plan → build → PR:
> **Slice 1 — Core** (this doc), **Slice 2 — Dependencies + Templates**, **Slice 3 — AI Re-plan**.
> Modules 01/06/07 are merged to `main` (PRs #1–#6).

---

## 1. Scope (Slice 1)

**In scope** — the roadmap tree (`roadmaps → phases → milestones → tasks`) + the
`roadmap_task_dependencies` table (created here; its cycle-checked *write* is Slice 2);
stage-template-driven **generation** consuming the `roadmap.generate` stub inline; `GET /roadmap`
full tree; CRUD on phases/milestones/tasks; milestone "mark complete" as a tracked transition;
**derived** milestone `progress` and **derived** `overdue`; the `roadmap.generated` and
`roadmap.milestone.completed` events. Kanban and Timeline are the same tree behind FE filters — no
extra backend.

**Deferred:**

| Deferred | To |
|---|---|
| Task dependency *creation* + cycle detection, dependency graph read | Slice 2 |
| Template gallery (`GET /roadmap/templates`) + apply-merge (`POST …/{id}/apply`) | Slice 2 |
| Industry template variants (Fintech/B2C/Nigeria overlays) | Slice 2 gallery |
| AI Re-plan: drift detection, `POST /roadmap/replan/preview|apply`, `roadmap.replan` consumer | Slice 3 |
| `roadmap.milestone.overdue` **event** + "due soon / overdue" notifications (need a scheduler) | Module 20 |
| Per-module grants (TM-if-granted) + BC suggest-mode | future RBAC/grants module |
| AI task breakdown, AI re-plan rationale | Module 03 |
| Workspace-timezone base date for generation (v1 uses server date) | follow-up |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Generation source | **Stage-only static versioned template catalog**, one template per `StartupStage`; **idempotent create-once**. Industry variants deferred to Slice 2. |
| 2 | Access model | **Role-based v1** — `founder` + `team_member` edit; `mentor` read-only. Per-module grants + BC suggest-mode deferred. |
| 3 | `POST /roadmap/generate` | **Honors the `202`+job contract, runs inline** → job `succeeded` synchronously. No worker. |
| 4 | Milestone progress/status | **Derived `progress`** (from task completion), **explicit `status`** (Mark complete), **derived `overdue`** (`due_on < today && status ≠ done`, never stored). |
| 5 | Recompute model | **Inline + lazy-on-read** (no async worker) — same as Health Score. |

## 3. Data model (migration `0006_roadmap`)

Five tables, `roadmap_`-prefixed (bare `phases`/`milestones`/`tasks` are too generic to own
globally). Two new enums in `app/db/models/enums.py`:
`RoadmapStatus(todo|in_progress|done)` (shared by milestones + tasks), `TaskEffort(small|medium|large)`.

### `roadmaps` — one per startup

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | **unique**, indexed, `ondelete=CASCADE` |
| `stage` | `StartupStage` (Enum, native_enum=False) | stage generation used |
| `template_key` | String | e.g. `stage.validation` |
| `template_version` | Integer | catalog version stamped |
| `generated_at` | timestamptz | |
| (TimestampMixin) | | |

### `roadmap_phases`

`id` PK · `roadmap_id` FK→roadmaps (CASCADE, indexed) · `name` String · `order` Integer ·
`starts_on` Date? · `ends_on` Date?

### `roadmap_milestones`

`id` PK · `phase_id` FK→roadmap_phases (CASCADE, indexed) · `title` String · `description` Text? ·
`due_on` Date? · `owner_id` FK→users? · `status` `RoadmapStatus` (default `todo`) ·
`progress` Integer (default 0, **derived/denormalized**) · `order` Integer

### `roadmap_tasks`

`id` PK · `milestone_id` FK→roadmap_milestones (CASCADE, indexed) · `title` String ·
`description` Text? · `effort` `TaskEffort` (default `medium`) · `status` `RoadmapStatus`
(default `todo`) · `assignee_id` FK→users? · `due_on` Date? · `order` Integer

### `roadmap_task_dependencies` — created now, written in Slice 2

`task_id` FK→roadmap_tasks (CASCADE) · `depends_on_task_id` FK→roadmap_tasks (CASCADE) ·
**composite PK** (both) · CHECK `task_id <> depends_on_task_id`

**Notes:** `progress` is denormalized (stored) so `GET /roadmap` is a flat tree fetch, not N
subqueries; kept honest by `recompute_milestone_progress` on task changes (§4.4). `owner_id`/
`assignee_id` are nullable (templates don't assign people) and, when set, must be an **active
member** of the workspace (validated on write → `422`). Calendar `Date`, not timestamps.

## 4. Template catalog & generation

### 4.1 Catalog — `app/services/roadmap/templates.py` (static versioned config)

```python
ROADMAP_TEMPLATE_VERSION = 1
STAGE_TEMPLATES = {
  "validation": {"key": "stage.validation", "phases": [
     {"name": "Validation", "start_week": 0, "end_week": 6, "milestones": [
        {"title": "Validate demand", "due_week": 2, "tasks": [
           {"title": "Run 10 customer interviews", "effort": "medium"},
           {"title": "Synthesize problem hypotheses", "effort": "small"}]},
        {"title": "Pricing test", "due_week": 5, "tasks": [ … ]}]},
     {"name": "Build MVP", "start_week": 6, "end_week": 14, "milestones": [ … ]}]},
  "idea": { … }, "build": { … }, "launch": { … }, "growth": { … }, "scale": { … },
}
```

All six `StartupStage` values have a template. Offsets are **relative weeks**; no absolute dates
in the catalog.

### 4.2 `generate_roadmap(db, startup, *, actor) -> Roadmap`

Deterministic given a base date; runs in the **caller's transaction**:

1. **Create-once claim:** `INSERT INTO roadmaps (…) … ON CONFLICT (startup_id) DO NOTHING
   RETURNING id`. If **no id** returned → a concurrent path already generated → **load and return**
   the existing roadmap (build nothing). (Postgres blocks the second inserter on the uncommitted
   unique row until the winner commits, so the loser reads a fully-committed tree.)
2. `tmpl = STAGE_TEMPLATES[startup.stage.value]` (stage guaranteed by onboarding-complete;
   defensive fallback to `idea`).
3. `base = today()` (server date; workspace-tz deferred).
4. Phase (index=`order`): `starts_on = base + start_week·7d`, `ends_on = base + end_week·7d`.
5. Milestone: `due_on = base + due_week·7d`, `status=todo`, `progress=0`, `order=idx`.
6. Task: `effort` from template, `status=todo`, `order=idx`, `due_on=null` (optional in template).
7. Stamp `roadmaps.{template_key, template_version, stage, generated_at}`; flush the tree.
8. Emit `roadmap.generated` `{startup_id, roadmap_id, stage, template_key, milestone_count}`.

Generated roadmaps have **no dependencies** in v1 (dependency writes are Slice 2).

### 4.3 Wiring (three call sites, one service)

- **`complete_onboarding`** (`app/services/onboarding/complete.py:43`): replace the
  `roadmap.generate` stub enqueue with an **inline** `generate_roadmap(...)` in the completion
  transaction; still return a roadmap `job_id` (a `jobs` row created + marked `succeeded`) for FE
  parity.
- **`POST /roadmap/generate`**: create a `jobs` row → `generate_roadmap` → mark `succeeded` →
  return `202 {job_id, status:"succeeded"}`.
- **`GET /roadmap`**: if no roadmap, `generate_roadmap` (lazy), commit, return the tree.

### 4.4 Derived-progress helper

`recompute_milestone_progress(db, milestone)` = `round(100 · done_tasks / total_tasks)`, or `0`
with no tasks (or `100` if the milestone's own `status==done`). Called from task
create/patch/delete.

## 5. Endpoints & access

All under `/api/v1/roadmap`, all **verified**. **Reads = any active member** (founder/team_member/
mentor); **writes = founder or team_member** (mentor → `403`). Adds one dependency
`require_roles(founder, team_member)` beside the existing `require_role`/`require_workspace`.

| # | Route | Access | Behaviour |
|---|---|---|---|
| 1 | `GET /roadmap` | member | Full tree (below); **lazy-generates** if missing. |
| 2 | `POST /roadmap/generate` | editor | `202 {job_id, status:"succeeded"}`; inline; **create-once**. |
| 3 | `POST /roadmap/phases` | editor | Create phase. |
| 4 | `PATCH /roadmap/phases/{id}` | editor | Rename / reorder / dates. |
| 5 | `DELETE /roadmap/phases/{id}` | editor | Cascades. |
| 6 | `POST /roadmap/milestones` | editor | `{phase_id, title, …}`; `phase_id` in caller's roadmap. |
| 7 | `PATCH /roadmap/milestones/{id}` | editor | Edit incl. `status`; **`→done` emits `roadmap.milestone.completed`** (edge only). |
| 8 | `DELETE /roadmap/milestones/{id}` | editor | Cascades. |
| 9 | `POST /roadmap/tasks` | editor | `{milestone_id, title, effort?, …}`; recompute progress. |
| 10 | `PATCH /roadmap/tasks/{id}` | editor | Edit incl. `status`; status change recomputes progress. |
| 11 | `DELETE /roadmap/tasks/{id}` | editor | Recompute progress. |

**`GET /roadmap` tree shape** (FE contract, verified live later):
```json
{ "data": {
  "roadmap": {"id":"…","stage":"validation","template_key":"stage.validation","generated_at":"…"},
  "current_stage": "validation",
  "phases": [ {"id":"…","name":"Validation","order":0,"starts_on":"…","ends_on":"…",
    "milestones": [ {"id":"…","title":"Validate demand","due_on":"…",
      "owner": {"id":"…","name":"Amara"}, "status":"in_progress","progress":60,
      "overdue": false, "order":0, "dependency_count":0,
      "tasks": [ {"id":"…","title":"…","effort":"medium","status":"todo",
        "assignee": null, "due_on":"…","overdue": false,"order":0,"depends_on": []} ]} ]} ]}}
```
In Slice 1, `dependency_count` is always `0` and `depends_on` always `[]` (the dependency table
exists but is never written until Slice 2). Both fields are present now for a stable FE contract.

**Tenancy & validation:**
- Every `{id}` resolves through its parent chain to `roadmap.startup_id == membership.startup_id`;
  cross-workspace ids → **uniform `404`** (no enumeration leak). Helpers
  `_roadmap/_phase/_milestone/_task(db, membership, id)`.
- `phase_id`/`milestone_id` in create bodies must be in the caller's roadmap, else `404`.
- `owner_id`/`assignee_id`, when set, must be an active member, else `422`.
- New items append (`order = max(siblings)+1`) unless an explicit `order` is given.
- "Mark complete" is `PATCH milestone {status:"done"}`; the handler **detects the transition** and
  emits the event only on the edge (`done→done` is silent).

## 6. Events, errors, versioning

### 6.1 Events (via `event_bus.publish`, fire-and-forget)

| Event | When | Payload |
|---|---|---|
| `roadmap.generated` | generation writes a roadmap | `{startup_id, roadmap_id, stage, template_key, milestone_count}` |
| `roadmap.milestone.completed` | milestone `status` transitions **into** `done` (edge only) | `{startup_id, roadmap_id, milestone_id, title}` |

`roadmap.milestone.overdue` event + due-soon/overdue notifications need a scheduler → **Module 20**
(overdue is surfaced now as a derived tree flag). `roadmap.replanned` → Slice 3.

### 6.2 Errors (reuse `AppError` taxonomy — no new codes)

| Code | HTTP | When |
|---|---|---|
| `NOT_FOUND` | 404 | unknown/cross-tenant roadmap/phase/milestone/task id; foreign `phase_id`/`milestone_id` in a create body |
| `VALIDATION_ERROR` | 422 | bad `status`/`effort` enum; `owner_id`/`assignee_id` not an active member; malformed field |
| `FORBIDDEN` | 403 | mentor (non-editor) attempting a write |
| `EMAIL_NOT_VERIFIED` | 403 | unverified user (shared guard) |

Generation is idempotent (create-once) → raises nothing on repeat. The dependency-cycle error is
Slice 2.

### 6.3 Config versioning

`ROADMAP_TEMPLATE_VERSION` stamped on `roadmaps.template_version`. Editing the catalog = config
change + version bump; existing roadmaps keep their version and user edits; only future
generations use the new catalog.

## 7. Testing

- **TDD**, real Postgres + per-test rollback; factories `create_roadmap/create_phase/create_milestone/create_task`.
- **Generation:** each stage template → right phase/milestone/task counts; dates = base + offsets;
  version + `template_key` stamped; **create-once** (2nd call, no dup) + **two-connection
  concurrency test** on the `ON CONFLICT` claim.
- **Progress:** derived from done/total; recompute on task create/patch/delete; no-tasks → 0 (or
  100 if `done`).
- **Milestone complete:** PATCH `→done` emits once; `done→done` silent.
- **Overdue flag:** `due_on < today && status ≠ done` → `overdue:true`.
- **CRUD:** each level create/patch/delete; non-member `owner_id`/`assignee_id` → `422`; foreign
  `phase_id`/`milestone_id` → `404`; order appends.
- **Tenancy/access:** cross-workspace `404` on every resource; mentor read OK, mentor write `403`;
  verified gate; lazy-GET generation; `generate` → `202` + `succeeded` job.
- **Live E2E** (`e2e/test_roadmap.py`): founder onboards → completion **auto-generates** the stage
  roadmap inline → `GET /roadmap` returns a real stage tree → add phase/milestone/task → mark task
  `done` → milestone `progress` updates → mark milestone complete (event) → a **team_member** edits,
  a **mentor** gets `403` on write but reads → a second workspace blocked (`404`).
- **FE integration guide** (`docs/fe-integration-guide-roadmap.md`): every payload captured live —
  `GET /roadmap` (fresh + after edits), `generate` 202+job, each CRUD response, error shapes, the
  derived `overdue`/`progress` (with the "don't hand-edit progress" note), uniform-404 note;
  verification table.

## 8. Plan shape

One implementation plan (`writing-plans`), ~11 TDD tasks, subagent-driven (fresh implementer +
independent review + fix loop per task, then whole-branch review):

1. Enums (`RoadmapStatus`, `TaskEffort`) + 5 models + factories
2. Migration `0006_roadmap`
3. `require_roles(founder, team_member)` editor dep + tenancy resolvers (`_roadmap/_phase/_milestone/_task`)
4. Template catalog + `generate_roadmap` (create-once + race test) + `roadmap.generated`
5. `recompute_milestone_progress` + `GET /roadmap` (tree serialize + lazy generate)
6. `POST /roadmap/generate` (202 job) + wire inline into `complete_onboarding` (retire stub)
7. Phases CRUD
8. Milestones CRUD + mark-complete transition event
9. Tasks CRUD + progress recompute
10. Live E2E roadmap journey
11. SOP + FE integration guide + checklist reconcile
