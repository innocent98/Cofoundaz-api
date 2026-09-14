# SOP — Documents & Templates, E-signature (Module 18, Slice 4 — final slice, Module 18 COMPLETE)

**What shipped** — the fourth and final slice of Module 18: send an uploaded file to N signers by
email; each signs via a secure, single-use tokenized link; the request tracks progress and
completes when everyone has signed. Two new tables, one new service module, seven endpoints (five
authenticated + two public), no changes to Slice 1's `documents`, Slice 2's `document_files`, or
Slice 3's `document_shares` tables. **This completes Module 18 (Documents & Templates) — all four
slices (Library Core, Upload & Files, Sharing, E-signature) now ship together.**

Commits (branch `feat/documents-esignature`, off `develop` @ `4c40640` post-PR #54 merge — the
Resend email backend):
`d87e44b` (design) → `2765fb4` (implementation plan) → `8602955` (Task 1 —
`signature_requests`/`signature_signers` tables + `SignatureRequestStatus` enum + migration
`0020_signatures`) → `838604a` (Task 2 — e-signature service: create/list/get/cancel/remind/open/
record/serialize) → `690c87d` (Task 3 — 7 HTTP endpoints + router wiring) → this task's e2e/SOP/
FE-guide/checklist commit (Task 4 — final task of the 4-task plan,
`.superpowers/sdd/2026-09-14-documents-esignature-slice4/`).

## Why

Slices 1–3 gave a startup a document library, an uploaded-file library, and read-only external
sharing. None of them close a real business loop: a founder sending an NDA or an investor
agreement out for actual signature. The comp (`Documents & Templates.dc.html`) shows a **"Send for
signature"** modal (add signers by email; "Signers receive an email with a secure link. You will
be notified as each one signs") and an **"E-signature requests"** list — title, sent date,
`signedCount` of `total`, a status pill, per-signer marks, and Remind/Download/Resend CTAs. This
slice builds exactly that surface.

## How

**Build-your-own tokenized-link signing, not a third-party e-sign provider.** The design (§1 of
the design doc) explicitly considered and rejected wiring DocuSign/Adobe Sign/HelloSign for v1 —
that's a real integration (OAuth, webhooks, a hosted signing UI, per-envelope billing) disproportionate
to what the comp actually needs to ship. Instead this reuses the **exact same pattern Slice 3
(Sharing) already proved**: `secrets.token_urlsafe(32)` + `hash_token` (SHA-256, the same
`app/services/auth/sessions.py::hash_token` every token-based feature in this codebase reuses) +
uniform 404. The tradeoff, recorded explicitly (D6 in the design): this is a **simple electronic
signature** (typed name + audit trail), not a certificate-based/notarized one — legally weaker than
a DocuSign envelope for document types that require real identity verification, but sufficient for
ESIGN/UETA-style simple consent in most cases. A real provider remains a named future option, not a
silently-dropped requirement — see Follow-ups.

**Sign uploaded files, not structured documents (D1).** A signature request's `file_id` points at a
Slice 2 `document_files` row — an immutable binary artifact — not a Slice 1 `documents` row. The
design rejected signing structured `documents` directly because they're mutable/versioned (a
signer's signature would be meaningless against a document that changes underneath them after
signing); "freeze a structured document to a static file first, then sign that file" is the correct
future shape and is called out as a follow-up rather than solved here.

**No draft state (D3).** The comp's "Send request" is a single action — create sends immediately.
There is no "save without sending" state; a founder who wants to review before sending has to do
that review outside the request (in the file itself before upload). `draft` is deferred, matching
how Slice 1's documents already ship without a scheduling concept.

**Any-order signing (D7).** `position` on `signature_signers` is stored and returned for display
ordering only — nothing in `open_for_signing`/`record_signature` enforces that signer 0 must sign
before signer 1. Two signers on the same request can sign in either order, or "simultaneously" (no
lock contention issue since each signer's row is independent). Sequential/ordered enforcement is a
named follow-up, not an oversight.

**Uniform 404 — five different causes, one response shape.** `open_for_signing`
(`app/services/documents/signatures.py`) raises the same `NotFound` whether the token never
existed, the signer already signed, or the request is cancelled/complete/expired. This is the same
deliberate security property Slice 3 already established for `/shared/{token}` (an attacker
probing tokens learns nothing from the response), carried straight over rather than re-litigated —
the FE guide (§5) documents all five causes explicitly so the frontend builds one "link no longer
valid" state instead of trying to distinguish copy the API can't support.

**`remind` rotates tokens — it does not resend the original link.** This is the one behavior that
differs meaningfully from Slice 3's sharing model (which has no remind/resend concept at all).
`reissue_unsigned` (`app/services/documents/signatures.py`) mints a **fresh** `token_urlsafe(32)`
per still-unsigned signer and overwrites `token_hash` — the reasoning: raw tokens are never
persisted (by design, same as everywhere else in this codebase), so there is no original raw value
left to re-send once the create response has been returned; the only way to "remind" is to issue a
new credential. The consequence, called out explicitly in the FE guide (§7): **any link the FE was
holding onto for an unsigned signer (from the one-time `signer_links` at create) goes dead the
moment remind runs**, even if that signer never clicked it.

**Best-effort email, wrapped from day one.** Unlike Slice 3 (whose share-create email send was
flagged as an unguarded-`try`/`except` gap in that slice's own Follow-ups and left unfixed),
this slice's `_signature_email` helper (`app/api/v1/endpoints/documents.py`) wraps every send —
create and remind — in `try`/`except Exception` + `log.warning`, so a flaky mail backend cannot
500 either endpoint. This was built correctly the first time, having learned from Slice 3's gap.

**Route ordering, again.** As with every prior slice's literal-vs-parameterized route conflict,
`GET /documents/signature-requests` and `POST /documents/files/{file_id}/signature-requests` are
registered ahead of `GET /documents/{document_id}` in `app/api/v1/endpoints/documents.py`, so
FastAPI doesn't swallow the literal segment into the `{document_id}` path parameter. `GET`/`POST
/sign/{token}` are separate top-level routes with **no** `Depends(require_workspace)`/
`Depends(get_verified_user)` at all — confirmed live in this task's e2e run, which sends zero auth
headers to either.

## What's involved

**Data model / migration**
- `alembic/versions/0020_signatures.py` — `signature_requests` (`startup_id` FK `ON DELETE CASCADE`,
  `file_id` FK → `document_files.id` `ON DELETE CASCADE`, `created_by_id` FK → `users.id`
  `ON DELETE SET NULL`, `status` VARCHAR-backed enum default `awaiting`) + standalone
  `ix_signature_requests_startup_id`/`_file_id`/`_created_by_id` + composite
  `ix_signature_requests_startup_created` on `(startup_id, created_at)`. `signature_signers`
  (`request_id` FK `ON DELETE CASCADE`, unique/indexed `token_hash`, `position`, audit fields
  `signed_at`/`signed_name`/`signed_ip`/`signed_user_agent`) + standalone
  `ix_signature_signers_request_id`. Chains directly off `0018_document_shares` — **`0019` is
  intentionally skipped**, reserved for another engineer's in-flight Module 17 work per the
  project's #52 heads-up; `0020_signatures` is the sole alembic head, verified via the
  `scripts/e2e_run.sh` run below, which migrates a fresh `cofoundaz_e2e` through `0020` from zero.
  Produced via `alembic revision --autogenerate` against the Task 1 ORM models, hand-edited only for
  revision id/down_revision/docstring — upgrade()/downgrade() bodies are exactly as generated.
- `app/db/models/enums.py` — `SignatureRequestStatus(enum.StrEnum)`: `awaiting`, `complete`,
  `cancelled` (`expired` is derived at read time, never stored — see below).
- `app/db/models/document.py` — `SignatureRequest`, `SignatureSigner` (`UUIDMixin`,
  `TimestampMixin`), alongside Slices 1–3's `Document`/`DocumentFile`/`DocumentShare` in the same
  module.

**Schemas**
- `app/schemas/document.py::SignerInput` — `{email: str, name: str | None}`.
- `app/schemas/document.py::SignatureRequestCreate` — `{signers: list[SignerInput], title: str |
  None, expires_in_days: int | None = 14}`.
- `app/schemas/document.py::SignAction` — `{typed_name: str}`.

**Services**
- `app/services/documents/signatures.py` — `create_request` (≥1-signer required, else 422; inserts
  the request + one signer row per input with a fresh raw token; flush; publishes
  `document.signature.requested`; returns `(request, [(signer, raw_token), ...])`) · `list_requests`
  (workspace, newest-first) · `get_request` (tenant-scoped 404) · `cancel_request` (409
  `SignatureNotActive` if not `awaiting`) · `unsigned_signers` · `reissue_unsigned` (rotates
  unsigned signers' tokens; 409 if request not active) · `open_for_signing` (hash lookup; uniform
  404 unknown/already-signed/inactive-request) · `record_signature` (re-validates active; sets
  `signed_at`/`signed_name`/`signed_ip`/`signed_user_agent`; publishes `document.signature.signed`;
  flips the request to `complete` + `completed_at` + publishes `document.signature.completed` the
  instant every signer has signed) · `request_status` (derives `expired` from `awaiting` + past
  `expires_at`) · `serialize_request` (with_signers toggle; never emits `token_hash`, and — see FE
  guide §10 — never emits `signed_ip`/`signed_user_agent` either, even though both are captured).

**Endpoints** (all under `/api/v1`, `app/api/v1/endpoints/documents.py`; the two `/sign/{token}`
routes and `/documents/signature-requests` registered ahead of the `{document_id}` catch-all)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/documents/files/{file_id}/signature-requests` | founder/team_member (editor) | body `{signers, title?, expires_in_days?}`; 201; tenant-scoped file 404; emails each signer `{SERVER_HOST}/sign/{raw}` (best-effort, wrapped); `db.commit()`; response includes `signer_links` (once) |
| GET | `/api/v1/documents/signature-requests` | any active member | workspace list, full `signers` array per row (not summary-shaped) |
| GET | `/api/v1/documents/signature-requests/{request_id}` | any active member | one request, tenant-scoped 404 |
| POST | `/api/v1/documents/signature-requests/{request_id}/remind` | founder/team_member (editor) | rotates unsigned signers' tokens (`reissue_unsigned`), re-emails fresh links; 409 if not active; `db.commit()` |
| POST | `/api/v1/documents/signature-requests/{request_id}/cancel` | founder/team_member (editor) | 409 if not active; `db.commit()` |
| GET | `/api/v1/sign/{token}` | **none — public** | `{request: {title, status}, file: {...}, signer: {email, name}}`; uniform 404 unknown/expired/cancelled/complete/already-signed |
| POST | `/api/v1/sign/{token}` | **none — public** | body `{typed_name}` (blank → 422); records the signature (`ip`/`user-agent` from the request); `db.commit()`; returns the full updated request |

**Errors** — one new error code. Reuses `NotFound` (404 — unknown/cross-tenant file/request, and
the public signing routes' uniform unknown/already-signed/cancelled/complete/expired case),
`VALIDATION_ERROR` (422 — empty `signers`, blank `typed_name`), `Forbidden` (403 — non-editor on
create/remind/cancel). New: `SignatureNotActive` (`app/core/errors.py`) — `409
SIGNATURE_NOT_ACTIVE`, raised by both `cancel_request` and `reissue_unsigned` when the target
request is already `complete` or `cancelled`.

**Events** — `document.signature.requested`, `document.signature.cancelled`,
`document.signature.signed`, `document.signature.completed`, all `{startup_id, request_id, ...}`,
published from the service layer. No consumer yet — same enqueue-now/consume-later posture as
every other event pre-Module-20 (owner in-app notification on completion is a named follow-up into
that module, per D8 in the design).

**Tests**
- `tests/services/documents/test_signatures.py` (9 tests, Task 2) — create (tokens distinct,
  ≥1-signer enforced), open (unknown/expired/cancelled/complete/already-signed all 404), sign
  (first-of-two stays awaiting; last-of-two completes), cancel (a complete request → 409), reissue
  (rotates tokens, old dead / new works), `request_status` derives `expired`, `serialize_request`
  counts + omits `token_hash`.
- `tests/api/test_signatures.py` (8 tests, Task 3) — create returns `signer_links` + public sign
  completes over HTTP, empty-signers 422, list + cancel, remind rotates (old link dead), mentor →
  403, unknown token 404, route-not-shadowed regression, cross-tenant cancel 404.
- `e2e/test_documents.py::test_documents_esignature_journey` (new, this task) — founder onboards →
  uploads a file → `POST .../signature-requests` (2 signers, 201, `signer_links`) → each signer's
  link independently confirmed against the captured signature email in the file mail dir → `GET
  /sign/{token}` (no auth) for each → `POST /sign/{token}` for the first (stays `awaiting`,
  `signed_count: 1`) → view + sign the second (flips to `complete`) → re-opening the first's
  (already-signed) token 404s → `GET .../{id}` and `GET .../` both show `2 of 2` + `complete` →
  a second one-signer request created then cancelled, its signer's link 404s afterward.

## Verification

- **Unit suite: 1016 passed** (`poetry run pytest -q`) — 24 new tests over the 992-test baseline
  this branch inherited from `develop` after PR #54 (Resend email) merged: 9 in
  `tests/services/documents/test_signatures.py` (Task 2), 8 in `tests/api/test_signatures.py` (Task
  3), plus model round-trip coverage folded into Task 1. 98% coverage, unchanged floor.
- **Live E2E: 37 passed** (`scripts/e2e_run.sh`) — up from 36, +1 for this task's new
  `test_documents_esignature_journey`. Full run: docker db+redis up, fresh `cofoundaz_e2e` migrated
  from zero through `0020_signatures` (verified single head, sole chain off `0018_document_shares`
  with `0019` skipped as designed), real uvicorn with `EMAIL_BACKEND=file`, all 37 tests green —
  **every commit point confirmed to persist**: the second signer's `POST /sign/{token}` genuinely
  flips the request to `complete` on the following `GET` (proves `sign_endpoint`'s `db.commit()`
  ran), the cancelled request's signer link genuinely 404s afterward (proves
  `cancel_signature_request_endpoint`'s `db.commit()` ran) — both endpoints already called
  `db.commit()` correctly; no fix was needed.
  - 11 new captures to `e2e/_captures/documents/signature_*.json` — every one is the verbatim
    source for `docs/fe-integration-guide-documents-esignature.md`, re-read fresh after the final
    green `scripts/e2e_run.sh` run in this task (not reused from an earlier run — see that guide's
    intro).
- `poetry run ruff check app tests e2e`, `poetry run black --check app tests e2e`, `poetry run mypy
  app` — all clean.
- Migration round-trip verified via the `scripts/e2e_run.sh` run above (fresh `cofoundaz_e2e`
  migrated `0018_document_shares` → `0020_signatures` from zero, skipping the reserved `0019`);
  `alembic heads` confirms `0020_signatures` is the sole head.

## Operate / roll back

- **Same `SERVER_HOST`/FE-link follow-up as Slice 3, now affecting a second link family.** The
  emailed signing link is built as `f"{settings.SERVER_HOST}/sign/{raw}"` — identical pattern to
  Slice 3's `f"{SERVER_HOST}/shared/{raw}"`. `SERVER_HOST` in `.env.staging`/`.env.production`
  still points at the **API's own host**, not an FE origin (unchanged since Slice 3 — not fixed in
  this slice either). A signer clicking a real emailed link today hits the API directly and gets
  raw JSON from `GET /sign/{token}` instead of a rendered FE signing page. This is the same
  tracked, unfixed gap — see Slice 3's SOP for the two remediation options (repoint `SERVER_HOST`,
  or introduce a separate `FRONTEND_HOST`/`APP_URL` setting) — now doubly motivating a fix since two
  slices' worth of emailed links depend on it.
- No other new deploy-time env vars; `alembic upgrade head` picks up `0020_signatures`
  automatically (and, transitively, `0019` whenever the other engineer's Module 17 work lands and
  is applied first — this migration was written to chain off `0018` specifically so it does not
  block on that work landing first).
- **Rollback:** `alembic downgrade -1` from `0020_signatures` drops
  `ix_signature_signers_request_id`, `signature_signers`, then
  `ix_signature_requests_startup_id`/`_startup_created`/`_file_id`/`_created_by_id`, then
  `signature_requests` itself — **lossy**: every signature request and signer record (including
  completed signatures and their audit trail) is destroyed on downgrade. This does not affect
  `documents`, `document_files`, or `document_shares` at all (no shared columns, no FK from those
  tables back into either new table). Rolling back the migration without also reverting the 7
  endpoint registrations in `app/api/v1/endpoints/documents.py` would make all of them fail against
  now-missing tables — roll back the migration and the endpoint registration together.

## Follow-ups

**Known gap deferred to a follow-up (flagged in the final whole-branch review, not blocking):**
- **Completion is not row-locked.** `record_signature` reads the request with a plain `.one()`,
  sets the signer's `signed_at`, then re-counts to decide completion. Under READ COMMITTED, two
  final signers POSTing concurrently can each miss the other's uncommitted `signed_at`, leaving the
  request stuck at `signed_count == total` but `status = "awaiting"` (and the
  `document.signature.completed` event never fires). No signatures are lost — only the completion
  transition is missed. Same class as the Slice 3 share pattern (also unlocked). Fix: `SELECT … FOR
  UPDATE` the request row before the all-signed check, plus a two-concurrent-signers race test.

**Deferred to later slices/modules (by design, not oversights):**
- **Third-party provider for certificate-based signing.** This slice's typed-name + audit-trail
  model is a valid simple electronic signature, not a certificate-based/notarized one (see the "How"
  section above and the FE guide's legal caveat, §11). A DocuSign/Adobe Sign/HelloSign integration
  is the path to certificate-based signing for document types that need it — not built here,
  named explicitly as the future option rather than silently dropped.
- **Drawn-signature image.** v1 is typed-name only (D2). A canvas-drawn signature image is a UI/FE
  feature with a small backend change (store an image blob or data-URI alongside `signed_name`) —
  not needed for the comp's current design, which only shows a text-confirmation flow.
- **Signing structured documents (Slice 1's `documents`), not just uploaded files.** D1's
  rationale — a mutable/versioned document can't safely be the target of a signature — still holds.
  The correct future shape is "freeze a structured document to a static file/PDF, then create a
  signature request against that frozen artifact" — a feature that needs its own freeze/export step,
  not built here.
- **Ordered/sequential signing enforcement.** `position` is stored and returned for display only
  (D7) — any signer can sign in any order today. A "signer 2 can't sign until signer 1 has" mode
  would need a new validation branch in `open_for_signing`/`record_signature` plus product
  decisions about what a not-yet-your-turn signer sees when they open their link early.
- **Decline-to-sign.** There's no way for a signer to actively decline (only to never click the
  link, which just leaves the request `awaiting` until it expires). A real decline flow needs a new
  signer-status value and a founder-facing notification distinct from "no one's gotten to it yet."
- **Owner in-app notification on completion.** `document.signature.completed` (and the other three
  events) publish today with no consumer — same enqueue-now/consume-later posture as every
  pre-Module-20 event in this codebase (D8). A founder currently has to poll `GET
  /documents/signature-requests` to notice a request completed; real push/in-app notification is
  Module 20's job.
- **`SERVER_HOST`/FE-link gap** — see Operate/roll back above; tracked, not fixed here, now
  affecting both Slice 3's share links and this slice's signing links.
- **Audit trail (`signed_ip`/`signed_user_agent`) is captured but never exposed via any API
  response** (FE guide §10). If a future compliance/audit screen needs to show this, it needs a new
  field added to `serialize_request`'s per-signer dict — a small, deliberate addition, not built in
  this slice since nothing in the comp currently calls for it.
- **No `Download` action** for the signed file/certificate, despite the comp showing a Download CTA
  on the E-signature requests list. The underlying file is already downloadable via Slice 2's `GET
  /documents/files/{id}` (`url`); there is no generated "signed certificate" PDF (with the audit
  trail baked in) the way a real e-sign provider would produce — that artifact doesn't exist in this
  build-your-own model and would need its own generation step (e.g. a rendered PDF summarizing
  signers + timestamps + typed names) if the comp's Download button needs to produce something
  beyond the original uploaded file.
