# FE integration guide: Marketplace listings, availability and the founder directory (slice B)

Every body below is pasted from a live run against a real server: `e2e/test_marketplace_listings.py`, captures in `e2e/_captures/marketplace_listings/<name>.json` (the block title is the capture name). Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`, **except the hidden 404 on staff routes, which is `{"detail": "Not Found"}`**. Where something could not be exercised live, the section says so and names where its shape comes from. Companion guide for the expert accounts and vetting: `docs/fe-integration-guide-marketplace-providers.md`.

Slice B is **browse only**: no booking, payment, messaging or reviews yet (slices C to F), and the Marketplace stays hidden in the sidebar. The founder pages can be built against these routes now, with the Book button disabled until slice C.

## What this is, in FE terms

1. **Expert portal additions (`/provider/*`)**: Listings (create, edit, submit, pause, resume, delete) and Availability (weekly hours, date exceptions, notice, buffer). Approved experts only.
2. **Admin console additions (`/admin/marketplace/listings*`, scope `moderation`)**: a listing review queue with a diff for edits, and approve / reject / request changes / unlist / relist. Reported listings arrive in the existing Reports tab.
3. **Founder app (`/marketplace/*`)**: expert directory (cards, filters, sort), expert profile, service listings, and open time slots for a consultation. Founders **and team members** can read; every other role gets 403.

## The visibility rule (the FE can rely on it)

A founder sees an expert only while the expert is **approved**, and sees a listing only while it is **active** and its expert is approved. A suspended expert vanishes on the next request. An expert with no active listing is not in the directory and has no profile (404). Unlisted, paused, draft, pending and rejected listings are 404. A listing whose edit is waiting for review shows its **live** (approved) version.

Founder payloads are an allow-list. They never contain: the expert's email or user id, vetting notes, pre-screen flags, commission, review messages, the meeting link, a pending edit, or listing status. Do not look for them.

## Listing state machine (the FE renders from `status`)

| status | Meaning | Expert can | Founders see it |
|---|---|---|---|
| `draft` | Being written (also what "request changes" returns a first submission to) | edit everything, submit, delete | no |
| `pending_review` | First submission waiting for staff | nothing (409 `LISTING_LOCKED`) | no |
| `active` | Approved and live | edit (see below), pause | yes |
| `paused` | The expert paused it | edit, resume | no |
| `unlisted` | Staff took it down | nothing (403 `LISTING_UNLISTED`) | no |
| `rejected` | Staff said no to a first submission | edit, then submit again, or delete | no |

**Editing a live listing (D-MP-L8, accepted by design).** `price_minor`, `duration_min`, `delivery_days`, `revisions_included`, `meeting_url` and `tags` change **immediately**. `title`, `summary`, `description`, `category` and `deliverables` do not: they are stored as `pending_revision` and go to the staff queue while the approved version stays live. The expert's own view shows both (`title` is still the live one, `pending_revision.title` is the proposed one). Staff approve (the edit goes live), or reject / request changes (the edit is discarded and the live listing is untouched; `review_message` says why). An edit that sets a field back to its live value is not a change.

**A flagged at-once edit.** Price and tags go live immediately, but if the deterministic pre-screen trips on them (a phone number or "WhatsApp" in the tags, a price below N1,000 or above N10,000,000) and the flag is new compared with what staff last approved, the listing **stays live** and appears in the staff queue as a flagged edit (`has_flag`). Staff approve it (that becomes the new baseline, so the same flag is not queued again), send a message back (the item closes, the listing stays live), or unlist it. The expert never sees `flag`. A booking (slice C) will snapshot price and text at purchase.

## Money and shapes

All prices are integers in **kobo** (`price_minor`, NGN only). Send integers: a string or a float is a 422. The expert's `earnings_preview` is `{commission_bps, commission_minor, net_minor}`: 15% off the expert's side by default, rounded half up to the kobo, and `commission_minor + net_minor` is always the price. The founder pays exactly the listed price.

Page-numbered lists (the founder directory) return `data.pagination = {page, page_size, total}` (12 per page) and `meta: null`. Cursor lists (Admin) use `meta.next_cursor`.

### Type rules (consultation vs package)

Fields belong to a type: a consultation has `duration_min` and `meeting_url`; a package has `delivery_days` and `deliverables`. Sending a field for the other type is 422 `FIELD_NOT_FOR_TYPE` (on create and on edit; sending it empty is harmless). A **live** (active or paused) listing cannot lose the fields its type needs: nulling `duration_min`, `meeting_url`, `delivery_days` or emptying `deliverables` is 422 `FIELD_REQUIRED`. A draft may be emptied; it simply cannot be submitted that way.

`listing_field_not_for_type_422`:
```json
{
  "error": {
    "code": "FIELD_NOT_FOR_TYPE",
    "message": "'duration_min' doesn't apply to a package.",
    "field_errors": []
  }
}
```

`listing_live_remove_required_field_422`:
```json
{
  "error": {
    "code": "FIELD_REQUIRED",
    "message": "A live consultation needs 'duration_min'. Pause it to remove that.",
    "field_errors": []
  }
}
```

## Expert: availability

Weekly windows and date exceptions are **wall-clock times in the expert's own timezone** (`timezone` on their profile; change it with `PATCH /provider/me`). `weekday` is 0 = Monday to 6 = Sunday. `slot_min` (15, 30 or 60) is how often a session may start inside a window. `min_notice_h` hides slots that start too soon; `buffer_min` keeps a gap around bookings (bookings arrive in slice C, so nothing is booked yet). Exceptions: `blocked` with no times blocks the whole day, with times blocks that window; `extra` opens extra hours and needs both times. `PUT` **replaces the whole schedule** and its settings in one transaction; a rejected PUT changes nothing.

`availability_empty_200` (a new expert; defaults):
```json
{
  "data": {
    "timezone": "Africa/Lagos",
    "min_notice_h": 24,
    "buffer_min": 0,
    "rules": [],
    "exceptions": []
  },
  "meta": null
}
```

`availability_put_200`:
```json
{
  "data": {
    "timezone": "Africa/Lagos",
    "min_notice_h": 0,
    "buffer_min": 15,
    "rules": [
      {
        "weekday": 0,
        "start": "09:00",
        "end": "13:00",
        "slot_min": 60
      },
      {
        "weekday": 1,
        "start": "09:00",
        "end": "13:00",
        "slot_min": 60
      },
      {
        "weekday": 2,
        "start": "09:00",
        "end": "13:00",
        "slot_min": 60
      },
      {
        "weekday": 3,
        "start": "09:00",
        "end": "13:00",
        "slot_min": 60
      },
      {
        "weekday": 4,
        "start": "09:00",
        "end": "13:00",
        "slot_min": 60
      }
    ],
    "exceptions": [
      {
        "on_date": "2026-11-10",
        "kind": "blocked",
        "start": null,
        "end": null
      }
    ]
  },
  "meta": null
}
```

`availability_end_before_start_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Each weekly window must end after it starts.",
    "field_errors": []
  }
}
```

`availability_bad_slot_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "rules.0.slot_min",
        "message": "Input should be 15, 30 or 60"
      }
    ]
  }
}
```

Not exercised live (unit-tested in `tests/api/test_marketplace_listings.py`): the other validation cases (past dates, an exception with only one time, `extra` without times, more than 28 rules, unknown fields, seconds in a time), and that a failed PUT keeps the old schedule.

## Expert: listings

Auth: `require_provider(approved=True)`. A signed-in non-expert gets 403 `NOT_A_PROVIDER`; an expert who is not approved (still in review, or suspended) gets 403 `PROVIDER_NOT_APPROVED`; no token is 401.

`provider_listings_anonymous_401`:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

`provider_listings_not_a_provider_403`:
```json
{
  "error": {
    "code": "NOT_A_PROVIDER",
    "message": "This account isn't a Marketplace expert.",
    "field_errors": []
  }
}
```

`provider_listings_suspended_403` (an expert who was suspended):
```json
{
  "error": {
    "code": "PROVIDER_NOT_APPROVED",
    "message": "Your expert profile isn't approved yet.",
    "field_errors": []
  }
}
```

### `POST /provider/listings` (201)

Body: `type` (`consultation` or `package`, fixed after creation), `title` (3-160), `category`, `price_minor` are required; `summary` (max 300), `description` (max 5000), `tags` (max 8), `duration_min` (15 to 240 in steps of 15, consultations), `meeting_url` (http/https, consultations), `delivery_days` (1 to 90) and `deliverables` (max 10 strings) for packages, `revisions_included` (0 to 5, default 2). Unknown fields are a 422. A listing starts as a `draft`. Limit 20 listings per expert (409 `LISTING_LIMIT_REACHED`). **Category rule:** Legal and Accounting listings need that to be the expert's vetted main category; any other category must be their main or a secondary one. Otherwise 422 `CATEGORY_NOT_ALLOWED`.

`listing_create_consultation_201`:
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "type": "consultation",
    "status": "draft",
    "title": "Contract review session",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [
      "contracts",
      "term sheets"
    ],
    "price_minor": 1000000,
    "currency": "NGN",
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "pending_revision": null,
    "review_message": null,
    "submitted_at": null,
    "published_at": null,
    "unlisted_reason": null,
    "earnings_preview": {
      "commission_bps": 1500,
      "commission_minor": 150000,
      "net_minor": 850000
    },
    "created_at": "2026-11-05T20:51:59.361749+00:00",
    "updated_at": "2026-11-05T20:51:59.361749+00:00"
  },
  "meta": null
}
```

`listing_price_as_string_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "price_minor",
        "message": "Input should be a valid integer"
      }
    ]
  }
}
```

`listing_category_not_allowed_422`:
```json
{
  "error": {
    "code": "CATEGORY_NOT_ALLOWED",
    "message": "You can only list services in the categories you were vetted for.",
    "field_errors": []
  }
}
```

`listing_bad_duration_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "duration_min",
        "message": "Input should be a multiple of 15"
      }
    ]
  }
}
```

### `PATCH /provider/listings/{id}`, `GET`, `DELETE`, `GET /provider/listings`

`PATCH` sends only what changed; `title`, `summary`, `category` and `price_minor` cannot be `null`; `tags` / `deliverables` set to `null` clear the list. `DELETE` works only on a `draft` or `rejected` listing (409 `INVALID_STATE` otherwise: pause it instead). Another expert's listing id is a 404.

`listing_patch_draft_200`:
```json
{
  "data": {
    "id": "b89c22dd-2c7d-4498-b7c4-e103a4c26491",
    "type": "package",
    "status": "draft",
    "title": "Shareholder agreement drafting",
    "summary": "A tailored shareholder agreement for your startup.",
    "description": "A focused tailored agreement drafting your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [],
    "price_minor": 25000000,
    "currency": "NGN",
    "duration_min": null,
    "delivery_days": 7,
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "revisions_included": 2,
    "meeting_url": null,
    "pending_revision": null,
    "review_message": null,
    "submitted_at": null,
    "published_at": null,
    "unlisted_reason": null,
    "earnings_preview": {
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000
    },
    "created_at": "2026-11-05T20:51:59.380263+00:00",
    "updated_at": "2026-11-05T20:51:59.409666+00:00"
  },
  "meta": null
}
```

`listing_live_edit_200` (a live listing: `price_minor` already changed, `title` still the live value, the proposed title in `pending_revision`):
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "type": "consultation",
    "status": "active",
    "title": "Contract review session",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [
      "contracts",
      "term sheets"
    ],
    "price_minor": 1200000,
    "currency": "NGN",
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "pending_revision": {
      "title": "Contract and term sheet review"
    },
    "review_message": null,
    "submitted_at": "2026-11-05T20:51:59.428576+00:00",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "unlisted_reason": null,
    "earnings_preview": {
      "commission_bps": 1500,
      "commission_minor": 180000,
      "net_minor": 1020000
    },
    "created_at": "2026-11-05T20:51:59.361749+00:00",
    "updated_at": "2026-11-05T20:51:59.764020+00:00"
  },
  "meta": null
}
```

`listing_patch_pending_review_409`:
```json
{
  "error": {
    "code": "LISTING_LOCKED",
    "message": "This listing is under review. You can edit it after the decision.",
    "field_errors": []
  }
}
```

`listing_patch_unlisted_403`:
```json
{
  "error": {
    "code": "LISTING_UNLISTED",
    "message": "This listing was taken down. Contact support.",
    "field_errors": []
  }
}
```

`listings_mine_200`:
```json
{
  "data": {
    "listings": [
      {
        "id": "b89c22dd-2c7d-4498-b7c4-e103a4c26491",
        "type": "package",
        "status": "pending_review",
        "title": "Shareholder agreement drafting",
        "summary": "A tailored shareholder agreement for your startup.",
        "description": "A focused tailored agreement drafting your contract or term sheet, with written notes after.",
        "category": "Legal",
        "tags": [],
        "price_minor": 25000000,
        "currency": "NGN",
        "duration_min": null,
        "delivery_days": 7,
        "deliverables": [
          "Shareholder agreement (Word)",
          "One round of edits"
        ],
        "revisions_included": 2,
        "meeting_url": null,
        "pending_revision": null,
        "review_message": null,
        "submitted_at": "2026-11-05T20:51:59.442582+00:00",
        "published_at": null,
        "unlisted_reason": null,
        "earnings_preview": {
          "commission_bps": 1500,
          "commission_minor": 3750000,
          "net_minor": 21250000
        },
        "created_at": "2026-11-05T20:51:59.380263+00:00",
        "updated_at": "2026-11-05T20:51:59.438820+00:00"
      },
      {
        "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
        "type": "consultation",
        "status": "pending_review",
        "title": "Contract review session",
        "summary": "A one hour review of your contract with written notes.",
        "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
        "category": "Legal",
        "tags": [
          "contracts",
          "term sheets"
        ],
        "price_minor": 1000000,
        "currency": "NGN",
        "duration_min": 60,
        "delivery_days": null,
        "deliverables": [],
        "revisions_included": 2,
        "meeting_url": "https://meet.example.com/ngozi-eze",
        "pending_revision": null,
        "review_message": null,
        "submitted_at": "2026-11-05T20:51:59.428576+00:00",
        "published_at": null,
        "unlisted_reason": null,
        "earnings_preview": {
          "commission_bps": 1500,
          "commission_minor": 150000,
          "net_minor": 850000
        },
        "created_at": "2026-11-05T20:51:59.361749+00:00",
        "updated_at": "2026-11-05T20:51:59.424812+00:00"
      }
    ]
  },
  "meta": null
}
```

### Submit, pause, resume

`POST /provider/listings/{id}/submit` (draft or rejected → `pending_review`) returns the listing; missing pieces come back as `field_errors` (`summary` 20+ chars, `description` 50+ chars; consultations need `duration_min` and `meeting_url`; packages need `delivery_days` and `deliverables`). `POST …/pause` (active → paused) and `POST …/resume` (paused → active) return the listing; any other state is 409 `INVALID_STATE`. Submit success returns the listing with `status: "pending_review"` (shape as above; unit-tested, not captured separately).

`listing_submit_incomplete_422`:
```json
{
  "error": {
    "code": "SUBMISSION_INCOMPLETE",
    "message": "A few things are still needed before you can submit.",
    "field_errors": [
      {
        "field": "description",
        "message": "Describe the service in at least 50 characters."
      },
      {
        "field": "delivery_days",
        "message": "Say how many days delivery takes."
      },
      {
        "field": "deliverables",
        "message": "List at least one deliverable."
      }
    ]
  }
}
```

`listing_pause_200`:
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "type": "consultation",
    "status": "paused",
    "title": "Contract and term sheet review",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [
      "contracts",
      "whatsapp me"
    ],
    "price_minor": 1200000,
    "currency": "NGN",
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "pending_revision": null,
    "review_message": null,
    "submitted_at": "2026-11-05T20:51:59.428576+00:00",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "unlisted_reason": null,
    "earnings_preview": {
      "commission_bps": 1500,
      "commission_minor": 180000,
      "net_minor": 1020000
    },
    "created_at": "2026-11-05T20:51:59.361749+00:00",
    "updated_at": "2026-11-05T20:51:59.889236+00:00"
  },
  "meta": null
}
```

`listing_mine_rejected_with_message` (after staff rejected it; `review_message` is the text to show):
```json
{
  "data": {
    "id": "b89c22dd-2c7d-4498-b7c4-e103a4c26491",
    "type": "package",
    "status": "rejected",
    "title": "Shareholder agreement drafting",
    "summary": "A tailored shareholder agreement for your startup.",
    "description": "A focused tailored agreement drafting your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [],
    "price_minor": 25000000,
    "currency": "NGN",
    "duration_min": null,
    "delivery_days": 7,
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "revisions_included": 2,
    "meeting_url": null,
    "pending_revision": null,
    "review_message": "N250,000 looks too high for the deliverables listed. Please revise.",
    "submitted_at": "2026-11-05T20:51:59.442582+00:00",
    "published_at": null,
    "unlisted_reason": null,
    "earnings_preview": {
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000
    },
    "created_at": "2026-11-05T20:51:59.380263+00:00",
    "updated_at": "2026-11-05T20:51:59.560854+00:00"
  },
  "meta": null
}
```

`listing_mine_unlisted` (after a report was upheld; `unlisted_reason` is shown to the expert):
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "type": "consultation",
    "status": "unlisted",
    "title": "Contract and term sheet review",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [
      "contracts",
      "whatsapp me"
    ],
    "price_minor": 1200000,
    "currency": "NGN",
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "pending_revision": null,
    "review_message": null,
    "submitted_at": "2026-11-05T20:51:59.428576+00:00",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "unlisted_reason": "Unlisted after a report.",
    "earnings_preview": {
      "commission_bps": 1500,
      "commission_minor": 180000,
      "net_minor": 1020000
    },
    "created_at": "2026-11-05T20:51:59.361749+00:00",
    "updated_at": "2026-11-05T20:51:59.966790+00:00"
  },
  "meta": null
}
```

## Admin: listing review

Guard: active staff with TOTP and scope `moderation` (Super Admin passes). Others get the hidden 404, or 403 for the wrong scope. **Staff cannot act on their own listings** (422 `CANNOT_ACT_ON_SELF`, unit-tested). Approve, reject and request-changes need a reason only; **unlist and relist also need the staff member's own `totp_code`** (checked after the listing exists, so a wrong id never burns a code).

`admin_listings_wrong_scope_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

`admin_listing_counts`:
```json
{
  "data": {
    "by_status": {
      "draft": 0,
      "pending_review": 2,
      "active": 0,
      "paused": 0,
      "unlisted": 0,
      "rejected": 0
    },
    "pending_revisions": 0,
    "flagged_edits": 0
  },
  "meta": null
}
```

### `GET /admin/marketplace/listings?status&queue&query&limit&cursor`

`queue=true` returns what needs a decision: first submissions (`pending_review`), live listings with a pending edit (`has_revision: true`) and live listings with a flagged at-once edit (`has_flag: true`, details in `flag`). `GET …/listings/counts` also returns `flagged_edits`. Oldest first; `meta.next_cursor` pages. `prescreen_flags` are deterministic chips (`contact_details`, `guaranteed_outcome`, `prohibited_content`, `price_out_of_range`, each with the `field`); they never block anything.

`admin_listing_queue`:
```json
{
  "data": {
    "listings": [
      {
        "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
        "provider_id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
        "provider_name": "Ngozi Eze",
        "provider_status": "approved",
        "type": "consultation",
        "title": "Contract review session",
        "category": "Legal",
        "price_minor": 1000000,
        "status": "pending_review",
        "has_revision": false,
        "has_flag": false,
        "flag": null,
        "submitted_at": "2026-11-05T20:51:59.428576+00:00",
        "revision_submitted_at": null,
        "prescreen_flags": [],
        "created_at": "2026-11-05T20:51:59.361749+00:00"
      },
      {
        "id": "b89c22dd-2c7d-4498-b7c4-e103a4c26491",
        "provider_id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
        "provider_name": "Ngozi Eze",
        "provider_status": "approved",
        "type": "package",
        "title": "Shareholder agreement drafting",
        "category": "Legal",
        "price_minor": 25000000,
        "status": "pending_review",
        "has_revision": false,
        "has_flag": false,
        "flag": null,
        "submitted_at": "2026-11-05T20:51:59.442582+00:00",
        "revision_submitted_at": null,
        "prescreen_flags": [],
        "created_at": "2026-11-05T20:51:59.380263+00:00"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

`admin_listing_queue_flagged_edit` (a live listing whose tags tripped the pre-screen; it is still live, and `flag.flags` says why):
```json
{
  "data": {
    "listings": [
      {
        "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
        "provider_id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
        "provider_name": "Ngozi Eze",
        "provider_status": "approved",
        "type": "consultation",
        "title": "Contract and term sheet review",
        "category": "Legal",
        "price_minor": 1200000,
        "status": "active",
        "has_revision": false,
        "has_flag": true,
        "flag": {
          "at": "2026-11-05T20:51:59.833758+00:00",
          "flags": [
            {
              "rule": "contact_details",
              "field": "tags"
            }
          ],
          "updated_at": "2026-11-05T20:51:59.833758+00:00"
        },
        "submitted_at": "2026-11-05T20:51:59.428576+00:00",
        "revision_submitted_at": null,
        "prescreen_flags": [
          {
            "rule": "contact_details",
            "field": "tags"
          }
        ],
        "created_at": "2026-11-05T20:51:59.361749+00:00"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

`admin_listing_queue_revision` (after the live listing was edited):
```json
{
  "data": {
    "listings": [
      {
        "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
        "provider_id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
        "provider_name": "Ngozi Eze",
        "provider_status": "approved",
        "type": "consultation",
        "title": "Contract review session",
        "category": "Legal",
        "price_minor": 1200000,
        "status": "active",
        "has_revision": true,
        "has_flag": false,
        "flag": null,
        "submitted_at": "2026-11-05T20:51:59.428576+00:00",
        "revision_submitted_at": "2026-11-05T20:51:59.767830+00:00",
        "prescreen_flags": [],
        "created_at": "2026-11-05T20:51:59.361749+00:00"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

### `GET /admin/marketplace/listings/{id}`

The full listing, the expert's summary and `badge`, the audit `history`, and for an edit the `diff` (`[{field, from, to}]`) next to `pending_revision`.

`admin_listing_detail_pending`:
```json
{
  "data": {
    "id": "b89c22dd-2c7d-4498-b7c4-e103a4c26491",
    "provider_id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
    "provider_name": "Ngozi Eze",
    "provider_status": "approved",
    "type": "package",
    "title": "Shareholder agreement drafting",
    "category": "Legal",
    "price_minor": 25000000,
    "status": "pending_review",
    "has_revision": false,
    "has_flag": false,
    "flag": null,
    "submitted_at": "2026-11-05T20:51:59.442582+00:00",
    "revision_submitted_at": null,
    "prescreen_flags": [],
    "created_at": "2026-11-05T20:51:59.380263+00:00",
    "summary": "A tailored shareholder agreement for your startup.",
    "description": "A focused tailored agreement drafting your contract or term sheet, with written notes after.",
    "tags": [],
    "duration_min": null,
    "delivery_days": 7,
    "deliverables": [
      "Shareholder agreement (Word)",
      "One round of edits"
    ],
    "revisions_included": 2,
    "meeting_url": null,
    "review_message": null,
    "unlisted_reason": null,
    "published_at": null,
    "pending_revision": null,
    "diff": [],
    "provider": {
      "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
      "display_name": "Ngozi Eze",
      "category": "Legal",
      "status": "approved",
      "badge": {
        "vetted": true,
        "checks": [
          "Identity verified",
          "Professional licence verified",
          "2 client references checked"
        ]
      }
    },
    "history": [
      {
        "action": "marketplace.listing.submitted",
        "at": "2026-11-05T20:51:59.438820+00:00",
        "actor_email": "delivered+e2e-5b5aabf98398@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.listing.created",
        "at": "2026-11-05T20:51:59.380263+00:00",
        "actor_email": "delivered+e2e-5b5aabf98398@resend.dev",
        "reason": null
      }
    ]
  },
  "meta": null
}
```

`admin_listing_detail_revision`:
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "provider_id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
    "provider_name": "Ngozi Eze",
    "provider_status": "approved",
    "type": "consultation",
    "title": "Contract review session",
    "category": "Legal",
    "price_minor": 1200000,
    "status": "active",
    "has_revision": true,
    "has_flag": false,
    "flag": null,
    "submitted_at": "2026-11-05T20:51:59.428576+00:00",
    "revision_submitted_at": "2026-11-05T20:51:59.767830+00:00",
    "prescreen_flags": [],
    "created_at": "2026-11-05T20:51:59.361749+00:00",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "tags": [
      "contracts",
      "term sheets"
    ],
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "review_message": null,
    "unlisted_reason": null,
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "pending_revision": {
      "title": "Contract and term sheet review"
    },
    "diff": [
      {
        "field": "title",
        "from": "Contract review session",
        "to": "Contract and term sheet review"
      }
    ],
    "provider": {
      "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
      "display_name": "Ngozi Eze",
      "category": "Legal",
      "status": "approved",
      "badge": {
        "vetted": true,
        "checks": [
          "Identity verified",
          "Professional licence verified",
          "2 client references checked"
        ]
      }
    },
    "history": [
      {
        "action": "marketplace.listing.revision_submitted",
        "at": "2026-11-05T20:51:59.764020+00:00",
        "actor_email": "delivered+e2e-5b5aabf98398@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.listing.approved",
        "at": "2026-11-05T20:51:59.532015+00:00",
        "actor_email": "delivered+e2e-5ec8266ed33c@resend.dev",
        "reason": "Scope and price are clear"
      },
      {
        "action": "marketplace.listing.submitted",
        "at": "2026-11-05T20:51:59.424812+00:00",
        "actor_email": "delivered+e2e-5b5aabf98398@resend.dev",
        "reason": null
      },
      {
        "action": "marketplace.listing.created",
        "at": "2026-11-05T20:51:59.361749+00:00",
        "actor_email": "delivered+e2e-5b5aabf98398@resend.dev",
        "reason": null
      }
    ]
  },
  "meta": null
}
```

### Decisions

- `POST …/approve` `{"reason"}`: a first submission goes live (and the expert is emailed); an edit is applied; a flagged at-once edit is accepted as the new baseline. The expert must be approved themselves (409 `PROVIDER_NOT_APPROVED`).
- `POST …/reject` `{"template", "message"?, "reason"}`, `template` one of `unclear_scope`, `pricing`, `prohibited_content`, `contact_details`, `other`: a first submission becomes `rejected`; an edit is discarded; a flagged at-once edit closes the queue item with the message (the listing stays live, so unlist it to take it down).
- `POST …/request-changes` `{"message"}` (10-1000 chars): a first submission goes back to `draft`; an edit is discarded.
- `POST …/unlist` and `…/relist` `{"reason", "totp_code"}`.
Each returns the detail. Anything not waiting for a decision is 409 `INVALID_STATE`.

`admin_listing_approve_reason_too_short_422`:
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

`admin_listing_approve_200`:
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "status": "active",
    "title": "Contract review session",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "review_message": null,
    "unlisted_reason": null,
    "pending_revision": null,
    "price_minor": 1000000
  }
}
```

`email_listing_approved`:
```json
{
  "to": "delivered+e2e-5b5aabf98398@resend.dev",
  "subject": "Your Marketplace listing is live",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px\">Approved</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, your listing <strong>Contract review session</strong> has been approved and is live.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/listings\" style=\"display:inline-block;padding:12px 22px;background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open your listings</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/listings\" style=\"color:#4f46e5\">http://localhost:3000/provider/listings</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">Founders can find it in the Marketplace now.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-05T20:51:59.540337+00:00"
}
```

`admin_listing_approve_revision_200` (an edit approved; trimmed):
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "status": "active",
    "title": "Contract and term sheet review",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "review_message": null,
    "unlisted_reason": null,
    "pending_revision": null,
    "price_minor": 1200000
  }
}
```

`admin_listing_reject_bad_template_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "template",
        "message": "Input should be 'unclear_scope', 'pricing', 'prohibited_content', 'contact_details' or 'other'"
      }
    ]
  }
}
```

`admin_listing_reject_200` (trimmed):
```json
{
  "data": {
    "id": "b89c22dd-2c7d-4498-b7c4-e103a4c26491",
    "status": "rejected",
    "title": "Shareholder agreement drafting",
    "published_at": null,
    "review_message": "N250,000 looks too high for the deliverables listed. Please revise.",
    "unlisted_reason": null,
    "pending_revision": null,
    "price_minor": 25000000
  }
}
```

`admin_listing_unlist_200` (trimmed):
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "status": "unlisted",
    "title": "Contract and term sheet review",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "review_message": null,
    "unlisted_reason": "Reviewed against the marketplace rules",
    "pending_revision": null,
    "price_minor": 1200000
  }
}
```

`admin_listing_relist_200` (trimmed):
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "status": "active",
    "title": "Contract and term sheet review",
    "published_at": "2026-11-05T20:51:59.538082+00:00",
    "review_message": null,
    "unlisted_reason": null,
    "pending_revision": null,
    "price_minor": 1200000
  }
}
```

`admin_listing_unlist_twice_409`:
```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Only a live listing can be unlisted.",
    "field_errors": []
  }
}
```

## Reports on listings and experts

`POST /reports` now also takes `item_type: "listing"` or `"provider"` (the ids from the directory). Rules: the item must be visible to the reporter (else 404); you cannot report your own listing or profile (422 `OWN_ITEM`); one live report per person per item (409 `ALREADY_REPORTED`); `"review"` is **not** a report type yet (it joins in slice F), so it is a plain 422 `VALIDATION_ERROR`. In the Admin Reports tab the item shows as `listing` / `provider`. **Remove** on a reported listing **unlists** it (with the usual authenticator code) and **restore** relists it **only if the report flow itself did the unlisting**: a listing a moderator unlisted for their own reasons (or unlisted again later) stays down when a report about it is restored. Both directions write a `marketplace_listing` audit row (`via: report`). Staff cannot remove, restore, **dismiss or escalate** a report about their own listing or profile (422 `CANNOT_ACT_ON_SELF`). **Remove** on a reported expert is refused with 422 `USE_VETTING` (suspend them from the vetting queue instead); dismiss and escalate work as for any report.

`report_listing_201`:
```json
{
  "data": {
    "id": "7cbddcda-eaf1-4c16-8b46-8150197d295c",
    "item_type": "listing",
    "item_id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "reason": "other",
    "status": "received",
    "created_at": "2026-11-05T20:51:59.931527+00:00"
  },
  "meta": null
}
```

`report_review_not_reportable_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "item_type",
        "message": "Input should be 'ai_message', 'comment', 'listing' or 'provider'"
      }
    ]
  }
}
```

`admin_reports_queue_with_listing` (shown trimmed to the first report; the list also holds others from the shared database):
```json
{
  "data": {
    "reports": [
      {
        "id": "7cbddcda-eaf1-4c16-8b46-8150197d295c",
        "item_type": "listing",
        "reason": "other",
        "note": "Misleading price",
        "status": "open",
        "snippet": "Contract and term sheet review\nA one hour review of your contract with written notes.",
        "snippet_truncated": false,
        "reporter": {
          "id": "4b7a5bfe-fa01-49fd-ba6d-9bebc0e9ad66",
          "name": "Ada Obi",
          "email": "delivered+e2e-f531c105b386@resend.dev"
        },
        "author": {
          "id": "4fc2d63c-e645-40ea-859b-884847a13fe0",
          "name": "delivered+e2e-5b5aabf98398@resend.dev"
        },
        "workspace": {
          "id": "8154af4b-112a-435e-816d-3fbe408349a8",
          "name": "Browse Co",
          "plan_key": "starter"
        },
        "created_at": "2026-11-05T20:51:59.931527+00:00",
        "escalated_at": null,
        "decided_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

`admin_report_remove_listing_200` (trimmed):
```json
{
  "data": {
    "id": "7cbddcda-eaf1-4c16-8b46-8150197d295c",
    "status": "removed",
    "item_type": "listing",
    "decided_at": "2026-11-05T20:51:59.976587+00:00"
  }
}
```

## Founder: the directory (`/marketplace/*`)

Needs a signed-in, verified account and `X-Workspace-Id`; role **founder or team member** (D-MP-L1), everyone else (mentor, accountant, legal advisor, investor) 403, no token 401. An expert account with no workspace cannot browse.

`directory_anonymous_401`:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

`directory_mentor_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### `GET /marketplace/providers?category&min_price&max_price&min_rating&language&available&q&sort&page`

Expert cards. `category` matches the expert's main or secondary category; `min_price` / `max_price` (kobo) keep experts with a live listing in that band, but `from_price_minor` is always the expert's cheapest live listing; `min_rating` only counts experts with 3+ reviews; `language` is case-insensitive; `available=true` keeps experts with a bookable slot in the next 7 days; `q` matches name, headline or a listing title (wildcard characters are literal). `sort`: `relevance` (default, deterministic), `rating`, `price_asc`, `price_desc`; an unknown value is a 422. `rating` is `{avg, count, is_new}`: **`avg` is `null` and `is_new` true until an expert has 3 reviews, so show "New", never 0 stars**. `badge` is rendered from `badge.checks`; never hard-code tooltip copy. `next_available_at` is the first open slot in the next two weeks (UTC), or `null`. `avatar_url` is a path to fetch with the same auth headers.

`directory_providers_200`:
```json
{
  "data": {
    "providers": [
      {
        "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
        "display_name": "Ngozi Eze",
        "headline": "Corporate lawyer for early-stage startups",
        "category": "Legal",
        "categories": [],
        "languages": [
          "English",
          "Igbo"
        ],
        "location": "Lagos, Nigeria",
        "timezone": "Africa/Lagos",
        "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
        "badge": {
          "vetted": true,
          "checks": [
            "Identity verified",
            "Professional licence verified",
            "2 client references checked"
          ]
        },
        "rating": {
          "avg": null,
          "count": 0,
          "is_new": true
        },
        "from_price_minor": 1000000,
        "currency": "NGN",
        "listing_count": 1,
        "listing_types": [
          "consultation"
        ],
        "next_available_at": "2026-11-06T08:00:00+00:00"
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 12,
      "total": 1
    }
  },
  "meta": null
}
```

`directory_providers_filtered_200` (`category=Legal&language=igbo&sort=price_asc`):
```json
{
  "data": {
    "providers": [
      {
        "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
        "display_name": "Ngozi Eze",
        "headline": "Corporate lawyer for early-stage startups",
        "category": "Legal",
        "categories": [],
        "languages": [
          "English",
          "Igbo"
        ],
        "location": "Lagos, Nigeria",
        "timezone": "Africa/Lagos",
        "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
        "badge": {
          "vetted": true,
          "checks": [
            "Identity verified",
            "Professional licence verified",
            "2 client references checked"
          ]
        },
        "rating": {
          "avg": null,
          "count": 0,
          "is_new": true
        },
        "from_price_minor": 1000000,
        "currency": "NGN",
        "listing_count": 1,
        "listing_types": [
          "consultation"
        ],
        "next_available_at": "2026-11-06T08:00:00+00:00"
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 12,
      "total": 1
    }
  },
  "meta": null
}
```

`directory_bad_category_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Unknown category.",
    "field_errors": [
      {
        "field": "category",
        "message": "Use one of: Legal, Accounting, Design, Development, Marketing."
      }
    ]
  }
}
```

`directory_bad_sort_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Unknown sort.",
    "field_errors": [
      {
        "field": "sort",
        "message": "Use one of: relevance, rating, price_asc, price_desc."
      }
    ]
  }
}
```

### `GET /marketplace/providers/{id}`

The card plus `bio`, `portfolio` and the expert's live `listings`. Portfolio items are links (`url`) and/or uploaded files (`file_url`, a path to fetch with auth). 404 unless the expert is approved **and** has a live listing.

`directory_profile_200`:
```json
{
  "data": {
    "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
    "display_name": "Ngozi Eze",
    "headline": "Corporate lawyer for early-stage startups",
    "category": "Legal",
    "categories": [],
    "languages": [
      "English",
      "Igbo"
    ],
    "location": "Lagos, Nigeria",
    "timezone": "Africa/Lagos",
    "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
    "badge": {
      "vetted": true,
      "checks": [
        "Identity verified",
        "Professional licence verified",
        "2 client references checked"
      ]
    },
    "rating": {
      "avg": null,
      "count": 0,
      "is_new": true
    },
    "from_price_minor": 1000000,
    "currency": "NGN",
    "listing_count": 1,
    "listing_types": [
      "consultation"
    ],
    "next_available_at": "2026-11-06T08:00:00+00:00",
    "bio": "Fifteen years advising founders on contracts, shareholder agreements and fundraising.",
    "portfolio": [],
    "listings": [
      {
        "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
        "type": "consultation",
        "title": "Contract review session",
        "summary": "A one hour review of your contract with written notes.",
        "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
        "category": "Legal",
        "tags": [
          "contracts",
          "term sheets"
        ],
        "price_minor": 1000000,
        "currency": "NGN",
        "duration_min": 60,
        "delivery_days": null,
        "deliverables": [],
        "revisions_included": 2,
        "next_available_at": "2026-11-06T08:00:00+00:00",
        "provider": {
          "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
          "display_name": "Ngozi Eze",
          "headline": "Corporate lawyer for early-stage startups",
          "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
          "badge": {
            "vetted": true,
            "checks": [
              "Identity verified",
              "Professional licence verified",
              "2 client references checked"
            ]
          },
          "rating": {
            "avg": null,
            "count": 0,
            "is_new": true
          }
        }
      }
    ]
  },
  "meta": null
}
```

`directory_profile_without_live_listing_404` (all their listings paused):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`directory_suspended_profile_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `GET /marketplace/listings?type&category&min_price&max_price&max_delivery_days&q&sort&page`

Live listings. `type` is `consultation` or `package`; `max_delivery_days` applies to packages only; `sort` also accepts `newest`. Each item has the provider summary nested under `provider` (no profile fields beyond what a card needs). `GET /marketplace/listings/{id}` returns one item.

`directory_listings_200`:
```json
{
  "data": {
    "listings": [
      {
        "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
        "type": "consultation",
        "title": "Contract review session",
        "summary": "A one hour review of your contract with written notes.",
        "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
        "category": "Legal",
        "tags": [
          "contracts",
          "term sheets"
        ],
        "price_minor": 1000000,
        "currency": "NGN",
        "duration_min": 60,
        "delivery_days": null,
        "deliverables": [],
        "revisions_included": 2,
        "next_available_at": "2026-11-06T08:00:00+00:00",
        "provider": {
          "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
          "display_name": "Ngozi Eze",
          "headline": "Corporate lawyer for early-stage startups",
          "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
          "badge": {
            "vetted": true,
            "checks": [
              "Identity verified",
              "Professional licence verified",
              "2 client references checked"
            ]
          },
          "rating": {
            "avg": null,
            "count": 0,
            "is_new": true
          }
        }
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 12,
      "total": 1
    }
  },
  "meta": null
}
```

`directory_listing_detail_200`:
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "type": "consultation",
    "title": "Contract review session",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [
      "contracts",
      "term sheets"
    ],
    "price_minor": 1000000,
    "currency": "NGN",
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "next_available_at": "2026-11-06T08:00:00+00:00",
    "provider": {
      "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
      "display_name": "Ngozi Eze",
      "headline": "Corporate lawyer for early-stage startups",
      "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
      "badge": {
        "vetted": true,
        "checks": [
          "Identity verified",
          "Professional licence verified",
          "2 client references checked"
        ]
      },
      "rating": {
        "avg": null,
        "count": 0,
        "is_new": true
      }
    }
  },
  "meta": null
}
```

`directory_listing_during_pending_edit` (the expert edited the title and price; the title shown is still the approved one, the price is the new one because price edits are immediate):
```json
{
  "data": {
    "id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "type": "consultation",
    "title": "Contract review session",
    "summary": "A one hour review of your contract with written notes.",
    "description": "A focused one hour session reviewing your contract or term sheet, with written notes after.",
    "category": "Legal",
    "tags": [
      "contracts",
      "term sheets"
    ],
    "price_minor": 1200000,
    "currency": "NGN",
    "duration_min": 60,
    "delivery_days": null,
    "deliverables": [],
    "revisions_included": 2,
    "next_available_at": "2026-11-06T08:00:00+00:00",
    "provider": {
      "id": "6f1d312c-6f16-4a0a-b59c-75edaef7ff4d",
      "display_name": "Ngozi Eze",
      "headline": "Corporate lawyer for early-stage startups",
      "avatar_url": "/api/v1/marketplace/providers/6f1d312c-6f16-4a0a-b59c-75edaef7ff4d/avatar",
      "badge": {
        "vetted": true,
        "checks": [
          "Identity verified",
          "Professional licence verified",
          "2 client references checked"
        ]
      },
      "rating": {
        "avg": null,
        "count": 0,
        "is_new": true
      }
    }
  },
  "meta": null
}
```

`directory_rejected_listing_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `GET /marketplace/listings/{id}/slots?from&to`

Open slots for a **consultation**. `from` / `to` are dates (`YYYY-MM-DD`, inclusive) **in the expert's timezone**, default today and the next six days, at most 14 days at a time and no further than 60 days ahead (422 `RANGE_TOO_LONG`, `RANGE_OUT_OF_BOUNDS`). A package is 422 `NOT_A_CONSULTATION`. Each slot has `starts_at` / `ends_at` in UTC and `local_start` in the expert's timezone (with its offset). **Show times in the founder's own timezone from `starts_at`** and say the expert's timezone from `timezone`. A slot is a start time; its length is `duration_min`. Slots already exclude blocked days, notice and buffer; booking is slice C and will also hold a slot for 15 minutes during checkout.

Daylight-saving behaviour (worked examples in `tests/services/marketplace/test_slots.py`): windows are wall-clock in the expert's timezone, converted per date, so the same "09:00" is a different UTC hour either side of a clock change, and a window spanning a change is its real length. Nothing for the FE to compute: render `starts_at`.

`slots_200` (Monday 09:00-13:00 Lagos, Tuesday blocked):
```json
{
  "data": {
    "listing_id": "df1134a9-5fcd-40e6-b8ae-f07eae3d8f14",
    "timezone": "Africa/Lagos",
    "duration_min": 60,
    "from": "2026-11-09",
    "to": "2026-11-10",
    "slots": [
      {
        "starts_at": "2026-11-09T08:00:00+00:00",
        "ends_at": "2026-11-09T09:00:00+00:00",
        "local_start": "2026-11-09T09:00:00+01:00"
      },
      {
        "starts_at": "2026-11-09T09:00:00+00:00",
        "ends_at": "2026-11-09T10:00:00+00:00",
        "local_start": "2026-11-09T10:00:00+01:00"
      },
      {
        "starts_at": "2026-11-09T10:00:00+00:00",
        "ends_at": "2026-11-09T11:00:00+00:00",
        "local_start": "2026-11-09T11:00:00+01:00"
      },
      {
        "starts_at": "2026-11-09T11:00:00+00:00",
        "ends_at": "2026-11-09T12:00:00+00:00",
        "local_start": "2026-11-09T12:00:00+01:00"
      }
    ]
  },
  "meta": null
}
```

`slots_range_too_long_422`:
```json
{
  "error": {
    "code": "RANGE_TOO_LONG",
    "message": "Ask for at most 14 days at a time.",
    "field_errors": []
  }
}
```

Not exercised live (unit-tested): `RANGE_OUT_OF_BOUNDS`, `from` after `to`, a malformed date, and `NOT_A_CONSULTATION` (their bodies use the standard envelope with those codes).

### Avatars and portfolio files

`GET /marketplace/providers/{id}/avatar` and `GET /marketplace/providers/{id}/portfolio/{item_id}/file` return the file bytes (attachment, `nosniff`, `no-store`) for experts who are visible (approved **and** with a live listing, like the profile), never an unscanned file (409 `FILE_NOT_CLEAN`), and only the avatar / portfolio uploads (a credential scan is unreachable here). Fetch with `Authorization` and `X-Workspace-Id` and turn the response into an object URL; they are not public URLs.

`avatar_headers` (the response is the image bytes, so only headers are captured):
```json
{
  "status": 200,
  "content_type": "image/png",
  "x_content_type_options": "nosniff",
  "cache_control": "private, no-store"
}
```

## UX instructions

- **Rating:** render `is_new` as "New". A 0 or a fake average is wrong.
- **Vetted tooltip:** list `badge.checks` only; if `badge.vetted` is false show no badge.
- **Empty directory:** an empty list is normal early on. Show an honest empty state, not mock cards.
- **Prices:** divide `price_minor` by 100 for display (₦) and never do arithmetic on floats; the commission is not shown to founders.
- **Slots:** group by the founder's local date; show the expert's timezone once; refetch when the founder changes the week; an empty week is a valid answer.
- **Expert portal:** show `review_message` prominently on `rejected` and on a `draft` that came back; show `pending_revision` as "Waiting for review" next to the live values; tell the expert that price changes apply at once but text changes wait for review.
- **Staff queue:** show `diff` side by side for edits; the unlist and relist dialogs must say "Active bookings are honored; no new bookings" and ask for the authenticator code.
- **Hidden means 404:** a listing or expert can disappear between list and click (paused, unlisted, suspended). Treat a 404 on a card click as "no longer available" and refresh the list.

## Verification table

| Behaviour | Verified live (`e2e/test_marketplace_listings.py`) | Unit test only | Not exercised |
|---|---|---|---|
| Expert listing guards (401, `NOT_A_PROVIDER`, suspended 403) | yes | yes (also in review, invited) | |
| Create / validate / patch draft / submit incomplete / locked | yes | yes (17 invalid-body cases, limit, rounding) | |
| Category rule for regulated categories | yes | yes | |
| Submit, approve, reject (template), request changes, emails | yes (approve, reject, email) | yes (request changes, resubmit) | |
| Live edit split, queue with diff, approve edit | yes | yes (reject edit, send back, restore-same-value) | |
| Pause / resume / delete rules | pause yes | yes | |
| Unlist / relist with code; wrong id keeps code | yes | yes | |
| Staff cannot act on own listings | | yes | live |
| Availability put / get / validation | yes | yes (13 invalid cases, atomic replace) | |
| Slot maths: timezones, DST, exceptions, notice, buffer | endpoint yes (Lagos, blocked day) | yes (21 worked examples) | live DST (no DST zone in the e2e expert) |
| Directory visibility (approved + active only) | yes (rejected listing, paused, suspended) | yes (every status) | |
| No staff fields in founder payloads | | yes (key-set and secret-string checks) | |
| Filters, sort, paging, `available` | filters and sort yes | yes (all filters, 14-expert paging) | |
| Rating "New" under 3 reviews | | yes | live (no reviews exist) |
| Roles: founder, team member, mentor, anonymous | yes (TM 200, mentor 403, anonymous 401) | yes (accountant, legal advisor, investor) | |
| Avatar / portfolio files | avatar yes | yes (scope, unclean, suspended) | |
| Reports on listings / experts; remove unlists | yes (file, remove, unlist, relist, review rejected) | yes (own item, hidden item, `USE_VETTING`, self-act incl. dismiss and escalate, relist only what the report unlisted, audit rows) | |
| Flagged at-once edit queued, approve / send back / unlist | yes (queue item, counts) | yes (price and tags, baseline after approve, send back) | |
| Type rules (`FIELD_NOT_FOR_TYPE`, `FIELD_REQUIRED`) | yes | yes | |
| Real email delivery | | | yes (file mailbox only) |
| Large catalogue performance | | | yes (sort and page are in Python; fine for tens of experts) |
