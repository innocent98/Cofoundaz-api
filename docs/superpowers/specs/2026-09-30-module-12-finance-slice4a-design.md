# Module 12 — Finance Hub, Slice 4a: Expenses — Design

**Status:** Draft for review
**Date:** 2026-09-30
**Depends on:** Slice 1 (`Transaction` + cash-flow), Slice 2 (runway — expense outflows feed it), Slice 3 (the invoice-managed-transaction guard we generalize here).
**Next:** Slice 4b (Budgets) builds on this — budgets are per-category monthly limits whose "actuals" sum these expenses.
**Spec author note:** every payload shape here is a target; the FE integration guide is re-derived verbatim from live e2e captures at build time.

---

## 1. Goal

Back the Finance → **Expenses** screen: record operational spend (vendor, category, amount, date),
flag recurring items, attach a receipt (image/PDF), and — the integrating decision — **each expense
posts an outflow `Transaction` so operational spend flows into Cash Flow and Runway** (symmetric with
Slice 3's paid-invoice inflow). Also a per-category breakdown for the month (the donut).

## 2. FE cross-check (`../cofoundaz/app/(dashboard)/finance/expenses/page.tsx` + `hooks/useFinanceApi.ts`)

| FE element | Backend obligation |
| --- | --- |
| Expense table: vendor, date, category, recurring toggle, receipt, amount | `Expense` fields + CRUD |
| "Add Expense" | `POST /finance/expenses` |
| Recurring checkbox (per row) | `recurring` bool, toggled via PATCH |
| Receipt upload/download (image/PDF) | `POST /finance/expenses/{id}/receipt` → Cloudinary, stores `receipt_url` |
| Category Breakdown donut (% per category, month) | `GET /finance/expenses/summary?month=YYYY-MM` |

FE `Expense`: `{ vendor, category, date, amountMinor, currency, recurring, receiptAttached }`.
Category is a free-text tag (Software & IT, Rent & Office…). **This same `category` string is the
dimension Slice 4b budgets match on** (agreed scope: per-category). Money in minor units.

## 3. Data model

**`expenses`** — one row per expense, per startup.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | |
| `startup_id` | UUID FK→startups CASCADE, index | tenancy |
| `created_by` | UUID FK→users | who logged it |
| `vendor` | String(200) | |
| `category` | String(120) | free tag; the shared budget dimension (4b) |
| `expense_date` | Date | index via composite `(startup_id, expense_date)` |
| `amount_minor` | Integer | `0 … 2_147_483_647` (int32) at validation |
| `currency` | String(3) | default NGN |
| `recurring` | Boolean, default False | flag only (scheduler deferred) |
| `receipt_url` | String(600), nullable | set by the receipt upload |
| `receipt_key` | String(400), nullable | storage key, for delete/replace |
| `notes` | Text, nullable | |
| `transaction_id` | UUID FK→transactions SET NULL, unique, nullable | the linked outflow (1:1) |
| `created_at`/`updated_at` | timestamptz | |

Migration **`0043_finance_expenses`**, `down_revision=0042_finance_invoices` (develop head). Single
head; re-verify at build time. **`TransactionSource.expense`** added to the existing varchar-backed
enum (`length=12`; "expense"=7) — **no DDL**.

## 4. Expense ↔ cash-flow (the outflow, agreed scope)

An expense is money **already spent**, so on **create** it posts an outflow `Transaction` immediately
(no draft lifecycle — unlike invoices): `direction=outflow`, `amount_minor=expense.amount_minor`,
`currency`, `date=expense_date`, `description=f"Expense: {vendor}"`, `category=expense.category`,
`source=TransactionSource.expense`; store `transaction_id` on the expense (1:1).
- **Edit:** a PATCH that changes amount/date/category/vendor **syncs the linked transaction** to
  match (so cash-flow always reconciles with the expense). `db.flush()`.
- **Delete:** deletes the linked transaction, then the expense (and its receipt from storage).
- **Runway consistency:** every create/update/delete endpoint runs `evaluate_runway_alert` +
  `upsert_runway_signal` after the service and before commit (exactly as Slice 3 does), so the
  `finance.runway.low` alert + `money.runway_live` signal reflect expense-driven burn.
- **Managed-transaction guard:** generalize Slice 3's `_reject_invoice_managed` → reject direct
  `PATCH`/`DELETE` on any transaction whose `source ∈ {invoice, expense}` (422 — "edit the
  expense/invoice instead"). Manual/bank/accounting/stripe transactions stay editable.
- **Idempotency/atomicity:** create → exactly one outflow; the transaction insert/sync/delete + the
  runway refresh commit atomically in the endpoint's single `db.commit()` (service flushes only).
- **Documented UX rule (FE guide):** log a given spend as an expense **or** a manual outflow
  transaction, not both, or it double-counts. (Same single-currency, no-conversion limitation as
  Slices 1–3.)

## 5. Receipts (Cloudinary, agreed scope)

Reuse `app/platform/storage.py` `get_storage().save(key, content, content_type) -> url` (Module 18
pattern, `documents/files.py`).
- `POST /finance/expenses/{id}/receipt` — multipart `UploadFile`. Allowlist **image/PDF only**
  (`image/png`, `image/jpeg`, `application/pdf`); reject others → 422. Enforce a size cap (e.g. ≤ 10 MB
  → 413/422). Key: `receipts/{startup_id}/{uuid}{ext}`. Set `receipt_url` + `receipt_key`; if a
  receipt already exists, delete the old key first (replace). Returns the serialized expense.
- **Delete cascade:** deleting an expense (or its receipt via `DELETE /finance/expenses/{id}/receipt`)
  calls `get_storage().delete(receipt_key)`.
- Serialized expense exposes `receipt_url` (+ a `has_receipt` bool for the FE's icon state).

## 6. Endpoints (`/api/v1/finance`, gated by the Slice-1 `_finance` dep: founder, team_member, accountant)

| Method + path | Purpose |
| --- | --- |
| `POST /finance/expenses` | create + post outflow transaction |
| `GET /finance/expenses?category=&month=&date_from=&date_to=&recurring=` | list, newest first, filters |
| `GET /finance/expenses/{id}` | one expense |
| `PATCH /finance/expenses/{id}` | edit (sync linked transaction); toggle `recurring` |
| `DELETE /finance/expenses/{id}` | delete (reverse transaction + delete receipt) |
| `POST /finance/expenses/{id}/receipt` | upload/replace receipt (Cloudinary) |
| `DELETE /finance/expenses/{id}/receipt` | remove receipt |
| `GET /finance/expenses/summary?month=YYYY-MM` | per-category totals + percentages (the donut) |

Validation: `amount_minor` `0 … int32` → 422; explicit-null on required PATCH fields → 422 (Slice-1
`model_validator`); tenancy `filter_by(startup_id=...)` from membership; cross-tenant → 404.

## 7. Files (indicative)

**Create:** `app/db/models/expense.py`; `app/schemas/expense.py`; `app/services/finance/expenses.py`
(CRUD, outflow sync, receipt, category summary); `alembic/versions/0043_finance_expenses.py`; tests
(`tests/db/test_expense_model.py`, `tests/test_expense_migration.py`, `tests/api/test_expenses.py`,
`tests/services/test_expense_cashflow.py`, `e2e/test_expenses.py` + captures);
`docs/fe-integration-guide-finance-expenses.md`, `docs/sop/2026-09-30-finance-slice4a.md`.
**Modify:** `app/db/models/enums.py` (`TransactionSource.expense`); `app/services/finance/service.py`
(generalize `_reject_invoice_managed` → invoice+expense); `app/api/v1/endpoints/finance.py`
(endpoints + runway hooks); `docs/checklist/PROJECT_CHECKLIST.md`.

## 8. Senior checkpoints (settle in brainstorming → already largely decided)

1. **Category as free string vs controlled vocabulary** — v1 uses a free `category` string (like
   `transactions.category`). Budgets (4b) match on it exactly, so typos would mis-bucket. Decide
   whether 4b introduces a controlled category list; note the risk in the FE guide.
2. **Expense edit syncing the transaction** — confirm we mutate the linked outflow on PATCH (chosen)
   rather than delete+recreate (which would churn `transaction_id`).
3. **Receipt size/type cap** — confirm the allowlist (image/PDF) and max size.

## 9. Deferred (later, tracked in checklist)

- **Budgets** — Slice 4b (per-category monthly limits + actuals-from-expenses + variance + copy/draft).
- **Recurring-expense scheduler** — auto-generating next month's expense from `recurring=true` is a
  scheduled job; v1 stores the flag only (same deferral as invoice reminders).
- **AI** — "draft budget from actuals" (4b) is a deterministic computation, not AI; no AI this slice.
- **Multi-currency conversion**, controlled category vocabulary, `invoice.*`/`expense.*` domain events.

## 10. Cross-cutting rules (carried)

Money int32-bounded at validation (422 not 500); services flush / endpoints commit; tenancy from
membership; managed-transaction guard; runway hooks on money mutations; no import cycle
(`expenses.py` imports `Transaction`/`cash_flow_summary`/`runway`/storage — none import it back);
CodeQL test hygiene; migration id ≤32 chars, single head; **no AI attribution** in commits/PR;
`import datetime as dt` in schemas.

## 11. Testing

- **Unit/service:** create posts exactly one outflow (source=expense, amount/date/category match);
  edit syncs the transaction; delete reverses it + deletes the receipt; over-int32 → 422; category
  summary math (per-category totals + percentages sum to ~100). Managed-guard: PATCH/DELETE a
  source=expense transaction → 422; manual unaffected.
- **Cash-flow composition:** create expenses → `GET /finance/cash-flow` burn/cash reflect them;
  delete → reverts; runway alert/signal refresh (as Slice 3).
- **Receipts:** upload image/PDF → `receipt_url` set (use the local/stub storage backend in tests);
  reject a disallowed content-type → 422; replace deletes the old key; delete-expense removes the file.
- **API:** RBAC (accountant 200, mentor/investor 403); cross-tenant 404; filters (category/month/
  date-range/recurring).
- **e2e:** create expenses (with categories) → list → filter → category summary → upload a receipt →
  `GET /finance/cash-flow` shows the burn → delete → reverts. Capture bodies.
- Local CI parity green before push (pytest ≥95%, alembic single-head + check, e2e, static);
  re-run `pip-audit` parity is handled by the pyjwt bump already on develop.
