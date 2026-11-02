# FE integration guide: Settings → Billing (Paystack)

Bodies below are pasted from responses captured by `e2e/test_billing.py` against a real running server (captures in
`e2e/_captures/billing/`). **What "live" means here:** the server is real; **Paystack is an in-process stub** (`PAYMENT_PROVIDER_STUB`,
local only) and the webhook is signed by the test with the same HMAC key the server holds. Ids, emails, references and card details in the
captures are placeholders from a throwaway user. The verification table at the end says exactly what was and wasn't proven.
Base path `/api/v1`. Envelope: success `{data, meta}`, error `{error:{code,message,field_errors[]}}`. Money is **kobo** (`amount_minor`), currency `NGN`.

## What this is, in the FE's terms

The Billing tab group of Settings (Overview, Plans, Usage, Invoices, Payment, Add-ons), PRD Module 24. Everything here is **founder only**:
any other role gets `403` with `"Billing is managed by your workspace founder."` on every route below except `GET /billing/plans`
(open to any member, so a plan name can be rendered). Every route needs `Authorization` + `X-Workspace-Id`.

**Plan gating is switched off** (`enforcement: false` in the subscription payload): nothing is locked by plan yet, Sales outreach included.
Build the locked states against the `402 PLAN_REQUIRED` shape below, but expect to see them only after Adebayo turns enforcement on.

## Money flows in one picture

Paystack takes the card on **its hosted page**. The FE never sees card data and never talks to Paystack.

1. FE calls a `POST` that needs payment (`/subscription/checkout`, `/payment-methods`, `/addons/{key}/purchase`). Response `{authorization_url, reference}`.
2. FE sends the browser to `authorization_url` (full redirect).
3. Paystack returns the user to `<app>/settings?tab=billing` with `?reference=...&trxref=...` appended.
4. FE calls `GET /billing/subscription/verify?reference=...` once, shows the result, then refetches the subscription.
5. Paystack's webhook usually lands first or at the same time. Both paths are safe to run together: the effect happens once.

**Trap:** never trust the query string to decide success. Only the `verify` response (`status: "paid"`) counts. `status: "pending"` means the user
abandoned or the payment hasn't landed: offer "Try again" (a new `POST`), don't poll forever (webhook usually settles within seconds; poll `GET /subscription`
every 3 s for up to 30 s at most, then show "We'll update this page as soon as payment confirms").

## Plans

`GET /billing/plans` (200, any member). `modules` are the feature keys the plan unlocks; `seats: null` means unlimited. Prices in kobo.
```json
{
  "data": {
    "plans": [
      {
        "key": "starter",
        "name": "Starter",
        "tagline": "For validating an idea.",
        "currency": "NGN",
        "price_monthly_minor": 0,
        "price_annual_minor": 0,
        "ai_credits": 500,
        "seats": 1,
        "storage_mb": 5120,
        "features": [
          "Core AI Co-Founder",
          "Roadmap and Health Score",
          "Business Builder",
          "Documents",
          "500 AI credits monthly",
          "1 seat"
        ],
        "modules": []
      },
      {
        "key": "growth",
        "name": "Growth",
        "tagline": "For getting to revenue.",
        "currency": "NGN",
        "price_monthly_minor": 2400000,
        "price_annual_minor": 24000000,
        "ai_credits": 5000,
        "seats": 5,
        "storage_mb": 20480,
        "features": [
          "Everything in Starter",
          "Marketing, Sales, Finance, Validation Hubs",
          "Analytics and Reports",
          "5,000 AI credits monthly",
          "5 seats"
        ],
        "modules": [
          "marketing",
          "sales",
          "finance",
          "validation",
          "analytics",
          "sales_outreach"
        ]
      },
      {
        "key": "scale",
        "name": "Scale",
        "tagline": "For raising and expanding.",
        "currency": "NGN",
        "price_monthly_minor": 6800000,
        "price_annual_minor": 68000000,
        "ai_credits": 20000,
        "seats": null,
        "storage_mb": 102400,
        "features": [
          "Everything in Growth",
          "Funding Hub and Investor Readiness",
          "Legal and Compliance",
          "Priority support",
          "20,000 AI credits monthly",
          "Unlimited seats"
        ],
        "modules": [
          "marketing",
          "sales",
          "finance",
          "validation",
          "analytics",
          "sales_outreach",
          "funding",
          "investor_readiness",
          "legal",
          "priority_support"
        ]
      }
    ],
    "addons": [
      {
        "key": "credits_2000",
        "name": "2,000 AI credits",
        "description": "One-time top-up.",
        "credits": 2000,
        "price_minor": 800000
      },
      {
        "key": "credits_10000",
        "name": "10,000 AI credits",
        "description": "Best value for heavy AI months.",
        "credits": 10000,
        "price_minor": 3200000
      }
    ]
  },
  "meta": null
}
```

## Subscription (Overview)

`GET /billing/subscription` (200, founder). Statuses: `free`, `trialing`, `active`, `past_due`, `canceled`.
```json
{
  "data": {
    "plan": {
      "key": "starter",
      "name": "Starter"
    },
    "interval": "monthly",
    "status": "free",
    "price_minor": 0,
    "currency": "NGN",
    "current_period_start": null,
    "renews_at": null,
    "ends_at": null,
    "trial_ends_at": null,
    "trial_available": true,
    "cancel_at_period_end": false,
    "cancel_reason": null,
    "pending_change": null,
    "payment_issue": null,
    "enforcement": false
  },
  "meta": null
}
```
Field notes (all verified in the captures or unit tests):
- `renews_at` is set only for a paid plan that isn't canceling; `ends_at` is set while canceling **or** trialing. Show exactly one of them.
- `trial_available` is true until the one trial per workspace is used and while not on a paid plan.
- `pending_change` is a scheduled downgrade (`{plan:{key,name}, interval}`), `null` otherwise.
- `payment_issue` is `null` unless `past_due`; then `{failed_at, grace_ends_at, grace_days_left}`: drive the site-wide banner from it
  ("Payment failed. Update your payment method to keep your workspace active, {n} days left in grace period." CTA "Fix payment").
- `enforcement` tells the FE whether locks are live.
```json
{
  "data": {
    "plan": {
      "key": "growth",
      "name": "Growth"
    },
    "interval": "monthly",
    "status": "active",
    "price_minor": 2400000,
    "currency": "NGN",
    "current_period_start": "2026-10-27T12:00:00+00:00",
    "renews_at": "2026-11-27T12:00:00+00:00",
    "ends_at": null,
    "trial_ends_at": null,
    "trial_available": false,
    "cancel_at_period_end": false,
    "cancel_reason": null,
    "pending_change": null,
    "payment_issue": null,
    "enforcement": false
  },
  "meta": null
}
```
```json
{
  "data": {
    "plan": {
      "key": "scale",
      "name": "Scale"
    },
    "interval": "monthly",
    "status": "active",
    "price_minor": 6800000,
    "currency": "NGN",
    "current_period_start": "2026-10-27T13:31:40.439611+00:00",
    "renews_at": "2026-11-27T13:31:40.439611+00:00",
    "ends_at": null,
    "trial_ends_at": null,
    "trial_available": false,
    "cancel_at_period_end": false,
    "cancel_reason": null,
    "pending_change": {
      "plan": {
        "key": "growth",
        "name": "Growth"
      },
      "interval": "monthly"
    },
    "payment_issue": null,
    "enforcement": false
  },
  "meta": null
}
```
```json
{
  "data": {
    "plan": {
      "key": "scale",
      "name": "Scale"
    },
    "interval": "monthly",
    "status": "active",
    "price_minor": 6800000,
    "currency": "NGN",
    "current_period_start": "2026-10-27T13:31:40.439611+00:00",
    "renews_at": null,
    "ends_at": "2026-11-27T13:31:40.439611+00:00",
    "trial_ends_at": null,
    "trial_available": false,
    "cancel_at_period_end": true,
    "cancel_reason": "Too expensive right now",
    "pending_change": null,
    "payment_issue": null,
    "enforcement": false
  },
  "meta": null
}
```
```json
{
  "data": {
    "plan": {
      "key": "scale",
      "name": "Scale"
    },
    "interval": "monthly",
    "status": "past_due",
    "price_minor": 6800000,
    "currency": "NGN",
    "current_period_start": "2026-11-27T10:00:00+00:00",
    "renews_at": "2026-12-27T10:00:00+00:00",
    "ends_at": null,
    "trial_ends_at": null,
    "trial_available": false,
    "cancel_at_period_end": false,
    "cancel_reason": null,
    "pending_change": null,
    "payment_issue": {
      "failed_at": "2026-10-27T13:31:40.938182+00:00",
      "grace_ends_at": "2026-11-03T13:31:40.938182+00:00",
      "grace_days_left": 7
    },
    "enforcement": false
  },
  "meta": null
}
```

### Start a trial: `POST /billing/trial` (200)
One 14-day Growth trial per workspace, no card. After it ends the workspace is on Starter (never read-only). Second call is **409 `TRIAL_UNAVAILABLE`**.
```json
{
  "data": {
    "plan": {
      "key": "growth",
      "name": "Growth"
    },
    "interval": "monthly",
    "status": "trialing",
    "price_minor": 2400000,
    "currency": "NGN",
    "current_period_start": "2026-10-27T13:31:42.796888+00:00",
    "renews_at": null,
    "ends_at": "2026-11-10T13:31:42.796888+00:00",
    "trial_ends_at": "2026-11-10T13:31:42.796888+00:00",
    "trial_available": false,
    "cancel_at_period_end": false,
    "cancel_reason": null,
    "pending_change": null,
    "payment_issue": null,
    "enforcement": false
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "TRIAL_UNAVAILABLE",
    "message": "This workspace has already used its trial.",
    "field_errors": []
  }
}
```

### Subscribe: `POST /billing/subscription/checkout` (200)
`{plan: "growth"|"scale", interval: "monthly"|"annual"}`. Send an `Idempotency-Key` header (any unique string per user action): a replay returns the same link.
Starter is 422 (nothing to pay), an unknown plan 404, already on a paid plan **409 `ALREADY_SUBSCRIBED`** (use change instead), the same key reused for a
different request **409 `IDEMPOTENCY_KEY_REUSED`**. Billing not configured on the server: **503 `BILLING_NOT_CONFIGURED`**; Paystack down **502 `PAYSTACK_UNAVAILABLE`**.
```json
{
  "data": {
    "authorization_url": "https://checkout.paystack.com/stub-cfz_checkout_d1c31eeec551fadcda49",
    "reference": "cfz_checkout_d1c31eeec551fadcda49"
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "PLAN_NOT_PURCHASABLE",
    "message": "Starter is free: there's nothing to pay for.",
    "field_errors": []
  }
}
```

### The callback: `GET /billing/subscription/verify?reference=` (200)
`outcome` is one of `applied`, `already_processed`, `not_successful`, `amount_mismatch`, `duplicate_payment_refunded` (a second checkout link was paid after the workspace had already subscribed: the payment is recorded as a refunded invoice and refunded). A reference that isn't this workspace's is **404**.
```json
{
  "data": {
    "reference": "cfz_checkout_d1c31eeec551fadcda49",
    "kind": "checkout",
    "status": "paid",
    "outcome": "already_processed"
  },
  "meta": null
}
```
(The first call, before the webhook, returns `outcome: "applied"`, `status: "paid"`; `verify_addon` below shows that shape.)
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### Change plan
`POST /billing/subscription/preview-change` `{plan, interval}` returns what the confirm sheet should say. `kind` is `upgrade`, `downgrade`, `cancel`
(paid to Starter), `checkout` (not on a paid plan yet: go through checkout), `free` or `same`.
- Upgrade copy: "You'll be charged {amount_due_minor} today (prorated). New limits apply immediately." `credit_minor` is the unused part of the current period.
- Downgrade copy: "Downgrade to {plan}? You'll keep {current} features until {keeps_until}." Nothing is charged.
```json
{
  "data": {
    "kind": "upgrade",
    "amount_due_minor": 4404929,
    "credit_minor": 2395071,
    "effective": "now",
    "currency": "NGN",
    "keeps_until": null
  },
  "meta": null
}
```
```json
{
  "data": {
    "kind": "downgrade",
    "amount_due_minor": 0,
    "credit_minor": 0,
    "effective": "period_end",
    "currency": "NGN",
    "keeps_until": "2026-11-27T13:31:40.439611+00:00"
  },
  "meta": null
}
```

`POST /billing/subscription/change` (same body). **The `Idempotency-Key` header is required** (422 `IDEMPOTENCY_KEY_REQUIRED` without it): generate one per confirm-click and reuse it on retries. `result` is `applied` (upgrade charged the saved card and switched now),
`scheduled` (downgrade or cancel at period end), or `checkout` (carries `authorization_url` + `reference`).
```json
{
  "data": {
    "result": "applied",
    "amount_charged_minor": 4404929
  },
  "meta": null
}
```
```json
{
  "data": {
    "result": "scheduled",
    "effective_at": "2026-11-27T13:31:40.439611+00:00"
  },
  "meta": null
}
```
Errors: **409 `NO_CHANGE`**; **409 `NO_PAYMENT_METHOD`** (upgrade with no saved card: send them to add one); **402 `CHARGE_FAILED`** (card declined, nothing changed) and **402 `CHARGE_PENDING`** (the bank hasn't confirmed; the plan changes by itself when Paystack's webhook confirms, so show "Check back in a minute" and refetch the subscription): both unit tested.
```json
{
  "error": {
    "code": "NO_CHANGE",
    "message": "You're already on that plan.",
    "field_errors": []
  }
}
```

### Cancel and resume
`POST /billing/subscription/cancel` `{reason?}` (reason ≤ 80 chars, shown back in `cancel_reason`) keeps the plan until `ends_at`; data is kept 90 days after.
**409 `NOT_SUBSCRIBED`** when there is no paid plan. `POST /billing/subscription/resume` undoes it before the period ends (**409 `NOT_CANCELED`** otherwise; **409 `NO_PAYMENT_METHOD`** when no card is saved) and returns the subscription.
```json
{
  "data": {
    "cancel_at_period_end": true,
    "ends_at": "2026-11-27T13:31:40.439611+00:00"
  },
  "meta": null
}
```
```json
{
  "data": {
    "plan": {
      "key": "scale",
      "name": "Scale"
    },
    "interval": "monthly",
    "status": "active",
    "price_minor": 6800000,
    "currency": "NGN",
    "current_period_start": "2026-10-27T13:31:40.439611+00:00",
    "renews_at": "2026-11-27T13:31:40.439611+00:00",
    "ends_at": null,
    "trial_ends_at": null,
    "trial_available": false,
    "cancel_at_period_end": false,
    "cancel_reason": null,
    "pending_change": null,
    "payment_issue": null,
    "enforcement": false
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "NOT_SUBSCRIBED",
    "message": "There's no paid plan to cancel.",
    "field_errors": []
  }
}
```
The retention "Pause for 2 months" in the design is **not built**: don't render a button for it.

## Usage

`GET /billing/usage` (200). One credit is 50 LLM tokens. The cycle is the paid period, else the calendar month. `ai_credits.limit` includes pack credits bought
in the cycle (`from_packs`). `breakdown` is always `[]` today (per-feature usage isn't recorded): hide the "What used credits" table when empty.
At 90% a `billing.usage.threshold` notification is sent once per cycle (the check runs when this endpoint is read).
```json
{
  "data": {
    "cycle": {
      "starts_on": "2026-10-27",
      "resets_on": "2026-11-27"
    },
    "ai_credits": {
      "used": 0,
      "limit": 5000,
      "percent": 0,
      "from_packs": 0,
      "tokens_per_credit": 50
    },
    "seats": {
      "used": 1,
      "limit": 5
    },
    "storage": {
      "used_mb": 0.0,
      "limit_mb": 20480
    },
    "daily": [],
    "breakdown": []
  },
  "meta": null
}
```

## Invoices

`GET /billing/invoices` (200, newest first). `status`: `paid`, `failed`. `kind`: `subscription`, `upgrade`, `addon`. The ₦50 card check never appears.
```json
{
  "data": [
    {
      "id": "ead643cf-2b33-4e2e-8c01-bb5acdd0cdec",
      "number": "CFZ-2026-0005",
      "kind": "addon",
      "description": "2,000 AI credits",
      "amount_minor": 800000,
      "currency": "NGN",
      "status": "paid",
      "date": "2026-10-01T09:30:00+00:00"
    },
    {
      "id": "a7a15940-7438-434b-a588-216ae7e1990e",
      "number": "CFZ-2026-0004",
      "kind": "subscription",
      "description": "Scale plan, monthly",
      "amount_minor": 6800000,
      "currency": "NGN",
      "status": "failed",
      "date": "2026-10-27T13:31:40.922667+00:00"
    },
    {
      "id": "d74b1363-f633-4a7f-bb16-1a695e759c58",
      "number": "CFZ-2026-0003",
      "kind": "subscription",
      "description": "Scale plan, monthly",
      "amount_minor": 6800000,
      "currency": "NGN",
      "status": "paid",
      "date": "2026-11-27T10:00:00+00:00"
    },
    {
      "id": "bd8d49f3-1520-4604-b162-6697d8bf99f2",
      "number": "CFZ-2026-0002",
      "kind": "upgrade",
      "description": "Upgrade to Scale, monthly (prorated)",
      "amount_minor": 4404929,
      "currency": "NGN",
      "status": "paid",
      "date": "2026-10-27T13:31:40.439611+00:00"
    },
    {
      "id": "fc530ced-a39b-42a1-963d-6f3e2c6d7960",
      "number": "CFZ-2026-0001",
      "kind": "subscription",
      "description": "Growth plan, monthly",
      "amount_minor": 2400000,
      "currency": "NGN",
      "status": "paid",
      "date": "2026-10-27T12:00:00+00:00"
    }
  ],
  "meta": null
}
```
`GET /billing/invoices/{id}/pdf` returns a PDF receipt (`application/pdf`, attachment). Fetch it with the bearer token (not a plain link): use `fetch` then a blob URL.
```json
{
  "status": 200,
  "content-type": "application/pdf",
  "content-disposition": "attachment; filename=\"CFZ-2026-0005.pdf\"",
  "starts_with": "%PDF-"
}
```

## Payment methods

`GET /billing/payment-methods` (200). Only brand, last four, expiry and bank: no token, no PAN.
```json
{
  "data": [
    {
      "id": "fbad42a4-2627-4d9e-935c-0523c43aeb2e",
      "brand": "visa",
      "last4": "4081",
      "exp_month": "12",
      "exp_year": "2030",
      "bank": "TEST BANK",
      "is_default": true
    }
  ],
  "meta": null
}
```
- `POST /billing/payment-methods` (200) returns a checkout link for a **₦50 charge that is refunded straight away** (tell the user: "We'll charge ₦50 to verify the card and refund it immediately").
```json
{
  "data": {
    "authorization_url": "https://checkout.paystack.com/stub-cfz_card_b9298b5da07643a4a069",
    "reference": "cfz_card_b9298b5da07643a4a069"
  },
  "meta": null
}
```
- `PUT /billing/payment-methods/{id}/default` (200). `DELETE /billing/payment-methods/{id}` (200). Unknown or already removed id: 404.
- Removing the only card while on a paid plan (even one set to cancel): **409 `LAST_PAYMENT_METHOD`** "Add another method before removing this one."
```json
{
  "error": {
    "code": "LAST_PAYMENT_METHOD",
    "message": "Add another method before removing this one.",
    "field_errors": []
  }
}
```

## Add-ons

`GET /billing/addons` lists the packs (also inside `GET /billing/plans`). Only **AI credit packs** exist (2,000 for ₦8,000, 10,000 for ₦32,000). "Extra seat" and "Storage +50GB"
from the design are not built: don't render them as buyable. `POST /billing/addons/{key}/purchase` (send `Idempotency-Key`) returns a checkout link; after the
callback verify, credits are live ("Added. 2,000 credits are live now.").
```json
{
  "data": {
    "authorization_url": "https://checkout.paystack.com/stub-cfz_addon_5fd917e38c7800358a00",
    "reference": "cfz_addon_5fd917e38c7800358a00"
  },
  "meta": null
}
```
```json
{
  "data": {
    "reference": "cfz_addon_5fd917e38c7800358a00",
    "kind": "addon",
    "status": "paid",
    "outcome": "applied"
  },
  "meta": null
}
```

## Billing details: `GET`/`PUT /billing/details`
Company name (160), tax ID (40), address (500); all optional, a blank clears. Printed on receipts.
```json
{
  "data": {
    "company_name": "Cofoundaz Ltd",
    "tax_id": "TIN-PLACEHOLDER",
    "address": "1 Placeholder Street, Lagos"
  },
  "meta": null
}
```

## Webhook (not an FE concern, documented for completeness)

`POST /webhooks/billing/paystack`, public, signature only. Events handled: `charge.success`, `subscription.create`, `subscription.not_renew`,
`subscription.disable`, `invoice.payment_failed`; everything else is acknowledged and ignored. Wrong or missing signature is **401 `INVALID_SIGNATURE`**.
```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Signature check failed.",
    "field_errors": []
  }
}
```
```json
{
  "data": {
    "received": true,
    "outcome": "applied"
  },
  "meta": null
}
```
```json
{
  "data": {
    "received": true,
    "outcome": "duplicate"
  },
  "meta": null
}
```
```json
{
  "data": {
    "received": true,
    "outcome": "renewed"
  },
  "meta": null
}
```
```json
{
  "data": {
    "received": true,
    "outcome": "past_due"
  },
  "meta": null
}
```

## The plan gate (402) and what to build for it

When `enforcement` is on, a locked route answers **402**:
```json
{"error": {"code": "PLAN_REQUIRED", "message": "Upgrade to Growth to use this.", "field_errors": [{"field": "plan", "message": "growth"}]}}
```
`field_errors[0].message` is the key of the cheapest plan that unlocks the feature: use it for the "Upgrade to {plan}" CTA. Today this is applied to Sales outreach only
(`POST /sales/sequences/{id}/enroll`, `POST /sales/deals/{id}/follow-up/send`). This exact body is from unit tests (`tests/api/billing/test_plan_gate.py`), not the live journey,
because the journey runs with enforcement off.

## UX consequences the FE must surface

- **A plan change can charge the card immediately** (upgrade): always show the preview amount and require confirmation. An upgrade starts a **new billing period from today** (the unused part of the old period is credited, but credit above the new price is not carried), so say "Your new plan renews on {renews_at}".
- **A failed payment keeps the plan for 7 days**, then the workspace drops to Starter limits. Show the banner on every page for the founder while `payment_issue` is set.
- **Cancel is at period end**, not instant; the data is kept 90 days after. Show `ends_at`.
- **A trial ends into Starter**, not a paywall: say so ("Your trial ends on {date}. You'll move to Starter unless you subscribe.").
- **Read-only mode after grace is not built:** don't show the PRD's "Workspace is read-only until payment is fixed." banner.

## Verification table

| Behaviour | How verified |
|---|---|
| Plans, subscription (free/active/pending/canceling/past_due), usage, invoices, cards, details | live journey (captures above), Paystack stubbed |
| Checkout link + idempotent replay, starter 422 | live journey |
| Signed webhook: bad signature 401, success, duplicate, renewal, payment failed | live journey (signed with the server's own key) |
| Callback verify (after webhook, add-on, foreign reference 404) | live journey |
| Upgrade (charge saved card), scheduled downgrade, cancel, resume, no-change 409 | live journey |
| Trial once + 409 again | live journey |
| Decline → 402 `CHARGE_FAILED` (operation survives the error), pending charge 402 `CHARGE_PENDING`, `IDEMPOTENCY_KEY_REQUIRED`, upgrade restarts the period, duplicate checkout refunded, resume/remove-last-card rules, lapse of an active plan with no renewal, renewal anchored on the payment, ambiguous customer code ignored, late retry reactivates from the plan code, `NO_PAYMENT_METHOD`, `IDEMPOTENCY_KEY_REUSED`, amount/currency mismatch ignored, 429-free paths, grace expiry to Starter, trial expiry, last-card rule | unit tests only |
| Plan gate 402 body, trial/paid pass, dark when off | unit tests only |
| Real Paystack test mode: create plan, initialize, verify (abandoned) through the real client | one manual run, shapes consistent with the code; not repeatable in CI |
| **Paystack hosted checkout page, a real card charge, a real `subscription.create`/renewal/failed delivery from Paystack, real refund of the ₦50 check, real `subscription/disable`** | **NOT exercised**: needs a browser on Paystack's page and a public webhook URL. Payload shapes for these events come from Paystack's documented webhook format and the verify response, not from a live capture. |
| Receipt PDF content | headers + `%PDF` prefix only |
