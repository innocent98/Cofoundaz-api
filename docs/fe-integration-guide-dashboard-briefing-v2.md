# FE Integration Guide — Dashboard briefing v2 (Risks/Opportunities rows, "Do it", mission empty reason)

**Provenance.** Two live sources, both committed:
- **Real LLM, local docker stack** (`:8000`, worker on `gpt-5.6-luna`), local test founder, 2026-10-17:
  `e2e/_captures/dashboard_briefing_live/{summary_live,accept_task,accept_task_again,accept_nav}.json`.
  Section 2's excerpts are from `summary_live.json`, trimmed to the named keys only.
- **e2e journey** `e2e/test_dashboard_ai_briefing.py` (stub LLM, real uvicorn) →
  `e2e/_captures/dashboard_ai_briefing/*.json`, plus `e2e/test_dashboard_kpis.py` for the investor
  role (`summary_investor.json`, `investor_accept_forbidden.json`).

This **extends** `fe-integration-guide-dashboard.md` (briefing/risks/opportunities sections) and
`fe-integration-guide-dashboard-kpis.md`. Same endpoint and auth: `GET /api/v1/dashboard/summary`.

---

## 1. What changed, in FE terms

| Card (PRD 02.1) | Before | Now |
|---|---|---|
| **Risks** | one AI paragraph in `risks.message`. The FE only rendered `items[]`, so the card showed its **empty** copy even when ready | `risks.items[]`: rows with `severity` + `text` + `href`. Render a severity dot + text + **Review** → `href` |
| **Opportunities** | one paragraph | `opportunities.items[]`: rows with `text` + `href` (CTA → `href`) |
| **AI Briefing** | prose only. "Do it" had been removed (it faked completion) | `briefing.id` + `briefing.actions[]` (1 to 3). **Do it** calls the new accept endpoint (§3) |
| **Today's Mission** | an empty list was always "Complete your kickoff assessment…" | `mission.empty_reason` says *why* it is empty (§4) |

The legacy `message` strings are still sent (risks/opportunities are the rows joined with spaces), so
nothing breaks before the FE moves over. Render from `items`, not `message`.

## 2. Shapes (live, real LLM)

> Since 2026-10-20 the generated text no longer states the Health Score number (the sample below, captured earlier,
> still shows one: that is exactly the stale-number bug). See `fe-integration-guide-dashboard-role-leaks.md` §4.

`data.briefing`:
```json
{
  "status": "ready",
  "message": "NovaPay is an idea-stage fintech startup with a thriving health score of 93. Execution and financial visibility are not yet established: no tasks are scheduled or completed today, no tasks were completed this week, and runway and monthly money-in are untracked.",
  "id": "b803f642-fd4c-48a0-87a1-da5acd48eeac",
  "actions": [
    {
      "index": 0,
      "label": "Add one validation task for today",
      "kind": "task",
      "href": "/validation",
      "accepted": false,
      "mission_task_id": null
    },
    {
      "index": 1,
      "label": "Go review the upcoming deadline",
      "kind": "navigate",
      "href": "/roadmap",
      "accepted": false,
      "mission_task_id": null
    },
    {
      "index": 2,
      "label": "Start tracking runway and monthly money-in",
      "kind": "task",
      "href": "/finance",
      "accepted": false,
      "mission_task_id": null
    }
  ]
}
```
(After this capture the prompt was tightened so a `task` label is the task itself, e.g. "Interview 3
target customers", rather than "Add one validation task…". The shape is unchanged.)

`data.risks`:
```json
{
  "status": "ready",
  "message": "One deadline is due within seven days, but no associated work is visible. Runway and monthly money-in are not tracked, limiting financial visibility. No tasks are scheduled or completed, so momentum is not yet measurable.",
  "items": [
    {
      "index": 0,
      "severity": "medium",
      "text": "One deadline is due within seven days, but no associated work is visible.",
      "href": "/roadmap"
    },
    {
      "index": 1,
      "severity": "low",
      "text": "Runway and monthly money-in are not tracked, limiting financial visibility.",
      "href": "/finance"
    },
    {
      "index": 2,
      "severity": "low",
      "text": "No tasks are scheduled or completed, so momentum is not yet measurable.",
      "href": "/mission"
    }
  ]
}
```

`data.opportunities`:
```json
{
  "status": "ready",
  "message": "Add a first validation task to turn the idea into measurable progress. Set a near-term milestone around the upcoming deadline. Start recording runway and monthly money-in for a financial baseline.",
  "items": [
    {
      "index": 0,
      "text": "Add a first validation task to turn the idea into measurable progress.",
      "href": "/validation"
    },
    {
      "index": 1,
      "text": "Set a near-term milestone around the upcoming deadline.",
      "href": "/roadmap"
    },
    {
      "index": 2,
      "text": "Start recording runway and monthly money-in for a financial baseline.",
      "href": "/finance"
    }
  ]
}
```

Field rules:
- `severity` ∈ `high | medium | low` (always one of these; bad model output is coerced to `medium`).
- `href` is **server-mapped** from a closed module list (`/mission`, `/roadmap`, `/health`, `/validation`,
  `/business-builder`, `/marketing`, `/finance`, `/documents`, `/team`, `/assessment`). It **can be
  `null`**: render the row without a CTA then. The model never authors a URL.
- `items` has at most 3 rows and **can be empty while `status == "ready"`** ("nothing genuinely at
  risk"). Show the PRD empty copy in that case: *"No open risks. I'm watching runway, deadlines, and
  pipeline for you."*
- `actions` has 1 to 3 rows when ready, and is `[]` for `empty` / `generating` / `restricted`.

### Statuses (all three blocks share one `status`)
| `status` | Meaning | Render |
|---|---|---|
| `empty` | no kickoff assessment yet | the PRD empty copy (`message` carries it); `id: null`, `items: []` |
| `generating` | being written now | skeleton or "Putting together your briefing…"; refetch once after ~3.5s (existing behaviour) |
| `ready` | done | rows + actions |
| `restricted` | **new**: the viewer isn't a founder/team member. `message: null`, `items: []`, `id: null` | **hide** the three cards |

Investor capture (`e2e/_captures/dashboard_kpis/summary_investor.json`):
```json
"briefing": {"status": "restricted", "message": null, "id": null, "actions": []},
"risks": {"status": "restricted", "message": null, "items": []},
"opportunities": {"status": "restricted", "message": null, "items": []}
```
Why: the briefing is generated from real runway/revenue figures, so its text must not reach roles that
can't open Finance (investor, mentor, advisors, accountant). This mirrors the KPI tiles' gate.

## 3. "Do it": `POST /api/v1/dashboard/briefing/{briefing_id}/actions/{index}/accept`

Auth: Bearer + `X-Workspace-Id`, **founder or team_member** (others get `403`). No body.

`kind: "task"` adds `label` to **today's mission** as a custom task (creating today's mission if there
is none) and returns it. Live `accept_task.json`:
```json
{"data":{"action_index":0,"kind":"task","href":"/validation","mission_task":{"id":"fd8195a3-f71f-4ca3-af96-3136105d7523","roadmap_task_id":null,"title":"Add one validation task for today","reason":null,"effort":"medium","status":"todo","order":0,"completed_at":null,"reject_reason":null}},"meta":null}
```
**Idempotent:** calling it again returns the **same** `mission_task.id` (live `accept_task_again.json`)
and never adds a duplicate. Disable the button while in flight anyway.

`kind: "navigate"` records the click and returns the route. Then `router.push(href)`. Live `accept_nav.json`:
```json
{"data":{"action_index":1,"kind":"navigate","href":"/roadmap","mission_task":null},"meta":null}
```

After a successful accept, refetch the summary. The action then shows `accepted: true` (and
`mission_task_id` for tasks), and the Mission widget shows the new task. Render accepted task actions as
done, e.g. "Added to today's mission ✓" linking to `/mission`.

Errors:
| Status | `code` | `message` | When | Verified |
|---|---|---|---|---|
| 404 | `NOT_FOUND` | `Not found.` | unknown briefing, another workspace's briefing, or `index` out of range | ✅ live: `{"error":{"code":"NOT_FOUND","message":"Not found.","field_errors":[]}}` |
| 403 | (standard permissions error) | | role not founder/team_member | ✅ e2e `investor_accept_forbidden.json` |
| 409 | `BRIEFING_NOT_READY` | `This briefing is still being written.` | status not `ready` | ❌ unit test only |
| 409 | `BRIEFING_STALE` | `That briefing is from an earlier day. Use today's briefing.` | an earlier day's briefing (e.g. a tab left open overnight) | ❌ unit test only (no earlier briefing existed locally) |

On a 409 `BRIEFING_STALE`, refetch the summary to get today's briefing. Don't retry.

## 4. Mission widget: `mission.empty_reason`

`data.mission` gains `empty_reason` (`null` when there are tasks). `data.mission` itself is still `null`
when there's no roadmap (render as `no_roadmap`).

| `empty_reason` | Meaning | Copy (PRD) |
|---|---|---|
| `weekend_off` | weekend, and the "weekend missions" setting is off | **"Weekends off. Rest is a strategy too."** + link to mission settings |
| `all_caught_up` | no open roadmap tasks left to draw from | e.g. "You're all caught up. Add your own task or pick one from your roadmap." |
| `no_roadmap` (when `mission` is `null`) | no roadmap yet | "Complete your kickoff assessment to get your daily plan" |

Live: before the accept, the Saturday summary had `"empty_reason": "weekend_off"`. After the accept,
the task is present and `"empty_reason": null`.

## 5. Notification

When the briefing becomes ready the backend emits **`dashboard.briefing.ready`** → an in-app
notification for every active member, *"Your daily AI briefing is ready"* (PRD 02.1). It has no email
category, so it's in-app only, and it arrives through the existing notifications feed and stream.
Deep-link it to `/dashboard`. Live (`dashboard_briefing_live/notification_briefing_ready.json`):
```json
{"id": "e92e73ff-6e2a-4f7d-948d-f7370b0d2899", "type": "dashboard.briefing.ready", "title": "Your daily AI briefing is ready", "body": "", "data": {"startup_id": "11ba2aa0-8c8d-4035-adb8-62530988ed44", "briefing_id": "15615fdd-0e58-4587-b4b3-215d9fe65e1c"}, "read": false, "created_at": "2026-10-17T22:02:19.293707+00:00"}
```
**Bug fixed along the way:** the worker process never registered the notification subscribers, so
**no** job-published event became a notification: this one, "Your AI business plan is ready", and the
scheduler's "Today's mission is ready" / "A roadmap milestone is overdue" / quarterly check-in. All of
them now arrive.

## 6. Verification

| Behaviour | Live? | Source |
|---|---|---|
| Structured risks/opportunities/actions from the real LLM, server hrefs | ✅ | `dashboard_briefing_live/summary_live.json` |
| Do it (task) adds to today's mission | ✅ | `accept_task.json`; e2e `action_accept_task.json` + `summary_after_accept.json` |
| Do it idempotent | ✅ | `accept_task_again.json`; e2e `action_accept_task_repeat.json` |
| Do it (navigate) | ✅ | `accept_nav.json` |
| 404 out of range | ✅ | live curl; e2e `action_accept_out_of_range.json` |
| 403 investor | ✅ | e2e `investor_accept_forbidden.json` |
| `restricted` blocks for investor | ✅ | e2e `summary_investor.json` |
| `empty_reason` weekend_off → null | ✅ | live summary before/after accept |
| `all_caught_up` | ❌ unit test only | `test_mission_empty_reasons` |
| 409 not-ready / stale | ❌ unit test only | `test_rejects_not_ready_or_stale` |
| Briefing-ready notification | ✅ | `dashboard_briefing_live/notification_briefing_ready.json` (after the worker-registration fix) |
