# SOP — Roadmap backend, PRD completion (+ team members and the comments thread)

**What shipped (one line):** The backend behind the Roadmap gaps: "Ask AI to break this down" (async AI job appending
tasks), `GET /team/members` for owner/assignee pickers, a generic comments thread (`/team/comments`, PRD 23.2) with
mentions, "milestone due soon" notifications and the PRD overdue copy. Plus a dev-stack fix so hot reloads don't hang on
open SSE streams. Branch `feat/roadmap-prd` (stacked on #136) → PR.

## Why
Walkthrough (`cofoundaz/docs/checklist/roadmap-prd-gaps.md`): the Milestones "Break down" button and the row menu were dead
with no backend; the drawer couldn't assign anyone (no members list) and had no comments; there was no due-soon
notification. Most other Roadmap gaps are FE-only (the tree already carries owner, dependency counts, re-plan info).

## How (decisions D-RM-1..4)
| Decision | Why | Rejected |
|---|---|---|
| Breakdown = async job `ai.roadmap.breakdown` (202 + poll `/jobs/{id}`); appends 3-5 tasks, deduped by title; one running job per milestone (409) | Co-pilot principle: never edit or delete; consistent with other AI jobs | Synchronous LLM call in the request |
| Outcome in `job.result` (`ready` + ids / `over_budget` / `gone`) | The FE gets a clear end state, and over-budget isn't a crash | Raising, which would retry and burn attempts |
| Comments: one generic table + `/team/comments` (PRD 23 API), v1 entities = roadmap milestones/tasks, entity validated against the workspace | Reusable by every module's threads later | A roadmap-only comments table |
| Post: all members but investors; edit/delete: author; resolve: founder/TM or author; mentions explicit ids (members only) → notification | PRD access lines; mention spoofing impossible | Parsing @names from text |
| `GET /team/members` readable by any member | Pickers and the directory | Founder-only |
| Due soon: daily scheduler claim per milestone, 2 days ahead | PRD notification, once only | — |
| Dev `uvicorn --timeout-graceful-shutdown 3` | SSE streams blocked every hot reload ("Waiting for connections to close"); prod gunicorn already caps at 30s | — |

## What's involved
**New:** `app/services/roadmap/breakdown.py`, `app/services/team/service.py`, `app/db/models/comment.py`, migration
`alembic/versions/0051_comments.py`. **Changed:** `app/api/v1/endpoints/roadmap.py` (breakdown route),
`app/api/v1/endpoints/team.py` (members + comments), `app/schemas/team.py`, `app/worker/handlers/ai.py` (breakdown handler),
`app/worker/scheduler.py` + `handlers/scheduled.py` (due soon), notifications registry/categories, `app/db/models/__init__.py`,
`docker-compose.yml` (dev only). Tests `tests/services/test_roadmap_prd.py` (11); e2e `e2e/test_roadmap_prd.py`.
Guide `docs/fe-integration-guide-roadmap-prd.md`.

## Verification
Fresh-DB pytest under UTC 2314 passed (97.54%); static gates; `alembic check` shows no drift; e2e 70/70. Live: a real-LLM
breakdown appended 4 relevant tasks (`e2e/_captures/roadmap_live/`).

## Operate / roll back
`alembic upgrade head` (0051) + **restart the worker**. Roll back: revert, `alembic downgrade 0050_roadmap_task_mission_rank`.

## Update 2026-10-19: re-walk fixes (branch `fix/roadmap-rewalk`)
| What | Why | How |
|---|---|---|
| Concurrent "Break down" requests serialize per milestone | Two clicks at once both passed the running-job check; two jobs appended 10 near-duplicate tasks | `start_breakdown` locks the milestone row (`SELECT … FOR UPDATE`) before checking for a queued/running job; test asserts the lock precedes the check (a true two-session race can't run inside the rollback-per-test DB fixture), live: 202 + 409 |
| D-RM-5: completing a milestone completes its open tasks | "Mark Milestone Complete" left 6 open tasks at 0%, and Today's Mission kept offering them | `update_milestone_ep` flips open tasks to `done` on the todo → done transition, then recomputes progress; reopening leaves them done. Rejected: blocking completion while tasks are open (the founder explicitly said it's done) |

Verified: `tests/services/test_roadmap_rewalk.py` (2) + the lock test in `tests/services/test_roadmap_prd.py`; live captures in the
guide §6.

## Follow-ups
- More comment entities (documents, tasks in other hubs) as their pages are built. Team page (PRD 23) uses the same APIs.
