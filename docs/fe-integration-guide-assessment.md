# FE Integration Guide — Startup Assessment (Module 07)

All request/response bodies below are pasted from **live captures** taken against a running
local server (`docker compose` API + worker + Postgres/Redis) on 2026-10-17, driving the real
`/api/v1/assessments/*` endpoints end-to-end with a real founder session. IDs/values are from
that ephemeral run (they differ per run; the shapes are exact). Where a path could not be
exercised from a normal run it is labelled inline.

> **Context:** the question objects previously carried **no human-readable text**, so the FE had
> nothing to render and the assessment UI stayed on mock data. As of this change every question
> serializes a **`prompt`** (and optional **`helper`**). This guide is the contract for wiring the
> real assessment flow.

Base path: `/api/v1/assessments`. Every route needs a Bearer access token **and** the
`X-Workspace-Id` header (from `GET /auth/me → data.active_workspace_id`), like all tenant-scoped
routes. Writes (start / answer / complete) require the **founder** role; reads (list / get /
compare) are open to any active member. Success envelope `{"data": …, "meta": null}`; errors
`{"error": {code, message, field_errors?}}`.

---

## 1. The question object

Returned as `data.next_question` by start / next-question / answers. `null` means **no more
questions — the assessment is ready to complete.**

```json
{
  "key": "product_stage",
  "dimension": "product",
  "section": "Product",
  "qtype": "single_choice",
  "prompt": "How far along is your product?",
  "options": [
    { "value": "idea", "label": "Just an idea" },
    { "value": "prototype", "label": "A prototype" },
    { "value": "mvp", "label": "A working MVP" },
    { "value": "live", "label": "Live with users" }
  ],
  "helper": null
}
```

| Field | Notes |
|---|---|
| `key` | stable question id — send it back as `question_key` when answering. |
| `dimension` | one of `product` / `market` / `money` / `legal` / `team` (the 5 scored dimensions). |
| `section` | display heading for the dimension ("Product", "Market", …). |
| `qtype` | render + value rules below. |
| `prompt` | **the question text to display.** Always present on real questions now. |
| `options` | present for `single_choice` / `multi_choice`; `null` otherwise. Render `label`, submit `value`. |
| `helper` | optional secondary line (e.g. a scale legend); may be `null`. |

### qtypes and the value you submit
| `qtype` | Render as | `value` to submit |
|---|---|---|
| `single_choice` | radio / option cards | the chosen option's `value` (string) — e.g. `"mvp"` |
| `scale_1_5` | 1–5 scale | integer `1`–`5` |
| `numeric_currency` | currency input | a number — e.g. `500000` |
| `multi_choice` | checkboxes | array of option `value`s (enum-defined; not used by the current v1 bank) |
| `short_text` | text input | string (enum-defined; not used by the current v1 bank) |

The current **v1 bank is 11 questions** across the 5 dimensions, using only
`single_choice` / `scale_1_5` / `numeric_currency`. Some are **adaptive** (`show_if`), so the FE
must not assume a fixed count or order — just render `next_question` until it is `null`.

---

## 2. Flow

### 2a. `POST /assessments` — start or resume
Creates a new in-progress assessment, or resumes the existing one (idempotent per startup).
Live capture (`201`):
```json
{
  "data": {
    "assessment_id": "945491d7-7bce-49c0-a0c0-3b17b2e82055",
    "type": "initial",
    "status": "in_progress",
    "next_question": { "key": "product_stage", "prompt": "How far along is your product?", "qtype": "single_choice", "section": "Product", "dimension": "product", "options": [ ... ], "helper": null }
  },
  "meta": null
}
```
`type` is `initial` the first time, `quarterly` afterwards.

### 2b. `GET /assessments/{id}/next-question`
Returns `{"data": {"next_question": <question|null>}}` — use on resume to get where the founder left off.

### 2c. `POST /assessments/{id}/answers` — answer the current question
Body: `{"question_key": "<key>", "value": <value per qtype>}`. Returns the **next** question (or
`null`). The server validates that `question_key` **is** the current question and that `value`
fits the qtype; a mismatch is `422 INVALID_ANSWER` (*"That isn't the current question."* / an
out-of-range scale, etc.). Re-submitting the current question overwrites the prior answer.

Live capture of the full adaptive walk (answers → next): product_stage→product_confidence→
market_clarity→market_research→has_revenue→mrr→runway_confidence→incorporated→ip_assigned→
team_size→team_confidence→`null`. (`product_confidence` appeared because `product_stage=live`;
`mrr` because `has_revenue=yes`; `ip_assigned` because `incorporated=yes` — that's `show_if`.)

### 2d. `POST /assessments/{id}/complete`
Only succeeds when `next_question` is `null` (all applicable questions answered); otherwise
`422 ASSESSMENT_INCOMPLETE` with the offending `field`. Live capture (`200`):
```json
{
  "data": {
    "assessment_id": "945491d7-7bce-49c0-a0c0-3b17b2e82055",
    "status": "completed",
    "dimension_scores": { "product": 89, "market": 88, "money": 86, "legal": 100, "team": 88 },
    "overall_provisional": 90,
    "narrative": "Your strongest area is legal (100). Focus next on money (86)."
  },
  "meta": null
}
```
Completing an **initial** assessment clears `assessment_pending`, recomputes the Health Score, and
enqueues `ai.assessment.narrative` (the `narrative` above is the deterministic templated line; a
richer AI narrative replaces it on the next `GET /assessments/{id}` read once the worker drains —
same pattern as the dashboard briefing). Completion is idempotent (safe to retry).

---

## 3. Reads

- `GET /assessments` → list: `[{id, type, status, started_at, completed_at, overall_provisional}]` (newest first).
- `GET /assessments/{id}` → `{id, type, status, answers_by_dimension, result}` where `result` is `null` until completed, else `{dimension_scores, overall_provisional, narrative}`.
- `GET /assessments/compare?ids=a,b,c` → up to 3 **completed** assessments: `[{id, dimension_scores}]`. More than 3, a bad id, or an incomplete/foreign id → `422` / `404`.

---

## 4. Errors
| Code | When |
|---|---|
| `INVALID_ANSWER` (422) | `question_key` isn't the current question, or `value` doesn't fit the qtype / is out of range. |
| `ASSESSMENT_INCOMPLETE` (422) | `complete` called with questions still unanswered — `field_errors[0].field` is the next `key`. |
| `VALIDATION_ERROR` (422) | compare with >3 ids or a malformed id. |
| `NOT_FOUND` (404) | unknown assessment id, or one in another workspace. |
| `403` | a non-founder calling a write route; missing/invalid `X-Workspace-Id` as elsewhere. |

---

## 5. Verification table
| Behaviour | Live? | Source |
|---|---|---|
| `POST /assessments` returns first question **with `prompt`** | ✅ | start capture above |
| Question shape (`key/dimension/section/qtype/prompt/options/helper`) | ✅ | start + answers captures |
| Full adaptive walk (11 questions, `show_if` branches) answers to `null` | ✅ | answers walk above |
| `POST /complete` → scores + overall + narrative | ✅ | complete capture above |
| Every bank question has non-empty prompt | ✅ | unit `tests/services/assessment/test_bank.py` |
| `multi_choice` / `short_text` value formats | ⬜ derived | in `QType` enum + `engine.validate_answer`; not used by the v1 bank |
| `GET /assessments`, `/{id}`, `/compare` shapes | ⬜ derived | `app/api/v1/endpoints/assessments.py` (not re-captured live this pass) |
