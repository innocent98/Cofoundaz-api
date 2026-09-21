# Module 18 Documents & Templates — Slice 4 Design: E-signature

**Date:** 2026-09-14
**Module:** 18 Documents & Templates (Slice 4 of 4 — the final slice; completes Module 18)
**Status:** Approved design → implementation plan next
**Base branch / PR target:** `develop` (→ staging)
**Depends on:** Slices 1–3 (Library Core #48, Upload & Files #50, Sharing #53), all merged to `develop`. Reuses the Slice 3 tokenized-link + email pattern and the Slice 2 `document_files` table.

---

## 1. Context & Scope

Slices 1–3 shipped structured documents, uploaded files, and sharing. Slice 4 adds **E-signature**: send an uploaded file to N signers by email; each signs via a secure link; the request tracks progress and completes when all sign.

Scope from the UI comp (`Documents & Templates.dc.html`): a **"Send for signature"** modal (add signers by email; "Signers receive an email with a secure link. You will be notified as each one signs"); an **"E-signature requests"** list — per request: title, `sent` date, `signedCount` of `total` signed, a status pill (Draft / Awaiting / Complete / Expired), a Remind/Download/Resend CTA, and the per-signer signed/unsigned marks.

### Approach (decided 2026-09-14)
**Build-your-own tokenized-link e-signature.** Reuses the Slice 3 share pattern: each signer gets an emailed secure link, opens a **public** page to view the file, and signs by **typing their name + confirming intent**. We capture a per-signer **audit trail** (typed name, timestamp, IP, user-agent). This is a valid *simple electronic signature* (ESIGN/UETA) — not a certificate-based/notarized signature; a third-party provider (DocuSign, etc.) is a possible later enhancement for legally-robust signing.

### Decisions
- **D1 — Sign uploaded files, not structured documents.** A signature request targets a Slice 2 `document_files` row (an immutable artifact), not a mutable/versioned structured `document`. Signing structured docs (freeze-to-PDF first) is a follow-up.
- **D2 — Typed-name simple signature + audit trail** (name, `signed_at`, IP, user-agent). No drawn-signature image in v1.
- **D3 — No draft state.** Create sends immediately (the comp's "Send request" is one action); `draft` deferred.
- **D4 — Default 14-day expiry** (`expires_in_days` overridable).
- **D5 — Migration `0020_signatures`.** `0019` is reserved for the junior's Module 17 (per the issue-#52 heads-up); settle the exact prefix against the live head at build time.

### Non-goals / deferred
- Third-party provider integration; drawn/image signatures; signing structured documents; sequential (ordered) signing enforcement — v1 lets any signer sign in any order; a `draft` (save-without-send) state; decline-to-sign; in-app owner notifications (events published, unconsumed until Module 20).

---

## 2. Data model (two new tables + migration `0020_signatures`)

### `signature_requests`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |
| `startup_id` | UUID FK→startups CASCADE, `index=True` | tenancy + workspace list |
| `file_id` | UUID FK→`document_files.id` CASCADE, `index=True` | the file being signed |
| `title` | `String(255)`, NOT NULL | request title (defaults from the file's filename) |
| `status` | `Enum(SignatureRequestStatus, native_enum=False, length=20)`, `server_default 'awaiting'` | `awaiting`/`complete`/`cancelled`; **`expired` derived** |
| `created_by_id` | UUID FK→users SET NULL, nullable, `index=True` | requester |
| `expires_at` | timestamptz, nullable | default now+14d |
| `completed_at` | timestamptz, nullable | set when all sign |
| `cancelled_at` | timestamptz, nullable | set on cancel |

Composite `Index("ix_signature_requests_startup_created", "startup_id", "created_at")`.

### `signature_signers`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `created_at`, `updated_at` | timestamptz | |
| `request_id` | UUID FK→`signature_requests.id` CASCADE, `index=True` | |
| `email` | `String(255)`, NOT NULL | |
| `name` | `String(255)`, nullable | optional pre-filled name |
| `token_hash` | `String(64)`, **unique**, NOT NULL | sha256 of the signer's secure-link token |
| `position` | `Integer`, NOT NULL | order in the request (0-based) |
| `signed_at` | timestamptz, nullable | null ⇒ pending |
| `signed_name` | `String(255)`, nullable | the name typed at signing |
| `signed_ip` | `String(64)`, nullable | audit |
| `signed_user_agent` | `String(512)`, nullable | audit |

Standalone FK indexes per convention. Per-signer status derived: `signed` if `signed_at` else `pending`.

**New enum** (`app/db/models/enums.py`, VARCHAR-backed):
```python
class SignatureRequestStatus(enum.StrEnum):
    awaiting = "awaiting"
    complete = "complete"
    cancelled = "cancelled"
```

---

## 3. Services — `app/services/documents/signatures.py`

Pure functions, no `db.commit()` (endpoints commit); token pattern mirrors Slice 3 / invitations (`secrets.token_urlsafe(32)` + `hash_token`, uniform 404).

- `create_request(db, file, *, created_by_id, title, signers, expires_in_days) -> tuple[SignatureRequest, list[tuple[SignatureSigner, str]]]`
  — inserts the request (`awaiting`, `expires_at = now+days`) + one signer row per `{email, name?}` with a fresh token; flush; publish `document.signature.requested`. Returns the request and each `(signer, raw_token)` so the endpoint can email the links. Requires ≥1 signer (else 422).
- `list_requests(db, startup) -> list[SignatureRequest]` — workspace list, newest first.
- `get_request(db, membership, request_id) -> SignatureRequest` — tenant-scoped (404).
- `cancel_request(db, request) -> None` — set `status=cancelled`, `cancelled_at=now`; flush; publish `document.signature.cancelled`. No-op-guard: cancelling a `complete` request → `AppError` 409 `SIGNATURE_NOT_ACTIVE`.
- `unsigned_signers(db, request) -> list[SignatureSigner]` — for remind (re-issue is NOT needed; the same token is re-emailed).
- `open_for_signing(db, token) -> SignatureSigner` — look up signer by `hash_token(token)`; 404 if unknown, or its request is cancelled/expired/complete, **or already signed** (`signed_at is not None`). (Returns the signer; the endpoint loads the request + file for the view.)
- `record_signature(db, signer, *, typed_name, ip, user_agent) -> SignatureRequest` — re-validates the signer is still active (else 404); sets `signed_at=now`, `signed_name`, `signed_ip`, `signed_user_agent`; publish `document.signature.signed`; if **all** signers of the request now have `signed_at` → set request `status=complete`, `completed_at=now`, publish `document.signature.completed`. Returns the (possibly-updated) request.
- `_is_expired(request) -> bool`; `request_status(request) -> str` (derives `expired` from `awaiting` + past `expires_at`).
- `serialize_request(db, r, *, with_signers=True) -> dict` — `{id, title, status (derived), file_id, filename, signed_count, total, expires_at, completed_at, created_at, signers:[{email, name, position, status, signed_at, signed_name}]}`; **never** emits `token_hash`.
- `_file(db, membership, file_id) -> DocumentFile` — tenant-scoped file fetch (404) for the create endpoint.

---

## 4. Endpoints — extend `app/api/v1/endpoints/documents.py`

Read = `require_workspace`; write = `_editor`; the two signing endpoints take **no auth**. Literal `/documents/signature-requests` and `/sign/{token}` declared before the `/documents/{document_id}` catch-all.

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `POST` | `/documents/files/{file_id}/signature-requests` | editor | body `{signers:[{email,name?}], title?, expires_in_days?}` → `_file` (404) → `create_request` → email each signer `{SERVER_HOST}/sign/{raw}` (best-effort per send) → `db.commit()` → 201 serialized request |
| `GET` | `/documents/signature-requests` | member | workspace list |
| `GET` | `/documents/signature-requests/{request_id}` | member | one request |
| `POST` | `/documents/signature-requests/{request_id}/remind` | editor | re-email unsigned signers (their existing links) → `db.commit()` |
| `POST` | `/documents/signature-requests/{request_id}/cancel` | editor | `cancel_request` → `db.commit()` |
| `GET` | `/sign/{token}` | **public** | `open_for_signing` → returns `{request:{title,status}, file:{filename,content_type,url}, signer:{email,name}}`; 404 unknown/expired/cancelled/complete/already-signed |
| `POST` | `/sign/{token}` | **public** | body `{typed_name}` (non-empty → 422 if blank); `record_signature(ip=request.client.host, user_agent=header)` → `db.commit()` (persists the signature) → returns the updated request status; 404 if bad/expired/cancelled/complete/already-signed |

**Email best-effort:** the create + remind sends are wrapped in try/except + log (a flaky provider must not 500 request creation), same as share notifications. The signer's link is `{SERVER_HOST}/sign/{raw}`.

`file_id`/`request_id` typed `uuid.UUID`. Every write endpoint AND the public `POST /sign/{token}` (writes the signature) MUST `db.commit()`.

---

## 5. Errors
- `VALIDATION_ERROR` (422) — no signers; blank `typed_name`.
- `NotFound` (404) — unknown/cross-tenant file or request; and uniformly on the public endpoints for unknown/expired/cancelled/complete/already-signed tokens (don't leak existence).
- `SIGNATURE_NOT_ACTIVE` (409) — cancel/remind on a complete or cancelled request.
- `Forbidden` (403) — non-editor on writes; non-member on authed reads.

---

## 6. Testing
**Unit (real Postgres):**
- `create_request`: request + N signer rows persisted, each with a distinct token; `expires_at ≈ now+14d`; ≥1-signer required (empty → 422); returns raw tokens; publishes `requested`.
- `open_for_signing`: valid token returns the signer; unknown/expired/cancelled/complete/already-signed → `NotFound`.
- `record_signature`: sets `signed_at`+audit fields; publishes `signed`; when the LAST signer signs → request `complete` + `completed_at` + `completed` event; a 2-signer request stays `awaiting` after the first.
- `cancel_request`: sets cancelled; a signer can't then sign (404); cancelling a complete request → 409.
- `request_status` derives `expired`; `serialize_request` gives `signed_count`/`total`, per-signer derived status, and omits `token_hash`.
- API: editor-only create/remind/cancel (mentor → 403); member list/get; cross-tenant get/cancel of a real other-startup request → 404; **public** `GET /sign/{token}` + `POST /sign/{token}` work with NO auth, record the signature, and 404 after cancel/expiry/complete; the two-signer completion flow over HTTP.

**Live e2e (`scripts/e2e_run.sh`, file email backend):** upload a file → create a 2-signer request → read each signer's link from the captured emails → `GET /sign/{token}` (no auth) each → `POST /sign/{token}` each → after the second, the request is `complete` → `GET /documents/signature-requests` shows 2/2 + complete. Capture every request/response body.

**FE integration guide** `docs/fe-integration-guide-documents-esignature.md` — verbatim captured bodies; the create contract; per-signer secure-link flow; the public no-auth view+sign endpoints and their uniform 404s; the derived `status` (incl. `expired`) + `signedCount`/`total`; remind/cancel; the audit-trail fields; **note this is a simple electronic signature, not certificate-based** (a UX/legal caveat the FE should surface).

---

## 7. File structure
| File | Change |
|---|---|
| `app/db/models/enums.py` | add `SignatureRequestStatus` |
| `app/db/models/document.py` | add `SignatureRequest`, `SignatureSigner` |
| `alembic/versions/0020_signatures.py` | new migration (both tables) |
| `app/services/documents/signatures.py` | **new** — create/list/get/cancel/remind/open/record/serialize |
| `app/schemas/document.py` | `SignatureRequestCreate`, `SignAction` request models |
| `app/api/v1/endpoints/documents.py` | 5 authed routes + 2 public (`/sign/{token}`) (route ordering) |
| `tests/services/documents/test_signatures.py`, `tests/api/test_signatures.py` | unit tests |
| `e2e/test_documents.py` | append the signing journey |
| `docs/fe-integration-guide-documents-esignature.md`, `docs/sop/2026-09-14-documents-esignature-slice4.md`, `docs/checklist/PROJECT_CHECKLIST.md` | docs (+ mark **Module 18 COMPLETE**) |

---

## 8. Decisions & waivers
- **D1–D5** as in §1.
- **D6 — Per-signer tokenized links** mirror the Slice 3 share / invitation pattern (`token_urlsafe(32)` + `hash_token`, uniform 404). A signer's token is single-use for signing (already-signed → 404).
- **D7 — Any-order signing** in v1 (no enforced sequence); `position` is stored for display/order only.
- **D8 — Owner notification** is event-only in v1 (+ best-effort email on completion via the existing sender); real in-app notification is Module 20.
- **D9 — Completes Module 18.** After this slice, Module 18 (all four slices) is done.
