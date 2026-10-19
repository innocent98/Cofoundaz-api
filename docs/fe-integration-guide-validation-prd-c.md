# FE integration guide: Validation Hub slice (c): MVP feedback, theme clustering, Send to roadmap

Gap row 11 (MVP feedback, D-VAL-2/3) plus the BE parts of rows 9 (survey answers, share link) and 10 (linked
experiments) of `cofoundaz/docs/checklist/validation-prd-gaps.md`. Builds on slices (a) and (b). Every body below is
pasted from a live run of `e2e/test_validation_prd_c.py` (captures in `e2e/_captures/validation_prd_c/`). The e2e
uses the **stub model** (AI text reads `[stub-llm] ...`); files ending in `_real_model.json` are the same calls with
the **REAL MODEL**. Ids, timestamps and tokens are throwaway test data.

All routes: `Authorization` + `X-Workspace-Id`, **founders and team members only** (others 403). Base `/api/v1/validation`.

---

## 1. Feedback inbox (MVP feedback tab)

Interview **key quotes** and **open survey answers** appear in the inbox automatically (re-synced on every read).
Founders add the rest by hand, tagged **interview / in_app / email**. Replace all seed quotes.

### 1a. List `GET /feedback` (optional `?source=interview|survey|in_app|email`, `?include_dismissed=true`)
Newest `received_on` first. `feedback_inbox.json` (verbatim):
```json
{"data": {"items": [
  {"id": "343d27f2-5ff8-44c1-8ade-5b3b1337bd6d", "source": "survey", "text": "Payouts take forever",
   "origin": {"type": "survey", "id": "c2a12dd8-69ff-4c3a-b55f-3e3a7aab0127", "label": "Survey: Payouts"},
   "imported": true, "dismissed": false, "received_on": "2026-10-19", "created_at": "2026-10-19T21:57:26.034384+00:00"},
  {"id": "ca030ce5-1ed6-4f77-bdba-d23138087864", "source": "in_app", "text": "The app froze when I tried to withdraw",
   "origin": {"type": "manual", "id": null, "label": "Added by hand"},
   "imported": false, "dismissed": false, "received_on": "2026-10-19", "created_at": "2026-10-19T21:57:25.979444+00:00"},
  {"id": "b992c272-2b34-4bcf-ba1a-a2f1ddc4afdd", "source": "email", "text": "Payout came late again",
   "origin": {"type": "manual", "id": null, "label": "Added by hand"},
   "imported": false, "dismissed": false, "received_on": "2026-10-18", "created_at": "2026-10-19T21:57:26.001832+00:00"},
  {"id": "949b692c-8bf9-4489-a7da-5e9489dfe28c", "source": "interview", "text": "I wait three days for my money",
   "origin": {"type": "interview", "id": "68478644-7333-4c13-9f34-8c251d79783a", "label": "Interview, Market traders"},
   "imported": true, "dismissed": false, "received_on": "2026-10-15", "created_at": "2026-10-19T21:57:26.034384+00:00"}],
  "counts": {"interview": 1, "survey": 1, "in_app": 1, "email": 1}}, "meta": null}
```
- Source chips: `interview`, `survey`, `in_app` ("In-app"), `email`. `counts` drives the filter tabs (dismissed excluded).
- `origin.label` is display text and never contains an interviewee's name. `origin.id` links to the interview or
  survey (`origin.type`), or is `null` for manual entries.
- `imported: true` items are **read-only**: show "Edit it in the interview/survey" and a **Dismiss** action instead of
  Edit/Delete. Editing the source quote or deleting the interview updates or removes the item on the next read.

### 1b. Add `POST /feedback` → 201
`{"text": "...", "source": "interview" | "in_app" | "email", "received_on": "YYYY-MM-DD"?}` (date defaults to today).
`feedback_created.json`:
```json
{"data": {"id": "ca030ce5-1ed6-4f77-bdba-d23138087864", "source": "in_app", "text": "The app froze when I tried to withdraw",
  "origin": {"type": "manual", "id": null, "label": "Added by hand"}, "imported": false, "dismissed": false,
  "received_on": "2026-10-19", "created_at": "2026-10-19T21:57:25.979444+00:00"}, "meta": null}
```
`source: "survey"` is rejected (`feedback_bad_source_422.json`): survey answers are imported, never typed in:
```json
{"error": {"code": "VALIDATION_ERROR", "message": "Please check the highlighted fields.",
  "field_errors": [{"field": "source", "message": "Input should be 'interview', 'in_app' or 'email'"}]}}
```
Blank text → 422 `VALIDATION_ERROR` (unit-tested).

### 1c. Edit `PATCH /feedback/{id}` (manual only; any of `text`, `source`, `received_on`) → 200, same item shape
(`feedback_edited.json`). On an imported item (`feedback_imported_edit_422.json`):
```json
{"error": {"code": "IMPORTED_FEEDBACK", "message": "This came from an interview or survey. Edit it there, or dismiss it here.", "field_errors": []}}
```

### 1d. Delete / dismiss `DELETE /feedback/{id}`, restore `POST /feedback/{id}/restore`
The reply tells you which happened:
- manual item: `{"data": {"deleted": true}, "meta": null}` (`feedback_deleted.json`): gone for good.
- imported item: `{"data": {"dismissed": true}, "meta": null}` (`feedback_dismissed.json`): hidden from the inbox and
  counts; list it with `?include_dismissed=true` to offer **Restore**, which returns the item with `"dismissed": false`
  (`feedback_restored.json`).
Confirm the manual delete ("Delete this feedback?"); dismiss needs no confirm because it can be undone.

---

## 2. AI theme clustering ("Find themes")

### 2a. Start `POST /feedback/cluster` → 202 (no body)
`cluster_queued.json`: `{"data": {"job_id": "86932304-3bed-4867-8630-9791bb9caca1", "status": "queued"}, "meta": null}`.
Empty inbox (`cluster_nothing_422.json`), show as an empty state:
```json
{"error": {"code": "NOTHING_TO_CLUSTER", "message": "Add some feedback first, or record an interview quote.", "field_errors": []}}
```

### 2b. Poll `GET /api/v1/jobs/{job_id}`
Same cadence as slices (a)/(b) (2 s, cap ~90 s because a failed model call is retried after 30 s). `cluster_job.json`:
```json
{"data": {"id": "86932304-…", "type": "validation.feedback.cluster", "status": "succeeded",
  "result": {"run_id": "9c316338-3c2f-4e23-983b-04cdc0cd4b7a", "status": "ready", "theme_count": 1}, "error": null}, "meta": null}
```
| `result.status` | FE does |
|---|---|
| `ready` | refetch `GET /feedback/themes` |
| `over_budget` | "AI is at its daily limit, try again tomorrow." |
| `empty` | the inbox was emptied meanwhile: empty state |

(`over_budget` / `empty` unit-tested.)

### 2c. Themes `GET /feedback/themes`
Before any run (`themes_none.json`): `{"data": {"run": null, "themes": []}, "meta": null}`.

**REAL MODEL** (`themes_real_model.json`, 10 items: 2 interview quotes, 3 open answers, 5 manual), trimmed to 2 of 4 themes:
```json
{"data": {"run": {"id": "beb93a4e-04e4-4e53-8652-19f2c0e10c3c", "item_count": 10, "created_at": "2026-10-19T21:58:30.058822+00:00"},
 "themes": [
  {"theme": "Slow Payouts", "label": "Slow Payouts mentioned 4× across 3 sources", "mention_count": 4, "source_count": 3,
   "sources": ["email", "interview", "survey"],
   "summary": "Customers need faster access to funds because delayed settlements disrupt restocking and supplier payments.",
   "items": ["…4 full feedback items, same shape as the inbox…"], "roadmap_task_id": null, "sent_at": null},
  {"theme": "Withdrawal Failures", "label": "Withdrawal Failures mentioned 2× across 1 source", "mention_count": 2, "source_count": 1,
   "sources": ["in_app"],
   "summary": "Customers report that withdrawals freeze or remain stuck in the app, including on specific devices."}]}}
```
The other two real themes were "Hidden Fees mentioned 2× across 2 sources" and "Unavailable Support mentioned 2× across 2 sources".
- Render the card title from **`label`** (it is the PRD copy, singular "source" handled). Drill-in = `items`.
- Themes come back most-mentioned first. Counts are live: deleting an item lowers them; a theme whose items are all
  deleted disappears. Each item belongs to at most one theme.
- Clustering again replaces the shown set (older runs are kept server-side). `run.created_at` → "Last grouped …".
- Theme text is AI-written from customer input (including anonymous survey answers): plain text only, labelled AI.

---

## 3. Send to roadmap `POST /feedback/themes/{id}/send-to-roadmap` → 201

Body optional: `{"milestone_id": "<uuid>"}`. Without it the task goes into the **first milestone that isn't done**.
Offer a milestone picker (from `GET /api/v1/roadmap`) with that default. **REAL MODEL**
(`theme_sent_to_roadmap_real_model.json`), `task` part:
```json
{"id": "…", "milestone_id": "…", "milestone_title": "Validate demand", "title": "Slow Payouts",
 "description": "From MVP feedback: Customers need faster access to funds because delayed settlements disrupt restocking and supplier payments.\n\nMentioned 4× across 3 sources, for example:\n- \"Settlement takes too long, I need cash to restock\"\n- \"I would pay a little more if money came the same day\"\n- \"Payout came late again, second time this week\"",
 "status": "todo"}
```
The reply also carries the updated `theme` (`roadmap_task_id` and `sent_at` set; stub capture `theme_sent_to_roadmap.json`).
Show "On your roadmap: {milestone_title}" with a link to `/roadmap`, and disable the button.
Second send (`theme_sent_again_409.json`):
```json
{"error": {"code": "ALREADY_SENT", "message": "This theme is already on your roadmap.", "field_errors": []}}
```
No open milestone: 422 `NO_OPEN_MILESTONE` "Your roadmap has no open milestone to add this to. Add one on the Roadmap
first." A `milestone_id` from another workspace: 404. (Both unit-tested.)

---

## 4. Insight Synthesizer on the MVP tab
`POST /synthesize` now also takes `{"source": "feedback"}` (manual entries only; imported ones are already the
interviews and answers), and `all` includes them. Every report's `inputs` now has three keys
(`insight_from_feedback.json`, `source: "feedback"`): `{"feedback": 2, "interviews": 0, "open_answers": 0}`.
Use `source=all` on the MVP tab. The empty-state message is now "Add an interview, collect an open survey answer or
log some feedback first." (slice (b) guide updated).

---

## 5. Surveys: read open answers and get a new share link (row 9)

### 5a. `GET /surveys/{id}/answers` (`survey_open_answers.json`)
```json
{"data": {"survey_id": "c2a12dd8-69ff-4c3a-b55f-3e3a7aab0127", "title": "Payouts",
  "questions": [{"id": "b7561000-1cf2-434c-9a44-4ac3a50eb73a", "prompt": "What frustrates you about payouts?",
    "answer_count": 1, "answers": [{"text": "Payouts take forever", "submitted_at": "2026-10-19T21:57:25.969151+00:00"}]}]}, "meta": null}
```
Open questions only, blank answers skipped, newest first, at most 500 listed per question (`answer_count` is the
full count). Anonymous: nothing identifies the respondent.

### 5b. `POST /surveys/{id}/share-link` → 200 (`survey_share_link_rotated.json`)
`{"data": {"public_token": "<new token, shown once>"}, "meta": null}` (the committed capture has the token value
redacted to a placeholder; the real value is a 43-character URL-safe string). The link is `/s/{public_token}`; show it with
copy + QR. **The previous link stops working** (`survey_old_link_404.json`: `{"error": {"code": "NOT_FOUND", …}}`),
so label the button "New share link" and confirm: "Anyone with the old link won't be able to answer." Draft or closed
survey: 409 `SURVEY_NOT_OPEN` "Open the survey to get a share link." (unit-tested).

---

## 6. Assumption cards: linked experiments (row 10)
Every assumption response (list, create, PATCH, Apply-suggestion) now has `linked_experiments`
(`assumptions_with_experiments.json`):
```json
{"id": "0f4bd20f-5de3-42be-a6a8-1dc748960c6e", "statement": "Traders will pay 0.5% for same-day settlement", "risk": "high",
 "status": "untested", "evidence_count": 2,
 "linked_experiments": [{"id": "a3a4bd77-4c7b-4639-bec9-3dbcef6b87bc", "name": "Fee fake door", "type": "smoke_test", "status": "draft"}],
 "created_at": "…", "updated_at": "…"}
```
`[]` when none. Link each to its smoke test (`/validation/smoke-tests?test={id}`).

## 7. Field-nesting traps
- `DELETE /feedback/{id}` returns `deleted` **or** `dismissed`; branch on the key.
- Theme `items` are full inbox items; `label` already contains the counts.
- Send-to-roadmap returns `data.theme` and `data.task`; the task id also lands in `theme.roadmap_task_id`.
- Cluster job result has `run_id`, not theme ids: refetch `/feedback/themes`.
- Report `inputs` gained `feedback` (always present now).

## 8. Verification

| Behaviour | Live | Source |
|---|---|---|
| Inbox mirrors interview quotes + open answers; counts; filter | ✅ | `feedback_inbox*.json` |
| Interviewee name never in inbox labels | ✅ (asserted) | e2e |
| Editing/deleting a source updates the mirror | ❌ unit | `tests/api/test_validation_prd_c.py` |
| Manual add / edit / delete; survey tag rejected | ✅ | `feedback_created/edited/deleted`, `feedback_bad_source_422` |
| Imported read-only, dismiss, restore | ✅ | `feedback_imported_edit_422`, `feedback_dismissed`, `feedback_restored` |
| Cluster: nothing → 422; job → themes | ✅ | `cluster_*`, `themes*.json` |
| Real-model themes (4 themes, all 10 items placed) | ✅ REAL MODEL | `cluster_job_real_model.json`, `themes_real_model.json` |
| Counts follow deletes; new run replaces themes; over_budget / empty | ❌ unit | same |
| Send to roadmap (default milestone) + 409 | ✅ (stub + REAL MODEL) | `theme_sent_to_roadmap*.json`, `theme_sent_again_409.json` |
| Chosen milestone; no open milestone 422; foreign milestone 404 | ❌ unit | same |
| Synthesizer `source=feedback` | ✅ | `insight_from_feedback.json` |
| Open answers; share-link rotation; old link 404 | ✅ | `survey_open_answers`, `survey_share_link_rotated`, `survey_old_link_404` |
| Share link on a draft → 409 | ❌ unit | same |
| `linked_experiments` | ✅ | `assumptions_with_experiments.json` |
| Mentor 403; other workspace 404 | ❌ unit | same |
