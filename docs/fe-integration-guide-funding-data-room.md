# FE Integration Guide: Funding Hub (Module 14, slice B: the data room)

Every JSON body below is pasted **verbatim** from live captures taken by `e2e/test_funding_data_room.py`
against a real server (`e2e/_captures/funding_data_room/*.json`; the capture name is given above each
block). Nothing is retyped from the schema. IDs, tokens, timestamps and emails are throwaway values from
that run (a local scratch database; the tokens are dead). Where a body was trimmed it says so. **What is
not covered live is marked inline and listed in the verification table at the end.**

Scope: the **Data room** page (`/funding/data-room`) for the founder and the **public investor viewer**
(`/dr/[token]`, own layout, no login). Pipeline is in `fe-integration-guide-funding.md`. Cap table, Term
sheets, Grants, Analytics have no API yet. Funding stays hidden from the sidebar until all slices ship.
Design: `cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md` sections 4 to 6. Calls: D-FUND-3
(investors get in by link + emailed code, **no investor accounts**), D-FUND-8 (downloads off by default,
PDFs always watermarked), D-FUND-L4 (items are references; trashed or unscanned items vanish at once),
D-FUND-L6 (one notification per share per day).

Base path `/api/v1/funding`. Founder routes need `Authorization: Bearer <token>` and `X-Workspace-Id`.
**Public routes (`/dr/...`) need neither** and are the only unauthenticated routes in Funding. Success
bodies are `{"data": ..., "meta": null}`; errors are `{"error": {"code", "message", "field_errors"}}`.

## 1. What it is, in the founder's terms

A founder picks files and documents already in the Documents library, groups them in folders, and sends a
**link** to one investor email, scoped to chosen folders. The investor opens the link, gets a 6-digit code
by email, enters it, and browses a read-only viewer. The founder sees who opened what and for how long, and
can revoke the link at any moment; revocation cuts off a viewer who is mid-document.

## 2. Who can call what

| Who | Founder routes (sections 3 to 5) | Public routes (section 6) |
|---|---|---|
| founder, team_member | allowed | not applicable |
| mentor, business_consultant, accountant, legal_advisor, investor | **403** | not applicable |
| an investor holding a link (no account) | cannot call founder routes | allowed, scoped to that one share |

Plan module `funding` (Scale) is checked after the role: **402** when `PLAN_ENFORCEMENT` is on and the
workspace lacks it. It is off today. *Not captured live (the e2e server runs with enforcement off); shape as
in the pipeline guide.* The role 403s are unit-tested (`tests/api/test_funding_data_room_api.py`), not
captured.

## 3. Founder: structure

`GET /data-room` before anything exists is the **empty state**, not an error. Show "Set up standard
structure".

capture `room_empty`
```json
{
  "data": {
    "set_up": false,
    "standard_folders": [
      "Corporate",
      "Financials",
      "Product",
      "Market",
      "Team",
      "Legal"
    ],
    "folders": [],
    "item_count": 0,
    "shares": []
  },
  "meta": null
}
```

`POST /data-room/setup` creates Corporate, Financials, Product, Market, Team, Legal. Safe to call again
(creates only the missing ones). Trimmed to two folders:

capture `setup`
```json
{
  "data": {
    "set_up": true,
    "standard_folders": [
      "Corporate",
      "Financials",
      "Product",
      "Market",
      "Team",
      "Legal"
    ],
    "folders": [
      {
        "id": "6f6d472f-53d4-4e20-9852-c7a60b217083",
        "name": "Corporate",
        "position": 0,
        "is_standard": true,
        "items": []
      },
      {
        "id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
        "name": "Financials",
        "position": 1,
        "is_standard": true,
        "items": []
      },
      {
        "...": "4 more standard folders: Product, Market, Team, Legal"
      }
    ],
    "item_count": 0
  },
  "meta": null
}
```

`POST /data-room/folders` (`{"name"}`, 201) adds a custom folder; names are unique per workspace,
case-insensitive (409 `FOLDER_EXISTS`). `PATCH /data-room/folders/{id}` takes `name` and/or `position`.
`DELETE /data-room/folders/{id}` removes the folder and its references (never the Documents files); the
response is `{"data": {"deleted": true}, "meta": null}` (capture `folder_deleted`).

capture `folder_created`
```json
{
  "data": {
    "id": "00389fb2-4c0e-455f-9e3e-1118ea07b80f",
    "name": "Board",
    "position": 6,
    "is_standard": false,
    "items": []
  },
  "meta": null
}
```

capture `folder_duplicate_409`
```json
{
  "error": {
    "code": "FOLDER_EXISTS",
    "message": "A folder with that name already exists.",
    "field_errors": []
  }
}
```

Add an item by **reference**: `POST /data-room/folders/{folder_id}/items` with exactly one of
`{"file_id"}` or `{"document_id"}` (a Documents file, or a rich Document). 201:

capture `item_added`
```json
{
  "data": {
    "id": "724306c2-e35b-4914-981f-20c8d226ab9c",
    "folder_id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
    "position": 0,
    "document_id": null,
    "file_id": "936efb03-8267-4543-8c6c-af2128041a00",
    "available": true,
    "kind": "file",
    "name": "pitch-deck.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1371,
    "viewer": "pdf",
    "viewable": true
  },
  "meta": null
}
```

Errors the picker must handle: the same item twice in one folder is 409 `ITEM_EXISTS`; a file that has not
passed the virus scan is **409 `FILE_NOT_CLEAN`** (*unit-tested, not captured*); a trashed item or another
workspace's item is 404; both ids or neither is 422 (*unit-tested*).

capture `item_duplicate_409`
```json
{
  "error": {
    "code": "ITEM_EXISTS",
    "message": "That item is already in this folder.",
    "field_errors": []
  }
}
```

`PATCH /data-room/items/{id}` moves it (`folder_id`, `position`). `DELETE /data-room/items/{id}` removes
the reference (capture `item_removed`: `{"data": {"removed": true}}`).

`GET /data-room` with items, trimmed to the Financials and Board folders. **Trap:** `available` is computed
at read time. When a Documents file is trashed or stops being scan-clean, the item stays in the founder's
list with `available: false` and a `reason` (`trashed`, `scan_pending`, `scan_blocked`, `removed`); show a
warning chip. The investor no longer sees it. *The unavailable shape is unit-tested, not captured.* The
`shares` array of this response is the same as `GET /data-room/shares` (section 4).

capture `room_with_items`
```json
{
  "data": {
    "set_up": true,
    "standard_folders": [
      "Corporate",
      "Financials",
      "Product",
      "Market",
      "Team",
      "Legal"
    ],
    "folders": [
      {
        "id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
        "name": "Financials",
        "position": 1,
        "is_standard": true,
        "items": [
          {
            "id": "724306c2-e35b-4914-981f-20c8d226ab9c",
            "folder_id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
            "position": 0,
            "document_id": null,
            "file_id": "936efb03-8267-4543-8c6c-af2128041a00",
            "available": true,
            "kind": "file",
            "name": "pitch-deck.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1371,
            "viewer": "pdf",
            "viewable": true
          },
          {
            "id": "3c279f72-5ff3-42ec-94c1-7393c8fb8cae",
            "folder_id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
            "position": 1,
            "document_id": null,
            "file_id": "e4b24ef2-945e-4a58-920f-83ca664ff8e2",
            "available": true,
            "kind": "file",
            "name": "model-notes.txt",
            "content_type": "text/plain",
            "size_bytes": 23,
            "viewer": "text",
            "viewable": false
          }
        ]
      },
      {
        "id": "00389fb2-4c0e-455f-9e3e-1118ea07b80f",
        "name": "Board",
        "position": 6,
        "is_standard": false,
        "items": []
      }
    ],
    "item_count": 2,
    "shares": []
  },
  "meta": null
}
```

## 4. Founder: shares (the links)

`POST /data-room/shares`: `email` (required), `folder_ids` (1 to 50 of your folders), optional
`investor_id` (a pipeline card), `allow_download` (default false), `expires_at` or `expires_in_days` (1 to
365; omit for never), `send_email` (default true: the invite goes out). `watermark` is **always true** and
cannot be turned off; it is in the body for display only. **`link` is the URL to copy and it is the same
every time you list the share** (recomputed, so "Copy link" always works while the share is active). `link`
is `null` once the share is revoked or expired.

capture `share_created`
```json
{
  "data": {
    "id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
    "email": "delivered+investor-578313484f65@resend.dev",
    "investor_id": "fc4755b2-d2dc-4e43-87f3-fbec9c222e9e",
    "investor_name": "Tolu Bello",
    "folder_ids": [
      "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8"
    ],
    "allow_download": false,
    "watermark": true,
    "status": "active",
    "expires_at": "2026-12-05T20:07:57.827262+00:00",
    "revoked_at": null,
    "last_viewed_at": null,
    "view_count": 0,
    "total_duration_s": 0,
    "unviewable_items": [
      {
        "item_id": "3c279f72-5ff3-42ec-94c1-7393c8fb8cae",
        "name": "model-notes.txt",
        "code": "VIEW_ONLY_UNSUPPORTED"
      }
    ],
    "link": "http://localhost:3000/dr/apeVwQjDlTOh8aNFvPOXlMTKKLTQd3DqXUwFaeEPMLM",
    "created_at": "2026-11-05T20:07:57.822309+00:00"
  },
  "meta": null
}
```

The invite email, as delivered (file email backend):

capture `email_invite`
```json
{
  "to": "delivered+investor-578313484f65@resend.dev",
  "subject": "Placeholder Co shared their data room with you",
  "html": "<p><strong>Placeholder Co</strong> invited you to their data room on Cofoundaz.</p><p><a href=\"http://localhost:3000/dr/apeVwQjDlTOh8aNFvPOXlMTKKLTQd3DqXUwFaeEPMLM\">Open the data room</a></p><p>You will be asked for a 6-digit code sent to this address. The link only works for this email.</p>",
  "sent_at": "2026-11-05T20:07:57.829337+00:00"
}
```

**Trap, view-only types:** with `allow_download: false` only PDFs, rich Documents and PNG/JPEG images can be
shown (they are watermarked; images are converted to a PDF page). Any other file type (docx, xlsx, pptx, csv,
txt, md) would leave as raw bytes, so the investor gets **422 `VIEW_ONLY_UNSUPPORTED`** for it. `unviewable_items`
lists those items for the scoped folders when the share is created or listed (empty when downloads are on).
Show the founder a warning before they send: "These files can't be shown view-only: upload a PDF or allow
downloads." In the founder's room list every item also carries `viewable` (false for those types).

Validation (empty folder list):

capture `share_validation_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "folder_ids",
        "message": "List should have at least 1 item after validation, not 0"
      }
    ]
  }
}
```

`GET /data-room/shares` lists all shares (`?investor_id=` filters), newest first, with `view_count`,
`total_duration_s` and `last_viewed_at`. That is the data for the pipeline card's "Viewed Pitch deck" chip.
`status` is `active`, `expired` or `revoked`. Trimmed to the first share:

capture `shares_list`
```json
{
  "data": {
    "items": [
      {
        "id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
        "email": "delivered+investor-578313484f65@resend.dev",
        "investor_id": "fc4755b2-d2dc-4e43-87f3-fbec9c222e9e",
        "investor_name": "Tolu Bello",
        "folder_ids": [
          "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8"
        ],
        "allow_download": true,
        "watermark": true,
        "status": "active",
        "expires_at": "2026-12-05T20:07:57.827262+00:00",
        "revoked_at": null,
        "last_viewed_at": "2026-11-05T20:08:00.418634+00:00",
        "view_count": 2,
        "total_duration_s": 2,
        "unviewable_items": [],
        "link": "http://localhost:3000/dr/apeVwQjDlTOh8aNFvPOXlMTKKLTQd3DqXUwFaeEPMLM",
        "created_at": "2026-11-05T20:07:57.822309+00:00"
      }
    ]
  },
  "meta": null
}
```

`PATCH /data-room/shares/{id}` changes `folder_ids`, `allow_download` and the expiry. **Trap:**
`"expires_at": null` clears the expiry (never expires); leaving the field out leaves it untouched. Changes
apply to a viewer who is already inside, immediately.

capture `share_updated`
```json
{
  "data": {
    "id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
    "email": "delivered+investor-578313484f65@resend.dev",
    "investor_id": "fc4755b2-d2dc-4e43-87f3-fbec9c222e9e",
    "investor_name": "Tolu Bello",
    "folder_ids": [
      "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8"
    ],
    "allow_download": true,
    "watermark": true,
    "status": "active",
    "expires_at": "2026-12-05T20:07:57.827262+00:00",
    "revoked_at": null,
    "last_viewed_at": "2026-11-05T20:07:58.299905+00:00",
    "view_count": 1,
    "total_duration_s": 2,
    "unviewable_items": [],
    "link": "http://localhost:3000/dr/apeVwQjDlTOh8aNFvPOXlMTKKLTQd3DqXUwFaeEPMLM",
    "created_at": "2026-11-05T20:07:57.822309+00:00"
  },
  "meta": null
}
```

capture `share_expiry_cleared`
```json
{
  "data": {
    "id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
    "email": "delivered+investor-578313484f65@resend.dev",
    "investor_id": "fc4755b2-d2dc-4e43-87f3-fbec9c222e9e",
    "investor_name": "Tolu Bello",
    "folder_ids": [
      "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8"
    ],
    "allow_download": true,
    "watermark": true,
    "status": "active",
    "expires_at": null,
    "revoked_at": null,
    "last_viewed_at": "2026-11-05T20:08:00.418634+00:00",
    "view_count": 2,
    "total_duration_s": 2,
    "unviewable_items": [],
    "link": "http://localhost:3000/dr/apeVwQjDlTOh8aNFvPOXlMTKKLTQd3DqXUwFaeEPMLM",
    "created_at": "2026-11-05T20:07:57.822309+00:00"
  },
  "meta": null
}
```

`DELETE /data-room/shares/{id}` **revokes** (it returns the share with `status: "revoked"`). It is
immediate. A revoked share cannot be edited or revived (404); create a new one.

capture `share_revoked`
```json
{
  "data": {
    "id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
    "email": "delivered+investor-578313484f65@resend.dev",
    "investor_id": "fc4755b2-d2dc-4e43-87f3-fbec9c222e9e",
    "investor_name": "Tolu Bello",
    "folder_ids": [
      "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8"
    ],
    "allow_download": true,
    "watermark": true,
    "status": "revoked",
    "expires_at": null,
    "revoked_at": "2026-11-05T20:08:00.473577+00:00",
    "last_viewed_at": "2026-11-05T20:08:00.418634+00:00",
    "view_count": 2,
    "total_duration_s": 2,
    "unviewable_items": [],
    "link": null,
    "created_at": "2026-11-05T20:07:57.822309+00:00"
  },
  "meta": null
}
```

capture `revoked_share_edit_404`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 5. Founder: access log and notification

`GET /data-room/access-log` (`?share_id=`, `limit` 1 to 200 default 50, `offset`). One row per document
**opened**, newest first. `duration_s` grows with the viewer's heartbeat, so a row for a document still open
keeps changing. `document` is a snapshot of the name, so rows survive the document being removed (`item_id`
becomes null). There is no IP address in the response.

capture `access_log`
```json
{
  "data": {
    "total": 2,
    "items": [
      {
        "id": "e63b60ff-3c44-4481-899f-5af101a2cb0f",
        "share_id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
        "viewer_email": "delivered+investor-578313484f65@resend.dev",
        "investor_name": "Tolu Bello",
        "document": "model-notes.txt",
        "item_id": "3c279f72-5ff3-42ec-94c1-7393c8fb8cae",
        "started_at": "2026-11-05T20:08:00.418634+00:00",
        "duration_s": 0
      },
      {
        "id": "727aabca-5360-44dc-b48f-2a9340250594",
        "share_id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
        "viewer_email": "delivered+investor-578313484f65@resend.dev",
        "investor_name": "Tolu Bello",
        "document": "pitch-deck.pdf",
        "item_id": "724306c2-e35b-4914-981f-20c8d226ab9c",
        "started_at": "2026-11-05T20:07:58.299905+00:00",
        "duration_s": 2
      }
    ]
  },
  "meta": null
}
```

In-app notification `funding.dataroom.viewed`, **at most one per share per day** (the first document opened
that day), to founder and team members. Title is "{investor name or viewer email} viewed your data room,
{document}"; `data.href` is `/funding/data-room`. Trimmed to that one notification:

capture `notifications`
```json
{
  "data": {
    "notifications": [
      {
        "id": "99d2fefb-6de2-432e-82d7-d5f077cc303c",
        "type": "funding.dataroom.viewed",
        "title": "Tolu Bello viewed your data room, pitch-deck.pdf",
        "body": "See who viewed",
        "data": {
          "href": "/funding/data-room",
          "document": "pitch-deck.pdf",
          "share_id": "6592e4aa-da42-4a22-add5-95a3807ce7f6",
          "startup_id": "5926493c-a014-46bc-bebd-dd01ecfc7a13",
          "investor_id": "fc4755b2-d2dc-4e43-87f3-fbec9c222e9e",
          "viewer_email": "delivered+investor-578313484f65@resend.dev",
          "investor_name": "Tolu Bello"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "funding",
        "created_at": "2026-11-05T20:07:58.291766+00:00"
      }
    ],
    "next_cursor": "..."
  },
  "meta": null
}
```

## 6. Investor viewer (public, no login)

The emailed link is `{APP_BASE_URL}/dr/{token}`. The FE page at `/dr/[token]` calls the API routes below
(`/api/v1/funding/dr/{token}/...`). Keep the **session** in memory only (never in the URL) and send it as
header `X-Data-Room-Session`.

**State machine**

| State | What the page shows | Next |
|---|---|---|
| link dead (404 on `GET /dr/{token}`) | one neutral page: "This link isn't available." Same for unknown, revoked, expired and mistyped | none |
| link ok | company name, email field, "Send code" | `POST /otp` |
| code sent | 6-digit field; "Send a new code" | `POST /verify` |
| verified | folder and file browser | `GET /tree`, `POST /items/{id}/open` |
| session expired (401 on any call) | back to the email field | `POST /otp` |
| share revoked or expired mid-session (404 on any call) | "This data room is no longer available." Stop heartbeats | none |

**Step 1: link check.** Returns only the company name. It never says which email was invited.

capture `public_meta`
```json
{
  "data": {
    "company": "Placeholder Co",
    "requires_code": true
  },
  "meta": null
}
```

An unknown, revoked, expired or mistyped token is the **same 404 body** on every `/dr/...` route (POST ones
too):

capture `public_unknown_link_404`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**Step 2: ask for a code.** `POST /dr/{token}/otp` `{"email"}`. The answer is **identical whether or not
the address is the invited one**; the email is only sent if it is. The page must say "If that address can
open this room, a code is on its way." and must **not** claim a code was sent. Codes last 10 minutes, work
once, and a new request replaces the old code. At most 5 code requests an hour per share and 10 an hour per client
address. **Over the per-share cap the answer is still the same 202 and nothing is sent** (a 429 would reveal
whether an address was invited), so a "didn't get it" state must not rely on an error: offer "Send a new
code" again after a few minutes. The per-address limit is a 429 `RATE_LIMITED`.

capture `otp_unknown_email_202`
```json
{
  "data": {
    "sent": true,
    "expires_in_s": 600
  },
  "meta": null
}
```

capture `otp_invited_email_202`
```json
{
  "data": {
    "sent": true,
    "expires_in_s": 600
  },
  "meta": null
}
```

The email the investor receives:

capture `email_code`
```json
{
  "to": "delivered+investor-578313484f65@resend.dev",
  "subject": "Your data room code",
  "html": "<p>Your code to open the <strong>Placeholder Co</strong> data room is <strong>577195</strong>.</p><p>It works once and expires in 10 minutes. If you didn't ask for it, ignore this email.</p>",
  "sent_at": "2026-11-05T20:07:57.908125+00:00"
}
```

**Step 3: verify.** `POST /dr/{token}/verify` `{"email", "code"}`. Returns a session valid for up to 2
hours (never past the share's expiry).

capture `verify_ok`
```json
{
  "data": {
    "session": "eyJzaWQiOiI2NTkyZTRhYS1kYTQyLTRhMjItYWRkNS05NWEzODA3Y2U3ZjYiLCJlbSI6ImRlbGl2ZXJlZCtpbnZlc3Rvci01NzgzMTM0ODRmNjVAcmVzZW5kLmRldiIsImUiOjE3OTM5MTY0Nzh9.6HwEnq_m5GdXJSrboe2B6RfiR3d3SbETSC_8kgb_o5k",
    "expires_at": "2026-11-05T22:07:58.227916+00:00"
  },
  "meta": null
}
```

Every failure is the **same 401**: wrong code, wrong email, expired code, reused code, no code asked for.
Do not distinguish them in the UI.

capture `verify_wrong_code_401`
```json
{
  "error": {
    "code": "INVALID_CODE",
    "message": "That code didn't work. Check it or ask for a new one.",
    "field_errors": []
  }
}
```

capture `verify_code_reused_401`
```json
{
  "error": {
    "code": "INVALID_CODE",
    "message": "That code didn't work. Check it or ask for a new one.",
    "field_errors": []
  }
}
```

**UX consequences to surface**

- 5 wrong tries kill the current code ("Ask for a new code"). 10 wrong tries against live codes lock the
  share for 15 minutes: **429 `TOO_MANY_ATTEMPTS`**, "Too many attempts. Try again in a few minutes.", on
  `/otp` and `/verify` (*unit-tested, not captured: the per-address throttle below trips first in a live
  run*). Wrong guesses made while no code is outstanding are not counted, so someone holding only the link
  cannot lock the room.
- Per client address the throttle is 8 verifies a minute and 20 an hour: **429 `RATE_LIMITED`**. Show "Too
  many requests, wait a minute".

capture `verify_ip_throttled_429`
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests. Slow down a moment.",
    "field_errors": []
  }
}
```

**Step 4: browse.** `GET /dr/{token}/tree` needs the session header. Only folders in the share's scope and
only items viewable right now are returned. **Traps:** `allow_download` is at the top level of `tree`, not per item; each item has `viewable`. With
downloads off, show an item with `viewable: false` greyed out with "Not available as a view-only copy".

capture `tree`
```json
{
  "data": {
    "folders": [
      {
        "id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
        "name": "Financials",
        "items": [
          {
            "id": "724306c2-e35b-4914-981f-20c8d226ab9c",
            "name": "pitch-deck.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1371,
            "viewer": "pdf",
            "viewable": true
          },
          {
            "id": "3c279f72-5ff3-42ec-94c1-7393c8fb8cae",
            "name": "model-notes.txt",
            "content_type": "text/plain",
            "size_bytes": 23,
            "viewer": "text",
            "viewable": false
          }
        ]
      }
    ],
    "allow_download": false,
    "viewer_email": "delivered+investor-578313484f65@resend.dev"
  },
  "meta": null
}
```

Without a session (or with an expired, tampered or other-share session) it is 401 `SESSION_REQUIRED`:

capture `tree_without_session_401`
```json
{
  "error": {
    "code": "SESSION_REQUIRED",
    "message": "Enter the code we emailed you to continue.",
    "field_errors": []
  }
}
```

**Step 5: open a document.** `POST /dr/{token}/items/{id}/open` (session header). This **logs the view**
(it appears in the founder's access log) and returns `content_url`, a **2-minute signed URL bound to this
share and this item**. **Trap:** `content_url` is a path relative to the API origin (`/api/v1/funding/dr/...`),
so prefix it with the API base URL; it is not an FE route. It carries its own ticket, so it needs no header
and can be an `<iframe>` or PDF.js source. Call `open` again when it expires (401). `viewer` is one of `pdf`,
`image`, `text`, `csv`, `markdown`, `docx`, `xlsx`, `pptx`, `download`; rich Documents are rendered to PDF
(`viewer: "pdf"`).

capture `open_pdf`
```json
{
  "data": {
    "view_id": "727aabca-5360-44dc-b48f-2a9340250594",
    "name": "pitch-deck.pdf",
    "content_type": "application/pdf",
    "viewer": "pdf",
    "allow_download": false,
    "viewable": true,
    "content_url": "/api/v1/funding/dr/apeVwQjDlTOh8aNFvPOXlMTKKLTQd3DqXUwFaeEPMLM/items/724306c2-e35b-4914-981f-20c8d226ab9c/content?ticket=eyJzaWQiOiI2NTkyZTRhYS1kYTQyLTRhMjItYWRkNS05NWEzODA3Y2U3ZjYiLCJpaWQiOiI3MjQzMDZjMi1lMzViLTQ5MTQtOTgxZi0yMGM4ZDIyNmFiOWMiLCJ2aWQiOiI3MjdhYWJjYS01MzYwLTQ0ZGMtYjQ4Zi0yYTkzNDAyNTA1OTQiLCJlbSI6ImRlbGl2ZXJlZCtpbnZlc3Rvci01NzgzMTM0ODRmNjVAcmVzZW5kLmRldiIsImUiOjE3OTM5MDkzOTh9.7LLzJk4jK6pFD-3an_QojSJFEE8SJn5LH0qcSr7v5RQ",
    "expires_in_s": 120,
    "heartbeat_every_s": 15
  },
  "meta": null
}
```

Fetching `content_url` returns binary, so the capture holds the response headers and the text extracted
from the PDF. **Every page carries the viewer's email and the time of viewing**, diagonally and in the
footer. Show a line in the viewer: "Confidential. This copy is marked with your email."

capture `content_pdf`
```json
{
  "status": 200,
  "headers": {
    "content-type": "application/pdf",
    "content-disposition": "inline; filename=\"pitch-deck.pdf\"",
    "cache-control": "private, no-store",
    "x-data-room-view": "727aabca-5360-44dc-b48f-2a9340250594"
  },
  "extracted_text": "Pitch deck (placeholder)\ndelivered+investor-578313484f65@resend.dev  |  2026-11-05 20:07 UTC  |  Confidential\ndelivered+investor-578313484f65@resend.dev  |  2026-11-05 20:07 UTC  |  Confidential\ndelivered+investor-578313484f65@resend.dev  |  2026-11-05 20:07 UTC  |  Confidential\ndelivered+investor-578313484f65@resend.dev  |  2026-11-05 20:07 UTC  |  Confidential\n"
}
```

**Downloads off (the default):** PDFs and rich Documents are stamped; PNG/JPEG images are converted to a
one-page PDF and stamped (*unit-tested, not captured*). Any other type is refused **before** a view is logged:

capture `open_text_view_only_422`
```json
{
  "error": {
    "code": "VIEW_ONLY_UNSUPPORTED",
    "message": "This file type can't be shown view-only. Ask the sender for a PDF.",
    "field_errors": []
  }
}
```

capture `content_text_view_only_422`
```json
{
  "error": {
    "code": "VIEW_ONLY_UNSUPPORTED",
    "message": "This file type can't be shown view-only. Ask the sender for a PDF.",
    "field_errors": []
  }
}
```

**Downloads on:** other types are served as they are, `Content-Disposition: inline` and unwatermarked
(D-FUND-8), and images are served as the original. `?download=true` gives `attachment` **only if the share has
`allow_download: true`**; otherwise the flag is ignored. When downloads are on, say so in the UI: "Downloaded
files are not watermarked." (PDFs are still stamped.) The founder turning downloads on applies to a viewer
already inside; refetch `tree` to refresh `viewable`. *`attachment` behaviour is unit-tested, not captured.*

capture `tree_downloads_on`
```json
{
  "data": {
    "folders": [
      {
        "id": "dff52cb5-59d1-4bfa-b6c4-2357f70ad4e8",
        "name": "Financials",
        "items": [
          {
            "id": "724306c2-e35b-4914-981f-20c8d226ab9c",
            "name": "pitch-deck.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1371,
            "viewer": "pdf",
            "viewable": true
          },
          {
            "id": "3c279f72-5ff3-42ec-94c1-7393c8fb8cae",
            "name": "model-notes.txt",
            "content_type": "text/plain",
            "size_bytes": 23,
            "viewer": "text",
            "viewable": true
          }
        ]
      }
    ],
    "allow_download": true,
    "viewer_email": "delivered+investor-578313484f65@resend.dev"
  },
  "meta": null
}
```

capture `content_text`
```json
{
  "status": 200,
  "headers": {
    "content-type": "text/plain; charset=utf-8",
    "content-disposition": "inline; filename=\"model-notes.txt\"",
    "cache-control": "private, no-store"
  },
  "body": "Placeholder model notes"
}
```

A bad or expired ticket with no session header is 401:

capture `content_bad_ticket_401`
```json
{
  "error": {
    "code": "SESSION_REQUIRED",
    "message": "Enter the code we emailed you to continue.",
    "field_errors": []
  }
}
```

A PDF that cannot be stamped (encrypted or damaged) is **never served bare**: 422 `DOCUMENT_UNAVAILABLE`
(*unit-tested, not captured*). Show "This document can't be shown right now."

**Step 6: heartbeat.** While a document is on screen, `POST /dr/{token}/heartbeat` `{"view_id"}` every **15
seconds** (`heartbeat_every_s`) with the session header. It returns the accumulated `duration_s`. Time added
per beat is capped at 45 seconds, so a sleeping tab does not inflate it. Stop on 404.

capture `heartbeat`
```json
{
  "data": {
    "duration_s": 2
  },
  "meta": null
}
```

**Revocation, expiry, trashing:** every `open`, `content` and `heartbeat` re-checks the share and the
Documents row. After the founder revokes, the very next call is the uniform 404. The same happens when the
share expires, the item or its folder is removed, the scope is narrowed, the Documents file is trashed or
fails the scan, or a rich Document is trashed.

capture `after_revoke_meta_404`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

capture `after_revoke_content_404`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 7. Errors in one table

| Where | Status | `code` | Meaning / FE action |
|---|---|---|---|
| any `/dr` route | 404 | `NOT_FOUND` | dead link, revoked, expired, or item no longer visible. Neutral page. |
| `/otp`, `/verify` | 429 | `TOO_MANY_ATTEMPTS` | share locked 15 min. "Try again later." (the hourly code cap is a silent 202) |
| `/verify` | 401 | `INVALID_CODE` | any wrong-code case. |
| any `/dr` route | 429 | `RATE_LIMITED` | per-address throttle. |
| `/tree`, `/open`, `/heartbeat`, `/content` | 401 | `SESSION_REQUIRED` | session missing or expired: back to the email step. |
| `/open`, `/content` | 422 | `VIEW_ONLY_UNSUPPORTED` | type can't be watermarked and downloads are off. |
| `/content` | 422 | `DOCUMENT_UNAVAILABLE` | cannot be stamped. |
| founder items POST | 409 | `FILE_NOT_CLEAN`, `ITEM_EXISTS` | see section 3. |
| founder folders POST/PATCH | 409 | `FOLDER_EXISTS` | duplicate name. |
| founder shares POST | 409 | `TOO_MANY_SHARES` | 200 active links in one workspace (*from code, not exercised*). |
| founder routes | 403 | | role. |

## 8. Verification table

| Behaviour | Verified |
|---|---|
| Empty state, setup, folder create and duplicate, item add and duplicate | live (`e2e/test_funding_data_room.py`) |
| Share create, list, update, clear expiry, revoke, edit-after-revoke 404 | live |
| Invite email and code email | live (file email backend; real delivery is Resend, not exercised) |
| Uniform 404 for an unknown link; same 202 for stranger and invited email; no mail to the stranger | live |
| Verify ok, wrong code 401, reused code 401, session required 401 | live |
| Tree scoping, open, ticket content, PDF watermark text, text file view-only, bad ticket 401 | live |
| Heartbeat accumulates, access log, share stats, notification | live |
| Revocation cuts off meta, tree, content, heartbeat | live |
| Per-address throttle 429 | live |
| `VIEW_ONLY_UNSUPPORTED` 422 and not logged as a view; allow-downloads then serves the raw file | live |
| `unviewable_items` warning, image conversion, silent hourly code cap, one live code per share | unit tests (the warning is also visible in the live `share_created` capture) |
| Role 403s, 402 plan gate | unit tests only |
| 15-minute per-share lock, 5 codes an hour, 5 tries per code, code, session and ticket expiry | unit tests only |
| Trashed file, scan-failed file, trashed Document, removed item or folder, narrowed scope cut-off | unit tests only |
| `allow_download` attachment, rich Document served as a watermarked PDF, unstampable PDF 422 | unit tests only |
| Real Resend delivery; real browser rendering of the stamped PDF | not exercised |
