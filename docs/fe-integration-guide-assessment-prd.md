# FE integration guide: Assessment PRD (Module 07) gaps

Gap rows 5, 6, 7, 8, 9, 11, 12 and 13 (BE parts) of the Assessment gap list, decisions D-ASM-1..4. Every body below
is pasted from a live run of `e2e/test_assessment_prd.py` (captures in `e2e/_captures/assessment_prd/`, named in
each section). Ids, timestamps and scores are throwaway test data. All changes are **additive** except the role
gate (row 5), which is a deliberate behaviour change.

Base `/api/v1/assessments`. Every route needs `Authorization` + `X-Workspace-Id`.

| Route | Who | Notes |
|---|---|---|
| `POST /assessments`, `GET /{id}/next-question`, `POST /{id}/answers`, `POST /{id}/complete` | founder only (unchanged) | |
| `GET /assessments`, `GET /assessments/{id}`, `GET /assessments/compare` | **founder + team member only** (was: any member) | everyone else gets the 403 below |

---

## 1. Who can read assessments (row 5, D-ASM-1)

Mentor, accountant, legal advisor, business consultant and investor now get **403** on all three reads. Founders and
team members are unchanged. A user who is not a member of the workspace at all still gets 403; another workspace's
assessment id still gets 404.

`list_mentor_403.json`, `detail_mentor_403.json` and `compare_mentor_403.json` are byte-identical:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
**FE instruction:** do not show the Assessment page, its nav entry or its cards to roles other than founder and
team member. If you render the page anyway for a blocked role, handle `FORBIDDEN` as "not available", not as an error toast.
`list_team_member_200.json` is a normal list response (section 3).

## 2. Progress on the question flow (row 7, D-ASM-2)

`progress` is `{answered, remaining, pct}`; `pct` is `answered / (answered + remaining)` as an integer, rounded down.
`remaining` counts only the questions still applicable **given the answers so far**, so the percentage can move
sideways or drop when an answer opens a follow-up. Do not animate it as monotonic.

It is added **next to `next_question`** on `POST /assessments` (201), `GET /{id}/next-question` and
`POST /{id}/answers`.

`start.json` (`POST /assessments`):
```json
{
  "data": {
    "assessment_id": "7e7286ef-d15f-4dc4-a76d-df4bbf35721d",
    "type": "initial",
    "status": "in_progress",
    "next_question": {
      "key": "product_stage",
      "dimension": "product",
      "section": "Product",
      "qtype": "single_choice",
      "prompt": "How far along is your product?",
      "options": [
        {"value": "idea", "label": "Just an idea"},
        {"value": "prototype", "label": "A prototype"},
        {"value": "mvp", "label": "A working MVP"},
        {"value": "live", "label": "Live with users"}
      ],
      "helper": null
    },
    "progress": {"answered": 0, "remaining": 8, "pct": 0}
  },
  "meta": null
}
```
(options re-flowed onto single lines here; the capture has them expanded.) After answering `product_stage = live`,
`answer_progress.json` (`POST /{id}/answers`) shows the path widening (a follow-up question appeared, so 8 remain, not 7):
```json
{
  "data": {
    "next_question": {
      "key": "product_confidence",
      "dimension": "product",
      "section": "Product",
      "qtype": "scale_1_5",
      "prompt": "How confident are you in your core feature set?",
      "options": null,
      "helper": "1 = not at all, 5 = very confident"
    },
    "progress": {"answered": 1, "remaining": 8, "pct": 11}
  },
  "meta": null
}
```
`next_question_progress.json` (`GET /{id}/next-question`) is the same body.

**Trap:** when every question is answered, `next_question` is `null` and `progress` is `{remaining: 0, pct: 100}` while
the assessment is still `in_progress`: show the "Complete" action, not another question.

## 3. List: filter, progress, stage, quarterly flag (rows 8, 7, 12, 13)

`GET /assessments?status=in_progress|completed` (optional). Without it, everything is returned as before, newest
first. The list is still a bare array in `data`; the new flag lives in **`meta.quarterly_due`**.

`list_empty.json` (no assessments yet):
```json
{"data": [], "meta": {"next_cursor": null, "total_estimate": null, "quarterly_due": false}}
```
`list_in_progress_filter.json` (`?status=in_progress`):
```json
{
  "data": [
    {
      "id": "7e7286ef-d15f-4dc4-a76d-df4bbf35721d",
      "type": "initial",
      "status": "in_progress",
      "started_at": "2026-10-20T13:46:45.008038+00:00",
      "completed_at": null,
      "overall_provisional": null,
      "stage_at_time": null,
      "progress": {"answered": 1, "remaining": 8, "pct": 11}
    }
  ],
  "meta": {"next_cursor": null, "total_estimate": null, "quarterly_due": false}
}
```
`list_after_first.json` (after completing; one row shown, meta unchanged):
```json
{
  "data": [
    {
      "id": "7e7286ef-d15f-4dc4-a76d-df4bbf35721d",
      "type": "initial",
      "status": "completed",
      "started_at": "2026-10-20T13:46:45.008038+00:00",
      "completed_at": "2026-10-20T13:46:45.215174+00:00",
      "overall_provisional": 80,
      "stage_at_time": "build",
      "progress": null
    }
  ],
  "meta": {"next_cursor": null, "total_estimate": null, "quarterly_due": false}
}
```
Invalid filter, `?status=done` (`list_status_invalid_422.json`). Values are case-sensitive; an empty `?status=` is also 422:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {"field": "query.status", "message": "Input should be 'in_progress' or 'completed'"}
    ]
  }
}
```
`list_completed_filter_empty.json` and `list_status_completed.json` are the same envelope with `data` filtered.

### Field traps
- **`progress` is `null` on completed rows** and an object on in-progress rows. Same key, two types.
- **`stage_at_time` is `null` for in-progress rows, for assessments completed before this shipped, and for startups
  with no stage.** It is never back-filled. Show "Not recorded", never a guess from the startup's current stage.
  Values are the startup stage keys (`idea`, `validation`, `build`, `launch`, `growth`, `scale`).
- **`meta.quarterly_due`** exists only on the list route; the other routes keep `meta: null`.

### `quarterly_due` (row 13)
`true` when the latest **completed** assessment is at least 90 days old **and** none is in progress; the same rule as
the quarterly cron. It is `false` for a startup with no completed assessment (the first one is not "due", it is
"pending": use your existing first-assessment prompt) and goes `false` again the moment a re-assessment is started.
`list_quarterly_due.json` (in this capture both completed rows were backdated by the test, so they share a
`completed_at`; real data will not):
```json
{
  "data": [
    {"id": "49e9139f-3b63-4efc-bdff-5e347e0a263d", "type": "quarterly", "status": "completed",
     "started_at": "2026-10-20T13:46:45.324891+00:00", "completed_at": "2026-07-12T13:46:45.667588+00:00",
     "overall_provisional": 31, "stage_at_time": "build", "progress": null},
    {"id": "7e7286ef-d15f-4dc4-a76d-df4bbf35721d", "type": "initial", "status": "completed",
     "started_at": "2026-10-20T13:46:45.008038+00:00", "completed_at": "2026-07-12T13:46:45.667588+00:00",
     "overall_provisional": 80, "stage_at_time": "build", "progress": null}
  ],
  "meta": {"next_cursor": null, "total_estimate": null, "quarterly_due": true}
}
```
**FE instruction:** when `quarterly_due` is true, show the re-assessment banner with the button **"Start re-assessment"**
(`POST /assessments`; it creates a `quarterly` assessment).

## 4. Complete returns the Health Score change (row 9, D-ASM-4)

`POST /{id}/complete` keeps every existing field and adds `health`. `previous_*`/`delta` are the Health Score
immediately **before** this completion. `dimensions` always has the five keys, in the order product, market, money,
legal, team.

First completion, `complete_first.json` (nothing to compare with, so previous and delta are `null`):
```json
{
  "data": {
    "assessment_id": "7e7286ef-d15f-4dc4-a76d-df4bbf35721d",
    "status": "completed",
    "dimension_scores": {"product": 78, "market": 75, "money": 71, "legal": 100, "team": 75},
    "overall_provisional": 80,
    "narrative": "Your strongest area is legal (100). Focus next on money (71).",
    "health": {
      "score": 80,
      "previous_score": null,
      "delta": null,
      "dimensions": [
        {"key": "product", "score": 78, "previous": null, "delta": null},
        {"key": "market", "score": 75, "previous": null, "delta": null},
        {"key": "money", "score": 71, "previous": null, "delta": null},
        {"key": "legal", "score": 100, "previous": null, "delta": null},
        {"key": "team", "score": 75, "previous": null, "delta": null}
      ]
    }
  },
  "meta": null
}
```
Re-assessment, `complete_second_health_delta.json` (this test deliberately answered the worst path):
```json
{
  "data": {
    "assessment_id": "49e9139f-3b63-4efc-bdff-5e347e0a263d",
    "status": "completed",
    "dimension_scores": {"product": 25, "market": 38, "money": 43, "legal": 0, "team": 50},
    "overall_provisional": 31,
    "narrative": "Your strongest area is team (50). Focus next on legal (0).",
    "health": {
      "score": 31,
      "previous_score": 80,
      "delta": -49,
      "dimensions": [
        {"key": "product", "score": 25, "previous": 78, "delta": -53},
        {"key": "market", "score": 38, "previous": 75, "delta": -37},
        {"key": "money", "score": 43, "previous": 71, "delta": -28},
        {"key": "legal", "score": 0, "previous": 100, "delta": -100},
        {"key": "team", "score": 50, "previous": 75, "delta": -25}
      ]
    }
  },
  "meta": null
}
```
**Replay trap, `complete_replay.json`:** calling `/complete` again on an already completed assessment returns the same
stored result with **`"health": null`** (the before/after pair is only known to the call that did the work). Keep the
`health` from the first response in your state; do not overwrite it with a replay. Note also that `dimension_scores`
key order differs on a replay; do not depend on object key order.

Note: `health.score` is the blended Health Score (assessment baseline plus live signals), so it can differ from
`overall_provisional` once live signals have weight. In the captures above they match because the live share ramps in over 14 days.

**UX consequence:** a re-assessment can lower the Health Score a lot (the capture shows -49). Show the delta honestly
with the per-dimension breakdown; do not hide a negative change. A "Health Score dropped" notification is also sent
by the existing Health module in that case.

## 5. Detail: structured answers with change markers (row 11)

`GET /assessments/{id}` keeps `answers_by_dimension` exactly as before and adds `answers` plus `stage_at_time`.
`answers` is a list grouped by dimension (order: product, market, money, legal, team, then `unknown` only if a
retired question key exists), questions in bank order. `detail_second_changed_from.json` (second assessment,
trimmed to two groups; every other group has the same shape):
```json
{
  "data": {
    "id": "49e9139f-3b63-4efc-bdff-5e347e0a263d",
    "type": "quarterly",
    "status": "completed",
    "stage_at_time": "build",
    "answers_by_dimension": {"product": [{"question_key": "product_stage", "value": "idea"}], "...": "unchanged, omitted here"},
    "answers": [
      {
        "dimension": "product",
        "questions": [
          {
            "question_key": "product_stage",
            "prompt": "How far along is your product?",
            "qtype": "single_choice",
            "value": "idea",
            "answer_label": "Just an idea",
            "changed": true,
            "changed_from": "Live with users"
          }
        ]
      },
      {
        "dimension": "market",
        "questions": [
          {
            "question_key": "market_clarity",
            "prompt": "How clearly do you understand your target market?",
            "qtype": "scale_1_5",
            "value": 3,
            "answer_label": "3",
            "changed": false,
            "changed_from": null
          },
          {
            "question_key": "market_research",
            "prompt": "How much customer research have you done?",
            "qtype": "single_choice",
            "value": "none",
            "answer_label": "No research yet",
            "changed": true,
            "changed_from": "Deep, ongoing research"
          }
        ]
      }
    ],
    "result": {
      "dimension_scores": {"team": 50, "legal": 0, "money": 43, "market": 38, "product": 25},
      "overall_provisional": 31,
      "narrative": "Your strongest area is team (50). Focus next on legal (0)."
    }
  },
  "meta": null
}
```
(the `"...": "unchanged, omitted here"` line is an editing note, not part of the response.)

- `answer_label` is always a string: choice label, the scale number, the amount without decimals, or the text. `value` is
  the raw stored value; render `answer_label`.
- **`changed_from` semantics:** the human label of the answer to the same question in the **previous completed
  assessment of the same startup** (the latest one that finished before this one; for an in-progress assessment, the
  latest completed one). It is `null` when there is no previous assessment, the question was not answered there
  (the path was different), or the answer is identical. `changed` is `true` exactly when `changed_from` is non-null.
- `detail_first_no_previous.json` (the first ever assessment): every question has `changed: false`, `changed_from: null`.
- `prompt` and `qtype` are `null` only for an answer whose question no longer exists in the bank (group `unknown`, unit-tested).
- `compare` is unchanged.

## 6. Notifications (row 6, row 13)

- `assessment.completed` now goes to **founders and team members only**, and not to the person who completed it.
  Mentors, investors, accountants, legal advisors and consultants no longer receive it.
- `assessment.quarterly.due` now goes to **founders only**, with body **"Start re-assessment"** and
  `data.href = "/assessment"`. Use that as the deep link and button label.

`notifications_founder_quarterly.json` (entry, trimmed to the relevant item; the founder's feed also has other types):
```json
{
  "id": "729c294f-0d3d-41c7-bdf4-a86009b3464b",
  "type": "assessment.quarterly.due",
  "title": "Time for your quarterly startup assessment",
  "body": "Start re-assessment",
  "data": {"href": "/assessment", "startup_id": "48469527-1d83-4ddf-afd0-b4df963e92e1"},
  "read": false,
  "created_at": "2026-10-20T13:46:46.164060+00:00"
}
```
`notifications_team_member.json` has two `assessment.completed` entries; one, verbatim:
```json
{
  "id": "3d85c6be-ab35-4729-ba7d-a03f3042e4f6",
  "type": "assessment.completed",
  "title": "A startup assessment was completed",
  "body": "",
  "data": {
    "actor_id": "dc724090-a2b5-4481-ae7d-a5839a8e76f8",
    "startup_id": "48469527-1d83-4ddf-afd0-b4df963e92e1",
    "assessment_id": "49e9139f-3b63-4efc-bdff-5e347e0a263d",
    "dimension_scores": {"team": 50, "legal": 0, "money": 43, "market": 38, "product": 25}
  },
  "read": false,
  "created_at": "2026-10-20T13:46:45.549161+00:00"
}
```
`data` gained `actor_id` (the completer). `notifications_mentor.json` contains **no** `assessment.*` entry.

**Known limit (outside this change):** other notification types (for example `healthscore.dropped`, `mission.ready`)
still go to every member, including mentors; see `notifications_mentor.json`. Not changed here.

## 7. Verification

| Behaviour | Verified live | Source |
|---|---|---|
| Mentor 403 on list, detail, compare (identical envelope) | yes | `list_mentor_403`, `detail_mentor_403`, `compare_mentor_403` |
| Team member 200 on list | yes | `list_team_member_200` |
| Team member 200 on detail and compare; accountant, legal advisor, consultant, investor 403; non-member 403; cross-workspace 404 | no, unit | `tests/api/assessment/test_prd_access.py` |
| `progress` on start, answer, next-question; moves with adaptive answers | yes | `start`, `answer_progress`, `next_question_progress` |
| `progress` 100 with null `next_question`; null on completed list rows | partly (null on completed rows live; 100 unit) | `list_after_first`; `test_prd_list.py` |
| `?status=` filter and invalid value 422 | yes | `list_in_progress_filter`, `list_completed_filter_empty`, `list_status_completed`, `list_status_invalid_422` |
| `stage_at_time` recorded at completion | yes | `list_after_first`, `detail_second_changed_from` |
| `stage_at_time` null for old rows / not rewritten on later stage change | no, unit | `test_prd_list.py` |
| `complete.health` first time (nulls) and re-assessment (deltas) | yes | `complete_first`, `complete_second_health_delta` |
| `complete.health` null on replay | yes | `complete_replay` |
| `answers` structure, labels, `changed`/`changed_from` | yes | `detail_second_changed_from`, `detail_first_no_previous` |
| `changed_from` against in-progress third assessment; unknown-key group; multi-choice label | no, unit | `test_prd_detail_answers.py` |
| `meta.quarterly_due` false then true (90 days) then false while re-assessment in progress | yes (90-day clock moved by backdating `completed_at` in the e2e database) | `list_empty`, `list_quarterly_due` |
| `quarterly_due` equals the cron's rule at the 89/91 day boundary | no, unit | `test_prd_list.py::test_quarterly_due_agrees_with_the_scheduler` |
| `assessment.quarterly.due`: founder only, body "Start re-assessment", `href` /assessment (real scheduler tick + worker drain) | yes | `notifications_founder_quarterly`, `notifications_mentor` |
| `assessment.completed`: team member yes, mentor no, completer no | yes | `notifications_team_member`, `notifications_mentor` |
| Notification audience per every role | no, unit | `tests/services/notifications/test_assessment_audience.py` |
