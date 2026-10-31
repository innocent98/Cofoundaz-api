# FE integration guide: Admin slice C (Billing overrides)

Every body below is pasted from a live run against a real server: `e2e/test_admin_billing.py`, captures in `e2e/_captures/admin_billing/<name>.json` (the placeholder in brackets after each heading is the capture name). Emails and IDs are throwaway values from that run (placeholders). Base path `/api/v1/super-admin`. Success: `{"data": ..., "meta": ...}`; errors: `{"error": {"code","message","field_errors"}}`, **except the hidden 404 `{"detail":"Not Found"}`**. Auth, step-up and the audit explorer are in `fe-integration-guide-admin-staff.md`. Decisions this follows: D-ADM-6 (Paystack, not Stripe; refunds and credits are internal entries), P1 (a fresh code on every mutation), and the slice C calls below.

## What this is

A **Billing overrides** page in the Super Admin console (PRD 26.6): find a workspace (use the Workspaces search from `fe-integration-guide-admin-users.md`, then open its billing), see its subscription, invoices and past overrides, and apply one of four overrides. **Super Admin only**: an Admin (any scope) gets the hidden 404 on every route here (`overview_non_staff_404` shows the shape).

| Override | Route | What it does to the founder |
|---|---|---|
| Refund | `POST /billing/{workspace_id}/refund` | The invoice shows `refunded_minor`; a full refund turns its status to `refunded`. The plan is **not** touched. |
| Credit | `POST /billing/{workspace_id}/credit` | Adds AI credits (1 credit = 50 tokens) to the founder's Usage limit at once. They never expire. |
| Extend trial | `POST /billing/{workspace_id}/extend-trial` | Moves the founder's trial end date later. |
| Comp | `POST /billing/{workspace_id}/comp` | Gives a free paid plan for N days; it ends by itself. |

Read: `GET /billing/{workspace_id}`.

## Every mutation: the same envelope

Body always has `request_id` (a UUID **you generate once per button press**), `reason` (10 to 500 characters, trimmed), `totp_code` (6 digits, the staff member's own fresh authenticator code, single use), plus the fields of the override. Unknown fields are rejected (422). Unit amounts: refunds are **kobo** (integer); credits are AI credits; days are whole days.

- **Retry safety.** If a request times out or the network drops, resend the **same `request_id`** (with a new code; the old one is spent). The server answers with the stored result and `"replayed": true`, and changes nothing. A reused `request_id` with different values, a different workspace, a different kind or a different staff member is 409 `REQUEST_ID_REUSED`. Generate a new `request_id` for a new press.
- A short reason is 422 (`refund_reason_too_short`) and does not consume the code. A wrong or already-used code is 403 (not 401: do not sign out):

`refund_wrong_code`:
```json
{"error":{"code":"STEP_UP_INVALID","message":"That authenticator code isn't right, or was already used. Enter a new one.","field_errors":[]}}
```
`refund_reason_too_short`:
```json
{"error":{"code":"VALIDATION_ERROR","message":"Please check the highlighted fields.","field_errors":[{"field":"reason","message":"String should have at least 10 characters"}]}}
```
- Every override writes an audit row (`billing.refund.issued`, `billing.credit.applied`, `billing.trial.extended`, `billing.comp.granted`) with the reason and amounts, visible in the audit explorer (`audit_for_workspace`).
- Unknown workspace: 404 `WORKSPACE_NOT_FOUND` (`overview_unknown_workspace`).

## `GET /billing/{workspace_id}` [overview]

```json
{"data":{"workspace":{"id":"edb5de9d-0515-4903-870c-c8c684312f2a","name":"PayCo"},"subscription":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"active","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T14:46:29+00:00","renews_at":"2026-11-30T14:46:29+00:00","ends_at":null,"trial_ends_at":null,"trial_available":false,"comped":false,"cancel_at_period_end":false,"cancel_reason":null,"pending_change":null,"payment_issue":null,"enforcement":false},"invoices":[{"id":"e78ff53c-d3a8-4404-b2d8-68f59fffaa78","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":0,"refundable_minor":2400000,"currency":"NGN","status":"paid","paid_at":"2026-10-31T14:46:29+00:00"}],"credits_granted_total":0,"overrides":[]},"meta":null}
```

Nesting traps:
- **`subscription` is `null`** (present, not absent) for a workspace that never opened Billing; treat it as Starter and free.
- `subscription` is the same object a founder gets from `GET /billing/subscription`, including `comped`.
- On `invoices[]` the field is **`paid_at`**; the founder's own invoice list uses **`date`** instead (see below). `refundable_minor` is what is left to refund (0 unless the invoice is `paid`).
- `overrides[]` is newest first, up to 50; `credits_granted_total` is the sum of all credit overrides for the workspace.

## Refund [refund_partial_ok]

Body: `{"request_id", "invoice_id", "amount_minor", "reason", "totp_code"}`. Pick the invoice from `invoices[]` where `refundable_minor > 0`; default the amount field to `refundable_minor` and cap it there.

```json
{"data":{"replayed":false,"override":{"id":"90617674-0fb5-4685-a5c4-834d4a8c35c9","request_id":"86b2235d-700c-47aa-890b-1a18a9515314","kind":"refund","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-0b8429e2df34@resend.dev","invoice_id":"e78ff53c-d3a8-4404-b2d8-68f59fffaa78","amount_minor":1000000,"credits":null,"days":null,"plan_key":null,"provider_refund":"pending","details":{"invoice_number":"CFZ-2026-0001","invoice_status_after":"paid"},"created_at":"2026-10-31T14:46:30.004213+00:00"},"invoice":{"id":"e78ff53c-d3a8-4404-b2d8-68f59fffaa78","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":1000000,"refundable_minor":1400000,"currency":"NGN","status":"paid","paid_at":"2026-10-31T14:46:29+00:00"}},"meta":null}
```

- **This does not return money.** `provider_refund` is `"pending"`: the refund is an entry in Cofoundaz only (D-ADM-6). The page must say plainly, next to the button and in the success toast: "Recorded. No money has been sent back yet; send it from the Paystack dashboard." (Paystack refunds are manual for now.)
- Partial refunds are allowed up to the invoice amount minus earlier refunds. At 100% the invoice status becomes `refunded` (`refund_full_ok`).
- A refund never changes the founder's plan. To also end the plan, cancel it separately (not part of this slice).
- Refunding an add-on or credit-pack invoice does **not** take the credits back.

Replay (`refund_replay`) is the same body with `"replayed": true`. Errors:

`refund_over_invoice` (422; the message and `field_errors` carry the remaining kobo):
```json
{"error":{"code":"REFUND_EXCEEDS_INVOICE","message":"That is more than is left to refund on this invoice (1400000 kobo).","field_errors":[{"field":"amount_minor","message":"at most 1400000"}]}}
```
`refund_already_refunded` (409; the same code is returned for a failed invoice):
```json
{"error":{"code":"INVOICE_NOT_REFUNDABLE","message":"Only a paid invoice can be refunded; this one is refunded.","field_errors":[]}}
```
`refund_invoice_of_another_workspace` (404 `INVOICE_NOT_FOUND`):
```json
{"error":{"code":"INVOICE_NOT_FOUND","message":"No invoice with that id in this workspace.","field_errors":[]}}
```

What the founder sees afterwards (`founder_invoices_after`, the existing `GET /billing/invoices`; note the new `refunded_minor` and that this list uses **`date`**, not `paid_at`):
```json
{"data":[{"id":"e78ff53c-d3a8-4404-b2d8-68f59fffaa78","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":2400000,"currency":"NGN","status":"refunded","date":"2026-10-31T14:46:29+00:00"}],"meta":null}
```
The founder's subscription is unchanged (`founder_subscription_after_refund`), and the downloadable receipt PDF gains a "Refunded" line.

## Credit [credit_ok]

Body: `{"request_id", "credits" (1 to 100000), "reason", "totp_code"}`.

```json
{"data":{"replayed":false,"override":{"id":"0f1ed8d7-f413-4bc7-9ebc-c2e62953400c","request_id":"f099e1dd-57cd-4efe-bc9e-9b3182ff7cab","kind":"credit","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-aa8a0752570d@resend.dev","invoice_id":null,"amount_minor":null,"credits":500,"days":null,"plan_key":null,"provider_refund":null,"details":{},"created_at":"2026-10-31T14:46:30.187188+00:00"}},"meta":null}
```

The founder's usage read changes at once (`founder_usage_after_credit`: `limit` went from 5000 to 5500, `from_packs` is 500). No money is involved.

```json
{"data":{"cycle":{"starts_on":"2026-10-31","resets_on":"2026-11-30"},"ai_credits":{"used":0,"limit":5500,"percent":0,"from_packs":500,"tokens_per_credit":50},"seats":{"used":1,"limit":5},"storage":{"used_mb":0.0,"limit_mb":20480},"daily":[],"breakdown":[]},"meta":null}
```

## Extend trial [extend_trial_ok]

Body: `{"request_id", "days" (1 to 60), "reason", "totp_code"}`. Only for `subscription.status == "trialing"`; the button should be disabled otherwise.

```json
{"data":{"replayed":false,"override":{"id":"b5d5aa2c-154d-4d77-bea9-d87f679e61e7","request_id":"036fde68-a86c-46ff-875e-80c9b05dc0b6","kind":"trial","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-0b8429e2df34@resend.dev","invoice_id":null,"amount_minor":null,"credits":null,"days":7,"plan_key":null,"provider_refund":null,"details":{"trial_ends_at_after":"2026-11-21T14:46:30.264066+00:00","trial_ends_at_before":"2026-11-14T14:46:30.264066+00:00"},"created_at":"2026-10-31T14:47:00.395710+00:00"},"subscription":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"trialing","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T14:46:30.264066+00:00","renews_at":null,"ends_at":"2026-11-21T14:46:30.264066+00:00","trial_ends_at":"2026-11-21T14:46:30.264066+00:00","trial_available":false,"comped":false,"cancel_at_period_end":false,"cancel_reason":null,"pending_change":null,"payment_issue":null,"enforcement":false}},"meta":null}
```

- The response includes the updated `subscription`; the founder's own read agrees (`founder_subscription_after_trial_extension`).
- A trial may run at most 90 days in total from the day it started: `TRIAL_TOO_LONG` (422).
- Not trialing (free, paid, or a trial that has already run out): 409 `NOT_TRIALING`.

`extend_trial_too_long`:
```json
{"error":{"code":"TRIAL_TOO_LONG","message":"A trial can run at most 90 days in total.","field_errors":[{"field":"days","message":"too long for this trial"}]}}
```
`extend_trial_not_trialing`:
```json
{"error":{"code":"NOT_TRIALING","message":"Only a workspace that is on a trial can have it extended.","field_errors":[]}}
```

## Comp [comp_ok]

Body: `{"request_id", "plan_key" (a paid plan key from the plans list, e.g. "growth"), "days" (1 to 365), "reason", "totp_code"}`. This is also how "Change plan (no charge)" is done: there is no separate route.

```json
{"data":{"replayed":false,"override":{"id":"bd68cb6a-814d-470b-a073-f70d9535907d","request_id":"db9702e5-e7c0-44b0-8cf4-e2d15a402fdf","kind":"comp","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-aa8a0752570d@resend.dev","invoice_id":null,"amount_minor":null,"credits":null,"days":30,"plan_key":"growth","provider_refund":null,"details":{"before":{"comped":false,"status":"free","plan_key":"starter"},"ends_at":"2026-11-30T14:47:30.714730+00:00"},"created_at":"2026-10-31T14:47:30.692031+00:00"},"subscription":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"active","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T14:47:30.714730+00:00","renews_at":null,"ends_at":"2026-11-30T14:47:30.714730+00:00","trial_ends_at":null,"trial_available":false,"comped":true,"cancel_at_period_end":true,"cancel_reason":"comp","pending_change":null,"payment_issue":null,"enforcement":false}},"meta":null}
```

- The founder gets the plan immediately: status `active`, **no card, no charge**, and it **ends by itself** at `ends_at`, after which the workspace is on Starter with status `canceled`.
- A comped workspace is not revenue: it is excluded from MRR and paid conversion on the dashboard. The staff Users, Workspaces list and detail now carry a `comped` flag; show a "Comped" tag.
- Not allowed on a workspace that pays (status `active` or `past_due` and not comped): 409 `PAID_SUBSCRIPTION` (`comp_paying_workspace`). A workspace on a trial can be comped (the trial clock stops). A second comp on a comped workspace replaces it.
- Starter: 422 `PLAN_NOT_COMPABLE` (`comp_starter`). Unknown plan: 404 `PLAN_NOT_FOUND` (unit-tested, not captured live).

`comp_paying_workspace`:
```json
{"error":{"code":"PAID_SUBSCRIPTION","message":"This workspace pays for a plan. Cancel it first; a comp would stop the charges from matching what they get.","field_errors":[]}}
```

What the founder sees (`founder_subscription_comped`; **note `cancel_at_period_end: true` and `cancel_reason: "comp"`: they are how a comp ends, not a cancellation by the founder; `renews_at` is null**):
```json
{"data":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"active","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T14:47:30.714730+00:00","renews_at":null,"ends_at":"2026-11-30T14:47:30.714730+00:00","trial_ends_at":null,"trial_available":false,"comped":true,"cancel_at_period_end":true,"cancel_reason":"comp","pending_change":null,"payment_issue":null,"enforcement":false},"meta":null}
```

Founder-side effects the founder app must handle (these are existing Billing screens, change them in the same release):
- `subscription.comped == true` -> show "Complimentary until {ends_at}", **do not** show "Your plan is set to cancel" or a Resume button, and do not show the usual renewal line.
- While comped, `POST /billing/subscription/cancel`, `/resume` and `/change` return 409 `PLAN_COMPLIMENTARY` (`founder_cancel_while_comped`):
```json
{"error":{"code":"PLAN_COMPLIMENTARY","message":"This plan is complimentary and ends on its own date. Choose a paid plan at checkout to keep it after that.","field_errors":[]}}
```
- Checkout still works while comped (`founder_checkout_while_comped`): paying replaces the comp with the paid plan.

## UX the console must surface (explicit instructions)

1. Put the **reason** and **authenticator code** in one confirm dialog per override; show the effect in plain words first ("This gives PayCo 500 AI credits. They never expire.").
2. Refund success must say no money has moved yet (see Refund).
3. After a success, a second mutation inside the same 30-second code window fails with `STEP_UP_INVALID`: tell the user to wait for the next code.
4. After any override, reload `GET /billing/{workspace_id}`; do not patch the page from the response by hand.
5. Disable the submit button while a request is in flight and keep the same `request_id` for a retry of that press.
6. Comp and credit are generous: show the actor, reason and time from `overrides[]` in the page so a second Super Admin sees what was granted.

## Verification table

| Behaviour | Status |
|---|---|
| Admin/non-staff get hidden 404 | verified live (`overview_non_staff_404`); every route for every role unit-tested |
| Overview shape; unknown workspace 404 | verified live |
| Reason too short 422; wrong code 403 | verified live |
| Refund partial, replay, over-refund 422, full refund flips status, already refunded 409, other workspace's invoice 404 | verified live |
| Founder invoice list shows `refunded_minor` and status after refund; plan unchanged | verified live |
| Receipt PDF "Refunded" line | unit-tested only |
| Credit ok; founder Usage limit rises by the credits | verified live |
| Credit bounds | 100001 verified live (`credit_too_many`); 0 and negatives unit-tested only |
| Extend trial ok; founder trial end moves; too long 422; not trialing 409 | verified live |
| Comp ok; founder subscription shows `comped`; cancel blocked 409; checkout allowed; paying workspace 409; Starter 422 | verified live |
| Comp lapses to Starter/`canceled` at `ends_at` | unit-tested only (needs time to pass) |
| A purchase while comped activates the plan; late Paystack events do not touch a comp | unit-tested only (needs Paystack to deliver webhooks) |
| `REQUEST_ID_REUSED` (different values, workspace, kind, or staff member) | unit-tested; replay itself verified live |
| Audit rows with reason and amounts | verified live (`audit_for_workspace`) |
| Refund actually returned at Paystack | **not built**: internal entry only, `provider_refund` is `pending` |
| Concurrent identical requests | unit-tested only (advisory + row locks; no live concurrency test) |
