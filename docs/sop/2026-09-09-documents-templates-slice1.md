# SOP — Documents & Templates, Document Library Core (Module 18, Slice 1)

**What shipped** — the first slice of Module 18 (no textual PRD entry beyond the module grid;
scope recovered from the UI comp — see the design spec below): a **document store** (`documents`
table — title + ordered markdown `sections` + `kind`/`status`/`ai_generated`/`folder`/`version`
metadata, one row per structured document a startup owns) and an in-code **template registry**
(Business Plan / Pitch Deck / Financial Model / Meeting Notes / One-Pager) that instantiates a new
document's `kind`/`title`/`sections`. Seven endpoints under `/api/v1/documents` +
`/api/v1/document-templates`; one migration (`0016_documents`); no changes to any existing table
or endpoint.

Commits (branch `feat/documents-templates`, off `develop` @ `2d21d3f` post-PR #47 merge):
`9e01ad3` (design) → `2d21d3f` (implementation plan) → `8dd4c43` (Task 1 — schema: `documents`
table + `DocumentKind`/`DocumentStatus` enums + migration `0016_documents`) → `bdf6af4` (Task 2 —
in-code template registry + `instantiate`) → `e67819e` (Task 3 — document service: create/list/
get/update/delete/serialize/validate + `DocumentVersionConflict`) → `12e8981` (Task 4 — 7 HTTP
endpoints + router registration) → this task's E2E/SOP/FE-guide/checklist commit (Task 5 — final
task of the 5-task plan, `.superpowers/sdd/2026-09-09-documents-templates-slice1/`).

## Why

Module 18 has no detailed textual PRD — the backend kickoff brief only lists it in the 26-module
grid; its scope was recovered from the UI design comp (`Documents & Templates.dc.html`), which
decomposes into four slices (Library Core, Upload/Cloudinary, Sharing, E-signature). This slice's
real purpose is dual:

1. **A standalone document library** — founders need somewhere to write and organize structured
   documents (business plans, pitch decks, meeting notes) without leaving the app, seeded from a
   template so they aren't staring at a blank page.
2. **The seam Module 08's deferred AI Business Plan Generator (PRD §08.11) needs to exist before
   it can ship.** Module 08's Slice 1–3 SOPs (`docs/sop/2026-09-01-business-builder-canvas.md`,
   `docs/sop/2026-09-08-business-builder-slice3.md`) both flag §08.11 as blocked on two
   dependencies: Module 03 (AI Co-Founder, to actually draft plan content) and **Module 18, for
   the generated plan's document store/viewer** — the PRD's own CTA for a generated plan is "Open
   document", which only makes sense if a document entity already exists to open. This slice
   builds exactly that entity and its create path (`create_document(...)`), without building the
   generator itself — Module 08's `business_plans` table and its `.document_id` FK stay deferred
   until Module 03 lands (see Follow-ups).

## How

**Documents follow the same generic-table + full-replace-versioned-PUT pattern as Business
Builder's canvases (`docs/sop/2026-09-01-business-builder-canvas.md`), not a bespoke schema per
document kind.** `sections` is a single JSONB column (`[{"id", "heading", "body"}, ...]`), order in
the array *is* display order, and edits go through `PUT /documents/{id}` with a required `version`
field that must match the row's current `version` or the write is rejected with 409
`DOCUMENT_VERSION_CONFLICT` (`app/services/documents/service.py:update_document`) — the exact same
optimistic-concurrency shape as `save_canvas`. The alternative considered (recorded in the design
spec) was a normalized `document_sections` child table with its own PK/ordering column; rejected
for the same reason Business Builder's canvas `blocks` stayed JSONB — a document's sections are
read and written as one atomic unit (there is no per-section endpoint in this slice), so a JSONB
array needs no join, no ordering column, and round-trips through one `validate_sections()` call
instead of N child-row upserts.

**Templates are an in-code, read-only registry (`app/services/documents/template_defs.py`),
mirroring `app/services/business/canvas_defs.py` / `app/services/roadmap/templates.py`** — not a
database table. `DOCUMENT_TEMPLATES: dict[str, DocumentTemplate]` holds five entries (key, name,
description, kind, section headings); `instantiate(template_key)` returns `(kind, title,
sections)` with a fresh `uuid4()` assigned to each section and empty bodies, ready to hand straight
to `create_document`. The decision to keep templates in code rather than a `document_templates`
table (recorded in the design spec) was the same tradeoff Business Builder's canvas block registry
already made: v1 has no user-authored-template requirement, so a table would add a migration,
a CRUD surface, and a seeding step for zero present benefit — reversible later if custom templates
become real scope.

**`folder` is a freeform string column, not a `folders` table.** A document's `folder` is exactly
what the founder types (`"Investor Docs"`, `"Pitches"`, ...) — no separate folder entity to create,
rename, or delete; `GET /documents?folder=...` is a plain equality filter. This keeps the surface
dependency-free for this slice; a real folder-management UI (rename-cascades, nesting) is out of
scope until there's a concrete need for it.

**No export, no uploads, no sharing in this slice — by design, not oversight.** The design spec's
non-goals for Slice 1 are explicit: no server-side PDF/DOCX export (the FE renders/prints the
structured sections it already has; a real export needs a URL-returning storage backend, which
doesn't exist until Slice 2's Cloudinary integration), no file uploads (Slice 2), no sharing/share
links (Slice 3, needs Module 20/email), no e-signature (Slice 4). Building any of those now would
be scope creep against a slice whose entire job is the document store + template seam.

**Access follows the exact same member-read/editor-write split as Business Builder, reusing the
same `require_workspace`/`require_role` dependencies — no new role concept.** `GET
/documents`/`GET /documents/{id}`/`GET /document-templates*` use `Depends(require_workspace)` (any
active member); `POST`/`PUT`/`DELETE /documents` use `Depends(_editor)` where `_editor =
require_role(founder, team_member)` (`app/api/v1/endpoints/documents.py:30`) — identical to
Business Builder's `_editor` gate.

**Unknown `?kind=`/`?status=` filter values 404, matching Slice 3's `?status=` precedent on
Business Builder's suggestions endpoint**, rather than 422 or silently returning an empty list —
an unrecognized filter value is treated as "no such view exists" (`_parse_kind`/`_parse_status` in
`app/api/v1/endpoints/documents.py`), consistent with how `GET /suggestions?status=` behaves.

## What's involved

**Data model / migration**
- `alembic/versions/0016_documents.py` — `documents` table (+ `ix_documents_startup_id`,
  `ix_documents_created_by_id`, composite `ix_documents_startup_kind` on `(startup_id, kind)`).
  `startup_id` FK `ON DELETE CASCADE`; `created_by_id` FK `ON DELETE SET NULL` (the document
  survives its author's account being removed). `kind`/`status` are `native_enum=False` VARCHAR(20),
  matching this codebase's StrEnum-column convention. Chains directly off
  `0015_business_positioning_maps` (Slice 3's head at the time this slice was built) — sole
  alembic head, verified via the `scripts/e2e_run.sh` run below, which migrates a fresh
  `cofoundaz_e2e` through `0016` from zero.
- `app/db/models/document.py` — `Document` (`UUIDMixin`, `TimestampMixin`).
- `app/db/models/enums.py` — `DocumentKind` (`business_plan`/`pitch_deck`/`financial_model`/
  `meeting_notes`/`one_pager`/`custom`), `DocumentStatus` (`draft`/`final`).

**Services**
- `app/services/documents/template_defs.py` — `DOCUMENT_TEMPLATES` (5 entries), `instantiate()`,
  `catalog()`, `template_view()`.
- `app/services/documents/service.py` — `validate_sections` (shape check + section-id assignment,
  422 `field_errors` on bad shape), `create_document` (the Module 08 seam — flushes, publishes
  `document.created`), `list_documents` (kind/folder/status filters, newest-updated first),
  `get_document` (tenant-scoped, 404 on miss), `update_document` (version check → 409
  `DocumentVersionConflict`, else full-replace + version bump), `delete_document`,
  `serialize_summary`/`serialize_document` (the summary-vs-full split — see FE guide §5).
- `app/schemas/document.py` — `DocumentCreate` (`{kind?, title?, folder?, template_key?,
  sections?}`, all optional), `DocumentSave` (`{title, sections, status, folder, version}`,
  `version` required — full-replace).

**Endpoints** (all under `/api/v1`, `app/api/v1/endpoints/documents.py`; router registered with
**no prefix** in `app/api/v1/api.py` — deliberate, since this module owns two path roots,
`/documents` and `/document-templates`, not one)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/document-templates` | any active member | in-code registry catalog |
| GET | `/api/v1/document-templates/{key}` | any active member | one template; unknown key → 404 |
| GET | `/api/v1/documents?kind=&folder=&status=` | any active member | **summaries only — no `sections` key** (see FE guide §5); unknown `kind`/`status` → 404 |
| POST | `/api/v1/documents` | founder / team_member (editor) | 201; `template_key` seeds `kind`/`title`/`sections`, else explicit `kind`/`title`/`sections` or blank defaults; publishes `document.created` |
| GET | `/api/v1/documents/{id}` | any active member | full document, **with `sections`**; cross-tenant/missing → 404 |
| PUT | `/api/v1/documents/{id}` | founder / team_member (editor) | full-replace `{title, sections, status, folder, version}`; stale `version` → 409 `DOCUMENT_VERSION_CONFLICT` |
| DELETE | `/api/v1/documents/{id}` | founder / team_member (editor) | `{deleted: true}` |

**Errors** — one new code (`DocumentVersionConflict`, `app/core/errors.py:123-125`, 409
`DOCUMENT_VERSION_CONFLICT`). Reuses `NotFound` (404), `VALIDATION_ERROR` (422, bad section
shape), `Forbidden` (403, non-editor write) — no other new error codes.

**Events** — `document.created` (`{startup_id, document_id, kind}`), published from
`create_document`. No consumer yet — same enqueue-now/consume-later posture as every other event
in this codebase pre-Module-03/notifications.

**Tests**
- `tests/services/documents/test_template_defs.py` (4 tests) — catalog shape, `instantiate` per
  template (section count/ids/empty bodies), unknown key → 404.
- `tests/services/documents/test_service.py` (11 tests) — create (blank/template/explicit
  sections), bad section shape → 422, list filters + summary serialization, get (found/404),
  update (full-replace + version bump, stale version → 409, row-not-mutated-on-conflict), delete,
  `document.created` event, the `ai_generated=True` seam call shape Module 08 will use.
- `tests/api/test_documents.py` (9 tests) — the 7 endpoints over HTTP, RBAC (`mentor` → 403 on
  write, member can still read), cross-tenant 404 (both unknown-id and a real document under a
  different startup), unknown `?kind=` → 404, version-conflict round trip via HTTP.
- `e2e/test_documents.py::test_documents_journey` (new, this task) — templates catalog + one
  template detail → create from `business_plan` template (201, kind/title/sections seeded,
  version 1) → get full document (`sections` present) → full-replace `PUT` (version 1→2, status
  draft→final, folder assigned) → stale `PUT` at `version:1` → 409
  `DOCUMENT_VERSION_CONFLICT` (row confirmed unmutated on re-GET) → `GET
  /documents?folder=...` (summary list, `sections` key absent) → `DELETE` → `GET` → 404.

## Verification

- **Unit suite: 961 passed** (`poetry run pytest`) — 24 of those are Slice-1-specific (`tests/
  services/documents/test_template_defs.py`, `test_service.py`, `tests/api/test_documents.py`),
  on top of the baseline this branch inherited from `develop` after PR #47 (Slice 3) merged.
  98% coverage.
- **Live E2E: 34 passed** (`scripts/e2e_run.sh`) — up from 33, +1 for this task's new journey.
  Full run: docker db+redis up, fresh `cofoundaz_e2e` migrated from zero through
  `0016_documents` (verified single head, sole chain off `0015_business_positioning_maps`), real
  uvicorn, all 34 tests green on the first run — **no server-only defect was found** (the brief
  flagged "a missing `db.commit()`" as a plausible failure mode; all three write endpoints in
  `documents.py` were checked and already call `db.commit()` after their service call — no fix
  was needed).
  - `test_documents_journey`: founder onboards (steps 1-4 + complete, no roadmap/assessment
    dependency, same as `e2e/test_business_builder.py`) → `GET /document-templates` confirms
    `business_plan`/`one_pager` in the catalog → `GET /document-templates/business_plan` confirms
    the first heading is "Executive Summary" → `POST /documents {"template_key":
    "business_plan"}` → 201, `kind: business_plan`, `title: "Business Plan"`, `version: 1`,
    9 sections seeded → `GET /documents/{id}` confirms `sections` present → `PUT` full-replace
    (new title, 2 sections, `status: final`, `folder: "Investor Docs"`, `version: 1`) → 200,
    `version: 2` → re-`PUT` at stale `version: 1` → 409 `DOCUMENT_VERSION_CONFLICT`, re-GET
    confirms the row was not partially mutated (`version` still 2, `status` still `final`) →
    `GET /documents?folder=Investor+Docs` → one summary row, `sections` key absent → `DELETE` →
    `GET` → 404.
  - 9 new captures to `e2e/_captures/documents/*.json` — every one is the verbatim source for
    `docs/fe-integration-guide-documents-templates.md`.
- `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app` —
  all clean.
- Migration round-trip verified via the `scripts/e2e_run.sh` run above (fresh `cofoundaz_e2e`
  migrated `0015_business_positioning_maps` → `0016_documents` from zero); `alembic heads`
  confirms `0016_documents` is the sole head.

## Operate / roll back

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `scripts/e2e_run.sh`
  flow.
- **Rollback:** `alembic downgrade -1` from `0016_documents` drops
  `ix_documents_startup_kind`, `ix_documents_startup_id`, `ix_documents_created_by_id`, then the
  `documents` table itself — **lossy**: any document written while this migration was applied is
  destroyed on downgrade, same as every other brand-new-table migration in this project. Rolling
  back the migration without also reverting the endpoint registration in `app/api/v1/api.py`
  would make all 7 routes fail on a now-missing table — roll back the migration and the endpoint
  registration together, or accept that the `/documents`/`/document-templates` surface starts
  raising until the code is reverted too.

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Slice 2 — Upload & Files (Cloudinary).** Opaque binary file upload via Cloudinary as a
  URL-returning `Storage` implementation — attaching a PDF/image to a document, not just
  structured markdown sections.
- **Slice 3 — Sharing.** Share links + expiry + access levels + email delivery (needs Module 20 /
  email infrastructure).
- **Slice 4 — E-signature.** Signature-request workflow (needs a real e-sign provider
  integration).
- **`business_plans.document_id` FK is not wired yet.** Module 08's AI Business Plan Generator
  (PRD §08.11) still cannot ship — it needs Module 03 (AI Co-Founder) for actual plan-section
  generation, which has not started. When that generator is built, it will call
  `create_document(db, startup, created_by_id=..., kind=DocumentKind.business_plan, title=...,
  sections=..., folder=None, template_key=None, ai_generated=True)` — the exact seam this slice
  shipped and tested — and Module 08's `business_plans` table will get a `document_id` FK to
  `documents.id`. **This slice does not build `business_plans` or the generator itself**, only the
  document store + create seam it depends on.
- **No server-side export (PDF/DOCX).** The FE renders/prints the structured `sections` it
  already has; a real export awaits Slice 2's URL-returning storage backend.
- **No per-section endpoints.** Editing is always full-replace `PUT /documents/{id}` with the
  whole `sections` array; if a future need arises for granular section-level ops (e.g.
  collaborative editing, comments per section), that would be a new addition, not a change to
  this slice's contract.
- **No `folders` table / folder management UI.** `folder` is a plain string column; renaming a
  folder today means editing every document's `folder` field individually — fine for a first
  version, worth revisiting if founders accumulate many documents per folder.
- **No user-authored templates.** The registry is read-only and in-code; if founders want to save
  their own reusable templates, that needs a real `document_templates` table and CRUD surface —
  deferred until there's a concrete request for it.
