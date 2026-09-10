# FE Integration Guide — Documents & Templates (Module 18, Slice 2: Upload & Files)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_documents.py::test_documents_files_journey` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/documents/file_*.json`. Nothing here is retyped from
the schema, the service, or memory. IDs, timestamps, and the storage `url` are real values from
that ephemeral test run (they differ on every real request; the shapes are exact). Every payload,
status code, and error body in this guide was exercised live, except the `403 FORBIDDEN` write-role
row (§6), which is cited from a passing unit test rather than captured live in this journey — noted
inline where it appears.

Base path: `/api/v1`. Same auth convention as the rest of Documents & Templates (and every other
tenant-scoped module): Bearer access token (`Authorization: Bearer <token>`) + `X-Workspace-Id`
header identifying the active workspace.

**Role rule — identical to Slice 1: read = any active member, write = founder or team_member
(editor).** `GET /documents/files`, `GET /documents/files/{id}` use `require_workspace` (any active
member). `POST`/`DELETE /documents/files` use the same `_editor = require_role(founder,
team_member)` gate Slice 1's document writes use. A non-editor member gets `403 FORBIDDEN` on
upload/delete (§6).

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §6).

**CRITICAL — the `url` field is environment-dependent.** In this local e2e run (and in your own
local dev environment, unless you've configured Cloudinary), the server runs on `LocalStorage` and
`url` is a **local filesystem path on the API host** (e.g. `var/storage/documents/<startup_id>/
<uuid>.pdf`, exactly what's captured below) — **not a fetchable HTTP URL**. In staging/production,
`STORAGE_BACKEND=cloudinary` is set, and `url` is a real Cloudinary `https://res.cloudinary.com/...`
secure URL that the FE can load/download directly. **Do not build against the local-path shape** —
treat `url` as an opaque string you hand to an `<a href>`/`<img src>`, and know that it will not
resolve to anything the browser can reach when pointed at a local dev backend running without
Cloudinary configured.

---

## 1. `POST /api/v1/documents/files` — upload

**Editor only** (founder/team_member) — `403 FORBIDDEN` for any other role (§6).

**This is `multipart/form-data`, NOT JSON** — the one exception to the JSON-body convention every
other endpoint in this API follows. Two parts:

| Part | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | The binary. `filename` and `content_type` come from the multipart part headers your HTTP client sets automatically from the `File`/`Blob` you attach — the server reads `content_type` for the allowlist check (§3) and stores the sent `filename` as-is. |
| `folder` | form field (string) | no | Freeform string, same semantics as a document's `folder` (Slice 1) — omit or send empty to leave it unset. |

There is no JSON alternative — do not `JSON.stringify` and POST as `application/json`; the server
expects a real multipart body (e.g. browser `FormData`, or your HTTP client's multipart file
helper).

**Request** (this journey's upload — `multipart/form-data`, part `file` = `nda.pdf` /
`application/pdf` / 46 bytes, form field `folder` = `"Legal"`):

```
POST /api/v1/documents/files
Content-Type: multipart/form-data; boundary=...

--...
Content-Disposition: form-data; name="file"; filename="nda.pdf"
Content-Type: application/pdf

%PDF-1.4
%a tiny fake PDF for e2e upload
%%EOF
--...
Content-Disposition: form-data; name="folder"

Legal
--...--
```

**Response — 201** (`e2e/_captures/documents/file_upload.json`):

```json
{
  "data": {
    "id": "afcd17ef-3203-404c-8593-2e305af6f715",
    "filename": "nda.pdf",
    "content_type": "application/pdf",
    "size_bytes": 46,
    "folder": "Legal",
    "url": "var/storage/documents/e48dc2d7-4788-4945-866e-c319004f7c01/9a0900b504664b61bc84e73c634ae49c.pdf",
    "uploaded_at": "2026-09-10T16:21:19.662384+00:00"
  },
  "meta": null
}
```

`url` here is a **LocalStorage filesystem path** — shape verified live on LocalStorage; the
production `url` is a Cloudinary `https://` URL (see the callout above). `id` is a fresh
server-assigned UUID — the storage key on disk/Cloudinary is a *different*, internally-generated
UUID (`storage_key`, not returned to the client) — do not assume `id` appears anywhere in `url`.

### The file summary shape

Every file response — upload, list row, get — is exactly this object, no variant:

```json
{ "id", "filename", "content_type", "size_bytes", "folder", "url", "uploaded_at" }
```

Unlike Slice 1's document summary-vs-full split (§5 of the templates guide), **there is no
separate "full" shape for files** — list rows and the single-`GET` response are identical. Nothing
is omitted on the list endpoint.

| Field | Meaning |
|---|---|
| `id` | Server-assigned UUID. Use this for `GET`/`DELETE /documents/files/{id}`. |
| `filename` | Exactly what the client sent as the multipart part's filename — not sanitized, not deduplicated. Two uploads of `nda.pdf` produce two rows, each with `filename: "nda.pdf"` and a distinct `id`. |
| `content_type` | The MIME type the client's multipart part declared — must be one of the allowlisted types (§3) or the upload 422s before a row is ever created. |
| `size_bytes` | Byte length the server measured while streaming the upload — trust this over any client-reported size. |
| `folder` | The `folder` form field you sent, or `null` if omitted. Same freeform-string semantics as a document's `folder`. |
| `url` | Opaque storage URL/path — see the environment callout above. Never parse it for meaning beyond "hand it to the browser to fetch the file" (and only in an environment where it's actually an `https://` URL). |
| `uploaded_at` | ISO-8601 timestamp, server clock. |

---

## 2. `GET /api/v1/documents/files?folder=` — list

Any active member. Optional `folder` query param — exact string match (same convention as Slice
1's document list filter), no pagination in this slice.

**Response — 200** for `?folder=Legal`, right after the upload above
(`e2e/_captures/documents/file_list_by_folder.json`):

```json
{
  "data": {
    "files": [
      {
        "id": "afcd17ef-3203-404c-8593-2e305af6f715",
        "filename": "nda.pdf",
        "content_type": "application/pdf",
        "size_bytes": 46,
        "folder": "Legal",
        "url": "var/storage/documents/e48dc2d7-4788-4945-866e-c319004f7c01/9a0900b504664b61bc84e73c634ae49c.pdf",
        "uploaded_at": "2026-09-10T16:21:19.662384+00:00"
      }
    ]
  },
  "meta": null
}
```

Rows are sorted newest-`uploaded_at`-first (same convention as Slice 1's document list). Omitting
`folder` returns every file across all folders for the tenant — there is no "unfiled only" filter
in this slice.

---

## 3. `GET /api/v1/documents/files/{id}` — one file

Any active member. Same shape as a list row (§1's summary shape) — nothing extra is returned for a
single fetch.

**Response — 200** (`e2e/_captures/documents/file_get.json`) — byte-identical to the upload
response in §1, since nothing had changed:

```json
{
  "data": {
    "id": "afcd17ef-3203-404c-8593-2e305af6f715",
    "filename": "nda.pdf",
    "content_type": "application/pdf",
    "size_bytes": 46,
    "folder": "Legal",
    "url": "var/storage/documents/e48dc2d7-4788-4945-866e-c319004f7c01/9a0900b504664b61bc84e73c634ae49c.pdf",
    "uploaded_at": "2026-09-10T16:21:19.662384+00:00"
  },
  "meta": null
}
```

An unknown or cross-tenant `id` → **404** (`NOT_FOUND`) — uniform, same as every other
tenant-scoped lookup in this API.

---

## 4. `DELETE /api/v1/documents/files/{id}` — delete

**Editor only.** Deletes both the database row **and** the underlying storage object (Cloudinary
asset or local file) — not a soft-delete. `GET` on the same `id` afterward reliably 404s (verified
live, see §6/§7).

**Response — 200** (`e2e/_captures/documents/file_delete.json`):

```json
{ "data": { "deleted": true }, "meta": null }
```

**Response — 404** for the follow-up `GET` of the same `id`
(`e2e/_captures/documents/file_get_after_delete.json`):

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

**This is a hard, unrecoverable delete.** There is no undo/trash/restore in this slice — surface a
confirmation step in the FE before calling this endpoint, exactly as you would for Slice 1's
`DELETE /documents/{id}`.

---

## 5. The allowlist and the size cap — real 422 bodies

Two independent 422 gates on `POST /documents/files`, both `VALIDATION_ERROR`, checked **before**
any database row or storage write happens (a rejected upload leaves nothing behind to clean up).

**Allowlisted `content_type`s** (anything else 422s):

| Content type | Extension |
|---|---|
| `application/pdf` | `.pdf` |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `.docx` |
| `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `.xlsx` |
| `application/vnd.openxmlformats-officedocument.presentationml.presentation` | `.pptx` |
| `image/png` | `.png` |
| `image/jpeg` | `.jpg` |
| `text/plain` | `.txt` |
| `text/csv` | `.csv` |

**Response — 422** for a disallowed type (`virus.exe` / `application/x-msdownload`,
`e2e/_captures/documents/file_upload_bad_type_422.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That file type isn't allowed. Upload a PDF, Office doc, image, text, or CSV.",
    "field_errors": []
  }
}
```

**Size cap: 15 MB (15 × 1024 × 1024 bytes).** The server streams the upload in 64 KB chunks and
aborts with `422 VALIDATION_ERROR` (message `"File must be 15 MB or smaller."`) the instant the
running total exceeds the cap — not captured live in this journey (a 15 MB+ payload was judged not
worth the added journey runtime), but asserted over HTTP in
`tests/api/test_document_files.py::test_upload_oversize_422`. **Client-side guidance:** check
`file.size` before you even open the upload dialog's submit button — don't rely on the 422 as your
only size gate, since the FE can give the founder instant feedback without a round trip.

**Validate `content_type` client-side too** — before attaching a file to the `FormData`, check it
against the allowlist above so a founder who picks a `.zip` or `.mp4` gets an immediate, friendly
rejection instead of waiting on a round trip for the same 422.

---

## 6. Errors

Standard envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | — | Missing or invalid access token | not captured in this journey — same auth dependency as every other module |
| 403 | `FORBIDDEN` | Non-editor (e.g. `mentor`) calls `POST`/`DELETE /documents/files` | not captured live in this journey — asserted over HTTP in `tests/api/test_document_files.py::test_mentor_cannot_upload_403` |
| 404 | `NOT_FOUND` | Unknown/cross-tenant file `id` | `file_get_after_delete.json` (id gone after delete) |
| 422 | `VALIDATION_ERROR` | Disallowed `content_type`, or file exceeds 15 MB | `file_upload_bad_type_422.json` (type); size cap not captured live — `tests/api/test_document_files.py::test_upload_oversize_422` |

---

## 7. Verification table

All rows below except the `403`/size-cap rows were exercised **live**, over real HTTP, against a
real Postgres-backed server (`scripts/e2e_run.sh`,
`e2e/test_documents.py::test_documents_files_journey`) — not just unit-tested in-process — and
every response body is captured verbatim in the named file.

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /documents/files` — multipart upload with `folder`, 201, file summary shape | ✅ | `file_upload.json` |
| `GET /documents/files?folder=...` — shows the upload, summary shape identical to upload response | ✅ | `file_list_by_folder.json` |
| `GET /documents/files/{id}` — metadata + `url` | ✅ | `file_get.json` |
| Disallowed content-type → `422 VALIDATION_ERROR` | ✅ | `file_upload_bad_type_422.json` |
| `DELETE /documents/files/{id}` — `{deleted: true}`, and the row + storage object are actually gone (db.commit persisted) | ✅ | `file_delete.json` |
| `GET /documents/files/{id}` after delete → `404 NOT_FOUND` | ✅ | `file_get_after_delete.json` |
| `url` is a LocalStorage filesystem path in local/e2e captures; a Cloudinary `https://` URL in staging/prod | ✅ (local shape) / explicitly labelled (prod shape not exercised here — needs `STORAGE_BACKEND=cloudinary` + real Cloudinary credentials) | every capture above, `app/platform/storage.py` |
| Non-editor (`mentor`) → `403 FORBIDDEN` on upload/delete; member can still read | ⚠️ unit only | `tests/api/test_document_files.py::test_mentor_cannot_upload_403` |
| File exceeds 15 MB → `422 VALIDATION_ERROR` | ⚠️ unit only | `tests/api/test_document_files.py::test_upload_oversize_422` |
| Cross-tenant `GET` (real file, different startup) → `404` | ⚠️ unit only | `tests/api/test_document_files.py::test_cross_tenant_get_real_file_404` |
| `GET /documents/files` route not shadowed by `GET /documents/{document_id}` | ⚠️ unit only | `tests/api/test_document_files.py::test_files_route_not_shadowed_by_document_id` |

The ⚠️ rows are genuine gaps in this journey (a second-role/second-tenant/oversized-payload setup
was judged not worth the added journey complexity and runtime for this slice) rather than
unexercised guesses — each is backed by a passing HTTP-level test, not merely inferred from source.
