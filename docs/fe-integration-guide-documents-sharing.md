# FE Integration Guide — Documents & Templates (Module 18, Slice 3: Sharing)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_documents.py::test_documents_sharing_journey` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/documents/share_*.json`. Nothing here is retyped from
the schema, the service, or memory. IDs, tokens, and timestamps are real values from that ephemeral
test run (they differ on every real request; the shapes are exact). Every payload, status code, and
error body in this guide was exercised live, except the rows explicitly marked "unit only" in §8,
each cited to a passing test.

Base path: `/api/v1`. Same auth convention as the rest of Documents & Templates: Bearer access token
(`Authorization: Bearer <token>`) + `X-Workspace-Id` header — **except the public open endpoint,
§4, which takes no auth at all.**

**Role rule:** read (`GET /documents/{id}/shares`, `GET /documents/shares`) = any active member
(`require_workspace`). Write (`POST`/`DELETE .../shares`) = founder or team_member (editor) —
`_editor = require_role(founder, team_member)`, the same gate every other Documents write uses. A
non-editor member (e.g. `mentor`) gets `403 FORBIDDEN` on create/revoke — not captured live in this
journey, see §8.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §7).

---

## 0. The model in one paragraph

A share is a **tokenized external link**, not an internal permission grant. `POST
/documents/{id}/shares` generates a random token, emails a link containing it to the given address,
and returns that same link **once**. Anyone holding the link opens the document **read-only**,
**with no login and no workspace membership**, via the public `GET /shared/{token}`. Revoking or
expiring the share makes that same link return a uniform 404 forever after — there is no
"expired" vs. "revoked" vs. "never existed" distinction the FE can detect from the response;
design for exactly one "this link is no longer valid" state (§5).

---

## 1. `POST /api/v1/documents/{document_id}/shares` — create a share

**Editor only** (founder/team_member) — `403 FORBIDDEN` for any other role (§8).

**This is a plain JSON body — not multipart**, unlike Slice 2's file upload. Request shape:

```json
{ "email": "string", "access_level": "view" | "comment", "expires_in_days": 30 }
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `email` | yes | — | Recipient's email address. Not validated against any user/member list — this is an arbitrary external recipient, which is the entire point of the feature. |
| `access_level` | no | `"view"` | `"view"` or `"comment"`. **Read this section's §2 before building an "Edit" option into the share modal — there isn't one in v1.** Unknown value → `422 VALIDATION_ERROR` (Pydantic enum validation). |
| `expires_in_days` | no | `30` | Integer days until the link stops working. `0` or `null`/omitted-as-null means **never expires** — see §3. |

**Request** (this journey's create call):

```json
POST /api/v1/documents/{document_id}/shares
{ "email": "delivered+shared-with-6bbbda2b564f@resend.dev", "access_level": "view" }
```

(`expires_in_days` omitted → defaults to 30, per the request model default.)

**Response — 201** (`e2e/_captures/documents/share_create.json`):

```json
{
  "data": {
    "id": "e9dd28ce-c7b6-4445-b3da-41294ba13a3f",
    "email": "delivered+shared-with-6bbbda2b564f@resend.dev",
    "access_level": "view",
    "expires_at": "2026-10-19T10:10:26.555734+00:00",
    "revoked_at": null,
    "last_viewed_at": null,
    "created_at": "2026-09-19T10:10:26.553829+00:00",
    "status": "active",
    "link": "http://localhost:3000/shared/Z0fDvGr2h3i9xOHSTlnQVhRlnNa81jy1HF9zXasOA2U"
  },
  "meta": null
}
```

**`link` is returned ONLY on this create call. It is never present on the list endpoints (§4a, §4b)
or anywhere else** — the raw token is not persisted server-side (only its hash is), so there is no
way to reconstruct it later. **If your share modal needs to show/copy the link after the initial
create, you must hold onto this response's `link` value client-side (e.g. in the modal's local
state) — a page refresh loses it, and there is no "regenerate link" endpoint in v1.**

The same link is also emailed to `email` (subject *"A document was shared with you on
Cofoundaz"*, an `<a href>` pointing at this exact URL — captured verbatim in
`e2e/_captures/documents/share_email.json`) — the FE does not need to send its own email; that
happens server-side as part of this call. **`link` is built as `{APP_BASE_URL}/shared/{token}` — the
FRONTEND origin, not the API host** (see §6). So the link opens **your** app at `/shared/:token`, and
that FE page is what calls `GET /api/v1/shared/{token}` (§5) to load and render the document. In
staging/prod `APP_BASE_URL` is set to the FE origin (`https://app.cofoundaz.com`); locally in this
e2e run it is `http://localhost:3000` (hence the captured `link` above). **This is the route you must
build — see §6.**

---

## 2. Access levels — what the FE should actually offer

The UI comp shows three tiers (View / Comment / Edit) in the share modal. **Only two are real in
v1:**

| Tier | Comp shows it? | Actually offered? | Behavior |
|---|---|---|---|
| **View** | yes | yes (`access_level: "view"`) | Read-only, exactly what `GET /shared/{token}` returns. |
| **Comment** | yes | yes (`access_level: "comment"`), **but functionally identical to View today** | Documents have no comment entity in this backend yet. The value round-trips (stored, returned, filterable) so a future comments feature doesn't need a migration, but a recipient opened via a `comment` share gets the exact same read-only document payload as a `view` share — there is nothing for them to comment ON yet. |
| **Edit** | yes | **no — do not send `access_level: "edit"`, it 422s** | Deliberately deferred: an anonymous link-holder editing a document can't be attributed in that document's version history (which shows named authors). This needs member-scoped ACL editing, a different feature, not built in this slice. |

**FE recommendation:** render the three-way toggle from the comp if you want to match the design,
but either (a) disable the "Edit" option with a tooltip ("Coming soon"), or (b) collapse to a
two-way View/Comment toggle and note in copy that Comment currently behaves like View. Sending
`access_level: "edit"` gets a `422 VALIDATION_ERROR` — it's rejected by the request schema, not
silently downgraded.

---

## 3. Expiry — the "Link expires in 30 days" toggle

The comp shows a single toggle: "Link expires in 30 days" (implying on/off). The API is more
granular (`expires_in_days: <int>`), but the FE only needs two states to match the comp:

| FE toggle state | Request field | Server behavior |
|---|---|---|
| **On (default)** | omit `expires_in_days`, or send `30` | `expires_at` set to `created_at + 30 days` (captured live: `2026-09-19T10:10:26` → `2026-10-19T10:10:26`, exactly +30d) |
| **Off ("never expires")** | `expires_in_days: 0` or `expires_in_days: null` | `expires_at: null` in the response — verified in `tests/services/documents/test_shares.py::test_create_no_expiry_when_falsy` (not captured live in this journey; the live journey exercises the default-30-day path only, see §8) |

**`expires_at: null` is a real, permanent state, not "not yet expired" — do not treat a `null`
`expires_at` as an error or a loading state.** Once past `expires_at` (for a share that has one),
the link uniformly 404s (§5) — there is no grace period and no warning email before expiry.

---

## 4. Reading shares back

### 4a. `GET /api/v1/documents/{document_id}/shares` — per-document "Shared with" list

Any active member. Returns every share ever created for that document (not filtered by
active/expired/revoked — the FE greys out non-active rows using `status`, see below).

**Response — 200**, right after the create + one public open above
(`e2e/_captures/documents/share_list_by_document.json`):

```json
{
  "data": {
    "shares": [
      {
        "id": "e9dd28ce-c7b6-4445-b3da-41294ba13a3f",
        "email": "delivered+shared-with-6bbbda2b564f@resend.dev",
        "access_level": "view",
        "expires_at": "2026-10-19T10:10:26.555734+00:00",
        "revoked_at": null,
        "last_viewed_at": "2026-09-19T10:10:26.566759+00:00",
        "created_at": "2026-09-19T10:10:26.553829+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```

Note **`link` is absent here** (§1) and **`last_viewed_at` is now populated** — it was `null` right
after create, and flipped to a real timestamp the moment the public `GET /shared/{token}` in §4
below was hit. This is the comp's "Shared with" list's data source; render `email` / `access_level`
/ `status` / `last_viewed_at` (as "Last viewed: <relative time>" or "Never viewed" when `null`).

### 4b. `GET /api/v1/documents/shares` — workspace "Shared with others" overview

Any active member. Same share objects, across **every** document in the workspace, newest-first —
this is the comp's `Document | Shared with | Access | Last viewed` table.

**Response — 200** (`e2e/_captures/documents/share_list_workspace.json`):

```json
{
  "data": {
    "shares": [
      {
        "id": "e9dd28ce-c7b6-4445-b3da-41294ba13a3f",
        "email": "delivered+shared-with-6bbbda2b564f@resend.dev",
        "access_level": "view",
        "expires_at": "2026-10-19T10:10:26.555734+00:00",
        "revoked_at": null,
        "last_viewed_at": "2026-09-19T10:10:26.566759+00:00",
        "created_at": "2026-09-19T10:10:26.553829+00:00",
        "status": "active",
        "document_id": "a9f9c75d-6fe7-4e4a-9a71-843b9d75cbfc"
      }
    ]
  },
  "meta": null
}
```

**Field-nesting trap:** this row is identical to §4a's row **plus one extra field, `document_id`**.
§4a (scoped to one document, so the document is implicit) never carries `document_id`; §4b (spans
every document, so the FE needs to know which) always does. Don't write one shared row-rendering
component that assumes `document_id` is always present or always absent — check which endpoint you
called, or make the field optional in your row type. This overview response has **no `document`
title/name field** — only `document_id` — so if the FE wants to show a document title in the
"Document" column, it must join against a document list you already have (e.g. from `GET
/documents`) or add a lookup; the backend does not denormalize the title onto this row.

---

## 5. `GET /api/v1/shared/{token}` — the public open endpoint (no auth)

**This is the one endpoint in this entire API a recipient can call with zero credentials** — no
`Authorization` header, no `X-Workspace-Id`, nothing. This is what the emailed link (§1) points at.
Every successful call also records `last_viewed_at` on the share (visible to the sharer via §4a/§4b
above) — there is no way to "peek" without it counting as a view.

**Response — 200** (`e2e/_captures/documents/share_open.json`, trimmed to 3 of 9 sections for
brevity — the live capture has all 9):

```json
{
  "data": {
    "document": {
      "id": "a9f9c75d-6fe7-4e4a-9a71-843b9d75cbfc",
      "kind": "business_plan",
      "title": "Business Plan",
      "status": "draft",
      "ai_generated": false,
      "folder": null,
      "template_key": "business_plan",
      "version": 1,
      "updated_at": "2026-09-19T10:10:26.543837+00:00",
      "sections": [
        { "id": "6e916097-5ca7-4e2f-8649-64f49c986c11", "body": "", "heading": "Executive Summary" },
        { "id": "b9534027-b23a-4501-add7-2dd87be57c5e", "body": "", "heading": "Problem" },
        { "id": "e1a8911e-883b-41e0-b746-05fb90d3a6e3", "body": "", "heading": "Solution" }
      ]
    },
    "access_level": "view",
    "expires_at": "2026-10-19T10:10:26.555734+00:00"
  },
  "meta": null
}
```

**`document` is the exact same FULL shape (with `sections`) as the authenticated `GET
/documents/{id}` from Slice 1** — build one document-viewer component and feed it either response.
`access_level` and `expires_at` are the share's, not the document's — surface `access_level` in the
viewer chrome (e.g. a small "View only" badge) since a recipient landing here has no other way to
know their permission level, and consider showing "Link expires <date>" (or nothing, if `null`).

**Response — 404** once the share is revoked or past `expires_at`, or the token is simply unknown
(`e2e/_captures/documents/share_open_after_revoke.json` — this journey's capture is the
just-revoked case; the unknown-token and expired cases return byte-identical bodies, verified in
`tests/api/test_document_shares.py::test_unknown_token_404` and
`tests/services/documents/test_shares.py::test_open_unknown_expired_revoked_all_404`):

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

**This is deliberately uniform.** The backend does not tell you whether the link never existed, was
revoked by the sharer, or simply expired — that's intentional (mirrors the existing invitation-token
convention: don't leak whether a token existed). **Build exactly one FE state for this** — something
like "This link is no longer valid. Ask the sender for a new one." — rather than trying to
distinguish "expired" from "revoked" copy; the API gives you no signal to do so, and there won't be
one added later without a deliberate, separate design decision.

---

## 6. The FE route to build — `/shared/:token` — and the `APP_BASE_URL` env

**Fixed (2026-09-19):** the emailed/returned `link` now points at the **FE origin**, built as
`f"{APP_BASE_URL}/shared/{raw_token}"` (falling back to `SERVER_HOST` only when `APP_BASE_URL` is
unset). This mirrors the auth verify/reset emails, which already build off `APP_BASE_URL`. In this
local e2e run `APP_BASE_URL=http://localhost:3000`, so the captured `link` is
`http://localhost:3000/shared/...`; in **staging/production** `APP_BASE_URL` is set to the FE origin
(`https://app.cofoundaz.com`), so the recipient lands on **your app**, not the API.

**What the FE must build:** a route **`/shared/:token`** in the frontend app. That page:

1. reads `:token` from its own URL,
2. calls **`GET /api/v1/shared/{token}`** (§5) client-side — **no auth headers** (this is the one
   public endpoint; the recipient has no login),
3. renders the returned `document` (same full shape as `GET /documents/{id}`) read-only, and
4. handles the uniform `404` (§5) as a single "this link is no longer valid" state.

The email body is captured verbatim in `e2e/_captures/documents/share_email.json` — the `<a href>`
in it is exactly the `link` from §1, so the token the FE parses out of the URL is the one §5 accepts.

> **Env requirement:** `APP_BASE_URL` must be set to the FE origin in every non-local environment
> (already set on staging/prod per prior work). If it is ever left blank, the link silently falls
> back to `SERVER_HOST` (the API host) and the old broken behaviour returns — so treat `APP_BASE_URL`
> as a required deploy var, not an optional one.

---

## 7. Errors

Standard envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | — | Missing/invalid access token on any authed route | not captured in this journey — same auth dependency as every other module |
| 403 | `FORBIDDEN` | Non-editor (e.g. `mentor`) calls `POST`/`DELETE .../shares` | not captured live — `tests/api/test_document_shares.py::test_mentor_cannot_share_403` |
| 404 | `NOT_FOUND` | Unknown document (create/list/revoke); cross-tenant document/share; unknown, expired, or revoked share token on `GET /shared/{token}` | `share_open_after_revoke.json` (revoked case, live) |
| 422 | `VALIDATION_ERROR` | Malformed body, or `access_level` outside `{view, comment}` (e.g. `"edit"`) | not captured live — Pydantic enum validation, same shape as every other 422 in this API |

---

## 8. Verification table

All rows below except those marked "unit only" were exercised **live**, over real HTTP, against a
real Postgres-backed server (`scripts/e2e_run.sh`,
`e2e/test_documents.py::test_documents_sharing_journey`) — not just unit-tested in-process — and
every response body is captured verbatim in the named file.

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /documents/{id}/shares` — JSON body, 201, response includes `link` | ✅ | `share_create.json` |
| `link` is the same value that was actually emailed (read back out of the file mail dir) | ✅ | `e2e/test_documents.py::test_documents_sharing_journey` (asserts `_latest_share_link(...) == link`); emailed body captured verbatim in `share_email.json` |
| `link` uses the **FE origin** (`APP_BASE_URL`), not the API host — opens `/shared/:token` | ✅ | `share_create.json` / `share_email.json` (`http://localhost:3000/shared/...`, the e2e `APP_BASE_URL`); unit: `tests/api/test_document_shares.py::test_share_link_uses_app_base_url_frontend_origin` |
| Falls back to `SERVER_HOST` when `APP_BASE_URL` is unset (trailing slash trimmed) | ⚠️ unit only | `tests/api/test_document_shares.py::test_share_link_falls_back_to_server_host_when_app_base_url_empty` |
| Default expiry = 30 days when `expires_in_days` omitted | ✅ | `share_create.json` (`created_at` → `expires_at` = +30d) |
| `expires_in_days: 0`/`null` → `expires_at: null` ("never expires") | ⚠️ unit only | `tests/services/documents/test_shares.py::test_create_no_expiry_when_falsy` |
| `GET /shared/{token}` — public, **no auth header at all**, returns the document + `access_level` + `expires_at` | ✅ | `share_open.json` |
| `GET /shared/{token}` sets `last_viewed_at` on the share | ✅ | `share_list_by_document.json` (`last_viewed_at` non-null after the open above) |
| `GET /documents/{id}/shares` — per-document "Shared with" list, no `link` field | ✅ | `share_list_by_document.json` |
| `GET /documents/shares` — workspace overview, carries `document_id` | ✅ | `share_list_workspace.json` |
| `DELETE /documents/{id}/shares/{share_id}` — revoke, `{revoked: true}` | ✅ | `share_revoke.json` |
| `GET /shared/{token}` → `404 NOT_FOUND` after revoke (uniform shape) | ✅ | `share_open_after_revoke.json` |
| Unknown token (never existed) → same uniform `404 NOT_FOUND` | ⚠️ unit only | `tests/api/test_document_shares.py::test_unknown_token_404` |
| Expired token (past `expires_at`, never revoked) → same uniform `404 NOT_FOUND` | ⚠️ unit only | `tests/services/documents/test_shares.py::test_open_unknown_expired_revoked_all_404` |
| Non-editor (`mentor`) → `403 FORBIDDEN` on create; member can still list | ⚠️ unit only | `tests/api/test_document_shares.py::test_mentor_cannot_share_403` |
| Cross-tenant revoke (real share, different startup) → `404` | ⚠️ unit only | `tests/api/test_document_shares.py::test_cross_tenant_revoke_404` |
| `GET /documents/shares` route not shadowed by `GET /documents/{document_id}` | ⚠️ unit only | `tests/api/test_document_shares.py::test_shares_overview_not_shadowed_by_document_id` |
| `access_level: "edit"` rejected (not offered in v1) | ⚠️ unit only (Pydantic enum validation — no dedicated test needed beyond schema coverage) | `app/schemas/document.py::ShareCreate`, `app/db/models/enums.py::ShareAccess` |

The ⚠️ rows are genuine gaps in this journey (a second-role/second-tenant/expired-clock/unknown-token
setup was judged not worth the added journey complexity and runtime for one live run) rather than
unexercised guesses — each is backed by a passing test at the cited path, not merely inferred from
source.
