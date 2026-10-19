# FE integration guide: Business Builder PRD completion, slices (a) and (b)

Last edited, version history + restore, Export to Documents, consultant-only suggest mode, overview metadata.

**Spec:** PRD Module 08 shared editor pattern + §08.1, gap list `cofoundaz/docs/checklist/business-builder-prd-gaps.md`
(rows 2, 4, 5, 18, 19, 20, 21 + the persona `fields` fix in 13). Decisions D-BB-1 (versions), D-BB-2 (export), D-BB-3
(suggest mode). This guide **adds to** `fe-integration-guide-business-builder.md` and
`fe-integration-guide-business-builder-suggestions.md`; everything there still holds except where marked **changed**.

**Every body below is pasted from a live run** (`e2e/test_business_builder.py::test_business_builder_prd_a_journey`,
captures in `e2e/_captures/business_prd/*.json`, plus `e2e/_captures/business/overview_empty.json`). IDs, timestamps
and emails are from that throwaway run and are placeholders: never hard-code them.

Base path `/api/v1/business-builder`. Every call needs `Authorization: Bearer <token>` and `X-Workspace-Id`.

---

## 1. What changed for the FE, in one screen

| Where | What to build |
|---|---|
| Every canvas editor header | "Last edited by {name}, {relative time}" from `updated_by` / `updated_at`; a **version link** that opens the history panel (§3) |
| Every editor header | **Export** → `POST .../export` → toast with **"Open document"** to `/documents/{document_id}` (§4) |
| Overview grid | CTA from `status` (`start` → **"Start"**, `continue` → **"Continue"**, `complete` → **"Review"**), "last edited" line, and a **Business Plan** tile (§2) |
| Suggest mode | Only `business_consultant` gets the banner and the suggest flow; every other non-editor role is read-only (§5) |
| Record lists | Each record now carries `updated_at` / `updated_by` |

---

## 2. Overview (`GET /overview`): **changed**

Two additions: every row has `updated_at`, and a **new last row `type: "plan"`**.

Before anything is touched (`overview_before.json` / `business/overview_empty.json`, plan row):
```json
{
  "type": "plan",
  "label": "Business Plan",
  "status": "none",
  "document_id": null,
  "updated_at": null
}
```

After edits (`overview_after.json`, excerpt):
```json
[
  { "type": "lean", "label": "Lean", "filled_blocks": 0, "total_blocks": 9, "completion_pct": 0, "status": "start", "updated_at": null },
  { "type": "swot", "label": "Swot", "filled_blocks": 1, "total_blocks": 4, "completion_pct": 25, "status": "continue", "updated_at": "2026-10-19T11:08:26.354497+00:00" },
  { "type": "competitor", "label": "Competitor", "status": "complete", "completion_pct": 100, "count": 1, "updated_at": "2026-10-19T11:08:26.421556+00:00" },
  { "type": "plan", "label": "Business Plan", "status": "none", "document_id": null, "updated_at": null }
]
```

- **CTA rule (gap row 1):** drive it from `status`, never from `completion_pct`. A 0% canvas is `start` → **"Start"**.
  The current FE shows "Continue" on every canvas because it ignores `status`.
- `status` values: canvases and records `start | continue | complete` (records are only ever `start` or `complete`);
  plan `none | generating | complete | failed`.
- Plan tile: `none` → "Start" to `/business-builder/plan`; `generating` → "Generating…"; `complete` → "Open document"
  to `/documents/{document_id}`.
- **Trap:** the `label` values (`"Swot"`, `"Lean"`, `"Persona"`) are machine-ish. Keep the FE's own artifact names
  ("SWOT", "Lean Canvas", "Customer Personas") and only use `type` to join.
- **Trap:** the overview is a **bare array** in `data`. `hooks/useBusinessBuilderApi.ts` reads `data.artifacts`, which
  never exists (gap row 21).
- Ordering: canvas rows, then record rows, then plan. Join by `type`; don't rely on index.

---

## 3. Last edited + version history (D-BB-1)

### 3.1 Canvas body: **changed**: two new fields on every canvas response
`GET /canvases/{type}` on a never-saved canvas (`canvas_fresh.json`): `"updated_by": null` (the lazy create is not an edit).
After a save, `PUT /canvases/swot` `{"blocks": {"strengths": ["Founder-market fit"]}, "version": 1}` → **200** (`canvas_save_v2.json`, tail):
```json
    "completion": { "filled_blocks": 1, "total_blocks": 4, "completion_pct": 25, "status": "continue" },
    "updated_at": "2026-10-19T11:08:26.260243+00:00",
    "updated_by": {
      "id": "e0973405-eb76-49f0-8a12-0f750fbd4e36",
      "name": "Ada Founder"
    }
```
- `updated_by` is `null` after an **AI fill** (the worker writes as nobody). Show "Last edited by AI".
- `updated_by.name` falls back to the member's **email** when they never set a name (see §5 trap).

### 3.2 List versions: `GET /canvases/{type}/versions` → **200**, newest first (`versions_list.json`)
```json
{
  "data": {
    "versions": [
      {
        "version": 3,
        "source": "manual",
        "edited_by": { "id": "e0973405-eb76-49f0-8a12-0f750fbd4e36", "name": "Ada Founder" },
        "created_at": "2026-10-19T11:08:26.275892+00:00"
      },
      {
        "version": 2,
        "source": "manual",
        "edited_by": { "id": "e0973405-eb76-49f0-8a12-0f750fbd4e36", "name": "Ada Founder" },
        "created_at": "2026-10-19T11:08:26.260243+00:00"
      }
    ]
  },
  "meta": null
}
```
- One row per save; the top row **is** the current state (its `version` equals the canvas `version`).
- `source`: `manual` (an editor's PUT), `ai` (AI fill, `edited_by: null`), `suggestion` (an approved consultant
  suggestion, `edited_by` = **the consultant**, see `versions_after_suggestion.json`), `restore`.
- Capped at **50** per canvas; older ones disappear. Version 1 (the empty scaffold) is never listed.
- Readable by every workspace member (the same as reading the canvas).

### 3.3 One version: `GET /canvases/{type}/versions/{version}` → **200** (`version_detail.json`)
```json
{
  "data": {
    "version": 2,
    "source": "manual",
    "edited_by": { "id": "e0973405-eb76-49f0-8a12-0f750fbd4e36", "name": "Ada Founder" },
    "created_at": "2026-10-19T11:08:26.260243+00:00",
    "blocks": { "threats": [], "strengths": ["Founder-market fit"], "weaknesses": [], "opportunities": [] }
  },
  "meta": null
}
```
Unknown or pruned version → **404** (`version_not_found.json`):
```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```
Only the list rows lack `blocks`. Fetch a version's blocks when the user previews it.

### 3.4 Restore: `POST /canvases/{type}/versions/{version}/restore` body `{"version": <the canvas's CURRENT version>}`
→ **200**, the full canvas body as a **new** version (`restore_ok.json`: restoring v2 on a canvas at v3 returns
`"version": 4` with v2's blocks). History is never rewritten; the new top row has `"source": "restore"`
(`versions_after_restore.json`).

Errors, captured live:
| Case | Status | Body |
|---|---|---|
| `version` in the body is stale (`restore_stale_409.json`) | 409 | `{"error": {"code": "CANVAS_VERSION_CONFLICT", "message": "This canvas was changed elsewhere. Reload and reapply your edits.", "field_errors": []}}` |
| Caller is not founder / team member (`restore_consultant_403.json`) | 403 | `{"error": {"code": "FORBIDDEN", "message": "You don't have permission to do that.", "field_errors": []}}` |
| Unknown version | 404 | `NOT_FOUND` as above |

**UX:** after restore, replace the editor's local state with the response (and its new `version`) or the next
autosave will 409. On 409, reload the canvas and reopen the panel.

### 3.5 Records: **changed**: `updated_at` / `updated_by` on every record (`record_create_with_updated_by.json`)
```json
{
  "data": {
    "id": "925ba924-ff7d-4e64-95d5-a371b8fec828",
    "kind": "competitor",
    "data": { "name": "Paystack", "map_x": null, "map_y": null, "price": "", "strengths": [], "weaknesses": [], "positioning": "Payments for Africa", "threat_level": "medium" },
    "position": 0,
    "updated_at": "2026-10-19T11:08:26.421556+00:00",
    "updated_by": { "id": "e0973405-eb76-49f0-8a12-0f750fbd4e36", "name": "Ada Founder" }
  },
  "meta": null
}
```
Records have no version history (D-BB-1). `updated_by` is `null` for records written before this release and for
AI-created records.

---

## 4. Export to Documents (D-BB-2)

- `POST /canvases/{type}/export`: canvases (`business_model | lean | value_prop | mission_vision | swot`).
- `POST /{kind}/export`: record lists (`personas | pricing | revenue-streams | competitors`).
- Founder / team member only. No body.

**201** (`export_canvas.json`):
```json
{ "data": { "document_id": "c0ad3028-21f1-49c1-9471-106ecc924c0a", "title": "Cofoundaz PRD A: SWOT" }, "meta": null }
```
Record export **201** (`export_records.json`): `"title": "Cofoundaz PRD A: Competitive Analysis"`.

The Document is a normal **draft** in Documents (`kind: "custom"`, `ai_generated: false`). One section per canvas block,
or one section per record headed by its name. From `GET /api/v1/documents/{id}` (`export_canvas_document.json`, excerpt):
```json
  "kind": "custom",
  "title": "Cofoundaz PRD A: SWOT",
  "status": "draft",
  "sections": [
    { "id": "2bad5c90-916d-421f-92ac-9b9637fe9741", "body": "- Founder-market fit", "heading": "Strengths" },
    { "id": "ca633410-aa0d-426c-837a-7c24aeca4c05", "body": "", "heading": "Weaknesses" }
  ]
```
List blocks are rendered as `- item` lines. Empty blocks still get their heading.

Errors, captured live:
| Case | Status | Body |
|---|---|---|
| Nothing filled / no records (`export_empty_422.json`) | 422 | `{"error": {"code": "NOTHING_TO_EXPORT", "message": "There's nothing to export yet. Add something first.", "field_errors": []}}` |
| Consultant or other read-only role (`export_consultant_403.json`) | 403 | `FORBIDDEN` (as above) |
| Unknown canvas type / kind | 404 | `NOT_FOUND` |

**UX:** each Export creates a **new** Document (no dedupe). Show a toast "Exported to Documents" with **"Open document"**.
Disable Export while the request is in flight. Show the 422 message inline as is. Remove the fake BMC toast and the
Lean canvas's local-JSON download.

---

## 5. Suggest mode (D-BB-3): **changed**

`POST /suggestions` is now **business_consultant only**. Founders, team members (who edit directly), mentors,
accountants, legal advisors and investors get **403** (`suggest_mentor_403.json`):
```json
{ "error": { "code": "FORBIDDEN", "message": "You don't have permission to do that.", "field_errors": [] } }
```
The consultant's **201** body is unchanged (`suggest_consultant_201.json`, see the suggestions guide).

**FE consequences:**
- Show the banner **"You're in suggest mode. The founder approves your edits."** only when the workspace role is
  `business_consultant`. The FE hard-codes the role to `'F'` today (`hooks/useBusinessBuilderApi.ts:36`), so the banner
  never shows (gap row 18). Read the real role from the workspace membership.
- For a consultant, every edit (PUT canvas, record create/update/delete) must become `POST /suggestions` instead; the
  direct writes 403 for them.
- Any other non-editor role is **read-only**: hide Add / edit / AI / Export.

### Founder notification: **changed**
Only **founders** are notified (team members and mentors are not; verified live: the mentor's feed had none). From the
founder's `GET /api/v1/notifications` (`founder_notifications.json`):
```json
{
  "id": "6f4138b2-e5b2-425c-b7b0-9bed3fc3b130",
  "type": "business.suggestion.created",
  "title": "delivered+business-3c3d1291b103@resend.dev suggested changes to Business Model Canvas",
  "body": "Review suggestions",
  "data": {
    "op": "canvas_update",
    "artifact": "Business Model Canvas",
    "created_by": "1d239fcc-220d-4304-9eb0-264af04b9069",
    "startup_id": "77337255-6f8f-42e9-b210-509e1fd083cc",
    "author_name": "delivered+business-3c3d1291b103@resend.dev",
    "suggestion_id": "a5193e7a-92ed-4d15-bc11-0cace9116108"
  },
  "read": false,
  "created_at": "2026-10-19T11:08:26.480462+00:00"
}
```
- `body` is the CTA label (**"Review suggestions"**). Deep-link it to the Overview's suggestions panel.
- **Trap: name fallback.** This test consultant never set a name, so the title uses their **email**. A consultant with a
  profile name shows as "{Name} suggested changes to {Artifact}".
- **Trap: two shapes for a person.** The suggestion's `author` is `{id, name, email}` with `name: null` when unset. The new
  `updated_by` / `edited_by` objects are `{id, name}` where `name` already falls back to the email. Don't reuse one renderer
  for both without handling `null`.

### Plan notification: **changed copy**
`business.plan.generated` → title **"Your AI-generated business plan is ready"**, body (CTA) **"Open it"**. Verified by
unit test (`tests/api/test_business_prd_a.py::test_plan_notification_uses_prd_copy`), **not live**: this journey doesn't
generate a plan.

---

## 6. Record field descriptors: **changed** (bug fix)
`GET /personas` `fields[].type` used to leak Python reprs (`"<class 'str'>"`). Now (`personas_fields.json`):
`"str"`, `"list[str]"`, and for other kinds `"float"`, `"float | None"`, `"ThreatLevel"`, `"PricingModelType"`,
`"list[PricingTier]"` (enum fields also carry `choices`).

---

## 7. Verification

| Behaviour | Verified |
|---|---|
| Overview `updated_at` + plan row (none) | ✅ live |
| Overview plan row `generating` | unit only (`test_overview_carries_last_edited_and_a_plan_row`) |
| Canvas `updated_by` / `updated_at` after PUT; null when fresh | ✅ live |
| Versions list, detail, 404 | ✅ live |
| Restore 200 / 409 / 403 | ✅ live |
| AI fill snapshot `source: "ai"`, `updated_by: null` | unit only (`test_ai_fill_fills_empty_canvas`) |
| Approved suggestion snapshot credited to the consultant | ✅ live |
| 50-version cap | unit only (`test_version_history_is_capped_at_50`) |
| Export canvas / records 201, Document sections | ✅ live |
| Export 422 empty / 403 consultant | ✅ live |
| Suggest 403 for mentor and founder, 201 for consultant | ✅ live (team member 403: unit) |
| Founder-only notification + PRD copy | ✅ live |
| Plan notification copy | unit only |
| Record `updated_by` | ✅ live |
| Field type names | ✅ live |


---

# Slice (b): section comments, per-section AI, Mission & Vision options, personas from validation, pricing advisor

Gap rows 8, 9, 11, 13 (AI part) and 14 (advisor). Captured live from
`e2e/test_business_builder.py::test_business_builder_prd_b_journey` → `e2e/_captures/business_prd_b/*.json`.
**The local run uses the stub LLM**, so AI text reads `"[stub-llm] …"` and every array has **one** item. With the real
model, arrays hold up to 3 items. The **shapes** are real; the **content** is not.

All four AI features are jobs: **202** `{"job_id", "status": "queued"}`. Poll `GET /api/v1/jobs/{job_id}` (every 2s is
fine) until `status` is `succeeded` or `failed`, then read `result`. Every job `result` has a `status` of its own. Handle
`over_budget` (daily AI budget used up, nothing written) and `gone` (the workspace or canvas disappeared) on all of them.

## 9. Canvas `id`: **changed** (additive)
Canvas bodies now carry `"id"`. It is the thread id for comments (`section_ai_canvas_after.json`):
```json
{"id": "d78e16f7-b1bf-4733-8786-e226da3f0591", "type": "swot", "version": 3, "blocks": {"threats": [], "strengths": ["Founder-market fit", "[stub-llm] items"], "weaknesses": [], "opportunities": []}}
```

## 10. Comment threads on sections and records (row 9)
Same `/api/v1/team/comments` API as the Roadmap drawer (`fe-integration-guide-roadmap-prd.md`), with two new
`entity_type`s and an optional **`anchor`**:
| entity_type | entity_id | anchor |
|---|---|---|
| `business_canvas` | the canvas `id` (§9) | a block key (e.g. `strengths`) for a section thread, or omitted for the whole canvas |
| `business_record` | the record `id` | must be omitted |

`POST /api/v1/team/comments` `{"entity_type": "business_canvas", "entity_id": "<canvas id>", "anchor": "strengths", "body": "…"}` → **201** (`comment_section_create.json`):
```json
{
  "data": {
    "id": "51b8c01a-a6be-498b-bf4d-f727be6e7c9d",
    "entity_type": "business_canvas",
    "entity_id": "d78e16f7-b1bf-4733-8786-e226da3f0591",
    "anchor": "strengths",
    "author": { "id": "6ed5aaaa-a0ed-49a8-b806-8eb7e853c06f", "name": "Ada Founder" },
    "body": "Is founder-market fit provable yet?",
    "mention_user_ids": [],
    "created_at": "2026-10-19T11:37:49.712731+00:00",
    "edited_at": null,
    "resolved_at": null,
    "is_mine": true
  },
  "meta": null
}
```
A record comment returns the same shape with `"entity_type": "business_record"` and `"anchor": null`
(`comment_record_create.json`).

`GET /api/v1/team/comments?entity_type=business_canvas&entity_id=<id>` returns the **whole canvas** thread, oldest first,
with each comment's `anchor` (`comment_thread_canvas.json`). Fetch it once per canvas and group by `anchor`, so each
block can show its count. Add `&anchor=<key>` to fetch one section.

Unknown block, or an anchor on any other entity type → **422** (`comment_bad_anchor_422.json`):
```json
{"error": {"code": "VALIDATION_ERROR", "message": "That section doesn't exist on this item.", "field_errors": [{"field": "anchor", "message": "Not a section of this item."}]}}
```
Permissions are unchanged: everyone but investors can post (consultants included, so they can explain a suggestion).
Only the author can edit or delete. Founders, team members or the author can resolve. Mentions notify as on the Roadmap.

## 11. Per-section AI assist (row 8, D-BB-4)
`POST /canvases/{type}/ai-fill` now takes an optional body `{"block": "<key>"}`. Without a body it is the old
whole-canvas fill (empty blocks only). With a block:
- **List block:** appends up to 3 new items, de-duplicated case-insensitively. Existing items are never edited, removed
  or reordered. Other blocks are untouched, even empty ones.
- **Text block** (Mission & Vision): written only if empty. Otherwise the result is `nothing_new`. Use "Give me 3 options"
  (§12) for an existing statement.

Enqueue → **202** (`section_ai_enqueue.json`): `{"data": {"job_id": "4d5324a0-…", "status": "queued"}, "meta": null}`.
Finished job (`section_ai_job_done.json`):
```json
{
  "data": {
    "id": "4d5324a0-6ae6-4e4b-b3d4-d9adaa46f4b2",
    "type": "business.canvas.ai_fill",
    "status": "succeeded",
    "result": { "added": ["[stub-llm] items"], "block": "strengths", "status": "ready", "version": 3 },
    "error": null
  },
  "meta": null
}
```
Running it again when the model has nothing new (`section_ai_nothing_new.json`):
`"result": {"added": [], "block": "strengths", "status": "nothing_new"}`.

`result.status`: `ready` (`added` lists the new items; the canvas is now at `result.version`) · `nothing_new` ·
`over_budget` · `gone`. **UX:** on `ready`, **refetch the canvas** before the next autosave (its version moved; a stale
PUT would 409) and briefly highlight the `added` items. The write appears in version history as `source: "ai"`.
Unknown block → **422** (`section_ai_bad_block_422.json`): `"message": "That section doesn't exist on this canvas."`,
`field_errors[0].field = "block"`. Founder / team member only; a consultant gets 403.

## 12. Mission & Vision "Give me 3 options" (row 11)
`POST /canvases/mission_vision/options` `{"field": "mission" | "vision"}` → 202 job `ai.business.mission_vision_options`.
Done (`mv_options_job_done.json`):
```json
"result": { "field": "mission", "status": "ready", "options": ["[stub-llm] options"] }
```
Up to 3 strings with the real model (1 with the stub). **Nothing is written.** Show them as variant cards; **"Use this"**
is a normal `PUT /canvases/mission_vision` with that text and the current `version`. A bad `field` → **422**
(`mv_options_bad_field_422.json`), a framework validation error:
`{"field": "field", "message": "Input should be 'mission' or 'vision'"}` with the top-level message
"Please check the highlighted fields.".

## 13. Personas from validation notes (row 13, AI part)
`POST /personas/from-validation` (no body). Uses the workspace's Validation interviews (segment, notes, key quotes,
latest 10) and assumptions. **Interviewee names are never sent to the model.**
- With no interviews and no assumptions → **422** (`personas_from_validation_422.json`):
  `{"error": {"code": "NO_VALIDATION_DATA", "message": "Log a customer interview or an assumption in Validation first.", "field_errors": []}}`.
  Show the message with a link to `/validation`.
- Otherwise 202 job `ai.business.personas_from_validation`. Done (`personas_from_validation_job_done.json`):
```json
"result": { "status": "ready", "interviews_used": 1, "created_record_ids": ["55bee50a-c4f2-489e-84da-234d1f92f663"] }
```
New personas are **appended** next to the founder's own, skipping any whose name already exists
(`personas_after.json` lists 2: the founder's "Amaka" plus the new one). `status: "nothing_new"` means every draft
duplicated an existing name. Refetch `GET /personas` on `ready`.

## 14. Pricing AI advisor (row 14, advisor part)
`POST /pricing/advisor`:
```json
{ "competitor_prices": [{"name": "Moniepoint", "price": "NGN 0 setup"}], "willingness_to_pay": "Traders said NGN 2,000 a month feels fair" }
```
Both fields are optional. `competitor_prices` holds at most 20 entries (name and price 1 to 120 chars each); more than 20 →
422. `willingness_to_pay` is at most 2000 chars. The server **adds the competitor records' own prices** (from Competitive
Analysis) to the ones you send, so prefill the panel from those records rather than asking twice.

Done (`pricing_advisor_job_done.json`):
```json
"result": {
  "advice": {
    "tiers": [ { "name": "[stub-llm] name", "price": "[stub-llm] price", "features": ["[stub-llm] features"] } ],
    "rationale": "[stub-llm] rationale",
    "wtp_questions": ["[stub-llm] wtp_questions"],
    "recommended_model": "subscription"
  },
  "status": "ready"
}
```
- `recommended_model` is one of `subscription | one_time | usage | freemium | tiered`, the same values as the pricing
  record's `model_type`.
- Real model: up to 5 `wtp_questions` and up to 3 `tiers`. `price` is free text (it may include a currency).
- **Advice only:** nothing is saved (verified live: `GET /pricing` stays empty). **"Apply"** = `POST /pricing` (or `PUT`
  the existing pricing record) with `model_type` and `tiers` mapped from the advice. Show the rationale and call the
  prices starting points.

## 15. Verification (slice b)
| Behaviour | Verified |
|---|---|
| Canvas `id` | ✅ live |
| Section comment create / thread grouped by anchor / record comment | ✅ live |
| Bad anchor 422 | ✅ live; anchor on a roadmap entity 422 and cross-tenant record 404: unit |
| Section AI append-only, other blocks untouched, `nothing_new` on rerun | ✅ live (stub LLM) |
| Section AI on a filled text block does not overwrite | unit only |
| Section AI bad block 422 | ✅ live; consultant 403: unit |
| M&V options job, nothing written, bad field 422 | ✅ live (stub); mentor 403: unit |
| Personas from validation 422 / append + dedupe | ✅ live (stub) |
| Interviewee names never in the prompt | unit (`test_validation_prompt_has_no_interviewee_names`) |
| Pricing advisor merges competitor-record prices, advice only | ✅ live (stub) + unit; >20 prices 422: unit |
| Real-model content | ✅ live against the real model on the local stack (`e2e/_captures/business_prd_b_live/`): section AI added 3 strengths, M&V returned 3 mission options, the advisor returned 3 tiers (NGN 0 / 2,000 / 5,000 a month) anchored on the typed willingness to pay. Personas-from-validation with the real model: **not run** (the local workspace has no interviews) |
