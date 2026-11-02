# SOP — Today's Mission backend, PRD completion

**What shipped (one line):** Everything the Mission pages need from the backend per PRD Module 04: delivery time
that actually works, the 7-day Upcoming projection with editable order, a Completed log, best streak, milestone chips,
"assigned to me", PRD notification copy, and two behaviour bugs fixed (weekend streak resets, rest-day "ready" pings).
Branch `feat/mission-prd` → PR to `develop`.

## Why
A live walkthrough against PRD Module 04 + the existing UI (`cofoundaz/docs/checklist/mission-prd-gaps.md`) found the
backend gaps behind rows 1–3, 5, 10–12 and 15: delivery time stored but ignored, no upcoming/completed/best-streak data,
no milestone on tasks, generic notification copy, and no team-member view.

## How (decisions D-MIS-1..6 in the gap list)
| Decision | Why | Rejected |
|---|---|---|
| Scheduler fires per workspace at its own `delivery_time` (default `MISSION_GEN_HOUR`:00) | The setting must do what it says | Keeping the global 06:00 |
| Lazy generation on view stays | A founder who opens early gets their plan, not a wait screen | Blocking until the delivery hour |
| Upcoming = pure projection mirroring the generator (size, weekends, snoozed carry, rank) | Always reflects the live roadmap. No speculative rows to clean up | Pre-creating future missions |
| Editable order = `roadmap_tasks.mission_rank` (migration 0050), sorted first by the generator | The founder's order actually changes future missions | A display-only order |
| Weekends-off rest days skip, don't break, streaks; `best_streak` uses the same rule | "Rest is a strategy too." Previously every weekend reset everyone's streak | — |
| `mission.ready` only when the mission has tasks; body = first task title | No "ready" ping for an empty rest day (the old handler sent one) | — |
| `assigned_to_me` from `RoadmapTask.assignee_id` | PRD access line, unblocked without waiting for Team tasks | Deferring to the Team page |

## What's involved
Migration `alembic/versions/0050_roadmap_task_mission_rank.py`; `app/db/models/roadmap.py`; `app/services/mission/service.py`
(ranked candidates, `_rest_day`, `streak`, `best_streak`, `milestones_for`, milestone in `serialize_task`, history streaks);
**new** `app/services/mission/planning.py` (`upcoming`, `set_upcoming_order`, `completed_log`, `assigned_to`);
`app/api/v1/endpoints/mission.py` (`GET /upcoming`, `PUT /upcoming/order`, `GET /completed`, today additions);
`app/schemas/mission.py` (`UpcomingOrder`); `app/worker/scheduler.py` (per-workspace delivery time);
`app/worker/handlers/scheduled.py`; `app/services/notifications/registry.py`. Also fixed a Sunday-only test bug in
`tests/services/marketing/test_channel_service.py`. Tests: `tests/services/mission/test_mission_prd.py` (new) + updated
history/handler tests. e2e: `e2e/test_mission_prd.py` (new), `e2e/test_notifications_scheduler.py` (date-proof). Guide:
`docs/fe-integration-guide-mission-prd.md`.

## Verification
Fresh-DB pytest under UTC, gates, e2e 69/69 (see the PR). Live on local docker: today/upcoming/completed/history for the
test founder (`e2e/_captures/mission_prd_live/`).

## Operate / roll back
`alembic upgrade head` + **restart the worker**. Roll back: revert, `alembic downgrade 0049_kpi_snapshots`.

## Update 2026-10-19: re-walk fixes (branch `fix/mission-rewalk`)
| What | Why | How |
|---|---|---|
| Completing a roadmap-linked mission task (or rejecting it as "Already done") completes the roadmap task + recomputes milestone progress | Mission work never reached the roadmap, so tomorrow re-offered finished work and the Health "roadmap done %" signal never moved | `_complete_roadmap_task` in `app/services/mission/service.py`, called from `complete_task` / `reject_task` |
| D-MIS-7: "Wrong priority" rests 7 days, "Doesn't apply" never returns to missions | "Thanks, I'll calibrate." promised calibration but rejected tasks came straight back | `_mission_candidates` (query-only, no migration) used by generation and the Upcoming projection; "Assigned to you" still lists every open task |
| A snoozed task no longer blocks "Mission complete." | Snooze + finish the rest left the day `pending` forever, so no completion state and a broken streak | `_RESOLVED_TODAY` in `_maybe_complete_mission`; `snooze_task` re-checks completion; history/weekly totals leave snoozed out |

| D-MIS-8: every Mission endpoint (reads too) is founder + team member | PRD Module 04 access is F, TM and the FE nav already hides Mission from other roles, but reads were open to mentors, so a direct URL showed a mentor the whole Mission section | `require_role(founder, team_member)` on all `GET /missions/*`; the two tests that asserted mentor reads now assert 403 |
| Migration `0055_mission_roadmap_backfill` (data-only) marks roadmap tasks done that were already finished through a mission (or rejected "Already done") and recomputes milestone progress | Existing workspaces carry the stale state (the local stack had 3 such tasks, still projected in Upcoming) | Idempotent SQL; downgrade is a documented no-op (pre-backfill state isn't recorded and re-opening finished work would be wrong) |

Operate: `alembic upgrade head` (CD runs it on staging). Verified: `tests/services/mission/test_mission_rewalk.py` (9), full suite 2373 passed @97.39%; live on the local stack the
day flipped to `complete` with streak 2 after done + rejected + snoozed (capture in the guide §8). Rejected alternative:
storing a per-task "excluded" flag (a migration) when the reject history already says it.

## Follow-ups
- Per-workspace timezones (scheduler uses `SCHEDULER_TIMEZONE`).
