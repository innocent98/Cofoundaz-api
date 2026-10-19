# FE integration guide: Business Builder PRD completion, slice (a)

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
