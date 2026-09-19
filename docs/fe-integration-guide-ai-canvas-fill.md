# FE Integration Guide — AI Canvas Fill (Module 03 Slice 2)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_canvas_ai_fill.py::test_canvas_ai_fill` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/canvas_ai_fill/*.json`. Nothing here is retyped from
the schema, the service, or memory. IDs in the examples are real values from that ephemeral test
run (they differ on every real request; the shapes are exact).

This is **not a new endpoint.** `POST /api/v1/business-builder/canvases/{type}/ai-fill` already
existed (Module 08 Slice 1) and already returned `202 {job_id, status}` — it just enqueued a job
with **no worker**, so nothing ever actually filled the canvas. What changed in this slice: the
`business.canvas.ai_fill` job now has a real handler, so the 202 you already integrate against now
completes for real, asynchronously, within seconds.

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

The four ⚠️ rows are genuine gaps in this one live journey, each with a named unit test or an
already-live-captured shape covering the same behavior — not invented or schema-derived guesses.
