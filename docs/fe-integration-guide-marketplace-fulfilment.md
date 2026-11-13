# FE integration guide: Marketplace fulfilment and messaging (slice D)

Every body below is pasted from a live run against a real server: `e2e/test_marketplace_fulfilment.py`, captures in `e2e/_captures/marketplace_fulfilment/<name>.json` (the block title is the capture name). Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`. Companion guides: `docs/fe-integration-guide-marketplace-booking.md` (booking, checkout, orders: the booking views there lack the slice D fields listed below), `docs/fe-integration-guide-marketplace-providers.md`, `docs/fe-integration-guide-marketplace-listings.md`.

**What was and was not live in that run**

- Paystack is the in-process stub (`PAYMENT_PROVIDER_STUB`); the webhook is signed by the test with the run's throwaway test key. Production charging stays off (see the booking guide).
- **The clock was moved by SQL for five steps** (marked `[sql]` in the test): the server runs on real time, so a consultation's session was ended by rewriting `starts_at` / `ends_at` in the e2e database, the unanswered-delivery deadline by rewriting `auto_release_at`, a package's `due_at` likewise, and the marketplace timer was run **in-process against that same database** (`timers.tick`, as the worker does). The HTTP calls around those steps are real and their bodies are real, but nothing waited 48 hours. The two staff attention flags were set by SQL too, because the Paystack stub cannot produce a mismatched amount. Each is repeated in the verification table.
- Emails are captured from the file email backend.

## What this is, in FE terms

1. **Messages.** A founder can write to an approved expert before booking (the "Message" button). When a booking is paid, that same conversation moves onto the booking; if there was none, one is created at payment. The expert replies from `/provider/threads`. Unread counts per side.
2. **Files.** A founder uploads a brief file (and attaches up to 5 when booking); the expert uploads delivery files; either side can attach files to a message. A file is private to its uploader until it is sent; once sent, only the people on that booking (or that conversation) can read it.
3. **Delivery.** A package: the expert delivers (message + files) → the founder has 7 days to **accept** or **request changes** (2 rounds included by default; the listing's snapshot decides) → after the rounds, "Report a problem" (slice E, not built). A consultation: after the session ends the expert marks it complete (or the timer does) → the founder has 48 hours.
4. **Auto-release.** A delivery nobody answers is accepted for the founder by the timer when its deadline passes.
5. **Documents.** A founder can let the expert read one of the workspace's Documents for 30 days, through the booking.
6. **Calendar.** When the expert accepts a consultation the meeting appears on the workspace's Calendar and the booking offers an "Add to calendar" `.ics`.
7. **Not in this slice:** moving money to the expert on acceptance (slice E hooks `marketplace.delivery.accepted`), "Report a problem" / disputes, reviews. **Accepting a delivery moves no money**: the order stays `paid`.

## Booking state machine (slice D additions)

| status | Meaning | Founder can | Expert can |
|---|---|---|---|
| `in_progress` | Package accepted; also a package after changes were requested | read, message, share documents | deliver |
| `confirmed` | Consultation accepted; has a Calendar event | read, message, add to calendar | complete (after the session ends) |
| `delivered` | Work handed over; `auto_release_at` is the deadline | accept, request changes (package) | read, message |
| `completed` | Accepted by the founder, or auto-released | read, message | read, message |

Transitions: `in_progress --deliver--> delivered --accept--> completed`; `delivered --request-changes--> in_progress` (package, while `revisions_left > 0`); `confirmed --complete or timer--> delivered`; `delivered --timer at auto_release_at--> completed`. A package past its `due_at` is **not** moved: the expert and the founder are told once.

**Polling.** There is no push for threads in this slice. While a conversation is open, poll `GET /marketplace/threads/{id}/messages` every 10 s (back off to 60 s after 5 minutes idle; stop when the tab is hidden) and `GET /marketplace/threads` (the `unread_total`) every 30 to 60 s for the badge. For a `delivered` booking show a countdown to `auto_release_at` and re-fetch the booking when it reaches zero (the timer runs once a minute, so allow up to a minute of lag before the status changes). Founder notifications also arrive in the notifications inbox (`module: "marketplace"`).

## Who can do what (D-MP-L1)

| Caller | Result |
|---|---|
| No token | 401 |
| Mentor, accountant, legal advisor, investor | 403 on every `/marketplace/*` route |
| Team member | **reads** (threads, messages, files, bookings, documents list, `.ics`); every write is 403 |
| Founder | everything; accept / request changes / attach documents / post / upload are founder-only |
| Another workspace's founder | 404 on any id that isn't theirs |
| The booking's expert | `/provider/*` routes for their own bookings and threads only |
| Another expert | 404 |
| Platform staff | no route reads a thread or a brief here (only inside a dispute, slice E) |

`thread_open_team_member_403` (the same body for every refused write):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
`threads_mentor_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

## Booking views: what slice D added

Both sides' booking views (`GET /marketplace/bookings/{id}`, `GET /provider/bookings/{id}`, and the action responses) gained these keys. **List endpoints carry the same keys except `deliveries` and `documents`**, which only the detail view and the action responses include.

| Key | Founder view | Expert view |
|---|---|---|
| `delivered_at`, `auto_release_at`, `revisions_used` | yes | yes |
| `revisions_left` | yes | no (the expert sees `revisions_used` and `revisions_included`) |
| `calendar_event_id`, `calendar_ics_url` | yes (null until a consultation is accepted) | no |
| `thread_id` | yes (null if none yet) | yes |
| `brief_files[]` | yes, `url` under `/api/v1/marketplace/files/{id}/content` | yes, `url` under `/api/v1/provider/files/{id}/content` |
| `deliveries[]` | yes | yes (file urls differ as above) |
| `documents[]` | yes: every link with `status` `active` / `expired` / `revoked` | yes: **live links only**, each with a `url` |

**Field-nesting traps.** (1) The same file has a different download path on each side; always use the `url` you were given. (2) `documents[]` differs: the founder sees revoked and expired links, the expert only live ones, and only the expert's carry `url`. (3) `thread_id` in a booking is the conversation's id for `/threads/{id}`. (4) `revisions_left` exists for the founder only. (5) A message's `read` is about the **other side** reading *your* message; it is always `true` on the other side's messages. (6) `mine` is relative to the caller.

`booking_get_delivered` (founder):
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "delivered",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:32.777452+00:00",
    "auto_release_at": "2026-11-19T19:35:32.777452+00:00",
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```
`provider_booking_awaiting_with_brief_files` (expert; no workspace name or id anywhere, `client_name` is the person's name only):
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "awaiting_provider",
    "type": "package",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "client_name": "Ada Obi",
    "starts_at": null,
    "ends_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "earnings": {
      "price_minor": 25000000,
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000,
      "currency": "NGN",
      "paid": true
    },
    "delivered_at": null,
    "auto_release_at": null,
    "revisions_used": 0,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/provider/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [],
    "documents": []
  },
  "meta": null
}
```

## Files

Allowed types and sizes (checked by the file's bytes, not its claimed type):

| Purpose | Who uploads | Types | Size |
|---|---|---|---|
| `brief`, `message` | founder (`POST /marketplace/files`) | PDF, Word, Excel, PNG, JPEG | 10 MB |
| `delivery` | expert (`POST /provider/files`) | PDF, Word, Excel, PowerPoint, PNG, JPEG, text, CSV | 15 MB |
| `message` | expert (`POST /provider/files`) | as brief | 10 MB |

Up to 20 unsent uploads per purpose. Experts use the **existing** `/provider/files` for these purposes (they have no workspace, so they cannot call `/marketplace/*`).

`POST /marketplace/files` (multipart: `purpose`, `file`), `file_upload_brief_201`:
```json
{
  "data": {
    "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
    "purpose": "brief",
    "filename": "seed-terms.pdf",
    "mime": "application/pdf",
    "size_bytes": 38,
    "scan_status": "clean",
    "created_at": "2026-11-12T19:35:32.150697+00:00"
  },
  "meta": null
}
```
`file_upload_unsupported_type_422`:
```json
{
  "error": {
    "code": "UNSUPPORTED_FILE_TYPE",
    "message": "That file type isn't allowed here. Upload a PDF, Word, Excel, PNG or JPEG file.",
    "field_errors": []
  }
}
```
`file_upload_founder_delivery_422` (a founder may only send `brief` or `message`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Choose 'brief' or 'message' as the purpose.",
    "field_errors": []
  }
}
```
`provider_file_upload_brief_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a client can upload a brief.",
    "field_errors": []
  }
}
```
`file_upload_team_member_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
`provider_file_upload_delivery_201` (expert):
```json
{
  "data": {
    "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
    "purpose": "delivery",
    "filename": "shareholder-agreement-v1.pdf",
    "mime": "application/pdf",
    "size_bytes": 38,
    "scan_status": "clean",
    "created_at": "2026-11-12T19:35:32.730104+00:00"
  },
  "meta": null
}
```
`provider_file_upload_delivery_csv_201`:
```json
{
  "data": {
    "id": "78acf26d-57bb-49d0-890f-ee49fde46389",
    "purpose": "delivery",
    "filename": "schedule.csv",
    "mime": "text/csv",
    "size_bytes": 11,
    "scan_status": "clean",
    "created_at": "2026-11-12T19:35:32.745987+00:00"
  },
  "meta": null
}
```

**Who can download.** `GET /marketplace/files/{id}/content` (founder side) and `GET /provider/files/{id}/content` (expert). `GET /marketplace/files/{id}` returns the same bytes. Your own upload is always readable by you. Once a file is attached (brief, message or delivery), the people on that booking or conversation can read it: founder and team member on one side, that booking's expert on the other (an expert must be approved). Nobody else. A file that is unsent, in another workspace, for another expert, or on an **unpaid** booking is a plain 404:

`provider_file_unpaid_booking_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`provider_file_other_expert_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`file_other_workspace_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`provider_file_delete_sent_409` (a sent file cannot be deleted by its uploader; unsent ones can: `DELETE /marketplace/files/{id}` for founders, `DELETE /provider/files/{id}` for experts):
```json
{
  "error": {
    "code": "FILE_IN_USE",
    "message": "That file was sent to a client, so it can't be removed.",
    "field_errors": []
  }
}
```

Downloads are `attachment` with `X-Content-Type-Options: nosniff` and a sandbox CSP; the download answers 409 `FILE_NOT_CLEAN` if the scan has not passed (not exercised live: the scanner here marks files clean).

## Attaching a brief to a booking

`POST /marketplace/bookings` takes an optional `file_ids` (max 5, your unsent `brief` uploads). A bad id fails the whole request before anything is created or charged; a replay with the same Idempotency-Key returns the original booking and does **not** attach the new ids.

`booking_create_bad_file_422`:
```json
{
  "error": {
    "code": "INVALID_FILE",
    "message": "One of those files can't be attached. Upload it again.",
    "field_errors": []
  }
}
```
`booking_create_package_with_brief_file_201` (note `brief_files`; the response is `201` as in slice C, and a replay is `200` with `replayed: true`):
```json
{
  "data": {
    "replayed": false,
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "pending_payment",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": "2026-11-12T19:50:32.279994+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "pending",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": "https://checkout.paystack.com/stub-mp_60c0c369479d82153a34",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": null,
    "auto_release_at": null,
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": null,
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00"
  },
  "meta": null
}
```
`provider_booking_unpaid_404` (the expert sees nothing of a booking until it is paid):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## Conversations

Founder: `GET /marketplace/threads`, `POST /marketplace/threads` (body `{"provider_id"}`; `201` the first time, `200` after; founder only; 20 a minute), `GET /marketplace/threads/{id}`, `GET|POST /marketplace/threads/{id}/messages`; the booking aliases `GET|POST /marketplace/bookings/{id}/messages`. Expert: the same under `/provider/threads...` and `/provider/bookings/{id}/messages` (an expert replies; they never open a conversation).

- A message is plain text, 1 to 4000 characters, with up to 5 `file_ids` (your unsent `message` uploads). **Render it as text, never as HTML.**
- Listing messages returns the **newest** `limit` (default 50, max 100) oldest-first; pass `before=<oldest loaded message id>` to page back; `has_more` says whether older ones exist. **Listing marks the other side's messages read** for the whole side (a team member opening the thread reads it for the founder). `GET /threads/{id}` does not.
- Unread: each thread has `unread_count`; the list has `unread_total`.
- A conversation with no message and no booking is invisible to the expert.
- Posting is limited to 30 a minute per caller; opening conversations to 20 a minute and to 20 new conversations per workspace per day (`429 THREAD_LIMIT_REACHED`).
- A booking that was cancelled, declined, expired or refunded makes its conversation read-only (`read_only: true`; posting is `409 THREAD_CLOSED`). A completed booking's stays open.
- Before the booking is paid there is no booking conversation: reading `GET /marketplace/bookings/{id}/messages` returns an empty list with `thread_id: null`; posting is `409 THREAD_NOT_AVAILABLE`. Reading never creates a conversation.

`thread_open_201`:
```json
{
  "data": {
    "id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "booking_id": null,
    "booking_title": null,
    "booking_status": null,
    "read_only": false,
    "last_message_at": null,
    "last_message_preview": null,
    "unread_count": 0,
    "created_at": "2026-11-12T19:35:31.773422+00:00",
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze"
    }
  },
  "meta": null
}
```
`thread_open_again_200` (same id):
```json
{
  "data": {
    "id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "booking_id": null,
    "booking_title": null,
    "booking_status": null,
    "read_only": false,
    "last_message_at": null,
    "last_message_preview": null,
    "unread_count": 0,
    "created_at": "2026-11-12T19:35:31.773422+00:00",
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze"
    }
  },
  "meta": null
}
```
`thread_open_unknown_expert_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`provider_threads_empty_before_first_message`:
```json
{
  "data": {
    "threads": [],
    "unread_total": 0,
    "pagination": {
      "total": 0,
      "limit": 25,
      "offset": 0,
      "has_more": false
    }
  },
  "meta": null
}
```
`message_post_founder_201`:
```json
{
  "data": {
    "id": "3406b14d-d912-4c19-925f-0f3bc990943d",
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "sender_side": "founder",
    "mine": true,
    "body": "Hi Ngozi, do you draft shareholder agreements for seed rounds?",
    "files": [],
    "created_at": "2026-11-12T19:35:31.849974+00:00",
    "read": false
  },
  "meta": null
}
```
`message_post_empty_422` (**trap:** for a body that fails validation here `field_errors[0].field` is the empty string, not `body`; show the message at the composer):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "String should have at least 1 character"
      }
    ]
  }
}
```
`message_post_team_member_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
`provider_threads_unread_1` (expert):
```json
{
  "data": {
    "threads": [
      {
        "id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "booking_id": null,
        "booking_title": null,
        "booking_status": null,
        "read_only": false,
        "last_message_at": "2026-11-12T19:35:31.849974+00:00",
        "last_message_preview": "Hi Ngozi, do you draft shareholder agreements for seed rounds?",
        "unread_count": 1,
        "created_at": "2026-11-12T19:35:31.773422+00:00",
        "client_name": null
      }
    ],
    "unread_total": 1,
    "pagination": {
      "total": 1,
      "limit": 25,
      "offset": 0,
      "has_more": false
    }
  },
  "meta": null
}
```
`provider_messages_read` (listing marks it read):
```json
{
  "data": {
    "messages": [
      {
        "id": "3406b14d-d912-4c19-925f-0f3bc990943d",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": false,
        "body": "Hi Ngozi, do you draft shareholder agreements for seed rounds?",
        "files": [],
        "created_at": "2026-11-12T19:35:31.849974+00:00",
        "read": true
      }
    ],
    "has_more": false
  },
  "meta": null
}
```
`message_post_provider_201`:
```json
{
  "data": {
    "id": "55b1fa72-97f0-45eb-b20d-ae2ca98ae78a",
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "sender_side": "provider",
    "mine": true,
    "body": "Yes, I do. Send me the details of the round.",
    "files": [],
    "created_at": "2026-11-12T19:35:32.007151+00:00",
    "read": false
  },
  "meta": null
}
```
`threads_unread_1` (founder side):
```json
{
  "data": {
    "threads": [
      {
        "id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "booking_id": null,
        "booking_title": null,
        "booking_status": null,
        "read_only": false,
        "last_message_at": "2026-11-12T19:35:32.007151+00:00",
        "last_message_preview": "Yes, I do. Send me the details of the round.",
        "unread_count": 1,
        "created_at": "2026-11-12T19:35:31.773422+00:00",
        "provider": {
          "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
          "display_name": "Ngozi Eze"
        }
      }
    ],
    "unread_total": 1,
    "pagination": {
      "total": 1,
      "limit": 25,
      "offset": 0,
      "has_more": false
    }
  },
  "meta": null
}
```
`messages_list`:
```json
{
  "data": {
    "messages": [
      {
        "id": "3406b14d-d912-4c19-925f-0f3bc990943d",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": true,
        "body": "Hi Ngozi, do you draft shareholder agreements for seed rounds?",
        "files": [],
        "created_at": "2026-11-12T19:35:31.849974+00:00",
        "read": true
      },
      {
        "id": "55b1fa72-97f0-45eb-b20d-ae2ca98ae78a",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "provider",
        "mine": false,
        "body": "Yes, I do. Send me the details of the round.",
        "files": [],
        "created_at": "2026-11-12T19:35:32.007151+00:00",
        "read": true
      }
    ],
    "has_more": false
  },
  "meta": null
}
```
`messages_list_page_back` (`limit=1`):
```json
{
  "data": {
    "messages": [
      {
        "id": "55b1fa72-97f0-45eb-b20d-ae2ca98ae78a",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "provider",
        "mine": false,
        "body": "Yes, I do. Send me the details of the round.",
        "files": [],
        "created_at": "2026-11-12T19:35:32.007151+00:00",
        "read": true
      }
    ],
    "has_more": true
  },
  "meta": null
}
```
`thread_get_other_workspace_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`provider_thread_get_other_expert_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`booking_message_unpaid_409`:
```json
{
  "error": {
    "code": "THREAD_NOT_AVAILABLE",
    "message": "Messaging opens once the booking is paid.",
    "field_errors": []
  }
}
```
`booking_get_awaiting_provider_with_thread` (the earlier conversation is now the booking's: `thread_id` equals the thread opened before booking):
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "awaiting_provider",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": null,
    "auto_release_at": null,
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [],
    "documents": []
  },
  "meta": null
}
```
`booking_message_alias_post_201`:
```json
{
  "data": {
    "id": "abc6f0e7-9977-440a-95a5-c68e3f4cafdd",
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "sender_side": "founder",
    "mine": true,
    "body": "Brief attached. Draft by Friday?",
    "files": [],
    "created_at": "2026-11-12T19:35:32.485668+00:00",
    "read": false
  },
  "meta": null
}
```
`booking_messages_list` (alias read, includes the pre-booking messages):
```json
{
  "data": {
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "messages": [
      {
        "id": "3406b14d-d912-4c19-925f-0f3bc990943d",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": true,
        "body": "Hi Ngozi, do you draft shareholder agreements for seed rounds?",
        "files": [],
        "created_at": "2026-11-12T19:35:31.849974+00:00",
        "read": true
      },
      {
        "id": "55b1fa72-97f0-45eb-b20d-ae2ca98ae78a",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "provider",
        "mine": false,
        "body": "Yes, I do. Send me the details of the round.",
        "files": [],
        "created_at": "2026-11-12T19:35:32.007151+00:00",
        "read": true
      },
      {
        "id": "abc6f0e7-9977-440a-95a5-c68e3f4cafdd",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": true,
        "body": "Brief attached. Draft by Friday?",
        "files": [],
        "created_at": "2026-11-12T19:35:32.485668+00:00",
        "read": false
      }
    ],
    "has_more": false
  },
  "meta": null
}
```
`provider_threads_with_booking`:
```json
{
  "data": {
    "threads": [
      {
        "id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "booking_id": "4aeef8f7-c785-4acb-ac70-027360090b72",
        "booking_title": "Shareholder agreement drafting",
        "booking_status": "awaiting_provider",
        "read_only": false,
        "last_message_at": "2026-11-12T19:35:32.485668+00:00",
        "last_message_preview": "Brief attached. Draft by Friday?",
        "unread_count": 1,
        "created_at": "2026-11-12T19:35:31.773422+00:00",
        "client_name": "Ada Obi"
      }
    ],
    "unread_total": 1,
    "pagination": {
      "total": 1,
      "limit": 25,
      "offset": 0,
      "has_more": false
    }
  },
  "meta": null
}
```
`provider_messages_after_completion` (a completed booking's conversation stays open):
```json
{
  "data": {
    "messages": [
      {
        "id": "3406b14d-d912-4c19-925f-0f3bc990943d",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": false,
        "body": "Hi Ngozi, do you draft shareholder agreements for seed rounds?",
        "files": [],
        "created_at": "2026-11-12T19:35:31.849974+00:00",
        "read": true
      },
      {
        "id": "55b1fa72-97f0-45eb-b20d-ae2ca98ae78a",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "provider",
        "mine": true,
        "body": "Yes, I do. Send me the details of the round.",
        "files": [],
        "created_at": "2026-11-12T19:35:32.007151+00:00",
        "read": true
      },
      {
        "id": "abc6f0e7-9977-440a-95a5-c68e3f4cafdd",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": false,
        "body": "Brief attached. Draft by Friday?",
        "files": [],
        "created_at": "2026-11-12T19:35:32.485668+00:00",
        "read": true
      },
      {
        "id": "83a8ea5b-af9c-42b2-b6e3-84d9f866d854",
        "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
        "sender_side": "founder",
        "mine": false,
        "body": "Thank you!",
        "files": [],
        "created_at": "2026-11-12T19:35:33.331064+00:00",
        "read": true
      }
    ],
    "has_more": false
  },
  "meta": null
}
```

The expert is emailed when a founder writes: **once per conversation per 10 minutes**, with no message text, and the founder's name escaped. `email_provider_new_message`:
```json
{
  "to": "delivered+e2e-978ac02bc1ef@resend.dev",
  "subject": "You have a new message on Cofoundaz",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">New message</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, you have a new message from Ada Obi. We send one email per conversation every few minutes, however many messages arrive.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/messages\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open your messages</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/messages\" style=\"color:#0F6AFC\">http://localhost:3000/provider/messages</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">Reply from the portal. Keep the conversation on Cofoundaz.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-12T19:35:31.858506+00:00"
}
```

## Delivering a package

`POST /provider/bookings/{id}/deliver` body `{"message": "...", "file_ids": [...]}` (message 1 to 4000 characters; up to 10 `delivery` uploads). Only for a package in `in_progress`. The response is the expert's booking detail. `auto_release_at` is `delivered_at` + 7 days.

`provider_deliver_empty_message_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "message",
        "message": "String should have at least 1 character"
      }
    ]
  }
}
```
`provider_deliver_other_expert_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`provider_deliver_200`:
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "delivered",
    "type": "package",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "client_name": "Ada Obi",
    "starts_at": null,
    "ends_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "earnings": {
      "price_minor": 25000000,
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000,
      "currency": "NGN",
      "paid": true
    },
    "delivered_at": "2026-11-12T19:35:32.777452+00:00",
    "auto_release_at": "2026-11-19T19:35:32.777452+00:00",
    "revisions_used": 0,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/provider/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/provider/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active",
        "url": "/api/v1/provider/bookings/4aeef8f7-c785-4acb-ac70-027360090b72/documents/95a3ef9f-dee0-411f-ad1b-43397076a0d9"
      }
    ]
  },
  "meta": null
}
```
`provider_deliver_twice_409`:
```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Can't deliver a booking that is 'delivered'.",
    "field_errors": []
  }
}
```
`founder_notifications_after_delivery` (founder's inbox; the first item is the delivery notice):
```json
{
  "data": {
    "notifications": [
      {
        "id": "5733efc2-4666-4b53-9edf-8de92ebac378",
        "type": "marketplace.delivery.submitted",
        "title": "Ngozi Eze delivered your order",
        "body": "Review delivery",
        "data": {
          "type": "package",
          "round": 1,
          "title": "Shareholder agreement drafting",
          "booking_id": "4aeef8f7-c785-4acb-ac70-027360090b72",
          "startup_id": "10ab376b-c078-4a7f-8f55-31a3a9105e5e",
          "delivery_id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
          "provider_id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
          "provider_name": "Ngozi Eze",
          "auto_release_at": "2026-11-19T19:35:32.777452+00:00"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "marketplace",
        "created_at": "2026-11-12T19:35:32.769389+00:00"
      },
      {
        "id": "633dcc21-8027-4518-b88b-a205447e9386",
        "type": "marketplace.booking.confirmed",
        "title": "Booking confirmed: Shareholder agreement drafting with Ngozi Eze",
        "body": "View booking",
        "data": {
          "date": null,
          "type": "package",
          "title": "Shareholder agreement drafting",
          "starts_at": null,
          "booking_id": "4aeef8f7-c785-4acb-ac70-027360090b72",
          "startup_id": "10ab376b-c078-4a7f-8f55-31a3a9105e5e",
          "provider_id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
          "provider_name": "Ngozi Eze"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "marketplace",
        "created_at": "2026-11-12T19:35:32.528370+00:00"
      },
      {
        "id": "014b8e87-39ea-443e-b68f-bc59921d2c32",
        "type": "marketplace.message.new",
        "title": "Ngozi Eze sent you a message",
        "body": "Open conversation",
        "data": {
          "service": null,
          "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
          "booking_id": null,
          "startup_id": "10ab376b-c078-4a7f-8f55-31a3a9105e5e",
          "provider_id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
          "provider_name": "Ngozi Eze"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "marketplace",
        "created_at": "2026-11-12T19:35:31.999074+00:00"
      },
      {
        "id": "7483febe-8fce-45a5-a1db-e499a4949f49",
        "type": "workspace.member.joined",
        "title": "A new member joined your workspace",
        "body": "",
        "data": {
          "role": "mentor",
          "user_id": "3f3cb9d2-30d8-4a89-a00f-d6e6c68513fe",
          "startup_id": "10ab376b-c078-4a7f-8f55-31a3a9105e5e"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "team",
        "created_at": "2026-11-12T19:35:31.663337+00:00"
      },
      {
        "id": "a0035677-5195-4369-adcd-0ae229d16631",
        "type": "workspace.member.joined",
        "title": "A new member joined your workspace",
        "body": "",
        "data": {
          "role": "team_member",
          "user_id": "1ce0222e-11ef-43d2-9e0b-2401f879f68e",
          "startup_id": "10ab376b-c078-4a7f-8f55-31a3a9105e5e"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "team",
        "created_at": "2026-11-12T19:35:30.931650+00:00"
      },
      {
        "id": "19adc49b-71f3-47d5-8a63-b2d23119adb0",
        "type": "notification.onboarding_complete",
        "title": "Your workspace is ready",
        "body": "Your roadmap and Health Score are being set up. Take the Kickoff Assessment to sharpen them.",
        "data": {
          "user_id": "bebab862-19ae-4ccc-ad40-725f9769451e",
          "startup_id": "10ab376b-c078-4a7f-8f55-31a3a9105e5e"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "other",
        "created_at": "2026-11-12T19:35:29.648202+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

## Accepting a delivery

`POST /marketplace/bookings/{id}/accept-delivery` (founder). The booking becomes `completed`, the delivery's `decision` is `accepted`, **no money moves** (`order.status` stays `paid`, `refunded_minor` 0), and the expert is emailed. Repeating it, or calling it after the delivery was auto-released, answers `200` with `replayed: true` and changes nothing.

`accept_delivery_team_member_403` is the same body as the other 403s above. `accept_delivery_200`:
```json
{
  "data": {
    "replayed": false,
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "completed",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.119508+00:00",
    "auto_release_at": null,
    "revisions_used": 2,
    "revisions_left": 0,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      },
      {
        "id": "d68425dc-15a0-4dcb-8330-48edb89711ea",
        "round": 2,
        "message": "Second draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.044848+00:00",
        "decision": "changes_requested",
        "decision_note": "Vesting cliff should be twelve months.",
        "decided_at": "2026-11-12T19:35:33.087192+00:00"
      },
      {
        "id": "17dbbfca-b740-426f-a024-5170b0517f75",
        "round": 3,
        "message": "Third draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.119508+00:00",
        "decision": "accepted",
        "decision_note": null,
        "decided_at": "2026-11-12T19:35:33.220344+00:00"
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "revoked"
      }
    ]
  },
  "meta": null
}
```
`accept_delivery_replay_200` (only the top of the body differs: `replayed: true`):
```json
{
  "data": {
    "replayed": true,
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "completed",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.119508+00:00",
    "auto_release_at": null,
    "revisions_used": 2,
    "revisions_left": 0,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      },
      {
        "id": "d68425dc-15a0-4dcb-8330-48edb89711ea",
        "round": 2,
        "message": "Second draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.044848+00:00",
        "decision": "changes_requested",
        "decision_note": "Vesting cliff should be twelve months.",
        "decided_at": "2026-11-12T19:35:33.087192+00:00"
      },
      {
        "id": "17dbbfca-b740-426f-a024-5170b0517f75",
        "round": 3,
        "message": "Third draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.119508+00:00",
        "decision": "accepted",
        "decision_note": null,
        "decided_at": "2026-11-12T19:35:33.220344+00:00"
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "revoked"
      }
    ]
  },
  "meta": null
}
```
`email_provider_delivery_accepted`:
```json
{
  "to": "delivered+e2e-978ac02bc1ef@resend.dev",
  "subject": "Delivery accepted: Shareholder agreement drafting",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">Delivery accepted</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, The client accepted your delivery. <strong>Shareholder agreement drafting</strong> is complete.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/bookings\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open the booking</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/bookings\" style=\"color:#0F6AFC\">http://localhost:3000/provider/bookings</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">Your payout is handled separately once settlement opens.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-12T19:35:33.229311+00:00"
}
```

## Requesting changes

`POST /marketplace/bookings/{id}/request-changes` body `{"reason": "..."}` (founder; reason 10 to 2000 characters; packages only; while `revisions_left > 0`). The booking goes back to `in_progress`, `revisions_used` goes up, the delivery's `decision` is `changes_requested` with your `decision_note`, `auto_release_at` is cleared, and the expert is emailed (the reason escaped). The expert then delivers again (round 2, 3, ...). Sending the same reason again while it is still `in_progress` is a replay (`replayed: true`).

`request_changes_reason_too_short_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "reason",
        "message": "String should have at least 10 characters"
      }
    ]
  }
}
```
`request_changes_team_member_403`, `request_changes_mentor_403`, `request_changes_other_workspace_404` are the standard 403 / 403 / 404 bodies shown above.
`request_changes_1_200`:
```json
{
  "data": {
    "replayed": false,
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "in_progress",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:32.777452+00:00",
    "auto_release_at": null,
    "revisions_used": 1,
    "revisions_left": 1,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```
`request_changes_replay_200` (top of the body: `replayed: true`):
```json
{
  "data": {
    "replayed": true,
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "in_progress",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:32.777452+00:00",
    "auto_release_at": null,
    "revisions_used": 1,
    "revisions_left": 1,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```
`email_provider_changes_requested`:
```json
{
  "to": "delivered+e2e-978ac02bc1ef@resend.dev",
  "subject": "Changes requested: Shareholder agreement drafting",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">The client asked for changes</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, the client reviewed <strong>Shareholder agreement drafting</strong> (round 1) and asked for changes: <br><br><em>Please soften clause 4 &amp; add a vesting schedule.</em></p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/bookings\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open the booking</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/bookings\" style=\"color:#0F6AFC\">http://localhost:3000/provider/bookings</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">Submit the updated work from the booking when it is ready.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-12T19:35:32.986956+00:00"
}
```
`provider_redeliver_round_2_200`:
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "delivered",
    "type": "package",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "client_name": "Ada Obi",
    "starts_at": null,
    "ends_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "earnings": {
      "price_minor": 25000000,
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000,
      "currency": "NGN",
      "paid": true
    },
    "delivered_at": "2026-11-12T19:35:33.044848+00:00",
    "auto_release_at": "2026-11-19T19:35:33.044848+00:00",
    "revisions_used": 1,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/provider/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/provider/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      },
      {
        "id": "d68425dc-15a0-4dcb-8330-48edb89711ea",
        "round": 2,
        "message": "Second draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.044848+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active",
        "url": "/api/v1/provider/bookings/4aeef8f7-c785-4acb-ac70-027360090b72/documents/95a3ef9f-dee0-411f-ad1b-43397076a0d9"
      }
    ]
  },
  "meta": null
}
```
`request_changes_2_200`:
```json
{
  "data": {
    "replayed": false,
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "in_progress",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.044848+00:00",
    "auto_release_at": null,
    "revisions_used": 2,
    "revisions_left": 0,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      },
      {
        "id": "d68425dc-15a0-4dcb-8330-48edb89711ea",
        "round": 2,
        "message": "Second draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.044848+00:00",
        "decision": "changes_requested",
        "decision_note": "Vesting cliff should be twelve months.",
        "decided_at": "2026-11-12T19:35:33.087192+00:00"
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```
`request_changes_limit_reached_409` (the third request; point the founder at "Report a problem", which does not exist yet):
```json
{
  "error": {
    "code": "REVISION_LIMIT_REACHED",
    "message": "All 2 included revisions are used. If the work still isn't right, use 'Report a problem' and our team will look at it.",
    "field_errors": []
  }
}
```
`booking_get_three_rounds` (all rounds, with decisions):
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "delivered",
    "type": "package",
    "listing_id": "e637810c-538c-4536-a9cd-1a4204bcf815",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "price_minor": 25000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "27160c8b-933d-415b-8400-484694506347",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_60c0c369479d82153a34",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:32+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.119508+00:00",
    "auto_release_at": "2026-11-19T19:35:33.119508+00:00",
    "revisions_used": 2,
    "revisions_left": 0,
    "calendar_event_id": null,
    "calendar_ics_url": null,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/marketplace/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [
      {
        "id": "2d70c48b-7e3e-4737-8d99-2ef4aa27d52a",
        "round": 1,
        "message": "First draft attached.",
        "files": [
          {
            "id": "43fe6a97-4fe2-4d0b-8b24-bc260a56276c",
            "purpose": "delivery",
            "filename": "shareholder-agreement-v1.pdf",
            "mime": "application/pdf",
            "size_bytes": 38,
            "scan_status": "clean",
            "created_at": "2026-11-12T19:35:32.730104+00:00",
            "url": "/api/v1/marketplace/files/43fe6a97-4fe2-4d0b-8b24-bc260a56276c/content"
          }
        ],
        "submitted_at": "2026-11-12T19:35:32.777452+00:00",
        "decision": "changes_requested",
        "decision_note": "Please soften clause 4 & add a vesting schedule.",
        "decided_at": "2026-11-12T19:35:32.980760+00:00"
      },
      {
        "id": "d68425dc-15a0-4dcb-8330-48edb89711ea",
        "round": 2,
        "message": "Second draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.044848+00:00",
        "decision": "changes_requested",
        "decision_note": "Vesting cliff should be twelve months.",
        "decided_at": "2026-11-12T19:35:33.087192+00:00"
      },
      {
        "id": "17dbbfca-b740-426f-a024-5170b0517f75",
        "round": 3,
        "message": "Third draft.",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.119508+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```
`request_changes_consultation_422`:
```json
{
  "error": {
    "code": "NOT_APPLICABLE",
    "message": "Consultations have no revision rounds. Use 'Report a problem' if it went wrong.",
    "field_errors": []
  }
}
```

## Consultations: Calendar, `.ics`, complete

When the expert **accepts** a consultation the booking gets a `calendar_event_id`, the workspace Calendar gets the meeting (title "<service> with <expert>", the meeting link, the expert as an external attendee who is emailed an invite), and `calendar_ics_url` appears. **The meeting link reaches the expert's inbox only through that invite, after accepting.** A package never gets an event.

`calendar_ics_before_accept_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`booking_get_confirmed_with_calendar`:
```json
{
  "data": {
    "id": "85cf4478-2bd0-43d1-9b96-3f274da0b5db",
    "status": "confirmed",
    "type": "consultation",
    "listing_id": "125b08b4-0f1d-475c-a443-f6ca79a54e1d",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": "2026-11-14T07:00:00+00:00",
    "ends_at": "2026-11-14T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:33.402344+00:00",
    "due_at": null,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "d59cce13-fd3f-479f-88f2-8dda1e1a9d69",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_df6ef6f821ab68612a1d",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:33+00:00",
      "receipt_number": "MPR-2026-1002",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": null,
    "auto_release_at": null,
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": "0ac10f92-23bd-4aa3-a476-849b115f450d",
    "calendar_ics_url": "/api/v1/marketplace/bookings/85cf4478-2bd0-43d1-9b96-3f274da0b5db/calendar.ics",
    "thread_id": "2c52d00c-1153-4df0-852e-bdf8c0303e03",
    "brief_files": [],
    "created_at": "2026-11-12T19:35:33.363289+00:00",
    "deliveries": [],
    "documents": []
  },
  "meta": null
}
```
`calendar_ics_200` (`GET /marketplace/bookings/{id}/calendar.ics`, content type `text/calendar; charset=utf-8`, an attachment; the file is addressed to whoever downloads it; founder and team member only):
```json
{
  "status": 200,
  "content_type": "text/calendar; charset=utf-8",
  "body": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Cofoundaz//Calendar//EN\r\nCALSCALE:GREGORIAN\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:0ac10f92-23bd-4aa3-a476-849b115f450d@cofoundaz.com\r\nSEQUENCE:0\r\nDTSTAMP:20261112T193533Z\r\nDTSTART:20261114T070000Z\r\nDTEND:20261114T080000Z\r\nSUMMARY:Contract review session with Ngozi Eze\r\nSTATUS:CONFIRMED\r\nORGANIZER;CN=Ada Obi:mailto:delivered+e2e-45c9f9200280@resend.dev\r\nATTENDEE;CN=Ada Obi;RSVP=TRUE:mailto:delivered+e2e-45c9f9200280@resend.dev\r\nDESCRIPTION:Booked on the Cofoundaz Marketplace.\\nhttps://meet.example.com/\r\n ngozi-eze\r\nURL:https://meet.example.com/ngozi-eze\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
}
```
What the expert receives (not an FE endpoint, so there is nothing to build; it explains what the founder-side `.ics` above is NOT). The expert's invite is sent by the worker, and its organiser is the Cofoundaz no-reply sender, never the person who booked: the booker's email address must not reach the provider (house rule: member emails go only to founder and team member). The booker is named by their display name only, and a booker with no name shows as "A team member". The founder-side file above keeps the booker as organiser, because it goes to the founder side. Captured live as `email_expert_consultation_invite` (the `ics` field is the invite's attachment, unfolded):
```json
{
  "to": "delivered+e2e-b625b0d525d9@resend.dev",
  "subject": "Ada Obi invited you: Contract review session with Ngozi Eze \u00b7 15 Nov 2026",
  "html": "<p>Ada Obi invited you to a meeting.</p><p><strong>Contract review session with Ngozi Eze</strong><br>Sun 15 Nov 2026, 08:00 (Africa/Lagos)</p><p><a href=\"https://meet.example.com/ngozi-eze\">Join the call</a></p><p>Booked on the Cofoundaz Marketplace.</p>",
  "sent_at": "2026-11-13T16:30:16.951304+00:00",
  "attachments": [
    "invite.ics"
  ],
  "ics": "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Cofoundaz//Calendar//EN\nCALSCALE:GREGORIAN\nMETHOD:REQUEST\nBEGIN:VEVENT\nUID:91a52a3e-9c3b-48bc-b3c0-2adf5d44118d@cofoundaz.com\nSEQUENCE:0\nDTSTAMP:20261113T163016Z\nDTSTART:20261115T070000Z\nDTEND:20261115T080000Z\nSUMMARY:Contract review session with Ngozi Eze\nSTATUS:CONFIRMED\nORGANIZER;CN=Ada Obi:mailto:no-reply@cofoundaz.com\nATTENDEE;CN=Ngozi Eze;RSVP=TRUE:mailto:delivered+e2e-b625b0d525d9@resend.de\n v\nDESCRIPTION:Booked on the Cofoundaz Marketplace.\\nhttps://meet.example.com/\n ngozi-eze\nURL:https://meet.example.com/ngozi-eze\nEND:VEVENT\nEND:VCALENDAR\n"
}
```

`calendar_ics_mentor_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
`calendar_ics_other_workspace_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`POST /provider/bookings/{id}/complete` marks a confirmed consultation done once its session has ended. `provider_complete_before_session_ends_409`:
```json
{
  "error": {
    "code": "SESSION_NOT_ENDED",
    "message": "You can mark this consultation complete once the session ends.",
    "field_errors": []
  }
}
```
`provider_complete_200` (**session ended by SQL**, the call is real):
```json
{
  "data": {
    "id": "85cf4478-2bd0-43d1-9b96-3f274da0b5db",
    "status": "delivered",
    "type": "consultation",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "client_name": "Ada Obi",
    "starts_at": "2026-11-12T17:35:33.701684+00:00",
    "ends_at": "2026-11-12T18:35:33.701684+00:00",
    "accept_by": "2026-11-14T19:35:33.402344+00:00",
    "due_at": null,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "closed_reason": null,
    "earnings": {
      "price_minor": 1000000,
      "commission_bps": 1500,
      "commission_minor": 150000,
      "net_minor": 850000,
      "currency": "NGN",
      "paid": true
    },
    "delivered_at": "2026-11-12T19:35:33.718746+00:00",
    "auto_release_at": "2026-11-14T19:35:33.718746+00:00",
    "revisions_used": 0,
    "thread_id": "2c52d00c-1153-4df0-852e-bdf8c0303e03",
    "brief_files": [],
    "created_at": "2026-11-12T19:35:33.363289+00:00",
    "deliveries": [
      {
        "id": "3140fa60-ad5c-4778-bde4-c0be7bdd04fe",
        "round": 1,
        "message": "",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.718746+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": []
  },
  "meta": null
}
```
`booking_get_consultation_delivered` (founder: `auto_release_at` is 48 hours after the later of the session end and the moment it was marked):
```json
{
  "data": {
    "id": "85cf4478-2bd0-43d1-9b96-3f274da0b5db",
    "status": "delivered",
    "type": "consultation",
    "listing_id": "125b08b4-0f1d-475c-a443-f6ca79a54e1d",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": "2026-11-12T17:35:33.701684+00:00",
    "ends_at": "2026-11-12T18:35:33.701684+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:33.402344+00:00",
    "due_at": null,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "d59cce13-fd3f-479f-88f2-8dda1e1a9d69",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_df6ef6f821ab68612a1d",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:33+00:00",
      "receipt_number": "MPR-2026-1002",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.718746+00:00",
    "auto_release_at": "2026-11-14T19:35:33.718746+00:00",
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": "0ac10f92-23bd-4aa3-a476-849b115f450d",
    "calendar_ics_url": "/api/v1/marketplace/bookings/85cf4478-2bd0-43d1-9b96-3f274da0b5db/calendar.ics",
    "thread_id": "2c52d00c-1153-4df0-852e-bdf8c0303e03",
    "brief_files": [],
    "created_at": "2026-11-12T19:35:33.363289+00:00",
    "deliveries": [
      {
        "id": "3140fa60-ad5c-4778-bde4-c0be7bdd04fe",
        "round": 1,
        "message": "",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.718746+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": []
  },
  "meta": null
}
```

## The timers (clock moved by SQL; the tick ran in-process)

Once a minute the worker: delivers a consultation whose session ended and was never marked complete; completes a delivery whose `auto_release_at` has passed (`decision: "auto_released"`); flags a package past `due_at` once. The tick returns counters (not an API, shown for the record): `timer_tick_result_session_delivered`:
```json
{
  "expired_unpaid": 0,
  "expired_unanswered": 0,
  "refunded": 0,
  "consultations_delivered": 1,
  "auto_released": 0,
  "overdue_flagged": 0
}
```
`booking_get_session_delivered_by_timer`:
```json
{
  "data": {
    "id": "75b1ef46-f7d5-4373-bbbc-cb4864920fa1",
    "status": "delivered",
    "type": "consultation",
    "listing_id": "125b08b4-0f1d-475c-a443-f6ca79a54e1d",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": "2026-11-12T15:35:33.882510+00:00",
    "ends_at": "2026-11-12T16:35:33.882510+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:33.810366+00:00",
    "due_at": null,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "f71d4c8b-7d01-42d3-a228-e005b1df86bc",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_bd3991ad82d6f64a3217",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:33+00:00",
      "receipt_number": "MPR-2026-1003",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.886626+00:00",
    "auto_release_at": "2026-11-14T19:35:33.886626+00:00",
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": "102a2af8-5995-4a7d-aab2-18591b3da5e6",
    "calendar_ics_url": "/api/v1/marketplace/bookings/75b1ef46-f7d5-4373-bbbc-cb4864920fa1/calendar.ics",
    "thread_id": "d9075ce2-4de2-47ab-8a91-09e51c14103f",
    "brief_files": [],
    "created_at": "2026-11-12T19:35:33.775622+00:00",
    "deliveries": [
      {
        "id": "1a9422c2-f811-4aa9-9d8e-10ba82e9e75f",
        "round": 1,
        "message": "",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.886626+00:00",
        "decision": null,
        "decision_note": null,
        "decided_at": null
      }
    ],
    "documents": []
  },
  "meta": null
}
```
`booking_get_auto_released` (the delivery's `decision` is `auto_released`, the booking `completed`):
```json
{
  "data": {
    "id": "75b1ef46-f7d5-4373-bbbc-cb4864920fa1",
    "status": "completed",
    "type": "consultation",
    "listing_id": "125b08b4-0f1d-475c-a443-f6ca79a54e1d",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": "2026-11-12T15:35:33.882510+00:00",
    "ends_at": "2026-11-12T16:35:33.882510+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:33.810366+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "f71d4c8b-7d01-42d3-a228-e005b1df86bc",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_bd3991ad82d6f64a3217",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:33+00:00",
      "receipt_number": "MPR-2026-1003",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.886626+00:00",
    "auto_release_at": null,
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": "102a2af8-5995-4a7d-aab2-18591b3da5e6",
    "calendar_ics_url": "/api/v1/marketplace/bookings/75b1ef46-f7d5-4373-bbbc-cb4864920fa1/calendar.ics",
    "thread_id": "d9075ce2-4de2-47ab-8a91-09e51c14103f",
    "brief_files": [],
    "created_at": "2026-11-12T19:35:33.775622+00:00",
    "deliveries": [
      {
        "id": "1a9422c2-f811-4aa9-9d8e-10ba82e9e75f",
        "round": 1,
        "message": "",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.886626+00:00",
        "decision": "auto_released",
        "decision_note": null,
        "decided_at": "2026-11-12T19:35:35.610965+00:00"
      }
    ],
    "documents": []
  },
  "meta": null
}
```
`accept_delivery_after_auto_release_200` (a late click is a replay, not an error):
```json
{
  "data": {
    "replayed": true,
    "id": "75b1ef46-f7d5-4373-bbbc-cb4864920fa1",
    "status": "completed",
    "type": "consultation",
    "listing_id": "125b08b4-0f1d-475c-a443-f6ca79a54e1d",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "starts_at": "2026-11-12T15:35:33.882510+00:00",
    "ends_at": "2026-11-12T16:35:33.882510+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-14T19:35:33.810366+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "5831d367-8f08-490c-8f63-d1e0d1538a82",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "f71d4c8b-7d01-42d3-a228-e005b1df86bc",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_bd3991ad82d6f64a3217",
      "checkout_url": null,
      "paid_at": "2026-11-12T19:35:33+00:00",
      "receipt_number": "MPR-2026-1003",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "delivered_at": "2026-11-12T19:35:33.886626+00:00",
    "auto_release_at": null,
    "revisions_used": 0,
    "revisions_left": 2,
    "calendar_event_id": "102a2af8-5995-4a7d-aab2-18591b3da5e6",
    "calendar_ics_url": "/api/v1/marketplace/bookings/75b1ef46-f7d5-4373-bbbc-cb4864920fa1/calendar.ics",
    "thread_id": "d9075ce2-4de2-47ab-8a91-09e51c14103f",
    "brief_files": [],
    "created_at": "2026-11-12T19:35:33.775622+00:00",
    "deliveries": [
      {
        "id": "1a9422c2-f811-4aa9-9d8e-10ba82e9e75f",
        "round": 1,
        "message": "",
        "files": [],
        "submitted_at": "2026-11-12T19:35:33.886626+00:00",
        "decision": "auto_released",
        "decision_note": null,
        "decided_at": "2026-11-12T19:35:35.610965+00:00"
      }
    ],
    "documents": []
  },
  "meta": null
}
```
`timer_tick_result_overdue`:
```json
{
  "expired_unpaid": 0,
  "expired_unanswered": 0,
  "refunded": 0,
  "consultations_delivered": 0,
  "auto_released": 0,
  "overdue_flagged": 1
}
```
`email_provider_overdue`:
```json
{
  "to": "delivered+e2e-978ac02bc1ef@resend.dev",
  "subject": "Overdue: Shareholder agreement drafting",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">This delivery is overdue</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, <strong>Shareholder agreement drafting</strong> has passed its due date. Please submit your work, or message the client if you need more time.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/bookings\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open the booking</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/bookings\" style=\"color:#0F6AFC\">http://localhost:3000/provider/bookings</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">The client can report a problem if a delivery is very late.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-12T19:35:35.874803+00:00"
}
```

## Documents ("Attach from Documents")

`POST /marketplace/bookings/{id}/documents` body `{"document_id"}` (founder; a live document of this workspace; up to 10 per booking; allowed while the booking is `awaiting_provider`, `confirmed`, `in_progress` or `delivered`). The expert may read that one document (read-only: title, kind, version, sections) through the booking for **30 days**. It ends sooner if the founder removes it (`DELETE /marketplace/bookings/{id}/documents/{document_id}`) and **ends for every document when the booking closes** (completed, cancelled, declined, expired). Attaching again after it lapsed starts a fresh 30 days; attaching a live one changes nothing. This is **not** the Documents "share with a person" feature: the expert does not get a Shared-with-me entry and never learns the workspace's name.

`document_attach_team_member_403` and `document_attach_other_workspace_404` are the standard bodies. `document_attach_201`:
```json
{
  "data": {
    "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
    "title": "Business Plan",
    "kind": "business_plan",
    "expires_at": "2026-12-12T19:35:32.629422+00:00",
    "status": "active"
  },
  "meta": null
}
```
`document_list_founder` (`GET /marketplace/bookings/{id}/documents`, team members may read):
```json
{
  "data": {
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active"
      }
    ]
  },
  "meta": null
}
```
`provider_document_read_200` (`GET /provider/bookings/{id}/documents/{document_id}`; trimmed by the document's own sections, kept whole here):
```json
{
  "data": {
    "id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
    "title": "Business Plan",
    "kind": "business_plan",
    "version": 1,
    "sections": [
      {
        "id": "19beb486-4779-4f3c-bb39-db58285e142c",
        "body": "",
        "heading": "Executive Summary"
      },
      {
        "id": "1fab0031-8427-488b-ba3a-a605d7d26c62",
        "body": "",
        "heading": "Problem"
      },
      {
        "id": "308bea22-b510-47dd-9a13-458f544eeb67",
        "body": "",
        "heading": "Solution"
      },
      {
        "id": "9af5116d-e3d0-4a8b-990b-8f9dde97380e",
        "body": "",
        "heading": "Market & Customer"
      },
      {
        "id": "c7f7d578-7694-4e03-88b3-07c625050a72",
        "body": "",
        "heading": "Business Model"
      },
      {
        "id": "6ba7262c-a479-4a71-a65d-bdcc89eef5db",
        "body": "",
        "heading": "Go-to-Market"
      },
      {
        "id": "013795b4-e2ff-408b-a279-6bfaf8953958",
        "body": "",
        "heading": "Team"
      },
      {
        "id": "663e3ed8-6fd9-43d3-b5c3-1e93849ea977",
        "body": "",
        "heading": "Financials"
      },
      {
        "id": "0ed40d84-0184-4509-bdc0-073a8dabb237",
        "body": "",
        "heading": "The Ask"
      }
    ],
    "expires_at": "2026-12-12T19:35:32.629422+00:00"
  },
  "meta": null
}
```
`provider_document_other_expert_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`provider_booking_in_progress_with_documents`:
```json
{
  "data": {
    "id": "4aeef8f7-c785-4acb-ac70-027360090b72",
    "status": "in_progress",
    "type": "package",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "duration_min": null,
    "delivery_days": 7,
    "revisions_included": 2,
    "brief": "We need a shareholder agreement drafted before we close our seed round next month.",
    "client_name": "Ada Obi",
    "starts_at": null,
    "ends_at": null,
    "accept_by": "2026-11-14T19:35:32.371327+00:00",
    "due_at": "2026-11-19T19:35:32.533623+00:00",
    "meeting_url": null,
    "closed_reason": null,
    "earnings": {
      "price_minor": 25000000,
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000,
      "currency": "NGN",
      "paid": true
    },
    "delivered_at": null,
    "auto_release_at": null,
    "revisions_used": 0,
    "thread_id": "5c8d4d70-38e6-4b0b-a749-dc4beede847b",
    "brief_files": [
      {
        "id": "2e619859-1048-4fc6-a17f-9cf33b940874",
        "purpose": "brief",
        "filename": "seed-terms.pdf",
        "mime": "application/pdf",
        "size_bytes": 38,
        "scan_status": "clean",
        "created_at": "2026-11-12T19:35:32.150697+00:00",
        "url": "/api/v1/provider/files/2e619859-1048-4fc6-a17f-9cf33b940874/content"
      }
    ],
    "created_at": "2026-11-12T19:35:32.261450+00:00",
    "deliveries": [],
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "active",
        "url": "/api/v1/provider/bookings/4aeef8f7-c785-4acb-ac70-027360090b72/documents/95a3ef9f-dee0-411f-ad1b-43397076a0d9"
      }
    ]
  },
  "meta": null
}
```
`provider_document_after_completion_404` (the share ended when the booking completed):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
`document_list_after_completion` (founder: `status: "revoked"`):
```json
{
  "data": {
    "documents": [
      {
        "document_id": "95a3ef9f-dee0-411f-ad1b-43397076a0d9",
        "title": "Business Plan",
        "kind": "business_plan",
        "expires_at": "2026-12-12T19:35:32.629422+00:00",
        "status": "revoked"
      }
    ]
  },
  "meta": null
}
```
`document_attach_after_completion_409`:
```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Documents can't be attached to this booking now.",
    "field_errors": []
  }
}
```

## Notifications and emails

Founder in-app notifications (founders only, never team members or mentors; type, then title): `marketplace.booking.confirmed` "Booking confirmed: <service> with <expert> · <date>"; `marketplace.delivery.submitted` "<expert> delivered your order"; `marketplace.delivery.auto_released` "<service> was accepted automatically"; `marketplace.booking.overdue` "<service> is past its due date"; `marketplace.booking.declined` / `.expired` "<expert> could not take this on." (body "You were refunded in full."); `marketplace.message.new` "<expert> sent you a message" (**no message text**; one per conversation per 10 minutes). They use the `marketplace` row in Settings → Notifications (in-app on, email off by default). See `founder_notifications_after_delivery` above for the item shape.

Expert emails: new message (above), changes requested, delivery accepted (also when auto-released), booking cancelled by the founder, overdue. Never the message text.

## Staff: orders that need a person

`GET /admin/marketplace/orders/attention` (scope `moderation`) lists unresolved flags; `?resolved=true` lists resolved ones. `POST /admin/marketplace/orders/{id}/attention/resolve` body `{"reason": "...", "totp_code": "..."}`: the reason is 10+ characters and audited with the caller's address; **`totp_code` is required for money flags** (`payment_mismatch`, `refund_failed`) and not for `unverified_close`. An `unverified_close` also clears itself when the order is later paid-and-revived or fully refunded (unit-tested). **The flags in this run were set by SQL.**

`admin_attention_queue`:
```json
{
  "data": {
    "orders": [
      {
        "id": "f71d4c8b-7d01-42d3-a228-e005b1df86bc",
        "booking_id": "75b1ef46-f7d5-4373-bbbc-cb4864920fa1",
        "reference": "mp_bd3991ad82d6f64a3217",
        "status": "paid",
        "currency": "NGN",
        "amount_minor": 1000000,
        "refunded_minor": 0,
        "refund_due_minor": 0,
        "attention_reason": "payment_mismatch",
        "attention_at": "2026-11-12T19:35:35.965023+00:00",
        "paystack_reported_minor": 1020000,
        "refund_attempts": 0,
        "refund_failed_at": null,
        "resolved_at": null
      },
      {
        "id": "9ef8812e-a1e5-43f0-923f-7309bf0796fc",
        "booking_id": "d5f91238-485b-41df-b3c8-7f45bd36382c",
        "reference": "mp_acc38ae67021c6ceeb0d",
        "status": "paid",
        "currency": "NGN",
        "amount_minor": 25000000,
        "refunded_minor": 0,
        "refund_due_minor": 0,
        "attention_reason": "unverified_close",
        "attention_at": "2026-11-12T19:35:35.944552+00:00",
        "paystack_reported_minor": null,
        "refund_attempts": 0,
        "refund_failed_at": null,
        "resolved_at": null
      }
    ]
  },
  "meta": null
}
```
`admin_resolve_reason_too_short_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "reason",
        "message": "String should have at least 10 characters"
      }
    ]
  }
}
```
`admin_resolve_unverified_close_200`:
```json
{
  "data": {
    "id": "9ef8812e-a1e5-43f0-923f-7309bf0796fc",
    "booking_id": "d5f91238-485b-41df-b3c8-7f45bd36382c",
    "reference": "mp_acc38ae67021c6ceeb0d",
    "status": "paid",
    "currency": "NGN",
    "amount_minor": 25000000,
    "refunded_minor": 0,
    "refund_due_minor": 0,
    "attention_reason": "unverified_close",
    "attention_at": "2026-11-12T19:35:35.944552+00:00",
    "paystack_reported_minor": null,
    "refund_attempts": 0,
    "refund_failed_at": null,
    "resolved_at": "2026-11-12T19:35:36.001215+00:00"
  },
  "meta": null
}
```
`admin_resolve_again_409`:
```json
{
  "error": {
    "code": "NOT_FLAGGED",
    "message": "This order has nothing waiting for review.",
    "field_errors": []
  }
}
```
`admin_resolve_money_flag_needs_code_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Enter your authenticator code to resolve a money flag.",
    "field_errors": [
      {
        "field": "totp_code",
        "message": "Required for this flag."
      }
    ]
  }
}
```
`admin_resolve_payment_mismatch_200`:
```json
{
  "data": {
    "id": "f71d4c8b-7d01-42d3-a228-e005b1df86bc",
    "booking_id": "75b1ef46-f7d5-4373-bbbc-cb4864920fa1",
    "reference": "mp_bd3991ad82d6f64a3217",
    "status": "paid",
    "currency": "NGN",
    "amount_minor": 1000000,
    "refunded_minor": 0,
    "refund_due_minor": 0,
    "attention_reason": "payment_mismatch",
    "attention_at": "2026-11-12T19:35:35.965023+00:00",
    "paystack_reported_minor": 1020000,
    "refund_attempts": 0,
    "refund_failed_at": null,
    "resolved_at": "2026-11-12T19:35:36.039106+00:00"
  },
  "meta": null
}
```
`admin_attention_queue_resolved`:
```json
{
  "data": {
    "orders": [
      {
        "id": "f71d4c8b-7d01-42d3-a228-e005b1df86bc",
        "booking_id": "75b1ef46-f7d5-4373-bbbc-cb4864920fa1",
        "reference": "mp_bd3991ad82d6f64a3217",
        "status": "paid",
        "currency": "NGN",
        "amount_minor": 1000000,
        "refunded_minor": 0,
        "refund_due_minor": 0,
        "attention_reason": "payment_mismatch",
        "attention_at": "2026-11-12T19:35:35.965023+00:00",
        "paystack_reported_minor": 1020000,
        "refund_attempts": 0,
        "refund_failed_at": null,
        "resolved_at": "2026-11-12T19:35:36.039106+00:00"
      },
      {
        "id": "9ef8812e-a1e5-43f0-923f-7309bf0796fc",
        "booking_id": "d5f91238-485b-41df-b3c8-7f45bd36382c",
        "reference": "mp_acc38ae67021c6ceeb0d",
        "status": "paid",
        "currency": "NGN",
        "amount_minor": 25000000,
        "refunded_minor": 0,
        "refund_due_minor": 0,
        "attention_reason": "unverified_close",
        "attention_at": "2026-11-12T19:35:35.944552+00:00",
        "paystack_reported_minor": null,
        "refund_attempts": 0,
        "refund_failed_at": null,
        "resolved_at": "2026-11-12T19:35:36.001215+00:00"
      }
    ]
  },
  "meta": null
}
```

## UX instructions

- **Show the auto-release deadline.** On a `delivered` booking show "You have until <auto_release_at> to accept or ask for changes; after that it is accepted automatically", with a countdown. Package: 7 days; consultation: 48 hours.
- **Show revision rounds left** (`revisions_left`) on the request-changes button ("1 of 2 left"). At 0 hide it and show "Report a problem" (slice E; until it ships, link to support). Do not let the user type a reason longer than 2000 characters.
- **Accepting is final and releases nothing yet**: say "Accepting marks this order complete". Do not claim a payout.
- **Documents expire.** Show "<expert> can read this until <expires_at>" and that it ends when the booking closes; offer "Stop sharing". Show `revoked` links as ended, not as errors.
- **The Calendar link appears only after the expert accepts** a consultation: hide "Add to calendar" while `calendar_ics_url` is null.
- **The two sides download from different paths.** Never build a file URL yourself; use `url`. A 404 on a file means "not available to you" (unsent, another booking, unpaid booking).
- **CSV and text deliveries may contain spreadsheet formulas** (the live run delivered one with a `=1+1` cell). Offer them as a download. Do not open them in a spreadsheet automatically and do not render their contents in the page as HTML.
- **Messages are text.** Escape on render; never `innerHTML`. Do not show the message text in browser notifications you build yourself; the server's notifications deliberately omit it.
- **Team members read only.** Hide the composer, upload, accept and request-changes controls for them (the server answers 403).
- **A closed booking's conversation is read-only** (`read_only`); a completed one is not.
- **Reading marks messages read.** Call `GET .../messages` only when the conversation is actually on screen, or the badge will clear while the user is elsewhere.
- **The 201-versus-closed lesson from slice C still applies:** `POST /marketplace/bookings` answering `201` means a checkout hold exists, not a booking; the booking is real only after payment. Likewise a conversation does not exist for a hold, and a `201` from `POST /marketplace/threads` is a conversation with no messages.

## Verification table

| Behaviour | Verified live (`e2e/test_marketplace_fulfilment.py`) | Unit test only | Not exercised |
|---|---|---|---|
| Brief file upload, bad type / purpose / team member refused | yes | yes | |
| Booking with `file_ids`, bad id refused before creating anything | yes (bad id, success) | yes (replay does not re-attach, limits, ownership) | |
| File access matrix (owner, team member, other workspace, other expert, unpaid booking) | yes | yes (adds suspended expert, mentor) | |
| Pre-booking conversation, moving onto the booking at payment, unread counts, mark read, paging | yes | yes | |
| Posting guards (team member 403, empty, other workspace / expert 404) | yes | yes (limits, closed threads, 4000 chars, files) | |
| Message email once per window, no text, name escaped | yes (first email, no text) | yes (the 10-minute window and escaping) | |
| Founder message notification once per 10 minutes, no text | | yes | live |
| Posting rate limit (30/min), opening limit (20/min), 20 new conversations per day | | yes | live (limits are raised in the e2e server) |
| Deliver with a file, twice refused | yes | yes | |
| Accept delivery (200, replay, no money moved, expert email) | yes | yes | |
| Request changes: reason, replay, emails escaped, round counting, `REVISION_LIMIT_REACHED` | yes | yes | |
| Listing snapshot decides the rounds | | yes | |
| Consultation: Calendar event, `.ics`, no `.ics` before accept, mentor / other workspace refused | yes | yes | the invite email to the expert (worker job) is unit-tested only |
| Calendar event removed and ICS CANCEL sent when a booking closes after accept | | yes | no live path reaches it before slice E |
| Calendar failure never undoes an accept | | yes | |
| `complete` before the session ends refused | yes | yes | |
| `complete` after the session ends | yes, **session ended by SQL** | yes | a genuinely elapsed session |
| Timer: unmarked session delivered, delivery auto-released, accept afterwards is a replay | yes, **clock moved by SQL, tick run in-process** | yes (with a controlled clock; not delivered and released in one pass; a late worker still gives 48 hours) | the worker process itself |
| Timer: overdue package flagged once, state unchanged | yes, **due date moved by SQL, tick in-process** | yes | |
| Documents: attach, expert read, other expert 404, revoke on completion | yes | yes (30-day expiry, trashed, limit 10, every ending, idempotent) | |
| Founder in-app notifications (delivery, auto-release, overdue, confirmed) | yes (delivery, confirmed, auto-release, overdue seen in the inbox) | yes (none to team member / mentor, preferences switch) | |
| Staff attention queue, resolve, step-up for money flags, `?resolved=true` | yes, **flags set by SQL** | yes (auto-clear on revive and on full refund, re-opening, audit with IP) | a flag produced by a real Paystack mismatch |
| Slice C lows: log wording, late-mismatch email once | | yes | |
| Real Paystack, real email delivery, real scanner (`FILE_NOT_CLEAN`) | | | yes (stubbed / no-op scanner) |
