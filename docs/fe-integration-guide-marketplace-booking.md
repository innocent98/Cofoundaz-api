# FE integration guide: Marketplace booking, checkout and orders (slice C)

Every body below is pasted from a live run against a real server: `e2e/test_marketplace_booking.py`, captures in `e2e/_captures/marketplace_booking/<name>.json` (the block title is the capture name). Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`. Companion guides: `docs/fe-integration-guide-marketplace-providers.md` (expert accounts), `docs/fe-integration-guide-marketplace-listings.md` (directory, listings, slots).

**Paystack is the in-process stub in that run** (`PAYMENT_PROVIDER_STUB`), so `checkout_url` points at a stub address and the webhook is signed by the test itself. The shapes are Paystack-independent; what a real checkout page does is not covered. **Production card charging is off** (`MARKETPLACE_PAYMENTS_ENABLED=false`) until counsel and Paystack confirm in writing (D-MP-3, D-MP-9); with it off every new booking is `403 PAYMENTS_DISABLED`, so build the drawer to read `GET /marketplace/checkout` first.

## What this is, in FE terms

1. **Founder: book and pay.** From a listing: pick a slot (consultation) or confirm the scope (package) → write a brief → accept the Marketplace terms the first time → pay on Paystack's hosted page (or one click on the workspace's saved card) → the booking waits for the expert.
2. **Expert: accept or decline** within 48 hours (`/provider/bookings`). No answer in 48 hours, or the consultation slot starts first: the booking expires and the card is refunded in full.
3. **Money rules (all kobo, NGN):** the founder pays exactly the listed price. Cofoundaz keeps 15% from the **expert's** side (or their override); the founder never sees an extra fee. Funds are held until delivery and release, which are slices D and E; this slice only takes payment and refunds.
4. **Not in this slice:** delivery, messages, "request changes", disputes, payouts, reviews. A booking stops at `confirmed` / `in_progress` here.

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
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
    "status": "pending_payment",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "slot_hold_expires_at": "2026-11-06T20:01:26.619330+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "a6885688-deec-477c-be5c-5614c1e104f5",
      "status": "pending",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_b3a7b28cbd8042d8974c",
      "checkout_url": "https://checkout.paystack.com/stub-mp_b3a7b28cbd8042d8974c",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:26.611376+00:00"
  },
  "meta": null
}
```

`booking_create_package_201`:
```json
{
  "data": {
    "replayed": false,
    "id": "c0a6437d-97f4-4522-ae7c-be351fb54184",
    "status": "pending_payment",
    "type": "package",
    "listing_id": "20eb9cf0-170c-4090-b73e-84602064873b",
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
    "slot_hold_expires_at": "2026-11-06T20:01:27.389971+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "06dba8db-5390-4867-b971-0ffe25b220b9",
      "status": "pending",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_193e3063d9bb15cdf3a5",
      "checkout_url": "https://checkout.paystack.com/stub-mp_193e3063d9bb15cdf3a5",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:27.378137+00:00"
  },
  "meta": null
}
```

`booking_create_replay_200`:
```json
{
  "data": {
    "replayed": true,
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
    "status": "pending_payment",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "slot_hold_expires_at": "2026-11-06T20:01:26.619330+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "a6885688-deec-477c-be5c-5614c1e104f5",
      "status": "pending",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_b3a7b28cbd8042d8974c",
      "checkout_url": "https://checkout.paystack.com/stub-mp_b3a7b28cbd8042d8974c",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:26.611376+00:00"
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

Charges the workspace's saved Billing card at once. Success: the booking comes back already `awaiting_provider`, `order.status: "paid"`. A declined card is **402 `CARD_DECLINED`** and the booking is cancelled (nothing charged). If Paystack doesn't answer, **502 `PAYMENT_UNCONFIRMED`** with the booking id in `field_errors[0].message`: the hold is kept, the card may or may not have been charged. Poll `GET /marketplace/bookings/{id}` (and call `verify-payment`) before letting the founder retry; the confirmation, if it happened, arrives by webhook. **Not exercised live** (no saved card exists in the e2e database); unit-tested, shapes follow the standard envelope with those codes.

## Confirming payment

After Paystack's page, the founder returns to the callback URL `{APP_BASE_URL}/marketplace/bookings/{id}`. Two things confirm the payment, whichever is first, and applying is idempotent:

1. Paystack's **webhook** (server to server).
2. **`POST /marketplace/bookings/{id}/verify-payment`** (founder): asks Paystack what happened. Call it when the founder lands on the callback page, then poll `GET /marketplace/bookings/{id}` every 3 s for up to 30 s while `status` is `pending_payment` and `order.status` is `pending`; after that show "Payment not confirmed yet. If you were charged it will appear shortly" and keep the hold countdown (`slot_hold_expires_at`) visible. `outcome`: `paid`, `already_applied`, `payment_not_successful` (still unpaid), `payment_mismatch` (Paystack's amount didn't match; not applied), `not_found_at_paystack`.

`booking_get_pending_payment`:
```json
{
  "data": {
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
    "status": "pending_payment",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "slot_hold_expires_at": "2026-11-06T20:01:26.619330+00:00",
    "accept_by": null,
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "a6885688-deec-477c-be5c-5614c1e104f5",
      "status": "pending",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_b3a7b28cbd8042d8974c",
      "checkout_url": "https://checkout.paystack.com/stub-mp_b3a7b28cbd8042d8974c",
      "paid_at": null,
      "receipt_number": null,
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:26.611376+00:00"
  },
  "meta": null
}
```

`booking_verify_payment_already_applied`:
```json
{
  "data": {
    "outcome": "already_applied",
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
    "status": "awaiting_provider",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "accept_by": "2026-11-08T19:46:26.775681+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "a6885688-deec-477c-be5c-5614c1e104f5",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_b3a7b28cbd8042d8974c",
      "checkout_url": null,
      "paid_at": "2026-11-06T19:46:26+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:26.611376+00:00"
  },
  "meta": null
}
```

`booking_get_awaiting_provider` (after the webhook; `accept_by` is the expert's 48-hour deadline):
```json
{
  "data": {
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
    "status": "awaiting_provider",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "accept_by": "2026-11-08T19:46:26.775681+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "a6885688-deec-477c-be5c-5614c1e104f5",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_b3a7b28cbd8042d8974c",
      "checkout_url": null,
      "paid_at": "2026-11-06T19:46:26+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:26.611376+00:00"
  },
  "meta": null
}
```

### The webhook (for the record; the FE never calls it)

`POST /api/v1/webhooks/billing/paystack`, public, authenticated by Paystack's `x-paystack-signature` (HMAC-SHA512 of the raw body under the secret key, constant-time, fail closed). Marketplace events are those whose reference starts with `mp_`; everything else is Billing's and untouched. A `charge.success` body is **never trusted for money**: the server asks Paystack to verify the reference and checks status, amount and currency against the order. Replays and duplicates answer 200 and apply once. An unknown reference answers 200 and does nothing. If Paystack can't be reached the answer is 502, so Paystack retries.

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
    "id": "c0a6437d-97f4-4522-ae7c-be351fb54184",
    "status": "declined",
    "type": "package",
    "listing_id": "20eb9cf0-170c-4090-b73e-84602064873b",
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
    "accept_by": "2026-11-08T19:46:27.410671+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "provider_declined",
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "06dba8db-5390-4867-b971-0ffe25b220b9",
      "status": "paid",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_193e3063d9bb15cdf3a5",
      "checkout_url": null,
      "paid_at": "2026-11-06T19:46:27+00:00",
      "receipt_number": "MPR-2026-1002",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:27.378137+00:00"
  },
  "meta": null
}
```

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
        "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
        "status": "confirmed",
        "type": "consultation",
        "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
        "accept_by": "2026-11-08T19:46:26.775681+00:00",
        "due_at": null,
        "meeting_url": "https://meet.example.com/ngozi-eze",
        "closed_reason": null,
        "provider": {
          "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
          "display_name": "Ngozi Eze",
          "avatar_url": null
        },
        "order": {
          "id": "a6885688-deec-477c-be5c-5614c1e104f5",
          "status": "paid",
          "amount_minor": 1000000,
          "currency": "NGN",
          "reference": "mp_b3a7b28cbd8042d8974c",
          "checkout_url": null,
          "paid_at": "2026-11-06T19:46:26+00:00",
          "receipt_number": "MPR-2026-1001",
          "refunded_minor": 0,
          "refund_pending_minor": 0
        },
        "created_at": "2026-11-06T19:46:26.611376+00:00"
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
        "id": "b662b039-b0da-4f56-915f-70b5b9cf276b",
        "status": "cancelled",
        "type": "consultation",
        "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
        "accept_by": "2026-11-08T19:46:27.556342+00:00",
        "due_at": null,
        "meeting_url": null,
        "closed_reason": "founder_cancelled",
        "provider": {
          "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
          "display_name": "Ngozi Eze",
          "avatar_url": null
        },
        "order": {
          "id": "15c590d3-b281-45db-b60d-f9b54d5b405e",
          "status": "refunded",
          "amount_minor": 1000000,
          "currency": "NGN",
          "reference": "mp_0fd5e735be2bdb6983c1",
          "checkout_url": null,
          "paid_at": "2026-11-06T19:46:27+00:00",
          "receipt_number": "MPR-2026-1003",
          "refunded_minor": 1000000,
          "refund_pending_minor": 0
        },
        "created_at": "2026-11-06T19:46:27.530919+00:00"
      },
      {
        "id": "c0a6437d-97f4-4522-ae7c-be351fb54184",
        "status": "declined",
        "type": "package",
        "listing_id": "20eb9cf0-170c-4090-b73e-84602064873b",
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
        "accept_by": "2026-11-08T19:46:27.410671+00:00",
        "due_at": null,
        "meeting_url": null,
        "closed_reason": "provider_declined",
        "provider": {
          "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
          "display_name": "Ngozi Eze",
          "avatar_url": null
        },
        "order": {
          "id": "06dba8db-5390-4867-b971-0ffe25b220b9",
          "status": "paid",
          "amount_minor": 25000000,
          "currency": "NGN",
          "reference": "mp_193e3063d9bb15cdf3a5",
          "checkout_url": null,
          "paid_at": "2026-11-06T19:46:27+00:00",
          "receipt_number": "MPR-2026-1002",
          "refunded_minor": 0,
          "refund_pending_minor": 0
        },
        "created_at": "2026-11-06T19:46:27.378137+00:00"
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
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
    "status": "confirmed",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "accept_by": "2026-11-08T19:46:26.775681+00:00",
    "due_at": null,
    "meeting_url": "https://meet.example.com/ngozi-eze",
    "closed_reason": null,
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "a6885688-deec-477c-be5c-5614c1e104f5",
      "status": "paid",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_b3a7b28cbd8042d8974c",
      "checkout_url": null,
      "paid_at": "2026-11-06T19:46:26+00:00",
      "receipt_number": "MPR-2026-1001",
      "refunded_minor": 0,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:26.611376+00:00"
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
    "id": "b662b039-b0da-4f56-915f-70b5b9cf276b",
    "status": "cancelled",
    "type": "consultation",
    "listing_id": "2a2730fb-b9c5-404b-b971-8081c7067c6a",
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
    "accept_by": "2026-11-08T19:46:27.556342+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "founder_cancelled",
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "15c590d3-b281-45db-b60d-f9b54d5b405e",
      "status": "refunded",
      "amount_minor": 1000000,
      "currency": "NGN",
      "reference": "mp_0fd5e735be2bdb6983c1",
      "checkout_url": null,
      "paid_at": "2026-11-06T19:46:27+00:00",
      "receipt_number": "MPR-2026-1003",
      "refunded_minor": 1000000,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:27.530919+00:00"
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
        "id": "a6885688-deec-477c-be5c-5614c1e104f5",
        "status": "paid",
        "amount_minor": 1000000,
        "currency": "NGN",
        "reference": "mp_b3a7b28cbd8042d8974c",
        "checkout_url": null,
        "paid_at": "2026-11-06T19:46:26+00:00",
        "receipt_number": "MPR-2026-1001",
        "refunded_minor": 0,
        "refund_pending_minor": 0,
        "booking_id": "50038c5a-80a2-4d73-b090-46ef40435f98",
        "title": "Contract review session",
        "provider_id": "b5db3153-0e32-465a-a26f-a75c664b5ca5"
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
    "paid_at": "2026-11-06T19:46:26+00:00",
    "currency": "NGN",
    "reference": "mp_b3a7b28cbd8042d8974c",
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
        "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
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
        "accept_by": "2026-11-08T19:46:26.775681+00:00",
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
        "created_at": "2026-11-06T19:46:26.611376+00:00"
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
    "id": "50038c5a-80a2-4d73-b090-46ef40435f98",
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
    "accept_by": "2026-11-08T19:46:26.775681+00:00",
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
    "created_at": "2026-11-06T19:46:26.611376+00:00"
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
    "id": "c0a6437d-97f4-4522-ae7c-be351fb54184",
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
    "accept_by": "2026-11-08T19:46:27.410671+00:00",
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
    "created_at": "2026-11-06T19:46:27.378137+00:00"
  },
  "meta": null
}
```

`booking_get_declined_refunded`:
```json
{
  "data": {
    "id": "c0a6437d-97f4-4522-ae7c-be351fb54184",
    "status": "declined",
    "type": "package",
    "listing_id": "20eb9cf0-170c-4090-b73e-84602064873b",
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
    "accept_by": "2026-11-08T19:46:27.410671+00:00",
    "due_at": null,
    "meeting_url": null,
    "closed_reason": "provider_declined",
    "provider": {
      "id": "b5db3153-0e32-465a-a26f-a75c664b5ca5",
      "display_name": "Ngozi Eze",
      "avatar_url": null
    },
    "order": {
      "id": "06dba8db-5390-4867-b971-0ffe25b220b9",
      "status": "refunded",
      "amount_minor": 25000000,
      "currency": "NGN",
      "reference": "mp_193e3063d9bb15cdf3a5",
      "checkout_url": null,
      "paid_at": "2026-11-06T19:46:27+00:00",
      "receipt_number": "MPR-2026-1002",
      "refunded_minor": 25000000,
      "refund_pending_minor": 0
    },
    "created_at": "2026-11-06T19:46:27.378137+00:00"
  },
  "meta": null
}
```

Accepting after the 48-hour window, or after a consultation's slot has started, is 409 `ACCEPT_WINDOW_CLOSED` (unit-tested, not captured).

## Emails

Provider: a new paid booking request with the 48-hour deadline. Founder: declined / expired / cancelled with "You were refunded in full."

`email_provider_new_booking_request`:
```json
{
  "to": "delivered+e2e-a9624caaa572@resend.dev",
  "subject": "New booking request: Contract review session",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">You have a new booking request</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Hi Ngozi Eze, a founder booked <strong>Contract review session</strong> for Sun 08 Nov 2026, 08:00 Africa/Lagos and has paid. Please accept or decline by Sun 08 Nov, 20:46, or the booking expires and they are refunded in full.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/provider/bookings\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Open your bookings</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/provider/bookings\" style=\"color:#0F6AFC\">http://localhost:3000/provider/bookings</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">You have 48 hours to respond.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-06T19:46:26.793906+00:00"
}
```

`email_founder_declined_refunded`:
```json
{
  "to": "delivered+e2e-a1e5abb9790b@resend.dev",
  "subject": "Your booking could not go ahead",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px;color:#051736\">Your booking could not go ahead</h2><p style=\"margin:0 0 20px;line-height:1.5;color:#333\">Ngozi Eze could not take on <strong>Shareholder agreement drafting</strong>. You were refunded in full. Refunds can take a few working days to reach your card.</p><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/marketplace\" style=\"display:inline-block;padding:12px 22px;background:#0F6AFC;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">Browse experts</a></p><p style=\"margin:0 0 6px;font-size:13px;color:#555\">Or paste this link into your browser:</p><p style=\"margin:0 0 20px;font-size:13px;word-break:break-all\"><a href=\"http://localhost:3000/marketplace\" style=\"color:#0F6AFC\">http://localhost:3000/marketplace</a></p><p style=\"margin:0 0 4px;font-size:12px;color:#888\">Your card is never charged for a booking that doesn't go ahead.</p><p style=\"margin:0;font-size:12px;color:#888\">If you didn't request this, you can safely ignore this email.</p></div>",
  "sent_at": "2026-11-06T19:46:27.451311+00:00"
}
```

## UX instructions

- **Read `GET /marketplace/checkout` first**; if `payments_enabled` is false show "Booking opens soon", not an error toast.
- **Show the countdown** for `slot_hold_expires_at` on a pending booking and explain the slot is held for 15 minutes.
- **Never cancel a booking in the UI just because verify says `payment_not_successful`**: the founder may still be paying. Let the hold expire or let them cancel.
- **Tell the founder what a refund means**: "Refunded in full. It can take a few working days to reach your card." Use `refund_pending_minor` to show "refund in progress".
- **The founder pays the listed price**: never display commission, net or fees to founders; the receipt shows the split only as a breakdown inside the price.
- **A 404 on a booking or order** means it isn't in this workspace.
- **Timers are server-side** (the hold, the 48-hour acceptance, refund retries run once a minute); the FE only renders the resulting status and `closed_reason`.

## Verification table

| Behaviour | Verified live (`e2e/test_marketplace_booking.py`) | Unit test only | Not exercised |
|---|---|---|---|
| Roles: founder books, team member reads, mentor / anonymous refused | yes | yes | |
| Booking validation (key, terms, brief, slot rules, taken slot) | yes | yes (and overlap, buffer, lapsed hold, DB unique index) | |
| Idempotent replay / conflict / cross-workspace replay | yes (replay, conflict) | yes (cross-workspace) | |
| Slot hold blocks a rival; paid booking keeps the slot | yes (hold) | yes | |
| Signed webhook confirms; forged 401; replay once; unknown reference | yes | yes | |
| Webhook never trusts the body for the amount; non-verdicts don't block a retry | | yes | |
| Verify-payment applies once with the webhook | yes (already applied) | yes | |
| Late payment: revived when free, refunded when slot taken or booking closed | | yes | live (needs a clock) |
| Saved card: success, decline, unconfirmed | | yes | live (no saved card in e2e) |
| Accept (consultation, package), decline, window closed | yes (accept, decline) | yes | |
| Decline / cancel refunds in full; ledger balances; no double refund | yes | yes | |
| Refund retries with backoff; flagged after 12 attempts; in-flight marker, lost-answer and crash-before-commit reconcile; other orders' refunds ignored | | yes | live (needs a failing Paystack); **Paystack's refund-list filter semantics are unconfirmed against the real API** (the stub and the unit fake filter by reference themselves); a first refund never depends on the list, and a full refund is safe either way (Paystack rejects a second full refund). Confirm on staging test keys before slice E issues partial refunds |
| `refund.failed` puts the money back on the books once | yes | yes | |
| `refund.processed` settles a refund we never saw answered | | yes | |
| 15-minute hold lapse, 48-hour acceptance expiry, slot-passed expiry | | yes (the timer, with a controlled clock) | live (needs a clock) |
| Receipts, orders list, workspace scoping | yes | yes | PDF receipt (slice F) |
| Ledger append-only trigger, order CHECK constraints, migration round trip | | yes (`tests/test_marketplace_booking_migration.py`, real schema) | |
| `MARKETPLACE_PAYMENTS_ENABLED=false` refuses booking | | yes | live (the e2e server runs with it on) |
| Real Paystack (hosted checkout, real webhook signature from Paystack, real refunds) | | | yes (stubbed; needs Paystack test keys on staging) |
