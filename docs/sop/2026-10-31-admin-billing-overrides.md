# Admin slice C: Billing overrides (refund, credit, extend trial, comp)

**What shipped:** Super Admin billing overrides under `/api/v1/super-admin/billing/{workspace_id}`: `GET` (overview), `POST refund`, `POST refunds/{override_id}/complete`, `POST credit`, `POST extend-trial`, `POST comp`. Branch `feature/admin-billing-overrides`, migration `0084_billing_overrides`, on develop after slice B (API #206, merged). Spec section 4 slice C, D-ADM-6, P1; PRD 26.6. FE guide `docs/fe-integration-guide-admin-billing.md`.

**Why:** staff needed to fix a billing problem (goodwill refund, a trial that ran out too soon, a gifted plan) without database access, with a reason and an audit row each time. The PRD says Stripe; billing here is Paystack (D-ADM-6), so refunds and credits are internal entries first.

**Calls made** (Adebayo, "use defaults", 2026-10-31; the spec and P1 to P13 did not define them)

| Topic | Decision |
|---|---|
| Credit | AI credits, not a kobo balance: no charge path reads a money balance, so a balance would have no founder-visible effect. A `BillingAddonPurchase` row (`addon_key = admin_credit`, `amount_minor = 0`, reference `admin-credit:<override id>`) is what the Usage read already sums, so the limit rises at once and never expires (D-SET-10). 1 to 100000 per override. |
| Refund (two steps, Adebayo "Accept all four") | **Record**: an internal entry, `provider_refund = pending`; staff then make the refund in the Paystack dashboard (this API never calls Paystack). It adds to `billing_invoices.refund_pending_minor` (staff-only, counts against the cap) and changes **nothing the founder sees**. Partial or full up to what is left (amount minus done minus pending); only a `paid` invoice of that workspace. **Mark done** (`POST .../refunds/{override_id}/complete`, step-up, reason, audit `billing.refund.completed`, idempotent): moves the amount from pending to `refunded_minor`; only when `refunded_minor` equals the amount does the invoice status become `refunded`. The plan is never touched; an add-on refund does not take credits back. |
| Extend trial | 1 to 60 days, trialing subscriptions only, never past 90 days from the trial start. Moves `trial_ends_at` and `current_period_end` together. |
| Comp (also "change plan, no charge") | A paid plan for 1 to 365 days: status `active`, `comped = true`, no Paystack link, `cancel_at_period_end = true` with reason `comp` so `apply_transitions` lapses it to Starter/`canceled` at the end. Refused on a workspace that pays (`PAID_SUBSCRIPTION`). A second comp replaces the first. Excluded from MRR and paid conversion. |

**How**
- Guard: `require_super_admin` (everyone else gets the unknown-route 404), then `verify_step_up` (a fresh single-use code), a reason of 10+ characters, and `write_audit` (`billing.refund.issued`, `billing.refund.completed`, `billing.credit.applied`, `billing.trial.extended`, `billing.comp.granted`, with request id, amounts, reason) in the same transaction as the change.
- Idempotency: the body carries a caller-made `request_id` (unique column). A repeat returns the stored row with `replayed: true`; a repeat with another workspace, kind, actor or value is 409 `REQUEST_ID_REUSED`. Two transaction-level advisory locks (on the request id, then the workspace) run first, then the subscription row lock, then the invoice row lock (the order the renewal path uses). `complete` takes the workspace lock, the subscription row, the refund entry row, then the invoice row (no request lock, so no deadlock with a record). A DB check `refunded_minor + refund_pending_minor <= amount_minor` backs the refund cap.
- Founder-visible effects: `GET /billing/invoices` gains `refunded_minor` (done refunds only), the receipt PDF gains a "Refunded" line once a refund is done, `GET /billing/subscription` gains `comped`. A comped subscription cannot be cancelled, resumed or changed by the founder (409 `PLAN_COMPLIMENTARY`) but can check out (a paid plan replaces it); `finalize_charge`, `_renewal`, `payment_failed` and `subscription.create` ignore a comp's Paystack traffic; credits and cycle metering use calendar months for a comp. Staff reads (user primary workspace and profile, workspace list and detail) carry `comped`.

**What's involved**
- `app/services/staff/billing_overrides.py` (new), `app/api/v1/endpoints/super_admin.py`, `app/schemas/staff.py`.
- `app/db/models/billing.py` (`BillingOverride`, `comped`, `refunded_minor`), `alembic/versions/0084_billing_overrides.py`.
- `app/services/billing/{flows,subscription,usage,webhook,receipt}.py`, `app/api/v1/endpoints/billing.py`, `app/services/staff/{users,workspaces}.py`.
- Tests `tests/api/test_staff_billing.py`; slice B key-set assertions updated for `comped`; live journey `e2e/test_admin_billing.py` (captures `e2e/_captures/admin_billing/`).

**Verification**
- A second Opus pass on the pending/done change: no High or Medium; two Lows are the follow-ups above.
- Opus review of the money path found 1 High (a purchase while comped was refunded as a duplicate), 2 Medium (late Paystack events on a comp; a year-long comp metered as one month), 3 Low (loose replay, a concurrency 500, staff list not showing comped). All fixed, each with a test.
- Unit: `tests/api/test_staff_billing.py` (guards, step-up, refund arithmetic and caps, replay, credit visible in Usage, trial, comp lapse and guards, MRR exclusion, webhook interactions) plus the billing and staff suites; black, ruff, mypy clean; migration upgrade, downgrade, upgrade and `alembic check` clean on a scratch DB.
- Live e2e on a real server with the Paystack stub (passes; does not assume it is the only staff or workspace in the shared e2e database): three founders, four Super Admins rotating authenticators.
- Not verified: a comp reaching its end in real time; a real Paystack webhook after a comp; concurrent requests against a live server; CI.

**Operate / roll back:** no new env. `alembic downgrade 0083_platform_staff` drops the table and both columns (override history is lost). To end a comp early there is no route yet; set the subscription back by hand in SQL.

**Follow-ups:** cancel a pending refund that fails at Paystack (today it holds the cap until fixed in SQL); a four-eyes rule (the staff member who recorded a refund can also mark it done; D-ADM-6 does not say); Paystack refund API call; an early "end comp" route; clawing back credits on an add-on refund; comp expiry notice to the founder; a billing section on the Workspaces detail page.
