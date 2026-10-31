# FE integration guide: Admin slice C (Billing overrides)

Every body below is pasted from a live run against a real server: `e2e/test_admin_billing.py`, captures in `e2e/_captures/admin_billing/<name>.json` (the placeholder in brackets after each heading is the capture name). Emails and IDs are throwaway values from that run (placeholders). Base path `/api/v1/super-admin`. Success: `{"data": ..., "meta": ...}`; errors: `{"error": {"code","message","field_errors"}}`, **except the hidden 404 `{"detail":"Not Found"}`**. Auth, step-up and the audit explorer are in `fe-integration-guide-admin-staff.md`. Decisions this follows: D-ADM-6 (Paystack, not Stripe; refunds and credits are internal entries), P1 (a fresh code on every mutation), and the slice C calls below.

## What this is

A **Billing overrides** page in the Super Admin console (PRD 26.6): find a workspace (use the Workspaces search from `fe-integration-guide-admin-users.md`, then open its billing), see its subscription, invoices and past overrides, and apply one of four overrides. **Super Admin only**: an Admin (any scope) gets the hidden 404 on every route here (`overview_non_staff_404` shows the shape).

| Override | Route | What it does to the founder |
|---|---|---|
| Refund | `POST /billing/{workspace_id}/refund`, then `POST /billing/{workspace_id}/refunds/{override_id}/complete` | Two steps. Recording a refund changes **nothing** the founder can see. Only after staff make the refund in Paystack and mark it done does the founder's invoice show `refunded_minor`, and a fully refunded invoice turns to `refunded`. The plan is never touched. |
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
- Every override writes an audit row (`billing.refund.issued`, `billing.refund.completed`, `billing.credit.applied`, `billing.trial.extended`, `billing.comp.granted`) with the reason and amounts, visible in the audit explorer (`audit_for_workspace`).
- Unknown workspace: 404 `WORKSPACE_NOT_FOUND` (`overview_unknown_workspace`).

## `GET /billing/{workspace_id}` [overview]

```json
{"data":{"workspace":{"id":"b39847c5-ced1-49de-b6d7-27e7e4ce3869","name":"PayCo"},"subscription":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"active","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T15:24:44+00:00","renews_at":"2026-11-30T15:24:44+00:00","ends_at":null,"trial_ends_at":null,"trial_available":false,"comped":false,"cancel_at_period_end":false,"cancel_reason":null,"pending_change":null,"payment_issue":null,"enforcement":false},"invoices":[{"id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":0,"refund_pending_minor":0,"refundable_minor":2400000,"currency":"NGN","status":"paid","paid_at":"2026-10-31T15:24:44+00:00"}],"credits_granted_total":0,"overrides":[]},"meta":null}
```

Nesting traps:
- **`subscription` is `null`** (present, not absent) for a workspace that never opened Billing; treat it as Starter and free.
- `subscription` is the same object a founder gets from `GET /billing/subscription`, including `comped`.
- On `invoices[]` the field is **`paid_at`**; the founder's own invoice list uses **`date`** instead (see below). `refundable_minor` is what is left to refund (0 unless the invoice is `paid`).
- `invoices[]` also has `refund_pending_minor` (recorded, not yet done). `overrides[]` is newest first, up to 50, and each has `provider_refund` (`pending`, `done` or null) and `completed_at`; `credits_granted_total` is the sum of all credit overrides for the workspace.

## Refund: record it, then mark it done

A refund is two separate actions. **Recording** it (this section) is an internal entry; **marking it done** (next section) is what the founder finally sees. Nothing is sent to Paystack by this API: staff make the refund in the Paystack dashboard themselves.

### Record: `POST /billing/{workspace_id}/refund` [refund_partial_ok]

Body: `{"request_id", "invoice_id", "amount_minor", "reason", "totp_code"}`. Pick the invoice from `invoices[]` where `refundable_minor > 0`; default the amount to `refundable_minor` and cap it there. Partial refunds are allowed.

```json
{"data":{"replayed":false,"override":{"id":"4d4fab41-95d6-4576-8394-94815be06b07","request_id":"18eb148a-5c74-423c-84f7-6a559b32de04","kind":"refund","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-e2803713042a@resend.dev","invoice_id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","amount_minor":1000000,"credits":null,"days":null,"plan_key":null,"provider_refund":"pending","completed_at":null,"details":{"invoice_number":"CFZ-2026-0001"},"created_at":"2026-10-31T15:24:44.532141+00:00"},"invoice":{"id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":0,"refund_pending_minor":1000000,"refundable_minor":1400000,"currency":"NGN","status":"paid","paid_at":"2026-10-31T15:24:44+00:00"}},"meta":null}
```

- **Pending, and the founder sees nothing.** `provider_refund` is `"pending"`; the invoice's `status` stays `paid` and its `refunded_minor` stays `0` (`founder_invoices_refund_pending`). Pending amounts are `refund_pending_minor` (staff-only) and **count against the limit**: `refundable_minor = amount - refunded_minor - refund_pending_minor`.
- The page must say next to the button and in the success toast: "Recorded as pending. No money has been sent. Make the refund in the Paystack dashboard, then mark it done here."
- Show every refund override with `provider_refund == "pending"` with a **Mark done** button (next section), in the overview's `overrides[]`.
- A refund never changes the founder's plan. Refunding an add-on or credit-pack invoice does not take the credits back.

Recording the rest of the invoice (`refund_full_recorded`) leaves `status` `paid`, with `refund_pending_minor` equal to the whole amount:
```json
{"data":{"replayed":false,"override":{"id":"147812c2-594b-4659-9c5f-080de2ee2ed4","request_id":"d40fd7c8-90b4-43bf-aa3d-87cfe60eccce","kind":"refund","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-e497acf2eaed@resend.dev","invoice_id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","amount_minor":1400000,"credits":null,"days":null,"plan_key":null,"provider_refund":"pending","completed_at":null,"details":{"invoice_number":"CFZ-2026-0001"},"created_at":"2026-10-31T15:24:44.651701+00:00"},"invoice":{"id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":0,"refund_pending_minor":2400000,"refundable_minor":0,"currency":"NGN","status":"paid","paid_at":"2026-10-31T15:24:44+00:00"}},"meta":null}
```
Replay (`refund_replay`) is the same body with `"replayed": true`. Errors:

`refund_over_invoice` (422; the message and `field_errors` carry what is left, pending refunds included):
```json
{"error":{"code":"REFUND_EXCEEDS_INVOICE","message":"That is more than is left to refund on this invoice (1400000 kobo).","field_errors":[{"field":"amount_minor","message":"at most 1400000"}]}}
```
`refund_nothing_left` (422, same code, when everything is already pending or done):
```json
{"error":{"code":"REFUND_EXCEEDS_INVOICE","message":"That is more than is left to refund on this invoice (0 kobo).","field_errors":[{"field":"amount_minor","message":"at most 0"}]}}
```
`refund_already_refunded` (409 once the invoice is `refunded`; the same code is returned for a failed invoice):
```json
{"error":{"code":"INVOICE_NOT_REFUNDABLE","message":"Only a paid invoice can be refunded; this one is refunded.","field_errors":[]}}
```
`refund_invoice_of_another_workspace` (404 `INVOICE_NOT_FOUND`):
```json
{"error":{"code":"INVOICE_NOT_FOUND","message":"No invoice with that id in this workspace.","field_errors":[]}}
```

What the founder sees while it is pending (`founder_invoices_refund_pending`, the existing `GET /billing/invoices`; the new field is `refunded_minor`, and this list uses **`date`**, not the console's `paid_at`):
```json
{"data":[{"id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":0,"currency":"NGN","status":"paid","date":"2026-10-31T15:24:44+00:00"}],"meta":null}
```

### Mark done: `POST /billing/{workspace_id}/refunds/{override_id}/complete` [refund_complete_partial]

`override_id` is the `override.id` of a refund entry. Body: `{"reason", "totp_code"}` only (no `request_id`: completing twice is harmless). Needs a reason and a fresh authenticator code like every override, and writes the audit action `billing.refund.completed`.

```json
{"data":{"replayed":false,"override":{"id":"4d4fab41-95d6-4576-8394-94815be06b07","request_id":"18eb148a-5c74-423c-84f7-6a559b32de04","kind":"refund","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-e497acf2eaed@resend.dev","invoice_id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","amount_minor":1000000,"credits":null,"days":null,"plan_key":null,"provider_refund":"done","completed_at":"2026-10-31T15:24:44.835512+00:00","details":{"invoice_number":"CFZ-2026-0001"},"created_at":"2026-10-31T15:24:44.532141+00:00"},"invoice":{"id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":1000000,"refund_pending_minor":1400000,"refundable_minor":0,"currency":"NGN","status":"paid","paid_at":"2026-10-31T15:24:44+00:00"}},"meta":null}
```

- Now, and only now, the founder's invoice moves: `refunded_minor` rises by the amount (`founder_invoices_partial_done` shows a partial one with `status` still `paid`), and once `refunded_minor` equals the invoice amount the status becomes `refunded` (`refund_complete_full`). The receipt PDF gains its "Refunded" line at the same time.
- Marking done again (a double submit, or a second staff member) is not an error: `"replayed": true`, nothing changes, no second audit row (`refund_complete_replay`). Note a double click with the same authenticator code fails at the code check with `STEP_UP_INVALID` first; reload and read `provider_refund` before retrying.
- An id that is not a refund of this workspace (another workspace's, a credit's, unknown) is 404 `REFUND_NOT_FOUND` (`refund_complete_other_workspace`):
```json
{"error":{"code":"REFUND_NOT_FOUND","message":"No refund entry with that id in this workspace.","field_errors":[]}}
```
- Confirm copy: "Confirm the refund of {amount} for {invoice number} was made in Paystack. The founder will see the invoice as refunded."
- There is **no way yet to cancel** a pending refund that fails at Paystack (follow-up); tell staff to record only refunds they are about to make.

What the founder sees afterwards (`founder_invoices_after`):
```json
{"data":[{"id":"045a32b5-689f-4c5f-b0b6-13ee24ff1c44","number":"CFZ-2026-0001","kind":"subscription","description":"Growth plan, monthly","amount_minor":2400000,"refunded_minor":2400000,"currency":"NGN","status":"refunded","date":"2026-10-31T15:24:44+00:00"}],"meta":null}
```
The founder's subscription is unchanged (`founder_subscription_after_refund`).

## Credit [credit_ok]

Body: `{"request_id", "credits" (1 to 100000), "reason", "totp_code"}`.

```json
{"data":{"replayed":false,"override":{"id":"4c7afbf6-5300-4bee-9f75-ae11302c3fb6","request_id":"5c995e53-5789-4e41-9308-d040e0140434","kind":"credit","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-e497acf2eaed@resend.dev","invoice_id":null,"amount_minor":null,"credits":500,"days":null,"plan_key":null,"provider_refund":null,"completed_at":null,"details":{},"created_at":"2026-10-31T15:25:01.386557+00:00"}},"meta":null}
```

The founder's usage read changes at once (`founder_usage_after_credit`: `limit` went from 5000 to 5500, `from_packs` is 500). No money is involved.

```json
{"data":{"cycle":{"starts_on":"2026-10-31","resets_on":"2026-11-30"},"ai_credits":{"used":0,"limit":5500,"percent":0,"from_packs":500,"tokens_per_credit":50},"seats":{"used":1,"limit":5},"storage":{"used_mb":0.0,"limit_mb":20480},"daily":[],"breakdown":[]},"meta":null}
```

## Extend trial [extend_trial_ok]

Body: `{"request_id", "days" (1 to 60), "reason", "totp_code"}`. Only for `subscription.status == "trialing"`; the button should be disabled otherwise.

```json
{"data":{"replayed":false,"override":{"id":"b9010f20-8430-4540-b520-24e11a5f0459","request_id":"7cbd6200-f78f-4529-ba30-704bc7726a9a","kind":"trial","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-6207fd03e022@resend.dev","invoice_id":null,"amount_minor":null,"credits":null,"days":7,"plan_key":null,"provider_refund":null,"completed_at":null,"details":{"trial_ends_at_after":"2026-11-21T15:25:30.726982+00:00","trial_ends_at_before":"2026-11-14T15:25:30.726982+00:00"},"created_at":"2026-10-31T15:25:30.763481+00:00"},"subscription":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"trialing","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T15:25:30.726982+00:00","renews_at":null,"ends_at":"2026-11-21T15:25:30.726982+00:00","trial_ends_at":"2026-11-21T15:25:30.726982+00:00","trial_available":false,"comped":false,"cancel_at_period_end":false,"cancel_reason":null,"pending_change":null,"payment_issue":null,"enforcement":false}},"meta":null}
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
{"data":{"replayed":false,"override":{"id":"1e2e4b0f-1f24-4431-9607-430c57514d08","request_id":"b5fed3a0-7d4e-4bdd-ba39-2ad5138b38e7","kind":"comp","reason":"Goodwill after a failed renewal","actor_email":"delivered+e2e-e497acf2eaed@resend.dev","invoice_id":null,"amount_minor":null,"credits":null,"days":30,"plan_key":"growth","provider_refund":null,"completed_at":null,"details":{"before":{"comped":false,"status":"free","plan_key":"starter"},"ends_at":"2026-11-30T15:26:00.302853+00:00"},"created_at":"2026-10-31T15:26:00.273332+00:00"},"subscription":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"active","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T15:26:00.302853+00:00","renews_at":null,"ends_at":"2026-11-30T15:26:00.302853+00:00","trial_ends_at":null,"trial_available":false,"comped":true,"cancel_at_period_end":true,"cancel_reason":"comp","pending_change":null,"payment_issue":null,"enforcement":false}},"meta":null}
```

- The founder gets the plan immediately: status `active`, **no card, no charge**, and it **ends by itself** at `ends_at`, after which the workspace is on Starter with status `canceled`.
- A comped workspace is not revenue: it is excluded from MRR and paid conversion on the dashboard. The staff Users, Workspaces list and detail now carry a `comped` flag; show a "Comped" tag.
- Not allowed on a workspace that pays (status `active` or `past_due` and not comped): 409 `PAID_SUBSCRIPTION` (`comp_paying_workspace`). A workspace on a trial can be comped (the trial clock stops). A second comp on a comped workspace replaces it.
- Starter: 422 `PLAN_NOT_COMPABLE` (`comp_starter`). Unknown plan: 404 `PLAN_NOT_FOUND`.

`comp_paying_workspace`:
```json
{"error":{"code":"PAID_SUBSCRIPTION","message":"This workspace pays for a plan. Cancel it first; a comp would stop the charges from matching what they get.","field_errors":[]}}
```

What the founder sees (`founder_subscription_comped`; **note `cancel_at_period_end: true` and `cancel_reason: "comp"`: they are how a comp ends, not a cancellation by the founder; `renews_at` is null**):
```json
{"data":{"plan":{"key":"growth","name":"Growth"},"interval":"monthly","status":"active","price_minor":2400000,"currency":"NGN","current_period_start":"2026-10-31T15:26:00.302853+00:00","renews_at":null,"ends_at":"2026-11-30T15:26:00.302853+00:00","trial_ends_at":null,"trial_available":false,"comped":true,"cancel_at_period_end":true,"cancel_reason":"comp","pending_change":null,"payment_issue":null,"enforcement":false},"meta":null}
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
2. Recording a refund must say no money has moved and the founder sees nothing yet; marking it done is a separate, deliberate click (see Refund).
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
| Refund recorded (partial, replay, over-refund 422, nothing left 422, other workspace's invoice 404); founder invoice unchanged while pending | verified live |
| Mark done: partial then full, founder invoice shows `refunded_minor` and then status `refunded`, replay, other workspace 404, plan unchanged, already refunded 409 | verified live |
| Pending refunds count against the cap (create vs complete under locks) | unit-tested; no live concurrency test |
| Receipt PDF "Refunded" line | unit-tested only |
| Cancel a pending refund | **not built** |
| Credit ok; founder Usage limit rises by the credits | verified live |
| Credit bounds (0 and 100001) | live for 100001 not run; unit-tested |
| Extend trial ok; founder trial end moves; too long 422; not trialing 409 | verified live |
| Comp ok; founder subscription shows `comped`; cancel blocked 409; checkout allowed; paying workspace 409; Starter 422 | verified live |
| Comp lapses to Starter/`canceled` at `ends_at` | unit-tested only (needs time to pass) |
| A purchase while comped activates the plan; late Paystack events do not touch a comp | unit-tested only (needs Paystack to deliver webhooks) |
| `REQUEST_ID_REUSED` (different values, workspace, kind, or staff member) | unit-tested; replay itself verified live |
| Audit rows with reason and amounts | verified live (`audit_for_workspace`) |
| Refund actually returned at Paystack | **not built**: staff make it in the Paystack dashboard and mark it done here |
| Concurrent identical requests | unit-tested only (advisory + row locks; no live concurrency test) |
