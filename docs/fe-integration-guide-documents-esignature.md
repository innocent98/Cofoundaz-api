# FE Integration Guide — Documents & Templates (Module 18, Slice 4: E-signature)

**This slice completes Module 18** (Documents & Templates) — all four slices (Library Core,
Upload & Files, Sharing, E-signature) now ship together.

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_documents.py::test_documents_esignature_journey` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/documents/signature_*.json`. Nothing here is retyped
from the schema, the service, or memory; every capture file was re-opened and copied fresh after
the final green e2e run for this task. IDs, tokens, and timestamps are real values from that
ephemeral test run (they differ on every real request; the shapes are exact). Every payload,
status code, and error body in this guide was exercised live, except the rows explicitly marked
"unit only" in §10, each cited to a passing test.

Base path: `/api/v1`. Same auth convention as the rest of Documents & Templates for the 5
workspace-scoped routes: Bearer access token (`Authorization: Bearer <token>`) + `X-Workspace-Id`
header — **except the two public signing routes, §3 and §4, which take no auth at all.**

**Role rule:** read (`GET /documents/signature-requests`, `GET
/documents/signature-requests/{id}`) = any active member (`require_workspace`). Write (`POST
.../signature-requests`, `.../remind`, `.../cancel`) = founder or team_member (editor) — the same
`_editor = require_role(founder, team_member)` gate every other Documents write uses. A non-editor
member (e.g. `mentor`) gets `403 FORBIDDEN` on create/remind/cancel — not captured live in this
journey, see §10 (`tests/api/test_signatures.py::test_mentor_cannot_create_403`).

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §9).

---

## 0. The model in one paragraph

A signature request targets one **already-uploaded file** (Slice 2's `document_files`, not a
Slice 1 structured `document`) and lists N signers by email. `POST
/documents/files/{file_id}/signature-requests` creates the request, emails each signer a secure
`{APP_BASE_URL}/sign/{token}` link (the **FE** origin — see §1), and returns every one of those links
**once**, in the create response only. A signer opens their link with **zero authentication** — no login, no workspace
membership — via `GET /sign/{token}`, reviews the file, and signs by **typing their name**
(`POST /sign/{token}`). This is a **simple electronic signature** (typed name + a server-recorded
audit trail), not a certificate-based/notarized signature — see §8's legal caveat before shipping
any UI copy that implies otherwise. The request is `awaiting` until every signer has signed, then
flips to `complete`; a founder/editor can `cancel` it early, or `remind` unsigned signers (which
**rotates** their tokens — the old link stops working, see §7).

---

## 1. `POST /documents/files/{file_id}/signature-requests` — send for signature

**Editor only** (founder/team_member) — `403 FORBIDDEN` for any other role (§10).

Request body:

```json
{ "signers": [{ "email": "string", "name": "string?" }], "title": "string?", "expires_in_days": 14 }
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `signers` | yes, ≥1 | — | Array of `{email, name?}`. Empty array → `422 VALIDATION_ERROR` (§9). `name` pre-fills the typed-name field on the signing page but is never enforced against what the signer actually types (§4). |
| `title` | no | the file's `filename` | Shown to the signer and on the founder's request list. |
| `expires_in_days` | no | `14` | Days until every unsigned link in this request stops working. Unlike Sharing (Slice 3, default 30d, `0`/`null` = never expires), e-signature's default is **14 days** and this journey did not exercise a "never expires" request — treat `expires_in_days: 0`/`null` as unverified for this endpoint until you've checked `app/services/documents/signatures.py::create_request` or asked backend to capture it. |

**Request** (this journey's create call, two signers):

```json
POST /api/v1/documents/files/067b0aad-43c8-469e-a02f-ca580508c9ce/signature-requests
{
  "signers": [
    { "email": "delivered+signer-one-9c893248fbd4@resend.dev", "name": "Ada Investor" },
    { "email": "delivered+signer-two-505c5afa4fd5@resend.dev", "name": "Bello Legal" }
  ],
  "title": "Investor Agreement"
}
```

**Response — 201** (`e2e/_captures/documents/signature_create.json`):

```json
{
  "data": {
    "id": "2fda1993-ea44-4834-abe0-0a5da875652f",
    "title": "Investor Agreement",
    "status": "awaiting",
    "file_id": "067b0aad-43c8-469e-a02f-ca580508c9ce",
    "filename": "investor-agreement.pdf",
    "signed_count": 0,
    "total": 2,
    "expires_at": "2026-10-03T10:10:27.190291+00:00",
    "completed_at": null,
    "created_at": "2026-09-19T10:10:27.186441+00:00",
    "signers": [
      {
        "email": "delivered+signer-one-9c893248fbd4@resend.dev",
        "name": "Ada Investor",
        "position": 0,
        "status": "pending",
        "signed_at": null,
        "signed_name": null
      },
      {
        "email": "delivered+signer-two-505c5afa4fd5@resend.dev",
        "name": "Bello Legal",
        "position": 1,
        "status": "pending",
        "signed_at": null,
        "signed_name": null
      }
    ],
    "signer_links": [
      "http://localhost:3000/sign/-5ApVpq-Lkl6wkVHpnRo2AcZPZ5Tqzc5yGcFFc8clrY",
      "http://localhost:3000/sign/rWw0hICPo7FsnAOh3ooWbASsjob0fB98PJT1YaNyD0c"
    ]
  },
  "meta": null
}
```

**`signer_links` is returned ONLY on this create call — one entry per signer, same order as the
request's `signers` array (by `position`). It is never present on `GET
/documents/signature-requests` (§6) or `GET /documents/signature-requests/{id}` (§6b) — compare
`signature_create.json` above (has `signer_links`) against `signature_get.json`/`signature_list.json`
below (do not).** The raw tokens are not persisted server-side (only their hash is), so there is no
way to reconstruct these links later short of `remind` minting fresh ones (§7). **If your "Send for
signature" flow wants to show/copy a signer's link immediately after create, hold onto this
response's `signer_links` client-side — a page refresh loses it.**

The same links are also emailed to each signer (subject *"Signature requested: {title}"*, an
`<a href>` pointing at the exact link — captured verbatim in
`e2e/_captures/documents/signature_email.json`) — confirmed live in this journey by reading the link
back out of the captured signature email and asserting it equals `signer_links[i]`. The FE does not
need to send its own email.

**✅ FE-origin fix (2026-09-19), same as Slice 3's Sharing:** `signer_links` are now built as
`{APP_BASE_URL}/sign/{token}` — the **FRONTEND** origin, not the API host — falling back to
`SERVER_HOST` only when `APP_BASE_URL` is unset. In this local e2e run `APP_BASE_URL=http://localhost:3000`
(hence the captured links above); in staging/prod it is the FE origin (`https://app.cofoundaz.com`).
So each signing link opens **your** app at **`/sign/:token`**, and that FE page calls `GET /sign/{token}`
(§3) and `POST /sign/{token}` (§4). **This is the route you must build** — read §3–§4 for the two
calls it makes. See the Sharing guide §6 for the shared `APP_BASE_URL` deploy requirement.

---

## 2. The per-signer secure-link flow (what the FE builds against `/sign/{token}`)

Each signer's flow is two calls against the **same public route family**, no auth, ever:

1. `GET /sign/{token}` — fetch what to show (file + request + the signer's own identity). Safe to
   call repeatedly; does not consume the link.
2. `POST /sign/{token}` — submit the typed-name signature. **Consumes** the link — a second `GET`
   or `POST` on the same token 404s immediately after (§3's step 6b capture).

There is no separate "decline" action in v1 (see the SOP Follow-ups) — a signer either signs or
simply never visits the link (it eventually expires per `expires_in_days`).

---

## 3. `GET /sign/{token}` — public signing view (no auth)

**Zero credentials** — no `Authorization` header, no `X-Workspace-Id`. This is what the emailed
link (§1) points at.

**Response — 200** (`e2e/_captures/documents/signature_sign_view.json`, first signer, before
signing):

```json
{
  "data": {
    "request": { "title": "Investor Agreement", "status": "awaiting" },
    "file": {
      "id": "067b0aad-43c8-469e-a02f-ca580508c9ce",
      "filename": "investor-agreement.pdf",
      "content_type": "application/pdf",
      "size_bytes": 47,
      "folder": null,
      "url": "var/storage/documents/b55d87ab-83ee-441c-b01b-9d7213321fd7/d05df11f91a040258a741ebf696ba2f4.pdf",
      "uploaded_at": "2026-09-19T10:10:27.175617+00:00"
    },
    "signer": {
      "email": "delivered+signer-one-9c893248fbd4@resend.dev",
      "name": "Ada Investor"
    }
  },
  "meta": null
}
```

**Field-nesting trap:** `file` here is the exact same shape `serialize_file` returns everywhere
else in Documents (Slice 2's `GET /documents/files/{id}`) — **`url`** is whatever the active
`Storage` backend produced (a local filesystem path in this e2e capture, since the runner sets no
`STORAGE_BACKEND`; a real Cloudinary URL in staging/production, per Slice 2's FE guide). Render the
file the same way your Slice 2 file viewer does; don't special-case this response's `file.url`.
`request.status` here is the **derived** status (§6) — `"awaiting"` — not the raw enum column; it
can also read `"expired"` for a signer whose link is technically un-consumed but past
`expires_at`, though in that case `GET /sign/{token}` itself already 404s (§5) before the FE would
ever see `status: "expired"` from this particular endpoint. `signer` is this token's own identity
only (`email`/`name` — never any other signer's), so the FE can greet "Hi Ada" without a separate
lookup.

---

## 4. `POST /sign/{token}` — public signature submission (no auth)

Body:

```json
{ "typed_name": "string" }
```

`typed_name` must be non-empty after `.strip()` — a blank/whitespace-only value → `422
VALIDATION_ERROR` (not exercised live; same shape as every other 422 in this API, enforced in
`app/api/v1/endpoints/documents.py::sign_endpoint` before the service layer is even called).

**Request** (first signer, this journey):

```json
POST /api/v1/sign/-5ApVpq-Lkl6wkVHpnRo2AcZPZ5Tqzc5yGcFFc8clrY
{ "typed_name": "Ada Investor" }
```

**Response — 200, first of two signers** (`e2e/_captures/documents/signature_sign_first.json`) —
the request **stays `awaiting`**, `signed_count` moves to 1:

```json
{
  "data": {
    "id": "2fda1993-ea44-4834-abe0-0a5da875652f",
    "title": "Investor Agreement",
    "status": "awaiting",
    "file_id": "067b0aad-43c8-469e-a02f-ca580508c9ce",
    "filename": "investor-agreement.pdf",
    "signed_count": 1,
    "total": 2,
    "expires_at": "2026-10-03T10:10:27.190291+00:00",
    "completed_at": null,
    "created_at": "2026-09-19T10:10:27.186441+00:00"
  },
  "meta": null
}
```

**Response — 200, second (last) of two signers**
(`e2e/_captures/documents/signature_sign_second.json`) — the request flips to **`complete`**,
`completed_at` is now set:

```json
{
  "data": {
    "id": "2fda1993-ea44-4834-abe0-0a5da875652f",
    "title": "Investor Agreement",
    "status": "complete",
    "file_id": "067b0aad-43c8-469e-a02f-ca580508c9ce",
    "filename": "investor-agreement.pdf",
    "signed_count": 2,
    "total": 2,
    "expires_at": "2026-10-03T10:10:27.190291+00:00",
    "completed_at": "2026-09-19T10:10:27.249267+00:00",
    "created_at": "2026-09-19T10:10:27.186441+00:00"
  },
  "meta": null
}
```

**Neither `POST /sign/{token}` response includes the `signers[]` roster.** This is a deliberate
privacy scoping (fixed in this slice's final review): the public, unauthenticated `/sign/{token}`
endpoints return only the request summary — `id`/`title`/`status`/`file_id`/`filename`/
`signed_count`/`total`/`expires_at`/`completed_at`/`created_at` — never the per-signer array, so a
signer who has no login and no workspace membership cannot read another co-signer's email or name
off their own signing confirmation. That's still enough for a signing-confirmation page to show "2
of 2 signed — complete!" (or "1 of 2 signed — waiting on someone else") purely from `signed_count`/
`total`/`status`. The **full** shape with `signers[]` is returned only by the authenticated,
workspace-scoped `GET /documents/signature-requests` / `GET /documents/signature-requests/{id}`
(§9) — and by the create response (§1) for the founder who just sent the request. **`signer_links`
is absent here too** (§1) — the signing page cannot use this response to discover other signers'
links.

**Immediately re-opening the same (now-signed) token 404s**
(`e2e/_captures/documents/signature_sign_after_signed_404.json`, `GET /sign/{token}` on the
first signer's already-consumed token):

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

**A signing link is single-use.** Do not build a "review your signature" page that re-fetches
`GET /sign/{token}` after signing — it will 404. Show the confirmation from the `POST` response
itself (above), or from `GET /documents/signature-requests/{id}` for a founder view.

---

## 5. Uniform 404 on `/sign/{token}` — five causes, one shape

`GET`/`POST /sign/{token}` return the **exact same** `404 NOT_FOUND` body for every one of these
causes — the API gives no signal which applies:

| Cause | Live capture |
|---|---|
| Token never existed / typo | ⚠️ unit only — `tests/api/test_signatures.py::test_unknown_sign_token_404` |
| Signer already signed (link consumed) | ✅ `signature_sign_after_signed_404.json` |
| Request was cancelled | ✅ `signature_sign_after_cancel_404.json` (§8) |
| Request is `complete` (a *different* signer's stale link, if it were somehow re-shared) | ⚠️ unit only — `tests/services/documents/test_signatures.py::test_open_unknown_expired_cancelled_signed_all_404` |
| Request's `expires_at` has passed (`awaiting` + past-due) | ⚠️ unit only — same test as above |

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

**Build exactly one FE state for this** — "This signing link is no longer valid." — the same
uniform-404 design as Slice 3's Sharing (`GET /shared/{token}`). Don't try to distinguish
"expired" from "already signed" from "cancelled" copy; the API deliberately gives no signal to do
so (an attacker probing tokens learns nothing from the response shape), and building UI text that
implies otherwise will eventually be wrong.

---

## 6. Derived `status`, `signed_count`/`total` — read this before rendering a status pill

`status` on a signature request is **derived**, not a raw enum passthrough — computed by
`request_status()` in `app/services/documents/signatures.py`:

| `status` value | Meaning | How it's derived |
|---|---|---|
| `"awaiting"` | Sent, not yet fully signed, not past its expiry | raw column value, `expires_at` still in the future (or `null`) |
| `"complete"` | Every signer has signed | raw column value, set the instant the last signer signs |
| `"cancelled"` | A founder/editor cancelled before completion | raw column value |
| `"expired"` | **Derived, not a stored value** — the raw column is still `"awaiting"` but `expires_at` has passed | computed on every read; nothing flips the DB row when expiry passes, it's evaluated live each time `request_status()`/`serialize_request()` runs |

**`expired` never appears as a raw column value — never filter/compare against it in a database
query if you ever build one; it only exists at serialization time.** This journey did not exercise
the expired path live (needs a manipulated clock — see `tests/services/documents/
test_signatures.py::test_request_status_derives_expired`, unit only) — treat the shape as
type-correct (`status: "expired"`, same envelope) but not independently live-verified for this
slice.

`signed_count`/`total` are always consistent with the per-signer `signers[].status` array
(`"signed"`/`"pending"`) — render the comp's "N of M signed" pill straight from `signed_count`/
`total`, and the individual signer checkmarks from `signers[].status`, not by counting
`signed_at !== null` client-side (same data, but don't duplicate the derivation).

---

## 7. `POST /documents/signature-requests/{id}/remind` — re-email unsigned signers

**Editor only.** No request body. **This is NOT captured live in this journey** — exercised only
in `tests/api/test_signatures.py::test_remind_rotates_and_new_link_works` — but the behavior is
important enough to call out explicitly because it changes what "resend" means compared to
Sharing's model:

**Remind ROTATES every still-unsigned signer's token and mints a fresh link — it does not resend
the original link.** `app/services/documents/signatures.py::reissue_unsigned` generates a brand
new `secrets.token_urlsafe(32)` per unsigned signer and overwrites `token_hash`; the raw token from
the original `POST .../signature-requests` create call (§1) **stops working the moment remind
runs**, even if that signer never clicked it. Already-signed signers are untouched (their link was
already consumed anyway).

Response shape (from the unit test, not a live capture): `{"reminded": <count>}` — the count of
unsigned signers whose token was rotated and re-emailed.

**FE consequence:** if your UI shows a "Copy link" button next to a pending signer (sourced from
the one-time `signer_links` at create time, §1, held in client state), that copied link is now
stale after a remind — there's no way for the FE to fetch the new one except through the email that
was just sent. Don't cache `signer_links` past a remind action; treat a "Remind" click as
invalidating any link you were holding for that request's unsigned signers.

---

## 8. `POST /documents/signature-requests/{id}/cancel` — cancel before completion

**Editor only.** No request body. Sets the request to `cancelled`; every signer's link (signed or
not) subsequently 404s via the uniform shape (§5).

**Request/response — 200** (`e2e/_captures/documents/signature_cancel.json`, cancelling the
one-signer "NDA" request created alongside this journey's `signature_create_for_cancel.json`):

```json
{ "data": { "cancelled": true }, "meta": null }
```

**Cancelling a request that's already `complete`** → `409 SIGNATURE_NOT_ACTIVE` (not exercised
live — same guard as `remind` on a non-active request — `app/services/documents/
signatures.py::cancel_request`/`reissue_unsigned` both call the same `_active()` check). See §9.

**The cancelled signer's link now 404s** (`e2e/_captures/documents/
signature_sign_after_cancel_404.json`):

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

There is no "undo cancel" — a founder who cancels by mistake must create a new request.

---

## 9. Reading requests back — `GET /documents/signature-requests` and `.../{id}`

Both are **workspace-scoped, member-readable, and never carry `signer_links`** (§1).

### 9a. `GET /documents/signature-requests/{id}` — one request, full detail

**Response — 200**, after both signers above have signed
(`e2e/_captures/documents/signature_get.json`):

```json
{
  "data": {
    "id": "2fda1993-ea44-4834-abe0-0a5da875652f",
    "title": "Investor Agreement",
    "status": "complete",
    "file_id": "067b0aad-43c8-469e-a02f-ca580508c9ce",
    "filename": "investor-agreement.pdf",
    "signed_count": 2,
    "total": 2,
    "expires_at": "2026-10-03T10:10:27.190291+00:00",
    "completed_at": "2026-09-19T10:10:27.249267+00:00",
    "created_at": "2026-09-19T10:10:27.186441+00:00",
    "signers": [
      {
        "email": "delivered+signer-one-9c893248fbd4@resend.dev",
        "name": "Ada Investor",
        "position": 0,
        "status": "signed",
        "signed_at": "2026-09-19T10:10:27.226000+00:00",
        "signed_name": "Ada Investor"
      },
      {
        "email": "delivered+signer-two-505c5afa4fd5@resend.dev",
        "name": "Bello Legal",
        "position": 1,
        "status": "signed",
        "signed_at": "2026-09-19T10:10:27.249267+00:00",
        "signed_name": "Bello Legal"
      }
    ]
  },
  "meta": null
}
```

### 9b. `GET /documents/signature-requests` — workspace list

**Response — 200**, same point in the journey (`e2e/_captures/documents/signature_list.json`,
trimmed here to the one relevant row — the live capture may contain more from other tests running
against the same ephemeral database, filter client-side on `id` if you diff against this file):

```json
{
  "data": {
    "requests": [
      {
        "id": "2fda1993-ea44-4834-abe0-0a5da875652f",
        "title": "Investor Agreement",
        "status": "complete",
        "file_id": "067b0aad-43c8-469e-a02f-ca580508c9ce",
        "filename": "investor-agreement.pdf",
        "signed_count": 2,
        "total": 2,
        "expires_at": "2026-10-03T10:10:27.190291+00:00",
        "completed_at": "2026-09-19T10:10:27.249267+00:00",
        "created_at": "2026-09-19T10:10:27.186441+00:00",
        "signers": [
          { "email": "delivered+signer-one-9c893248fbd4@resend.dev", "name": "Ada Investor", "position": 0, "status": "signed", "signed_at": "2026-09-19T10:10:27.226000+00:00", "signed_name": "Ada Investor" },
          { "email": "delivered+signer-two-505c5afa4fd5@resend.dev", "name": "Bello Legal", "position": 1, "status": "signed", "signed_at": "2026-09-19T10:10:27.249267+00:00", "signed_name": "Bello Legal" }
        ]
      }
    ]
  },
  "meta": null
}
```

**This list is NOT summary-shaped — it includes the full `signers` array on every row**, unlike
Slice 1's `GET /documents` (which strips `sections` for the list view). The comp's "E-signature
requests" table (title / sent date / `signedCount` of `total` / status pill / per-signer marks) can
render entirely off this one call — no need to fetch each request individually to show the
per-signer signed/unsigned dots in the list.

---

## 10. Audit trail — captured server-side, not exposed in any response today

Per the design (D2), every signature records a typed-name **audit trail**: `signed_name` (what the
signer typed — returned, see `signers[].signed_name` above), `signed_at` (returned), and
**`signed_ip` + `signed_user_agent` — captured (`POST /sign/{token}` passes `request.client.host`
and the `User-Agent` header into `record_signature`) but NEVER serialized into any API response.**
`serialize_request()`'s per-signer dict only emits `email`/`name`/`position`/`status`/`signed_at`/
`signed_name` — confirmed by reading `app/services/documents/signatures.py::serialize_request`
directly, and indirectly by every capture in this guide (no response above contains `signed_ip` or
`signed_user_agent` anywhere). **If the comp or a future screen needs to show "Signed from IP
x.x.x.x" or a device/browser string for compliance purposes, that requires a new field on
`serialize_request` — it is not something the FE can get today by any existing call.** Don't build
that UI against an assumption it's already there.

---

## 11. Legal caveat — surface this in the UI, not just in a footnote

**This is a simple electronic signature, not a certificate-based or notarized one.** A signature
here is: the signer clicked a unique emailed link and typed their name. There is no identity
verification beyond "this person controls this email inbox," no cryptographic signing
certificate, and no notarization. This is a legally valid *simple electronic signature* under
ESIGN/UETA in most US contexts (and broadly similar frameworks elsewhere) for many document types,
but it is **not equivalent to** a DocuSign/Adobe Sign certificate-based signature or an in-person
notarized wet signature, and is not suitable for document types that require one of those (varies
by jurisdiction and document type — this guide is not legal advice; the design doc names a future
third-party-provider integration as the path to certificate-based signing, not yet built — see the
SOP Follow-ups).

**FE requirement:** the signing page (`GET`/`POST /sign/{token}`, §3–§4) and the "Send for
signature" modal (§1) should both carry visible copy to this effect — e.g. "By typing your name
below, you agree this constitutes your electronic signature" on the signing page, and "This is a
simple electronic signature, not a notarized or certificate-based signature" as a modal footnote
on send. This is a UX/legal decision the backend cannot enforce — the API will happily record a
typed-name signature regardless of what the FE tells the signer beforehand.

---

## 12. Errors

Standard envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | — | Missing/invalid access token on any of the 5 workspace-scoped routes | not captured in this journey — same auth dependency as every other module |
| 403 | `FORBIDDEN` | Non-editor (e.g. `mentor`) calls `POST .../signature-requests`, `.../remind`, or `.../cancel` | not captured live — `tests/api/test_signatures.py::test_mentor_cannot_create_403` |
| 404 | `NOT_FOUND` | Unknown/cross-tenant `file_id` on create; unknown/cross-tenant `request_id` on get/remind/cancel; unknown, already-signed, cancelled, complete, or expired token on `GET`/`POST /sign/{token}` (§5) | `signature_sign_after_signed_404.json`, `signature_sign_after_cancel_404.json` (both live) |
| 409 | `SIGNATURE_NOT_ACTIVE` | `remind` or `cancel` on a request that's already `complete` or `cancelled` | not captured live — `tests/services/documents/test_signatures.py::test_cancel_complete_request_409` (cancel-side; remind shares the same `_active()` guard, not separately unit-tested for the 409 path) |
| 422 | `VALIDATION_ERROR` | `signers: []` on create; blank/whitespace-only `typed_name` on `POST /sign/{token}` | `tests/api/test_signatures.py::test_create_requires_signer_422` (create-side, live-equivalent shape confirmed via unit test); blank `typed_name` not separately unit-tested but enforced identically in `sign_endpoint` before any service call |

---

## 13. Verification table

All rows below except those marked "unit only" were exercised **live**, over real HTTP, against a
real Postgres-backed server (`scripts/e2e_run.sh`,
`e2e/test_documents.py::test_documents_esignature_journey`) — not just unit-tested in-process — and
every response body is captured verbatim in the named file.

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /documents/files/{id}/signature-requests` — 2 signers, 201, response includes `signer_links` (once) | ✅ | `signature_create.json` |
| `signer_links[i]` is the same value actually emailed to `signers[i].email` (read back out of the file mail dir) | ✅ | `e2e/test_documents.py::test_documents_esignature_journey` (asserts `_latest_sign_link(...) == signer_links[i]` for both signers); emailed body captured verbatim in `signature_email.json` |
| `signer_links` use the **FE origin** (`APP_BASE_URL`), not the API host — open `/sign/:token` | ✅ | `signature_create.json` / `signature_email.json` (`http://localhost:3000/sign/...`, the e2e `APP_BASE_URL`); unit: `tests/api/test_signatures.py::test_signer_links_use_app_base_url_frontend_origin` |
| `remind`'s re-emailed link also uses the FE origin (`APP_BASE_URL`) | ⚠️ unit only | `tests/api/test_signatures.py::test_remind_email_link_uses_app_base_url_frontend_origin` |
| `GET /sign/{token}` — public, **no auth header at all**, returns file + request + signer identity | ✅ | `signature_sign_view.json`, `signature_sign_view_second.json` |
| `POST /sign/{token}` — first of two signers — request stays `awaiting`, `signed_count: 1` | ✅ | `signature_sign_first.json` |
| `POST /sign/{token}` — last signer — request flips to `complete`, `completed_at` set | ✅ | `signature_sign_second.json` |
| Re-opening an already-signed token → uniform `404 NOT_FOUND` | ✅ | `signature_sign_after_signed_404.json` |
| `GET /documents/signature-requests/{id}` — full detail, `signed_count: 2`/`total: 2`, `status: complete` | ✅ | `signature_get.json` |
| `GET /documents/signature-requests` — workspace list shows the same 2/2 complete row, full `signers` array (not summary-shaped) | ✅ | `signature_list.json` |
| `POST .../cancel` — `{cancelled: true}` | ✅ | `signature_cancel.json` |
| Cancelled request's signer link → uniform `404 NOT_FOUND` | ✅ | `signature_sign_after_cancel_404.json` |
| `signer_links` absent from get/list/sign responses | ✅ | `signature_get.json`, `signature_list.json`, `signature_sign_first.json`, `signature_sign_second.json` (none contain the key) |
| `signers[]` roster absent from both `POST /sign/{token}` responses (privacy scoping — a signer cannot see co-signers' emails/names) | ✅ | `signature_sign_first.json`, `signature_sign_second.json` (neither contains the key; full shape only on `signature_create.json`, `signature_get.json`, `signature_list.json`) |
| Unknown token (never existed) → same uniform `404 NOT_FOUND` | ⚠️ unit only | `tests/api/test_signatures.py::test_unknown_sign_token_404` |
| Expired token (past `expires_at`, never signed/cancelled) → same uniform `404 NOT_FOUND` | ⚠️ unit only | `tests/services/documents/test_signatures.py::test_open_unknown_expired_cancelled_signed_all_404` |
| `status: "expired"` derivation on a request whose clock has passed `expires_at` | ⚠️ unit only | `tests/services/documents/test_signatures.py::test_request_status_derives_expired` |
| `remind` rotates unsigned signers' tokens; the OLD link 404s afterward | ⚠️ unit only | `tests/api/test_signatures.py::test_remind_rotates_and_new_link_works`, `tests/services/documents/test_signatures.py::test_reissue_unsigned_rotates_tokens` |
| `cancel`/`remind` on an already-`complete` request → `409 SIGNATURE_NOT_ACTIVE` | ⚠️ unit only | `tests/services/documents/test_signatures.py::test_cancel_complete_request_409` |
| `signers: []` on create → `422 VALIDATION_ERROR` | ⚠️ unit only | `tests/api/test_signatures.py::test_create_requires_signer_422` |
| Non-editor (`mentor`) → `403 FORBIDDEN` on create | ⚠️ unit only | `tests/api/test_signatures.py::test_mentor_cannot_create_403` |
| Cross-tenant cancel (real request, different startup) → `404` | ⚠️ unit only | `tests/api/test_signatures.py::test_cross_tenant_cancel_404` |
| `GET /documents/signature-requests` route not shadowed by `GET /documents/{document_id}` | ⚠️ unit only | `tests/api/test_signatures.py::test_signature_requests_not_shadowed_by_document_id` |
| `signed_ip`/`signed_user_agent` captured server-side but never serialized in any response | ✅ (absence confirmed across every capture in this guide) + code-read | §10 above; `app/services/documents/signatures.py::serialize_request` |

The ⚠️ rows are genuine gaps in this journey (a second-role/second-tenant/expired-clock/remind/
already-complete setup was judged not worth the added journey complexity and runtime for one live
run) rather than unexercised guesses — each is backed by a passing test at the cited path, not
merely inferred from source.
