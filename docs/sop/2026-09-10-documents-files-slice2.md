# SOP — Documents & Templates, Upload & Files (Module 18, Slice 2)

**What shipped** — the second slice of Module 18: opaque binary file upload for a startup's
document library. A new `document_files` table (one row per uploaded binary — `filename`/
`content_type`/`size_bytes`/`folder`/`storage_key`/`url`, separate from the `documents` table
Slice 1 shipped) plus a `Storage` protocol extended with `delete`, backed by `LocalStorage`
(dev/test/e2e, already existed) and a new `CloudinaryStorage` implementation. Four endpoints under
`/api/v1/documents/files` (upload/list/get/delete); one migration (`0017_document_files`); no
changes to Slice 1's `documents` table or endpoints.

Commits (branch `feat/documents-files`, off `develop` @ `66ac4b0` post-PR #48 merge):
`9c3850d` (design) → `8ea847c` (implementation plan) → `14795ea` (Task 1 — `Storage.delete` +
`CloudinaryStorage`) → `ff6a55b` (Task 2 — `document_files` table + model + migration `0017`) →
`d0e20c2` (Task 3 — file service: upload/list/get/delete/serialize + allowlist) → `4ca2911`
(Task 4 — 4 HTTP endpoints + router wiring) → this task's e2e/SOP/FE-guide/checklist commit
(Task 5 — final task of the 5-task plan, `.superpowers/sdd/2026-09-10-documents-files-slice2/`).

## Why

Slice 1's SOP (`docs/sop/2026-09-09-documents-templates-slice1.md`) explicitly deferred file
uploads: "no export, no uploads, no sharing in this slice — by design... a real export needs a
URL-returning storage backend, which doesn't exist until Slice 2's Cloudinary integration." This
slice builds that storage backend and the upload surface it enables — a founder attaching a real
PDF/DOCX/image/spreadsheet to their document library (an NDA, a signed term sheet, a logo), not
just editing structured markdown `sections`. It does not attach files *to* a `Document` row — that
association (attachments-to-document) is an explicit non-goal here (see Follow-ups); this slice
ships a standalone file library, keyed only by `startup_id` and an optional freeform `folder`,
mirroring Slice 1's own folder convention.

## How

**A separate `document_files` table, not a reuse of `documents`.** The design spec considered
adding binary-file columns to the existing `Document` row and rejected it: a document's identity is
its structured `sections` (JSONB, versioned, optimistically-locked); a file's identity is its bytes
in storage plus metadata — different lifecycle (no `version`/optimistic-concurrency semantics
needed for a file — an upload simply exists until deleted), different validation (content-type
allowlist + size cap, not section-shape validation), different serialization (no summary-vs-full
split — see the FE guide). Cramming both into one table would mean nullable columns for whichever
concept isn't in use on a given row. A dedicated table keeps each concept's invariants enforceable
at the schema level.

**Cloudinary sits behind the existing `Storage` protocol (`app/platform/storage.py`), extended
with a `delete` method** (Task 1, `14795ea`) — the protocol already existed for Slice 1's design
docs to plan against, with only `LocalStorage.save` implemented; this slice is the first thing to
actually call `delete`, and the first thing to add a second implementation. `get_storage()` picks
`CloudinaryStorage` when `settings.STORAGE_BACKEND == "cloudinary"`, else `LocalStorage` — the
service layer (`app/services/documents/files.py`) never branches on backend; it only calls
`get_storage().save(...)`/`.delete(...)`. This is the same seam-behind-a-protocol pattern the
codebase already uses for email (`EMAIL_BACKEND`) and events (`event_bus`).

**`resource_type="raw"` on every Cloudinary asset, deliberately, not `"auto"`.** Cloudinary's
default `"auto"` resource-type detection routes images to its image pipeline (transforms, format
conversion) and documents to `"raw"` — which means the *same* `public_id` could collide or behave
differently depending on what Cloudinary infers about the bytes, and `destroy()` requires knowing
the resource type a `public_id` was uploaded under to delete it correctly. Fixing every upload to
`resource_type="raw"` makes `storage_key` (== Cloudinary `public_id`, exactly our own
`documents/<startup_id>/<uuid><ext>` key) sufficient on its own to both `save` and `delete` an
asset deterministically — no separate "what type was this" lookup needed before a delete, and no
accidental Cloudinary-side image transforms applied to an uploaded PDF or DOCX.

**`io.BytesIO(content)` wrapping, not raw `bytes`, when calling `cloudinary.uploader.upload`.** The
Cloudinary Python SDK's support for uploading raw `bytes` directly is version-dependent; wrapping
in a file-like `BytesIO` stream is accepted across SDK versions and costs nothing extra (the bytes
are already fully buffered in memory by the time the service layer calls `Storage.save` — see the
next paragraph).

**The endpoint buffers the entire upload into memory before validating size, in 64 KB chunks,
rather than trusting a `Content-Length` header.** `app/api/v1/endpoints/documents.py:
upload_document_file` reads `UploadFile` in a loop, accumulating `content: bytes`, and aborts with
422 the instant the running total exceeds `_MAX_FILE_BYTES` (15 MB) — this rejects a spoofed or
missing `Content-Length` (a client can send whatever header value it wants; the server must still
enforce the real cap against actual bytes received) and avoids ever holding more than
`15 MB + 64 KB` in memory for a rejected oversized upload. The allowlist check
(`file.content_type not in EXT_BY_CONTENT_TYPE`) happens **before** any bytes are read at all — a
disallowed type fails fast without wasting I/O on content the server is going to reject anyway.

**The allowlist is a `dict[content_type, extension]` (`EXT_BY_CONTENT_TYPE`,
`app/services/documents/files.py`), doing double duty** as both the upload allowlist (any
`content_type` not a key is rejected) and the storage-key extension source (`ext =
EXT_BY_CONTENT_TYPE.get(content_type, "")`) — one table instead of two lists that could drift out
of sync. Eight types allowed: PDF, the three Office Open XML formats (`.docx`/`.xlsx`/`.pptx`),
PNG, JPEG, plain text, CSV. No video, no audio, no archives, no executables — a deliberately narrow
v1 set matching what a startup's document library plausibly needs (contracts, decks, spreadsheets,
scanned signatures), not a general-purpose file host.

**Access follows the exact same member-read/editor-write split as Slice 1 and Business Builder —
no new role concept.** `GET /documents/files`, `GET /documents/files/{id}` use
`Depends(require_workspace)` (any active member); `POST`/`DELETE /documents/files` use
`Depends(_editor)` where `_editor = require_role(founder, team_member)` — the same dependency
instance Slice 1's document writes already use (`app/api/v1/endpoints/documents.py`).

**Route ordering: `/documents/files` and `/documents/files/{file_id}` are registered before
`/documents/{document_id}`** in `app/api/v1/endpoints/documents.py` — FastAPI matches routes in
registration order, and a literal path segment (`files`) must be declared ahead of a same-position
path *parameter* (`{document_id}`) or every request to `/documents/files` would be swallowed by the
`{document_id}` route with `document_id="files"` (which is not a valid UUID, so it would 422/404 in
a confusing way instead of reaching the files router at all). This is asserted directly —
`tests/api/test_document_files.py::test_files_route_not_shadowed_by_document_id`.

## What's involved

**Data model / migration**
- `alembic/versions/0017_document_files.py` — `document_files` table (`startup_id`
  FK `ON DELETE CASCADE`, `uploaded_by_id` FK `ON DELETE SET NULL`) + standalone
  `ix_document_files_startup_id`/`ix_document_files_uploaded_by_id` + composite
  `ix_document_files_startup_folder` on `(startup_id, folder)`. Chains directly off
  `0016_documents` (Slice 1's head) — sole alembic head, verified via the `scripts/e2e_run.sh` run
  below, which migrates a fresh `cofoundaz_e2e` through `0017` from zero.
- `app/db/models/document.py` — `DocumentFile` (`UUIDMixin`, `TimestampMixin`), alongside Slice 1's
  `Document` in the same module (one file per table would be inconsistent with how this codebase
  groups closely-related models — e.g. `app/db/models/membership.py` holds both `Membership` and
  invite-adjacent rows).

**Platform**
- `app/platform/storage.py` — `Storage` protocol (`save`, `delete`); `LocalStorage` (unchanged
  `save`, new `delete` — `Path.unlink(missing_ok=True)`); `CloudinaryStorage` (new — `save`/
  `delete`, both `resource_type="raw"`); `get_storage()` backend switch on
  `settings.STORAGE_BACKEND`.
- `app/core/config.py` — `STORAGE_BACKEND` (default `"local"`), `CLOUDINARY_CLOUD_NAME`/
  `CLOUDINARY_API_KEY`/`CLOUDINARY_API_SECRET` (default `""`, only read when
  `STORAGE_BACKEND == "cloudinary"`).

**Services**
- `app/services/documents/files.py` — `EXT_BY_CONTENT_TYPE` (allowlist + extension map),
  `upload_file` (builds the `documents/<startup_id>/<uuid><ext>` storage key, calls
  `get_storage().save`, inserts the row, flushes, publishes `document.file.uploaded`), `list_files`
  (startup + optional folder filter, newest-first), `get_file` (tenant-scoped, 404 on miss),
  `delete_file` (calls `get_storage().delete`, deletes the row, flushes, publishes
  `document.file.deleted`), `serialize_file` (the one-shape-fits-all serializer — no summary/full
  split, see the FE guide).

**Endpoints** (all under `/api/v1`, `app/api/v1/endpoints/documents.py`, registered ahead of the
`{document_id}` routes — see the route-ordering decision above)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/documents/files` | founder / team_member (editor) | `multipart/form-data`, part `file` + form field `folder`; 201; allowlist + 15 MB cap → 422; publishes `document.file.uploaded` |
| GET | `/api/v1/documents/files?folder=` | any active member | file summaries, newest-first |
| GET | `/api/v1/documents/files/{file_id}` | any active member | one file; cross-tenant/missing → 404 |
| DELETE | `/api/v1/documents/files/{file_id}` | founder / team_member (editor) | deletes storage object + row; publishes `document.file.deleted` |

**Errors** — no new error codes. Reuses `NotFound` (404), `VALIDATION_ERROR` (422, allowlist +
size cap — raised directly as `AppError("VALIDATION_ERROR", ..., 422)` in the endpoint, same as
Slice 1's section-shape 422), `Forbidden` (403, non-editor write).

**Events** — `document.file.uploaded` (`{startup_id, file_id, content_type}`),
`document.file.deleted` (`{startup_id, file_id}`), both published from the service layer. No
consumer yet — same enqueue-now/consume-later posture as every other event in this codebase
pre-Module-03/notifications.

**Tests**
- `tests/platform/test_storage.py` (5 tests total, 4 new in Task 1 — `LocalStorage.save` already
  had a test pre-Slice-2) — adds `LocalStorage.delete`, `CloudinaryStorage.save`/`.delete`
  (Cloudinary SDK mocked — never hits the live API in tests), `get_storage()` backend switch.
- `tests/db/test_document_file_model.py` (1 test, Task 2) — `DocumentFile` round-trips through the
  migrated table.
- `tests/services/documents/test_files.py` (5 tests, Task 3) — upload (row shape, storage key
  format, event), list (folder filter, ordering), get (found/404), delete (storage + row removed,
  event).
- `tests/api/test_document_files.py` (7 tests, Task 4) — upload-then-get-and-list, bad content-type
  → 422, oversize → 422, non-editor → 403, delete-then-404, cross-tenant `GET` → 404, the
  `/documents/files` route-shadowing regression test.
- `e2e/test_documents.py::test_documents_files_journey` (new, this task) — founder onboards →
  `POST /documents/files` (multipart, PDF, `folder: "Legal"`) → 201 → `GET
  /documents/files?folder=Legal` (summary list) → `GET /documents/files/{id}` (same shape) →
  disallowed content-type → 422 `VALIDATION_ERROR` → `DELETE` → `GET` → 404.

## Verification

- **Unit suite: 978 passed** (`poetry run pytest -q`) — 17 new tests over the 961-test baseline
  this branch inherited from `develop` after PR #48 (Slice 1) merged: 4 new in `tests/platform/
  test_storage.py` (Task 1, `LocalStorage.delete` + both `CloudinaryStorage` methods + the backend
  switch — `LocalStorage.save` already had coverage pre-Slice-2), 1 in `tests/db/
  test_document_file_model.py` (Task 2), 5 in `tests/services/documents/test_files.py` (Task 3),
  7 in `tests/api/test_document_files.py` (Task 4). 98% coverage.
- **Live E2E: 35 passed** (`scripts/e2e_run.sh`) — up from 34, +1 for this task's new
  `test_documents_files_journey`. Full run: docker db+redis up, fresh `cofoundaz_e2e` migrated
  from zero through `0017_document_files` (verified single head, sole chain off `0016_documents`),
  real uvicorn, all 35 tests green on the first run — **both write paths confirmed to persist**
  (the brief flagged "a missing `db.commit()` would surface as non-persistence"): the upload is
  visible on a subsequent `GET`/`GET ?folder=` within the same run (proves `db.commit()` ran after
  `upload_file`'s `db.flush()`), and the delete is confirmed by a following `GET` 404ing (proves
  `db.commit()` ran after `delete_file`'s `db.delete()` + `db.flush()`) — both endpoints already
  called `db.commit()`; no fix was needed.
  - `test_documents_files_journey`: founder onboards (steps 1-4 + complete) → `POST
    /documents/files` multipart (`nda.pdf`, `application/pdf`, `folder: "Legal"`) → 201, file
    summary with `size_bytes: 46`, `folder: "Legal"`, non-empty `url` → `GET
    /documents/files?folder=Legal` → one row, same `id` → `GET /documents/files/{id}` → identical
    shape → `POST` a `.exe`/`application/x-msdownload` → 422 `VALIDATION_ERROR` → `DELETE` → 200
    `{deleted: true}` → `GET` → 404.
  - 6 new captures to `e2e/_captures/documents/file_*.json` — every one is the verbatim source for
    `docs/fe-integration-guide-documents-files.md`. Confirmed live: `url` in this LocalStorage run
    is a filesystem path (`var/storage/documents/<startup_id>/<uuid>.pdf`), not an `https://` URL —
    see the FE guide's environment callout.
- `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app` —
  all clean.
- Migration round-trip verified via the `scripts/e2e_run.sh` run above (fresh `cofoundaz_e2e`
  migrated `0016_documents` → `0017_document_files` from zero); `alembic heads` confirms
  `0017_document_files` is the sole head.

## Operate / roll back

- **DEPLOY STEP (do not skip): set `STORAGE_BACKEND=cloudinary` + `CLOUDINARY_CLOUD_NAME` +
  `CLOUDINARY_API_KEY` + `CLOUDINARY_API_SECRET` in `.env.staging.enc` / `.env.production.enc`
  before this slice reaches either environment.** Without it, `get_storage()` silently falls back
  to `LocalStorage` (its default) — uploads would write to the API container's local filesystem
  (`settings.LOCAL_STORAGE_DIR`, ephemeral on every redeploy in a container-based deploy, and not
  shared across replicas), and `url` would be a local path no external client can fetch. This is a
  **silent** failure mode — there is no startup check that fails loudly if Cloudinary env vars are
  unset while `STORAGE_BACKEND=cloudinary` is NOT set; the app just uses local storage without
  complaint. Confirm the four env vars are present in the encrypted env files for both environments
  before merging this slice past `develop`.
- No new migration-adjacent env vars; `alembic upgrade head` picks up `0017_document_files`
  automatically.
- **Rollback:** `alembic downgrade -1` from `0017_document_files` drops
  `ix_document_files_startup_folder`, `ix_document_files_startup_id`,
  `ix_document_files_uploaded_by_id`, then the `document_files` table itself — **lossy**: any file
  metadata row written while this migration was applied is destroyed on downgrade (the underlying
  Cloudinary/local storage objects are **not** deleted by the migration downgrade — only the
  database rows; a full rollback of storage usage would need a separate cleanup pass against
  Cloudinary/the local disk). Rolling back the migration without also reverting the endpoint
  registration in `app/api/v1/api.py` would make all 4 file routes fail on a now-missing table —
  roll back the migration and the endpoint registration together.

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Attachments-to-document.** This slice ships a standalone file library (keyed by `startup_id` +
  freeform `folder`), not a `document_id` FK linking an uploaded file to a specific `Document` row.
  A founder attaching a signed term sheet to a specific business plan document is a plausible
  future need but was out of scope here — adding it later means a nullable `document_id` FK on
  `document_files`, not a schema rework.
- **Slice 3 — Sharing.** Share links + expiry + access levels + email delivery (needs Module 20/
  email) — applies to both `documents` (Slice 1) and `document_files` (this slice).
- **Slice 4 — E-signature.** Signature-request workflow (needs a real e-sign provider
  integration) — a natural consumer of an uploaded PDF from this slice.
- **No content scanning in v1.** Uploaded files are stored and served as-is; no malware/virus
  scanning, no PDF-content inspection, no image-content moderation. The allowlist + 15 MB cap are
  the only gates. Worth revisiting if this library is ever exposed to untrusted/public uploads —
  today every uploader is an authenticated, tenant-scoped editor.
- **No file versioning / replace-in-place.** Re-uploading "the same" file (same name) creates a new
  row with a new `id` — there is no "update this file's bytes" endpoint. Deleting the old row is a
  separate, explicit `DELETE` call.
- **No per-file access level beyond the tenant's own editor/member split.** Every file is visible
  to every active member of the startup; there's no per-file "founder only" or "shared externally"
  flag — that's Slice 3's job (share links + access levels).
