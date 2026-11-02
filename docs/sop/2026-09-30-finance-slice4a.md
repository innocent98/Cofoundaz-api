# SOP — Module 12 Finance Hub, Slice 4a: Expenses (expense→outflow 1:1 sync, generalized managed-transaction guard, receipts via storage, category summary)

**What shipped** — Module 12's fourth build slice, first half (PRD 12.4, Expenses; Budgets are the second
half, Slice 4b). **Slice 4a of the 4a/4b/5/6 remaining plan.**

1. **`expenses` table** — one row per expense `(vendor, category, expense_date, amount_minor, currency,
   recurring, notes, receipt_url, receipt_key, transaction_id, created_by)`; `TransactionSource` gains
   `expense`.
2. **8 endpoints under `/finance/expenses`** — create, list (`?category=&month=&date_from=&date_to=&recurring=`),
   get, `summary?month=`, `PATCH`, `DELETE`, `POST {id}/receipt`, `DELETE {id}/receipt`.
3. **Every expense posts an outflow** into the Slice 1 ledger (`source="expense"`), 1:1 and linked by
   `transaction_id`. Create / edit / delete keep that outflow in sync and refresh the Slice 2 runway alert +
   `money.runway_live` signal — so **Cash Flow and Runway move** when an expense is logged (the composition
   of Slices 1–4, same shape as Slice 3's paid-invoice inflow).
4. **Generalized managed-transaction guard** — `PATCH`/`DELETE /finance/transactions/{id}` on a
   `source="expense"` row is a 422 ("edit or delete the expense instead"), alongside the existing
   `source="invoice"` guard.
5. **Receipts** — optional PNG / JPEG / PDF (≤ 10 MB) per expense stored through the project's storage
   backend (`STORAGE_BACKEND` local / Cloudinary); replace deletes the old file; deleting the expense
   deletes the file.
6. **Category summary** — per-category totals + 1-dp percents for a month, for the Expenses breakdown chart.

Migration **`0043_finance_expenses`** (one new table), `app/services/finance/expenses.py`,
`app/schemas/expense.py`, `app/db/models/expense.py`, 8 new routes behind the same
`require_role(founder, team_member, accountant)` as Slices 1–3.

Commits (branch `feat/module-12-finance-slice4a`, off `develop` at `5b78cb0`; not yet merged, no PR opened
yet), oldest to newest:
`8e666bc` (design spec) → `aa7b165` (implementation plan) →
`d2133e2` (expenses model + migration `0043` + `TransactionSource.expense`) →
`a442763` (create/list/get/summary with outflow into cash-flow) →
`159ef26` (test: guard expense-create runway hooks + summary bad-month 422) →
`bfaa66b` (edit/delete sync the outflow + generalized `_reject_managed` guard) →
`42905c6` (docs: Slice-3 SOP guard reference after the `_reject_managed` rename) →
`b5c84b6` (receipt upload/replace/delete via storage backend) →
`c9552a1` (e2e expenses journey + 39 captures) → this docs commit.

## Why

Slice 4 of the Module 12 plan, PRD 12.4. Slices 1–3 gave founders a ledger, cash-flow, runway scenarios and
invoices (money in), but the only way money went *out* was a hand-typed transaction with no vendor, receipt
or category breakdown. Expenses are the natural money-out document: log a spend against a vendor and a
category, attach the receipt, and the books — and the runway — update themselves. The FE Expenses screen is
an add-expense form, a filterable list, a receipt attachment and a category-breakdown chart; Budgets (the
per-category monthly limit next to that breakdown) are Slice 4b.

The core correctness risks are **money** and **ledger integrity**: an expense whose ledger outflow drifts from
its own amount would make cash-on-hand lie; a user-editable outflow that the expense still "owns" would let
the two disagree; and a deleted expense that leaves its outflow behind would keep burning cash that was
never spent. Each is a designed-in guard below.

## How

**1:1 expense ↔ outflow, edit-sync, delete-reverse** (`expenses.py::_post_outflow`, `create_expense`,
`update_expense`, `delete_expense`). `create_expense` inserts the `Expense`, flushes for its id, inserts a
`Transaction` (`direction=outflow`, `source=expense`, same amount / date / category / currency,
description `Expense: <vendor>`), flushes, and stores `exp.transaction_id`. `update_expense` applies the
partial update and, when any of `amount_minor`/`expense_date`/`category`/`vendor`/`currency` changed
(`_LEDGER_FIELDS`), mirrors them onto the **same** `Transaction` row in place, so `transaction_id` stays
stable; `notes`/`recurring` edits do not touch the ledger. `delete_expense` deletes the linked transaction,
removes the receipt file, then the expense. A missing linked transaction (e.g. `SET NULL`) is tolerated, not
a 500. `expenses.transaction_id` is `UNIQUE` with `ON DELETE SET NULL` so one ledger row backs at most one
expense. *Alternative rejected:* deriving cash-flow from `expenses` directly (a second source of truth for
`cashflow.py`, which is built on `transactions`) — the 1:1 outflow keeps Cash Flow, Runway, the health signal
and the transactions ledger all reading one table.

**Runway hook refresh** (`finance.py` create / patch / delete endpoints). Each calls
`runway_svc.evaluate_runway_alert` and `upsert_runway_signal` before `db.commit()` — the same hooks Slices 2–3
put on the transaction and invoice routes — in the same transaction as the ledger write, so the alert state
and the ledger cannot diverge. Receipt endpoints do not (a receipt never changes the ledger).

**Generalized managed-transaction guard** (`service.py::_reject_managed`, renamed from
`_reject_invoice_managed`). One helper now raises a 422 (`field: "source"`) for both `source == invoice` and
`source == expense` rows, called from `update_transaction` and `delete_transaction`. Without it a user could
delete the outflow and leave the expense claiming a ledger entry that no longer exists, or edit its amount out
of sync with `amount_minor`. The expense endpoints are the only sanctioned path to change or remove the row.
*Alternative rejected:* letting `DELETE /transactions/{id}` cascade to delete the expense — a destructive side
effect hidden behind a transactions call.

**Receipts via the storage backend** (`expenses.py::attach_receipt`, `remove_receipt`;
`app/platform/storage.py`). Allowed types are `image/png`, `image/jpeg`, `application/pdf`
(`_RECEIPT_EXT`); size cap 10 MB (`MAX_RECEIPT_BYTES`); both are domain 422s on field `receipt`. The endpoint
reads at most `MAX_RECEIPT_BYTES + 1` bytes so an oversized upload is rejected without buffering it all. Key is
`receipts/<startup_id>/<uuid4hex><ext>`. **Replace saves the new asset first, then deletes the old key**, so a
failed upload never loses the existing receipt. `receipt_url` (public URL) and `receipt_key` (storage key)
are stored separately; only `receipt_url`/`has_receipt` are in the API. Delete-expense removes the file too.
Reuses the project's `get_storage()` seam (Local in dev/e2e, Cloudinary in staging/prod) — the same one
Documents uses. *Alternative rejected:* storing the file bytes in Postgres — bloats the DB and the backups.

**Category summary** (`expenses.py::category_summary`). One `GROUP BY category` over the month's expenses
(`_month_range` → inclusive first/last day, malformed `YYYY-MM` → domain 422). Rows are sorted by total
descending, then category ascending; `percent = round(100 × total ÷ grand_total, 1)`; an all-zero month
returns no rows (no divide-by-zero); `currency` is the most common currency in the month, `"NGN"` when empty.
The summary route is declared **before** `/expenses/{expense_id}` so `summary` is never parsed as an id.

**Int32 + null hardening.** `amount_minor` is `0 … 2,147,483,647` at the schema (matches the `Integer`
column — over-range is a 422, not a flush-time 500). `ExpenseUpdate` rejects an explicit `null` on every
non-nullable field via a `model_validator` (would otherwise be a NOT NULL violation → 500); only `notes` may be
nulled.

**RBAC** unchanged from Slices 1–3: `founder` / `team_member` / `accountant`; other roles → 403. Every expense
query is scoped to `membership.startup_id`; a foreign id is 404.

## What's involved

**Migration `0043_finance_expenses`** (`alembic/versions/0043_finance_expenses.py`, chains off
`0042_finance_invoices`, single alembic head) — one additive `create_table("expenses")`: FK `startup_id →
startups` `CASCADE`, FK `created_by → users` `SET NULL`, FK `transaction_id → transactions` `SET NULL` +
unique, index on `startup_id`, composite index `ix_expenses_startup_date (startup_id, expense_date)`. The new
`TransactionSource.expense` needs **no DDL** — `transactions.source` is varchar-backed. No existing table
touched; downgrade drops the indexes and the table. Confirmed against `origin/develop` on 2026-09-30: develop
still tops out at `0042_finance_invoices`, so no renumber was needed.

**Models / enums** — `app/db/models/expense.py` (`Expense`, registered in `app/db/models/__init__.py`);
`app/db/models/enums.py` (`TransactionSource.expense`).

**Schemas** — `app/schemas/expense.py`: `ExpenseCreate`, `ExpenseUpdate` (explicit-`null` validator),
`ExpenseResponse`, `CategorySummaryRow`, `CategorySummary`.

**Services** — `app/services/finance/expenses.py` (`_month_range`, `_post_outflow`, `create_expense`,
`get_expense`, `update_expense`, `delete_expense`, `attach_receipt`, `remove_receipt`, `list_expenses`,
`serialize_expense`, `category_summary`); `app/services/finance/service.py` (`_reject_managed` on transaction
update/delete).

**Endpoints** (`app/api/v1/endpoints/finance.py`, mounted at `/finance`):

| Route | Purpose |
|---|---|
| `POST /finance/expenses` | create; posts the linked outflow; runway hooks |
| `GET /finance/expenses?category=&month=&date_from=&date_to=&recurring=` | list; `expense_date` desc, `created_at` desc; unpaginated |
| `GET /finance/expenses/summary?month=YYYY-MM` | per-category totals + percents (defaults to the current month) |
| `GET /finance/expenses/{id}` | one expense |
| `PATCH /finance/expenses/{id}` | partial edit; syncs the outflow; runway hooks |
| `DELETE /finance/expenses/{id}` | delete; reverses the outflow, removes the receipt file; runway hooks |
| `POST /finance/expenses/{id}/receipt` | multipart `file`; PNG/JPEG/PDF ≤ 10 MB; replace deletes old |
| `DELETE /finance/expenses/{id}/receipt` | remove the receipt (no-op 200 if none) |

**Tests** (58 new test functions; full suite **1942 passed**)
- `tests/api/test_expenses.py` (27) — create + linked outflow, `created_by`, list order + filters, bad month
  422, get / cross-tenant 404, RBAC 403 + accountant allowed, auth required, create validation 422s, summary
  totals/percents/empty/all-zero/default-month/route-not-shadowed/tenant-scoped, create refreshes runway alert +
  signal, PATCH syncs the transaction + cash-flow, partial + null-notes, explicit-null 422, out-of-range 422,
  DELETE reverses cash, cross-tenant PATCH/DELETE 404, RBAC on PATCH/DELETE, **the expense-managed
  transaction guard**, manual transactions still editable, PATCH/DELETE refresh runway.
- `tests/api/test_expense_receipts.py` (12) — PNG/PDF upload stores the file, disallowed type and oversize 422
  with no receipt set, replace deletes the old file, delete receipt clears + removes the file, no-op delete,
  deleting the expense removes the file, RBAC, cross-tenant 404, auth required.
- `tests/services/test_expense_cashflow.py` (14) — exactly one linked outflow, cash-on-hand and by-month series,
  serialize `has_receipt`, all-zero summary guard, in-place sync of the same transaction, non-ledger edits leave
  the transaction alone, missing linked transaction tolerated, delete reverses, tenant scoping, and
  **`autoflush=False` variants** for create and for update/delete (prod `SessionLocal` is autoflush-off while
  the unit `db` fixture is autoflush-on, so add-then-select-in-one-transaction bugs would otherwise hide).
- `tests/db/test_expense_model.py` (4) + `tests/test_expense_migration.py` (1); one assertion added to
  `tests/db/test_finance_transaction_model.py` (`expense` source).
- `e2e/test_expenses.py::test_finance_expenses_journey` (new) — see Verification.

**Docs**
- `docs/fe-integration-guide-finance-expenses.md` (new) — every body pasted verbatim from the 39 captures via a
  substitution script; expense→outflow→cash-flow/runway table, document-once rule, managed-transaction guard,
  receipts (types/size/replace/cascade, local-path vs Cloudinary URL), summary shape + rounding, limitations;
  verification table (verified-live vs unit-only vs source-only).
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 12 Slice 4a marked done; 4b / 5 / 6 left open.
- `docs/sop/2026-09-29-finance-slice3.md` — guard reference updated for the `_reject_managed` rename
  (`42905c6`).

## Verification

**Unit / static.** Full unit suite **1942 passed** (`poetry run pytest`). CI parity through the project's own
toolchain, every check the `lint` job runs: `poetry run black --check app tests` (492 files unchanged),
`isort --check-only app tests` (clean), `ruff check app tests` (all checks passed), `mypy app` (no issues in
210 source files).

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 63 passed**, including the new
`test_finance_expenses_journey` (**no Resend-429 flake this run**). The runner applied `0043_finance_expenses`
from zero on an isolated DB, with `STORAGE_BACKEND=local`. The journey seeds a ₦1,000,000 raise four months
back (cash 100 000 000, burn 0), then: creates four expenses in four categories (hosting 12M, payroll 20M,
marketing 8M today; Notion 4M last month, `recurring: true`) each returning a distinct linked
`transaction_id` → list, filter by `category` / `month` / `recurring`, get one → **summary** for this month
(payroll 20M **50.0%** / infrastructure 12M **30.0%** / marketing 8M **20.0%**, total 40M), last month
(software 100.0%), and an empty month (`rows: []`) → **cash-flow** `cash_on_hand` 100M → **56 000 000**,
`monthly_burn` 0 → **14 666 667**, `runway_months` null → **3.8**, `runway_low` false → **true**; `GET /runway`
baseline moves the same → the four outflows are in `GET /finance/transactions?direction=out` with `source:
"expense"`, and `PATCH`/`DELETE` on one = **422** → **receipts**: upload PNG (`has_receipt: true`), replace
with PDF (new `receipt_url`, **old file gone from disk**), `.exe` = 422 with the receipt intact, upload +
`DELETE /receipt` on another (file removed) → **PATCH** hosting 12M → 10M (same `transaction_id`, receipt
survives; cash **58 000 000**, burn 14 000 000, runway 4.1; ledger row follows the amount, then a
vendor+category edit follows too) → **DELETE** payroll with a receipt attached (200 `{deleted: true}`, then
404; **receipt file removed**; cash **78 000 000**, burn 7 333 333, runway 10.6, `runway_low` false; three
outflows remain; summary total 18M) → 422 (create validation, explicit null, bad month) / 404 / 401 shapes.
All numbers reconcile by hand. 39 captures under `e2e/_captures/expenses/` are the verbatim source of the FE
guide. Single alembic head after this slice: `0043_finance_expenses`, `down_revision = 0042_finance_invoices`;
`origin/develop` re-checked on 2026-09-30 still tops out at `0042_finance_invoices`.

**Honest gap disclosure (unit-only or source-only, not e2e-captured).** 403 for non-finance roles and
accountant access; cross-workspace 404; the runway alert/signal refresh on create/edit/delete (the live run
crossed the low-runway threshold but did not read the notification back); oversize (> 10 MB) receipt 422;
`date_from`/`date_to` filters; non-ledger edits leaving the ledger untouched; the percent-rounding
case that does not divide exactly; all-zero-month summary; the Cloudinary `https://` receipt URL (e2e ran
local storage); future-dated expenses (in summary, not in cash-flow); mixed currency; case-sensitive category
grouping.

## Operate / roll back

**Deploy-time requirements.** Run `alembic upgrade head` to apply `0043_finance_expenses`. Receipts need the
storage backend configured for the environment: `STORAGE_BACKEND=cloudinary` with the Cloudinary credentials
in staging/prod (the same config Documents already uses); with the default `local` backend `receipt_url` is a
filesystem path the browser cannot open. Existing workspaces are unaffected (no backfill; expenses start empty).

**Rollback:** revert this slice's commits as a unit (`d2133e2..c9552a1`, plus this docs commit) and downgrade
(`poetry run alembic downgrade 0042_finance_invoices`), which drops the `expenses` table (all expenses and
their receipt links are lost; **receipt files already uploaded to storage are not deleted** by the downgrade).
**Order matters and there is one data hazard:** `transactions` rows with `source = 'expense'` survive the
downgrade, and code from *before* this slice does not know that enum value, so reading those rows would
error. Before rolling the code back, either delete every expense through the API (removes its outflow) or
`DELETE FROM transactions WHERE source = 'expense'` (this **raises** cash-on-hand by those expenses' totals — a
deliberate ledger change, take a backup first). Downgrade the migration only *after* the app code is rolled
back, same ordering as every slice.

## Follow-ups / known non-blocking gaps

- **Budgets (Slice 4b).** Per-category monthly limits whose "actual" is the sum of these expenses (the same
  `category` string the summary groups on). Not built; `category` being free text (case-sensitive grouping)
  matters for how a budget matches an expense — decide a controlled vocabulary or normalisation there.
- **Recurring-expense scheduler deferred.** `recurring` is a stored, returned, filterable flag only — nothing
  generates the next occurrence or reminds the user. Needs a Module 20 scheduler job.
- **Concurrent PATCH on one expense can desync it from its outflow.** `update_expense` uses `get_expense`
  (no row lock), so two simultaneous edits can leave the ledger row reflecting a different write than the
  expense row. Fix: `with_for_update()` + `populate_existing` on the expense fetch in `update_expense` (and
  `delete_expense`), as `invoices._get_invoice_locked` does.
- **No idempotency key on money POSTs — module-wide.** `POST /finance/expenses` (like `/finance/transactions`
  and `/finance/invoices`) has no `Idempotency-Key`, so a client retry after a timeout can double-post an
  expense and its outflow. The FE guide tells the FE to disable submit in flight. Fix is cross-slice: the
  Redis-backed cache + in-flight-sentinel pattern on every money write.
- **Mixed currency is not converted.** Inherited single-currency assumption (Slices 1–3): the summary sums raw
  minor units and each outflow adds its raw minor units into `cash_on_hand`, burn and runway. Needs a
  reporting currency + FX source (same follow-up as Slices 1–3).
- **`currency` is not ISO-4217-validated** — only `1..3` chars.
- **Storage delete happens before the DB commit (module-wide, matches Documents).** `delete_expense`,
  `remove_receipt` and the replace path call `get_storage().delete` before the request commits; a rare commit
  failure can leave a row pointing at a deleted file (or, on upload, an orphaned file). Fix: delete from
  storage after a successful commit (outbox / post-commit hook).
- **Receipt content type is trusted from the header** — not sniffed from the bytes; a mislabelled file is
  stored as labelled. Fix: magic-byte sniff (PNG / JPEG / `%PDF`) at the service boundary.
- **Future-dated expenses appear in the summary but not in cash-flow** (cash-flow filters `date <= today`).
  Either block future dates or label the summary.
- **No `expense.*` domain events / notifications**; list is unpaginated.
- **Live e2e did not exercise** RBAC 403, the Cloudinary URL form, oversize receipts, or the runway
  notification firing through an expense — all unit- or source-verified; see the honest-gap disclosure above.
- **Slice 4b — budgets. Slice 5 — financial model. Slice 6 — bank / accounting / Stripe integrations** (which
  will reuse the reserved `source` values alongside `invoice` and `expense`).
