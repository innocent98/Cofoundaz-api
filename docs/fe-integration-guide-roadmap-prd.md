# FE Integration Guide — Roadmap, PRD completion (Module 05 + the 23.2 thread)

**Provenance.** e2e journey `e2e/test_roadmap_prd.py` (real uvicorn, worker drained in-process, stub LLM) →
`e2e/_captures/roadmap_prd/*.json`, plus the **real LLM** run on the local docker stack (`e2e/_captures/roadmap_live/`).
Bodies are verbatim. This **extends** `fe-integration-guide-roadmap.md`; the gap list is
`cofoundaz/docs/checklist/roadmap-prd-gaps.md`. All routes: Bearer + `X-Workspace-Id` unless noted.

## 0. Already in the tree: render it (no new API)
`GET /roadmap` milestones already carry **`owner`** (`{id, name}` or null), **`dependency_count`**, **`overdue`**,
**`replanned`** (`{at, reason}` or null) and `tasks[]` (each with `assignee`, `due_on`, `overdue`). Gap rows 3 and 7:
- Milestones table: add **Owner** and **Dependencies** columns.
- Timeline: **"Overdue"** chip (danger) when `overdue`; **"↻ Re-planned"** chip with the tooltip "Adjusted on {at} because
  {reason}" when `replanned`; expandable tasks under each milestone.
- Stage rail (row 1): the six real stages `idea, validation, build, launch, growth, scale` (labels Idea, Validation, Build,
  Launch, Growth, Scale), current = the startup's stage.
- Editing (row 4): `PATCH /roadmap/milestones/{id}` already accepts `owner_id`, `due_on`; `PATCH /roadmap/tasks/{id}` accepts
  `assignee_id`, `due_on` (see the base guide). The pickers are fed by §2.

## 1. AI "Ask AI to break this down" (row 2): `POST /roadmap/milestones/{id}/breakdown`
Founder/team member only (others 403). No body. **202** (e2e `breakdown_started.json`):
```json
{
  "data": {
    "job_id": "bcf0d900-f62e-4951-81d6-2633595a6a86",
    "status": "queued"
  },
  "meta": null
}
```
A second request while one is queued/running → **409** (`breakdown_in_progress_409.json`):
```json
{
  "error": {
    "code": "BREAKDOWN_IN_PROGRESS",
    "message": "I'm already breaking this milestone down.",
    "field_errors": []
  }
}
```
Poll `GET /jobs/{job_id}` every ~2s (stop after ~60s with an honest "Taking longer than usual, check back shortly"). Done
(`breakdown_job_done.json`):
```json
{
  "data": {
    "id": "bcf0d900-f62e-4951-81d6-2633595a6a86",
    "type": "ai.roadmap.breakdown",
    "status": "succeeded",
    "result": {
      "status": "ready",
      "milestone_id": "be3cc2e4-d5e2-4425-bc67-6d1a06c16d71",
      "created_task_ids": [
        "0cec7f9e-3da0-4caa-a6f5-31cd2f1e0300"
      ]
    },
    "error": null
  },
  "meta": null
}
```
Then refetch `GET /roadmap` and highlight the new tasks (`created_task_ids`). The AI only **appends** tasks (3 to 5 from the
real model, deduped against existing titles) and never edits or deletes. `result.status` can also be **`over_budget`**
(show "AI is at its daily limit, try again tomorrow") or `gone` (the milestone was deleted). A job `status: "failed"` → show
"Couldn't break this down. Try again." (The stub e2e model creates 1 task; the real model creates 3 to 5, see the live capture.)

## 2. Members for pickers (row 4): `GET /team/members`
Any member. Active members (`members.json`):
```json
{
  "data": [
    {
      "user_id": "e2fbf711-3d34-41e6-af61-f0854d9010a2",
      "name": "Ada F",
      "email": "delivered+e2e-77ee95a006d6@resend.dev",
      "role": "founder"
    },
    {
      "user_id": "36f881f3-b3f2-4573-9ca5-91d932fcc9bb",
      "name": null,
      "email": "delivered+mate-80abbf919888@resend.dev",
      "role": "team_member"
    }
  ],
  "meta": null
}
```
`name` can be **null** (a member who never filled in a profile): show the email instead. Offer "Unassigned" (send `null`).

## 3. Comments thread (row 6): `/team/comments`
Entities v1: `roadmap_milestone`, `roadmap_task` (anything else → 422). Show the thread in the drawer.
- `GET /team/comments?entity_type=&entity_id=`: oldest first; any member can read.
- `POST /team/comments` `{entity_type, entity_id, body (1-4000), mention_user_ids?: [user_id] (max 20)}` → **201**
  (`comment_created.json`):
```json
{
  "data": {
    "id": "66e92e1c-211f-4b20-80b3-8d957c305ef1",
    "entity_type": "roadmap_milestone",
    "entity_id": "be3cc2e4-d5e2-4425-bc67-6d1a06c16d71",
    "author": {
      "id": "e2fbf711-3d34-41e6-af61-f0854d9010a2",
      "name": "Ada F"
    },
    "body": "Can you own the interviews?",
    "mention_user_ids": [
      "36f881f3-b3f2-4573-9ca5-91d932fcc9bb"
    ],
    "created_at": "2026-10-18T03:18:55.080086+00:00",
    "edited_at": null,
    "resolved_at": null,
    "is_mine": true
  },
  "meta": null
}
```
  Everyone except **investors** can post (403). Composer placeholder (PRD): "Add a comment… @ to mention". Build the @-autocomplete
  from `/team/members` and send the picked ids in `mention_user_ids` (mentioning a non-member → 422 "You can only mention people
  in this workspace."). Rate limit 30/min.
- `PATCH /team/comments/{id}` `{body}`: **author only** (others 403, `comment_edit_not_author_403.json`); sets `edited_at`. Show
  edit/delete only when `is_mine`.
- `DELETE /team/comments/{id}`: author only → 204.
- `POST /team/comments/{id}/resolve` `{resolved: true|false}`: founder/team member or the author → `resolved_at` set or cleared
  (`comment_resolved.json`). PRD toggle "Resolve thread".
- Mentioned members get the notification **"You were mentioned by {name}: "{snippet}""** (body = the milestone/task title), type
  `team.comment.mentioned`. Deep-link to `/roadmap` and open that entity's drawer.

## 4. Notifications (row 10)
- New `roadmap.milestone.due_soon` (once per milestone, 2 days ahead): **"Milestone due soon: {title}, {relative}"** (`relative` ∈
  today / tomorrow / in N days).
- `roadmap.milestone.overdue` copy: **"Milestone overdue, I've drafted a re-plan for your review"**, body "Review re-plan" →
  `/roadmap/replan`.

## 5. Verification
| Behaviour | Live? | Source |
|---|---|---|
| Breakdown 202 → 409 while running → job succeeded with created ids | ✅ | e2e captures; real LLM: `roadmap_live/` |
| Members list | ✅ | `members.json` |
| Comment create (mention) → teammate notified → thread → non-author edit 403 → resolve | ✅ | e2e journey |
| Over-budget / gone results; investor 403; foreign entity 404; non-member mention 422; delete | ❌ unit | `tests/services/test_roadmap_prd.py` |
| Due-soon scheduling + copy; overdue copy | ❌ unit | `test_due_soon_scheduling_and_copy` |

## 6. Re-walk fixes (2026-10-19): behaviour only, no new fields
| Action | Before | Now | FE consequence |
|---|---|---|---|
| Two `POST /roadmap/milestones/{id}/breakdown` at the same time | both `202`, two jobs appended near-duplicate tasks | the second gets `409` `BREAKDOWN_IN_PROGRESS` "I'm already breaking this milestone down." (milestone row lock) | show the message (the drawer and the Milestones table already do) |
| `PATCH /roadmap/milestones/{id}` `{"status":"done"}` | milestone `done` but its tasks stayed open, progress could read 0% | every open task becomes `done`, `progress` 100 (D-RM-5); setting it back to `todo` leaves the tasks done | refetch the milestone's tasks after completing it; Today's Mission no longer offers them |

Captured (local stack, ids are local throwaway data):
- `e2e/_captures/roadmap_rewalk/breakdown_concurrent_202_409.json`: first `202 {"data":{"job_id":"…","status":"queued"}}`,
  second `409 {"error":{"code":"BREAKDOWN_IN_PROGRESS","message":"I'm already breaking this milestone down.","field_errors":[]}}`.
- `e2e/_captures/roadmap_rewalk/milestone_complete_completes_tasks.json`: `200`, body `{"data":{…,"status":"done","progress":100,…}}`;
  note the PATCH body has **no `tasks`** key (read them from `GET /roadmap`), and both tasks came back `done`.

| Behaviour | Live? | Source |
|---|---|---|
| Concurrent breakdown → 202 + 409 | ✅ | capture above |
| Milestone complete → tasks done, 100% | ✅ | capture above |
| Reopen leaves tasks done | ❌ unit | `tests/services/test_roadmap_rewalk.py` |
