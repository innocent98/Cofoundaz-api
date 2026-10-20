# SOP: Dashboard activity feed filtered by role (D-MIS-8 follow-up)

| | |
|---|---|
| **What shipped** | `GET /dashboard/activity` and the live `activity.created` stream event are filtered by the caller's membership role. Branch `fix/dashboard-activity-feed-roles` (PR number filled in when opened). Follow-up to #153. |
| **Why** | Security. #153 gated the Dashboard mission, briefing, search and notifications by role, but the activity feed still returned every `activity_log` row to every member, and the SSE stream forwarded every `activity.created` on the workspace channel. A mentor could read mission task titles ("Ada added 'Call the seed lead investor' to today's mission") and, once written, finance and marketing events. Flagged by the Dashboard re-walk and by the security review on #153. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| One audience policy in `app/platform/activity.py`: a row is restricted by its action **namespace** (first dotted segment) **or** its `entity_type`. `mission` / `mission_task` → `MISSION_ROLES`; `finance` / `transaction`, `invoice`, `expense`, `budget` → `FINANCE_ROLES`; `marketing` / `campaign`, `post`, `keyword`, `segment` → `MARKETING_ROLES` | Reuses the role sets from `app/db/models/enums.py`, so the feed can't drift from the routes and from #153's search/notification gates. Checking `entity_type` too means a mission task can't leak through some other action name | prefix-only matching (a `roadmap.task.linked` row about a mission task would leak) |
| Open namespaces: `healthscore`, `roadmap`, `business`, `document` (as decided in #153), plus `assessment` and `member` / `workspace` | see the audience table below | |
| **Unclassified namespaces fail closed** to founder + team member | a new module that starts writing activity can't leak by default. `tests/platform/test_activity_visibility.py` scans the app for every `write_activity(action=...)` and fails if one is unclassified, so it won't silently vanish either | defaulting open |
| Filter in SQL (`activity_visibility_filter(role)`), not after the query | `LIMIT limit + 1` counts visible rows only, so pages stay full and `next_cursor` stays correct; one SELECT per page (no N+1, no refill loop). `split_part(action, '.', 1)` matches the Python split exactly, so there's no `LIKE` escaping | post-filtering in Python and over-fetching / looping to refill pages |
| Stream: `_active_membership` → `_membership_role` (role or `None`). Role read at connect and refreshed on the existing membership re-check timer; each `activity.created` is checked with `activity_visible(role, ...)` before it's yielded | the channel is workspace-wide (one publish per row), so filtering has to happen per recipient at delivery. Refreshing on the re-check means a demoted member is re-filtered without reconnecting | per-role channels (changes the publish path and the after-commit listener for no gain) |
| A stream whose membership is already gone at connect yields `revoked` right away | the endpoint checks before opening, but the generator no longer assumes it | |

## Activity type audit (every `write_activity` call site)
| Action | Written by | Audience | Why |
|---|---|---|---|
| `mission.task.added` | `endpoints/mission.py`, `services/dashboard/briefing.py` (accept action) | founder, team member | D-MIS-8 |
| `mission.task.completed` / `snoozed` / `rejected` | `endpoints/mission.py` | founder, team member | D-MIS-8 |
| `roadmap.milestone.completed`, `roadmap.replanned` | `endpoints/roadmap.py` | every role | roadmap reads are `require_workspace` (#153) |
| `roadmap.milestone.broken_down` | `worker/handlers/ai.py` | every role | same; the summary says "AI broke '<milestone>' into N tasks" (roadmap tasks, not mission tasks) |
| `assessment.completed` | `endpoints/assessments.py` | every role (**flagged as unclear**) | the summary is "<name> completed the startup assessment", no score; the same event is already shown to every role as a Health Score history marker (`health_score.service.MARKER_LABELS`). Assessment *reads* are founder + team member, so if the lead wants the row gated, move `assessment` from `_OPEN_NAMESPACES` to `_RESTRICTED_NAMESPACES` |
| `member.joined` | `endpoints/invitations.py` | every role | team membership is visible to every member |
| `finance.*`, `marketing.*` | **no writer yet** | finance / marketing roles | rules ready for when a module starts writing them |

## What's involved
- `app/platform/activity.py`: `activity_visible()`, `activity_visibility_filter()`, the namespace/entity tables.
- `app/services/dashboard/service.py:344` `get_activity(db, startup, role, ...)`; `app/api/v1/endpoints/dashboard.py` passes `membership.role`.
- `app/api/v1/endpoints/notifications.py`: `_membership_role`, per-recipient filter in `_event_stream`, endpoint check.
- No migration, no config, no response-shape change.
- Tests: `tests/api/test_dashboard_activity_roles.py` (mentor never sees a mission title; every `MembershipRole` vs finance/marketing; core sees all; fail-closed unclassified; restricted entity under an open action; full pages across hidden runs; one SQL statement per page), `tests/api/notifications/test_stream.py` (every role on the stream, role change mid-stream, inactive membership 403), `tests/platform/test_activity_visibility.py` (SQL == Python for every role; every written action classified).
- e2e `e2e/test_dashboard_activity_roles.py`, captures `e2e/_captures/dashboard_activity_roles/`; guide `docs/fe-integration-guide-dashboard-role-leaks.md` section 5, pointer in `docs/fe-integration-guide-dashboard.md`.

## Verification
TDD: the new feed tests failed on the old code (10 of 14), the stream tests failed (11 of 13; two of those are the existing tests, which now patch the renamed `_membership_role`), and the e2e journey failed on the old code (the mentor's stream received the mission, finance and marketing frames), then all passed with the fix. Full unit suite with CI's coverage gate (98%), black / isort / ruff / mypy / pylint / bandit, semgrep on the changed files, the alembic single-head / upgrade / check / round-trip steps, and the e2e suite, all run locally against scratch databases (see the PR for counts).

Not verified live: a finance or marketing row produced by a real product endpoint (none exists yet); the e2e writes those rows in-process with `write_activity` and labels them as test data.

## Operate / roll back
Deploy as usual; no migration. Roll back by reverting the commit. The FE needs no change: same shapes, fewer rows for non-core roles.

## Follow-ups
- `assessment.completed` audience: kept open (see the table); needs a lead decision if it should follow the assessment read gate.
- Health Score markers (`health_score.service.MARKER_LABELS`) look for `workspace.member.joined`, but the invitation endpoint writes `member.joined`, so the "Teammate joined" marker never appears. Pre-existing, unrelated to roles.
- Feed query cost for a mostly-hidden workspace: the filter runs on the `(startup_id, created_at, id)` index scan, so a mentor in a workspace with thousands of consecutive mission rows scans them per page. Fine at current volumes; an expression index on `split_part(action, '.', 1)` is the fix if it ever shows up.
