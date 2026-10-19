# FE Integration Guide — Today's Mission, PRD completion (Module 04)

**Provenance.** Two live sources, both committed:
- **e2e journey** `e2e/test_mission_prd.py` (real uvicorn) → `e2e/_captures/mission_prd/*.json`.
- **Local docker stack**, 2026-10-18 (a Sunday, weekends off), local test founder → `e2e/_captures/mission_prd_live/*.json`.

This **extends** `fe-integration-guide-mission.md`, which still holds for everything not listed here. The gap list this
closes is `cofoundaz/docs/checklist/mission-prd-gaps.md`. All routes: Bearer + `X-Workspace-Id`.

## 1. `GET /missions/today`: three additions
Every task now has **`milestone`**: `{id, title, due_on}` for roadmap tasks, `null` for custom tasks. Render it as the
PRD's **milestone link chip** → `/roadmap/milestones`. `reason` was always there: render it as the PRD why-line
**"Why: {reason}"** when it is non-null (it starts as "From your '{milestone}' milestone." and is replaced by the AI's
reason once the worker writes it).

e2e `today.json` (trimmed to the first task):
```json
{
  "id": "866a01aa-5561-4e5c-bbd6-198bd3bdbce6",
  "roadmap_task_id": "f3b09749-1f5e-4315-8480-3a4ba012f5b7",
  "title": "Run 10 customer interviews",
  "reason": "From your 'Validate demand' milestone.",
  "effort": "medium",
  "status": "todo",
  "order": 0,
  "completed_at": null,
  "reject_reason": null,
  "milestone": {
    "id": "cc49b3c8-9738-4d50-8a42-174eb2288503",
    "title": "Validate demand",
    "due_on": "2026-11-01"
  }
}
```
The mission object also gains:
- **`empty_reason`**: `null` when there are tasks; `weekend_off` → **"Weekends off. Rest is a strategy too."** with a link to
  `/mission/settings`; `all_caught_up` → "You're all caught up. Add your own task or pick one from your roadmap." Same values
  as the dashboard's `mission.empty_reason`.
- **`assigned_to_me`**: open roadmap tasks assigned to the viewer (PRD access: "own assigned tasks appear"), soonest
  milestone first, max 10, same row shape as Upcoming tasks (§2). Show an **"Assigned to you"** section when non-empty
  (typically for team members). `[]` for the e2e founder.

No-roadmap response is now (`test_today_without_roadmap_still_reports_assigned`):
```json
{"status": "no_roadmap", "assigned_to_me": []}
```
Render the PRD empty state **"Your mission comes from your roadmap."** with CTA **"Generate my roadmap"** →
`POST /api/v1/roadmap/generate` (see `fe-integration-guide-roadmap.md`), then refetch.

## 2. `GET /missions/upcoming` (new): PRD 04.2
The next 7 days, **projected** from the roadmap queue. Nothing is created, so it always reflects the live roadmap and
settings. Rules: `mission_size` tasks per day; weekends are `rest_day: true` when weekend missions are off; today's
**snoozed** tasks lead the next working day (`carried_over: true`); today's other tasks are assumed done.

e2e `upcoming.json` (first day, trimmed):
```json
{
  "data": {
    "has_roadmap": true,
    "mission_size": 3,
    "days": [
      {
        "date": "2026-10-19",
        "rest_day": false,
        "tasks": [
          {
            "roadmap_task_id": "bab1bbb4-1e36-436d-b8e0-ca2fd2769183",
            "title": "Run a willingness-to-pay test",
            "effort": "medium",
            "milestone": {
              "id": "b86621e6-e38a-457f-9fdb-39f071e78b32",
              "title": "Pricing test",
              "due_on": "2026-11-22"
            },
            "carried_over": false
          }
        ]
      }
    ]
  },
  "meta": null
}
```
- No roadmap: `{"has_roadmap": false, "days": []}`. Show the same "Generate my roadmap" empty state.
- **A working day can have `tasks: []` when the roadmap queue runs out** (live: Wed to Fri on the small test roadmap). Show
  "Nothing queued yet. Add tasks to your roadmap." with a link, not a blank row.
- `rest_day: true` → "Rest day".

## 3. `PUT /missions/upcoming/order` (new): "editable order"
Founder/team member only (mentor etc. → **403**). Body: every `roadmap_task_id` currently shown in Upcoming, in the new
order (1 to 200 ids):
```json
{"roadmap_task_ids": ["a7eb83f8-…", "bab1bbb4-…", "…"]}
```
Returns the **recomputed** Upcoming (same shape as §2). e2e `upcoming_after_reorder.json`: after swapping the first two,
day 1 starts with "Define the core user journey". The order persists, and **real missions** are generated in it from
the next generation on. Implement as drag-and-drop or ↑/↓ on the Upcoming list, then send the whole visible order.

Errors (verbatim, e2e `upcoming_order_duplicate_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Each task can appear only once.",
    "field_errors": [
      {
        "field": "roadmap_task_ids",
        "message": "Duplicate task."
      }
    ]
  }
}
```
Also 422 "Some tasks aren't open tasks on your roadmap." (a stale list: refetch) and 409 `NO_ROADMAP` "Generate your
roadmap first." (both unit-tested).

## 4. `GET /missions/completed?cursor=&limit=` (new): PRD 04.3 log
Completed tasks, newest first; `limit` 1 to 50 (default 20); keyset `next_cursor` (null on the last page). e2e
`completed.json`:
```json
{
  "data": {
    "items": [
      {
        "id": "866a01aa-5561-4e5c-bbd6-198bd3bdbce6",
        "title": "Run 10 customer interviews",
        "completed_at": "2026-10-18T00:34:12.056573+00:00",
        "mission_date": "2026-10-18",
        "milestone": {
          "id": "cc49b3c8-9738-4d50-8a42-174eb2288503",
          "title": "Validate demand",
          "due_on": "2026-11-01"
        }
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```
Group the items by `mission_date` under the existing day rows, and keep the weekly % chip from `/missions/history`.
A bad cursor → 422 "Invalid pagination cursor.". Show "Load more" while `next_cursor` is non-null.

## 5. `GET /missions/history`: streaks
Adds **`current_streak`** and **`best_streak`** (e2e `history.json`: both `0` for a fresh founder; live test founder: `1`
and `1`). Render **best streak** on `/mission/streaks` (PRD 04.4). **Behaviour change:** weekends-off rest days no longer
break a streak (they neither add nor reset), so "Rest is a strategy too." is now true.

## 6. Settings now matter + notifications
- `delivery_time` from `PATCH /missions/settings` now **drives the daily generation and notification** for that workspace
  (it used to be ignored). Times are in the server's scheduler timezone.
- Notification `mission.ready`: title **"Your mission for today is ready"**, body = the first task's title,
  `data.first_task_title`. Not sent on rest days or when there's nothing to do.
- Notification `mission.streak.milestone` (7/30/100): **"🔥 {n} days straight. You're building a habit most founders never do."**

## 7. Verification
| Behaviour | Live? | Source |
|---|---|---|
| `milestone` on today / completed task | ✅ | e2e `today.json`, `task_completed.json` |
| `empty_reason`, `assigned_to_me` on today | ✅ (e2e founder: null, []) | `today.json`; team-member list unit-tested (`test_today_lists_my_assigned_tasks_and_empty_reason`) |
| Upcoming projection + rest days + empty queue days | ✅ | e2e `upcoming.json`; local `mission_prd_live/upcoming.json` (Sat/Sun rest, Wed to Fri empty) |
| Reorder + 422 duplicate | ✅ | `upcoming_after_reorder.json`, `upcoming_order_duplicate_422.json` |
| Reorder 403 for mentor; 409 no roadmap; 422 foreign task | ❌ unit | `test_endpoints_and_roles`, `test_set_order_*` |
| Completed log + pagination | ✅ (1 page) / ❌ unit (cursor) | `completed.json`; `test_completed_log_newest_first_with_cursor` |
| current/best streak | ✅ | `history.json`; local `mission_prd_live/history.json` |
| Rest days don't break streaks | ❌ unit | `test_weekends_off_rest_days_do_not_break_the_streak` |
| Delivery time drives generation | ❌ unit | `test_mission_is_due_at_the_workspace_delivery_time` |
| mission.ready copy + first task body | ✅ | e2e `e2e/test_notifications_scheduler.py` asserts title/body |
| No "ready" ping on an empty rest day | ❌ unit | `test_mission_generate_does_not_announce_an_empty_mission` |
