# SOP: Dashboard role leaks (D-MIS-8)

| | |
|---|---|
| **What shipped** | Rows 1, 2, 3, 8, 9 of the Dashboard re-walk (FE repo `docs/checklist/dashboard-rewalk.md`, PR #120). Branch `fix/dashboard-role-leaks` (PR number filled in when opened). |
| **Why** | Security. Rule D-MIS-8 says Today's Mission reads are founder + team member only, and `GET /missions/today` enforced it, but three other doors did not: the Dashboard summary returned the whole mission (titles, reasons) to mentors and every other role, global search returned mission tasks and linked the Mission page, and several notifications fanned out to every member. Same class as the Assessment leak fixed in #152. Plus two product bugs: the briefing prose quoted a stale Health Score, and team members could not find the Assessment page in search. |

## How (decisions, all reversible)
| Decision | Why | Rejected |
|---|---|---|
| One shared role set: `MISSION_ROLES` (and `BRIEFING_ROLES = MISSION_ROLES`) in `app/db/models/enums.py`, next to `FINANCE_ROLES` / `MARKETING_ROLES`. `dashboard.service.BRIEFING_ROLES` is now an import of it | The briefing already used founder + team member. One definition shared by the summary, search and the notification registry cannot drift | a fourth copy of `{founder, team_member}` in each module |
| Row 1: `mission: null` for other roles **plus a new sibling `mission_status: "ok" \| "restricted"`** (always present). The mission section is not even evaluated for them | `mission: null` alone already means "no roadmap", so the FE could not tell the two apart. The existing `restricted` convention is a `{status}` block (briefing, KPI tiles); `mission` is a nullable object, so a sibling key keeps the old shape for allowed roles. Not evaluating it also stops `_mission_section` from lazily **writing** today's mission when a mentor opens the dashboard | replacing `mission` with `{status: "restricted"}` for all roles (breaking for founders) |
| Row 2: `mission_task` source and the "Today's Mission" page use `MISSION_ROLES`. Every other source and page was audited against the route gates of the module it opens (roadmap, documents, Business Builder, Health: any member; Validation, Calendar, Academy, AI, Marketing: core; Finance: + accountant; Journal: founder). No other leak found | search "never surfaces a row the caller couldn't open" is the stated contract; now enforced by `tests/services/test_search_gates.py` (a new source or page must declare its gate or the test fails) | fixing only the mission rows |
| Row 3: `registry._roles_minus_actor(roles)` factory, one query on `Membership.role`. Applied to briefing, mission and finance/marketing types | same shape as the existing `_kpi_audience` / `_assessment_members_minus_actor` | per-type bespoke functions |
| Row 8: stop passing `health_score` into the briefing prompt (removed from `gather_briefing_context` and `build_dashboard_briefing_messages`); the user message keeps only the band, and the system prompt says the dashboard shows the live score beside the text, so never state the number or its change | the prose is generated once a day and stored, the card is live; any number in the text is wrong by evening. Removing the input fixes it at the source, with no new read-time moving parts. The model can still say "your health is strong" in words | substituting a placeholder token at read time (needs the prose to carry a token, a parser, and handles the "Tell me more" prefill separately); post-filtering digits out of the generated text (fragile, strips legitimate numbers) |
| Row 9: Assessment page in search is `CORE_ROLES` (was founder only) | assessment reads are founder + team member since D-ASM-1 (#152), and it is in the team member's sidebar | |

## Notification audit (`app/services/notifications/registry.py`)
Read gate = who can open the feature the notification links to. "Core" = founder + team member.

| Spec | Old audience | New audience | Why |
|---|---|---|---|
| `dashboard.briefing.ready` | every member | core | briefing prose is built from finance figures and is `restricted` for other roles |
| `mission.ready` | every member | core | D-MIS-8 |
| `mission.completed` | every member minus actor | core minus actor | D-MIS-8 |
| `mission.streak.milestone` | every member minus actor | core minus actor | D-MIS-8 (same page) |
| `finance.runway.low` | every member | founder, team member, accountant | Finance Hub gate (`FINANCE_ROLES`) |
| `marketing.post.published` | every member minus actor | core minus actor | Marketing Hub gate (`MARKETING_ROLES`) |
| `marketing.campaign.launched` | every member minus actor | core minus actor | same |
| `marketing.campaign.completed` | every member minus actor | core minus actor | same |
| `healthscore.dropped`, `healthscore.record` | every member | unchanged | Health reads are open to every role (`require_workspace`) |
| `roadmap.replanned`, `roadmap.milestone.completed\|overdue\|due_soon` | every member (minus actor where applicable) | unchanged | Roadmap reads open to every role |
| `business.suggestion.approved\|rejected`, `business.artifact.completed`, `business.plan.generated` | every member (minus actor where applicable) | unchanged | Business Builder reads open to every role |
| `business.suggestion.created` | founders | unchanged | |
| `document.shared`, `document.signature.*` | every member minus actor | unchanged | Documents list is workspace-wide for every role (see follow-ups) |
| `workspace.member.joined` | existing members | unchanged | Team reads open to every role |
| `assessment.completed`, `assessment.quarterly.due` | core minus actor / founders | unchanged | fixed in #152 |
| `validation.*`, `kpi.threshold.crossed` | core / finance-or-marketing roles | unchanged | already gated |
| `team.comment.mentioned` | the mentioned user | unchanged | direct |

`tests/services/notifications/test_dashboard_audience.py` lists every spec with its allowed roles and fails if a new
spec is added without a declared audience.

## What's involved
- `app/db/models/enums.py` (`MISSION_ROLES`, `BRIEFING_ROLES`), `app/services/dashboard/service.py` (`get_summary`, `gather_briefing_context`), `app/services/dashboard/ai_briefing.py` (prompt), `app/services/search/service.py` (`PAGES`, `SOURCES`), `app/services/notifications/registry.py` (`_with_roles`, `_roles_minus_actor`, 8 specs). No migration, no config.
- Tests: `tests/api/test_dashboard_role_leaks.py` (every `MembershipRole`: summary, no lazy generation, `/missions/today` 403, search tasks/pages), `tests/services/test_search_gates.py`, `tests/services/notifications/test_dashboard_audience.py`, `tests/services/dashboard/test_ai_briefing.py` (prompt has no score); `tests/services/test_dashboard_summary.py` and `e2e/test_dashboard.py` updated for the new key.
- e2e `e2e/test_dashboard_role_leaks.py`, captures `e2e/_captures/dashboard_role_leaks/` (and the existing dashboard summary captures regenerated, they now carry `mission_status`); guide `docs/fe-integration-guide-dashboard-role-leaks.md`, with pointers added to the dashboard, search and notifications guides.

## Verification
TDD: the new tests were written first and failed on the old code (34 failures: missing key, mission returned, task rows returned, wrong audiences, score in prompt). Then full unit suite with CI's coverage gate, black / isort / ruff / mypy, and the e2e suite (78/78) with the new mentor/consultant journey, run in this worktree against scratch databases. See the PR description for the counts. Not verified live: that a real LLM stops quoting a number (the e2e uses the stub client).

## Operate / roll back
Deploy as usual; no migration, no worker config. Restart the worker anyway if its image is rebuilt (the prompt and registry are imported there). Roll back by reverting the commit. The FE should be able to cope with `mission === null` and read `mission_status` before this deploys (the key is additive; an FE that ignores it will just see `mission: null` for non-core roles).

Already-stored briefings keep their old text (possibly with a stale score) until tomorrow's regeneration.

## Follow-ups
- `GET /dashboard/summary` still returns, to every role: `kpis.tasks_done_this_week` / `tasks_trend` (a count derived from mission tasks, so a mission aggregate), and `calibration.assessment_complete` (a fact about the Assessment). Low sensitivity, but the same class; hiding them means a tile change on the FE, so it needs a decision.
- `GET /dashboard/activity` and the `activity.created` stream are not filtered by role: mission task completions, finance and marketing actions appear in every member's feed. Needs its own pass (filter by action prefix vs. entity type).
- Unsure, not changed: `document.shared` / `document.signature.*` go to every member because `GET /documents` is open to every role; if shares are meant to be targeted, the audience should be the share's recipients.
- `GET /health-score` recommendations (`top_recommendations[].href`) link to `/finance`, `/documents/signatures` for roles that cannot open them. Pre-existing, FE can already hide links to pages the role lacks.
- Briefing: `mission_done`/`mission_total` and `tasks_done_week` are also frozen at generation time; they can go stale the same way the score did.
