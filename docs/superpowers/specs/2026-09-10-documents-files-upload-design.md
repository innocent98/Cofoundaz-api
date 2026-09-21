# Module 18 Documents & Templates — Slice 2 Design: Upload & Files

**Date:** 2026-09-10
**Module:** 18 Documents & Templates (Slice 2 of 4)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)
**Depends on:** Slice 1 (Document Library Core, PR #48, merged to `develop`).

---

## 1. Context & Scope

Slice 1 shipped **structured documents** (`documents`: title + ordered markdown sections,
optimistic-concurrency `version`, kind/status/ai_generated/folder) + an in-code template
registry. Slice 2 adds the other half of the document library the UI shows: **opaque file
uploads** — a founder uploads a PDF pitch deck, a signed contract, a spreadsheet — stored on a
real URL-returning backend (Cloudinary) and listed in the same library grid.

The UI comp (`Documents & Templates.dc.html`) shows an **"Upload"** button beside **"New Doc"**
over one library grid: uploaded files and structured documents live in the **same library**.

### Scope (this slice)
- A **`document_files`** entity: standalone opaque files per startup, grouped by `folder`
  (the same folder concept documents use).
- **Cloudinary** as the storage backend, added **behind the existing `Storage` protocol**
  (`app/platform/storage.py`) — endpoints stay backend-agnostic. `LocalStorage` stays the
  default for local/dev/CI.
- Upload / list / get / delete endpoints, following the existing logo-upload pattern.

### Non-goals / deferred
- **Attachments** — files are standalone library items in v1, NOT attached to a specific
  `document` (no `document_id` FK). Attaching a file to a structured document is a later concern.
- **Server-side unified library feed** — the FE composes the grid from `GET /documents` +
  `GET /documents/files`; no combined UNION endpoint this slice.
- **Inline preview/transform** — we store and return a URL; rendering/preview is the FE's job.
- Sharing (Slice 3) and e-signature (Slice 4) are separate slices.
- No virus scanning / content inspection beyond MIME allowlist + size cap in v1.

---

## 2. Storage — Cloudinary behind the `Storage` seam

### 2.1 Protocol extension
`app/platform/storage.py` currently defines:

```python
class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> str: ...
```

Extend it with a `delete`, so removing a file row does not leak the stored asset:

```python
class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> str: ...
    def delete(self, key: str) -> None: ...
```

- `save(key, content, content_type) -> str` returns the **display URL** (Cloudinary: the
  `secure_url`; Local: the filesystem path, unchanged).
- `delete(key) -> None` removes the stored asset by the **key we generated** (Local: unlink
  `base_dir/key`; Cloudinary: `destroy` the corresponding asset). Best-effort but exact — the
  row persists the `storage_key` so delete targets the right asset.

`LocalStorage` gains a `delete` (unlink if present, no error if already gone).

### 2.2 `CloudinaryStorage`
- New `CloudinaryStorage` implementing `Storage`. `save` calls `cloudinary.uploader.upload(
  content, public_id=key, resource_type="auto", overwrite=True)` and returns `secure_url`;
  `delete` calls `cloudinary.uploader.destroy(key, resource_type="raw"/"image"/"auto")`.
  (`resource_type="auto"` lets Cloudinary classify images vs raw docs; the impl records what it
  needs to destroy accurately — an implementation detail behind the protocol.)
- `get_storage()` returns `CloudinaryStorage()` when `settings.STORAGE_BACKEND == "cloudinary"`,
  else `LocalStorage()` (current behavior).

### 2.3 Config & dependency
- Add the `cloudinary` SDK to `pyproject.toml` (pinned).
- Add settings (default empty so local/CI is unaffected): `CLOUDINARY_CLOUD_NAME`,
  `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` (or a single `CLOUDINARY_URL`). `CloudinaryStorage`
  configures the SDK from these.
- **Deploy dependency (out of band):** for staging/prod these secrets must be added to
  `.env.staging.enc` / `.env.production.enc` (needs the env-encryption passphrase) and
  `STORAGE_BACKEND=cloudinary` set there. Local/CI keep `STORAGE_BACKEND=local`. Tracked as a
  deploy follow-up, same class as the `JOURNAL_ENCRYPTION_KEY` step.

### 2.4 Testing the seam
- Unit + e2e run with `STORAGE_BACKEND=local` (default) — the upload endpoints are exercised
  end-to-end against `LocalStorage` (returns a path/url; `delete` unlinks). No live Cloudinary.
- `CloudinaryStorage` is unit-tested in isolation with `cloudinary.uploader.upload` and
  `.destroy` **mocked** — asserting `save` passes the right key/resource_type and returns the
  mocked `secure_url`, and `delete` calls `destroy` with the stored key. No network.

---

## 3. Data model — `document_files` (new table + migration)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin`, `server_default now()` |
| `startup_id` | UUID FK→`startups.id` CASCADE, `index=True` | tenancy |
| `uploaded_by_id` | UUID FK→`users.id` SET NULL, nullable, `index=True` | author |
| `folder` | `String(120)`, nullable | shared grouping concept with `documents` |
| `filename` | `String(255)`, NOT NULL | original filename (display) |
| `content_type` | `String(120)`, NOT NULL | MIME type |
| `size_bytes` | `Integer`, NOT NULL | byte length |
| `storage_key` | `String(512)`, NOT NULL | the key we generated (for exact delete) |
| `url` | `String(1024)`, NOT NULL | display/download URL from the backend |

**Indexes:** standalone `ix_document_files_startup_id`, `ix_document_files_uploaded_by_id`
(FK convention), plus composite `Index("ix_document_files_startup_folder", "startup_id", "folder")`.

**Migration:** `0017_document_files`, chained onto the current develop head `0016_documents`
(settle the exact prefix against the then-current head at build time; single alembic head +
zero drift required).

No new enum. No change to the `documents` table.

---

## 4. Service — `app/services/documents/files.py`

Pure functions, no `db.commit()` (endpoints commit):

- `upload_file(db, startup, *, uploaded_by_id, filename, content_type, size_bytes, content, folder) -> DocumentFile`
  — generates `storage_key = f"documents/{startup.id}/{uuid4().hex}{ext}"`, calls
  `get_storage().save(storage_key, content, content_type)` for the URL, inserts the row,
  `flush`, publishes `"document.file.uploaded"` (`{startup_id, file_id, content_type}` string
  values). (Content-type allowlist + size validation happen at the endpoint, mirroring the logo
  endpoint's streaming guard — see §5.)
- `list_files(db, startup, *, folder) -> list[DocumentFile]` — optional folder filter, newest
  first.
- `get_file(db, membership, file_id) -> DocumentFile` — scoped by `membership.startup_id`;
  `NotFound` (404) on miss/cross-tenant.
- `delete_file(db, file) -> None` — `get_storage().delete(file.storage_key)` then delete the
  row + `flush`; publishes `"document.file.deleted"`.
- `serialize_file(f) -> dict` — `{id, filename, content_type, size_bytes, folder, url,
  uploaded_at}`.

---

## 5. Endpoints — extend `app/api/v1/endpoints/documents.py`

Read = any member (`require_workspace`); write = editor (`_editor`). Declared alongside the
Slice 1 document routes (the literal `/files` sub-paths are distinct from `/documents/{id}`;
`document_id` is a typed `uuid.UUID`, so `/documents/files` cannot be captured by
`/documents/{document_id}` — but declare the `/files` routes **before** the `/{document_id}`
routes as a guard).

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `POST` | `/documents/files` | editor | multipart `file: UploadFile` + optional `folder` form field. Content-type allowlist (see below) → 422 on miss; chunked read with a 15 MB cap → 422 on oversize. `upload_file` → `db.commit()` → 201 with the serialized file. |
| `GET` | `/documents/files?folder=` | member | list files (summaries) |
| `GET` | `/documents/files/{file_id}` | member | one file's metadata + url (`uuid.UUID` path) |
| `DELETE` | `/documents/files/{file_id}` | editor | `delete_file` (asset + row) → `db.commit()` |

**Allowlist (content-type → ext):** `application/pdf` (pdf),
`application/vnd.openxmlformats-officedocument.wordprocessingml.document` (docx),
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (xlsx),
`application/vnd.openxmlformats-officedocument.presentationml.presentation` (pptx),
`image/png` (png), `image/jpeg` (jpg), `text/plain` (txt), `text/csv` (csv).
**Max size:** 15 MB, enforced by the streaming-accumulate guard (same shape as
`onboarding/logo.py`). Bad type or oversize → `AppError("VALIDATION_ERROR", …, 422)`.

`db.commit()` discipline: `POST` and `DELETE` MUST commit (get_db does not auto-commit).

---

## 6. Errors
- `VALIDATION_ERROR` (422) — disallowed content-type; file over 15 MB.
- `NotFound` (404) — unknown / cross-tenant file id.
- `Forbidden` (403) — non-editor on write (via `_editor`); non-member on any (via
  `require_workspace`, the codebase-wide convention).

---

## 7. Testing
**Unit (real Postgres; `STORAGE_BACKEND=local`):**
- Upload happy path: `POST /documents/files` with a small PDF → 201, row persisted, `url`
  present, `size_bytes` correct; file readable back via `GET`.
- Bad content-type → 422; oversize (>15 MB) → 422 (use a small cap override or a crafted body).
- List (folder filter narrows); get (full); delete removes row **and** calls `storage.delete`.
- RBAC: member can read (list/get); mentor (non-editor) → 403 on upload/delete; non-member →
  403; cross-tenant get/delete of a real other-startup file → 404.
- `CloudinaryStorage` unit test with `cloudinary.uploader.upload`/`destroy` **mocked**: `save`
  returns the mocked `secure_url` and passes the key; `delete` calls `destroy` with the key.
- `get_storage()` returns `CloudinaryStorage` when `STORAGE_BACKEND=cloudinary`, `LocalStorage`
  otherwise.

**Live e2e (`scripts/e2e_run.sh`, LocalStorage):** upload → list → get → delete round trip
against the real server; capture every request/response body. (The `POST` is multipart; capture
the JSON response bodies.)

**FE integration guide** `docs/fe-integration-guide-documents-files.md` — real captured bodies;
the multipart upload contract (field name `file`, optional `folder`), the allowlist + 15 MB cap
as returned 422s, the file summary shape, and the delete semantics. Note explicitly that in
staging/prod the `url` is a Cloudinary URL while local captures show a local path (labelled).

---

## 8. File structure
| File | Change |
|---|---|
| `pyproject.toml` | add `cloudinary` dep |
| `app/core/config.py` | `CLOUDINARY_*` settings (default empty) |
| `app/platform/storage.py` | `delete` on protocol + `LocalStorage`; new `CloudinaryStorage`; `get_storage()` switch |
| `app/db/models/document.py` | add `DocumentFile` model |
| `alembic/versions/0017_document_files.py` | new migration |
| `app/services/documents/files.py` | **new** — upload/list/get/delete/serialize |
| `app/api/v1/endpoints/documents.py` | 4 file routes (before `/{document_id}`) |
| `tests/services/documents/test_files.py`, `tests/services/documents/test_cloudinary_storage.py`, `tests/api/test_document_files.py` | unit tests |
| `e2e/test_documents.py` | append the file upload journey |
| `docs/fe-integration-guide-documents-files.md`, `docs/sop/2026-09-10-documents-files-slice2.md`, `docs/checklist/PROJECT_CHECKLIST.md` | docs |

---

## 9. Decisions & waivers
- **D1 — Separate `document_files` table**, not file-columns on `documents` and not attachments.
  Files have genuinely different fields (url/content_type/size, no sections/version) and
  lifecycle; the FE composes the one grid from two list endpoints. (User decision, 2026-09-10.)
- **D2 — Cloudinary behind the `Storage` protocol**, default `LocalStorage` for local/CI. The
  `cloudinary` SDK is mocked in unit tests; e2e runs on LocalStorage. Real Cloudinary is a
  config/secret swap for staging/prod. (Memory: Slice-2 backend = Cloudinary.)
- **D3 — `Storage.delete` added** so file deletion removes the stored asset (no leak); the row
  stores `storage_key` for an exact delete.
- **D4 — Standalone files (no `document_id`)** in v1; attach-to-document deferred.
- **D5 — Allowlist + 15 MB cap**; no content scanning in v1 (follow-up if needed).
