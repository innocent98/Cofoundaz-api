# Billing: find the Paystack subscription on cancel and resume

**What shipped:** cancel, plan changes and resume now look up the workspace's live Paystack subscription, using the numeric customer id Paystack requires.

**Why:** Adebayo's staging walk: "Keep my plan" answered "Our payment provider couldn't complete that". `list_subscriptions` filtered by customer code, and Paystack returns an empty list for a code, so checkout never linked the subscription. Only the `subscription.create` webhook could. With no link, cancel changed our row but never disabled the subscription at Paystack, and resume then hit "This subscription is already in place." The card would have kept being charged after the plan showed as ended.

**How**
- `paystack.list_subscriptions` reads the customer first and filters by its numeric id.
- `_adopt_active_subscription` stores an active subscription on the row. `_cancel` and `_switch_paystack_plan` call it when no code is stored. Lookup errors there fail the action, so we never show "canceled" while Paystack keeps charging.
- `resume` adopts a subscription that is still active instead of creating a second one, which also repairs workspaces already in this state.
- `_request` logs Paystack's own refusal message. It was dropped before, which hid the cause.
- Rejected: `POST /subscription/enable`. Paystack answers "Subscription has been cancelled, and cannot be reactivated" for a disabled subscription.

**What's involved:** `app/services/billing/paystack.py`, `app/services/billing/flows.py`, `app/services/finance/payments/stub_transport.py` (the stub now behaves like Paystack: numeric filter, "already in place", disable sets `non-renewing`), `tests/api/billing/test_billing_flows.py`. No migration, no API shape change.

**Verification**
- `tests/api/billing`: 61 passed, including an unlinked cancel and a resume that adopts.
- Real Paystack test mode (throwaway customers): list by code returned `[]`, list by id returned the subscription; a second create while active returned 400 "This subscription is already in place."; a create after disable returned 200; enable after disable returned 400.
- Not verified: a real cancel and resume through staging after this deploy.

**Operate / roll back:** revert the PR. Nothing else to run.

**Follow-ups:** confirm webhook delivery in the Paystack dashboard. Renewals need it, because the `charge.success` webhook is what extends the period.
