# FE Integration Guide — Roadmap (Module 05, Slice 1)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_roadmap.py` running against a real server (`make e2e`) — see
`e2e/_captures/roadmap/*.json`. Nothing here is retyped from memory or invented. IDs in the
examples are real ids from that ephemeral test run (not hand-written placeholders) — they will
differ on every real request, but the shapes are exact.

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

### `depends_on` and `dependency_count` are always empty in Slice 1

Every task carries `depends_on: []` and every milestone carries `dependency_count: 0` in every
capture in this guide. This is **not** a bug or a sign nothing has dependencies yet by chance —
Slice 1 creates the `roadmap_task_dependencies` table but nothing writes to it. Both fields are
present now, at these constant values, purely so the **shape** is stable — Slice 2 will start
populating them without changing the field names or types the FE already renders against. Build
any "this task is blocked" / "N dependencies" UI now against these fields, but expect it to stay
inert (always unblocked, always zero) until Slice 2 ships.

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

`PATCH /roadmap/phases/{id}` is not separately captured here (see the cross-tenant 404 in §6,
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

**Field-nesting trap:** this single-milestone response has no `tasks` array (unlike the same
milestone nested inside `GET /roadmap`'s tree, §1, which does). Fetch `GET /roadmap` (or a
future single-milestone-with-tasks endpoint, not built in Slice 1) if you need this milestone's
tasks right after creating it — don't expect `tasks: []` to appear here.

A milestone can be created (or later patched) directly to `status: "done"` with zero tasks —
`progress` snaps to `100` in that case (see §7), not `0`.

`e2e/_captures/roadmap/milestone_complete.json` (`PATCH /roadmap/milestones/{id}`,
`{"status": "done"}`, called after the milestone's one task was already marked `done` — see
§7's ordering) — status `200`:

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

## 6. Auth boundary: mentor read-only, cross-tenant 404

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
| `depends_on` / `dependency_count` always empty/zero in Slice 1 | ✅ (implicit in every capture above) |

Rows marked ⬜ are covered by the unit suite (`tests/api/test_roadmap_phases.py`,
`test_roadmap_milestones.py`, `test_roadmap_tasks.py`) but not independently re-asserted over
live HTTP in `e2e/test_roadmap.py` — safe to build against, just not double-verified
end-to-end.
