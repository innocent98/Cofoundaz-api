# FE Integration Guide — Legal Advisor Requests (Module 13, Slice 2)

> **Provenance.** Every response body here is pasted verbatim from a live capture
> (`e2e/test_legal_advisor_requests.py` → `e2e/_captures/legal_advisor_requests/`, run 2026-11-21
> against a real uvicorn server on an isolated `cofoundaz_e2e` database migrated from zero to
> `0110_legal_advisor_requests`). IDs, names and timestamps are **placeholders from that run**.
> Behaviour the live run did not exercise is labelled inline and listed in §9.

Base path: `/api/v1/legal`. Every route needs a Bearer access token and an `X-Workspace-Id`
header (`GET /auth/me` → `data.active_workspace_id`). There are **no public routes**.

**Access — and this module has two response shapes, not one:**

| Role | List / read / thread | Create, cancel | Return a draft |
|---|---|---|---|
| founder, team_member | ✅ **every** request, full shape | ✅ | **403** |
| **legal_advisor** | ✅ **only requests naming them**, narrow shape | **403** | ✅ |
| mentor, accountant, business_consultant, investor | **403** | **403** | **403** |

Unauthenticated → **401**. Unverified email → **403** `EMAIL_NOT_VERIFIED`.

> The advisor's list is **scoped**, not filtered by the client. A second advisor in the same
> workspace sees an empty list, not someone else's requests. Do not build a client-side filter
> and assume the server sent everything.

---

## 0. Seven things the mock prototype will mislead you about

The prototype at `app/(dashboard)/legal/requests/` is a mock: seeded `useState`, no network, and
"New request" only fires a toast.

**1. A request needs a document and an advisor. The mock asks for neither.** Its dialog collects
a title and a description. The API requires `advisor_user_id` **and** exactly one of
`document_id` / `file_id`. There is no way to raise a request without a real document.

**2. `title` and `subtitle` are `subject` and `note`.** `note` is optional and nullable.

**3. Status values are lowercase with underscores.** `requested` · `in_review` · `returned` ·
`cancelled`. Not `"In review"`. Map to display text yourself.

**4. There is a fourth status the mock has no design for: `cancelled`.**

**5. `statusBg` does not exist.** Colour is a front-end decision.

**6. Nothing in the mock reflects who is looking.** The same endpoint returns a **different
shape** to a founder and to an advisor (§3). Write two renderers, or one that tolerates both.

**7. `author.name` can be `null`; `requested_by.name` never is.** The request serializer
substitutes `"A team member"` when a user has no profile name; the comment serializer, shared
with Documents and Team, returns the raw value and so can be `null`. Render a fallback for
comment authors.

---

## 1. Finding the advisors

There is no legal-specific endpoint for this. Use the members list, which carries each role:

### `GET /api/v1/team/members` → **200**

Founder and team member only — exactly who raises a request. `data` is a **bare list**, not an
object.

```json
{
  "data": [
    {
      "user_id": "3f5c2f74-e9b1-4938-8ab5-da65bf8a4536",
      "name": "Ada Founder",
      "email": "delivered+e2e-0dacb0849714@resend.dev",
      "role": "founder",
      "joined_at": "2026-11-21T23:03:55.875648+00:00",
      "last_active_at": "2026-11-21T23:03:49.254874+00:00"
    },
    {
      "user_id": "8a106b45-e8df-4031-a958-5c65e764f2fd",
      "name": null,
      "email": "delivered+legal_advisor-13cefd42c3f0@resend.dev",
      "role": "legal_advisor",
      "joined_at": "2026-11-21T23:04:19.106902+00:00",
      "last_active_at": "2026-11-21T23:04:18.330928+00:00"
    },
    {
      "user_id": "ef3988fe-b66a-47e0-a05b-c028bf003590",
      "name": null,
      "email": "delivered+legal_advisor-ac631bc52774@resend.dev",
      "role": "legal_advisor",
      "joined_at": "2026-11-21T23:04:09.814732+00:00",
      "last_active_at": "2026-11-21T23:04:08.819114+00:00"
    },
    {
      "user_id": "1bb51581-2fca-40dc-8731-f139c7cca1ca",
      "name": null,
      "email": "delivered+mentor-f356fc165aa1@resend.dev",
      "role": "mentor",
      "joined_at": "2026-11-21T23:04:21.972907+00:00",
      "last_active_at": "2026-11-21T23:04:21.193154+00:00"
    }
  ],
  "meta": null
}
```

Note both advisors have `name: null` here. That is why the picker should fall back to the email
or a placeholder, and it is the same nullable name that reaches comment authors (§0, point 7).

Filter to `role === "legal_advisor"` for the picker. Note this list **does** carry email
addresses, which is exactly why it is founder + team member only.

---

## 2. Creating a request

### `POST /api/v1/legal/advisor-requests` → **200**

```json
{
  "subject": "Please check clause 7 before we sign",
  "advisor_user_id": "ef3988fe-b66a-47e0-a05b-c028bf003590",
  "file_id": "51ddfce9-af37-4521-ad1c-cb30690a24b8",
  "note": "The non-compete looks far too wide to me."
}
```

`subject` is required (1–200 chars, trimmed). `note` is optional (1–2000, trimmed). Send
**exactly one** of `document_id` or `file_id` — both or neither is a **422**.

```json
{
  "data": {
    "id": "f7b3e12e-0983-4d58-b5a9-f267027eaa1c",
    "subject": "Please check clause 7 before we sign",
    "note": "The non-compete looks far too wide to me.",
    "status": "requested",
    "attachment": {
      "kind": "file",
      "id": "51ddfce9-af37-4521-ad1c-cb30690a24b8",
      "title": "supplier-agreement.pdf"
    },
    "advisor": {
      "id": "ef3988fe-b66a-47e0-a05b-c028bf003590",
      "name": "A team member"
    },
    "returned_file": null,
    "returned_at": null,
    "cancelled_at": null,
    "created_by": {
      "id": "3f5c2f74-e9b1-4938-8ab5-da65bf8a4536",
      "name": "Ada Founder"
    },
    "created_at": "2026-11-21T23:04:28.250334+00:00",
    "updated_at": "2026-11-21T23:04:28.250334+00:00"
  },
  "meta": null
}
```

`attachment.kind` is `"document"` (written in Cofoundaz) or `"file"` (uploaded). Use it instead
of checking which id is null.

### The refusals

| What you sent | Response |
|---|---|
| both `document_id` and `file_id`, or neither | **422** |
| a document or file from another workspace, or a trashed one | **404** |
| a file that has not passed the virus scan | **409** `FILE_NOT_CLEAN` |
| an `advisor_user_id` who is not a live `legal_advisor` here | **422** on that field |

A refused request **creates nothing** — everything is validated before the insert.

---

## 3. The two list shapes

### `GET /api/v1/legal/advisor-requests` → **200**

Optional `?status=`. Newest first.

**As a founder or team member** — every request, with user ids:

```json
{
  "data": {
    "advisor_requests": [
      {
        "id": "f7b3e12e-0983-4d58-b5a9-f267027eaa1c",
        "subject": "Please check clause 7 before we sign",
        "status": "requested",
        "attachment": { "kind": "file", "id": "51ddfce9-af37-4521-ad1c-cb30690a24b8", "title": "supplier-agreement.pdf" },
        "advisor": { "id": "ef3988fe-b66a-47e0-a05b-c028bf003590", "name": "A team member" },
        "created_by": { "id": "3f5c2f74-e9b1-4938-8ab5-da65bf8a4536", "name": "Ada Founder" }
      }
    ]
  },
  "meta": null
}
```

*(abridged to the fields that differ; the full shape is §2's body.)*

**As the advisor** — only their own requests, and **no user ids anywhere**:

```json
{
  "data": {
    "advisor_requests": [
      {
        "id": "f7b3e12e-0983-4d58-b5a9-f267027eaa1c",
        "subject": "Please check clause 7 before we sign",
        "note": "The non-compete looks far too wide to me.",
        "status": "requested",
        "attachment": {
          "kind": "file",
          "id": "51ddfce9-af37-4521-ad1c-cb30690a24b8",
          "title": "supplier-agreement.pdf"
        },
        "returned_file": null,
        "returned_at": null,
        "cancelled_at": null,
        "created_at": "2026-11-21T23:04:28.250334+00:00",
        "updated_at": "2026-11-21T23:04:28.250334+00:00",
        "requested_by": {
          "name": "Ada Founder"
        }
      }
    ]
  },
  "meta": null
}
```

Three differences, all deliberate: **`advisor` is gone** (it is always the viewer), **`created_by`
becomes `requested_by` with a name only**, and **no user id appears anywhere**. Resource ids stay,
because the advisor needs them to fetch the document.

A second advisor in the same workspace sees:

```json
{
  "data": {
    "advisor_requests": []
  },
  "meta": null
}
```

### `GET /api/v1/legal/advisor-requests/{id}` → **200**

Same two shapes. An advisor asking for a request that does not name them gets **404**, not 403 —
a 403 would confirm that someone else's request exists.

---

## 4. Reading the attached document

### `GET /api/v1/legal/advisor-requests/{id}/document` → **200**

Streams the attachment. **The request is the permission** — there is no share link and no token.

- an uploaded **file** streams as itself, with its own content type and filename
- a rich **document** has no bytes, so it comes back as a **PDF**, carrying the
  not-legal-advice footer

Headers: `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`,
`Content-Security-Policy: sandbox; default-src 'none'`, and
`Content-Disposition: attachment; filename="…"`.

> **Fetch it with the bearer token and save the blob.** An `<a href>` cannot send the
> `Authorization` or `X-Workspace-Id` headers, so a plain link will 401. Same pattern as Slice 1's
> proof of filing.

**404** for: a request that does not name you (as an advisor), another workspace, a **cancelled**
request, a trashed attachment, a file that has stopped being `clean`, or a missing stored object.
Treat a 404 here as "not available", not as "something broke".

---

## 5. The comment thread

### `GET /api/v1/legal/advisor-requests/{id}/comments` → **200**

Oldest first. One thread per request.

**As a founder** — author ids present:

```json
{
  "data": {
    "comments": [
      {
        "id": "332b96a2-c0bc-4e43-878d-e35f8e9a8d17",
        "entity_type": "advisor_request",
        "entity_id": "f7b3e12e-0983-4d58-b5a9-f267027eaa1c",
        "anchor": null,
        "author": {
          "id": "ef3988fe-b66a-47e0-a05b-c028bf003590",
          "name": null
        },
        "body": "Clause 7 is unenforceable as drafted. Suggest 12 months, Lagos only.",
        "removed_by_moderation": false,
        "mention_user_ids": [],
        "created_at": "2026-11-21T23:04:29.119429+00:00",
        "edited_at": null,
        "resolved_at": null,
        "is_mine": false
      }
    ]
  },
  "meta": null
}
```

Note `author.name` is **`null`** here: this user has no profile name, and the comment serializer
returns the raw value. Render a fallback. (§0, point 7.)

### `POST /api/v1/legal/advisor-requests/{id}/comments` → **200**

```json
{ "body": "Clause 7 is unenforceable as drafted. Suggest 12 months, Lagos only." }
```

1–5000 characters and must contain a non-space character; `"   "` is a **422**.

**As the advisor**, the response carries a name without an id, and no `mention_user_ids`:

```json
{
  "data": {
    "id": "332b96a2-c0bc-4e43-878d-e35f8e9a8d17",
    "entity_type": "advisor_request",
    "entity_id": "f7b3e12e-0983-4d58-b5a9-f267027eaa1c",
    "anchor": null,
    "body": "Clause 7 is unenforceable as drafted. Suggest 12 months, Lagos only.",
    "removed_by_moderation": false,
    "created_at": "2026-11-21T23:04:29.119429+00:00",
    "edited_at": null,
    "resolved_at": null,
    "is_mine": true,
    "author": {
      "name": null
    }
  },
  "meta": null
}
```

> **The advisor's first comment moves the request to `in_review`.** Nothing says so in this
> response, so **refetch the request** after the advisor's first comment or the status badge will
> be stale. It only ever moves `requested → in_review`: a founder's comment never changes the
> status, and a `returned` request is not dragged backwards.

---

## 6. Returning the reviewed draft

### `POST /api/v1/legal/advisor-requests/{id}/reviewed-draft` → **200**

`multipart/form-data`, field name **`file`**. **The named advisor only** — a founder or team
member gets a **403** here, and another advisor gets a **404**.

This goes through Documents' own upload, so it accepts far more than Slice 1's proof did: **PDF,
Word, Excel, PowerPoint, PNG, JPEG, text, CSV and Markdown**, up to **15 MB**. A lawyer returning
a marked-up `.docx` is the normal case.

The request moves to `returned` and `returned_file` is populated:

```json
{
  "data": {
    "id": "f7b3e12e-0983-4d58-b5a9-f267027eaa1c",
    "subject": "Please check clause 7 before we sign",
    "note": "The non-compete looks far too wide to me.",
    "status": "returned",
    "attachment": {
      "kind": "file",
      "id": "51ddfce9-af37-4521-ad1c-cb30690a24b8",
      "title": "supplier-agreement.pdf"
    },
    "returned_file": {
      "id": "941ca718-33ed-4811-ab55-48ef9e979618",
      "filename": "supplier-agreement-reviewed.docx"
    },
    "returned_at": "2026-11-21T23:04:30.736037+00:00",
    "cancelled_at": null,
    "created_at": "2026-11-21T23:04:28.250334+00:00",
    "updated_at": "2026-11-21T23:04:30.301089+00:00",
    "requested_by": {
      "name": "Ada Founder"
    }
  },
  "meta": null
}
```

**The draft is a new file, not a new version of the original.** An advisor can author no document
version — there is no edit-level share and every document write route is founder + team member —
so the returned draft is an ordinary private file in the Documents library, which the founder
opens through Documents.

A second upload moves the pointer; **earlier drafts are kept**, so nothing the advisor sent is
destroyed.

Refusals: a declared type outside the allowlist, a file over 15 MB, or bytes that contradict the
declared type are all **422**; a draft that fails the virus scan is **409** `FILE_NOT_CLEAN` and
leaves the status alone; a cancelled request is **409** `REQUEST_CANCELLED`.

---

## 7. Cancelling

### `POST /api/v1/legal/advisor-requests/{id}/cancel` → **200**

Founder and team member only. **There is no reopen** — raise a new request instead.

```json
{
  "data": {
    "id": "10d82a0f-7626-44b1-a662-27db8a7980f2",
    "subject": "Check the NDA too",
    "note": null,
    "status": "cancelled",
    "attachment": { "kind": "file", "id": "51ddfce9-af37-4521-ad1c-cb30690a24b8", "title": "supplier-agreement.pdf" },
    "advisor": { "id": "ef3988fe-b66a-47e0-a05b-c028bf003590", "name": "A team member" },
    "returned_file": null,
    "returned_at": null,
    "cancelled_at": "2026-11-21T23:04:31.504846+00:00",
    "created_by": { "id": "3f5c2f74-e9b1-4938-8ab5-da65bf8a4536", "name": "Ada Founder" },
    "created_at": "2026-11-21T23:04:31.104538+00:00",
    "updated_at": "2026-11-21T23:04:31.482531+00:00"
  },
  "meta": null
}
```

**What cancelling does, all at once:** the advisor's document read starts returning 404, new
comments are refused, a reviewed draft is refused — **and the thread stays readable**, because the
history of a cancelled request is closed, not hidden.

Cancelling twice:

```json
{
  "error": {
    "code": "REQUEST_CANCELLED",
    "message": "This request was cancelled, so it can't take new comments.",
    "field_errors": []
  }
}
```

Cancelling a completed review:

```json
{
  "error": {
    "code": "REQUEST_COMPLETE",
    "message": "This review is already complete, so it can't be cancelled.",
    "field_errors": []
  }
}
```

---

## 8. Row 39 — how 13.3 will send a contract to an advisor

**There is no separate endpoint for this, and none is needed.** When AI Contract Review (13.3)
ships, its "Send to my legal advisor" button calls the same
`POST /api/v1/legal/advisor-requests` with the contract under review:

```json
{
  "subject": "Review of Supplier Agreement",
  "advisor_user_id": "<from GET /team/members, role legal_advisor>",
  "file_id": "<the contract being reviewed>",
  "note": "<optional: the findings summary>"
}
```

If the contract is a rich Cofoundaz document, send `document_id` instead. Everything in §2 to §7
then applies unchanged, which is why row 39's API half needed no new code.

---

## 9. Errors and verification

### The two 403 bodies you will see

A mentor, accountant, business consultant or investor on any route, an advisor on create or
cancel, and a founder on reviewed-draft all return:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### Another workspace's id → 404, never 403

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### Verification

| Behaviour | Status |
|---|---|
| Create with a file; both/neither target 422; foreign and trashed 404; unclean file 409 | ✅ verified live (422/404 paths: ⚠️ API tests) |
| Advisor validation: not an advisor, another workspace's advisor, a removed advisor → 422 | ⚠️ API tests only |
| A refused create leaves no row | ⚠️ API tests only |
| List scoped to the advisor; second advisor sees `[]`; founder sees all | ✅ verified live |
| Advisor shape carries no user ids; founder shape does | ✅ verified live |
| `?status=` filter | ⚠️ API tests only |
| Detail read; another advisor 404 | ✅ 404 live · ⚠️ detail shapes API tests |
| Attachment read: file bytes, safe headers, filename | ✅ verified live |
| Attachment read: a rich document rendered to PDF | ⚠️ API tests only |
| Attachment 404 on cancelled, on a blocked file, on a missing object | ✅ cancelled live · ⚠️ others API tests |
| Thread oldest-first; `entity_type` is `advisor_request` | ✅ verified live |
| Advisor's first comment → `in_review`; founder's comment does not move it | ✅ verified live |
| A `returned` request is not dragged back to `in_review` | ⚠️ API tests only |
| Whitespace-only comment 422 | ⚠️ API tests only |
| Reviewed draft: `.docx` accepted, status `returned`, private and scanned | ✅ verified live |
| Reviewed draft: oversized, mismatched bytes, founder 403, another advisor 404 | ⚠️ API tests only |
| A second draft keeps the first | ⚠️ API tests only |
| Cancel from `requested` and `in_review`; 409 twice; 409 after return | ✅ return-then-cancel live · ⚠️ others API tests |
| Cancel ends document access, refuses comments, keeps the thread readable | ✅ verified live |
| Mentor 403; cross-tenant 404 | ✅ verified live |
| Advisor notified per request, copy carries no address | ✅ verified live |
| Unauthenticated → 401 | ⚠️ the API test accepts 401 **or** 403; handle both |

### Capture map

Every body above comes from `e2e/_captures/legal_advisor_requests/` (2026-11-21). Re-run
`e2e/test_legal_advisor_requests.py` against a live server to regenerate.

| Section | Capture |
|---|---|
| `GET /team/members` | `members_with_advisors.json` |
| the contract upload (`POST /documents/files`, Documents' own route) | `contract_uploaded.json` *(evidence only; Documents has its own guide)* |
| `POST /advisor-requests` | `request_created.json` |
| advisor's list | `advisor_list.json` |
| second advisor's empty list | `other_advisor_sees_nothing.json` |
| advisor's comment | `advisor_comment.json` |
| request after the first comment | `request_in_review.json` *(evidence only; identical to §2 with `status: "in_review"`)* |
| founder's thread | `founder_thread.json` |
| reviewed draft returned | `reviewed_draft_returned.json` |
| cancel after return 409 | `cancel_after_return_409.json` |
| cancel | `request_cancelled.json` |
| comment after cancel 409 | `comment_after_cancel_409.json` |
| mentor 403 | `mentor_forbidden.json` |
| cross-tenant 404 | `cross_tenant_404.json` |
| advisor's inbox | `advisor_inbox.json` *(not pasted; §5 covers the notification copy)* |
