# FE Integration Guide — AI Business Plan Generator (§08.11, Module 08 Slice 4)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_business_plan.py::test_business_plan_generation` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/business_plan/*.json`. Nothing here is retyped from the
schema, the service, or memory. IDs/timestamps in the examples are real values from that ephemeral
test run (they differ on every real request; the shapes are exact).

---

## 0. The one thing the FE must know

A business plan is generated **asynchronously**, in three steps, over roughly ten to a few tens of
seconds:

1. **`POST /business-builder/plan/generate`** returns **202** immediately with `{plan_id, status:
   "generating"}`. No plan content exists yet — this call only starts the job.
2. **A background worker writes ten plan sections one at a time**, calling the configured LLM once
   per section. There is no push notification for completion in this response cycle — see §3 for
   how to detect it's done.
3. **Poll `GET /business-builder/plan`** until `data.status === "complete"`. At that point
   `data.document_id` is set — fetch the plan's actual content from the Document at that id
   (`GET /documents/{document_id}`), NOT from the plan endpoint itself. The plan endpoint never
   returns section content; it only ever returns the run's status.

**Failure is silent in v1 — there is no `failed` state reachable today.** If the underlying LLM
job exhausts its retries, the plan is left at `status: "generating"` **forever** — there is
currently no code path that flips it to `failed`, even though the enum has that value. **The FE
must treat "still generating after a reasonable timeout (e.g. 2 minutes)" as an effective failure
and offer the founder a retry** (a fresh `POST /plan/generate`), rather than polling indefinitely.
See §4 for the recommended cadence/timeout.

**Regenerating is just a fresh `POST`.** There is no "regenerate this plan" endpoint — calling
`POST /business-builder/plan/generate` again creates a brand-new `BusinessPlan` row and a brand-new
`Document`. The previous plan and its document are NOT deleted, but `GET /business-builder/plan`
only ever returns the LATEST one (by creation time) — there is no history/list endpoint in v1, so
the FE cannot show "your previous plans" without building that itself against the Documents list
(`GET /documents?kind=business_plan`).

---

## 1. Endpoints

### 1a. `POST /api/v1/business-builder/plan/generate`

Starts generation. **Auth:** founder or team_member (`editor`) role in the active workspace —
`business_consultant`/mentor/accountant/legal_advisor/investor get 403 `FORBIDDEN`, same as every
other Business Builder write. Requires `X-Workspace-Id`.

**Request:** no body.

**Response — 202 Accepted.** `e2e/_captures/business_plan/generate_enqueued.json`:

```json
{
  "data": {
    "plan_id": "1e5bc79a-d66b-456b-b7ef-fc10bd0afebf",
    "status": "generating"
  },
  "meta": null
}
```

### 1b. `GET /api/v1/business-builder/plan`

Returns the LATEST plan run for the workspace (by creation time) — **not** a list. **Auth:** any
active member (read-only). Requires `X-Workspace-Id`.

**404** (standard `NOT_FOUND` envelope) if no plan has ever been generated for this workspace.

**Response — 200, after the worker completes.** `e2e/_captures/business_plan/plan_complete.json`:

```json
{
  "data": {
    "id": "1e5bc79a-d66b-456b-b7ef-fc10bd0afebf",
    "status": "complete",
    "document_id": "02009be9-1d4d-4d76-ad7c-1d2779da4d79",
    "created_at": "2026-09-19T15:44:15.831281+00:00"
  },
  "meta": null
}
```

`data.id` is the SAME value as `data.plan_id` from §1a's response — it is just named `id` here, not
`plan_id`. `data.document_id` is `null` while `status === "generating"`; only becomes non-null on
`"complete"`.

### 1c. `GET /api/v1/documents/{document_id}` — the actual plan content

This is Module 18's existing, general-purpose document-read endpoint, not a plan-specific route.
Once `document_id` is known (from §1b), fetch it here. **Auth:** any active member. Requires
`X-Workspace-Id`.

**Response — 200.** `e2e/_captures/business_plan/plan_document.json`:

```json
{
  "data": {
    "id": "02009be9-1d4d-4d76-ad7c-1d2779da4d79",
    "kind": "business_plan",
    "title": "Cofoundaz — Business Plan",
    "status": "draft",
    "ai_generated": true,
    "folder": null,
    "template_key": null,
    "version": 1,
    "updated_at": "2026-09-19T15:44:15.908335+00:00",
    "sections": [
      {
        "id": "c0e214a5-9317-47ba-9389-e4b144a6202f",
        "body": "[stub-llm] AI-generated assessment narrative.",
        "heading": "Executive Summary"
      },
      {
        "id": "f9a150b4-3e85-491f-aed6-0b2671d8cea3",
        "body": "[stub-llm] AI-generated assessment narrative.",
        "heading": "Problem & Opportunity"
      }
    ]
  },
  "meta": null
}
```

(Truncated to 2 of the 10 sections for brevity — the full 10-section array is in the capture file.
The remaining 8 headings, in order: "Solution & Product", "Market & Customers", "Business Model",
"Go-to-Market", "Competition", "Team", "Financials & Projections", "Roadmap & Milestones".)

**Field notes, confirmed live:**
- `title` is always `"{startup name or 'Business'} — Business Plan"` — an em dash (`—`), not a
  hyphen. The example above shows a raw (unescaped) em dash; the actual HTTP response has it
  JSON-escaped as `—`, same character.
- `status` here is the DOCUMENT's status (`draft`/`final`/etc., Module 18's own field), NOT the
  plan generation status — **do not confuse this with §1b's `data.status`, which is the generation
  run's status (`generating`/`complete`/`failed`).** A freshly-generated plan document is always
  `status: "draft"`; the founder can edit and finalize it like any other document afterward.
- `sections[].body` is markdown prose (rendered from the stub LLM as a single sentence in this e2e
  run; a real `LLM_PROVIDER=openai` deployment returns 2+ paragraphs of markdown per section — see
  §2). Render it as markdown, not plain text.
- `sections[].id` is a per-section UUID assigned by `create_document` — stable for the life of the
  document, useful as a React `key` when rendering the section list, but not otherwise
  externally meaningful (there is no per-section GET/PUT; editing a document is still Module 18's
  existing full-replace `PUT /documents/{id}`).
- This is the SAME shape `GET /documents/{id}` returns for every other document kind
  (template-instantiated, manually created) — `kind: "business_plan"` and `ai_generated: true` are
  the only fields that distinguish an AI-generated plan from any other document.

---

## 2. Stub vs. real LLM output

`"[stub-llm] AI-generated assessment narrative."` is the fixed, deterministic output of the
`StubLLMClient` used by this e2e run (`LLM_PROVIDER=stub` — no real LLM call, no network, no API
key) — this is why every section in the capture above has the IDENTICAL body text; the stub does
not vary its output by section. In a real deployment (`LLM_PROVIDER=openai`), each section's body
is independently generated from that section's own `heading`/`guidance` plus the startup's
Business Builder context (canvases, records, latest assessment, roadmap) — ten distinct LLM calls,
ten distinct bodies. The FE should not assume any particular length, prefix, or that all sections
look similar — only that each is markdown prose under the given heading.

---

## 3. Suggested FE handling — polling

There is no push/webhook for plan completion (unlike Module 20's notification feed, which now has
real-time SSE for the `business.plan.generated` notification itself — see
`docs/fe-integration-guide-notifications-realtime.md` if you want a push signal for "a plan finished
somewhere," but it carries no `document_id`, only a generic "Your AI business plan is ready"
message — you still need to poll `GET /business-builder/plan` to get the `document_id`).

**Recommended flow:**
1. `POST /plan/generate` → show a "Generating your business plan…" loading state immediately, store
   `plan_id`.
2. Poll `GET /business-builder/plan` every **3–5 seconds**.
3. On `status === "complete"` → fetch `GET /documents/{document_id}` → render the plan.
4. **Give up after a bounded timeout (suggest 2 minutes)** — if still `"generating"`, show a retry
   affordance ("This is taking longer than expected — try again") rather than polling forever. As
   noted in §0, a permanently-stuck-`generating` plan is indistinguishable from "still working" by
   design in this version; there is no server-side signal to short-circuit an indefinite poll.
5. **A page refresh mid-generation is safe** — `GET /business-builder/plan` always returns the
   latest run's current status; there is no client-side state to lose. Resume polling from step 2.

**Never poll `GET /documents/{document_id}` before `GET /business-builder/plan` confirms
`"complete"`.** The document is created ONLY at the end of the worker's run (all 10 sections
assembled first, one `create_document` call) — there is no partial/in-progress document to fetch
early, and `document_id` is `null` until then anyway.

---

## 4. Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /plan/generate` returns 202 with `{plan_id, status: "generating"}` | ✅ | `generate_enqueued.json` |
| Draining the worker runs 10 sequential `complete()` calls and assembles a Document | ✅ | `plan_document.json` — 10 sections, `test_business_plan.py` asserts `len(sections) == 10` |
| `GET /business-builder/plan` reflects `status: "complete"` + non-null `document_id` after the worker runs | ✅ | `plan_complete.json` |
| `GET /documents/{document_id}` returns `kind: "business_plan"`, `ai_generated: true`, all 10 sections | ✅ | `plan_document.json` |
| Editor role (founder/team_member) required for `POST /plan/generate`; non-editor gets 403 | ⚠️ unit only — not re-exercised in THIS live journey (only a founder account is used); covered by `tests/api/test_business_plan.py`, and the same role split is exercised live elsewhere in the suite (e.g. `e2e/test_business_builder.py`) | `tests/api/test_business_plan.py` |
| `GET /business-builder/plan` 404s when no plan has ever been generated | ⚠️ unit only — this e2e journey always generates a plan first | `tests/api/test_business_plan.py` |
| A plan stuck `generating` never transitions to `failed` (no terminal-failure code path exists) | ⚠️ not reachable live without breaking the shared e2e process's `LLM_PROVIDER=stub` guarantee — confirmed by reading `app/worker/handlers/plan.py` (no code path sets `BusinessPlanStatus.failed`) | source inspection, cited in the SOP's Follow-ups |
| `business.plan.generated` notification fires and maps to the `business` category | ⚠️ unit only — this e2e journey does not assert on the notification feed (that surface is covered by `e2e/test_notifications*.py`) | `tests/services/notifications/test_plan_notification.py` |
| Context sent to the LLM is PII-free (no founder name/email) | ⚠️ unit only — not observable over HTTP (the LLM call itself is stubbed in e2e) | `tests/services/business/test_plan_generation.py::test_build_plan_context_gathers_business_data_no_pii` |

The five ⚠️ rows are genuine gaps in this one live journey — forcing a 403, an empty-workspace 404,
a terminal LLM failure, or inspecting the notification feed / LLM prompt contents are either not
reachable from this specific generate→drain→GET walk without duplicating coverage that already
exists live elsewhere in the suite, or (for the failure/PII rows) not observable over HTTP at all —
each is covered by a passing unit test at the cited path instead of an invented live example.
