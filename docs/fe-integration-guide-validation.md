# FE Integration Guide — Validation Hub (Module 09)

> ⚠️ **Provenance — read this first.** The bodies below are **derived from the response builders**
> in `app/services/validation/` (`serialize_assumption`, `serialize_experiment`,
> `serialize_interview`, `serialize_survey`, `survey_analytics`, `public_view`), **not** yet pasted
> from live captures. `e2e/test_validation.py` exists and captures every body to
> `e2e/_captures/validation/`, but the e2e suite has not been run on this machine. The **shapes and
> field names are exact**; the **values are illustrative**. Regenerate this guide from the real
> captures after the first `scripts/e2e_run.sh` (or CI) run and delete this note.

Base path: `/api/v1/validation`. Every route **except the two public ones** requires a Bearer
access token and an `X-Workspace-Id` header naming the active workspace
(`GET /auth/me` → `data.active_workspace_id`), the same as every other tenant-scoped endpoint.

**Access:** founders and team members only. Mentor, accountant, legal advisor, business consultant
and investor receive **403** on every member route. Unauthenticated → **401**. Unverified email →
**403** `EMAIL_NOT_VERIFIED`.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}`.

---

## 1. Assumptions — the kanban

### `GET /api/v1/validation/assumptions?status=&risk=`

Newest first. `status` is one of `untested`, `testing`, `validated`, `invalidated`; `risk` is
`low`, `medium`, `high`. Both filters are optional. The board is this list, grouped by `status`.

```json
{
  "data": {
    "assumptions": [
      {
        "id": "0a2f1b7c-63c4-4d0a-9f2e-6b1f0f2d9a11",
        "statement": "Founders will pay for validation tooling",
        "risk": "high",
        "status": "untested",
        "evidence_count": 0,
        "created_at": "2026-09-24T10:12:00.123456+00:00",
        "updated_at": "2026-09-24T10:12:00.123456+00:00"
      }
    ]
  },
  "meta": null
}
```

**`evidence_count`** is derived on every read: the number of experiments **plus** interviews in
this workspace whose `assumption_ids` include this assumption. It is never stored, so it cannot go
stale.

### `POST /api/v1/validation/assumptions` → **201**

```json
{ "statement": "Founders will pay for validation tooling", "risk": "high" }
```

`status` may be supplied; it defaults to `untested`.

### `PATCH /api/v1/validation/assumptions/{id}` → **200**

Send only what changes: `statement`, `risk` and/or `status`.

```json
{ "status": "validated" }
```

**Any status may follow any other** — the board may drag a card backwards. Moving **into**
`validated` or `invalidated` publishes an event once; saving the same status again publishes
nothing.

Unknown id, or one belonging to another workspace → **404**.

---

## 2. Experiments and smoke tests

### `GET /api/v1/validation/experiments?type=&status=`

`type` is `smoke_test`, `landing_page`, `ad_test` or `other`; `status` is `draft`, `live` or
`ended`.

```json
{
  "data": {
    "experiments": [
      {
        "id": "8c1d0a44-1e77-4b0e-9c3a-2f5f8f0a77e2",
        "name": "Fake door landing page",
        "type": "smoke_test",
        "status": "draft",
        "config": { "headline": "Validate before you build" },
        "metrics": { "visits": 120, "signups": 9 },
        "assumption_ids": ["0a2f1b7c-63c4-4d0a-9f2e-6b1f0f2d9a11"],
        "created_at": "2026-09-24T10:14:02.000000+00:00",
        "updated_at": "2026-09-24T10:14:02.000000+00:00"
      }
    ]
  },
  "meta": null
}
```

`config` and `metrics` are free-form objects: send whatever the builder needs. `assumption_ids`
must name assumptions **in the same workspace**; anything else → **422**.

### `POST /api/v1/validation/experiments` → **201**

```json
{
  "name": "Fake door landing page",
  "type": "smoke_test",
  "metrics": { "visits": 120, "signups": 9 },
  "assumption_ids": ["0a2f1b7c-63c4-4d0a-9f2e-6b1f0f2d9a11"]
}
```

### `PATCH /api/v1/validation/experiments/{id}` → **200**

Any subset of the create fields. A field you send **replaces** the old value (metrics are not
merged).

### `GET /api/v1/validation/smoke-tests/{id}/stats` → **200**

Only for experiments of type `smoke_test`; anything else → **404**.

```json
{
  "data": {
    "experiment_id": "8c1d0a44-1e77-4b0e-9c3a-2f5f8f0a77e2",
    "name": "Fake door landing page",
    "status": "live",
    "visits": 120,
    "signups": 9,
    "conversion": 7.5
  },
  "meta": null
}
```

`conversion` is `round(100 × signups ÷ visits, 1)`, and **`0.0` when there are no visits** — never
an error. Missing or nonsensical counters read as 0.

---

## 3. Interviews

### `GET /api/v1/validation/interviews?segment=&verdict=&assumption_id=`

Most recent `held_on` first. `verdict` is `supports`, `contradicts` or `neutral`.

```json
{
  "data": {
    "interviews": [
      {
        "id": "b7f2c0de-2a11-4d51-8f2b-71b5e0a2c9d4",
        "interviewee": "Ada",
        "segment": "fintech",
        "held_on": "2026-09-20",
        "notes": "Asked for it unprompted.",
        "key_quotes": ["I would pay for this today"],
        "verdict": "supports",
        "assumption_ids": ["0a2f1b7c-63c4-4d0a-9f2e-6b1f0f2d9a11"],
        "created_at": "2026-09-24T10:15:40.000000+00:00",
        "updated_at": "2026-09-24T10:15:40.000000+00:00"
      }
    ]
  },
  "meta": null
}
```

### `POST /api/v1/validation/interviews` → **201**

```json
{
  "interviewee": "Ada",
  "held_on": "2026-09-20",
  "verdict": "supports",
  "segment": "fintech",
  "notes": "Asked for it unprompted.",
  "key_quotes": ["I would pay for this today"],
  "assumption_ids": ["0a2f1b7c-63c4-4d0a-9f2e-6b1f0f2d9a11"]
}
```

`key_quotes` must be a list of non-empty strings; anything else → **422**. `notes` is plain text.

### `PATCH /api/v1/validation/interviews/{id}` → **200**

Any subset of the above.

---

## 4. Surveys (the owner's side)

### `GET /api/v1/validation/surveys` → **200**

```json
{
  "data": {
    "surveys": [
      {
        "id": "f1c0b2a3-5d6e-4f70-8192-a3b4c5d6e7f8",
        "title": "Pricing check",
        "status": "open",
        "questions": [
          {
            "id": "3f9a1c22-7b0e-4c31-9d55-2a6b8c0d1e2f",
            "type": "choice",
            "prompt": "Would you pay for this?",
            "required": true,
            "options": ["Yes", "No"]
          },
          {
            "id": "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
            "type": "nps",
            "prompt": "How likely are you to recommend it?",
            "required": false
          }
        ],
        "response_count": 1,
        "has_link": true,
        "created_at": "2026-09-24T10:13:10.000000+00:00",
        "updated_at": "2026-09-24T10:13:44.000000+00:00"
      }
    ]
  },
  "meta": null
}
```

**The token never appears here, in any form.** `has_link` tells you only whether one exists.

### `POST /api/v1/validation/surveys` → **201**

```json
{
  "title": "Pricing check",
  "questions": [
    { "type": "choice", "prompt": "Would you pay for this?", "options": ["Yes", "No"], "required": true },
    { "type": "nps", "prompt": "How likely are you to recommend it?" },
    { "type": "open", "prompt": "What would make it a must-have?" }
  ]
}
```

**Question rules** (all violations → **422**):

| Rule | Limit |
|---|---|
| Types | `choice`, `scale`, `nps`, `open` |
| Questions per survey | 50 |
| Options per `choice` question | 20, each non-empty text |
| Prompt | required, non-empty |

**Ids are assigned by the server** and returned in `questions[].id`. When editing a survey, send
the questions back **with their ids** so answers already collected still match; a question without
an id is treated as new.

### `PATCH /api/v1/validation/surveys/{id}` → **200**

Send `title`, `questions` and/or `status` (`draft`, `open`, `closed`).

```json
{ "status": "open" }
```

```json
{
  "data": {
    "survey": { "id": "f1c0b2a3-…", "status": "open", "has_link": true, "…": "…" },
    "public_token": "sZ2p1Qe8w1nKcGk0m0X2VrJb7fHh5S_AQ3lLd9uYwbM"
  },
  "meta": null
}
```

⚠️ **`public_token` is returned exactly once**, the first time the survey is opened. Every later
response has `"public_token": null`, and the raw value cannot be recovered — only its hash is
stored. **Show it to the user immediately** (copy link / QR) and don't expect to fetch it again.
Re-opening a closed survey reuses the same token but does **not** return it.

Build the public link from it, e.g. `https://app.cofoundaz.com/s/{public_token}`, whose page calls
the two public routes below.

### `GET /api/v1/validation/surveys/{id}/analytics` → **200**

```json
{
  "data": {
    "survey_id": "f1c0b2a3-…",
    "title": "Pricing check",
    "status": "open",
    "responses": 1,
    "completion_rate": 100,
    "questions": [
      {
        "id": "3f9a1c22-…",
        "type": "choice",
        "prompt": "Would you pay for this?",
        "answered": 1,
        "counts": { "Yes": 1, "No": 0 }
      },
      {
        "id": "9d8c7b6a-…",
        "type": "nps",
        "prompt": "How likely are you to recommend it?",
        "answered": 1,
        "counts": { "9": 1 },
        "average": 9.0
      },
      {
        "id": "2b3c4d5e-…",
        "type": "open",
        "prompt": "What would make it a must-have?",
        "answered": 1
      }
    ]
  },
  "meta": null
}
```

- `choice` carries a count per option, **including options nobody picked**.
- `scale` and `nps` carry a count per value plus an `average` (0.0 when nobody answered).
- `open` carries only `answered`. **Raw open answers are not returned by any route in v1.**
- `completion_rate` is the percentage of responses that answered **every required** question, and
  is `0` when there are no responses.

---

## 5. The public survey link (no authentication at all)

Both routes are addressed by the **token**, never by the survey id, and neither accepts or requires
any header. They are the only unauthenticated surface in this module.

### `GET /api/v1/validation/surveys/{token}` → **200**

```json
{
  "data": {
    "title": "Pricing check",
    "questions": [
      {
        "id": "3f9a1c22-…",
        "type": "choice",
        "prompt": "Would you pay for this?",
        "required": true,
        "options": ["Yes", "No"]
      }
    ]
  },
  "meta": null
}
```

**That is the entire body.** No survey id, no workspace, no owner, no response counts, no status.

### `POST /api/v1/validation/surveys/{token}/responses` → **201**

```json
{
  "answers": {
    "3f9a1c22-…": "Yes",
    "9d8c7b6a-…": 9,
    "2b3c4d5e-…": "Stop me re-typing interview notes"
  }
}
```

```json
{ "data": { "received": true }, "meta": null }
```

**That is the entire reply** — an acknowledgement, nothing else.

**Answer rules** (all → **422**, with per-question `field_errors` describing only the respondent's
own answer):

| Question type | Accepted value |
|---|---|
| `choice` | one of that question's `options` |
| `scale` | whole number 1–5 |
| `nps` | whole number 0–10 |
| `open` | text, at most 4,000 characters (trimmed) |

A required question left out → 422. A question id that isn't in the survey → 422.

**Uniform 404.** An unknown token, a survey still in `draft`, and a `closed` survey all return the
**same** 404 body. The frontend cannot (and must not try to) distinguish them:

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found." } }
```

**Rate limit: 20 submissions per minute**, per caller IP address, on the submit route only.
Exceeding it returns **429**:

```json
{ "error": { "code": "RATE_LIMITED", "message": "Too many requests. Slow down a moment." } }
```

**Repeat submissions are allowed** in v1 — the same visitor may answer twice and both are stored.
Nothing about the respondent is recorded: no account, no IP address, no browser details, only the
time they answered.

---

## 6. AI stubs (not built yet)

### `POST /api/v1/validation/synthesize` → **202**
### `POST /api/v1/validation/scripts/generate` → **202**

```json
{ "data": { "job_id": "6a5b4c3d-…", "status": "queued" }, "meta": null }
```

Both record a job and return its id. **Nothing processes them yet** — the AI Insight Synthesizer
and interview-script generation arrive with Module 03. Don't poll for a result; treat the 202 as
"noted, coming later".

`scripts/generate` accepts an optional `{"assumption_ids": [...]}`.

---

## 7. Errors at a glance

| Status | When |
|---|---|
| 401 | No token on a member route |
| 403 | Wrong role, not a member of the workspace, or unverified email (`EMAIL_NOT_VERIFIED`) |
| 404 | Unknown id, another workspace's record, a non-smoke-test's stats, or **any** bad public token |
| 422 | Invalid question schema, invalid answer, a link to an assumption outside the workspace |
| 429 | More than 20 public submissions a minute |

---

## 8. Verification

Each row below names the capture that will replace its illustrative values after the first live
e2e run (`e2e/test_validation.py`, captures under `e2e/_captures/validation/`).

| Section | Capture file | Status |
|---|---|---|
| Assumption created | `assumption_created.json` | pending first e2e run |
| Assumption validated (evidence count 2) | `assumption_validated.json` | pending |
| Assumption list, filtered | `assumptions_list.json` | pending |
| Survey created | `survey_created.json` | pending |
| Survey opened (token returned once) | `survey_opened.json` | pending |
| Public form | `public_form.json` | pending |
| Public submission | `public_response.json` | pending |
| Unknown token → 404 | `public_unknown_token.json` | pending |
| Survey analytics | `survey_analytics.json` | pending |
| Experiment created | `experiment_created.json` | pending |
| Smoke-test stats | `smoke_test_stats.json` | pending |
| Interview created | `interview_created.json` | pending |

Rows not covered by the journey — the 403 access matrix, the 422 answer rules and the 429 rate
limit — are pinned by unit tests in `tests/api/test_validation.py` and
`tests/services/validation/`.
