# Module 12 — Finance Hub, Slice 1: Cash Flow

**Status:** design approved (2026-09-29), ready for implementation planning. **First slice of Module 12** (6-slice module).

## Context

Module 12 (Finance Hub, PRD 12.1–12.7) is a new hub: Cash Flow, Runway, Budgets, Invoices,
Expenses, Financial Model, Integrations. It's large, so it's decomposed into **6 slices**, building
the value spine first:

1. **Cash Flow** (this slice) — `transactions` ledger + categorize + cash-flow summary (cash on
   hand, burn, revenue, runway).
2. Runway detail — Base/Best/Worst scenario engine; owns the `finance.runway.low` event +
   Health-Score financial signal.
3. Invoices — CRUD + lifecycle + send + overdue/paid events + notifications.
4. Expenses + Budgets — expense CRUD (+ receipt) + budget-vs-actuals + AI draft-from-actuals.
5. Financial Model — AI 3-statement generation (async job).
6. Integrations (12.7) — QuickBooks/Xero/Bank/Stripe OAuth — **deferred/stub** (no OAuth
   integrations exist, mirroring the SEO-provider deferral in Module 10 Slice 4).

### FE cross-check (per the standing rule)

All 7 finance screens exist in the FE (`cofoundaz/app/(dashboard)/finance/*`), all **mock**
(`useFinanceApi.ts` is `useState` with hardcoded data, no backend calls). The FE type contract for
this slice:
- `Transaction { id; date; description; category; amountMinor: number; currency: string;
  direction: 'in' | 'out'; source }` — **money is `amountMinor` (integer minor units) + `currency`**,
  matching the PRD ("integer minor units + currency code").
- `runwayMonths: number` (e.g. 5.2); `formatCurrency(minorAmount, currency)`.
The FE renders `currency` as a **symbol** ("₦"); the backend stores an ISO code — the FE guide maps
it. Cash-flow read is FE-aligned; there is no FE ingestion beyond manual add.

## Goal

Ship the Cash Flow spine: a per-startup `transactions` ledger (CRUD + inline categorize) and a
derived cash-flow summary (hero stats incl. a runway number + a `runway_low` flag, plus a monthly
in/out/net series) — the default Finance screen and the headline value that later feeds the Health
Score.

## Global constraints (carried verbatim where they bind)

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by
  `startup_id` (from membership, never the body); cross-tenant id → `NotFound` (404).
- **RBAC:** every route behind `_finance = require_role(MembershipRole.founder,
  MembershipRole.team_member, MembershipRole.accountant)` — the `accountant` role is Finance's
  domain (PRD "AC"); other roles (mentor/investor/…) → 403. The PRD's per-module **Finance grant**
  is deferred (grants are deferred system-wide), so `team_member` is allowed for now.
- **Money:** `amount_minor` is a **non-negative** integer (minor units); `direction` carries the
  sign; `currency` is an ISO-4217 string (default `"NGN"`).
- **Validation** via a `_validation(field, message)` helper (422 with `field_errors`), placed in a
  dependency-free `app/services/finance/errors.py` from the start (avoids the service↔helper import
  cycle CodeQL flagged in Module 10 Slice 5).
- **`AppError.http_status`** (not `.status_code`); `get_db` does NOT auto-commit.
- **CodeQL (required check):** no mutating call inside an `assert` (bind to a var first); no
  implicit string concatenation in a list literal.
- **Migration:** id ≤ 32 chars (`0039_finance_transactions`), down_revision `0038_marketing_metrics`
  (current develop head). Migration-head test asserts **single head + revision-in-`alembic history`**
  (not "my revision is THE head").
- **No AI attribution** in any commit message, PR body, or review comment.

## Design

### 1. `transactions` table (migration `0039_finance_transactions`)

New model `Transaction`:
- `startup_id` (FK startups CASCADE, index)
- `date` (`Date`, NN)
- `description` (`String(300)`, NN)
- `category` (`String(120)`, **nullable** — null = uncategorized)
- `amount_minor` (`Integer`, NN, ≥0)
- `currency` (`String(3)`, NN, default `"NGN"`)
- `direction` (new `Enum(TransactionDirection, native_enum=False, length=8)`, NN)
- `source` (new `Enum(TransactionSource, native_enum=False, length=12)`, NN, default `manual`)
- `external_ref` (`String(200)`, nullable — for future bank/accounting sync)
- Index `(startup_id, date)`.

New enums:
- `TransactionDirection` (StrEnum): `inflow = "in"`, `outflow = "out"` — **member names avoid the
  `in` keyword; values match the FE `direction` strings**.
- `TransactionSource` (StrEnum): `manual = "manual"`, `bank`, `accounting`, `stripe` — only `manual`
  is user-creatable in this slice; the rest exist for the deferred integrations (Slice 6).

### 2. Transactions CRUD

All `_finance`-gated; `startup_id`-scoped:
- `GET /finance/transactions` — list, newest first (`date desc, created_at desc`). Query filters:
  `category?` (exact), `uncategorized=true` (category IS NULL), `direction?`, `date_from?`,
  `date_to?`.
- `POST /finance/transactions` — create; `source` forced to `manual` (body `source` ignored/omitted);
  `amount_minor ≥ 0`, `direction`/`currency` validated.
- `PATCH /finance/transactions/{transaction_id}` — partial update (`exclude_unset`); this is the
  "inline categorize" path (patch `category`). Cannot set `amount_minor` negative (422).
- `DELETE /finance/transactions/{transaction_id}` — returns `success_response({"deleted": True})`
  (the codebase's DELETE convention).

### 3. Cash-flow summary — `GET /finance/cash-flow` (derived, read-only)

Response `CashFlowResponse`:
- `cash_on_hand` (int minor) = Σ inflow − Σ outflow, all-time.
- `monthly_burn` (int minor) = `max(0, round((Σ outflow − Σ inflow over the trailing 3 months) / 3))`
  — net outflow per month, floored at 0 (a net-positive business has 0 burn).
- `monthly_revenue` (int minor) = `round(Σ inflow over the trailing 3 months / 3)`.
- `runway_months` (float | **null**) = `round(cash_on_hand / monthly_burn, 1)` when `monthly_burn > 0`
  **and** `cash_on_hand > 0`; otherwise `null` (effectively infinite / not applicable — **no
  divide-by-zero**).
- `runway_low` (bool) = `runway_months is not null and runway_months < 6` — the FE danger banner.
- `currency` (str) = the startup's prevailing currency (most-frequent on its transactions; default
  `"NGN"` when none).
- `by_month`: last 6 calendar months, `[{month: "YYYY-MM", inflow, outflow, net}]` (ascending) for
  the cash-flow chart; months with no transactions appear with zeros.
- "Trailing 3 months" / "last 6 months" are computed from `date.today()` in **UTC**
  (`datetime.now(UTC).date()`), consistent with the Slice-5 analytics cutoff.

## Data flow / tenancy / transactions

Standard. Services flush; endpoints commit. All reads/writes filter `startup_id`. The cash-flow
summary is read-only aggregation (SQLAlchemy `func.sum` with filtered `case()` for inflow/outflow;
`date_trunc('month', date)` for `by_month`), empty-data-safe (`int(x or 0)`, guarded division).

## Components / files

- `app/db/models/enums.py` — `TransactionDirection`, `TransactionSource`.
- `app/db/models/finance.py` (new) — `Transaction`.
- `alembic/versions/0039_finance_transactions.py` — one table (single head off
  `0038_marketing_metrics`).
- `app/schemas/finance.py` (new) — `TransactionCreate`, `TransactionUpdate`, `TransactionResponse`,
  `CashFlowResponse` (+ nested `MonthPoint`).
- `app/services/finance/errors.py` (new) — `_validation` (dependency-free, avoids future cycles).
- `app/services/finance/service.py` (new) — transaction CRUD + `serialize_transaction`.
- `app/services/finance/cashflow.py` (new) — `cash_flow_summary` aggregation.
- `app/api/v1/endpoints/finance.py` (new) — the 4 transaction routes + `GET /finance/cash-flow`;
  registered in `app/api/v1/api.py` at prefix `/finance`.
- Docs: FE guide `docs/fe-integration-guide-finance-cashflow.md` (verbatim from captures; money in
  minor units + currency mapping; `runway_low` semantics); SOP
  `docs/sop/2026-09-29-finance-slice1.md`; `docs/checklist/PROJECT_CHECKLIST.md` (add Module 12 as a
  new 🟡 open module, Slice 1 done).

## Review focus (inputs the spec implies but happy-path tests may miss)

- **Negative / bad money:** `amount_minor < 0` on create or PATCH → 422 (not stored). — CRUD task.
- **Bad enum:** out-of-enum `direction`/`currency` too long → 422; `source` from the body is ignored
  (always `manual`). — CRUD task.
- **Empty-data cash-flow:** no transactions → `cash_on_hand 0`, `monthly_burn 0`, `runway_months
  null`, `runway_low false`, `by_month` six zeroed months, no ZeroDivisionError. — summary task.
- **Runway edges:** `monthly_burn = 0` (net-positive) → `runway_months null`; `cash_on_hand ≤ 0` →
  `runway_months null`. — summary task.
- **Trailing-window boundaries:** a transaction dated 4 months ago is excluded from burn/revenue but
  still counts in `cash_on_hand` (all-time); `by_month` buckets by calendar month. — summary task.
- **Tenancy:** only the caller's transactions in list/summary; cross-tenant id → 404. — CRUD task.
- **RBAC:** 403 for mentor/investor; **accountant is allowed** (200). — endpoints task.

## Decisions (rulings settled during brainstorming)

- **D1 — 6-slice module; Slice 1 = Cash Flow** (transactions + summary + runway number). Detailed
  runway scenarios, invoices, expenses/budgets, financial model, integrations are later slices.
- **D2 — money:** `amount_minor` non-negative int + `direction` sign + `currency` ISO string.
- **D3 — RBAC includes `accountant`** (founder/team_member/accountant); Finance grant deferred.
- **D4 — runway event + Health-Score signal deferred to Slice 2** (Runway detail owns the runway
  domain); Slice 1 exposes the runway number + `runway_low` flag only.
- **D5 — `source` is `manual`-only** now (integrations are Slice 6); the enum carries the future
  values.
- **D6 — `_validation` lives in a dependency-free `finance/errors.py`** from the start (learned from
  the Module 10 Slice 5 service↔analytics CodeQL cycle).

## Out of scope

Runway scenarios (S2); the `finance.runway.low` notification + Health-Score financial signal (S2);
invoices (S3); expenses/budgets (S4); financial model (S5); external integrations / bank sync (S6 —
`source` manual-only); multi-currency conversion (each transaction carries its own currency; the
summary reports the prevailing currency and does not convert).
