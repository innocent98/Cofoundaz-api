# SOP — Documents BE-D: e-sign v2 and link shares

**What shipped** — completion now produces an **executed PDF** (original pages untouched + a signature page with every typed
signature + an audit certificate), stored privately, downloadable by editors and by each signer, and **emailed to every
party**; a **signing order** switch (sequential release), a **message** to signers, a **Viewed** state, a full **request
detail** (sent date, expiry, per-signer email / status / viewed / signed times, `signed_ip`, `signed_user_agent`), **copy a
signer's link again**, and **"anyone with the link" shares with a custom expiry** for rich documents *and* uploaded files
(list, change expiry, revoke, copy again). Migrations `0075_documents_esign_v2` and (review fix, PR after #190) `0076_documents_signer_link_audit` (`signature_signers.link_copies`). New dependency `pypdf ^5`.
Branch `feature/documents-esign-v2` → `develop` (PR in the hand-back). Closes gap-list rows 15, 16 (minus field placement),
17, 31, 37. FE guide: `docs/fe-integration-guide-documents-esign-v2.md` (live-captured).

## Why

The 2026-10-24 walk: completion produced nothing (15); the send flow had no order, message or Viewed state (16); the share
modal was a one-shot link maker with a fixed 30-day expiry and no files (17); the request list had no detail (31); audit
fields were captured but never returned (37). D-DOC-2: simple typed-name e-sign, a stamped executed PDF and an audit
certificate emailed to all parties, a legal caveat, no provider.

## How — decisions and rejected alternatives

| Topic | Decision |
|---|---|
| **Field placement** | **Left out.** The gap row ties it to the PDF viewer (row 11, FE) and to Q2 (provider level); neither decided it. Signatures are stamped on an appended **signature page** next to the certificate. Follow-up: needs a viewer + a field model (`signature_fields`: page, x, y, signer) and an overlay stamp in `esign_pdf.build_executed_pdf`. |
| **DOCX signing** | **Require PDF.** `POST .../signature-requests` answers `422 PDF_REQUIRED` for any non-PDF, with "Export the document to PDF first". A rich document is signable via the BE-C export (`format: pdf`), an uploaded DOCX needs converting by the user. Rejected: server-side DOCX→PDF (needs LibreOffice in the image, and layout fidelity we cannot promise on something people sign). Side effect: signing used to accept any file type; the pre-v2 tests that sent non-PDFs were updated. |
| **Executed PDF** | `pypdf` appends the original pages (never modified) and the reportlab-rendered certificate. Built by the worker (job `documents.signature_finalize`, enqueued in the *same transaction* as the completing signature), not in the request: PDF work and N emails do not belong in a public POST. The request is `complete` immediately; `executed.state` goes `pending` → `ready`. |
| **Resumable job** | Handler is `manages_transactions=True` (it commits between the file and each email). Step 1 (file) is skipped once `executed_file_id` is set; step 2 stamps `signature_signers.executed_emailed_at` / `signature_requests.owner_emailed_at` per party, so a retry after a mail failure only resends to the parties not yet done. Failures re-raise so the runner's backoff retries. |
| **Executed file** | An ordinary private `DocumentFile` (same storage, scan gate, 5-minute signed links). Listed as `origin: "executed"`. Trash/delete is refused (409) for it **and** for the source while the request exists (`has_signature_records`). |
| **Signer download** | `GET /sign/{token}/executed` with the signer's own link, only after completion, 90 days (`EXECUTED_ACCESS_DAYS`), uniform 404 otherwise; 409 `EXECUTED_NOT_READY` while building. The existing `open_for_signing` still 404s signed tokens. |
| **Tokens: hashed at rest AND retrievable** | The old random tokens could not be re-shown. New tokens are **derived**: `HMAC-SHA256(SECRET_KEY, "cofoundaz-<purpose>:<row id>:<version>")` (`links.py`), base64url; only `sha256(token)` is stored and looked up, the raw value is recomputed for an editor. A database leak yields no usable token. `signature_signers.token_version` rotates a signer's link (remind). Public lookups bound the input length (128), then compare with `hmac.compare_digest`. Rejected: storing the raw token (reversible at rest) or Fernet-encrypting it (a second secret to manage); rotating on every "copy" (it would kill the link already in the signer's inbox). Cost: rotating `SECRET_KEY` invalidates every outstanding derived link; pre-v2 shares keep their random token and keep working, but their link cannot be re-copied (`link: null`). |
| **Sequential** | `signature_requests.sequential`. Every signer row gets a placeholder hash nobody holds; the link is derived and `notified_at` set only on release (create for position 0, otherwise after the previous signature, `release_next`). `open_for_signing` also checks that all lower positions signed, so even a forged/derived token for a waiting signer is a 404. Copy-link for an unreleased signer is `409 SIGNER_NOT_RELEASED`. |
| **Concurrency** | `record_signature` locks the request row, then re-reads the signer `FOR UPDATE`, re-checks `signed_at` / active, and re-reads the whole roster for the completion check. Without it the last two parallel signers each saw the other as unsigned and the request never completed; a double submit overwrote the audit fields and queued 2 jobs. |
| **Link audit** | Editors can obtain a live signer link (create response, copy route), i.e. could sign as a counterparty. Kept (row 31) but audited: `signature_signers.link_copies` (JSONB) gets an entry per hand-out, returned to editors and printed on the certificate. Pre-v2 signers (random token) get `409 LINK_NOT_RECOVERABLE` on copy (rotating would kill the link in their inbox); a reminder mints a derived one. |
| **Legacy requests** | `executed.state: "legacy"` for completed requests with no `document_hash` (pre-v2): no executed copy is built or promised. |
| **Per-party email claim** | The handler's row lock is released by its first commit, so each send is first claimed with an atomic `UPDATE ... SET executed_emailed_at = now() WHERE ... IS NULL`; only the job whose UPDATE matched sends, and a failed send frees the claim for the retry. |
| **Bounds / malformed PDFs** | `expires_in_days` is 1–365 or null (never); any parser exception on a malformed PDF is `422 PDF_UNREADABLE`, never a 500. |
| **Viewed** | `GET /sign/{token}` stamps `viewed_at` once. Signing also fills it. No notification (not asked for). |
| **Message** | Stored on the request (`message`; the default sentence "{Founder} has sent you {doc} to sign via Cofoundaz." is stored when blank). Control chars stripped, ≤ 2000 chars. Only ever in an email **body**, `html.escape`d; subjects are built from the title with all whitespace collapsed (no CR/LF). Signer and share emails are validated as one plain address (no whitespace, `<>,;"'`). |
| **Link shares** | `document_shares` gains `file_id` (CHECK: exactly one of `document_id` / `file_id`), `email` nullable (NULL = anyone with the link, nothing emailed). Expiry: explicit `expires_at` (future, ≤ 365 days) wins over `expires_in_days` (default 30, `null` = never); `PATCH` changes it. Files: view only, must be `clean`, served by `GET /shared/{token}/file` (redirect to a provider-signed URL, or a local file response with `nosniff` + sandbox CSP), 404 when revoked / expired / trashed / not clean. |
| **Readability checks at send** | The PDF is read from storage and parsed when the request is created (`validate_pdf`: not encrypted, 1–300 pages), and its SHA-256 recorded as `document_hash`. A file we could not stamp later never gets sent. |
| **Storage** | `Storage.read_private(key)` added (local: file bytes; Cloudinary: download of a 60-second signed URL, 30 s timeout, no redirects). |

## What's involved

`alembic/versions/0075_documents_esign_v2.py` (signature_requests: `message`, `sequential`, `document_hash`,
`executed_file_id`, `executed_hash`, `owner_emailed_at`; signature_signers: `token_version`, `link_copies`, `notified_at` (backfilled to
`created_at`), `viewed_at`, `executed_emailed_at`; document_shares: `file_id`, nullable `document_id` / `email`, CHECK)
· `app/db/models/document.py` · `app/services/documents/{signatures,shares,esign_pdf,links,files,service}.py` ·
`app/worker/handlers/documents_sign.py` (+ `app/worker/__main__.py`) · `app/api/v1/endpoints/documents.py` ·
`app/schemas/document.py` · `app/platform/storage.py` · `pyproject.toml` / `poetry.lock` (`pypdf`).

New routes (founder + team member): `POST|GET /documents/files/{id}/shares`, `PATCH|DELETE .../shares/{share_id}`,
`PATCH /documents/{id}/shares/{share_id}`, `GET /documents/signature-requests/{id}/signers/{signer_id}/link`,
`GET /documents/signature-requests/{id}/executed`. Public: `GET /shared/{token}/file`, `GET /sign/{token}/executed`.
Changed: `POST /documents/{id}/shares` (email optional, `expires_at`, `link` in every editor read),
`POST .../signature-requests` (`sequential`, `message`, `signer_links` may hold `null`, PDF-only), `GET /sign/{token}`
(marks viewed; `message`, `sent_by`, `legal_caveat`), `POST /sign/{token}` (releases the next signer, `executed_url`),
remind (rotates only released, unsigned signers), serializers. Rate limits (`shared_limit`): shares create 60/h, request
create 30/h, copy link 60/h, remind 30/h; the public routes keep their shared buckets (`public-sign-read` 30/min,
`public-sign` 10/min, `public-doc-share` 30/min) and the two new public routes use them.

## Verification

- Unit/API: `tests/api/test_documents_esign_v2.py` (sequential order, viewed, message and header-injection cases,
  signer-email validation, PDF-only, request detail, link re-copy and rotation, executed PDF through the worker, signer
  download + 90-day limit, delete locks, link shares of documents and files, expiry rules, pre-v2 shares),
  `tests/worker/test_documents_sign_finalize.py` (through `run_once`, `SELF_MANAGED`, idempotent, per-party retry, missing
  source), `tests/services/documents/{test_esign_pdf,test_links}.py`, `tests/platform/test_storage_private.py`
  (`read_private`), `tests/test_documents_esign_migration.py` (backfill, CHECK, downgrade, round trip), the route × role
  matrix in `tests/api/test_documents_access.py` (every new route × every role). Existing signing tests now use real PDFs
  (`tests/pdf_helpers.py`), and `tests/conftest.py` isolates `LOCAL_STORAGE_DIR` per test.
- Live: `e2e/test_documents_esign_v2.py` → `e2e/_captures/documents_esign_v2/` (35 bodies). Tokens redacted before writing.
  The older `e2e/test_documents*.py` journeys still pass (their PDFs were made real; their captures are unchanged).
- Local CI parity (scratch Postgres/Redis, worktree poetry env): black, isort, ruff, mypy, bandit, pytest with
  `--cov-fail-under=95`, alembic single head / upgrade / check / downgrade base / upgrade, e2e — results in the PR.

## Operate / roll back

- **Deploy:** `docker compose build api worker` (new dependency `pypdf`), then migrate (`0075`), restart api + worker.
  The worker **must** be running for executed PDFs and their emails (job `documents.signature_finalize`); a request completed
  while it is down stays `executed.state: "pending"` and finishes when it returns.
- **Stuck job:** a failed `documents.signature_finalize` job (attempts exhausted) leaves `executed.state: "pending"`. Re-queue by
  inserting a job with the same payload (`{"request_id": "..."}`) or resetting the failed row to `queued`; the handler is idempotent.
- **`SECRET_KEY`:** do not rotate casually; every outstanding signer link and v2 share link dies (re-copy after remind /
  re-share).
- **Roll back:** `alembic downgrade 0074_documents_templates_export` drops the new columns and **deletes** file shares and
  link-only shares (they cannot exist in the old schema); executed files stay as ordinary files. Old code ignores the new
  rows' extra fields. Signers whose links were derived keep valid hashes (the hash column is unchanged).

## Follow-ups

- Field placement (page / x / y per signer) once the PDF viewer (row 11, FE) and a Q2 decision exist; draw-a-signature pad.
- Re-queue/"rebuild executed copy" action for editors, and an admin view of failed finalize jobs.
- Reminder timing (14/30-day timers), expiry reminders and a "Viewed" notification to the sender.
- DOCX/image signing would need a trusted converter in the image.
- Shares: per-person grants on files; comment-level shares of files; rotating a share's link without revoking it.
- A real malware scanner behind `get_scanner()` (still the no-op), which also gates file shares.
- The Documents Cloudinary read path (`read_private`) is unit-tested with a fake provider only; exercise it on staging.
