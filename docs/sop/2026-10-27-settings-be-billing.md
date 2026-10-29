# SOP: Settings BE (b), Billing on Paystack

**What shipped:** Cofoundaz's own subscriptions on Paystack. Three plans (Starter free, Growth ₦24,000/mo, Scale ₦68,000/mo, annual =
10 months), hosted checkout + callback verify, a **signature-verified, idempotent webhook**, subscription state with trial / grace / cancel /
scheduled downgrade, prorated upgrades, usage meters (AI credits, seats, storage), saved cards, invoices + PDF receipts, credit-pack
add-ons, billing details, billing notifications, and a reusable **plan gate** (`require_feature` / `assert_feature`) applied to Sales outreach
(D-SAL-11). Migration `0078_billing`. Branch `feature/settings-billing` (stacked on `feature/settings-be`). Gap rows B1 to B13 in
`cofoundaz/docs/checklist/settings-prd-gaps.md`. FE guide: `docs/fe-integration-guide-settings-billing.md` (live-captured).

## Why

`/settings` was a mock Billing screen with no API behind it. Adebayo chose Paystack (HANDOFF §0b 17) and wants Sales outreach tied to a paid
plan (D-SAL-11, "leave it on for now").

## How: decisions and rejected alternatives

Product calls D-SET-1 to 14 were **built to the recommended defaults** (the lead had not returned answers when this was built); each is a data
or flag change if Adebayo decides otherwise. See the gap list for the table.

| Topic | Decision |
|---|---|
| **Gate is dark** | `PLAN_ENFORCEMENT` (env, default false everywhere). Off: every feature is allowed, nothing is blocked. On: `402 PLAN_REQUIRED` ("Upgrade to Growth to use this.") with the plan key in `field_errors`; existing workspaces get one 14-day Growth trial the first time billing is read. The gate is a *dependency* on enroll and follow-up send, so a locked workspace gets 402 before the body is validated. |
| **Webhook trust** | `x-paystack-signature` = HMAC-SHA512(raw body, secret key), constant-time compare, fails closed (no key, no header, wrong signature, oversize, non-JSON all 401 and record nothing). Public route, no bearer. |
| **Idempotency** | Verified events are inserted under `event:sha256(raw body)` with `ON CONFLICT DO NOTHING`; Paystack's retries carry identical bytes, so a redelivery answers 200 `duplicate`. Money effects are keyed again by Paystack reference: `billing_operations` row locked `FOR UPDATE`, `status` flips to `paid` once, so webhook + callback verify + retry apply once. Client POSTs take `Idempotency-Key` (reference derived from it; reusing a key for different params is `409 IDEMPOTENCY_KEY_REUSED`). |
| **Never grant what wasn't paid** | A charge whose amount or currency differs from what we initiated is marked `failed` and ignored. The callback never trusts query-string status: it asks Paystack (`verify`) for a reference that belongs to the caller's workspace (else 404). |
| **Plan codes** | Created lazily in Paystack on first checkout and stored (`billing_plans.paystack_plan_code_*`). No provisioning step. Prices live in `billing_plans` (kobo). |
| **Proration** | Paystack has none. Upgrade: charge `new price - unused credit` on the saved authorization now (limits apply immediately), disable the old Paystack subscription, create the new one starting at the current period end. Downgrade: scheduled, applied when the period ends (lazily on read, or by the renewal webhook). Paid to Starter = cancel at period end. |
| **Time is evaluated on read** | Trial end, period end with cancel/pending change, and grace expiry are applied by `apply_transitions` when the subscription is read; no cron. |
| **Failed payment** | `invoice.payment_failed` sets `past_due` + 7-day grace (`grace_days_left` in the payload), notifies founders once, keeps the plan. Grace expiry drops to Starter limits. **Read-only workspace mode is not built** (needs a write block across every router; follow-up). |
| **Cards** | Only reusable card authorizations Paystack reports are kept (brand, last4, expiry, authorization code). No PAN. Add-card is a ₦50 hosted charge that is refunded at once. The last card on a live paid plan can't be removed. |
| **Credits** | 1 credit = 50 LLM tokens (`catalog.TOKENS_PER_CREDIT`), read from `llm_usage_daily`. Pack credits count in the cycle they were bought (**"never expire" is not honoured yet**: carry-over needs a ledger). Per-feature breakdown is not recorded (`breakdown: []`). |
| **Secrets** | `PAYSTACK_SECRET_KEY` (preferred) or `PAYSTACK_TEST_SECRET_KEY`. A `sk_live_` key is refused unless `ENVIRONMENT=production`. The key is never logged, returned or put in an error. |
| **Access** | Founder only (403 "Billing is managed by your workspace founder."). The PRD's TM "billing grant" has no storage yet. |
| **Not built** | Seat / storage add-ons, "pause", read-only mode, payment-failed email, Paystack refunds by the founder, plan price edits UI, deleting a workspace's billing data. |

## What's involved

`app/services/billing/{catalog,paystack,subscription,flows,webhook,gate,usage,receipt}.py`, `app/api/v1/endpoints/billing.py` (router +
public `webhook_router`), models `app/db/models/billing.py`, migration `0078_billing` (seeds the plans), notification events
`billing.payment.failed|usage.threshold|plan.changed` (founders only), `app/api/v1/endpoints/sales_sequences.py` (`_outreach` dependency),
`app/core/config.py` (`PAYSTACK_*`, `PLAN_ENFORCEMENT`, `BILLING_TRIAL_DAYS`, `BILLING_GRACE_DAYS`), the Paystack stub in
`app/services/finance/payments/stub_transport.py` (shared with Finance). Tests: `tests/api/billing/`, `tests/test_billing_migration.py`;
live journey `e2e/test_billing.py`.

## Verification

See the FE guide's table. In short: unit tests cover every state and error; the live journey drives a real server with the in-process Paystack
stub and a webhook signed with the real HMAC. Real Paystack **test mode** was exercised through the real client for create-plan, initialize
and verify (an abandoned transaction); the hosted checkout page, a real charge and Paystack's own webhook delivery were **not** exercised.

## Operate / roll back

Env: `PAYSTACK_SECRET_KEY` (or the test key), keep `PLAN_ENFORCEMENT` off until Adebayo decides. Webhook URL to register in the Paystack dashboard:
`https://<api host>/api/v1/webhooks/billing/paystack`. Callback return page: `<APP_BASE_URL>/settings?tab=billing` (the FE reads the `reference`
Paystack appends and calls `GET /billing/subscription/verify`). Roll back with `alembic downgrade 0077_settings_notif_schedule` (drops the
billing tables) after reverting the code; no data outside the billing tables changes.

## Follow-ups

Flip `PLAN_ENFORCEMENT` (a deliberate step); read-only mode after grace; seat/storage add-ons; credit carry-over ledger + per-feature usage
breakdown; payment-failed email; TM billing grant; live-mode checklist (live key, live webhook URL, real card round trip).
