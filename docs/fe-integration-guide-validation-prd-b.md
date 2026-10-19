# FE integration guide: Validation Hub slice (b): interview scripts + Insight Synthesizer

Gap rows 7, 12 and 13 of `cofoundaz/docs/checklist/validation-prd-gaps.md`. Builds on slice (a)
(`docs/fe-integration-guide-validation-prd-a.md`). Every body below is pasted from a live run of
`e2e/test_validation_prd_b.py` (captures in `e2e/_captures/validation_prd_b/`). The e2e drains AI
jobs with the **stub model**, so AI text there reads `[stub-llm] ...`; the files ending in
`_real_model.json` were captured the same way (real HTTP, fresh DB) with the **REAL MODEL** and show
what founders actually see. Ids and timestamps are throwaway test data.

All routes: `Authorization: Bearer …` + `X-Workspace-Id`, **founders and team members only**
(other roles get 403, like the rest of `/validation`). Base path `/api/v1/validation`.

---

## 1. Interview scripts (PRD 09.4: "Generate script")

The founder ticks one or more assumptions (max 5) and clicks **Generate script**. The AI writes a
title and 6 to 10 questions, each with an interviewer tip, and **saves** the script. Scripts appear
in a list and are fully editable. Replace the mock questions and the seed assumption list.

### 1a. Generate: `POST /scripts/generate` → 202
```json
{"assumption_ids": ["f7fe6d6e-f516-480a-a7f7-3a05f78ad01b"]}
```
`script_generate_queued.json`:
```json
{"data": {"job_id": "4804f11c-aa96-4592-8a9b-6ebd54d195f6", "status": "queued"}, "meta": null}
```
No assumption ticked (`script_generate_no_assumption_422.json`): disable the button, but handle it:
```json
{"error": {"code": "ASSUMPTIONS_REQUIRED", "message": "Pick at least one assumption to write a script for.", "field_errors": []}}
```
An id from another workspace or more than 5 ids: `422 VALIDATION_ERROR` (unit-tested, not captured).

### 1b. Poll `GET /api/v1/jobs/{job_id}`
Same cadence as slice (a): every 2 s, stop at `succeeded`/`failed`. Real-model runs took ~15 s. A
failed model call is **retried by the worker after 30 s** (then backoff), and the job reads `queued` meanwhile,
so cap the poll at ~90 s and then show "Still working, check back in a minute" rather than an error.
`script_generate_job.json`:
```json
{"data": {"id": "4804f11c-…", "type": "validation.scripts.generate", "status": "succeeded",
  "result": {"status": "ready", "script_id": "a1cfab7e-f030-4f9a-b4d1-2598f938fa97"}, "error": null}, "meta": null}
```
| `result.status` | FE does |
|---|---|
| `ready` | open the script `result.script_id` in the editor, refresh the list |
| `over_budget` | "AI is at its daily limit, try again tomorrow." (nothing was saved) |
| `gone` | "Those assumptions were deleted." (nothing saved) |
| `empty` | "The AI couldn't write questions this time. Try again." (nothing saved) |

`over_budget` / `gone` are unit-tested (`tests/api/test_validation_prd_b.py`); `empty` is defensive
and was never produced in testing.

### 1c. List `GET /scripts` (newest first) · one `GET /scripts/{id}`
`scripts_list.json` (stub text):
```json
{"data": {"scripts": [{
  "id": "a1cfab7e-f030-4f9a-b4d1-2598f938fa97",
  "title": "[stub-llm] title",
  "assumption_ids": ["f7fe6d6e-f516-480a-a7f7-3a05f78ad01b"],
  "questions": [{"id": "f1ac6ca91eda", "tip": "[stub-llm] tip", "question": "[stub-llm] question"}],
  "created_at": "2026-10-19T20:30:54.637835+00:00",
  "updated_at": "2026-10-19T20:30:54.637835+00:00"}]}, "meta": null}
```
**REAL MODEL** (`script_real_model.json`, two assumptions, trimmed to 3 of its 9 questions):
```json
{
 "title": "How Market Payments and Member Services Work Today",
 "questions": [
  {
   "id": "5dc3f0bfd965",
   "tip": "Ask for the most recent real example, including who paid whom and when.",
   "question": "Tell me about the last market trade you completed and how the payment was settled."
  },
  {
   "id": "ea56e7255318",
   "tip": "Listen for actual settlement times, delays, and steps rather than general opinions.",
   "question": "What usually happens between completing a trade and receiving the money?"
  },
  {
   "id": "3ffe5ba289b8",
   "tip": "Ask what happened next and what it cost in time, money, or trust.",
   "question": "Tell me about the last time a delayed payment caused a problem for you or your members."
  }
 ]
}
```
Show the PRD tip copy *"Ask about the last time it happened, not hypotheticals."* as the editor's
static hint; each question's own `tip` sits under it.

### 1d. Edit `PATCH /scripts/{id}` → 200 (any subset of `title`, `questions`, `assumption_ids`)
Send the **whole** question list in the new order. Keep each existing question's `id`; new questions
go without one and get an id back. `script_edited.json`:
```json
{"title": "Payout pain interviews",
 "questions": [
  {"id": "f1ac6ca91eda", "question": "Tell me about the last time a payout was late.", "tip": "[stub-llm] tip"},
  {"question": "What did you do while you waited?", "tip": "Stay quiet."}]}
```
```json
{"data": {"id": "a1cfab7e-…", "title": "Payout pain interviews",
  "assumption_ids": ["f7fe6d6e-f516-480a-a7f7-3a05f78ad01b"],
  "questions": [
    {"id": "f1ac6ca91eda", "tip": "[stub-llm] tip", "question": "Tell me about the last time a payout was late."},
    {"id": "0d9ceb5715ed", "tip": "Stay quiet.", "question": "What did you do while you waited?"}],
  "created_at": "2026-10-19T20:30:54.637835+00:00", "updated_at": "2026-10-19T20:30:54.675607+00:00"}, "meta": null}
```
Errors (`422 VALIDATION_ERROR`, message is user-safe, `field_errors` empty):
| Case | `message` |
|---|---|
| `questions: []` (`script_edit_empty_422.json`) | `A script needs at least one question.` |
| a blank question | `Each question needs text.` |
| more than 30 questions | `A script may have at most 30 questions.` |
| question or tip over 500 chars | `Questions and tips are limited to 500 characters.` |
| `title: ""` | pydantic 422 with `field_errors` on `title` |

Only `script_edit_empty_422` was captured live; the others are unit-tested.

### 1e. Delete `DELETE /scripts/{id}` → `{"data": {"deleted": true}, "meta": null}` (`script_deleted.json`)
Confirm first. Deleting an **assumption** silently removes its id from every script's
`assumption_ids` (same as interviews and experiments in slice (a)).

---

## 2. Insight Synthesizer (Interviews, Surveys and MVP tabs: "Synthesize insights")

Reads the workspace's interview notes, key quotes, verdicts and linked assumptions, and/or the
**open-text** survey answers, and stores a report: **patterns** (theme, summary, supporting quotes),
**contradictions**, and **suggested assumption status changes**, each with its own **Apply**.
Replace the 2.5 s mock and its hardcoded report. Interviewee names and closed-question answers are
never sent to the model.

### 2a. Start `POST /synthesize` → 202
Body optional: `{"source": "interviews" | "surveys" | "all"}` (no body = `all`). Use `interviews` on
the Interviews tab, `surveys` on Surveys, `all` on MVP (slice (c) will add MVP feedback to `all`).
`synthesize_queued.json`:
```json
{"data": {"job_id": "bbb66fe6-0a4b-4a4e-87de-baef5c4eb0ae", "status": "queued"}, "meta": null}
```
Nothing to read for that source (`synthesize_nothing_422.json`). Show it as an empty state with the
message, not as an error toast:
```json
{"error": {"code": "NOTHING_TO_SYNTHESIZE", "message": "Add an interview or collect an open survey answer first.", "field_errors": []}}
```
An unknown `source` → pydantic `422 VALIDATION_ERROR` (unit-tested).

### 2b. Poll `GET /api/v1/jobs/{job_id}` (same cadence)
`synthesize_job.json`:
```json
{"data": {"id": "bbb66fe6-…", "type": "validation.synthesize", "status": "succeeded",
  "result": {"status": "ready", "insight_id": "ca7654d5-ebb3-4851-8e53-751a1ca3dd70"}, "error": null}, "meta": null}
```
| `result.status` | FE does |
|---|---|
| `ready` | open report `result.insight_id` |
| `over_budget` | "AI is at its daily limit, try again tomorrow." (no report stored) |
| `empty` | the data was deleted while queued: show the 2a empty state |

### 2c. Read `GET /insights/{id}` · history `GET /insights` (newest first, same shape in `insights`)
**REAL MODEL** (`insight_report_real_model.json`, 3 interviews + 3 open answers, verbatim):
```json
{
 "data": {
  "id": "4fbafc3b-220f-4fd2-bc4b-6da83a7367bf",
  "source": "all",
  "inputs": {
   "interviews": 3,
   "open_answers": 3
  },
  "patterns": [
   {
    "id": "6847d0653257",
    "theme": "Same-day cash urgency",
    "quotes": [
     "Friday is when I need the money, not Monday",
     "I wait three days for my money and my supplier wants cash today",
     "Settlement takes too long, I need cash to restock"
    ],
    "summary": "Traders need faster settlement to restock and meet supplier payment demands, especially before high-volume trading periods."
   },
   {
    "id": "ea5865cac264",
    "theme": "Association distrust",
    "quotes": [
     "We stopped recommending anybody after the loan app wahala",
     "Talk to the traders one by one, not the association"
    ],
    "summary": "An association secretary said the association no longer recommends vendors and expects NovaPay to approach traders individually."
   }
  ],
  "contradictions": [
   {
    "id": "6d7b26f504cb",
    "summary": "Trader interviews indicate demand and willingness to pay for same-day settlement, while the association source rejects the assumption that the association will promote NovaPay and instead recommends individual outreach."
   }
  ],
  "suggestions": [
   {
    "id": "997d247516e1",
    "reason": "The market association secretary explicitly said the association no longer recommends vendors and told NovaPay to approach traders individually, directly refuting the assumption that market associations will promote NovaPay to their members.",
    "to_status": "invalidated",
    "applied_at": null,
    "from_status": "untested",
    "assumption_id": "47ceffec-5e9f-4231-8a9a-a11922682694",
    "assumption_statement": "Market associations will promote NovaPay to their members"
   }
  ],
  "created_at": "2026-10-19T20:55:37.916055+00:00"
 }
}
```
(trimmed: the real report had 4 patterns and 2 suggestions; the other suggestion was untested → testing.)

Rendering notes:
- `inputs` → "Based on 3 interviews and 3 survey answers".
- Themes, summaries and reasons are written by the model from customer evidence, including anonymous
  survey answers. Render them as plain text (never HTML/markdown), and label the report as AI-generated.
- A pattern's `quotes` can be **`[]`**: every quote is checked against the real evidence and any the
  model paraphrased or invented is dropped. Render the theme and summary without a quote block.
- `contradictions` and `suggestions` can be empty arrays: hide those sections.
- A suggestion only exists when it changes the status (`from_status` ≠ `to_status`) and names a real
  assumption. `assumption_statement` is a snapshot from report time; show it, not a lookup.
- `from_status` is the status **at report time**; the assumption may have moved since. Apply still
  sets it to `to_status`.

### 2d. Apply one suggestion `POST /insights/{id}/suggestions/{suggestion_id}/apply` → 200
No body. Moves the assumption and stamps `applied_at`. `suggestion_applied.json` (stub run; trimmed):
```json
{"data": {
  "insight": {"id": "ca7654d5-…", "suggestions": [{"id": "414b03bf75d1", "to_status": "untested",
     "applied_at": "2026-10-19T20:30:54.795481+00:00", "from_status": "testing",
     "assumption_id": "f7fe6d6e-…", "assumption_statement": "Traders will pay 0.5% for same-day settlement",
     "reason": "[stub-llm] reason"}], "…": "full report as in 2c"},
  "assumption": {"id": "f7fe6d6e-f516-480a-a7f7-3a05f78ad01b", "statement": "Traders will pay 0.5% for same-day settlement",
     "risk": "high", "status": "untested", "evidence_count": 1,
     "created_at": "2026-10-19T20:30:54.585917+00:00", "updated_at": "2026-10-19T20:30:54.791699+00:00"}},
 "meta": null}
```
Replace the report with `data.insight` (button → "Applied") and update the assumption card from
`data.assumption`. Moving into validated/invalidated fires the usual
`validation.assumption.validated|invalidated` events.

Second click (`suggestion_applied_again_409.json`): treat as already applied and refetch:
```json
{"error": {"code": "ALREADY_APPLIED", "message": "This suggestion was already applied.", "field_errors": []}}
```
Unknown suggestion id, or the assumption was deleted since: `404 NOT_FOUND` (unit-tested). Show
"That assumption no longer exists." and disable the button.

### 2e. Delete a report `DELETE /insights/{id}` → `{"data": {"deleted": true}, "meta": null}`
(`insight_deleted.json`); afterwards `GET` is `404 {"error": {"code": "NOT_FOUND", "message": "Not found.", "field_errors": []}}` (`insight_gone_404.json`).

---

## 3. Notification (PRD 09, row 13)

When a report has at least one pattern, founders and team members (not mentors, consultants or
investors) get **one** notification for it. `founder_notifications.json` (stub) entry:
```json
{"id": "663f66ad-5fb4-44b9-a2f1-262a3bd95b33",
 "type": "validation.insight.detected",
 "title": "New pattern detected in customer feedback: [stub-llm] theme",
 "body": "See insight",
 "data": {"theme": "[stub-llm] theme", "insight_id": "ca7654d5-ebb3-4851-8e53-751a1ca3dd70",
          "startup_id": "8ba01706-d647-45f0-a56a-ce439003ec2e"},
 "read": false, "created_at": "2026-10-19T20:30:54.750857+00:00"}
```
**REAL MODEL** title: `New pattern detected in customer feedback: Same-day cash urgency`.
`body` is the CTA label (**"See insight"**); deep link to the report with `data.insight_id`. In-app
only (no email category, like the slice (a) milestone). A report with no pattern sends nothing.
Slice (b) also narrowed the slice (a) `validation.smoke_test.milestone` notification to founders +
team members (it went to every member before).

---

## 4. Field-nesting traps
- Job results nest under `data.result` (`script_id`, `insight_id`), not `data`.
- Apply returns **two** objects: `data.insight` and `data.assumption`.
- Question ids, pattern/contradiction/suggestion ids are 12-char hex strings, not UUIDs. Script,
  report and assumption ids are UUIDs.
- Script `assumption_ids` can become `[]` after assumptions are deleted; the script stays.
- Notification `body` is a CTA label, not prose.

## 5. Verification

| Behaviour | Live | Source |
|---|---|---|
| Generate script → job → saved script, list, get | ✅ | `script_generate_*.json`, `scripts_list.json` |
| Real-model script (9 questions with tips) | ✅ REAL MODEL | `script_generate_job_real_model.json`, `script_real_model.json` |
| No assumption → 422 `ASSUMPTIONS_REQUIRED` | ✅ | `script_generate_no_assumption_422.json` |
| Edit keeps question ids; empty list 422 | ✅ | `script_edited.json`, `script_edit_empty_422.json` |
| Other edit errors, >5 assumptions, foreign assumption | ❌ unit | `tests/api/test_validation_prd_b.py` |
| Script `over_budget` / `gone` | ❌ unit | same |
| Deleting an assumption unlinks it from scripts | ❌ unit | same |
| Synthesize nothing → 422 `NOTHING_TO_SYNTHESIZE` | ✅ | `synthesize_nothing_422.json` |
| Synthesize → job → stored report | ✅ | `synthesize_*.json`, `insight_report.json`, `insights_list.json` |
| Real-model report (4 themes, verbatim quotes, 2 suggestions, no internal refs in prose) | ✅ REAL MODEL | `synthesize_job_real_model.json`, `insight_report_real_model.json` |
| Interviewee names never sent or returned | ✅ (asserted on the report) + unit (asserted on the prompt) | e2e + unit |
| Invented quotes dropped; bad / no-op / unknown suggestions dropped | ❌ unit | same |
| `source=surveys` reads only open answers | ❌ unit | same |
| Apply → assumption moves; second click 409 | ✅ | `suggestion_applied*.json` |
| Apply after assumption deleted / unknown suggestion → 404 | ❌ unit | same |
| `validation.insight.detected` in the founder's feed | ✅ (stub + REAL MODEL) | `founder_notifications*.json` |
| Notification audience founders + team members only | ❌ unit | same |
| Synthesis `over_budget` / `empty` | ❌ unit | same |
| Delete report / script | ✅ | `insight_deleted.json`, `insight_gone_404.json`, `script_deleted.json` |
| Mentor/other roles 403; other workspace 404 | ❌ unit | same |
