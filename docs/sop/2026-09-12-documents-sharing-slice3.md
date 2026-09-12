# SOP — Documents & Templates, Sharing (Module 18, Slice 3)

**What shipped** — the third slice of Module 18: external document sharing via an expiring,
tokenized read link. A founder shares a document by email; the recipient opens it through a
**public, unauthenticated** `GET /shared/{token}` — no workspace login, no membership, read-only.
A new `document_shares` table, one new service module, five endpoints (four authenticated + the one
public route), and no changes to Slice 1's `documents` or Slice 2's `document_files` tables.

Commits (branch `feat/documents-sharing`, off `develop` @ `a156953` post-PR #50 merge):
`4080fc6` (design) → `5290716` (implementation plan) → `0a88b97` (unrelated but required fix —
`extra=ignore` on settings so a new env var doesn't break startup) → `53c577d` (Task 1 —
`document_shares` table + `ShareAccess` enum + migration `0018`) → `0a2e0a0` (Task 2 — sharing
service: create/list/revoke/open/serialize) → `ee7ced4` (Task 3 — 5 HTTP endpoints + router wiring)
→ this task's e2e/SOP/FE-guide/checklist commit (Task 4 — final task of the 4-task plan,
`.superpowers/sdd/2026-09-12-documents-sharing-slice3/`).

## Why

Slice 1 and Slice 2 gave a startup a document library and a file library, both scoped strictly to
workspace members. Neither has any concept of an outside party — an investor, a lawyer, a
consultant not on the platform — viewing a document. The comp (`Documents & Templates.dc.html`)
shows a **Share** modal (email + access level + expiry toggle), a per-document **"Shared with"**
list, and a workspace-wide **"Shared with others"** table. This slice builds exactly that surface
for structured `documents` (Slice 1). File sharing (Slice 2's `document_files`) and true in-workspace
per-member ACL editing are explicitly out of scope — see Follow-ups.

## How

**External expiring link (token), not an internal ACL grant.** The design considered elevating a
non-member to some in-workspace role scoped to one document, and rejected it: that conflates
tenancy/RBAC (which already has a clean member/editor split) with document-level permissions, and
raises questions this slice doesn't need to answer (does a share recipient get a real account? can
they be a mentor on one document and nothing else?). Instead, a share is just a **secret token**
mapped to one document, one access level, and an optional expiry — the recipient never
authenticates as anyone; they just hold a link. Workspace members keep editing through their normal
authenticated access unchanged.

**Mirrors the existing invitation-token pattern exactly**
(`app/services/onboarding/invites.py` + `app/db/models/invitation.py`): `raw =
secrets.token_urlsafe(32)`, `token_hash = hash_token(raw)` (sha256, reusing
`app/services/auth/sessions.py::hash_token` rather than writing a second hashing routine), store
only the hash, email the raw token in a link, look up by hash on open. This is a proven pattern
already in the codebase rather than a new one invented for this slice.

**Uniform 404 for unknown / expired / revoked — no distinguishing signal to the caller.**
`open_shared` (`app/services/documents/shares.py`) raises the same `NotFound` whether the token
never existed, its share was revoked, or its `expires_at` has passed. This is a deliberate security
property (an attacker probing tokens learns nothing from the response shape) carried over from the
invitation convention, not an oversight — the FE guide calls this out explicitly so the frontend
doesn't try to build "expired" vs. "revoked" copy the API can't support.

**Create returns the raw token/link exactly once.** `create_share` returns `(row, raw)` — the raw
token is never persisted (only `token_hash` is), so `POST /documents/{id}/shares`'s response is the
only place the link can ever be read from the API after the fact. Every other read (`GET
/documents/{id}/shares`, `GET /documents/shares`) serializes the row without a `link`/token field.
This is why the FE guide is explicit that the create-response `link` must be captured client-side
if the UI needs to show/copy it again.

**`comment` access tier stored but functionally `view`; `edit` not offered at all.** Documents have
no comment entity yet, so `comment` round-trips through the schema (forward-compatible column,
no migration needed when comments ship) but the public open endpoint returns the identical
read-only payload for both `view` and `comment`. `edit` isn't in the `ShareAccess` enum at all —
sending it 422s. The reasoning (recorded in the design doc, D3): an anonymous link-holder editing a
document can't be attributed in that document's version history, which shows named authors; solving
that needs member-scoped ACL editing, a different feature.

**Email via the existing `EmailSender` seam, not blocked on Module 20.** `POST
/documents/{id}/shares` calls `get_email_sender().send(...)` (`app/platform/email.py`) — the same
seam onboarding invites already use (Console backend for dev, SMTP for real delivery, file backend
for e2e/tests). Module 20 (in-app/push notifications) is a different, unrelated concern; this slice
does not depend on it.

**Route ordering, again.** As with Slice 2's `/documents/files`, the literal path `GET
/documents/shares` (workspace overview) is registered **before** `GET /documents/{document_id}` in
`app/api/v1/endpoints/documents.py`, or FastAPI would swallow `/documents/shares` into the
`{document_id}` route with `document_id="shares"`. `GET /shared/{token}` is its own top-level route
on the same router, with **no** `Depends(require_workspace)`/`Depends(get_verified_user)` at all —
confirmed by the live e2e call in this task, which sends zero auth headers.

## What's involved

**Data model / migration**
- `alembic/versions/0018_document_shares.py` — `document_shares` table (`startup_id` FK `ON DELETE
  CASCADE`, `document_id` FK `ON DELETE CASCADE`, `shared_by_id` FK `ON DELETE SET NULL`) +
  standalone `ix_document_shares_startup_id`/`ix_document_shares_document_id`/
  `ix_document_shares_shared_by_id` + unique `token_hash` (doubles as the lookup index) + composite
  `ix_document_shares_startup_created` on `(startup_id, created_at)` for the overview ordering.
  Chains directly off `0017_document_files` (Slice 2's head) — sole alembic head, verified via the
  `scripts/e2e_run.sh` run below, which migrates a fresh `cofoundaz_e2e` through `0018` from zero.
- `app/db/models/enums.py` — `ShareAccess(enum.StrEnum)`: `view`, `comment`.
- `app/db/models/document.py` — `DocumentShare` (`UUIDMixin`, `TimestampMixin`), alongside Slice
  1/2's `Document`/`DocumentFile` in the same module.

**Schemas**
- `app/schemas/document.py::ShareCreate` — `{email: str, access_level: ShareAccess = view,
  expires_in_days: int | None = 30}`.

**Services**
- `app/services/documents/shares.py` — `create_share` (token gen + hash + row + flush + publishes
  `document.shared`, returns `(row, raw)`), `list_shares` (per-document, newest-first),
  `list_workspace_shares` (per-startup, newest-first), `revoke_share` (sets `revoked_at` + flush +
  publishes `document.share.revoked`), `open_shared` (hash lookup, uniform 404, sets
  `last_viewed_at` + flush), `share_document`/`_share` (tenant-scoped fetch helpers for the
  endpoints), `serialize_share` (derives `status` ∈ `{active, expired, revoked}`, omits
  `token_hash`; `with_document=True` adds `document_id` for the workspace overview).

**Endpoints** (all under `/api/v1`, `app/api/v1/endpoints/documents.py`; `/documents/shares`
registered ahead of the `{document_id}` routes — see the route-ordering decision above)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/documents/{document_id}/shares` | founder / team_member (editor) | JSON body `{email, access_level?, expires_in_days?}`; 201; builds `{SERVER_HOST}/shared/{raw}`, emails it, `db.commit()`, response includes `link` (once) |
| GET | `/api/v1/documents/{document_id}/shares` | any active member | per-document "Shared with" list, no `link` |
| DELETE | `/api/v1/documents/{document_id}/shares/{share_id}` | founder / team_member (editor) | `{revoked: true}`; tenant/document-scoped 404 on miss |
| GET | `/api/v1/documents/shares` | any active member | workspace "Shared with others" overview; rows carry `document_id` |
| GET | `/api/v1/shared/{token}` | **none — public** | `{document (full, with sections), access_level, expires_at}`; 404 uniform for unknown/expired/revoked; `db.commit()` persists `last_viewed_at` |

**Errors** — no new error codes. Reuses `NotFound` (404 — unknown document/share, cross-tenant,
and the public open's uniform unknown/expired/revoked case), `VALIDATION_ERROR` (422 — bad
`access_level`/malformed body, via the `ShareCreate` Pydantic model), `Forbidden` (403 — non-editor
on create/revoke).

**Events** — `document.shared` (`{startup_id, document_id, share_id}`), `document.share.revoked`
(`{startup_id, share_id}`), both published from the service layer. No consumer yet — same
enqueue-now/consume-later posture as every other event pre-Module-03/20.

**Tests**
- `tests/services/documents/test_shares.py` (6 tests, Task 2) — create (raw returned, hashed
  stored, default/falsy expiry), open (valid sets `last_viewed_at`; unknown/expired/revoked all
  404), list + workspace list shapes, serialize (omits `token_hash`, derives `status`).
- `tests/api/test_document_shares.py` (7 tests, Task 3) — create returns `link` + public open
  works with no auth, list-then-revoke-then-404, unknown token 404, non-editor 403, workspace
  overview shape, route-not-shadowed regression, cross-tenant revoke 404.
- `e2e/test_documents.py::test_documents_sharing_journey` (new, this task) — founder onboards →
  creates a `business_plan` document → `POST /documents/{id}/shares` (201, `link` in response) →
  the same link independently confirmed against the raw email captured in the file mail dir → `GET
  /shared/{token}` with **zero** auth headers (200, document + access level) → `GET
  /documents/{id}/shares` (last_viewed_at now set) → `GET /documents/shares` (workspace overview,
  `document_id` present) → `DELETE` revoke → `GET /shared/{token}` → 404.

## Verification

- **Unit suite: 992 passed** (`poetry run pytest -q`) — 14 new tests over the 978-test baseline
  this branch inherited from `develop` after PR #50 (Slice 2) merged: 1 in `tests/db/
  test_document_share_model.py` (Task 1 — `DocumentShare` round-trips through the migrated table),
  6 in `tests/services/documents/test_shares.py` (Task 2), 7 in `tests/api/test_document_shares.py`
  (Task 3). The unrelated `extra=ignore` config fix (`0a88b97`) touched only
  `app/core/config.py` — no test changes — and predates Task 1 in the branch history; it made an
  undeclared `.env` key stop failing settings validation at import time, which nothing in this
  slice depends on directly but which the branch needed fixed before its own work could run
  cleanly. 98% coverage.
- **Live E2E: 36 passed** (`scripts/e2e_run.sh`) — up from 35, +1 for this task's new
  `test_documents_sharing_journey`. Full run: docker db+redis up, fresh `cofoundaz_e2e` migrated
  from zero through `0018_document_shares` (verified single head, sole chain off
  `0017_document_files`), real uvicorn with `EMAIL_BACKEND=file`, all 36 tests green — **all three
  commit points confirmed to persist** (the brief flagged that a missing `db.commit()` on
  create/revoke/open would surface here): the share is visible on the following `GET
  /documents/{id}/shares` (proves `create_share_endpoint`'s `db.commit()` ran), the public open
  actually returns the document and the follow-up "Shared with" list shows a non-null
  `last_viewed_at` (proves `open_shared_endpoint`'s `db.commit()` persisted the flush from
  `open_shared`), and the post-revoke `GET /shared/{token}` genuinely 404s (proves
  `revoke_share_endpoint`'s `db.commit()` ran) — all three endpoints already called `db.commit()`;
  no fix was needed.
  - `test_documents_sharing_journey`: founder onboards (steps 1-4 + complete) → `POST
    /documents` (`template_key: business_plan`) → `POST /documents/{id}/shares` (`{email,
    access_level: "view"}`, `expires_in_days` omitted → default 30d) → 201 with `link` → the raw
    link independently re-derived from the captured share email in `E2E_MAIL_DIR` (parsing the
    `<a href>` out of the email `html`, since — unlike the auth verification/reset emails — this
    one carries a link, not a bare `<code>` token) and asserted equal to the response's `link` →
    `GET /shared/{token}` with no `Authorization`/`X-Workspace-Id` headers at all → 200, document +
    `access_level: "view"` → `GET /documents/{id}/shares` → one row, `last_viewed_at` now set,
    `link` absent → `GET /documents/shares` → the same row plus `document_id` → `DELETE` revoke →
    200 `{revoked: true}` → `GET /shared/{token}` → 404 uniform envelope.
  - 6 new captures to `e2e/_captures/documents/share_*.json` — every one is the verbatim source for
    `docs/fe-integration-guide-documents-sharing.md`, re-read fresh after the final green
    `scripts/e2e_run.sh` run in this task (not reused from an earlier run — see that guide's intro).
- `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app` —
  all clean (also spot-checked `ruff`/`black` against `e2e/` for the new test file; not part of the
  CI-gated `app tests` scope but kept consistent regardless).
- Migration round-trip verified via the `scripts/e2e_run.sh` run above (fresh `cofoundaz_e2e`
  migrated `0017_document_files` → `0018_document_shares` from zero); `alembic heads` confirms
  `0018_document_shares` is the sole head.

## Operate / roll back

- **FOLLOW-UP, not yet fixed: `SERVER_HOST` should point at the FE origin, not the API host, before
  this reaches staging/production as currently configured.** The emailed link is built as
  `f"{settings.SERVER_HOST}/shared/{raw}"`. Checked live in this task: `.env.staging` sets
  `SERVER_HOST=https://staging-api.cofoundaz.com` and `.env.production` sets
  `SERVER_HOST=https://api.cofoundaz.com` — both the **API's own** host, not an FE origin. A
  recipient clicking the emailed link today would hit the API directly and get the raw JSON body
  from `GET /shared/{token}` rather than a rendered FE page. This is **not** fixed in this slice —
  fixing it needs either repointing `SERVER_HOST` at the FE's deployed origin (if the FE will own a
  `/shared/:token` route that itself calls the API) or introducing a separate `FRONTEND_HOST`/
  `APP_URL` setting so the two hosts can differ independently of each other, which is a decision for
  whoever owns the FE deploy — flagged here rather than silently shipped broken. See the FE guide
  §6 for how the frontend should read this today.
- No other new deploy-time env vars; `alembic upgrade head` picks up `0018_document_shares`
  automatically. `EMAIL_BACKEND` (already existing — `console`/`smtp`/`file`) governs whether the
  share email actually sends anywhere real; staging/production should already have this set to
  `smtp` from prior slices/modules that use the same sender.
- **Rollback:** `alembic downgrade -1` from `0018_document_shares` drops
  `ix_document_shares_startup_created`, `ix_document_shares_shared_by_id`,
  `ix_document_shares_document_id`, `ix_document_shares_startup_id`, then the `document_shares`
  table itself — **lossy**: every share record (including revoked/expired history) is destroyed on
  downgrade. This does not affect `documents` or `document_files` at all (no shared columns, no FK
  from those tables back into `document_shares`). Rolling back the migration without also reverting
  the 5 endpoint registrations in `app/api/v1/endpoints/documents.py` would make all of them fail
  against a now-missing table — roll back the migration and the endpoint registration together.

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Edit access tier + member-scoped ACL editing.** Not offered at all in v1 (`ShareAccess` has
  only `view`/`comment`) — an anonymous link-holder editing a document can't be attributed in the
  version history. A future "invite this external person as a scoped collaborator" feature would
  need a real account + a document-level ACL grant, not an extension of this token model.
- **Comment feature.** `access_level: "comment"` is stored and returned but functionally identical
  to `view` today — there is no comment entity on a `Document` yet. When comments ship, the public
  open endpoint should start branching on `access_level` to allow comment writes for `comment`-tier
  shares.
- **Sharing uploaded files (`document_files`, Slice 2).** This slice covers structured `documents`
  only. A founder cannot generate a share link for an uploaded PDF/DOCX — that would need its own
  `document_shares`-style table (or a nullable `file_id` alternative to `document_id` on this one)
  scoped to `DocumentFile` rows.
- **E-signature is Slice 4** — the next and final slice of Module 18, a natural consumer of both
  this slice's read-only external access and Slice 2's uploaded files.
- **`SERVER_HOST`/FE-link gap** — see Operate/roll back above; tracked, not fixed here.
- **Wrap the share-create email send in `try`/`except`.** `create_share_endpoint` currently calls
  `get_email_sender().send(...)` inline, unguarded, between `create_share` and `db.commit()`. On the
  `console`/`file` backends used in dev/test/e2e this can't fail; on the real `SMTPEmailSender`
  (used in staging/production once `EMAIL_BACKEND=smtp`), a transient SMTP error would raise out of
  the endpoint and 500 the whole share-create call — even though the share row itself would already
  be valid and the link would still resolve correctly via `GET /shared/{token}` had the row been
  committed. The fix is to wrap the `send(...)` call, log + Sentry-capture on failure, and still
  return the 201 with the link (the recipient just doesn't get an email in that one failure case,
  which is a strictly better outcome than a 500 on an otherwise-successful share). Not fixed in this
  task — Task 3 (endpoints) predates this task's SOP + verification pass, and per the brief this
  final task ships docs/e2e only, no `app/` changes — flagged here as the first thing to pick up
  before this branch merges, or immediately after, rather than left as a silent gap.
