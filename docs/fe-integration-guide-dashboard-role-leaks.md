# FE integration guide: Dashboard role leaks (D-MIS-8)

Rows 1, 2, 3, 8 and 9 of the Dashboard re-walk (`docs/checklist/dashboard-rewalk.md` in the FE repo, PR #120).
Rule D-MIS-8: **Today's Mission reads are founder + team member only** (`GET /missions/today` already 403s for
everyone else). Every body below is pasted from a live run of `e2e/test_dashboard_role_leaks.py` (captures in
`e2e/_captures/dashboard_role_leaks/`, named in each section). Ids, timestamps and scores are throwaway test data.
The change is **additive** (one new key) plus deliberate removals of data a role was never meant to see.

Roles below: "core" = `founder` and `team_member`. "Other" = `mentor`, `accountant`, `legal_advisor`,
`business_consultant`, `investor`.

| Route | Core roles | Other roles |
|---|---|---|
| `GET /dashboard/summary` | `mission` object + `mission_status: "ok"` | `mission: null` + `mission_status: "restricted"` |
| `GET /search` | finds mission tasks, the Mission page, the Assessment page | none of the three |
| notifications | unchanged | no longer receive `mission.*`, `dashboard.briefing.ready` (and the others in section 3) |

---

## 1. `GET /dashboard/summary`: `mission` and the new `mission_status` (row 1)

One new top-level key, **`mission_status`**: `"ok"` or `"restricted"`. It is always present.

- `"restricted"` means the caller's role cannot open Today's Mission. `mission` is `null`. **Hide the Mission card;
  do not show an empty state ("No roadmap yet" would be wrong).**
- `"ok"` and `mission: null` still means what it always meant: the workspace has no roadmap.
- Nothing is generated for a restricted role: a mentor opening the dashboard no longer creates today's mission row.

Mentor (`summary_mentor.json`, trimmed to the relevant keys; the other keys are unchanged):
```json
{
  "data": {
    "mission": null,
    "mission_status": "restricted",
    "briefing": { "status": "restricted", "message": null, "id": null, "actions": [] },
    "risks": { "status": "restricted", "message": null, "items": [] },
    "opportunities": { "status": "restricted", "message": null, "items": [] }
  },
  "meta": null
}
```
`summary_business_consultant.json` carries the same `mission` / `mission_status` pair. `briefing`, `risks` and
`opportunities` were already `restricted` for these roles; `mission_status` follows the same convention
(`status: "restricted"` blocks), as a sibling key because `mission` itself is a nullable object, not a `{status}` block.

Founder and team member (`summary_founder.json`, `summary_team_member.json`), trimmed:
```json
{
  "data": {
    "mission": {
      "mission_date": "2026-10-20",
      "status": "pending",
      "streak": 0,
      "tasks": [
        {
          "id": "3d405cc0-1593-4707-939b-d0628bea8664",
          "roadmap_task_id": "3613767a-f2d5-49fa-8657-56216f87062c",
          "title": "Run 10 customer interviews",
          "reason": "From your 'Validate demand' milestone.",
          "effort": "medium",
          "status": "todo",
          "order": 0,
          "completed_at": null,
          "reject_reason": null,
          "milestone": { "id": "f3e5776b-4d2a-40a8-a2a9-e0f73580530f", "title": "Validate demand", "due_on": "2026-11-03" }
        }
      ],
      "empty_reason": null
    },
    "mission_status": "ok"
  },
  "meta": null
}
```
(The capture has 3 tasks; one is shown.) Other captures with the new key: `e2e/_captures/dashboard/summary.json`,
`dashboard_kpis/summary_investor.json` (investor: restricted), `dashboard_kpis/summary_accountant.json` (accountant:
restricted).

**Trap.** The same role used to get the full `mission` object; an FE that renders `data.mission.tasks` without a null
check will now throw for a mentor. Branch on `mission_status` first, then on `mission === null`.

**Not changed (see the SOP follow-ups):** `kpis.tasks_done_this_week` (a count derived from mission tasks) and
`calibration.assessment_complete` are still returned to every role.

---

## 2. `GET /search` (rows 2 and 9)

Search is filtered server-side; never filter client-side.

- **Mission tasks** (`type: "mission_task"`) are only returned to core roles. Founder searching `Run 10 custo`
  (`search_mission_task_founder.json`):
```json
{
  "data": {
    "query": "Run 10 custo",
    "results": [
      { "type": "roadmap_task", "group": "Roadmap", "id": "3613767a-f2d5-49fa-8657-56216f87062c", "title": "Run 10 customer interviews", "subtitle": "Task in Validate demand", "href": "/roadmap" },
      { "type": "mission_task", "group": "Mission", "id": "3d405cc0-1593-4707-939b-d0628bea8664", "title": "Run 10 customer interviews", "subtitle": "Mission task · 2026-10-20", "href": "/mission" }
    ]
  },
  "meta": null
}
```
  Mentor, same query (`search_mission_task_mentor.json`): only the `roadmap_task` row (the roadmap is readable by
  every member), no `mission_task` row.
- **The "Today's Mission" page** is a core-role result. Mentor searching `mission` (`search_mission_page_mentor.json`)
  gets only `Mission & Vision` (a Business Builder page); the founder (`search_mission_page_founder.json`) gets
  `Today's Mission` (`/mission`) first.
- **The "Assessment" page** (`/assessment`) is now offered to team members as well as founders (it was founder only,
  although both see it in the sidebar). Team member searching `assessment` (`search_assessment_team_member.json`):
```json
{
  "data": {
    "query": "assessment",
    "results": [
      { "type": "page", "group": "Pages", "id": "/assessment", "title": "Assessment", "subtitle": null, "href": "/assessment" }
    ]
  },
  "meta": null
}
```
  Mentor (`search_assessment_mentor.json`): `{"data": {"query": "assessment", "results": []}, "meta": null}`.
- Every other source and page was audited against the read gate of the module it opens and needed no change
  (documents, roadmap, Business Builder, Health, Notifications, Settings: any member; Validation, Calendar, Academy,
  AI chat, Marketing: core; Finance: core + accountant; Journal: founder). The audit is a test
  (`tests/services/test_search_gates.py`).

---

## 3. Notification audiences (row 3)

The feed shape is unchanged. Who gets which type changed:

| Type | Was | Now |
|---|---|---|
| `dashboard.briefing.ready` | every member | founder + team member |
| `mission.ready` | every member | founder + team member |
| `mission.completed` | every member except the actor | founder + team member, except the actor |
| `mission.streak.milestone` | every member except the actor | founder + team member, except the actor |
| `finance.runway.low` | every member | founder, team member, accountant |
| `marketing.post.published`, `marketing.campaign.launched`, `marketing.campaign.completed` | every member except the actor | founder + team member, except the actor |

Live (`notifications_team_member.json`, trimmed): after the founder completed the whole mission, the team member's feed
holds both of these:
```json
[
  {
    "id": "ab3625a9-530b-41d3-a738-dfe44980ee51",
    "type": "mission.completed",
    "title": "Today's mission is complete",
    "body": "",
    "data": { "mission_id": "745a31de-b7a6-4128-9abf-1f4e2c3ae48a", "startup_id": "482ae457-86a1-4f59-93c7-5ddf66d37bd5", "mission_date": "2026-10-20" },
    "read": false,
    "created_at": "2026-10-20T15:16:08.896023+00:00"
  },
  {
    "id": "4116f758-ea28-4eef-b910-86df1b5a3d61",
    "type": "dashboard.briefing.ready",
    "title": "Your daily AI briefing is ready",
    "body": "",
    "data": { "startup_id": "482ae457-86a1-4f59-93c7-5ddf66d37bd5", "briefing_id": "a8ac6676-7685-4643-bd6a-d74d3c1e2410" },
    "read": false,
    "created_at": "2026-10-20T15:16:08.748170+00:00"
  }
]
```
The mentor's feed in the same run (`notifications_mentor.json`) holds only `workspace.member.joined`; the business
consultant received no `mission.*` or `dashboard.briefing.ready` either (asserted in the journey).

Checked and **kept for every member** because the page each opens is readable by every role: `healthscore.dropped`,
`healthscore.record`, `roadmap.*`, `business.suggestion.approved|rejected`, `business.artifact.completed`,
`business.plan.generated`, `document.*`, `workspace.member.joined`.

---

## 4. The briefing no longer quotes a stale Health Score (row 8)

The briefing text used to include the score as it was when generated (e.g. "88") while the card beside it shows the
live score (e.g. "95"). The generation prompt no longer receives the number and is told never to state it or its
change; it gets the band (`strong`, `at_risk`, ...) so it can still talk in words.

- **Response shape: unchanged.** No FE change is needed for this row.
- Briefings generated **before** the deploy keep their old text until the next daily regeneration, so a stale number
  can still appear for up to a day.
- "Tell me more" is an FE action: if it prefills a chat prompt with the briefing text, that text now contains no score.
- **Not verified live:** the e2e runs with `LLM_PROVIDER=stub`, which never writes a number. The prompt is asserted in
  unit tests (below).

---

## 5. Verification

| Behaviour | Verified | Source |
|---|---|---|
| Mentor summary: `mission: null`, `mission_status: "restricted"` | live | `summary_mentor.json` |
| Business consultant summary: same | live | `summary_business_consultant.json` |
| Investor / accountant summaries carry `mission_status: "restricted"` | live | `dashboard_kpis/summary_investor.json`, `summary_accountant.json` |
| Founder + team member: full mission + `"ok"` | live | `summary_founder.json`, `summary_team_member.json` |
| Mentor still 403 on `GET /missions/today` | live | `missions_today_mentor_403.json` |
| No mission row is generated by a restricted role's summary | unit | `tests/api/test_dashboard_role_leaks.py::test_summary_does_not_lazily_generate_a_mission_for_a_blocked_role` |
| All 7 `MembershipRole` values on summary and search | unit | `tests/api/test_dashboard_role_leaks.py` (parametrised) |
| Mission task / Mission page hidden from mentor, shown to founder | live | `search_mission_task_*.json`, `search_mission_page_*.json` |
| Assessment page for team member, hidden from mentor | live | `search_assessment_team_member.json`, `search_assessment_mentor.json` |
| Every search source + page vs its module gate | unit | `tests/services/test_search_gates.py` |
| `mission.completed` and `dashboard.briefing.ready` reach the team member, not the mentor or consultant | live | `notifications_team_member.json`, `notifications_mentor.json` |
| Every spec in the registry has a declared audience, exact role set per type | unit | `tests/services/notifications/test_dashboard_audience.py` |
| `mission.ready`, `mission.streak.milestone`, `finance.runway.low`, `marketing.*` audiences | unit only | same file (the scheduler tick / marketing triggers are not part of this journey) |
| Briefing prompt carries no Health Score number | unit only | `tests/services/dashboard/test_ai_briefing.py` |
| Real LLM output no longer quotes a number | not verified | e2e uses the stub LLM |
