# SOP — Documents BE-C: templates, generator and export

**What shipped** — export of any rich document to a stored **PDF or DOCX** file; a **template generator**
(`POST /documents/templates/{key}/instantiate`) that pre-fills from the startup profile and Business
Builder, reports field state, and can queue an **AI section draft** through the metered worker path;
the PRD's four template **categories** and four **legal drafts** (NDA, contractor agreement, founder
agreement, SAFE) with a not-legal-advice disclaimer. Migration `0074_documents_templates_export` (the only
one). New dependency `python-docx ^1.2.0` (pulls `lxml`). Branch `feature/documents-templates-export`
→ `develop` (PR in the hand-back). Closes gap-list rows 12, 13 and 18.

## Why

The 2026-10-24 walk found two models that never met: a business plan could be shared but not exported or
sent for signature (12); "Use template" made a blank document with no auto-fill, no field form and no job
(13); and the gallery had five templates, none legal, under the wrong categories (18). D-DOC-4: ship
structured legal drafts with a clear banner; a lawyer replaces the text later.

## How

| Piece | Decision |
|---|---|
| Export is a **file** | An export is an ordinary private `DocumentFile` (same storage, scan gate, signed 5-minute links, Trash, 15 MB ceiling) with `source_document_id` + `source_version`. That is all it takes for the existing file features (send for signature) to apply. Rejected: a separate `exports` table (a second file kind everywhere downstream). |
| Idempotent per version | The document row is locked (`get_document(lock=True)`); the same `(document, version, format)` returns the live file already made (200), a new version makes a new file (201). Stops double-clicks and retries filling storage. A trashed export is not reused. |
| The file outlives its document | FK is `ON DELETE SET NULL`; `origin` is derived from `source_version IS NOT NULL`, so a purged source still reads as an export. |
| Renderers | PDF: reportlab platypus (already a dependency), Type 1 Helvetica, text always XML-escaped so a body can never inject markup. Chosen over a TrueType font because the standard font keeps files small and the page text readable to tests; the cost is that characters outside Latin-1 are reduced to their base letter in the PDF (documented in the FE guide). DOCX: `python-docx`; control characters stripped (XML-invalid). Bodies are plain text: blank line = paragraph, `- ` = bullet. |
| Disclaimer printed | `disclaimer_for(template_key, kind)`: boxed notice on page 1 + footer line on every PDF page; paragraph under the title + footer in DOCX. |
| Generator | `generator.py`. Auto-fill specs are strings (`profile.name`, `canvas.lean.problem`, `record.revenue_stream`, `user.full_name`, `today`) so a template stays declarative. A section is `filled` or `empty`, with a `source` (`profile` / `business_builder` / `account` / `system` / `template` / `you`) and `ai_draftable`. `GET /document-templates/{key}` returns this as `form` (nothing created) so the FE can render chips before "Create document"; `instantiate` returns `{document, form, ai}`. |
| Legal text | Fixed clauses with `{{field}}` placeholders, rendered in **one regex pass** (a value that looks like a placeholder is inserted literally). A missing value is a visible `[Label]`, never a silent blank. No AI on legal clauses. The API does not refuse a document with required fields empty: the FE blocks/warns (the form returns `unfilled_required`). |
| AI draft job | `ai.documents.template_draft` (the `ai.` prefix keeps it in the AI-enrichment accounting). Worker `app/worker/handlers/documents_ai.py` mirrors the plan-section handler: budget checked before every call and every call debited; **never overwrites text** (sections filled meanwhile are `skipped`, checked before the call and again under the row lock); provider errors re-raise until the last attempt, then drafted sections are saved and the rest reported `failed`; outcome always in `job.result` (`ready / partial / failed / over_budget / gone`). Writing bumps the version with an author-less snapshot "AI drafted: …" and sets `ai_generated`. |
| Over budget on create | The document is created anyway; `ai.status: "over_budget"`, no job. The retry route (`POST /documents/{id}/ai-draft`) refuses with `429 AI_BUDGET_EXHAUSTED`. A founder should not lose a usable auto-filled draft because the AI allowance is spent. |
| Categories | `Legal / Financial / Pitch / Operational`. Business plan, pitch deck and one-pager sit under **Pitch** (their use is fundraising); financial model under Financial; meeting notes under Operational. Easy to move: one string per template. |

## What's involved

`alembic/versions/0074_documents_templates_export.py` · `app/db/models/document.py` (`DocumentFile.source_*`) ·
`app/db/models/enums.py` (4 legal `DocumentKind`s, no DDL: `documents.kind` is a plain VARCHAR(20)) ·
`app/services/documents/{template_defs,generator,export,draft_prompt}.py` · `app/worker/handlers/documents_ai.py`
(+ `app/worker/__main__.py` registration) · `app/api/v1/endpoints/documents.py` · `app/schemas/document.py` ·
`app/core/errors.py` (`AiBudgetExhausted`, 429) · `app/services/documents/{files,service}.py` (serializers:
`type`, `origin`, `source_*`, `disclaimer`; `disclaimer` also on the public token view) · `pyproject.toml` +
`poetry.lock`.

New routes (all founder + team member): `GET /document-templates/{key}` now returns `form`;
`POST /documents/templates/{key}/instantiate` (201); `POST /documents/{id}/export` (201 / 200);
`POST /documents/{id}/ai-draft` (202). `POST /documents` with `template_key` now renders legal drafts.
Rate limits: export `20/minute`, re-draft `20/hour`, instantiate `60/hour` (`shared_limit`). Export caps: 2000 lines / 200,000 characters.

## Verification

- `tests/api/test_documents_export.py`, `tests/api/test_documents_templates.py`,
  `tests/services/test_documents_export_render.py`, `tests/test_documents_export_migration.py`, the route
  matrix in `tests/api/test_documents_access.py` (the three new routes × every role), worker registration in
  `tests/worker/test_entrypoint.py`. Roles: founder and team member 200/201; mentor, accountant, legal advisor,
  business consultant, investor 403 and nothing written; trashed and unknown and cross-workspace documents 404.
- Live: `e2e/test_documents_templates_export.py` (captures in `e2e/_captures/documents_templates_export/`):
  pre-fill from a real canvas + record, AI job drained in-process with the stub model, NDA with values,
  PDF and DOCX export, re-export 200, download bytes, DOCX text, send an export for signature, mentor 403,
  trashed 404.
- CI parity run locally on scratch databases (black, isort, ruff, mypy, pytest `--cov-fail-under=95`, alembic
  single head / upgrade / check / downgrade base / upgrade, e2e, security scanners) — results in the PR.
- One Opus review on file generation, storage, access and the AI job — findings below.

## Opus review

| # | Finding | Resolution |
|---|---|---|
| 1 | High: render time super-linear in line/bullet count, run inside the request while the document row is locked (8k lines = 28 s) | Hard caps: 2000 lines and 200,000 characters (422), one flowable per line; tested |
| 2 | DOCX export 500 on XML-forbidden characters (U+FFFE/FFFF) in a body or title | `_clean` strips everything outside the XML 1.0 range |
| 3 | Local download 500 for a non-Latin-1 filename (existing `Content-Disposition` code) | ASCII `filename=` plus RFC 5987 `filename*=UTF-8''…` |
| 4 | Retries re-spent tokens and failed calls were never debited | Draft calls go through `metered_complete` (failed calls billed on their own session). Re-drafting after a retry still re-pays sections finished in the rolled-back attempt (follow-up) |
| 5 | Rename/move did not bump the version, so re-export returned the stale file | Reuse also requires the same filename and folder |
| 6 | Instantiate with AI bypassed the re-draft rate limit; two re-drafts could both run | `60/hour` on instantiate; `409 AI_DRAFT_IN_PROGRESS` while a draft job for the document is queued/running |
| 7 | Re-draft queued sections whose heading the user deleted, and the job reported `ready` | Only headings present count as empty |
| – | Nothing found | Access/tenancy, disclaimer bypass, placeholder injection, markup injection, enum/migration |

## Operate / roll back

**Deploy:** `docker compose build api worker` (a new Python dependency, `python-docx`, so the images must be
rebuilt, not just restarted), then `alembic upgrade head`, restart api + worker. The worker must run the new
code or the `ai.documents.template_draft` job type has no handler. No new env vars. **Roll back:**
`alembic downgrade 0073_documents_library_core` drops the two columns (exports become plain files; their
objects stay in storage and in the library). Rolling back only the code leaves the columns harmless; jobs of
the new type already queued would fail with "no handler" and can be ignored.

## Follow-ups

- **Sharing a file** (row 12's other half): files still cannot be shared (token link or grant); only documents can.
- A real **TrueType font** if PDFs must show Yoruba dot-below vowels or non-Latin scripts (embed a font; PDFs grow).
- Counsel-reviewed **clause text** (D-DOC-4): replace the strings in `template_defs.py`; the API does not change.
- Executed-PDF stamping and signing a DOCX export are BE-D.
- A retried AI job re-drafts sections finished in a rolled-back attempt (per-section progress is not persisted).
- Export has no per-section page-break or cover page; headings only.
- Version-aware export history ("exports of this document") is derivable from `source_document_id` but has no route yet.
