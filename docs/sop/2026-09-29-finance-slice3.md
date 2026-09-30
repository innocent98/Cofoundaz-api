# SOP — Module 12 Finance Hub, Slice 3: Invoices (builder totals, INV numbering, draft→sent→paid lifecycle, paid-inflow into Cash Flow + Runway, client email)

**What shipped** — Module 12's third slice (PRD 12.3, Invoices). **Slice 3 of 6.**

1. **`invoices` table** — one row per invoice `(number, client_name, client_email, line_items JSONB,
   subtotal/tax/total minor units, currency, terms, status, issued_on, due_on, paid_at, auto_remind,
   transaction_id)`; new `InvoiceStatus` (`draft`/`sent`/`paid`) and `InvoiceTerms`
   (`net_15`/`net_30`/`due_on_receipt`) enums; `TransactionSource` gains `invoice`.
2. **8 endpoints under `/finance/invoices`** — create (draft), list (`?status=`), get, `PATCH`,
   `send`, `mark-paid`, `mark-unpaid`, `DELETE`. Server-computed totals, `INV-<year>-<NNN>` numbering,
   derived `overdue` status.
3. **Paid → books into the ledger.** `mark-paid` creates an idempotent inflow transaction
   (`source="invoice"`); `mark-unpaid` deletes it. Both refresh the Slice 2 runway alert +
   `money.runway_live` signal, so **Cash Flow and Runway move** — the composition of Slices 1–3.
4. **Invoice-managed transactions are protected** — `PATCH`/`DELETE /finance/transactions/{id}` on a
   `source="invoice"` row is a 422 ("unpay the invoice instead").
5. **`email.invoice_sent` worker job** — `send` queues an email to the client via the project's
   `EmailSender` (Resend in prod, file backend in e2e), with amounts rendered in **major** currency units.

Migration **`0041_finance_invoices`** (one new table), `app/services/finance/invoices.py`,
`app/schemas/invoice.py`, `app/worker/handlers/invoice_email.py`, 8 new routes (**191 operations** in
the OpenAPI schema now, up from 183) behind the same
`require_role(founder, team_member, accountant)` as Slices 1–2.

Commits (branch `feat/module-12-finance-slice3`, off `develop` at `bb9ef1e`; not yet merged, no PR
opened yet), oldest to newest:
`ddc8f38` (design spec) → `108ee58` (implementation plan) →
`99348de` (invoices model + migration `0041` + `InvoiceStatus`/`InvoiceTerms` enums) →
`fd418a9` (create/list/get with server-computed totals + INV numbering) →
`18abebe` (fix: half-up money rounding + overdue / cross-tenant invoice tests) →
`f3b6466` (edit / delete / send lifecycle + email enqueue) →
`9dbf23a` (mark-paid inflow + mark-unpaid reversal, cash-flow integration) →
`da0a73d` (fix: refresh runway alert + health signal on mark-paid/unpaid) →
`2916c2c` (fix: protect invoice-managed transactions from direct edit/delete) →
`50ac413` (`email.invoice_sent` handler) →
`06d2bc1` (fix: render invoice email amounts in major currency units) →
`df88409` (e2e invoices journey + 40 captures) → this docs commit.

## Why

Slice 3 of 6 in the Module 12 plan, PRD 12.3 **Invoices**. Slices 1–2 gave founders a ledger, a
cash-flow read and runway scenarios, but the only way money came *in* was a hand-typed transaction.
Invoices are the natural money-in document: bill a client, send it, and when they pay, the books —
and the runway — should update themselves. The FE Invoices screen is a builder (line items, tax,
terms), a list with Draft / Sent / Paid / Overdue filters, and Send / Mark paid actions; "Download PDF"
is a client-side concern.

The core correctness risk is **money**: a client-editable total is a tampering vector, a double
`mark-paid` would double-count cash, and a paid invoice whose inflow can be freely edited or deleted
would leave the invoice and the ledger disagreeing. Each of those is a designed-in guard below, not a
bolt-on.

## How

**Server-computed totals, half-up rounding** (`invoices.py::_compute_totals`). The create/update
schemas have **no** subtotal/tax/total fields — Pydantic drops any the client sends, so a client total
can never reach storage (proved live: the e2e sends `total_minor: 1` and gets `134375` back).
`amount = quantity × unit_price_minor`; `subtotal = Σ`; `tax = subtotal × tax_percent ÷ 100` using
`Decimal` with `ROUND_HALF_UP` (a float `round()` is banker's rounding, which mis-rounds a half-kobo of
tax); `total = subtotal + tax`. `tax_percent` is quantised to 2 dp *before* computing so the stored rate
is the applied rate (column is `NUMERIC(5,2)`). Subtotal and total are checked against int32 (Postgres
`Integer`) and raise a domain 422 instead of a 500 at flush. `line_items` is a JSONB snapshot; the
response adds `amount_minor` per line.

**`INV-<year>-<NNN>` numbering with SAVEPOINT retry** (`_next_number`, `create_invoice`). Next number =
**max existing suffix + 1** for that workspace and UTC year (not `count + 1`, so it stays monotonic if
a row is ever removed). Uniqueness is enforced by `UniqueConstraint(startup_id, number)`; two
concurrent creates can pick the same number, so the insert runs inside `db.begin_nested()` and an
`IntegrityError` retries with a fresh number (up to 5 times). `db.add()` is deliberately **inside** the
SAVEPOINT — outside it a failed insert would poison the outer session
(`PendingRollbackError` → 500). *Alternative rejected:* a DB sequence per workspace/year — extra
moving parts (a sequence row per tenant-year) for a low-write path.

**draft → sent → paid lifecycle** (`update_invoice`, `send_invoice`, `mark_paid`, `mark_unpaid`).
Stored status is only `draft`/`sent`/`paid`. Only drafts can be edited (all fields, and totals
recompute) or deleted; after `send`, only `auto_remind` remains editable. `send` sets `issued_on` =
today UTC and `due_on` = `issued_on` + 15/30/0 days by terms, flips status to `sent`, and enqueues
`email.invoice_sent` in the same transaction. Illegal moves are `VALIDATION_ERROR` 422 with
`field_errors[0].field == "status"` (via the shared `_validation` helper), never a silent no-op.

**`overdue` is derived, never stored** (`_derived_status`): a stored-`sent` invoice with `due_on <
today` (UTC) *reads* `overdue`. `GET ?status=` filters on the derived value, so `overdue` works and
`sent` excludes overdue. There is no way to set it via the API (`PATCH` has no status field). *Alternative
rejected:* a stored `overdue` state maintained by a daily job — needs the scheduler (deferred) and would
drift the moment the job fails to run.

**Idempotent mark-paid inflow, row lock, exact reversal** (`mark_paid`, `mark_unpaid`,
`_get_invoice_locked`). Both take `SELECT … FOR UPDATE` on the invoice with `populate_existing` (bypass
the identity map) so concurrent calls serialise. `mark_paid`: if the invoice is already `paid` **or**
already has a `transaction_id`, return unchanged (idempotent — no second inflow); if not `sent`, 422;
else create a `Transaction` (amount = `total_minor`, same currency, category `Revenue`, date = **paid
day UTC**, `source=invoice`), flush to get its id, then set `status=paid`, `paid_at`, `transaction_id`.
`mark_unpaid` (paid only): clear `transaction_id`/`paid_at`, status back to `sent`, then delete the
linked transaction; if the linked row is somehow already gone it logs a warning and clears the link
only. `invoices.transaction_id` is `UNIQUE` with `ON DELETE SET NULL` so one ledger row backs at most
one invoice. **Reversal is exact**: the e2e asserts the whole `GET /finance/cash-flow` and `GET
/finance/runway` payloads are identical before payment and after mark-unpaid.

**Runway hook refresh** (`finance.py` endpoints). `mark-paid` / `mark-unpaid` call
`runway_svc.evaluate_runway_alert` and `upsert_runway_signal` before `db.commit()` — the same hooks
Slice 2 put on the transaction routes — because an inflow can lift a startup out of low runway (or a
reversal push it in). Same transaction, so the alert state and the ledger cannot diverge. The hooks
also run on the idempotent no-op path (harmless self-healing; see follow-ups).

**Invoice-managed-transaction guard** (`service.py::_reject_invoice_managed`). `update_transaction`
and `delete_transaction` raise a 422 (`field: "source"`, "This transaction is managed by an invoice;
unpay the invoice to change or remove it.") for `source == invoice` rows. Without it a user could
delete the inflow and leave the invoice claiming `paid` against a ledger that no longer has the money,
or edit its amount out of sync with `total_minor`. `mark_unpaid` is the only sanctioned way to remove
it. *Alternative rejected:* letting `DELETE` cascade to un-pay the invoice — a destructive side effect
hidden behind a transactions call.

**Async Resend send in major units** (`app/worker/handlers/invoice_email.py`). `send` only *enqueues*
`email.invoice_sent {invoice_id}` (job table, same commit); the handler, registered in
`app/worker/__main__.py::register()`, loads the invoice + workspace name and calls
`get_email_sender().send(...)`. The subject is `Invoice <number> from <workspace name>` with CR/LF
stripped (SMTP header-injection guard); every user-controlled string interpolated into the HTML
(`client_name`, line descriptions, workspace name, number, currency) is `html.escape`d. Money is
converted **minor → major** with `Decimal(minor) / 100` formatted `1,343.75 NGN` — the first cut sent
raw minor units (`134375`), fixed in `06d2bc1`. A missing invoice or empty `client_email` is a no-op
(deleted since enqueue). Best-effort: a provider failure fails the job (worker retry semantics), not the
API request. *Alternative rejected:* sending inline in the request — 500 ms+ provider latency on the
hot path and a request that succeeds/fails on someone else's SMTP.

**RBAC** unchanged from Slices 1–2: `founder` / `team_member` / `accountant`; other roles → 403.
Every invoice query is scoped to `membership.startup_id`; a foreign id is 404.

## What's involved

**Migration `0041_finance_invoices`** (`alembic/versions/0041_finance_invoices.py`, chains off
`0040_finance_runway`, single alembic head) — one additive `create_table("invoices")`: FK
`startup_id → startups` `CASCADE`, FK `transaction_id → transactions` `SET NULL`, unique
`(startup_id, number)` (`uq_invoices_startup_number`), unique `transaction_id`, index on `startup_id`;
`terms`/`status` are varchar-backed enums (`native_enum=False`, stored by value). The new
`TransactionSource.invoice` needs **no DDL** — `transactions.source` is varchar(12)-backed. No existing
table touched; downgrade drops the index and the table.

**Models / enums** — `app/db/models/invoice.py` (`Invoice`, registered in `app/db/models/__init__.py`);
`app/db/models/enums.py` (`InvoiceStatus`, `InvoiceTerms`, `TransactionSource.invoice`).

**Schemas** — `app/schemas/invoice.py`: `LineItem`, `InvoiceCreate`, `InvoiceUpdate` (explicit-`null`
`model_validator`), `MoneyLine`, `InvoiceResponse`.

**Services** — `app/services/finance/invoices.py` (`_compute_totals`, `_next_number`,
`_derived_status`, `create_invoice`, `get_invoice`, `_get_invoice_locked`, `update_invoice`,
`delete_invoice`, `send_invoice`, `mark_paid`, `mark_unpaid`, `list_invoices`, `serialize_invoice`);
`app/services/finance/service.py` (`_reject_invoice_managed` on transaction update/delete).

**Worker** — `app/worker/handlers/invoice_email.py` (`handle_invoice_email`, `render_invoice_email`);
registered in `app/worker/__main__.py`.

**Endpoints** (`app/api/v1/endpoints/finance.py`, mounted at `/finance`):

| Route | Purpose |
|---|---|
| `POST /finance/invoices` | create a draft (server totals + number) |
| `GET /finance/invoices?status=` | list; `status` ∈ `draft`/`sent`/`paid`/`overdue` (derived); `created_at` desc |
| `GET /finance/invoices/{id}` | one invoice |
| `PATCH /finance/invoices/{id}` | edit a draft; `auto_remind` in any status |
| `POST /finance/invoices/{id}/send` | draft → sent; sets dates; queues the client email |
| `POST /finance/invoices/{id}/mark-paid` | sent/overdue → paid; idempotent inflow; runway hooks |
| `POST /finance/invoices/{id}/mark-unpaid` | paid → sent; deletes the inflow; runway hooks |
| `DELETE /finance/invoices/{id}` | delete a draft |

**Tests** (93 new unit tests; full suite 1632 → **1725 passed**)
- `tests/api/test_invoices.py` (50) — totals + number, client totals ignored, numbers per workspace,
  list order + status filter, overdue derivation, half-up rounding, number retry on a stale number,
  RBAC 403 / accountant allowed, cross-tenant 404 on every route, validation 422s, edit/delete/send
  lifecycle, mark-paid/unpaid round-trip incl. cash-flow, runway alert + signal refresh, the
  invoice-managed-transaction guard.
- `tests/services/test_invoice_transitions.py` (17) — edit/delete/send state rules and job enqueue.
- `tests/services/test_invoice_paid_cashflow.py` (13) — exactly-one inflow, idempotency, the
  `transaction_id` guard, reversal, fresh single inflow after unpay, row-lock assertion, missing-linked-txn.
- `tests/worker/test_invoice_email.py` (7) — content, HTML escaping, CR/LF subject strip, fallback
  name, missing-invoice/empty-recipient no-ops, handler registration.
- `tests/db/test_invoice_model.py` (5) + `tests/test_invoice_migration.py` (1); one assertion added to
  `tests/db/test_finance_transaction_model.py` (`invoice` source).
- `e2e/test_invoices.py::test_finance_invoices_journey` (new) — see Verification.

**Docs**
- `docs/fe-integration-guide-finance-invoices.md` (new) — every body pasted verbatim from the 40
  captures; server-computed totals, derived `overdue`, paid→cash-flow/runway, invoice-managed
  transactions, minor-vs-major money, async email, FE-only PDF, limitations; verification table.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 12 Slice 3 marked done.

## Verification

**Unit / static.** Full unit suite **1725 passed** (`poetry run pytest`). CI parity through the
project's own toolchain: `poetry run black --check app tests` (467 files unchanged), `isort
--check-only app tests`, `ruff check app tests`, `mypy app` (no issues in 200 source files) — all clean.

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 61 passed**, including the new
`test_finance_invoices_journey` (**no Resend-429 flake this run**; the invoice test uses the file email
backend, not real Resend). The runner applied `0041_finance_invoices` from zero. The journey onboards a
founder and seeds a ₦1,000,000 raise + ₦120,000 hosting (cash 88 000 000; burn 4 000 000; runway 22.0),
then: create a draft (subtotal 125 000 / tax 9 375 at 7.5 % / total 134 375, `INV-<year>-001`, the
request's bogus `total_minor: 1` ignored) → `PATCH` a line (150 000 / 11 250 / 161 250) → second draft
(`-002`) + mark-paid/mark-unpaid on a draft = 422 → `send` (`sent`, `issued_on` today, `due_on` +30) →
drain the worker in-process and read the delivered email from the file mailbox (one message to
`client_email`, subject has the number, body `1,612.50 NGN`, never raw minor) → `PATCH`/`DELETE`/second
`send` on the sent invoice = 422, `auto_remind` still editable → `mark-paid` (`paid`, `transaction_id`
set; a second `mark-paid` returns the same `transaction_id` and `paid_at`) → cash-flow `cash_on_hand`
88 000 000 → **88 161 250** (+ the total, once), `monthly_revenue` 53 750, `monthly_burn` 3 946 250,
`runway_months` 22.0 → 22.3; `GET /finance/runway` baseline moves the same → the inflow appears in
`GET /finance/transactions` with `source: "invoice"`, and `PATCH`/`DELETE` on it = 422 → list filters
(`paid`, `draft`, `overdue` empty, all, bad value 422) → `mark-unpaid` (`sent`, link cleared) →
cash-flow **and** runway payloads identical to the "before" captures, inflow gone → delete the draft (200,
then 404) → 401 / 404 / create-validation 422 shapes → **overdue**: `due_on` backdated directly in the
DB, GET + `?status=overdue` show `overdue`, `?status=sent` empty, `PATCH {"status": …}` ignored, the
overdue invoice marked paid. All numbers reconcile by hand. 40 captures under
`e2e/_captures/invoices/` are the verbatim source of the FE guide. Single alembic head after this
slice: `0041_finance_invoices`; develop still tops out at `0040_finance_runway` (checked against
`origin/develop` before this commit), so no renumbering was needed.

**Honest gap disclosure (unit-only, not e2e-captured).** 403 for non-finance roles and accountant
access; cross-workspace 404 on the invoice routes; half-up rounding; the SAVEPOINT number-retry; the
int32 overflow 422 and explicit-`null` PATCH 422; `due_on` for `net_15`/`due_on_receipt` on send; runway
alert/signal refresh on paid/unpaid (the live run never crossed the low-runway threshold); the row
lock; email HTML-escaping and CR/LF stripping. **Delivery through real Resend was not exercised** (the
file backend accepts any recipient). The overdue proof relies on a direct DB backdate of `due_on`
because a live run cannot advance time.

## Operate / roll back

**Deploy-time requirements.** Run `alembic upgrade head` to apply `0041_finance_invoices`. The invoice
**email is an async worker job** (`email.invoice_sent`): it is only sent while the worker process
(`python -m app.worker`) is running, and needs the same email config as every other transactional email
(`EMAIL_BACKEND=resend`, `RESEND_API_KEY`, a Resend-verified `EMAILS_FROM_EMAIL`). Existing workspaces
are unaffected (no backfill; invoices start empty).

**Rollback:** revert this slice's commits as a unit (`99348de..df88409`, plus this docs commit) and
downgrade (`poetry run alembic downgrade 0040_finance_runway`), which drops the `invoices` table
(all invoices are lost). **Order matters and there is one data hazard:** `transactions` rows with
`source = 'invoice'` created by `mark-paid` survive the downgrade, and code from *before* this slice
does not know that enum value, so reading those rows would error. Before rolling the code back,
either mark every paid invoice unpaid (removes its inflow) or `DELETE FROM transactions WHERE source =
'invoice'` (this reduces cash-on-hand by those invoices' totals — a deliberate ledger change, take a
backup first). Downgrade the migration only *after* the app code is rolled back, same ordering as every
slice. Queued-but-unsent `email.invoice_sent` jobs for a removed handler will be marked failed by the
worker; they are harmless.

## Follow-ups / known non-blocking gaps

- **Reminder / overdue scheduler deferred.** `auto_remind` is stored and returned but nothing sends
  reminders; `overdue` is only *derived on read*. Needs a Module 20 scheduler job (and an email
  category) to send due/overdue reminders and an `invoice.overdue` notification.
- **Server-side PDF deferred.** "Download PDF" is FE-only; no `GET …/pdf` endpoint.
- **Client / CRM entity deferred.** `client_name` / `client_email` are free text per invoice — no
  reusable client record, no per-client history.
- **Mixed currency is not converted.** Inherited single-currency assumption (Slices 1–2): a
  foreign-currency invoice's inflow adds its raw minor units into `cash_on_hand`, burn, revenue and
  runway. Needs a reporting currency + FX source (same follow-up as Slice 1).
- **`currency` is not ISO-4217-validated** — only `1..3` chars. Validate against an ISO list (or an
  enum of supported currencies) at the schema boundary.
- **`send` / `PATCH` / `DELETE` use an unlocked fetch** (`get_invoice`, no `FOR UPDATE`): a double-tap
  Send can queue **two `email.invoice_sent` jobs** (the invoice only flips once, but the client gets two
  emails), and two concurrent PATCHes can lose an update. `mark-paid` / `mark-unpaid` **do** lock.
  Fix: use `_get_invoice_locked` in `send_invoice` (and `update_invoice`), or dedupe the job by
  `invoice_id`. The FE guide tells the FE to disable Send while in flight.
- **The no-op `mark-paid` path re-runs the runway hooks.** A repeated `mark-paid` returns early from the
  service but the endpoint still calls `evaluate_runway_alert` + `upsert_runway_signal` — harmless
  self-healing (they recompute from the ledger) but two extra `cash_flow_summary` computations per retry.
- **No `invoice.*` domain events / notifications.** Nothing publishes `invoice.sent` /
  `invoice.paid` / `invoice.overdue` to the notification feed or the Health Score this slice.
- **Invoice number can be reused after deleting the newest draft** (max-suffix + 1); numbers of
  sent/paid invoices are never reused. Not a correctness issue for drafts; noted because `number` is
  not a permanent external key.
- **List is unpaginated** (full set, `created_at` desc), same as the Slice 1 transactions list.
- **Live e2e did not exercise real Resend delivery**, RBAC 403, or the low-runway transition through an
  invoice payment (all unit-verified) — see the honest-gap disclosure above.
- **Slice 4 — expenses / budgets. Slice 5 — financial model. Slice 6 — bank / accounting / Stripe
  integrations** (which will reuse the reserved `source` values alongside `invoice`).
