# FE Integration Guide — Documents & Templates (Module 18, Slice 1: Document Library Core)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_documents.py::test_documents_journey` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/documents/*.json`. Nothing here is retyped from the
schema, the service, or memory. IDs and timestamps are real values from that ephemeral test run
(they differ on every real request; the shapes are exact). Every payload, status code, and error
body in this guide was exercised live, except the `403 FORBIDDEN` write-role row (§7), which is
cited from a passing unit test rather than captured live in this journey — noted inline where it
appears.

Base path: `/api/v1`. Same auth convention as every other tenant-scoped module in this API: Bearer
access token (`Authorization: Bearer <token>`) + `X-Workspace-Id` header identifying the active
workspace (`GET /auth/me` → `data.active_workspace_id` is the source for that header).

**Role rule: read = any active member, write = founder or team_member (editor).**
`GET /documents`, `GET /documents/{id}`, `GET /document-templates`, `GET
/document-templates/{key}` use the same `require_workspace` dependency as every other read
endpoint in this API — any active member (including `mentor`, `business_consultant`, `investor`,
etc.) can call them. `POST`/`PUT`/`DELETE /documents` use `_editor = require_role(founder,
team_member)` — the exact same role gate Business Builder's write endpoints use. A non-editor
member gets `403 FORBIDDEN` on any write (§7).

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §7).

---

## 1. The template catalog — `GET /api/v1/document-templates`

The in-code template registry, read-only, five entries. Any active member can call this.

Response (`e2e/_captures/documents/templates_catalog.json`, status `200`):

```json
{
  "data": {
    "templates": [
      {
        "key": "business_plan",
        "name": "Business Plan",
        "description": "A full business plan.",
        "kind": "business_plan",
        "sections": [
          "Executive Summary",
          "Problem",
          "Solution",
          "Market & Customer",
          "Business Model",
          "Go-to-Market",
          "Team",
          "Financials",
          "The Ask"
        ]
      },
      {
        "key": "pitch_deck",
        "name": "Pitch Deck",
        "description": "An investor pitch narrative.",
        "kind": "pitch_deck",
        "sections": ["Hook", "Problem", "Solution", "Why Now", "Market", "Product", "Team", "The Ask"]
      },
      {
        "key": "financial_model",
        "name": "Financial Model",
        "description": "A lightweight financial narrative.",
        "kind": "financial_model",
        "sections": ["Assumptions", "Revenue", "Costs", "Runway", "Projections"]
      },
      {
        "key": "meeting_notes",
        "name": "Meeting Notes",
        "description": "Structured meeting notes.",
        "kind": "meeting_notes",
        "sections": ["Attendees", "Agenda", "Discussion", "Decisions", "Action Items"]
      },
      {
        "key": "one_pager",
        "name": "One-Pager",
        "description": "A concise one-page overview.",
        "kind": "one_pager",
        "sections": ["Overview", "Problem", "Solution", "Traction", "The Ask"]
      }
    ]
  },
  "meta": null
}
```

`templates[].sections` here is a **plain list of heading strings** — this is the template's
*preview* shape, not a document's actual sections. When you instantiate a document from a
template (§3), each heading becomes a real section object `{id, heading, body: ""}` (§4). Don't
confuse the two — a template has no `id`s or bodies of its own.

### `GET /api/v1/document-templates/{key}` — one template

Same shape as one element of the catalog above. Response for `business_plan`
(`e2e/_captures/documents/template_detail.json`, status `200`):

```json
{
  "data": {
    "key": "business_plan",
    "name": "Business Plan",
    "description": "A full business plan.",
    "kind": "business_plan",
    "sections": [
      "Executive Summary",
      "Problem",
      "Solution",
      "Market & Customer",
      "Business Model",
      "Go-to-Market",
      "Team",
      "Financials",
      "The Ask"
    ]
  },
  "meta": null
}
```

An unknown `key` returns **404** (`NOT_FOUND` — see §7), same shape as any other 404 in this API.

---

## 2. The section object

Every document's `sections` array (present on create/get/update — see the summary-vs-full trap in
§5) holds objects shaped:

```json
{ "id": "e6bea67f-776a-410f-a8c1-68f0ba32bd6d", "body": "", "heading": "Executive Summary" }
```

| Field | Meaning |
|---|---|
| `id` | Server-assigned UUID (assigned on create if you omit it, or if you send a section without one on a `PUT`). Stable across edits — reuse it if you want to preserve a section's identity through a resave, but it is **not** required in your `PUT` body: any section without an `id` gets a fresh one. |
| `heading` | Plain text. Required, must be a string. |
| `body` | Markdown text. Required, must be a string (empty string `""` is valid — that's what a freshly-instantiated template section looks like). |

**Order in the array *is* display order** — there is no separate `position`/`order` field. To
reorder sections, resend the whole array in the order you want (full-replace, see §6).

**Field-nesting trap:** note the key order in the live capture above is `id`, `body`, `heading` —
**do not rely on JSON key order**; JSONB does not preserve insertion order across a round trip.
Always read by key name, never by array-of-keys position.

---

## 3. `POST /api/v1/documents` — create (from a template or blank)

**Editor only** (founder/team_member) — `403 FORBIDDEN` for any other role (§7). Body:
`{"kind"?, "title"?, "folder"?, "template_key"?, "sections"?}` — every field optional.

- **If `template_key` is set:** the server looks it up (§1), seeds `kind`/`title`/`sections` from
  that template, and ignores any `kind`/`sections` you also sent. A `title` you send **overrides**
  the template's default title; if you omit `title`, the template's `name` is used as-is.
- **If `template_key` is omitted:** `kind` defaults to `custom`, `title` defaults to `""`, and
  `sections` (if provided) is validated against the shape in §2 — a bad shape (non-string
  `heading`/`body`, or a non-object array element) → `422 VALIDATION_ERROR`.

**Request** (this journey's create call): `{"template_key": "business_plan"}`.

**Response — 201** (`e2e/_captures/documents/document_create.json`):

```json
{
  "data": {
    "id": "7fe71ec8-65f6-48d1-98b9-7b28485db29b",
    "kind": "business_plan",
    "title": "Business Plan",
    "status": "draft",
    "ai_generated": false,
    "folder": null,
    "template_key": "business_plan",
    "version": 1,
    "updated_at": "2026-09-09T15:08:01.109437+00:00",
    "sections": [
      { "id": "e6bea67f-776a-410f-a8c1-68f0ba32bd6d", "body": "", "heading": "Executive Summary" },
      { "id": "55a51467-6ce5-4340-b3e5-2f98cec013dc", "body": "", "heading": "Problem" },
      { "id": "63f8094d-782a-421f-8b26-8fe35b0a76eb", "body": "", "heading": "Solution" },
      { "id": "5b1e5048-bb84-4a1d-81d9-3f0a51bb6828", "body": "", "heading": "Market & Customer" },
      { "id": "a2b78259-3cc4-459a-8424-92d8c787ab83", "body": "", "heading": "Business Model" },
      { "id": "7e7544ef-90e0-4800-bcfd-0b90461caaea", "body": "", "heading": "Go-to-Market" },
      { "id": "eaea23a4-b98b-44a9-ade5-4dd809550b8a", "body": "", "heading": "Team" },
      { "id": "5ca5941b-6f9d-4776-a9bb-51c7aa002a9d", "body": "", "heading": "Financials" },
      { "id": "faed385f-bc29-4020-9341-2e3ee963f9bd", "body": "", "heading": "The Ask" }
    ]
  },
  "meta": null
}
```

Note: `status_code` is **201**, not 200 — this is a real create, unlike Founder Journal's
upsert-`POST` (which returns 200). `folder` is `null` until you set one via `PUT` (§6).
`ai_generated` is `false` here — it will be `true` only when Module 08's future AI Business Plan
Generator calls the same create seam (see the SOP's Follow-ups for that dependency).

---

## 4. `GET /api/v1/documents/{id}` — one full document

Any active member. Same shape as the create response, **with `sections`** — see the trap in §5.

Response right after create (`e2e/_captures/documents/document_get.json`, status `200`) — byte-
identical to §3's create response for this document, since nothing had changed yet:

```json
{
  "data": {
    "id": "7fe71ec8-65f6-48d1-98b9-7b28485db29b",
    "kind": "business_plan",
    "title": "Business Plan",
    "status": "draft",
    "ai_generated": false,
    "folder": null,
    "template_key": "business_plan",
    "version": 1,
    "updated_at": "2026-09-09T15:08:01.109437+00:00",
    "sections": [ /* same 9 sections as §3 */ ]
  },
  "meta": null
}
```

An unknown or cross-tenant `id` → **404** (`NOT_FOUND`, §7) — uniform, same as every other
tenant-scoped lookup in this API.

---

## 5. `GET /api/v1/documents?kind=&folder=&status=` — the summary-vs-full trap

**This is the single most important thing in this guide.** The list endpoint returns
**summaries** — the exact same fields as the full document **except `sections` is absent
entirely**, not `null`, not `[]` — the key does not exist on a list row.

Query params (all optional, combinable): `kind` (one of `business_plan`/`pitch_deck`/
`financial_model`/`meeting_notes`/`one_pager`/`custom` — unknown value → 404, §7), `folder`
(exact string match), `status` (`draft`/`final` — unknown value → 404, §7).

Response for `?folder=Investor+Docs` after this journey's `PUT` (§6) moved the document into that
folder (`e2e/_captures/documents/documents_list_by_folder.json`, status `200`):

```json
{
  "data": {
    "documents": [
      {
        "id": "7fe71ec8-65f6-48d1-98b9-7b28485db29b",
        "kind": "business_plan",
        "title": "Cofoundaz Business Plan",
        "status": "final",
        "ai_generated": false,
        "folder": "Investor Docs",
        "template_key": "business_plan",
        "version": 2,
        "updated_at": "2026-09-09T15:08:01.135778+00:00"
      }
    ]
  },
  "meta": null
}
```

**Build your library/grid view off this endpoint, and fetch `GET /documents/{id}` (§4) only when
the founder actually opens a document.** Do not write client code that expects `sections` to be
present-but-empty on a list row — `row.sections` is `undefined`, not `[]`, and code that assumes
otherwise will silently show a blank editor instead of erroring. List rows are sorted
newest-`updated_at`-first.

---

## 6. `PUT /api/v1/documents/{id}` — full-replace edit + optimistic concurrency

**Editor only.** Body: `{"title", "sections", "status", "folder", "version"}` — **`version` is
required**, and this is a genuine full-replace: any field you omit (other than `folder`, which is
nullable) resets to its schema default, same as Business Builder's canvas/record `PUT`s. Send
back every section you want to keep, in the order you want them displayed — an omitted section is
gone, not preserved.

**Request** (this journey's edit — 2 of the original 9 sections, a title change, `status`
`draft`→`final`, a folder, and `version: 1` matching the document's current version):

```json
{
  "title": "Cofoundaz Business Plan",
  "sections": [
    { "heading": "Executive Summary", "body": "We help founders ship faster." },
    { "heading": "The Ask", "body": "$500k pre-seed." }
  ],
  "status": "final",
  "folder": "Investor Docs",
  "version": 1
}
```

**Response — 200** (`e2e/_captures/documents/document_update.json`) — note **`version` is now
`2`**, and the two sections you sent got **fresh server-assigned `id`s** (you didn't send `id`s,
so none were reused):

```json
{
  "data": {
    "id": "7fe71ec8-65f6-48d1-98b9-7b28485db29b",
    "kind": "business_plan",
    "title": "Cofoundaz Business Plan",
    "status": "final",
    "ai_generated": false,
    "folder": "Investor Docs",
    "template_key": "business_plan",
    "version": 2,
    "updated_at": "2026-09-09T15:08:01.135778+00:00",
    "sections": [
      { "id": "7f947bc2-8c99-45bd-b5f3-12cc9821f618", "body": "We help founders ship faster.", "heading": "Executive Summary" },
      { "id": "bb41b8e5-28c5-4cba-a6ec-9e051466bea7", "body": "$500k pre-seed.", "heading": "The Ask" }
    ]
  },
  "meta": null
}
```

### The version-conflict contract — `409 DOCUMENT_VERSION_CONFLICT`

`version` is optimistic-concurrency, exactly like Business Builder's canvas `PUT`. **Always send
back the `version` you last read** — after a successful edit, the response's `version` (here,
`2`) is what your *next* `PUT` must send. Re-sending a now-stale `version` (here, the original `1`,
after the document had already moved to `2`) fails:

**Request** (same document, `version: 1` again — now stale):

```json
{ "title": "Should not apply", "sections": [], "status": "draft", "version": 1 }
```

**Response — 409** (`e2e/_captures/documents/document_update_conflict.json`):

```json
{
  "error": {
    "code": "DOCUMENT_VERSION_CONFLICT",
    "message": "This document was changed elsewhere. Reload and reapply your edits.",
    "field_errors": []
  }
}
```

**A rejected write does not partially apply.** Confirmed live: after the 409 above, `GET
/documents/{id}` still showed `version: 2` and `status: final` — none of the stale request's
fields leaked through. **UX guidance:** on this 409, re-fetch the document (`GET
/documents/{id}`), show the founder the current state, and let them re-apply their edit on top of
it — do not silently retry the same stale `version`, and do not let the editor keep the founder's
local draft without warning them someone/something else moved the document (e.g. another tab, or
in a later slice, a collaborator).

---

## 7. Errors

Standard envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | — | Missing or invalid access token | not captured in this journey — same auth dependency as every other module |
| 403 | `FORBIDDEN` | Non-editor (e.g. `mentor`) calls `POST`/`PUT`/`DELETE /documents` | not captured live in this journey — asserted over HTTP in `tests/api/test_documents.py::test_mentor_cannot_write_403` (same member-can-read / non-editor-cannot-write assertion this guide describes) |
| 404 | `NOT_FOUND` | Unknown/cross-tenant document `id`; unknown `?kind=`/`?status=` filter value; unknown template `key` | `document_get_after_delete.json` (id gone after delete) |
| 409 | `DOCUMENT_VERSION_CONFLICT` | `PUT /documents/{id}` with a `version` that no longer matches the document's current version | `document_update_conflict.json` |
| 422 | `VALIDATION_ERROR` | Bad `sections` shape on create/update — non-object array element, or `heading`/`body` not a string | not captured live in this journey (happy-path payloads only) — asserted in `tests/services/documents/test_service.py` / `tests/api/test_documents.py::test_create_blank_and_bad_sections_422` |

### The `?kind=`/`?status=` unknown-value trap

Sending an unrecognized `kind` or `status` filter value (e.g. `?kind=bogus`) does **not** return
an empty list, and does **not** 422 — it 404s, exactly like an unknown suggestion `?status=` on
Business Builder. Treat any 404 from `GET /documents` the same way you'd treat a typo'd route: it
means "no such view", not "no results for this query". Build your filter UI from a fixed enum of
known `kind`/`status` values (§1's catalog for `kind`; `draft`/`final` for `status`), never from
free user text, and this trap never surfaces to a real founder.

---

## Verification table

All rows below except the `403` row were exercised **live**, over real HTTP, against a real
Postgres-backed server (`scripts/e2e_run.sh`, `e2e/test_documents.py::test_documents_journey`) —
not just unit-tested in-process — and every response body is captured verbatim in the named file.

| Behaviour | Verified live? | Source |
|---|---|---|
| `GET /document-templates` — 5-entry catalog | ✅ | `templates_catalog.json` |
| `GET /document-templates/{key}` — one template detail | ✅ | `template_detail.json` |
| `POST /documents` with `template_key` — seeds `kind`/`title`/9 sections, `version: 1`, 201 | ✅ | `document_create.json` |
| `GET /documents/{id}` — full document, `sections` present | ✅ | `document_get.json` |
| `PUT /documents/{id}` — full-replace, `version` 1→2, `status` draft→final, `folder` set | ✅ | `document_update.json` |
| Stale `PUT` (`version: 1` after the doc moved to `2`) → `409 DOCUMENT_VERSION_CONFLICT` | ✅ | `document_update_conflict.json` |
| Rejected (409) write does not partially mutate the row | ✅ | re-`GET` in `test_documents_journey`, asserted inline (not a separate capture) |
| `GET /documents?folder=...` — summary shape, `sections` key absent | ✅ | `documents_list_by_folder.json` |
| `DELETE /documents/{id}` — `{deleted: true}` | ✅ | `document_delete.json` |
| `GET /documents/{id}` after delete → `404 NOT_FOUND` | ✅ | `document_get_after_delete.json` |
| Non-editor (`mentor`) → `403 FORBIDDEN` on write; member can still read | ⚠️ unit only | `tests/api/test_documents.py::test_mentor_cannot_write_403` |
| Bad `sections` shape → `422 VALIDATION_ERROR` | ⚠️ unit only | `tests/api/test_documents.py::test_create_blank_and_bad_sections_422` |
| Unknown `?kind=` filter → `404` | ⚠️ unit only | `tests/api/test_documents.py::test_bad_kind_filter_404` |
| Cross-tenant `GET` (real document, different startup) → `404` | ⚠️ unit only | `tests/api/test_documents.py::test_cross_tenant_get_real_document_404` |

The two ⚠️ rows are genuine gaps in this journey (a second-role/second-tenant setup was judged not
worth the added journey complexity for this slice) rather than unexercised guesses — both are
backed by a passing HTTP-level test, not merely inferred from source.
