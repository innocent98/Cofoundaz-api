# SOP — Documents BE-A: access scoping + upload/e-sign integrity

**What shipped** — every authenticated Documents route is now founder + team member only (reads
included); document notifications go to the sender/owner instead of the whole workspace; uploads are
judged by their bytes with a `scan_status` gate in front of signing; and a file with a signature
request can no longer be deleted. Migration `0072_documents_scan_status`. PR: see the branch
`fix/documents-access` → `develop` (merge **after** `feature/finance-slice-e`, which owns `0071`).

Closes rows 1–6, 25 and 37 of the Documents PRD gap list (`docs/checklist/documents-prd-gaps.md` on
FE PR #161) under D-DOC-1..4 (Adebayo, 2026-10-24): per-person grants come later (BE-B), so until
then mentors, consultants, investors, accountants and legal advisors get 403 everywhere.

## Why

The FE live walk found four security gaps and two integrity gaps:

| Row | Problem |
|---|---|
| 1–3 | Reads used `require_workspace`: a mentor or consultant listed every document, opened its full content, read every file URL, every signer email and every share-recipient email. |
| 4 | `document.shared` / `document.signature.*` fanned out to every member via `_members_minus_actor` (mentors included), and `requested` told members "a document needs a signature" although signers are external. |
| 5 | The type check trusted the client `Content-Type`: `MZ` sent as `application/pdf` was stored (201) and could be mailed to signers. No scan state. |
| 6 | `signature_requests.file_id` was `ON DELETE CASCADE`: deleting a file silently deleted its requests, completed ones included. |
| 25 | Markdown was not an allowed type. |
| 37 | `signed_ip` / `signed_user_agent` were captured but never returned. |

## How

**Access (rows 1–3).** `DOCUMENT_ROLES` (`app/db/models/enums.py`, founder + team member) is the one
definition. `_editor = require_role(*DOCUMENT_ROLES)` replaces `require_workspace` on all 18
authenticated routes. Two more Documents read paths were gated with the same set so no side door
remains: global search (the `document` source and the `/documents` page, `app/services/search/service.py`)
and the dashboard activity feed (`document` namespace moved from open to restricted in
`app/platform/activity.py`). The two public token routes (`/shared/{token}`, `/sign/{token}`) are
unchanged: the token is the credential.

| Route | Method | Dependency |
|---|---|---|
| `/document-templates`, `/document-templates/{key}` | GET | `_editor` |
| `/documents` | GET, POST | `_editor` |
| `/documents/{id}` | GET, PUT, DELETE | `_editor` |
| `/documents/files` | GET, POST | `_editor` |
| `/documents/files/{id}` | GET, DELETE | `_editor` |
| `/documents/shares` | GET | `_editor` |
| `/documents/{id}/shares` | GET, POST | `_editor` |
| `/documents/{id}/shares/{share_id}` | DELETE | `_editor` |
| `/documents/signature-requests`, `/{id}` | GET | `_editor` |
| `/documents/signature-requests/{id}/remind`, `/cancel` | POST | `_editor` |
| `/documents/files/{id}/signature-requests` | POST | `_editor` |
| `/shared/{token}`, `/sign/{token}` | GET (+POST sign) | none (public, token) |

**Notifications (row 4).** `document.shared`, `document.signature.{requested,signed,completed}` are in
`AUDIENCE` (`app/services/notifications/audience.py`) as `DOCUMENT_ROLES`, so the read filter also
hides rows stored before this change from mentors. Fan-out (`registry.py`): `signed` / `completed` →
the request's creator (`owner_id` in the payload) while still F/TM, else every active founder;
`shared` → the document owner unless they are the sharer; `requested` → nobody in-app (signers get
the email). Payloads gained `owner_id`.

**Upload integrity (rows 5, 25).** `app/services/documents/sniff.py` decides what the bytes are (PDF
`%PDF-`, PNG, JPEG, OOXML = zip with `[Content_Types].xml` + `word/`/`xl/`/`ppt/`, text = NUL-free
UTF-8) and requires it to match the declared type. The stored `content_type` is the canonical sniffed
one. Markdown accepted as `text/markdown`, `text/x-markdown`, or `application/octet-stream` with an
`.md`/`.markdown` name. Empty files are refused. Size limit stays 15 MB (D-DOC-3).

**Scan gate (row 5).** `document_files.scan_status` (`pending|clean|blocked`) + `scanned_at`.
`app/services/documents/scanner.py` is the hook: `NoopScanner` marks every file `clean`. **There is no
real scanner yet** — swap `get_scanner()` for ClamAV/a vendor; an async scanner may return `pending`
and finish later with `apply_verdict`. `assert_clean` (409 `FILE_NOT_CLEAN`) gates creating a
signature request, and `open_for_signing` 404s when a file is blocked after the request went out.
Files are not shareable today (shares attach to documents), so `assert_clean` is ready for BE-C.

**Delete (row 6).** `delete_file` returns 409 `FILE_HAS_SIGNATURE_REQUESTS` when any request (any
status) references the file; the FK is now `NO ACTION` so the database backs the rule. Workspace
deletion still works (both tables cascade from `startups`). Rejected alternative: keep the request and
mark the file gone — needs a tombstone model and an executed-PDF story that BE-D owns.

**Audit (row 37).** Request detail and list include per-signer `signed_ip` and `signed_user_agent`
(editors only; the public sign response still has no roster).

## What's involved

`app/api/v1/endpoints/documents.py` · `app/services/documents/{files,signatures,shares,sniff,scanner}.py`
· `app/db/models/{document,enums}.py` · `app/core/errors.py` (`FileHasSignatureRequests`,
`FileNotClean`) · `app/services/notifications/{audience,registry}.py` · `app/platform/activity.py` ·
`app/services/search/service.py` · `alembic/versions/0072_documents_scan_status.py`.

Migration `0072` (down_revision `0071_finance_slice_e`): adds `scan_status` (server default
`pending`) and `scanned_at`; **grandfathers existing rows to `clean`** (they predate any scan hook, and
live signature requests must keep working); replaces the `signature_requests.file_id` FK with
`NO ACTION`. Downgrade restores `CASCADE` and drops the columns.

## Verification

- `tests/api/test_documents_access.py`: every route × every role (every `MembershipRole`): denied roles get `403 FORBIDDEN` and no signer/share email in the body; F/TM pass the
  gate; an OpenAPI-driven test fails if a Documents route is added without a matrix row.
- `tests/api/test_documents_integrity.py`, `tests/services/documents/test_{sniff,scanner}.py`,
  `tests/services/notifications/test_document_notifications.py`, activity/search gate tests.
- Live: `e2e/test_documents_access.py` (founder + team member + mentor + consultant on a running
  server; captures in `e2e/_captures/documents_access/`), plus the existing `e2e/test_documents.py`.
- CI parity run locally: black, isort, ruff, mypy, pytest `--cov-fail-under=95`, alembic heads /
  upgrade / check / downgrade base / upgrade, e2e, gitleaks, semgrep, bandit, pip-audit, pylint ≥ 9.5.

## Operate / roll back

No new env vars. Rolling back the code without downgrading is safe for reads; to revert the schema run
`alembic downgrade 0071_finance_slice_e` (re-introduces the delete cascade). The FE must hide Documents
for roles outside F/TM (gap row 27) — until then mentors see 403 toasts.

## Follow-ups

- A real scanner (`get_scanner`) and a worker path for `pending` → verdict.
- Per-person grants + "Shared with me" (BE-B, row 14); the roles denied here regain scoped access then.
- Dashboard health signal `legal.signatures_complete` still counts signature requests for every role
  (count only, no content) — revisit with BE-B.
- Notification copy/payload (row 28) is untouched; `owner_id` was added to payloads only.
