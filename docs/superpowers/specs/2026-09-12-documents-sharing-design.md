# Module 18 Documents & Templates — Slice 3 Design: Sharing

**Date:** 2026-09-12
**Module:** 18 Documents & Templates (Slice 3 of 4)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)
**Depends on:** Slice 1 (Document Library Core, PR #48) + Slice 2 (Upload & Files, PR #50), both merged to `develop`.

---

## 1. Context & Scope

Slice 1 shipped structured `documents`; Slice 2 added opaque file uploads. Slice 3 adds **Sharing**: a founder shares a document by email so someone outside the workspace can open it via an **expiring, tokenized read link**, at a chosen access level, with a "Shared with" list per document and a "Shared with others" overview.

Scope recovered from the UI comp (`Documents & Templates.dc.html`): a **Share** modal with an **email** field, an **access level** (View / Comment / Edit), and a **"Link expires in 30 days"** toggle; a per-document **"Shared with"** list (recipient + access + role); and a **"Shared with others"** table (`Document | Shared with | Access | Last viewed`).

### Access model (decided 2026-09-12)
**External expiring link (token), View/Comment only.** A share is a tokenized read link. The recipient opens the document through a **public** `GET /shared/{token}` (no workspace login), read-only. Workspace members keep editing through their normal authenticated access — the feature's value is external reach, which sidesteps ACL-vs-RBAC elevation and the anonymous-edit-vs-version-history conflict.

This mirrors the existing **invitation token pattern** (`app/services/onboarding/invites.py` + `app/db/models/invitation.py`): generate `raw = secrets.token_urlsafe(32)`, store `token_hash = hash_token(raw)` (sha256, `String(64)`, unique — `hash_token` lives in `app/services/auth/sessions.py`), email the raw token in the link, look up by hash, and return a **uniform 404** for unknown/expired/revoked (don't leak whether a token existed).

Transactional email already exists — `app/platform/email.py` (`EmailSender`, Console/SMTP backends, `get_email_sender()`) is already used by onboarding invites — so this is **not** blocked on Module 20 (which is in-app/push notifications, a different thing).

### Non-goals / deferred
- **Edit via link** — an anonymous link editor can't be attributed in the document version history (which shows named authors). Deferred; when member-scoped ACL editing lands it can add an `edit` tier.
- **Comment feature** — documents have no comment entity in v1; the `comment` access tier is stored for forward-compat but functionally equals `view` until a comments feature exists.
- **Sharing uploaded files** (Slice 2 `document_files`) — v1 shares structured `documents` only. File sharing is a follow-up.
- **Internal per-document ACL grants** (elevating a member on one doc) — not this slice.
- **"Expired vs invalid" distinction** on the public endpoint — uniform 404, per the invitation convention.
- E-signature is Slice 4.

---

## 2. Data model — `document_shares` (new table + migration)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin`, `server_default now()` |
| `startup_id` | UUID FK→`startups.id` CASCADE, `index=True` | tenancy + the workspace overview query |
| `document_id` | UUID FK→`documents.id` CASCADE, `index=True` | the shared document |
| `shared_by_id` | UUID FK→`users.id` SET NULL, nullable, `index=True` | who created the share |
| `email` | `String(255)`, NOT NULL | recipient email |
| `access_level` | `Enum(ShareAccess, native_enum=False, length=20)`, NOT NULL | `view` / `comment` |
| `token_hash` | `String(64)`, **unique**, NOT NULL | sha256 of the raw link token |
| `expires_at` | timestamptz, nullable | `null` = never expires |
| `revoked_at` | timestamptz, nullable | revoke sets this |
| `last_viewed_at` | timestamptz, nullable | set on each successful public open |

**Indexes:** standalone `ix_document_shares_startup_id`, `ix_document_shares_document_id`, `ix_document_shares_shared_by_id` (FK convention); the `unique=True` on `token_hash` gives the lookup index. Composite `Index("ix_document_shares_startup_created", "startup_id", "created_at")` for the overview ordering.

**New enum** (`app/db/models/enums.py`, VARCHAR-backed per convention):
```python
class ShareAccess(enum.StrEnum):
    view = "view"
    comment = "comment"
```

**Migration:** `0018_document_shares`, chained onto the current develop head `0017_document_files`. ⚠️ **Coordination:** the junior's Module 17 (Learning Academy) branch also targets `0018` (per the issue-#49 reply). Whoever merges second renumbers; settle the exact prefix against the live head at build time. Single alembic head + zero drift required.

---

## 3. Service — `app/services/documents/shares.py`

Pure functions, no `db.commit()` (endpoints commit):

- `create_share(db, document, *, shared_by_id, email, access_level, expires_in_days) -> tuple[DocumentShare, str]`
  — generates `raw = secrets.token_urlsafe(32)`, stores `token_hash = hash_token(raw)`; sets `expires_at = now + expires_in_days` (or `None` when `expires_in_days` is falsy); inserts the row; `flush`; publishes `"document.shared"` (`{startup_id, document_id, share_id}` string values). **Returns the row AND the raw token** (the endpoint builds the link + sends the email; the raw token is never persisted).
- `list_shares(db, document) -> list[DocumentShare]` — a document's shares, newest first (for the "Shared with" list). Excludes nothing by default; the serializer marks revoked/expired state so the FE can grey them.
- `list_workspace_shares(db, startup) -> list[DocumentShare]` — all shares in the workspace (the "Shared with others" overview), newest first.
- `revoke_share(db, share) -> None` — set `revoked_at = now`; `flush`; publish `"document.share.revoked"`.
- `open_shared(db, token) -> DocumentShare` — look up by `hash_token(token)`; raise `NotFound` (404) if missing, revoked (`revoked_at is not None`), or expired (`expires_at is not None and expires_at <= now`); else set `last_viewed_at = now`, `flush`, and return the row (caller serializes the document).
- `_share(db, membership, document, share_id) -> DocumentShare` — tenant-scoped fetch for revoke (404 if missing / wrong document / cross-tenant).
- `serialize_share(db, s) -> dict` — `{id, email, access_level, expires_at, revoked_at, last_viewed_at, created_at, status}` where `status ∈ {active, expired, revoked}` is derived; the "Shared with others" overview adds `document_id` + `document_title`.

Access to a shared document reuses Slice 1 `serialize_document` at view level.

---

## 4. Endpoints — extend `app/api/v1/endpoints/documents.py`

Read = any member (`require_workspace`); write = editor (`_editor`); the public open endpoint takes **no auth**. The share sub-routes are under `/documents/{document_id}/...` (distinct from the Slice 1/2 routes) and the literal `/documents/shares` + `/shared/{token}` are declared **before** the `/documents/{document_id}` catch-all routes.

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `POST` | `/documents/{document_id}/shares` | editor | body `{email, access_level, expires_in_days?}` (default 30). `create_share` → build link `{SERVER_HOST}/shared/{raw}` → `get_email_sender().send(...)` → `db.commit()` → 201 serialized share |
| `GET` | `/documents/{document_id}/shares` | member | the doc's "Shared with" list |
| `DELETE` | `/documents/{document_id}/shares/{share_id}` | editor | `revoke_share` → `db.commit()` |
| `GET` | `/documents/shares` | member | workspace "Shared with others" overview |
| `GET` | `/shared/{token}` | **public** | `open_shared` → `db.commit()` (persists `last_viewed_at`) → returns `{document: serialize_document(view), access_level, expires_at}`; **404** unknown/expired/revoked |

**Route placement:** `GET /documents/shares` MUST precede `GET /documents/{document_id}` (else `shares` is captured as a `document_id` and 422s). `GET /shared/{token}` is a new top-level route (its own router include, or added to this router with a `/shared` path) — confirm it is NOT under any workspace-auth dependency.

**`document_id`/`share_id` typed `uuid.UUID`.** `access_level` validated against `ShareAccess` (unknown → 422 via the Pydantic body model).

---

## 5. Errors
- `VALIDATION_ERROR` (422) — bad `access_level`, malformed body.
- `NotFound` (404) — unknown document/share, cross-tenant, and (uniformly) unknown/expired/revoked share token on the public open.
- `Forbidden` (403) — non-editor on create/revoke; non-member on the authed reads (via `require_workspace`).

---

## 6. Testing
**Unit (real Postgres):**
- `create_share`: row persisted, `token_hash` set (raw not stored), `expires_at` = ~now+30d by default and `None` when `expires_in_days=0/None`; returns the raw token; publishes `document.shared`.
- `open_shared`: valid token returns the share + sets `last_viewed_at`; expired → `NotFound`; revoked → `NotFound`; unknown → `NotFound`.
- `revoke_share`: sets `revoked_at`; a subsequent `open_shared` → 404.
- list (per-doc) + workspace overview shapes; `serialize_share` derives `status` (active/expired/revoked) and omits `token_hash`.
- API: POST creates + sends an email (assert the sender was called / captured) + 201; editor-only (mentor → 403); member can list; cross-tenant revoke of a real other-startup share → 404; **public** `GET /shared/{token}` works with NO auth header, returns the document, sets last_viewed, and 404s after revoke/expiry; `GET /documents/shares` not shadowed by `/documents/{document_id}`.

**Live e2e (`scripts/e2e_run.sh`, file email backend):** founder shares a doc → read the raw link out of the captured email (the e2e mail dir, same mechanism auth journeys use) → `GET /shared/{token}` returns the doc → revoke → `GET /shared/{token}` → 404. Capture every request/response body.

**FE integration guide** `docs/fe-integration-guide-documents-sharing.md` — real captured bodies; the multipart-free JSON share contract; the access tiers (note **Edit is not offered in v1**, and **Comment == View functionally** until comments exist); the default-30-day expiry + "never" case; the uniform-404 for expired/invalid links (FE shows one "link invalid or expired" state); the public no-auth open endpoint; the last-viewed semantics.

---

## 7. File structure
| File | Change |
|---|---|
| `app/db/models/enums.py` | add `ShareAccess` |
| `app/db/models/document.py` | add `DocumentShare` model |
| `alembic/versions/0018_document_shares.py` | new migration |
| `app/services/documents/shares.py` | **new** — create/list/list_workspace/revoke/open/serialize |
| `app/schemas/document.py` | `ShareCreate` request model |
| `app/api/v1/endpoints/documents.py` | 4 authed share routes + the public `/shared/{token}` (route ordering) |
| `tests/services/documents/test_shares.py`, `tests/api/test_document_shares.py` | unit tests |
| `e2e/test_documents.py` | append the share journey |
| `docs/fe-integration-guide-documents-sharing.md`, `docs/sop/2026-09-12-documents-sharing-slice3.md`, `docs/checklist/PROJECT_CHECKLIST.md` | docs |

---

## 8. Decisions & waivers
- **D1 — External expiring-link (token) model, View/Comment only.** Read-only public access; members edit via normal auth. (User decision, 2026-09-12.)
- **D2 — Mirror the invitation token pattern** (`token_urlsafe(32)` + `hash_token` sha256 + uniform 404). Reuses `hash_token` from `app/services/auth/sessions.py`.
- **D3 — `comment` tier stored but functionally == `view`** (no comment entity in v1); `edit` deferred (anonymous edits unattributable).
- **D4 — Shares target structured `documents` only**; file sharing deferred.
- **D5 — Default 30-day expiry** (comp default); `expires_in_days` falsy ⇒ never.
- **D6 — Email via the existing `EmailSender`** (not blocked on Module 20).
