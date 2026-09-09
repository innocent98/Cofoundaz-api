# Module 18 — Documents & Templates, Slice 1: Document Library Core

**Date:** 2026-09-09
**Module:** 18 Documents & Templates (Slice 1 of 4)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop`
**Depends on:** nothing (dependency-free). Storage/AI not required for this slice.

---

## 1. Context & Scope

Module 18 has **no detailed textual PRD** — the backend kickoff brief only lists it in the
26-module grid. Its scope was recovered from the **UI design comp**
(`../# Cofoundaz Web App UI Build/Documents & Templates.dc.html`, one comp per module) and
decomposes into **four slices**:

1. **Library Core** *(this slice)* — structured section-based documents + an in-code template
   registry. Dependency-free.
2. **Upload & Files** — opaque file upload via **Cloudinary** (a URL-returning `Storage` impl).
3. **Sharing** — share links + expiry + access levels + email (needs Module 20 / email).
4. **E-signature** — signature-request workflow (needs an e-sign provider).

### Slice 1 delivers

A **document** = `title + ordered markdown sections`, owned per-startup, lightly versioned,
with `kind` / `status` / `ai_generated` / `folder` metadata; full CRUD + list; and an in-code
**template registry** (Business Plan / Pitch Deck / Financial Model / Meeting Notes / One-Pager)
that instantiates a new document's sections.

**Why now / why this is the seam:** this is the storage/render home Module 08's deferred
**AI Business Plan generator** plugs into — when that slice is built (gated on Module 03 AI +
this module), it will call `create_document(...)` with `ai_generated=True, kind=business_plan`,
and Module 08's `business_plans.document_id` will FK to `documents.id`. **Slice 1 does NOT build
`business_plans` or the generator** — only the document store + the create seam.

### Non-goals (this slice)

- No file uploads / binary attachments (Slice 2, Cloudinary).
- No sharing, share links, or email (Slice 3).
- No e-signature (Slice 4).
- No server-side PDF/DOCX export — the FE renders/​prints the returned structured sections
  (decided during brainstorming; a real export awaits a URL-returning storage backend).
- No separate `folders` table — `folder` is a freeform string label on the document.
- No granular per-section endpoints — documents use full-replace `PUT` with a `version`
  (the canvas pattern); section-level ops are deferred until a real need appears.
- No user-authored templates — the template registry is read-only, in-code, for v1.

---

## 2. Data model — `documents` (one new table + migration)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin`, `server_default now()` |
| `startup_id` | UUID FK→`startups.id` CASCADE, `index=True` | tenancy scope |
| `created_by_id` | UUID FK→`users.id` `ON DELETE SET NULL`, `index=True` | author |
| `kind` | `Enum(DocumentKind, native_enum=False, length=20)`, `server_default 'custom'` | category |
| `status` | `Enum(DocumentStatus, native_enum=False, length=20)`, `server_default 'draft'` | `draft`/`final` |
| `ai_generated` | `Boolean`, `server_default 'false'`, NOT NULL | the comp's "AI generated" marker |
| `folder` | `String(120)`, nullable | freeform grouping label |
| `template_key` | `String(50)`, nullable | which registry template instantiated it |
| `title` | `String(200)`, NOT NULL, `server_default ''` | |
| `sections` | `JSONB`, NOT NULL, `server_default '[]'` | `[{"id": uuid, "heading": str, "body": str}]` |
| `version` | `Integer`, NOT NULL, `server_default '1'` | optimistic concurrency |

**Indexes:** FK `startup_id` gets a standalone `index=True` (convention) **and** a composite
`Index("ix_documents_startup_kind", "startup_id", "kind")` for list-by-kind.

**New enums** (`app/db/models/enums.py`, `enum.StrEnum` + VARCHAR-backed columns per the
22-column convention):

```python
class DocumentKind(enum.StrEnum):
    business_plan = "business_plan"
    pitch_deck = "pitch_deck"
    financial_model = "financial_model"
    meeting_notes = "meeting_notes"
    one_pager = "one_pager"
    custom = "custom"

class DocumentStatus(enum.StrEnum):
    draft = "draft"
    final = "final"
```

**Section shape (JSONB element):** `{"id": <uuid str>, "heading": <str>, "body": <str markdown>}`.
On write, the service assigns `id` (uuid4) to any section lacking one, and validates each element
is an object with string `heading`/`body` (else 422). Order in the array *is* the display order.

---

## 3. Templates — in-code `DOCUMENT_TEMPLATES` registry

A read-only registry in `app/services/documents/template_defs.py`, mirroring
`app/services/business/canvas_defs.py` / `app/services/roadmap/templates.py`. Each entry:

```python
@dataclass(frozen=True)
class DocumentTemplate:
    key: str
    name: str
    description: str
    kind: DocumentKind
    sections: tuple[str, ...]   # section headings; bodies start empty
```

Seeded set (headings from the comp):

| key | name | kind | section headings |
|---|---|---|---|
| `business_plan` | Business Plan | business_plan | Executive Summary · Problem · Solution · Market & Customer · Business Model · Go-to-Market · Team · Financials · The Ask |
| `pitch_deck` | Pitch Deck | pitch_deck | Hook · Problem · Solution · Why Now · Market · Product · Team · The Ask |
| `financial_model` | Financial Model | financial_model | Assumptions · Revenue · Costs · Runway · Projections |
| `meeting_notes` | Meeting Notes | meeting_notes | Attendees · Agenda · Discussion · Decisions · Action Items |
| `one_pager` | One-Pager | one_pager | Overview · Problem · Solution · Traction · The Ask |

`instantiate(template_key) -> (kind, title, sections)`: returns the template's `kind`, a default
`title` (the template `name`), and `sections` = one `{id, heading, body:""}` per heading.
Unknown `template_key` → `NotFound` (404).

---

## 4. API — new `/documents` + `/document-templates` router

Mounted at `/api/v1/documents` and `/api/v1/document-templates` (own module
`app/api/v1/endpoints/documents.py`, registered in `app/api/v1/api.py`). Documents are
workspace artifacts: **read = any member** (`require_workspace`); **write = editor**
(`_editor = require_role(founder, team_member)`), consistent with Business Builder.

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/documents?kind=&folder=&status=` | member | list **summaries** (no `sections` body): `{id, kind, title, status, ai_generated, folder, template_key, version, updated_at}`, newest-updated first; optional filters |
| `POST` | `/documents` | editor | create — body `{kind?, title?, folder?, template_key?, sections?}`. If `template_key` → seed `kind`/`title`/`sections` from that registry entry; else use the provided `kind`/`title`/`sections` (validated) or blank defaults. Returns the full document. Publishes `document.created`. |
| `GET` | `/documents/{id}` | member | full document incl. `sections` |
| `PUT` | `/documents/{id}` | editor | full-replace `{title, sections, status, folder, version}` → **409 `DOCUMENT_VERSION_CONFLICT`** if `version` ≠ current; on success bumps `version` |
| `DELETE` | `/documents/{id}` | editor | delete → `{deleted: true}` |
| `GET` | `/document-templates` | member | registry catalog: `[{key, name, description, kind, sections:[headings]}]` |
| `GET` | `/document-templates/{key}` | member | one template (same shape); unknown key → 404 |

- Cross-tenant / missing document by id → uniform **404** (`NotFound`), scoped by
  `membership.startup_id`. `document_id` path param typed `uuid.UUID` (FastAPI 422s a malformed
  id before the service — the lesson from the Slice 3 review).
- Bad section shape → **422 `VALIDATION_ERROR`** (existing `AppError`).
- `?kind=`/`?status=` parse to their enums; an unknown value → 404 (uniform "no such view",
  matching the Slice 3 suggestions `?status=` handling).
- **`get_db()` does not auto-commit** — `POST`/`PUT`/`DELETE` MUST `db.commit()`.

### Service (`app/services/documents/service.py`)

- `create_document(db, startup, *, created_by_id, kind, title, sections, folder, template_key, ai_generated=False) -> Document` — the seam Module 08 will call; validates sections, assigns section ids, flushes, publishes `document.created`.
- `list_documents(db, startup, *, kind, folder, status) -> list[Document]`
- `get_document(db, membership, document_id) -> Document` (404 scoped)
- `update_document(db, doc, *, title, sections, status, folder, expected_version) -> Document` (409 on stale)
- `delete_document(db, doc) -> None`
- `serialize_document(doc) -> dict` / `serialize_summary(doc) -> dict`
- `validate_sections(sections) -> list[dict]` (shape check + id assignment)

---

## 5. Cross-cutting

- **Migration:** one `documents` table, chained onto the **then-current `develop` head at build
  time**. Coordination: Slice-3 PR #47 (`0014_business_suggestions`/`0015_business_positioning_maps`)
  is open; if it merges first, this is `0016_documents`; otherwise number against whatever head
  exists. `alembic heads` must show a single head and `alembic check` zero drift before push.
- **Events:** `document.created` via `event_bus`, payload `{startup_id, document_id, kind}`.
  Consistent with `business.artifact.completed`; no consumer until a notifications module.
- **Errors:** new `DocumentVersionConflict` (409, code `DOCUMENT_VERSION_CONFLICT`); reuse
  `NotFound` (404) and `VALIDATION_ERROR` (422).

---

## 6. Testing

**Unit (real Postgres, per-test rollback):**
- Create: blank; from each `template_key` (sections seeded, correct `kind`/`title`, ids assigned);
  with explicit `sections` (validated, ids assigned); unknown `template_key` → 404; bad section
  shape → 422.
- List: returns summaries without `sections`; `kind`/`folder`/`status` filters; newest-updated
  ordering; empty list.
- Get: full document incl. sections; cross-tenant / unknown id → 404.
- Update: full-replace (title/sections/status/folder); version bump; stale version → 409
  `DOCUMENT_VERSION_CONFLICT`.
- Delete: removes; subsequent get → 404.
- Template registry: catalog shape; `instantiate` produces one section per heading with ids +
  empty bodies; unknown key → 404.
- RBAC matrix: member can read (list/get/templates); non-editor (mentor) → 403 on
  create/update/delete; non-member of the workspace → **403** (`require_workspace` raises
  `Forbidden`, the codebase-wide convention). Note: a *cross-tenant resource* access — a
  valid document id belonging to another startup — returns uniform **404** (scoped by
  `membership.startup_id`); the 403 is the workspace-membership gate, the 404 is the
  resource-scoping gate.
- `document.created` event fires once on create.
- Seam: `create_document(..., ai_generated=True, kind=business_plan)` persists correctly (the
  shape Module 08 will use).

**Live e2e (`scripts/e2e_run.sh`):** create-from-template → get → edit (version bump) →
list-filtered → delete, end to end, capturing every request/response body under
`e2e/_captures/documents/`.

**FE integration guide:** `docs/fe-integration-guide-documents-templates.md` — every payload/
status/error copied verbatim from the captures; the template catalog, the section JSON shape,
the `version`/409 optimistic-concurrency contract, the summary-vs-full field difference
(`sections` absent on list), and the member-read/editor-write rule all documented.

---

## 7. File structure

| File | Change |
|---|---|
| `app/db/models/enums.py` | add `DocumentKind`, `DocumentStatus` |
| `app/db/models/document.py` | **new** — `Document` model |
| `app/db/models/__init__.py` | register `Document` |
| `alembic/versions/00NN_documents.py` | **new** migration |
| `app/services/documents/__init__.py` | **new** package |
| `app/services/documents/template_defs.py` | **new** — `DOCUMENT_TEMPLATES` registry + `instantiate` |
| `app/services/documents/service.py` | **new** — CRUD + validate + serialize |
| `app/schemas/document.py` | **new** — `DocumentCreate`, `DocumentSave` request models |
| `app/api/v1/endpoints/documents.py` | **new** — router (7 routes) |
| `app/api/v1/api.py` | register the router |
| `app/core/errors.py` | add `DocumentVersionConflict` |
| `tests/…/documents/` | unit tests per §6 |
| `e2e/test_documents.py` (+ `e2e/_captures/documents/`) | live journey |
| `docs/fe-integration-guide-documents-templates.md` | verified guide |
| `docs/sop/2026-09-09-documents-templates-slice1.md` | SOP |
| `docs/checklist/PROJECT_CHECKLIST.md` | Module 18 section + Slice 1 items |

---

## 8. Decisions & waivers

- **D1 — Sections as a JSONB array + optimistic-concurrency `version` (full-replace PUT)**,
  mirroring canvases — not per-section rows. The AI generator writes a whole document at once and
  documents are read-mostly; granular section ops are YAGNI for v1.
- **D2 — Templates are a read-only in-code registry**, like `canvas_defs`/`roadmap templates` —
  no template table, no user-authored templates in v1.
- **D3 — `folder` is a freeform string, not a `folders` table.** The comp groups documents; a
  string label satisfies grouping without a folder-CRUD surface. A real folder entity can come
  later without breaking the column.
- **D4 — No server-side export.** The FE renders/prints the structured sections; a real
  PDF/DOCX export awaits Slice 2's URL-returning storage backend.
- **D5 — `status` is `draft`/`final` only** for v1 (the comp shows a document state; richer
  lifecycle deferred).
- **D6 — This slice builds the document store + `create_document` seam only.** Module 08's
  `business_plans` table and the AI plan generator are NOT built here — they are Module 08's
  deferred slice, gated additionally on Module 03 (AI).
