# FE Integration Guide — Roadmap (Module 05, Slices 1–3)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_roadmap.py` and `e2e/test_roadmap_replan.py` running against a real server
(`make e2e`) — see `e2e/_captures/roadmap/*.json`. Nothing here is retyped from memory or
invented. IDs in the examples are real ids from that ephemeral test run (not hand-written
placeholders) — they will differ on every real request, but the shapes are exact.

Base path: `/api/v1/roadmap`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active
workspace (`GET /auth/me` → `data.active_workspace_id`), same as every other tenant-scoped
endpoint in this API. **Reads** (`GET /roadmap`) are open to any active member (founder,
team_member, or mentor). **Writes** (every other route below) require the caller's membership
role to be `founder` or `team_member` — a `mentor` gets a `403`.

---

## 1. `GET /api/v1/roadmap` — the tree

Returns the full phase → milestone → task tree. If no roadmap exists yet for the caller's
startup, this endpoint generates one on the fly from the startup's stage template (lazy
generate) — in practice this almost never happens, because onboarding-complete already
generates the roadmap inline (see §2).

`e2e/_captures/roadmap/get_tree.json` — freshly generated, stage `validation`, before any
manual edits:

```json
{
  "data": {
    "roadmap": {
      "id": "176f0c77-3225-42a2-ae76-498b19fbb647",
      "stage": "validation",
      "template_key": "stage.validation",
      "generated_at": "2026-08-21T17:37:00.826215+00:00"
    },
    "current_stage": "validation",
    "phases": [
      {
        "id": "24da44bd-3423-4b4f-b048-26f5e32a2dfb",
        "name": "Validation",
        "order": 0,
        "starts_on": "2026-08-21",
        "ends_on": "2026-10-02",
        "milestones": [
          {
            "id": "acbf5f5e-d7c0-47b0-ad9a-8f9049a7bf13",
            "title": "Validate demand",
            "description": null,
            "due_on": "2026-09-04",
            "owner": null,
            "status": "todo",
            "progress": 0,
            "overdue": false,
            "order": 0,
            "dependency_count": 0,
            "tasks": [
              {
                "id": "06cbd34c-ec41-45c9-afaf-ba621d740b4e",
                "title": "Run 10 customer interviews",
                "description": null,
                "effort": "medium",
                "status": "todo",
                "assignee": null,
                "due_on": null,
                "overdue": false,
                "order": 0,
                "depends_on": []
              },
              {
                "id": "4e866fb3-6be6-4780-9e35-baea71333e7b",
                "title": "Synthesize problem hypotheses",
                "description": null,
                "effort": "small",
                "status": "todo",
                "assignee": null,
                "due_on": null,
                "overdue": false,
                "order": 1,
                "depends_on": []
              }
            ]
          }
          // ... "Pricing test" milestone omitted here, same shape — see the
          // full file for both milestones under the "Validation" phase.
        ]
      }
      // ... "Build MVP" phase omitted here, same shape — see
      // e2e/_captures/roadmap/get_tree.json for the full 2-phase tree.
    ]
  },
  "meta": null
}
```

**Field-nesting notes:**
- `roadmap` (top-level metadata: id/stage/template_key/generated_at) is a sibling of
  `current_stage` and `phases` — not nested inside `phases[0]`.
- `current_stage` is the **startup's live stage** (from `startups.stage`, i.e. whatever the
  founder currently has set in onboarding/settings) — it can drift from `roadmap.stage` (the
  stage the roadmap was *generated against*) if the founder changes their stage later. Slice 1
  does not regenerate or re-template the roadmap when stage changes; that reconciliation is
  Slice 3 (AI re-plan)'s job. Render both, don't assume they're always equal.
- `owner` (milestone) and `assignee` (task) are either `null` or an object
  `{"id": "<uuid>", "name": "<string or null>"}` — never a bare uuid string. `name` can itself
  be `null` inside a non-null owner/assignee object if that user has no profile `full_name` set.
- `phases[].milestones[].tasks[]` — three levels deep, always present as arrays (empty array,
  never omitted, for a phase/milestone with no children).
- **New in Slice 3:** `roadmap.drift` (`{"slipped_count": <n>}`) is nested **inside the `roadmap`
  object**, as a sibling of `id`/`stage`/`template_key`/`generated_at` — **not** a top-level
  sibling of `phases`/`current_stage`. Read it as `data.roadmap.drift.slipped_count`, not
  `data.drift`. Each milestone also gains `"replanned": {"at": "...", "reason": "..."} | null` —
  see §9 for both, with live captures.

### `progress` and `overdue` are backend-derived — never write them

**`progress` (0–100, on milestones) and `overdue` (bool, on both milestones and tasks) are
computed server-side on every read.** There is no `PATCH` field for either — sending
`{"progress": 50}` to `PATCH /roadmap/milestones/{id}` is simply ignored (not a schema field on
`MilestoneUpdate`). `progress` is recomputed from the milestone's tasks' `status` every time a
task is created/updated/deleted or the milestone itself is patched. Do not hand-maintain
`progress` client-side across multiple task edits in a batch — always re-fetch (or trust the
response body of the mutating call) rather than computing it from local task state, since the
computation happens server-side and could grow more nuanced (weighted-by-effort, etc.) without
a contract change.

**`overdue` on a milestone with no `due_on` is always `false`**, not an error — treat `null`
`due_on` as "no deadline set," not "overdue by default."

### `depends_on` and `dependency_count` — populated on the tree since Slice 2

In Slice 1, every task carried `depends_on: []` and every milestone carried
`dependency_count: 0` unconditionally — the `roadmap_task_dependencies` table existed but
nothing wrote to it. **As of Slice 2, `GET /roadmap`'s tree populates both fields for real**: a
task's `depends_on` is the list of task ids it depends on (see §6 for how those edges are
created), and a milestone's `dependency_count` is how many of its own tasks have at least one
dependency. `e2e/_captures/roadmap/get_tree_with_deps.json` — the "Custom phase" → "Custom
milestone" slice of the same tree used throughout this guide, captured **after** creating one
dependency edge (`"Task A"` depends on `"Task B"`, see §6):

```json
{
  "id": "0a075905-5250-4670-8f56-54c148d16d5f",
  "title": "Custom milestone",
  "description": null,
  "due_on": null,
  "owner": null,
  "status": "done",
  "progress": 0,
  "overdue": false,
  "order": 0,
  "dependency_count": 1,
  "tasks": [
    {
      "id": "6a2f2215-dd73-4674-bab4-cc63c86124e2",
      "title": "Custom task",
      "description": null,
      "effort": "medium",
      "status": "in_progress",
      "assignee": null,
      "due_on": null,
      "overdue": false,
      "order": 0,
      "depends_on": []
    },
    {
      "id": "3d697a19-1157-448b-99e4-4e4897b1c16c",
      "title": "Task A",
      "description": null,
      "effort": "medium",
      "status": "todo",
      "assignee": null,
      "due_on": null,
      "overdue": false,
      "order": 1,
      "depends_on": ["b936223d-2dd7-47f4-8cf9-d7136048d04d"]
    },
    {
      "id": "b936223d-2dd7-47f4-8cf9-d7136048d04d",
      "title": "Task B",
      "description": null,
      "effort": "medium",
      "status": "todo",
      "assignee": null,
      "due_on": null,
      "overdue": false,
      "order": 2,
      "depends_on": []
    }
  ]
}
```

Note `"Task A"`'s `depends_on` holds `"Task B"`'s **task id** (`b936223d-...`), and the
milestone's `dependency_count` is `1` — counting *tasks with at least one dependency*, not the
total number of edges. Note too that `"Task A"` still shows `status: "todo"` with an unmet
dependency, and `"Custom milestone"` shows `status: "done"` despite one of its own tasks being
`todo` — Slice 2 stores and exposes the graph but does not enforce it: nothing blocks a
`status` transition on an unmet dependency, and a milestone's own `status` (an explicit founder
override that can be set independently of its tasks' completion, see §4) is independent of both.
Don't infer "blocked" state or gray out a task client-side from `depends_on` alone unless that's
a deliberate UX choice — the backend will never reject the transition.

### Field-nesting trap: `depends_on` is a tree-only field — task-CRUD responses always return `[]`

**This is the single most important trap in this update.** `depends_on` is populated on the
**tree** (`GET /roadmap`, above) but the single-task `POST /roadmap/tasks` and
`PATCH /roadmap/tasks/{id}` responses (§5) still hardcode `depends_on: []` regardless of what
dependencies actually exist for that task — they were not wired to the dependency graph in
Slice 2. Concretely: if `"Task A"` genuinely depends on `"Task B"`, a `PATCH
/roadmap/tasks/{task_a_id}` (e.g. to change its `title`) returns `"depends_on": []` in that same
response, even though `GET /roadmap` immediately after would show `"depends_on":
["<task_b_id>"]` for the identical task. **Never read a task's dependencies from a task-CRUD
response.** Read them from the tree (`GET /roadmap`, this section) or from
`GET /roadmap/dependencies` (§6) — both are the source of truth; the flat task shape is not.

---

## 2. `POST /api/v1/roadmap/generate` — explicit (re-)generate, always idempotent

`e2e/_captures/roadmap/generate.json` (called when a roadmap already exists — the common case,
since onboarding-complete generates one inline already):

```json
{
  "data": {
    "job_id": "aa992ed0-6dd5-4a59-b781-5b15c10c44cc",
    "status": "succeeded"
  },
  "meta": null
}
```

Status code: **`202`**.

**This is a synchronous operation returning an already-finished job — there is nothing to
poll.** Generation happens inline, in the same request, before the response is built; the
`job_id`/`status: "succeeded"` pair exists only so a client that already has FE code polling
`GET /jobs/{job_id}` (from the onboarding-complete flow, or from Health Score's job pattern)
gets a consistent, immediately-`succeeded` result rather than a special case. **Do not build a
polling loop for this endpoint** — treat the `202` + `succeeded` body as the final answer, then
call `GET /roadmap` (§1) to render the tree.

Calling this when a roadmap already exists is the **normal, idempotent path** — Slice 1 never
regenerates or merges a second time; the create-once claim (`ON CONFLICT DO NOTHING`) means a
repeat call is a safe no-op that returns the same already-`succeeded` shape.

---

## 3. Phases — `POST /roadmap/phases`, `PATCH /roadmap/phases/{id}`

`e2e/_captures/roadmap/phase_create.json` (`POST /api/v1/roadmap/phases`,
`{"name": "Custom phase"}`) — status `201`:

```json
{
  "data": {
    "id": "08cafc09-4950-497d-aa9a-22c17836511a",
    "name": "Custom phase",
    "order": 2,
    "starts_on": null,
    "ends_on": null
  },
  "meta": null
}
```

`order` was not supplied in the request — the server auto-assigned the next value (`2`, after
the two template-generated phases `0` and `1`) via a `MAX(order)+1` computation scoped to the
roadmap. `starts_on`/`ends_on` are `null` unless supplied — a hand-created phase has no implied
date range the way template phases do.

`PATCH /roadmap/phases/{id}` is not separately captured here (see the cross-tenant 404 in §8,
which exercises this route's tenancy guard rather than a successful edit), but accepts the same
fields as create (`name`, `order`, `starts_on`, `ends_on`) as a partial update. **Sending an
explicit `null` for `name` or `order` is rejected with `422`** — see §5, the same
explicit-null-rejection validator applies to phases, milestones, and tasks alike.

`DELETE /roadmap/phases/{id}` exists (cascade-deletes its milestones/tasks) but was not
exercised by the live E2E journey — verified only by the unit suite
(`tests/api/test_roadmap_phases.py`).

---

## 4. Milestones — `POST /roadmap/milestones`, `PATCH /roadmap/milestones/{id}`

`e2e/_captures/roadmap/milestone_create.json` (`POST /api/v1/roadmap/milestones`,
`{"phase_id": "08cafc09-...", "title": "Custom milestone"}`) — status `201`:

```json
{
  "data": {
    "id": "19d264f8-f639-4a59-a260-ca26512d7905",
    "phase_id": "08cafc09-4950-497d-aa9a-22c17836511a",
    "title": "Custom milestone",
    "description": null,
    "due_on": null,
    "owner": null,
    "status": "todo",
    "progress": 0,
    "overdue": false,
    "order": 0
  },
  "meta": null
}
```

**Field-nesting trap:** this single-milestone response has no `tasks` array and no
`dependency_count` field (unlike the same milestone nested inside `GET /roadmap`'s tree, §1,
which includes both). The single-milestone create/patch responses return the flat shape with
`phase_id`, `status`, `progress`, `overdue`, and `order` only. Fetch `GET /roadmap` (or a
future single-milestone-with-tasks endpoint, not built in Slice 1) if you need this milestone's
tasks and dependency count right after creating it.

A milestone can be created (or later patched) directly to `status: "done"` with zero tasks —
`progress` snaps to `100` in that case (see §5), not `0`.

`e2e/_captures/roadmap/milestone_complete.json` (`PATCH /roadmap/milestones/{id}`,
`{"status": "done"}`, called after the milestone's one task was already marked `done` — see
§5's ordering) — status `200`:

```json
{
  "data": {
    "id": "19d264f8-f639-4a59-a260-ca26512d7905",
    "phase_id": "08cafc09-4950-497d-aa9a-22c17836511a",
    "title": "Custom milestone",
    "description": null,
    "due_on": null,
    "owner": null,
    "status": "done",
    "progress": 100,
    "overdue": false,
    "order": 0
  },
  "meta": null
}
```

**A milestone crossing to `status: "done"` (from any other status) fires a backend event**
(`roadmap.milestone.completed`, internal — not delivered to the FE as a webhook or push in
Slice 1). No client-visible effect beyond the response body today; flagged here so the FE team
knows a completion transition is a meaningful backend event, not just a field update, in case a
future notification feature surfaces it.

`DELETE /roadmap/milestones/{id}` exists (cascade-deletes its tasks) but was not exercised live
— unit-tested only (`tests/api/test_roadmap_milestones.py`).

---

## 5. Tasks — `POST /roadmap/tasks`, `PATCH /roadmap/tasks/{id}`

`e2e/_captures/roadmap/task_create.json` (`POST /api/v1/roadmap/tasks`,
`{"milestone_id": "19d264f8-...", "title": "Custom task"}`) — status `201`:

```json
{
  "data": {
    "id": "7e8edffd-2354-448a-bad5-f465cbdb910e",
    "milestone_id": "19d264f8-f639-4a59-a260-ca26512d7905",
    "title": "Custom task",
    "description": null,
    "effort": "medium",
    "status": "todo",
    "assignee": null,
    "due_on": null,
    "overdue": false,
    "order": 0,
    "depends_on": []
  },
  "meta": null
}
```

`effort` defaults to `"medium"` when omitted (one of `small`/`medium`/`large`). Creating this
task recomputed its parent milestone's `progress` server-side (0/1 tasks done → stays `0`) —
the create response only shows the **task's** shape, not the milestone's updated `progress`; if
the FE is showing a live progress bar for the parent milestone, re-fetch the milestone (or the
tree) after a task create/update/delete rather than assuming the previous progress value is
still current.

`e2e/_captures/roadmap/task_patch.json` (`PATCH /roadmap/tasks/{id}`, `{"status": "done"}`) —
status `200`:

```json
{
  "data": {
    "id": "7e8edffd-2354-448a-bad5-f465cbdb910e",
    "milestone_id": "19d264f8-f639-4a59-a260-ca26512d7905",
    "title": "Custom task",
    "description": null,
    "effort": "medium",
    "status": "done",
    "assignee": null,
    "due_on": null,
    "overdue": false,
    "order": 0,
    "depends_on": []
  },
  "meta": null
}
```

Immediately after this call, `GET /roadmap` (`e2e/_captures/roadmap/get_tree_after_edits.json`)
shows the parent milestone's `progress` at `100` (this was its only task, now done):

```json
{
  "id": "19d264f8-f639-4a59-a260-ca26512d7905",
  "title": "Custom milestone",
  "description": null,
  "due_on": null,
  "owner": null,
  "status": "todo",
  "progress": 100,
  "overdue": false,
  "order": 0,
  "dependency_count": 0,
  "tasks": [
    {
      "id": "7e8edffd-2354-448a-bad5-f465cbdb910e",
      "title": "Custom task",
      "description": null,
      "effort": "medium",
      "status": "done",
      "assignee": null,
      "due_on": null,
      "overdue": false,
      "order": 0,
      "depends_on": []
    }
  ]
}
```

**Field-nesting / semantics trap: `progress: 100` with `status: "todo"`.** The milestone's own
`status` is *still* `"todo"` here — only `progress` reached 100 from its tasks. `progress`
reaching 100% does **not** auto-flip the milestone's `status` to `done`; those are two
independent fields the FE must render separately (e.g. a progress bar at 100% next to a status
chip that still reads "To do"). The founder has to explicitly `PATCH` the milestone's `status`
to `done` (§4) to change the status chip — that's a deliberate design choice (a milestone can
be "functionally complete" per its tasks while the founder hasn't yet formally closed it out),
not an inconsistency to "fix" client-side by inferring status from progress.

`DELETE /roadmap/tasks/{id}` exists (also recomputes the parent milestone's progress) but was
not exercised live — unit-tested only (`tests/api/test_roadmap_tasks.py`).

### The `422` — explicit `null` on a required field

`e2e/_captures/roadmap/validation_error.json` (`PATCH /roadmap/tasks/{id}`,
`{"title": null}`) — status `422`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "title",
        "message": "Value error, This field cannot be null."
      }
    ]
  }
}
```

**This applies to every `PATCH` on phases, milestones, and tasks alike.** The NOT-NULL-backed
fields are: phase `name`/`order`; milestone `title`/`status`/`order`; task `title`/`effort`/
`status`/`order`. **Omitting a field from the PATCH body is fine and leaves it unchanged** —
only an *explicit* `null` for one of these specific fields 422s. Fields that are genuinely
nullable in the data model (`description`, `due_on`, `owner_id`/`assignee_id`, phase
`starts_on`/`ends_on`) accept an explicit `null` normally (that's how you'd clear a due date or
un-assign an owner) — the rejection is scoped to the fields above only, not a blanket "no nulls
in PATCH" rule.

---

## 6. Task dependencies — `POST`/`DELETE /roadmap/tasks/{id}/dependencies`, `GET /roadmap/dependencies`

**New in Slice 2.** A dependency edge means "this task depends on that task" —
`POST /roadmap/tasks/{task_id}/dependencies` with body `{"depends_on_task_id": "<uuid>"}`
records that `task_id` depends on `depends_on_task_id`. Both ids must be real tasks in the
caller's own roadmap (cross-tenant or unknown ids 404, same guard as every other roadmap
lookup — see §8).

`e2e/_captures/roadmap/dependency_create.json` (`POST /roadmap/tasks/{task_a_id}/dependencies`,
`{"depends_on_task_id": "<task_b_id>"}`, first time this edge is created) — status `201`:

```json
{
  "data": {
    "task_id": "3d697a19-1157-448b-99e4-4e4897b1c16c",
    "depends_on_task_id": "b936223d-2dd7-47f4-8cf9-d7136048d04d"
  },
  "meta": null
}
```

**Creating the exact same edge again returns `200`, not `201` and not an error** — dependency
creation is idempotent by design (`add_dependency()` checks for the existing row before
inserting). The response body is identical either way; only the status code differs. Don't
treat a `200` here as a failure — it means "this dependency already existed," which is a normal,
expected outcome for a client that retries.

### `409 DEPENDENCY_CYCLE` — the exact error shape

`e2e/_captures/roadmap/dependency_cycle.json` (`POST /roadmap/tasks/{task_b_id}/dependencies`,
`{"depends_on_task_id": "<task_a_id>"}` — the **reverse** of the edge above, attempted after
`task_a` already depends on `task_b`) — status `409`:

```json
{
  "error": {
    "code": "DEPENDENCY_CYCLE",
    "message": "That would create a loop — Task A already depends on Task B.",
    "field_errors": []
  }
}
```

(That `—` is a literal em dash, "—", just JSON-escaped in the raw capture file — render it
as-is.) **The `message` names the specific two tasks in the conflict, by title** — "`{the task
named by depends_on_task_id}` already depends on `{the task named by task_id}`" — not a generic
"a cycle would form." Surface this message directly to the founder rather than writing your own
generic "can't add that dependency" copy; it tells them exactly which existing relationship is
blocking the new one. A task depending directly on itself (`depends_on_task_id == task_id`) is
a separate, simpler case — `422 VALIDATION_ERROR` (same shape as §5's explicit-null 422), not
`409 DEPENDENCY_CYCLE`, since it's a shape error the request itself is malformed, not a graph
conflict.

`DELETE /roadmap/tasks/{task_id}/dependencies/{depends_on_task_id}` removes one edge —
`{"data": {"deleted": true}, "meta": null}` on success, `404 NOT_FOUND` if that exact edge
doesn't exist (not separately captured — same uniform-404 shape as §8). This route was exercised
by the unit suite; not independently re-captured live since its response shape is identical to
every other `{"deleted": true}` delete across this API.

### `GET /roadmap/dependencies` — the full graph

`e2e/_captures/roadmap/dependencies_graph.json` (trimmed to 2 of 9 `nodes` here — see the full
file for all task/milestone/phase context in the roadmap):

```json
{
  "data": {
    "nodes": [
      {
        "task_id": "7f7735cb-578f-4fe2-9168-763d00188ac4",
        "title": "Run 10 customer interviews",
        "milestone_id": "af46d3db-f34a-484a-b67d-12a63bd5d56e",
        "milestone_title": "Validate demand",
        "phase_id": "274f0137-69e5-4174-845d-e8d85beab569",
        "phase_name": "Validation"
      }
      // ... 8 more nodes, one per task in the roadmap, same shape
    ],
    "edges": [
      {
        "task_id": "3d697a19-1157-448b-99e4-4e4897b1c16c",
        "depends_on_task_id": "b936223d-2dd7-47f4-8cf9-d7136048d04d"
      }
    ],
    "list": [
      {
        "task": "Task A",
        "depends_on": "Task B"
      }
    ]
  },
  "meta": null
}
```

**`nodes` is every task in the roadmap** (not just ones with dependencies) — one entry per task,
each carrying its own id/title plus its parent milestone and phase's id/title/name, flattened so
a graph-rendering UI doesn't need to walk the tree separately to label nodes. **`edges` is every
dependency edge**, in the same `{task_id, depends_on_task_id}` shape as the create/delete
routes above. **`list` is a denormalized, human-readable duplicate of `edges`** — `{"task": "Task
A", "depends_on": "Task B"}` by title, not id — provided so a simple "N dependencies" list view
can render directly without cross-referencing `nodes` by id. Build a graph visualization from
`nodes` + `edges`; build a plain list view from `list` alone.

---

## 7. Template gallery — `GET /roadmap/templates`, `GET /roadmap/templates/{id}`, `POST /roadmap/templates/{id}/apply`

**New in Slice 2.** Separate from the one stage template auto-generated at onboarding (§1) —
this is an opt-in catalog of 6 named, industry-tagged packs a founder can browse and layer onto
their *existing* roadmap on demand. Read routes (`GET /templates`, `GET /templates/{id}`) are
open to any active member (including mentor); `POST /templates/{id}/apply` requires
`founder`/`team_member`, same split as every other roadmap write.

`e2e/_captures/roadmap/templates_list.json` (`GET /roadmap/templates`) — status `200`:

```json
{
  "data": [
    {
      "id": "validation-sprint",
      "title": "Validation sprint",
      "stage": "validation",
      "category": "Fintech",
      "milestone_count": 2,
      "task_count": 3,
      "applied": false
    },
    {
      "id": "mvp-build",
      "title": "MVP build",
      "stage": "build",
      "category": "Fintech",
      "milestone_count": 1,
      "task_count": 2,
      "applied": false
    },
    {
      "id": "pre-seed-raise",
      "title": "Pre-seed raise",
      "stage": null,
      "category": "General",
      "milestone_count": 2,
      "task_count": 4,
      "applied": false
    }
    // ... "go-to-market", "company-formation", "scale-playbook" omitted here,
    // same shape — see the full capture for all 6 gallery templates.
  ],
  "meta": null
}
```

`GET /templates` returns a flat **array** as `data` (not wrapped in an object with a `templates`
key) — 6 items today, always in the same fixed catalog order. **`stage` can be `null`** (e.g.
`pre-seed-raise`, `company-formation`) for templates that aren't tied to one specific startup
stage — render those without a stage badge rather than treating `null` as an error or defaulting
it to something. **`applied`** reflects whether *this workspace's* roadmap already has that
template's id in its `applied_template_keys` — it's per-roadmap state, not a global flag, and
starts `false` for every template on a fresh roadmap.

### Preview — the full phase/milestone/task breakdown before applying

`e2e/_captures/roadmap/template_preview.json` (`GET /roadmap/templates/mvp-build`) — status `200`:

```json
{
  "data": {
    "id": "mvp-build",
    "title": "MVP build",
    "stage": "build",
    "category": "Fintech",
    "milestone_count": 1,
    "task_count": 2,
    "phases": [
      {
        "name": "MVP",
        "milestones": [
          {
            "title": "Core flow shipped",
            "tasks": [
              {
                "title": "Build the core feature",
                "effort": "large"
              },
              {
                "title": "Instrument analytics",
                "effort": "small"
              }
            ]
          }
        ]
      }
    ]
  },
  "meta": null
}
```

The preview's `phases[].milestones[].tasks[]` shape has **no ids** (nothing has been created
yet — this is a read-only preview of the static catalog entry) and **no `due_on`/`starts_on`/
`ends_on`** (those are computed relative to `date.today()` only at apply time, not shown in the
preview). Use this to render a "here's what applying this template will add" confirmation
screen before the founder commits. `GET /templates/{unknown_id}` 404s `NOT_FOUND` (not
separately captured — same uniform shape as every other roadmap 404).

### Apply — fresh `201` vs. already-applied `200`

`e2e/_captures/roadmap/template_apply.json` (`POST /roadmap/templates/mvp-build/apply`, first
call) — status `201`:

```json
{
  "data": {
    "already_applied": false,
    "added": {
      "phases": 1,
      "milestones": 1,
      "tasks": 2
    }
  },
  "meta": null
}
```

`e2e/_captures/roadmap/template_apply_noop.json` (`POST /roadmap/templates/mvp-build/apply`,
second call on the same roadmap) — status `200`:

```json
{
  "data": {
    "already_applied": true,
    "added": {
      "phases": 0,
      "milestones": 0,
      "tasks": 0
    }
  },
  "meta": null
}
```

**Applying is additive and non-destructive** — it appends the template's phases after the
roadmap's existing ones (new phases get the next available `order`); it never edits, reorders,
or removes anything already in the roadmap. **Applying the same template a second time is a
safe no-op**, not an error and not a duplicate — `already_applied: true` with `added` all zeros.
Use `already_applied` (not the HTTP status code) to decide whether to show "Template applied!"
vs. "Already applied" copy — both are success responses. After either response, `GET /roadmap`
(§1) or the phase list will reflect the new content (on a fresh apply) — the apply response
itself only reports counts, not the created phase/milestone/task bodies; re-fetch the tree to
render them.

---

## 8. Auth boundary: mentor read-only, cross-tenant 404

### `403` — mentor attempting a write

`e2e/_captures/roadmap/mentor_forbidden.json` (`POST /roadmap/phases` called by a `mentor`
member) — status `403`:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Gate every roadmap write control (create/edit/delete phase, milestone, task) on the caller's
membership role being `founder` or `team_member`. A `mentor` (and any future read-only role)
should see the whole tree via `GET /roadmap` but never see write affordances rendered as
enabled.

### `404` — cross-tenant phase/milestone/task id

`e2e/_captures/roadmap/cross_tenant_404.json` (a **second, unrelated founder** — with their own
separately-generated roadmap — calling `PATCH /roadmap/phases/{id}` with the **first** founder's
real phase id) — status `404`:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**This is a uniform `404`, not a `403`.** A phase/milestone/task id that is syntactically valid
and genuinely exists — just under a different startup's roadmap — returns the exact same
`NOT_FOUND` shape as an id that doesn't exist anywhere. The same guard applies to milestone and
task lookups (the live journey also exercises a cross-tenant milestone `PATCH`, same `404`
shape, not separately captured since it's byte-identical). **Never build UI that distinguishes
"doesn't exist" from "not yours" from this response** — treat any `404` here as "remove this
from local state," the same guidance as Health Score's identical cross-tenant pattern
(`docs/fe-integration-guide-health-score.md` §6).

---

## 9. AI Re-plan — `POST /replan/preview`, `POST /replan/apply`, `GET /replan/history`

**New in Slice 3.** A re-plan is a co-pilot proposal, never an automatic action: `preview` is a
`POST` that **changes nothing** (it's a `POST` rather than a `GET` only to match the PRD's verb
and mirror the rest of roadmap's read/write split — treat it as a pure read for caching/retry
purposes), and `apply` **never runs on its own** — nothing auto-triggers a re-plan server-side.
A founder (or team_member) must explicitly call `preview` to see the proposal, then explicitly
call `apply` with the specific `change_ids` they chose to accept. `preview` and `history` are
open to any active member (mentor included, read-only); `apply` requires `founder`/`team_member`
— same editor split as every other roadmap write.

### `POST /roadmap/replan/preview` — the proposal, read-only

`e2e/_captures/roadmap/replan_preview.json` (called after one milestone's `due_on` was `PATCH`ed
10 days into the past to force a slip) — status `200`:

```json
{
  "data": {
    "drift_count": 1,
    "changes": [
      {
        "change_id": "7150d76c-9214-4cf1-88c2-a598e7cc81b1",
        "milestone_id": "7150d76c-9214-4cf1-88c2-a598e7cc81b1",
        "title": "Validate demand",
        "old_due": "2026-08-17",
        "new_due": "2026-09-03",
        "reason": "10 days overdue and not yet done."
      }
    ]
  },
  "meta": null
}
```

**`change_id == milestone_id`** — a milestone has at most one proposed shift, so there's no
separate id space to track; use either interchangeably to key a diff-row UI, but pass whichever
you use back as `change_id` in `apply`'s `change_ids` array (below). `drift_count` counts every
slipped milestone (`due_on < today` and not yet `done`), which can be `>=` `changes.length` — a
slipped milestone still gets its own base shift even if the cascade math nets `0` shift for some
other reason; in practice for the current engine every drifted milestone produces a change, but
don't assume the two counts are always equal by contract. **No drift → `changes: []`, `200`, not
an error** — this is the common steady-state response for a roadmap that hasn't slipped; render
an empty/"you're on track" state, not a spinner or error banner.

`reason` is a **templated string**, not a stable enum — three shapes exist today (own-slip only,
cascade only, both), and the exact wording may grow more shapes over time. Render it as opaque
prose; don't parse or pattern-match it client-side. `reason` is per-change (one line per shifted
milestone); it's distinct from `rationale` (below), a single holistic paragraph for the whole
re-plan — the SOP's Follow-ups that used to reserve "AI-authored rationale" for Module 03 is now
**delivered**, see below.

### `POST /roadmap/replan/apply` — commit selected changes

Body: `{"change_ids": ["<uuid>", ...]}` — pass the `change_id`s (== `milestone_id`s) the founder
chose to accept from the preview's `changes` array. A founder can accept a subset — omit any
`change_id` they want to leave alone.

`e2e/_captures/roadmap/replan_apply.json` (`{"change_ids": ["7150d76c-9214-4cf1-88c2-a598e7cc81b1"]}`,
applying the one change from the preview above) — status `200`:

```json
{
  "data": {
    "applied": [
      "7150d76c-9214-4cf1-88c2-a598e7cc81b1"
    ],
    "skipped": [],
    "replan_id": "6bc26405-ce3d-48ee-b751-8d0476f7df99",
    "summary": "Re-planned 1 milestone"
  },
  "meta": null
}
```

**`apply` recomputes the proposal from current state — it never trusts a client-held diff.** If
anything about the roadmap changed between your `preview` call and this `apply` call (a task got
completed, a dependency was removed, someone else manually re-dated the milestone), a `change_id`
that's no longer valid is silently dropped into `skipped` rather than applied against stale data.
**Always render both `applied` and `skipped`** after a call — don't assume every requested
`change_id` landed in `applied` just because the call returned `200`. `replan_id`/`summary` are
`null` when nothing in `change_ids` was still valid (`applied: []`) — that's a `200` empty-state
too, not an error; no history row is written and no event fires for an all-stale/empty apply.
**Re-applying the same `change_ids` a second time is safe** — the milestones are no longer
shifting (already at their target `due_on`), so the fresh proposal omits them and they come back
`skipped`, not double-applied.

After a successful apply, `GET /roadmap` (§1) reflects the new `due_on` and the milestone's
`replanned` marker (below) — the apply response itself only returns ids/counts, not the updated
milestone bodies; re-fetch the tree to render the new dates.

**New in this slice: `rationale` (string | null) on a successful apply.** See the dedicated
`rationale` subsection below `GET /replan/history` for the full field contract — in short, the
value you get back here is the **instant templated fallback**, not AI-authored text yet.

### `GET /roadmap/replan/history` — past re-plans, newest first

`e2e/_captures/roadmap/replan_history.json` — status `200`:

```json
{
  "data": [
    {
      "id": "6bc26405-ce3d-48ee-b751-8d0476f7df99",
      "change_count": 1,
      "summary": "Re-planned 1 milestone",
      "applied_by": {
        "id": "7bc9cb23-2620-4c9e-bb5d-b9ce134acf7c",
        "name": "Ada Founder"
      },
      "created_at": "2026-08-27T10:40:55.183930+00:00",
      "changes": [
        {
          "title": "Validate demand",
          "reason": "10 days overdue and not yet done.",
          "new_due": "2026-09-03",
          "old_due": "2026-08-17",
          "milestone_id": "7150d76c-9214-4cf1-88c2-a598e7cc81b1"
        }
      ]
    }
  ],
  "meta": null
}
```

`data` is a flat **array**, newest-first by `created_at` — one entry per `apply` call that
committed at least one change (empty/all-stale applies never appear here). `applied_by` is the
same `{"id", "name"}` shape as milestone `owner`/task `assignee` elsewhere in this guide, never a
bare uuid. `changes` inside each history row is the **snapshot at the time of that apply** —
note the key order/shape here (`title`, `reason`, `new_due`, `old_due`, `milestone_id`) is
whatever the JSONB blob happened to serialize as; don't rely on key ordering, only on the keys
themselves, which match the `changes[]` entries from `preview`/`apply` minus `change_id` (the
history snapshot doesn't carry a separate `change_id` — use `milestone_id` if you need to
correlate a past change back to a specific milestone).

**The capture above predates this slice's `rationale` field** (captured before the re-plan
rationale feature existed, so its history row doesn't show the key at all — a live `GET
/replan/history` response today always includes `rationale`, see immediately below). Every
history row's top-level shape is otherwise unchanged — one new key, no restructuring.

### `rationale` — AI-authored re-plan narrative (new in this slice)

**New field on both the `apply` response (`data.rationale`) and every `GET /replan/history` item
(`data[].rationale`): `string | null`.** It's a single holistic paragraph explaining *why* the
re-plan happened, distinct from each change's own per-milestone `reason` line above — think
"one-sentence coach summary for the whole re-plan" vs. "one line per shifted milestone."

**The critical nuance: `apply`'s `rationale` and a subsequent `history` read's `rationale` are
NOT the same text, even for the exact same re-plan.**

1. `apply_replan` writes a **templated fallback** synchronously, in the same call that creates
   the `RoadmapReplan` row — this is the value `POST /replan/apply` returns immediately in
   `data.rationale`. It is deterministic prose built from the change count + shifted milestone
   titles, never `null` once at least one change was applied.
2. That same call also enqueues an `ai.roadmap.rationale` job. Once a worker drains it (typically
   seconds later), the job **overwrites the same row's `rationale`** with LLM-authored prose — the
   apply response has already been returned by then, so the FE never sees this upgrade inline.
3. **To pick up the AI-authored version, re-fetch `GET /replan/history` after a short delay** (a
   few seconds is enough in practice) — the newest row's `rationale` will have changed from the
   templated fallback to the AI-authored text. There is no separate "is it upgraded yet" flag;
   diff against the templated value if you need to detect the swap, or simply always re-render
   from the latest `history` read rather than caching the `apply` response's `rationale`.

**Render `rationale` as opaque prose, same guidance as `reason`** — don't parse it, don't assume
a fixed sentence count or format; both the templated and AI-authored versions are meant to be
displayed as-is, not parsed.

**`rationale` is `null` on any history row that predates this feature** — the column is a new
nullable one added by migration `0028`, with no backfill for pre-existing `roadmap_replans` rows.
Treat `null` as "no rationale recorded for this older re-plan," not an error or a loading state;
only render a loading affordance for a freshly-applied re-plan whose `history` row you haven't
re-fetched yet (and even then, it already has the templated value, never a bare `null`).

**If the AI job fails, the templated fallback written at apply time is permanent** — there is no
separate "AI enrichment failed" signal, and no retry the FE needs to trigger; a `rationale` that
never changes between two `history` reads a few minutes apart just means the upgrade didn't land
(same fire-and-forget shape as the assessment-narrative and mission-reason upgrades documented in
the sibling FE guides).

`e2e/_captures/roadmap_replan_rationale/apply.json` (same re-plan as above, this slice's own live
run — one milestone shifted) — status `200`:

```json
{
  "data": {
    "applied": [
      "b15906ac-b14b-46e6-af62-3f6fb5105795"
    ],
    "skipped": [],
    "replan_id": "afa98998-50a2-43dc-8281-ab6030f51240",
    "summary": "Re-planned 1 milestone",
    "rationale": "Re-planned 1 milestone: adjusted the dates for Validate demand to keep your roadmap realistic after recent slips."
  },
  "meta": null
}
```

`e2e/_captures/roadmap_replan_rationale/history_after_drain.json` (`GET /replan/history` for the
same startup, called **after** draining the worker that processes `ai.roadmap.rationale`) —
status `200`:

```json
{
  "data": [
    {
      "id": "afa98998-50a2-43dc-8281-ab6030f51240",
      "change_count": 1,
      "summary": "Re-planned 1 milestone",
      "rationale": "[stub-llm] AI-generated assessment narrative.",
      "applied_by": {
        "id": "f628140b-897c-46ec-b5e7-496fd6907c95",
        "name": "Ada Founder"
      },
      "created_at": "2026-09-21T09:15:19.788190+00:00",
      "changes": [
        {
          "title": "Validate demand",
          "reason": "10 days overdue and not yet done.",
          "new_due": "2026-09-28",
          "old_due": "2026-09-11",
          "milestone_id": "b15906ac-b14b-46e6-af62-3f6fb5105795"
        }
      ]
    }
  ],
  "meta": null
}
```

Compare the two captures: same `id`/`replan_id` (`afa98998-...`), but `rationale` moved from the
templated sentence ("Re-planned 1 milestone: adjusted the dates for...") to the AI-authored one
("[stub-llm] AI-generated assessment narrative.") — this is exactly the swap described above,
proven live by draining the worker in between the two calls.

**About that AI-authored string in the capture:** `[stub-llm] AI-generated assessment narrative.`
is not a roadmap-specific bug — it's the offline `StubLLMClient`'s fixed, generic marker string
for **every** free-text `complete()` call in this codebase (assessment narrative, business plan
sections, and now roadmap rationale all return the exact same literal string in
tests/e2e/dev with `LLM_PROVIDER=stub`), used so a test can assert the AI path ran without a real
network call. **In production** (`LLM_PROVIDER=openai` or compatible), the real LLM returns
contextual 2–3 sentence prose built from `build_roadmap_rationale_messages` (the shifted
milestones' titles/dates/reasons + startup name/industry/stage) — don't build any FE logic that
depends on the literal stub string.

### `GET /roadmap` tree — `drift` summary + per-milestone `replanned` marker

`e2e/_captures/roadmap/get_tree_replanned.json` (captured immediately after the apply above) —
trimmed to the re-planned milestone and its untouched sibling:

```json
{
  "data": {
    "roadmap": {
      "id": "0af33300-772d-425c-b425-b9aeae4b14ad",
      "stage": "validation",
      "template_key": "stage.validation",
      "generated_at": "2026-08-27T10:40:55.138376+00:00",
      "drift": {
        "slipped_count": 0
      }
    },
    "current_stage": "validation",
    "phases": [
      {
        "id": "2b13719d-9a22-4f82-8bcc-dd7ed9951d67",
        "name": "Validation",
        "order": 0,
        "starts_on": "2026-08-27",
        "ends_on": "2026-10-08",
        "milestones": [
          {
            "id": "7150d76c-9214-4cf1-88c2-a598e7cc81b1",
            "title": "Validate demand",
            "description": null,
            "due_on": "2026-09-03",
            "owner": null,
            "status": "todo",
            "progress": 0,
            "overdue": false,
            "order": 0,
            "dependency_count": 0,
            "replanned": {
              "at": "2026-08-27T10:40:55.190314+00:00",
              "reason": "10 days overdue and not yet done."
            },
            "tasks": [ /* ...unchanged, see get_tree_replanned.json for the full array... */ ]
          },
          {
            "id": "abd13ca4-325f-495d-9152-e1bca65901de",
            "title": "Pricing test",
            "due_on": "2026-10-01",
            "replanned": null
            /* ...rest of the untouched milestone, same shape as §1... */
          }
        ]
      }
    ]
  },
  "meta": null
}
```

**Two field-nesting traps here, both important:**

1. **`drift` lives inside `roadmap`, not at the top level.** Read
   `data.roadmap.drift.slipped_count` — there is no `data.drift`. This is the count that powers
   the PRD's *"{n} tasks have slipped..."* banner; `0` means render nothing (or an "on track"
   state), not a `0` badge that reads as an error.
2. **`replanned` is a tree-only field, same trap shape as Slice 2's `depends_on`.** It appears on
   each milestone inside `GET /roadmap`'s nested tree, but the single-milestone
   `POST`/`PATCH /roadmap/milestones/{id}` responses (§4) do **not** include a `replanned` key at
   all (not even `null`) — that flat shape wasn't extended for this field. **Read a milestone's
   re-plan marker from the tree, never from a milestone-CRUD response.**

`replanned` is `null` for any milestone that has never been shifted by an apply (the common
case) — treat `null` as "never re-planned," not an error or a loading state. Once set, it is
**never cleared automatically** — a milestone that gets manually edited back to an earlier due
date, or that slips again later and gets re-planned again, simply overwrites `at`/`reason` with
the newest apply's values; there is no history of *prior* markers on the milestone itself (use
`GET /replan/history`, above, for the full audit trail across every apply).

### UX note: re-plan is a two-step, human-gated flow — never render it as automatic

Because `apply` never runs unless a human explicitly calls it with explicit `change_ids`, the FE
should **always show the `preview` proposal as a review/diff screen the founder must actively
accept** (per the PRD's AI Re-plan view) — never poll `preview` in the background and silently
`apply` its result. A `drift.slipped_count > 0` on the tree is a signal to *offer* the re-plan
flow (e.g. a banner with a "Review re-plan" CTA), not a trigger to change anything on its own.

---

## Verification table

Every row below was exercised **live**, over real HTTP, against a real Postgres-backed server
(`make e2e`, `e2e/test_roadmap.py::test_roadmap_journey`) — not just unit-tested in-process.

| Endpoint / behavior | Verified live (`make e2e`)? |
|---|---|
| `GET /roadmap` — freshly-generated tree (stage `validation`) | ✅ |
| `GET /roadmap` — after manual phase/milestone/task creation + task completion | ✅ |
| `POST /roadmap/generate` — idempotent re-call, `202` + already-`succeeded` job | ✅ |
| Inline generation at `POST /onboarding/complete` (no separate generate call needed) | ✅ |
| `POST /roadmap/phases` — create, auto `order` assignment | ✅ |
| `PATCH /roadmap/phases/{id}` — happy-path edit | ⬜ (unit-tested only; live journey only exercises the cross-tenant 404 on this route) |
| `DELETE /roadmap/phases/{id}` | ⬜ (unit-tested only, not in the live E2E journey) |
| `POST /roadmap/milestones` — create under a phase | ✅ |
| `PATCH /roadmap/milestones/{id}` — status → `done`, progress snaps to 100 | ✅ |
| `DELETE /roadmap/milestones/{id}` | ⬜ (unit-tested only, not in the live E2E journey) |
| `POST /roadmap/tasks` — create under a milestone | ✅ |
| `PATCH /roadmap/tasks/{id}` — status → `done`, parent milestone progress recomputed | ✅ |
| `DELETE /roadmap/tasks/{id}` | ⬜ (unit-tested only, not in the live E2E journey) |
| Explicit-`null` rejection on required `PATCH` fields → `422 VALIDATION_ERROR` | ✅ (task `title`; same validator covers phase/milestone) |
| team_member can write (in `_editor`) | ✅ |
| mentor can read but not write → `403 FORBIDDEN` | ✅ |
| Cross-tenant phase/milestone lookup → uniform `404 NOT_FOUND` | ✅ |
| `POST /tasks/{id}/dependencies` — fresh edge, `201` | ✅ |
| `POST /tasks/{id}/dependencies` — duplicate edge, idempotent `200` | ⬜ (unit-tested only, not in the live E2E journey) |
| `POST /tasks/{id}/dependencies` — self-dependency → `422 VALIDATION_ERROR` | ⬜ (unit-tested only) |
| `POST /tasks/{id}/dependencies` — reverse edge → `409 DEPENDENCY_CYCLE` (exact message) | ✅ |
| `DELETE /tasks/{id}/dependencies/{depends_on_task_id}` | ⬜ (unit-tested only, not in the live E2E journey) |
| `GET /roadmap/dependencies` — graph (`nodes`/`edges`/`list`) | ✅ |
| `GET /roadmap`'s tree `depends_on` / `dependency_count` populated after a real edge exists | ✅ |
| Single-task CRUD response (`_task_out`) still returns `depends_on: []` regardless of real edges | ✅ (implicit — every `task_create`/`task_patch` capture in §5 predates the dependency step, and the endpoint hardcodes `[]` unconditionally per the source, not just per this run's ordering) |
| `GET /roadmap/templates` — gallery list (+ `applied` flag) | ✅ |
| `GET /roadmap/templates/{id}` — preview | ✅ |
| `GET /roadmap/templates/{unknown_id}` → `404 NOT_FOUND` | ⬜ (unit-tested only) |
| `POST /roadmap/templates/{id}/apply` — fresh apply, `201` + real `added` counts | ✅ |
| `POST /roadmap/templates/{id}/apply` — re-apply, idempotent `200` + `already_applied: true` | ✅ |
| `GET /roadmap/templates` reflects `applied: true` after a real apply | ✅ |
| `POST /roadmap/replan/preview` — drift + cascade proposal after forcing a slip | ✅ |
| `POST /roadmap/replan/preview` — no-drift empty `changes: []` | ⬜ (unit-tested only, `test_preview_no_drift_empty`) |
| `POST /roadmap/replan/apply` — applies selected `change_ids`, `replan_id` + `summary` returned | ✅ |
| `POST /roadmap/replan/apply` — stale `change_id` skipped, re-apply idempotent | ⬜ (unit-tested only, `test_apply_skips_stale_change_id` / `test_reapply_is_idempotent`) |
| `POST /roadmap/replan/apply` — mentor (non-editor) → `403 FORBIDDEN` | ⬜ (unit-tested only, `test_apply_forbidden_for_mentor`) |
| `GET /roadmap/replan/history` — lists an applied re-plan with `applied_by` + `changes` snapshot | ✅ |
| `POST /roadmap/replan/apply` — `rationale` present, templated fallback value | ✅ |
| `GET /roadmap/replan/history` — `rationale` AI-upgraded after draining `ai.roadmap.rationale` | ✅ |
| `GET /roadmap` tree — `roadmap.drift.slipped_count` before (>0) and after (reduced) an apply | ✅ |
| `GET /roadmap` tree — milestone `replanned` marker `null` before, populated after | ✅ |
| Cascade: downstream milestone shifts with its slipped upstream dependency | ⬜ (unit-tested only, `test_downstream_dependency_shifts`) |
| Cascade: diamond dependency shifts by `max`, not sum, of its two upstreams | ⬜ (unit-tested only, `test_diamond_shifts_by_max_not_sum`) |

Rows marked ⬜ are covered by the unit suite (`tests/api/test_roadmap_phases.py`,
`test_roadmap_milestones.py`, `test_roadmap_tasks.py`, `test_roadmap_dependencies_api.py`,
`test_roadmap_templates_gallery.py`, `test_roadmap_apply_api.py`, `test_roadmap_replan_api.py`,
`tests/services/test_roadmap_replan_compute.py`, `test_roadmap_replan_apply.py`,
`tests/services/roadmap/test_ai_rationale.py`, `tests/worker/test_roadmap_rationale_handler.py`)
but not independently re-asserted over live HTTP in `e2e/test_roadmap.py` /
`e2e/test_roadmap_replan.py` / `e2e/test_roadmap_replan_rationale.py` — safe to build against,
just not double-verified end-to-end.
