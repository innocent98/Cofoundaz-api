# FE integration guide: Marketplace booking, checkout and orders (slice C)

Every body below is pasted from a live run against a real server: `e2e/test_marketplace_booking.py`, captures in `e2e/_captures/marketplace_booking/<name>.json` (the block title is the capture name). Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`. Companion guides: `docs/fe-integration-guide-marketplace-providers.md` (expert accounts), `docs/fe-integration-guide-marketplace-listings.md` (directory, listings, slots).

**Paystack is the in-process stub in that run** (`PAYMENT_PROVIDER_STUB`), so `checkout_url` points at a stub address and the webhook is signed by the test itself. The shapes are Paystack-independent; what a real checkout page does is not covered. **Production card charging is off** until counsel and Paystack confirm in writing (D-MP-3, D-MP-9). Two switches guard it: `MARKETPLACE_PAYMENTS_ENABLED` (off: every new booking is `403 PAYMENTS_DISABLED`) and, in production or with a live Paystack key, a second `MARKETPLACE_LIVE_CHARGING_CONFIRMED` (off: `403 LIVE_CHARGING_NOT_CONFIRMED`). `GET /marketplace/checkout` reports `payments_enabled` as true only when both gates pass, so build the drawer to read it first. Unit-tested, not captured (the e2e server runs on test keys).

## What this is, in FE terms

1. **Founder: book and pay.** From a listing: pick a slot (consultation) or confirm the scope (package) → write a brief → accept the Marketplace terms the first time → pay on Paystack's hosted page (or one click on the workspace's saved card) → the booking waits for the expert.
2. **Expert: accept or decline** within 48 hours (`/provider/bookings`). No answer in 48 hours, or the consultation slot starts first: the booking expires and a full refund is requested to the card.
3. **Money rules (all kobo, NGN):** the founder pays exactly the listed price. Cofoundaz keeps 15% from the **expert's** side (or their override); the founder never sees an extra fee. Funds are held until delivery and release, which are slices D and E; this slice only takes payment and refunds.
4. **Not in this slice:** delivery, messages, "request changes", disputes, payouts, reviews. A booking stops at `confirmed` / `in_progress` here. **Since slice D** it continues to `delivered` and `completed`, and every booking view carries extra keys (`delivered_at`, `auto_release_at`, `revisions_used`, `revisions_left`, `calendar_event_id`, `calendar_ics_url`, `thread_id`, `brief_files`; the detail view adds `deliveries` and `documents`; the expert view the same minus `revisions_left` and the calendar keys). The bodies in this guide were captured before slice D and do not show them: see `docs/fe-integration-guide-marketplace-fulfilment.md`, which also adds optional `file_ids` to `POST /marketplace/bookings`.

## Booking state machine

| status | Meaning | Founder can | Expert can |
|---|---|---|---|
| `pending_payment` | Checkout hold, 15 minutes (`slot_hold_expires_at`) | pay, verify, cancel | (not visible) |
| `awaiting_provider` | Paid; waiting for the expert (`accept_by`, 48 h) | cancel (full refund) | accept, decline |
| `confirmed` | Consultation accepted | read (meeting link shown) | read |
| `in_progress` | Package accepted (`due_at` = acceptance + delivery days) | read | read |
| `declined` | Expert declined; full refund | read | read |
| `expired` | Hold lapsed unpaid (`payment_not_completed`), or no answer in 48 h (`provider_no_response`), or slot started first (`slot_passed`) | read | read |
| `cancelled` | Founder cancelled before acceptance | read | read |

`closed_reason` says why a closed booking closed. The founder's tabs map to `tab=upcoming` (awaiting_provider, confirmed), `in_progress`, `delivered` (later slices) and `closed` (declined, expired, cancelled, completed, refunded). A checkout hold (`pending_payment`) is **not listed** by default: read it by id, or ask for it with `status=pending_payment`.

Order status: `pending` → `paid` → `refunded` / `partially_refunded`; `failed` for an unpaid hold that lapsed or was cancelled. `refund_pending_minor` is money owed back that Paystack hasn't taken yet (a retry runs every few minutes; show "refund in progress"). Refunds can take a few working days to reach the card. A refund the server can't confirm is reconciled against Paystack's own list (by total, for this transaction only) and is not re-sent for at least 2 hours, so the founder is never refunded twice; in a Paystack outage a refund can therefore be delayed by a few hours.

## Who can do what (D-MP-L1)

| Caller | Result |
|---|---|
| No token | 401 |
| Mentor, accountant, legal advisor, investor | 403 on every `/marketplace/*` route |
| Team member | reads bookings, orders, receipts, checkout status; **cannot** book, cancel or verify (403) |
| Founder | everything |

`booking_anonymous_401`:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

`booking_team_member_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

`bookings_mentor_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

## `GET /marketplace/checkout`

Call before opening the booking drawer. `can_book` is false for team members (disable the button). `terms_accepted` false means show the terms checkbox. `saved_card` is the workspace's saved card from Billing, if any.

`checkout_status_before_first_booking`:
```json
{
  "data": {
    "payments_enabled": true,
    "terms_version": "draft-2026-11",
    "terms_accepted": false,
    "can_book": true,
    "saved_card": null
  },
  "meta": null
}
```

`checkout_status_team_member`:
```json
{
  "data": {
    "payments_enabled": true,
    "terms_version": "draft-2026-11",
    "terms_accepted": false,
    "can_book": false,
    "saved_card": null
  },
  "meta": null
}
```

`checkout_status_after_booking`:
```json
{
  "data": {
    "payments_enabled": true,
    "terms_version": "draft-2026-11",
    "terms_accepted": true,
    "can_book": true,
    "saved_card": null
  },
  "meta": null
}
```

## `POST /marketplace/bookings`

Founder only. **Header `Idempotency-Key` is required** (8-64 characters of letters, digits and `. _ : -`); generate one per attempt and **reuse it on a retry**: the same key and body returns the same booking (200, `replayed: true`, same `checkout_url`), a different body is 409 `IDEMPOTENCY_CONFLICT`. Body: `listing_id`, `starts_at` (consultations only; ISO with a UTC offset, taken from `GET /marketplace/listings/{id}/slots`), `brief` (10-4000 chars), `accept_terms` (true the first time), `pay_with` (`card` default, or `saved_card`). Unknown fields are a 422.

Response: **201** with the booking. The `order` object carries `checkout_url`: send the browser there. `price_minor` and the title are snapshots: later listing edits never change this booking. The founder payload never contains the commission or the expert's net.

`booking_create_consultation_201`:
```json
{
  "data": {
    "replayed": false,
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "pending_payment",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": "2026-11-06T21:44:11.065735+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
      "status": "pending",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_73a2d9dce9a635530038",
      "checkout_url": "https://checkout.paystack.com/stub-mp_73a2d9dce9a635530038",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

`booking_create_package_201`:
```json
{
  "data": {
    "replayed": false,
    "id": "f2668462-9152-4979-bbf9-152351e5d253",
    "status": "pending_payment",
    "type": "package",
    "listing_id": "0ec910d1-6c38-4318-b8a5-7c1496fbaf6d",
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
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": "2026-11-06T21:44:12.705568+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "213755ad-ada5-4bd7-8c58-da8c0f23897a",
      "status": "pending",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_6e4eb7583f11ae1e639b",
      "checkout_url": "https://checkout.paystack.com/stub-mp_6e4eb7583f11ae1e639b",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:12.671385+00:00"
  },
  "meta": null
}
```

`booking_create_replay_200`:
```json
{
  "data": {
    "replayed": true,
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "pending_payment",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": "2026-11-06T21:44:11.065735+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
      "status": "pending",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_73a2d9dce9a635530038",
      "checkout_url": "https://checkout.paystack.com/stub-mp_73a2d9dce9a635530038",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

Errors (all before any money moves; nothing is created):

`booking_missing_idempotency_key_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Send an Idempotency-Key header of 8 to 64 letters, digits or . _ : -",
    "field_errors": [
      {
        "field": "Idempotency-Key",
        "message": "Required, 8-64 characters."
      }
    ]
  }
}
```

`booking_terms_required_422`:
```json
{
  "error": {
    "code": "TERMS_REQUIRED",
    "message": "Accept the Marketplace terms to book.",
    "field_errors": [
      {
        "field": "accept_terms",
        "message": "You need to accept the terms."
      }
    ]
  }
}
```

`booking_brief_too_short_422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "brief",
        "message": "String should have at least 10 characters"
      }
    ]
  }
}
```

`booking_slot_required_422`:
```json
{
  "error": {
    "code": "SLOT_REQUIRED",
    "message": "Choose a time for this consultation.",
    "field_errors": []
  }
}
```

`booking_slot_not_applicable_422`:
```json
{
  "error": {
    "code": "SLOT_NOT_APPLICABLE",
    "message": "Packages don't have a time slot.",
    "field_errors": []
  }
}
```

`booking_slot_unavailable_409` (not an offered slot, or taken meanwhile: refetch the slots):
```json
{
  "error": {
    "code": "SLOT_UNAVAILABLE",
    "message": "That time isn't available any more. Pick another.",
    "field_errors": []
  }
}
```

`booking_rival_slot_held_409` (another founder holds the slot for 15 minutes while they pay):
```json
{
  "error": {
    "code": "SLOT_UNAVAILABLE",
    "message": "That time isn't available any more. Pick another.",
    "field_errors": []
  }
}
```

`booking_idempotency_conflict_409`:
```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "That Idempotency-Key was already used for a different booking.",
    "field_errors": []
  }
}
```

Other codes, **unit-tested but not captured live**: `403 PAYMENTS_DISABLED` (the production switch), `422 CANNOT_BOOK_OWN` (the expert is in the booker's workspace or shares an email with a member), `422 NO_SAVED_CARD`, `404` for a listing that isn't live or an expert who isn't approved, `422 VALIDATION_ERROR` for a missing or timezone-less `starts_at`.

### Saved card (`pay_with: "saved_card"`)

Charges the workspace's saved Billing card at once. Success: the booking comes back already `awaiting_provider`, `order.status: "paid"`. A declined card is **402 `CARD_DECLINED`** and the booking is cancelled (nothing charged). If Paystack doesn't answer, or says the charge is still open (for example it wants an OTP), **502 `PAYMENT_UNCONFIRMED`** with the booking id in `field_errors[0].message` (the same code and shape also come back whenever the charge succeeded but we then couldn't get Paystack's confirmation, for example a 429 or an outage; it is never `PAYSTACK_UNAVAILABLE "Try again"` or "Nothing was charged", because the card may have been charged: do NOT let the founder retry with a new key until the booking is checked): the hold is kept, the card may or may not have been charged. Poll `GET /marketplace/bookings/{id}` (and call `verify-payment`) before letting the founder retry; the confirmation, if it happened, arrives by webhook. **Not exercised live** (no saved card exists in the e2e database); unit-tested, shapes follow the standard envelope with those codes.

## Confirming payment

After Paystack's page, the founder returns to the callback URL `{APP_BASE_URL}/marketplace/bookings/{id}`. Two things confirm the payment, whichever is first, and applying is idempotent:

1. Paystack's **webhook** (server to server).
2. **`POST /marketplace/bookings/{id}/verify-payment`** (founder): asks Paystack what happened. Call it when the founder lands on the callback page, then poll `GET /marketplace/bookings/{id}` every 3 s for up to 30 s while `status` is `pending_payment` and `order.status` is `pending`; after that show "Payment not confirmed yet. If you were charged it will appear shortly" and keep the hold countdown (`slot_hold_expires_at`) visible. If Paystack can't be asked at all, this endpoint also answers **502 `PAYMENT_UNCONFIRMED`** (hold and order stay pending; keep polling `GET /marketplace/bookings/{id}`). `outcome`: `paid`, `already_applied`, `payment_not_successful` (still unpaid), `payment_mismatch` (Paystack's amount didn't match; not applied: the booking becomes `expired` with `closed_reason: "payment_review"`, the slot is released, the founder is emailed that the team is checking and not to pay again, and staff see it in the attention queue), `not_found_at_paystack`.

`booking_get_pending_payment`:
```json
{
  "data": {
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "pending_payment",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": "2026-11-06T21:44:11.065735+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
      "status": "pending",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_73a2d9dce9a635530038",
      "checkout_url": "https://checkout.paystack.com/stub-mp_73a2d9dce9a635530038",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

`booking_verify_payment_already_applied`:
```json
{
  "data": {
    "outcome": "already_applied",
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "awaiting_provider",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-08T21:29:11.487242+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_73a2d9dce9a635530038",
      "checkout_url": null,
      "paid_at": "2026-11-06T21:29:11+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

`booking_get_awaiting_provider` (after the webhook; `accept_by` is the expert's 48-hour deadline):
```json
{
  "data": {
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "awaiting_provider",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-08T21:29:11.487242+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_73a2d9dce9a635530038",
      "checkout_url": null,
      "paid_at": "2026-11-06T21:29:11+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

### The webhook (for the record; the FE never calls it)

`POST /api/v1/webhooks/billing/paystack`, public, authenticated by Paystack's `x-paystack-signature` (HMAC-SHA512 of the raw body under the secret key, constant-time, fail closed). Marketplace events are those whose reference starts with `mp_`; everything else is Billing's and untouched. A `charge.success` body is **never trusted for money**: the server asks Paystack to verify the reference and checks status, amount and currency against the order. Replays and duplicates answer 200 and apply once. An unknown reference answers 200 and does nothing. If Paystack can't be reached, or refuses to verify (a 429 or any 4xx other than "no such transaction"), the answer is 502, so Paystack retries. A signed `charge.success` that Paystack doesn't yet show as paid answers **409 `RETRY_LATER`** (not recorded, so the redelivery is processed afresh). A reference Paystack has never heard of (its 404) answers 200 with outcome `unverified` and records nothing. A payment whose amount or currency doesn't match the order is **never booked**, but it is no longer silent: the order is flagged (`attention_reason: payment_mismatch`, with Paystack's amount), an audit row and an error log are written, and staff see it in `GET /admin/marketplace/orders/attention` (moderation scope, read-only; it also lists orders whose refund gave up after 12 attempts). The FE never calls that endpoint here; it is for the staff console. A signed `refund.processed` books a refund only while a send of ours is unanswered and only up to what is owed; otherwise it is only noted. 409 / 502 / mismatch behaviours are unit-tested, not captured live.

`webhook_bad_signature_401`:
```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Signature check failed.",
    "field_errors": []
  }
}
```

`webhook_charge_success_200`:
```json
{
  "data": {
    "received": true,
    "outcome": "paid"
  },
  "meta": null
}
```

`webhook_replay_200`:
```json
{
  "data": {
    "received": true,
    "outcome": "duplicate"
  },
  "meta": null
}
```

`webhook_unknown_reference_200`:
```json
{
  "data": {
    "received": true,
    "outcome": "unverified"
  },
  "meta": null
}
```

`webhook_refund_failed_reversed_200` (Paystack reports a refund we had sent has failed: the money goes back on the books and `refund_failed_at` flags the order for staff; the FE just sees `refunded_minor` drop):
```json
{
  "data": {
    "received": true,
    "outcome": "refund_reversed"
  },
  "meta": null
}
```

`booking_get_after_refund_failed`:
```json
{
  "data": {
    "id": "f2668462-9152-4979-bbf9-152351e5d253",
    "status": "declined",
    "type": "package",
    "listing_id": "0ec910d1-6c38-4318-b8a5-7c1496fbaf6d",
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
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-08T21:29:12.964828+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "provider_declined",
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "213755ad-ada5-4bd7-8c58-da8c0f23897a",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_6e4eb7583f11ae1e639b",
      "checkout_url": null,
      "paid_at": "2026-11-06T21:29:12+00:00",
      "receipt_number": "MPR-2026-1002",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:12.671385+00:00"
  },
  "meta": null
}
```

**A saved-card booking can answer 201 and still not be booked**: if Paystack took a different amount than the price (a `payment_mismatch`), the response is `201` with the booking already `status: "expired"` and `closed_reason: "payment_review"`. The FE must read `status` / `closed_reason` and must NOT treat a 201 as booked; show the "We're checking your payment, you don't need to pay again" state. Unit-tested, not captured live.

**New `closed_reason` values the FE must render** (status is `expired`): `payment_review` ("We're checking your payment. You don't need to pay again.") and `payment_not_completed` (the hold lapsed; when Paystack could not be asked for over an hour after the hold ended the founder is also emailed "we couldn't confirm your payment; if you were charged we'll confirm or refund automatically; don't pay again"). A payment that turns up after `payment_not_completed` still revives or refunds the booking (unless its amount doesn't match: then the same `payment_review` state and email apply, even though the hold had already closed); after `payment_review` staff resolve it. All unit-tested; **not captured live** (the e2e stub cannot produce a mismatch or an unanswerable Paystack).

**Money that arrives for a booking that can no longer go ahead** (the hold lapsed and the slot was taken, or the booking was cancelled): it is refunded in full automatically (`order.status` `paid` with `refund_pending_minor`, then `refunded`). Unit-tested, not captured.

## Reading bookings

`GET /marketplace/bookings?tab&status&limit&offset` (founder and team member, scoped to the workspace; another workspace's id is a 404). `pagination` is `{total, limit, offset, has_more}`.

`bookings_list_excludes_checkout_holds`:
```json
{
  "data": {
    "bookings": [],
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

`bookings_list_upcoming_tab`:
```json
{
  "data": {
    "bookings": [
      {
        "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
        "status": "confirmed",
        "type": "consultation",
        "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
        "title": "Contract review session",
        "category": "Legal",
        "deliverables": [],
        "duration_min": 60,
        "delivery_days": null,
        "revisions_included": 2,
        "price_minor": 1000000,
        "currency": "NGN",
        "brief": "We need a review of our seed investment agreement before we sign next week.",
        "starts_at": "2026-11-08T07:00:00+00:00",
        "ends_at": "2026-11-08T08:00:00+00:00",
        "timezone": "Africa/Lagos",
        "slot_hold_expires_at": null,
        "accept_by": "2026-11-08T21:29:11.487242+00:00",
        "due_at": null,
        "meeting_url": "https://meet.example.com/ngozi-eze",
        "closed_reason": null,
        "provider": {
          "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
          "display_name": "Ngozi Eze",
          "avatar_url": null
        },
        "order": {
          "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
          "status": "paid",
          "amount_minor": 1000000,
          "currency": "NGN",
          "reference": "mp_73a2d9dce9a635530038",
          "checkout_url": null,
          "paid_at": "2026-11-06T21:29:11+00:00",
          "receipt_number": "MPR-2026-1001",
          "refunded_minor": 0,
          "refund_pending_minor": 0
        },
        "created_at": "2026-11-06T21:29:11.051649+00:00"
      }
    ],
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

`bookings_list_closed_tab`:
```json
{
  "data": {
    "bookings": [
      {
        "id": "ea1836ae-5d20-4d22-81ee-bcf23de10f26",
        "status": "cancelled",
        "type": "consultation",
        "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
        "title": "Contract review session",
        "category": "Legal",
        "deliverables": [],
        "duration_min": 60,
        "delivery_days": null,
        "revisions_included": 2,
        "price_minor": 1000000,
        "currency": "NGN",
        "brief": "We need a review of our seed investment agreement before we sign next week.",
        "starts_at": "2026-11-08T08:00:00+00:00",
        "ends_at": "2026-11-08T09:00:00+00:00",
        "timezone": "Africa/Lagos",
        "slot_hold_expires_at": null,
        "accept_by": "2026-11-08T21:29:14.196509+00:00",
        "due_at": null,
        "meeting_url": null,
        "closed_reason": "founder_cancelled",
        "provider": {
          "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
          "display_name": "Ngozi Eze",
          "avatar_url": null
        },
        "order": {
          "id": "fa889904-5de3-468b-9cb2-e1e661d6a48c",
          "status": "refunded",
          "amount_minor": 1000000,
          "currency": "NGN",
          "reference": "mp_50e9c9d0099b1c825bf7",
          "checkout_url": null,
          "paid_at": "2026-11-06T21:29:14+00:00",
          "receipt_number": "MPR-2026-1003",
          "refunded_minor": 1000000,
          "refund_pending_minor": 0
        },
        "created_at": "2026-11-06T21:29:13.700340+00:00"
      },
      {
        "id": "f2668462-9152-4979-bbf9-152351e5d253",
        "status": "declined",
        "type": "package",
        "listing_id": "0ec910d1-6c38-4318-b8a5-7c1496fbaf6d",
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
        "brief": "We need a review of our seed investment agreement before we sign next week.",
        "starts_at": null,
        "ends_at": null,
        "timezone": "Africa/Lagos",
        "slot_hold_expires_at": null,
        "accept_by": "2026-11-08T21:29:12.964828+00:00",
        "due_at": null,
        "meeting_url": null,
        "closed_reason": "provider_declined",
        "provider": {
          "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
          "display_name": "Ngozi Eze",
          "avatar_url": null
        },
        "order": {
          "id": "213755ad-ada5-4bd7-8c58-da8c0f23897a",
          "status": "paid",
          "amount_minor": 25000000,
          "currency": "NGN",
          "reference": "mp_6e4eb7583f11ae1e639b",
          "checkout_url": null,
          "paid_at": "2026-11-06T21:29:12+00:00",
          "receipt_number": "MPR-2026-1002",
          "refunded_minor": 0,
          "refund_pending_minor": 0
        },
        "created_at": "2026-11-06T21:29:12.671385+00:00"
      }
    ],
    "pagination": {
      "total": 2,
      "limit": 25,
      "offset": 0,
      "has_more": false
    }
  },
  "meta": null
}
```

`booking_get_other_workspace_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`booking_get_confirmed_with_meeting_link` (the meeting link appears only once the expert has accepted):
```json
{
  "data": {
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "confirmed",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-08T21:29:11.487242+00:00",
    "due_at": null,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "closed_reason": null,
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_73a2d9dce9a635530038",
      "checkout_url": null,
      "paid_at": "2026-11-06T21:29:11+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

## Cancel

`POST /marketplace/bookings/{id}/cancel` (founder). Unpaid hold: cancelled, hold released, nothing charged. Paid and not yet accepted: cancelled and **refunded in full**. After acceptance: 409 `CANCEL_NOT_ALLOWED` (a "report a problem" flow arrives with disputes); cancelling within 24 hours of a consultation (D-MP-4) is a later slice.

`booking_cancel_refunds_200`:
```json
{
  "data": {
    "id": "ea1836ae-5d20-4d22-81ee-bcf23de10f26",
    "status": "cancelled",
    "type": "consultation",
    "listing_id": "d92df2ba-22c1-4960-8473-4af4b33fd2f2",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "price_minor": 1000000,
    "currency": "NGN",
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": "2026-11-08T08:00:00+00:00",
    "ends_at": "2026-11-08T09:00:00+00:00",
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-08T21:29:14.196509+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "founder_cancelled",
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "fa889904-5de3-468b-9cb2-e1e661d6a48c",
      "status": "refunded",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_50e9c9d0099b1c825bf7",
      "checkout_url": null,
      "paid_at": "2026-11-06T21:29:14+00:00",
      "receipt_number": "MPR-2026-1003",
      "refunded_minor": 1000000,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:13.700340+00:00"
  },
  "meta": null
}
```

`booking_cancel_team_member_403`:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

`booking_cancel_after_accept_409`:
```json
{
  "error": {
    "code": "CANCEL_NOT_ALLOWED",
    "message": "This booking can't be cancelled now. Use 'Report a problem' once it's under way.",
    "field_errors": []
  }
}
```

## Orders and receipts

`GET /marketplace/orders?status&limit&offset` lists paid (and refunded) orders, never an unpaid checkout. `GET /marketplace/orders/{id}/receipt` is **JSON** (a PDF is a later slice): the service line is what the expert keeps and the platform-fee line is Cofoundaz's cut, both inside the price the founder paid; `is_tax_invoice` is **false** and the `note` must be shown (VAT and withholding tax treatment is still under review). No payment, no receipt (404). Other workspaces get 404.

`orders_list`:
```json
{
  "data": {
    "orders": [
      {
        "id": "7fc543cd-a258-42a8-9411-7ba2e80c207f",
        "status": "paid",
        "amount_minor": 1000000,
        "currency": "NGN",
        "reference": "mp_73a2d9dce9a635530038",
        "checkout_url": null,
        "paid_at": "2026-11-06T21:29:11+00:00",
        "receipt_number": "MPR-2026-1001",
        "refunded_minor": 0,
        "refund_pending_minor": 0,
        "booking_id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
        "title": "Contract review session",
        "provider_id": "e2501910-33e3-4964-9454-9d1032ead0c1"
      }
    ],
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

`order_receipt`:
```json
{
  "data": {
    "receipt_number": "MPR-2026-1001",
    "paid_at": "2026-11-06T21:29:11+00:00",
    "currency": "NGN",
    "reference": "mp_73a2d9dce9a635530038",
    "total_minor": 1000000,
    "refunded_minor": 0,
    "lines": [
      {
        "label": "Contract review session (service by Ngozi Eze)",
        "amount_minor": 850000
      },
      {
        "label": "Cofoundaz platform fee",
        "amount_minor": 150000
      }
    ],
    "is_tax_invoice": false,
    "note": "This receipt is not a tax invoice. VAT and withholding tax treatment is being reviewed and a tax invoice will follow."
  },
  "meta": null
}
```

`order_receipt_other_workspace_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## Expert: the booking inbox (`/provider/bookings*`)

Approved experts only (a suspended or unapproved expert gets 403 `PROVIDER_NOT_APPROVED`; a non-expert 403 `NOT_A_PROVIDER`). The expert sees the brief, the client's **name only** (never the workspace), and their earnings (`earnings.net_minor` is what they keep; `earnings.paid` says whether the money is in). The meeting link shows only after acceptance. Paid bookings only: an unpaid checkout is invisible to the expert.

`provider_bookings_list`:
```json
{
  "data": {
    "bookings": [
      {
        "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
        "status": "awaiting_provider",
        "type": "consultation",
        "title": "Contract review session",
        "category": "Legal",
        "deliverables": [],
        "duration_min": 60,
        "delivery_days": null,
        "revisions_included": 2,
        "brief": "We need a review of our seed investment agreement before we sign next week.",
        "client_name": "Ada Obi",
        "starts_at": "2026-11-08T07:00:00+00:00",
        "ends_at": "2026-11-08T08:00:00+00:00",
        "accept_by": "2026-11-08T21:29:11.487242+00:00",
        "due_at": null,
        "meeting_url": null,
        "closed_reason": null,
        "earnings": {
          "price_minor": 1000000,
          "commission_bps": 1500,
          "commission_minor": 150000,
          "net_minor": 850000,
          "currency": "NGN",
          "paid": true
        },
        "created_at": "2026-11-06T21:29:11.051649+00:00"
      }
    ],
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

`provider_bookings_not_a_provider_403`:
```json
{
  "error": {
    "code": "NOT_A_PROVIDER",
    "message": "This account isn't a Marketplace expert.",
    "field_errors": []
  }
}
```

`provider_accept_consultation_200` (`POST /provider/bookings/{id}/accept`; a package becomes `in_progress` with `due_at`):
```json
{
  "data": {
    "id": "da1e156d-cfb8-482e-8d23-4fd870307cbb",
    "status": "confirmed",
    "type": "consultation",
    "title": "Contract review session",
    "category": "Legal",
    "deliverables": [],
    "duration_min": 60,
    "delivery_days": null,
    "revisions_included": 2,
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "client_name": "Ada Obi",
    "starts_at": "2026-11-08T07:00:00+00:00",
    "ends_at": "2026-11-08T08:00:00+00:00",
    "accept_by": "2026-11-08T21:29:11.487242+00:00",
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
    "created_at": "2026-11-06T21:29:11.051649+00:00"
  },
  "meta": null
}
```

`provider_accept_twice_409`:
```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Can't accept a booking that is 'confirmed'.",
    "field_errors": []
  }
}
```

`provider_decline_200` (`POST …/decline`; the founder is refunded in full and emailed):
```json
{
  "data": {
    "id": "f2668462-9152-4979-bbf9-152351e5d253",
    "status": "declined",
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
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "client_name": "Ada Obi",
    "starts_at": null,
    "ends_at": null,
    "accept_by": "2026-11-08T21:29:12.964828+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "provider_declined",
    "earnings": {
      "price_minor": 25000000,
      "commission_bps": 1500,
      "commission_minor": 3750000,
      "net_minor": 21250000,
      "currency": "NGN",
      "paid": false
    },
    "created_at": "2026-11-06T21:29:12.671385+00:00"
  },
  "meta": null
}
```

`booking_get_declined_refunded`:
```json
{
  "data": {
    "id": "f2668462-9152-4979-bbf9-152351e5d253",
    "status": "declined",
    "type": "package",
    "listing_id": "0ec910d1-6c38-4318-b8a5-7c1496fbaf6d",
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
    "brief": "We need a review of our seed investment agreement before we sign next week.",
    "starts_at": null,
    "ends_at": null,
    "timezone": "Africa/Lagos",
    "slot_hold_expires_at": null,
    "accept_by": "2026-11-08T21:29:12.964828+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "provider_declined",
    "provider": {
      "id": "e2501910-33e3-4964-9454-9d1032ead0c1",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "213755ad-ada5-4bd7-8c58-da8c0f23897a",
      "status": "refunded",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_6e4eb7583f11ae1e639b",
      "checkout_url": null,
      "paid_at": "2026-11-06T21:29:12+00:00",
      "receipt_number": "MPR-2026-1002",
      "refunded_minor": 25000000,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T21:29:12.671385+00:00"
  },
  "meta": null
}
```

Accepting after the 48-hour window, or after a consultation's slot has started, is 409 `ACCEPT_WINDOW_CLOSED` (unit-tested, not captured).

## Emails

Provider: a new paid booking request with the 48-hour deadline. Founder: declined / expired / cancelled / slot no longer available. The copy says "We've requested a full refund to the card you paid with. It can take a few working days to reach you." (it never claims the money has already landed). A late payment for a slot someone else took also emails the founder.

`email_provider_new_booking_request`:
```json
{
  "to": "delivered+e2e-62535773b99e@resend.dev",
  "subject": "New booking request: Contract review session",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">You have a new booking request</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, a founder booked <strong>Contract review session</strong> for Sun 08 Nov 2026, 08:00 Africa/Lagos and has paid. Please accept or decline by Sun 08 Nov, 22:29, or the booking expires and they are refunded in full.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/bookings\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open your bookings</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/bookings\" style=\"color:#0F6AFC\">http://localhost:3000/provider/bookings</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">You have 48 hours to respond.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-06T21:29:11.514772+00:00"
}
```

`email_founder_declined_refunded`:
```json
{
  "to": "delivered+e2e-64b9934d59ac@resend.dev",
  "subject": "Your booking could not go ahead",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">Your booking could not go ahead</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Ngozi Eze could not take on <strong>Shareholder agreement drafting</strong>. We've requested a full refund to the card you paid with. It can take a few working days to reach you.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/marketplace\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Browse experts</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/marketplace\" style=\"color:#0F6AFC\">http://localhost:3000/marketplace</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">You'll see the refund on the booking as soon as it is confirmed.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-06T21:29:13.095101+00:00"
}
```

## UX instructions

- **Read `GET /marketplace/checkout` first**; if `payments_enabled` is false show "Booking opens soon", not an error toast.
- **Show the countdown** for `slot_hold_expires_at` on a pending booking and explain the slot is held for 15 minutes.
- **Never cancel a booking in the UI just because verify says `payment_not_successful`** (or a saved-card charge answers `PAYMENT_UNCONFIRMED`: the charge may still complete): the founder may still be paying. Let the hold expire or let them cancel.
- **Tell the founder what a refund means**: "Refunded in full. It can take a few working days to reach your card." Use `refund_pending_minor` to show "refund in progress".
- **The founder pays the listed price**: never display commission, net or fees to founders; the receipt shows the split only as a breakdown inside the price.
- **A 404 on a booking or order** means it isn't in this workspace.
- **Timers are server-side** (the hold, the 48-hour acceptance, refund retries run once a minute; since slice D also session hand-over, delivery auto-release and overdue notices); the FE only renders the resulting status and `closed_reason`.
- **An accepted consultation no longer stays `confirmed` for ever:** once its session ends it becomes `delivered`, then `completed` (see the fulfilment guide).

## Verification table

| Behaviour | Verified live (`e2e/test_marketplace_booking.py`) | Unit test only | Not exercised |
|---|---|---|---|
| Roles: founder books, team member reads, mentor / anonymous refused | yes | yes | |
| Booking validation (key, terms, brief, slot rules, taken slot) | yes | yes (and overlap, buffer, lapsed hold, DB unique index) | |
| Idempotent replay / conflict / cross-workspace replay | yes (replay, conflict) | yes (cross-workspace) | |
| Slot hold blocks a rival; paid booking keeps the slot | yes (hold) | yes | |
| Signed webhook confirms; forged 401; replay once; unknown reference | yes | yes | |
| Webhook never trusts the body for the amount; a payment Paystack hasn't confirmed answers 409 and is retried; verify refusal / outage answers non-2xx; unknown transaction 200 | | yes | |
| Amount mismatch is flagged (attention columns, audit, staff queue `GET /admin/marketplace/orders/attention`, guards), closes the checkout as `payment_review`, frees the slot, emails the founder, and is never re-verified by the timer | | yes | |
| Charge succeeded but verify fails (saved card and `verify-payment`) answers `PAYMENT_UNCONFIRMED`, never "nothing charged" | | yes | |
| Hold Paystack can't answer for is retried for 1 h past expiry, then closed with an audit row and email; a late payment still revives it; one failing hold doesn't abort the tick | | yes | **what status Paystack's real verify returns for an unknown reference (404 vs 400) is NOT confirmed against Paystack TEST mode**: the code treats 404 as "unpaid" and anything else as "can't tell", bounded by the grace, so either answer ends safely |
| Unpaid hold that was in fact paid is applied, not expired; Paystack unreachable leaves it pending | | yes | live (needs a clock) |
| Two gates on real charging (`MARKETPLACE_LIVE_CHARGING_CONFIRMED`) | | yes | live (e2e uses test keys) |
| Double submit racing the replay check is still a replay | | yes | |
| Verify-payment applies once with the webhook | yes (already applied) | yes | |
| Late payment: revived when free, refunded when slot taken or booking closed | | yes | live (needs a clock) |
| Saved card: success, decline, unconfirmed | | yes | live (no saved card in e2e) |
| Accept (consultation, package), decline, window closed | yes (accept, decline) | yes | |
| Decline / cancel refunds in full; ledger balances; no double refund | yes | yes | |
| Refund retries with backoff; flagged after 12 attempts; in-flight marker, lost-answer and crash-before-commit reconcile; other orders' refunds ignored | | yes | live (needs a failing Paystack); **Paystack's refund-list filter semantics are unconfirmed against the real API** (the stub and the unit fake filter by reference themselves); a first refund never depends on the list, and a full refund is safe either way (Paystack rejects a second full refund). Confirm on staging test keys before slice E issues partial refunds |
| `refund.failed` puts the money back on the books once | yes | yes | |
| Signed `refund.processed` settles a refund we never saw answered (only while a send is unanswered, up to what is owed); noted otherwise | | yes | **the real `refund.processed` payload fields (`refund_reference`, `amount`) are taken from Paystack's docs, not captured** |
| 15-minute hold lapse, 48-hour acceptance expiry, slot-passed expiry | | yes (the timer, with a controlled clock) | live (needs a clock) |
| Receipts, orders list, workspace scoping | yes | yes | PDF receipt (slice F) |
| Ledger append-only trigger, order CHECK constraints, migration round trip | | yes (`tests/test_marketplace_booking_migration.py`, real schema) | |
| `MARKETPLACE_PAYMENTS_ENABLED=false` refuses booking | | yes | live (the e2e server runs with it on) |
| Real Paystack (hosted checkout, real webhook signature from Paystack, real refunds) | | | yes (stubbed; needs Paystack test keys on staging) |
