# SOP — Documents BE-B: library core (versions, trash, folders, search, grants)

**What shipped** — version history with restore, soft-delete Trash with a 30-day purge job,
rename/move/tag, nested folders with counts, server-side search, richer list rows, an inline file
content route, notification payloads/copy, and **per-person grants with a "Shared with me" list**
(D-DOC-1). Migration `0073_documents_library_core` (only one). Branch `feature/documents-library-core`
→ `develop` (PR in the hand-back). Closes gap-list rows 7, 8, 9, 11, 14, 20, 21, 22, 28.

## Why

The Documents walk (2026-10-24) found: saves overwrote content with no history (7); deletes were
permanent and unconfirmed (8); no rename/move/tag/download (9); uploaded files could not be opened (11);
search ignored `q` (20); a hard-coded folder list (21); rows without owner/tags/share state (22);
notification copy without names or links (28); and no way for a mentor, advisor, investor or accountant
to see a specific document (14). BE-A made every route F/TM-only; BE-B adds the sanctioned way back in.

## How

**Access model (the important part).** All BE-A routes keep `_editor` (F + TM). Grants never touch them.
A grantee uses four dedicated routes under `/documents/shared-with-me*` (any verified account, **no
workspace header**, so an investor who is not a member can use them). Access = a live grant
(`document_grants.revoked_at IS NULL`) **bound to the caller's user id** on a document with
`trashed_at IS NULL`; anything else is a uniform 404. `tests/api/test_documents_access.py` lists every
route × role and fails if a Documents route is added without a row; the four grantee routes are the only
declared exception.

| Piece | Decision |
|---|---|
| Grantee | A workspace member (`user_id`) or an email. Email grants bind to the account whose **verified** email matches: on `auth.user.verified`, and lazily at the top of each shared-with-me call (covers login). An unverified account never binds. |
| Permissions | `view`, `comment` (reuses `ShareAccess`); `edit` is rejected (422) until the doc-level concurrency story exists. |
| Grant responses | A non-member grantee is always `status: "pending"` with only the email (Opus review: otherwise creating a grant by email is an oracle for "is this address a Cofoundaz account / what is their name"). |
| Comments | Rows in the existing `comments` table (`entity_type='document'`); owner routes under `/documents/{id}/comments`, grantee routes under shared-with-me. Deliberately **not** added to the team `/comments` API, whose reads are open to every member. Deleted with the document. |
| Versions | `document_versions` snapshot per version number (unique `(document_id, version)`), written on create, save, restore and AI section regeneration. Restore = new version (history never rewritten). PUT/restore take a row lock so concurrent saves get the 409, not a duplicate number. Existing documents get one snapshot of their current version. |
| Trash | `trashed_at` on documents and files; every normal lookup filters it; `GET /documents/trash`; restore; `/permanent`. Files with signature requests still cannot be trashed (BE-A rule). Grants and token links survive trash (invisible) and return on restore; grants can be revoked while trashed. |
| Purge | Daily scheduled task `documents.trash_purge` (one global job) → self-managed worker handler `run_purge`: delete documents (cascades versions/grants/shares; comments explicitly) → commit → delete files → commit → remove stored objects. Batch 500; `SKIP LOCKED`. |
| Folders | `document_folders` (name, parent, `path`); items keep `folder` = the path string, so the existing field/filter are unchanged. Using a new path creates the chain. Rename re-points the subtree and every item; delete only when empty. Max depth 4, path ≤ 120. Rejected: a `folder_id` FK on items (would break the FE contract and the upload form field). |
| Search | `q`: every term must be a case-insensitive substring of title/tags/body (docs) or name/tags (files); `search_text` column maintained on write (backfilled in the migration). Chose substring over Postgres FTS so half-typed words work; no index yet (per-workspace volumes are small; add `pg_trgm` if it ever matters). File contents are not indexed. |
| Content route | `GET /documents/files/{id}/content` = BE-A's signed link served inline (`/documents/files/content?token=`), same nosniff + sandbox CSP, with a `viewer` hint. |
| Notifications | `document.version.created` → owner/founders + members with a live grant, minus the author; `document.grant.created` → a grantee who is a member; both are "open" audience types with exact recipients (`DIRECT` in `test_dashboard_audience.py`). Existing share/signature copy now carries names, titles, `href`. Signer `typed_name` capped at 200. |
| Grants rate limit | `30/hour` per caller on `POST …/grants` (the invite email is sent to any address). |

## What's involved

`alembic/versions/0073_documents_library_core.py` · `app/db/models/document.py` (+`DocumentVersion`,
`DocumentFolder`, `DocumentGrant`; trash/tags/search columns) · `app/services/documents/{service,versions,
folders,grants,trash,files,shares,signatures}.py` · `app/api/v1/endpoints/documents.py` ·
`app/schemas/document.py` · `app/core/errors.py` (4 errors) · `app/worker/{scheduler,handlers/scheduled,
handlers/plan}.py` · `app/services/notifications/{registry,categories}.py` · `app/services/search/service.py`
· `app/api/v1/api.py` (grant-binding subscriber).

New routes (all F/TM unless noted): `GET /documents/{id}/versions[/{n}]`, `POST …/versions/{n}/restore`,
`PATCH /documents/{id}`, `PATCH /documents/files/{id}`, `POST …/restore`, `DELETE …/permanent` (docs + files),
`GET /documents/trash`, folders (`GET/POST /documents/folders`, `POST …/standard`, `PATCH/DELETE …/{id}`),
`GET /documents/files/{id}/content`, `GET /documents/files/content?token=` (public, signed),
`GET/POST /documents/{id}/grants`, `PATCH/DELETE …/{grant_id}`, `GET /documents/grants`,
`GET/POST /documents/{id}/comments`, and **(any verified account)** `GET /documents/shared-with-me`,
`…/{id}`, `…/{id}/comments` (GET, POST). Changed: `DELETE /documents/{id}` and `/files/{id}` are soft;
`GET /documents`/`/files` take `q`/`tag`; rows gained fields.

Migration `0073` (down `0072_documents_scan_status`): columns + three tables + backfill (search text,
one snapshot per document, root folders for every folder string in use). Downgrade drops it all
(grants and history are lost).

## Verification

- `tests/api/test_documents_{library,grants,access,notifications_be_b}.py`, `tests/worker/test_documents_trash_purge.py`,
  `tests/test_documents_library_migration.py` (backfill + round trip), plus the updated plan-regeneration test.
  Roles exercised: founder, team member, mentor and consultant (members), a member grantee, a non-member
  email grantee (binding after sign-up), a non-grantee, a revoked grantee; 403/404 on trashed and ungranted.
- Live: `e2e/test_documents_library.py` (captures in `e2e/_captures/documents_library/`), and the full
  `e2e/` suite (94 tests) green.
- CI parity run locally on scratch databases: black, isort, ruff, mypy, pytest with `--cov-fail-under=95`,
  alembic single head / upgrade / check / downgrade base / upgrade, e2e, plus the security scanners (see the PR).
- One Opus access-control review; findings and fixes below.

## Opus review (access control, trash, purge, email binding)

| # | Finding | Resolution |
|---|---|---|
| 1 | Email grant response revealed account existence + real name | Non-member grantees are always `pending`, email only |
| 2 | Grant email could be used to mail-bomb an address (create/revoke loop) | `30/hour` rate limit |
| 3 | Comments survived permanent delete / purge | Deleted with the document (service + purge) |
| 4 | AI section regeneration bumped `version` with no snapshot, stale search text, no lock, wrote to trashed docs | `plan.py` snapshots, updates `search_text`, locks the row, skips trashed |
| 5 | Concurrent identical grants → 500 | `IntegrityError` → 409 `GRANT_EXISTS` |
| 6 | Could not revoke a grant/link while the document was trashed | Grant routes use `trashed=None` (token-link revoke by id was already not trash-gated) |
| 7 | Grantee comment payload leaked internal user ids / `mention_user_ids` | Outsider serializer returns `{name}` only |
| 10–12 | Tag filter wildcards; unbounded `typed_name` in a notification title; blank comment body | `autoescape`, `max_length=200`, non-blank pattern |
| 8, 9 | Grants outlive membership removal; a provider-signed URL already issued survives a trash for ≤ 5 min | Accepted, see Follow-ups |

## Operate / roll back

No new env vars. Deploy: `alembic upgrade head`; the worker picks up the daily purge by itself (first
tick enqueues it). Roll back: `alembic downgrade 0072_documents_scan_status` (drops grants, history,
folders; trashed items become live again because the column goes). Rolling back the code without the
downgrade leaves the extra columns harmless.

## Follow-ups

- Member removal does not exist yet: when it ships, decide whether `user_id` grants end with membership.
- Grants on files, "Can edit" grants, extracted-text search for files, version pruning (history is unbounded).
- Real malware scanner (BE-A), export/instantiate (BE-C), e-sign v2 (BE-D).
- A provider-signed (Cloudinary) URL stays valid for its ≤ 5 minutes after the file is trashed.
