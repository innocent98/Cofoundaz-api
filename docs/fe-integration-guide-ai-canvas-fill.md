# FE Integration Guide — AI Canvas Fill (Module 03 Slices 2–3)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_canvas_ai_fill.py::test_canvas_ai_fill` (§1–§4) and `e2e/test_records_ai_fill.py::
test_records_ai_fill_personas` (§5, added in Slice 3) running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/canvas_ai_fill/*.json` and
`e2e/_captures/records_ai_fill/*.json`. Nothing here is retyped from the schema, the service, or
memory. IDs in the examples are real values from those ephemeral test runs (they differ on every
real request; the shapes are exact).

This is **not a new endpoint.** `POST /api/v1/business-builder/canvases/{type}/ai-fill` already
existed (Module 08 Slice 1) and already returned `202 {job_id, status}` — it just enqueued a job
with **no worker**, so nothing ever actually filled the canvas. What changed in Slice 2: the
`business.canvas.ai_fill` job now has a real handler, so the 202 you already integrate against now
completes for real, asynchronously, within seconds. Slice 3 (this update) does the exact same thing
for the OTHER ai-fill trigger Module 08 shipped — `POST /business-builder/{kind}/ai-fill`
(persona/revenue-streams/competitors/pricing) — see §5 below. §1–§4 are unchanged from Slice 2.

---

## 0. The one thing the FE must know

Calling ai-fill does **not** rewrite the whole canvas. The worker re-reads the canvas at run time
and **only drafts blocks that are still empty** — any block the founder (or a teammate) has
already typed something into is left completely untouched, even if the AI would have produced a
different value for it. **Ai-fill augments, it never overwrites.**

Consequence: calling ai-fill on a canvas that's already fully filled in is a safe no-op (the
worker finds no empty blocks and exits without touching anything, including `version`). Calling it
on a partially-filled canvas fills in only the gaps. There's no "regenerate everything" mode in
this slice — see Follow-ups in the SOP.

`version` **increments by exactly 1** when (and only when) the worker actually writes filled
blocks — same optimistic-concurrency counter as a direct `PUT /canvases/{type}`, so a stale
client-side `version` you were holding for a `PUT` is now stale after an ai-fill lands; re-`GET`
before your next `PUT` if one might be in flight.

---

## 1. `POST /api/v1/business-builder/canvases/{type}/ai-fill` — trigger

Requires founder/team_member (`_editor` — same role gate as every other Business Builder write; a
`mentor`/`business_consultant`/etc. gets `403 FORBIDDEN`). Unknown `{type}` → `404 NOT_FOUND`.
No request body.

`e2e/_captures/canvas_ai_fill/ai_fill_enqueued.json` (status `202`):

```json
{
  "data": {
    "job_id": "d87c1638-3e7f-4ca8-bbf9-47b544834509",
    "status": "queued"
  },
  "meta": null
}
```

The response is a plain job handle — it tells you nothing about the canvas itself. You need one of
the two polling strategies in §3 to learn when the fill has landed.

---

## 2. `GET /api/v1/business-builder/canvases/{type}` — after the fill lands

Same endpoint you already call to render the canvas — no new endpoint, no new field. Once the
worker has drained the job, `blocks` carries the AI-drafted content for whatever was empty, and
`version`/`completion` reflect the change.

`e2e/_captures/canvas_ai_fill/canvas_after_fill.json` (status `200`, a `business_model` canvas
that started **completely empty** — every block was a fill target, so every block changed; on a
partially-filled canvas only the previously-empty keys in `blocks` would differ, the rest stay
byte-identical to what you already had):

```json
{
  "data": {
    "type": "business_model",
    "version": 2,
    "blocks": {
      "channels": ["[stub-llm] channels"],
      "key_partners": ["[stub-llm] key_partners"],
      "key_resources": ["[stub-llm] key_resources"],
      "cost_structure": ["[stub-llm] cost_structure"],
      "key_activities": ["[stub-llm] key_activities"],
      "revenue_streams": ["[stub-llm] revenue_streams"],
      "customer_segments": ["[stub-llm] customer_segments"],
      "value_propositions": ["[stub-llm] value_propositions"],
      "customer_relationships": ["[stub-llm] customer_relationships"]
    },
    "block_defs": [
      { "key": "key_partners", "label": "Key Partners", "kind": "list" },
      { "key": "key_activities", "label": "Key Activities", "kind": "list" },
      { "key": "key_resources", "label": "Key Resources", "kind": "list" },
      { "key": "value_propositions", "label": "Value Propositions", "kind": "list" },
      { "key": "customer_relationships", "label": "Customer Relationships", "kind": "list" },
      { "key": "channels", "label": "Channels", "kind": "list" },
      { "key": "customer_segments", "label": "Customer Segments", "kind": "list" },
      { "key": "cost_structure", "label": "Cost Structure", "kind": "list" },
      { "key": "revenue_streams", "label": "Revenue Streams", "kind": "list" }
    ],
    "completion": { "filled_blocks": 9, "total_blocks": 9, "completion_pct": 100, "status": "complete" }
  },
  "meta": null
}
```

**Blocks live at `data.blocks`, a flat `{block_key: value}` map — confirmed against the real body,
not the schema.** Note `"[stub-llm] AI-generated assessment narrative."`-style markers:
`"[stub-llm] <key>"` is the fixed, deterministic output of the `StubLLMClient` used by this e2e run
(`LLM_PROVIDER=stub` — no real LLM call). In a real deployment with `LLM_PROVIDER=openai`, each
filled block instead holds real AI-generated content (a short array of phrases for `kind="list"`
blocks like every `business_model` block above, or a sentence or two of prose for `kind="text"`
blocks — `mission_vision`'s `mission`/`vision` are the only two blocks of that kind across all 5
canvas types). The FE should not assume any particular prefix, length, or item count — only that a
previously-empty block now holds non-empty content of the shape its `kind` implies.

---

## 3. How the FE learns the fill landed — two options, no push

There is **no webhook, SSE event, or notification** for ai-fill completion in this slice (Module 20's
real-time delivery is not wired to `business.canvas.ai_fill` — see Follow-ups). Pick one:

**Option A — poll the job (`GET /api/v1/jobs/{job_id}`, already shipped, unrelated to this slice).**
Returns `{id, type, status, result, error}`; `status` moves `queued` → `running` → `succeeded` |
`failed`. **`result` stays `null` for this job type** — the handler writes the drafted content
straight onto the canvas row, not into the job's `result` column — so a `succeeded` status tells you
*when* to re-fetch the canvas, not *what* changed. On `failed`, `error` holds the exception string
(same fail-loud contract as every other LLM-backed job in this codebase); the canvas is left exactly
as it was before the call (no partial write — see the SOP's "How").

**Option B — just re-fetch the canvas on a short interval or on next screen visit.** Since ai-fill is
additive/idempotent-ish (re-running it after a partial success only fills whatever is still empty),
polling `GET /canvases/{type}` every few seconds for ~10–15s after the 202, or simply re-fetching
when the founder next opens that canvas screen, is enough — there's no risk of double-applying
content the way a naive retry might double-charge a payment.

**Recommendation:** Option A if you're already polling `/jobs/{id}` elsewhere in the app (e.g. you
built a generic "background job in progress" spinner) — it gives a crisp success/failure signal.
Option B if you'd rather not track a job id — the worst case is the founder sees a stale canvas for
one extra screen visit, not an error.

---

## 4. Errors — quick reference

| Code | HTTP | When |
|---|---|---|
| `FORBIDDEN` | 403 | non-editor (mentor/business_consultant/accountant/legal_advisor/investor) calls `POST .../ai-fill` — same role gate as every other Business Builder write |
| `NOT_FOUND` | 404 | unknown `{type}` in the URL (not one of `business_model`/`lean`/`value_prop`/`swot`/`mission_vision`) |

No new error codes introduced by this slice — the trigger endpoint's error surface is unchanged
from Module 08 Slice 1's FE guide. A downstream LLM failure never surfaces as an HTTP error to the
FE at all (the 202 already returned before the worker ever runs) — it only shows up as the job's
`status: "failed"` if you're polling Option A, or as "the canvas silently stayed as it was" if
you're using Option B and not checking job status.

---

## 5. Records ai-fill — `POST /business-builder/{kind}/ai-fill` (Module 03 Slice 3)

`{kind}` is one of `personas` / `revenue-streams` / `competitors` / `pricing` — the same four
typed-record kinds `GET`/`POST`/`PUT`/`DELETE /business-builder/{kind}[/…]` already cover (Module 08
Slice 2). This trigger endpoint **already existed and already returned `202 {job_id, status}`** —
it just enqueued a `business.{kind}.ai_fill` job with **no worker**, exactly the same "shipped
enqueue point, missing handler" gap §1 described for canvases. This slice adds the worker; nothing
about the request shape changes.

### 5.0 The one thing the FE must know

Records ai-fill is **fill-empties-only at the kind level, not a per-field augment like canvases.**
The worker checks whether the kind has **any** records at all: if it already has one or more, the
whole call is a **no-op** — no new records, nothing changes, even if you'd expect "add 2 more
personas" to top the list up to 3. If the kind is completely empty, the worker drafts **up to 3**
new records via one structured LLM call and creates each one through the same validated
`create_record` path a manual `POST /{kind}` uses (a record that fails the kind's own Pydantic
validation is silently skipped, not surfaced as a partial error — the good records still land).

**There is no "top up" or "regenerate" mode in this slice.** A kind with 1 existing record stays at
1 record after calling ai-fill again — see Follow-ups in the SOP. Don't call this expecting it to
fill a partially-populated list the way canvas ai-fill fills partially-empty blocks.

### 5.1 Trigger — same role gate, same 404 as every other `{kind}` route

Requires founder/team_member (`_editor`); unknown `{kind}` → `404 NOT_FOUND`. No request body.

`e2e/_captures/records_ai_fill/personas_ai_fill_enqueued.json` (status `202`):

```json
{
  "data": {
    "job_id": "f97bd433-47bf-4526-8712-97c46d234be8",
    "status": "queued"
  },
  "meta": null
}
```

Identical shape to canvas ai-fill's 202 (§1) — a plain job handle, nothing about the records
themselves.

### 5.2 `GET /business-builder/{kind}` — after the fill lands

**Field-nesting trap: this is NOT the same response shape as canvas ai-fill's `GET`.** Canvas
`GET /canvases/{type}` returns blocks flat at `data.blocks` (§2). Records `GET /{kind}` returns
`{"records": [...], "fields": [...]}` **under** `data` — the records you want are at
`data.records`, an array, not `data` itself. Each element is the same shape a manual
`POST /{kind}` returns: `{id, kind, data, position}`.

`e2e/_captures/records_ai_fill/personas_after_fill.json` (status `200`, a `personas` kind that
started completely empty — the stub LLM drafted exactly 1 record, since the stub's deterministic
array expansion always produces a single-item array regardless of `maxItems`; a real
`LLM_PROVIDER=openai` call can return up to 3):

```json
{
  "data": {
    "records": [
      {
        "id": "0baa70e2-cb6b-4293-86e8-d4104644f8e3",
        "kind": "persona",
        "data": {
          "name": "[stub-llm] name",
          "goals": ["[stub-llm] goals"],
          "quote": "[stub-llm] quote",
          "demographics": "[stub-llm] demographics",
          "frustrations": ["[stub-llm] frustrations"],
          "watering_holes": ["[stub-llm] watering_holes"]
        },
        "position": 0
      }
    ],
    "fields": [
      { "key": "name", "required": true, "type": "<class 'str'>", "choices": null },
      { "key": "demographics", "required": false, "type": "<class 'str'>", "choices": null },
      { "key": "goals", "required": false, "type": "list[str]", "choices": null },
      { "key": "frustrations", "required": false, "type": "list[str]", "choices": null },
      { "key": "watering_holes", "required": false, "type": "list[str]", "choices": null },
      { "key": "quote", "required": false, "type": "<class 'str'>", "choices": null }
    ]
  },
  "meta": null
}
```

**Note the `"[stub-llm] <field>"` markers** — same fixed `StubLLMClient` convention §2 documents for
canvases (`LLM_PROVIDER=stub`, no real LLM call in this e2e run). In a real deployment each AI-drafted
record instead holds real generated content per field, with the kind's own validation still applied
(e.g. `competitor.threat_level` will always be a valid `ThreatLevel` enum value, `pricing.model_type`
a valid `PricingModelType` — the LLM's structured output is schema-constrained to the same
`RECORD_SCHEMAS` shape a manual create is validated against). `fields` is unchanged by this slice —
same descriptor array `GET /{kind}` has always returned.

### 5.3 How the FE learns the fill landed

Same two options §3 describes for canvases — poll `GET /jobs/{job_id}` (Option A; `result` stays
`null` here too, the handler writes straight to `business_records` rows) or just re-fetch
`GET /{kind}` on a short interval / next screen visit (Option B). No push/SSE for this job type
either.

### 5.4 Errors — quick reference

| Code | HTTP | When |
|---|---|---|
| `FORBIDDEN` | 403 | non-editor calls `POST /{kind}/ai-fill` — same role gate as every other `{kind}` write |
| `NOT_FOUND` | 404 | unknown `{kind}` in the URL (not one of `personas`/`revenue-streams`/`competitors`/`pricing`) |

No new error codes. Same fail-loud-to-the-job-not-the-HTTP-response posture as canvas ai-fill (§4):
a downstream LLM failure never surfaces as an HTTP error on the trigger call, only as the job's
`status: "failed"` (Option A) or as "the kind silently stayed empty" (Option B).

---

## Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST .../ai-fill` returns `202 {job_id, status: "queued"}` | ✅ | `ai_fill_enqueued.json` |
| Draining the worker in-process runs `business.canvas.ai_fill` and drafts every empty block with the LLM (stub) output | ✅ | `canvas_after_fill.json` |
| Filled blocks land at `data.blocks`, a flat `{key: value}` map, on the SAME `GET /canvases/{type}` endpoint (no new route) | ✅ | `canvas_after_fill.json` |
| `version` increments by exactly 1 when the worker writes | ✅ | `canvas_after_fill.json` (`version: 2`, up from the lazy-created canvas's `version: 1`) |
| `completion` recomputes to `status: "complete"` once every block is filled | ✅ | `canvas_after_fill.json` |
| Ai-fill never overwrites a block the user already filled (augment, not overwrite) | ⚠️ unit only — this e2e journey starts from a completely empty canvas (every block was a valid fill target) so it cannot ALSO prove a pre-filled block is skipped without a second, redundant live run; proven directly at the handler level instead | `tests/services/business/test_canvas_ai_fill.py::test_ai_fill_preserves_user_filled_blocks` |
| Ai-fill on a canvas with no empty blocks is a no-op (doesn't touch `version`) | ⚠️ unit only — same reasoning as above | derived from `handle_canvas_ai_fill`'s `if not empty_keys: return` guard (`app/worker/handlers/ai.py`), not separately unit-tested by name but exercised by the "preserves user-filled blocks" test staying at a single `version` bump for the one empty key it fills |
| `GET /jobs/{job_id}` for an ai-fill job — `result` stays `null` on success | ⚠️ derived from source, not captured live in this journey (the e2e asserts on the canvas, not the job's own detail endpoint) | `app/api/v1/endpoints/jobs.py`, `app/worker/handlers/ai.py::handle_canvas_ai_fill` (never writes `job.result`) |
| A failed ai-fill job leaves the canvas completely untouched (fail-loud, no partial write) | ⚠️ unit only — forcing the configured LLM to fail is not reachable from a normal live HTTP walk without breaking the shared e2e process's `LLM_PROVIDER=stub` guarantee | `tests/services/business/test_canvas_ai_fill.py::test_ai_fill_fails_loud_when_llm_errors` |
| Non-editor gets `403 FORBIDDEN` on `POST .../ai-fill` | ⚠️ not re-captured in this journey — same `_editor` dependency and error shape already captured live in the Module 08 Slice 1/3 guides | `docs/fe-integration-guide-business-builder.md`, `docs/fe-integration-guide-business-builder-suggestions.md` §5 |
| **§5 (Slice 3):** `POST /{kind}/ai-fill` returns `202 {job_id, status: "queued"}` | ✅ | `records_ai_fill/personas_ai_fill_enqueued.json` |
| Draining the worker runs `business.{kind}.ai_fill` and drafts records for an EMPTY kind via structured LLM (stub) output | ✅ | `records_ai_fill/personas_after_fill.json` |
| Records land at `data.records` (NOT `data` directly — field-nesting trap, see §5.2), each `{id, kind, data, position}` | ✅ | `records_ai_fill/personas_after_fill.json` |
| Each drafted record passes the kind's own `RECORD_SCHEMAS` validation (created via `create_record`, same path a manual `POST` uses) | ✅ (persona's `name` required field present and non-empty) | `records_ai_fill/personas_after_fill.json` |
| Ai-fill on a kind that already has ≥1 record is a whole-kind no-op (not a per-field/per-record top-up) | ⚠️ unit only — this e2e journey starts from a completely empty kind, so it cannot ALSO prove a non-empty kind is skipped without a second, redundant live run | `tests/worker/test_records_ai_fill_handler.py::test_ai_fill_noop_when_kind_not_empty` |
| Up to 3 records are created per call, and a record that fails validation is skipped rather than surfaced as an error | ⚠️ unit only — the stub LLM's deterministic array expansion always returns exactly 1 record, so this live journey cannot exercise the 2-or-3-record or skip-on-validation-failure paths | `tests/worker/test_records_ai_fill_handler.py::test_ai_fill_populates_empty_kind`, `app/worker/handlers/ai.py::handle_record_ai_fill` (`(result.get("records") or [])[:3]`, `except AppError: continue`) |
| A failed ai-fill job leaves the kind completely untouched (fail-loud, no partial write) | ⚠️ unit only — same reasoning as §4's canvas row: forcing the configured LLM to fail is not reachable without breaking the shared e2e process's `LLM_PROVIDER=stub` guarantee | `tests/worker/test_records_ai_fill_handler.py::test_ai_fill_fails_loud_on_llm_error` |
| Non-editor gets `403 FORBIDDEN` on `POST /{kind}/ai-fill`; unknown `{kind}` gets `404 NOT_FOUND` | ⚠️ not re-captured in this journey — same `_editor` dependency and `_parse_kind` 404 already captured live in the Module 08 Slice 2 FE guide | `docs/fe-integration-guide-business-builder.md` §6–§11 |

The ⚠️ rows are genuine gaps in these two live journeys, each with a named unit test or an
already-live-captured shape covering the same behavior — not invented or schema-derived guesses.
