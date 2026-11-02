# SOP — Module 12 Finance Hub, Slice 1: Cash Flow (transactions ledger, CRUD + categorize, cash-flow summary)

**What shipped** — Module 12's first slice (PRD 12.1, Cash Flow). **Slice 1 of 6; Module 12 is now
open.**

1. **`transactions` ledger** — a per-workspace table of money movements
   `(date, description, category?, amount_minor, currency, direction, source)`; new
   `TransactionDirection` (`in`/`out`) and `TransactionSource` (`manual`/`bank`/`accounting`/
   `stripe`) enums. `source` is always `manual` this slice; the other values are reserved for the
   Slice 6 integrations.
2. **Transactions CRUD + inline categorize** — `POST/GET/PATCH/DELETE /finance/transactions`,
   with list filters (`uncategorized`, `category`, `direction`, `date_from`, `date_to`); categorizing
   is just a `PATCH {"category": …}`.
3. **Cash-flow summary** — `GET /finance/cash-flow`: `cash_on_hand`, `monthly_burn`,
   `monthly_revenue`, `runway_months` (float | null), `runway_low` (danger flag), `currency`, and a
   6-point zero-filled `by_month` series.

Migration **`0039_finance_transactions`** (one new table), a new `app/services/finance/` package
(`service.py`, `cashflow.py`, `errors.py`), 5 new routes under `/finance` behind
`require_role(founder, team_member, accountant)`.

Commits (branch `feat/module-12-finance-slice1`, off `develop` `be40a38`; not yet merged, no PR
opened yet), oldest to newest:
`540f2f0` (design spec) → `65267d2` (implementation plan) →
`2e79681` (`transactions` table + enums, migration `0039`) →
`316cab8` (fix: store the enums by value — `in`/`out`) →
`eaee34e` (transactions CRUD + inline categorize) →
`0b00aa8` (fix: explicit `null` on non-nullable PATCH fields -> 422 not 500; cross-tenant tests) →
`f55ac93` (cash-flow summary) →
`a658448` (fix: `runway_low` fires when out-of-cash and burning; exclude future-dated; cap amount to
int32) →
`1fea1c6` (e2e finance journey + 5 captures) → this docs commit.

## Why

Slice 1 of 6 in the Module 12 (Finance Hub) plan, PRD 12.1 **Cash Flow** — the screen founders
open first: "how much cash do we have, what are we burning, how long does it last". Before building,
the sub-features were cross-checked against the FE's current UI (the `cofoundaz` handoff):

| Sub-feature | FE status | Consequence |
|---|---|---|
| Cash-flow read (`cash_on_hand`, burn, revenue, runway, monthly series) | An FE Cash Flow screen exists | **FE-aligned** — read shape follows the UI's cards and chart |
| Money as integer minor units + ISO currency | FE renders a symbol | **FE-aligned** — the FE maps the code to a symbol and divides by 100 |
| Transactions ledger CRUD + categorize | Ledger/categorize UI exists | **FE-aligned** |

A summary needs something to summarize, so the ledger (manual entry) had to ship with it; bank /
accounting sync is a later slice and `source` is reserved for it.

**Deliberately deferred** (per the design spec): runway scenarios / what-if, the
`finance.runway.low` event and the Health-Score finance signal (Slice 2); invoices (Slice 3);
expenses / budgets (Slice 4); the financial model (Slice 5); bank / accounting / Stripe integrations
(Slice 6); multi-currency conversion.

## How

**Money is `amount_minor` (int) + `currency`, with `direction` carrying the sign.** `amount_minor` is
always `>= 0` (`ge=0, le=2_147_483_647`) and `direction` is `in`/`out`. Signed amounts were rejected:
one representation (non-negative magnitude + direction) means the sums are a plain
`case(direction == in, …)` and there is no ambiguity about a "negative inflow". Integer minor units
avoid float drift; the FE renders the symbol and divides by 100. The int32 upper bound came from fix
`a658448`: the column is a Postgres `Integer`, so an oversized value used to pass Pydantic and fail at
flush as a **500**; the schema bound makes it a **422**.

**Enums stored by value, not member name** (`316cab8`). `TransactionDirection` has members
`inflow`/`outflow` whose *values* are `"in"`/`"out"`. SQLAlchemy `Enum` defaults to storing the member
**name**, which would have written `inflow` while the migration's labels and the FE strings are
`in`/`out`. The model uses `values_callable=lambda e: [m.value for m in e]` on both enum columns (and
`native_enum=False`, so no PG enum type to migrate), so the DB, migration and wire all agree.

**Explicit `null` on non-nullable PATCH fields is a 422** (`0b00aa8`). `TransactionUpdate` uses
`Optional` fields with `None` defaults for "omitted", so an *explicit* `null` was indistinguishable
from omission until the row hit `NOT NULL` at flush (500). A `model_validator(mode="after")` inspects
`model_fields_set` and rejects an explicit `null` on `date` / `description` / `amount_minor` /
`currency` / `direction`; `category` (nullable on the row) may be `null` to re-uncategorize. The
service then applies `model_dump(exclude_unset=True)`.

**`source` cannot be set by the client.** `TransactionCreate` has no `source` field and the service
forces `TransactionSource.manual`; a `source` in the body is ignored.

**Cash-flow summary is computed in SQL with `case()` sums.** One filtered base query
(`startup_id`, `date <= today`) yields all-time inflow/outflow (cash on hand) and, with extra date
filters, the trailing-3-month window and the 6-month series. `cash_on_hand = Σin − Σout`.

**Trailing-3-calendar-month average for burn and revenue.** The window starts on the first day of the
month two months ago (this month + the two before). `monthly_burn = max(0, round((out − in) / 3))`
(net burn, clamped at 0), `monthly_revenue = round(in / 3)`. The divisor is always 3, so a startup
younger than 3 months is diluted (lower burn/revenue, longer runway) — accepted and documented in the
FE guide rather than special-cased.

**Null-safe runway + an out-of-cash danger flag.** `runway_months = round(cash / burn, 1)` only when
`burn > 0 and cash > 0`, else `None` — never a divide-by-zero, never a negative runway. But `None`
means two opposite things: *not burning* (healthy) and *burning with no cash* (critical).
`runway_low = burn > 0 and (runway_months is None or runway_months < 6)` fires in the second case too.
The first cut only checked `< 6`, so an overspent workspace (cash <= 0, burning) got `runway_low:
false` — the worst case showed no banner; fixed in `a658448`. The FE guide tells the FE to drive the
banner from `runway_low` and disambiguate with `monthly_burn` / `cash_on_hand`, never to render null
as "healthy".

**Future-dated transactions are excluded from the summary** (`a658448`). `date <= today` is on the
base query, so a future entry never counts toward cash on hand, burn, revenue or `by_month` (keeps
the totals and the series consistent). The list endpoint still returns them.

**UTC.** "Today" is `datetime.now(UTC).date()`; month boundaries are UTC. `by_month` is the last 6
calendar months, oldest → newest, zero-filled in Python from the grouped rows
(`to_char(date_trunc('month', date), 'YYYY-MM')`).

**Multi-currency is not converted.** The sums add `amount_minor` across currencies as if they were
one unit; `currency` on the summary is the most common currency among counted rows (default `NGN`).
Single-currency workspaces only; a mixed-currency summary is a follow-up. *Alternative rejected:*
FX conversion now — needs a rate source and a reporting-currency setting, out of scope for the
ledger slice.

**`_validation` lives in `finance/errors.py`** (mirrors marketing) so any finance module can raise a
field-level 422 without importing `service.py`, preempting the service↔helper import cycle that
Marketing Slice 5 had to break with a function-local import.

**RBAC includes `accountant`.** `_finance = require_role(founder, team_member, accountant)` —
the accountant role exists for exactly this module. Other roles → 403.

## What's involved

**Migration `0039_finance_transactions`** (`alembic/versions/0039_finance_transactions.py`, chains off
`0038_marketing_metrics`, sole alembic head) — one additive `create_table` (`transactions`; `startup_id`
FK `CASCADE`, `external_ref` reserved for integrations) plus indexes on `startup_id` and
`(startup_id, date)`. No existing table touched; reversible downgrade drops the table.

**Enums** — `app/db/models/enums.py`: `TransactionDirection`, `TransactionSource`.

**Model** — `app/db/models/finance.py`: `Transaction` (registered in `app/db/models/__init__.py`).

**Schemas** — `app/schemas/finance.py`: `TransactionCreate`, `TransactionUpdate`,
`TransactionResponse`, `MonthPoint`, `CashFlowResponse`.

**Service** — `app/services/finance/` (new package): `service.py` (`create/get/list/update/delete_
transaction`, `serialize_transaction`), `cashflow.py` (`cash_flow_summary`, `_months_back`),
`errors.py` (`_validation`).

**Endpoints** — `app/api/v1/endpoints/finance.py` (new), mounted in `app/api/v1/api.py` at
`/finance`, all behind `_finance`:

| Route | Purpose |
|---|---|
| `POST /finance/transactions` | record a transaction → the transaction |
| `GET /finance/transactions` | list, filters `uncategorized` / `category` / `direction` / `date_from` / `date_to`; date desc, newest first; unpaginated |
| `PATCH /finance/transactions/{id}` | partial update / inline categorize |
| `DELETE /finance/transactions/{id}` | hard delete → `{"deleted": true}` |
| `GET /finance/cash-flow` | the summary |

**Tests**
- `tests/db/test_finance_transaction_model.py` + `tests/test_finance_migration.py` — enums stored by
  value, model persists, single head `0039`, upgrade/downgrade round-trip.
- `tests/api/test_finance.py` — CRUD round-trip (incl. `uncategorized` filter and delete);
  client-cannot-set-`source`; negative and over-int32 amount -> 422; RBAC 403 (parametrized) and
  accountant allowed; explicit-null PATCH -> 422 while `category: null` -> 200; cross-tenant 404; empty
  summary zeroed; runway + `runway_low` at < 6 months; runway not-low; **null runway when
  net-positive**; **null runway + `runway_low` true when out of cash**; future-dated excluded.
- `e2e/test_finance.py::test_finance_cash_flow_journey` (new) — onboard a founder → record a raise, a
  customer payment and three expenses (one uncategorized) → list → `uncategorized` filter →
  categorize via PATCH → read the summary; **5 live captures** under `e2e/_captures/finance/`
  (`transaction_created`, `transactions_list`, `transactions_list_uncategorized`,
  `transaction_categorized`, `cash_flow`).

**Errors / API surface — additive only** (no existing route or shape changed): `VALIDATION_ERROR`
(422) — negative / over-int32 `amount_minor`, bad `direction`/`date`, length limits, explicit `null`
on a non-nullable PATCH field; `NOT_FOUND` (404) — unknown or other-workspace id; `FORBIDDEN` (403)
for non-finance roles. No async/LLM behaviour.

**Docs**
- `docs/fe-integration-guide-finance-cashflow.md` (new) — every success body pasted verbatim from
  the 5 captures; error / DELETE / filter shapes labelled not-captured; the runway-null,
  trailing-3-month, future-dated and multi-currency semantics called out up front; verification table.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 12 added as an open module (Slice 1 done, Slices 2–6
  planned); tally now 13 fully complete, 1 open (12), 12 not started.

## Verification

**Unit/integration:** the API, model and migration tests above, landed with each feature commit. The
whole-repo unit + CI-parity run is owned by the controller before the PR is opened and is not
reproduced by this docs commit.

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 59 passed**, including the new
`test_finance_cash_flow_journey`. That journey's captures are the source of every success body in the
FE guide, and its numbers reconcile by hand (cash 70,000,000; burn 10,000,000 = 30,000,000 net out
÷ 3; revenue 2,000,000; runway 7.0, not low). `alembic heads` — single head
`0039_finance_transactions`.

**Honest gap disclosure.** The e2e journey only exercises the happy path. Not e2e-captured (unit-tested
or source-derived only, and labelled as such in the FE guide): all 422 / 403 / 404 responses and their
bodies; the `DELETE` response; the `category` / `direction` / `date_from` / `date_to` filters; the
empty-workspace zeroed summary; both **null-runway** cases (net-positive and out-of-cash) and
`runway_low: true`; the future-dated exclusion. Mixed-currency behaviour has **no test at all** (it is
a documented limitation read from `cashflow.py`).

## Operate / roll back

**New deploy-time requirement: none.** No new worker job, no new config. Run `alembic upgrade head`
to apply `0039_finance_transactions`. Nothing populates the ledger automatically — the Cash Flow
screen stays empty until transactions are entered via `POST /finance/transactions`.

**Rollback:** revert this slice's commits as a unit (`540f2f0..1fea1c6`, plus this docs commit) and
downgrade (`poetry run alembic downgrade 0038_marketing_metrics`), which drops `transactions`. Safe —
nothing else reads it. Downgrade the migration only *after* the app code is rolled back, same
ordering as every slice in this codebase. Recorded transactions are lost on downgrade.

## Follow-ups

- **Slice 2 — runway scenarios + `finance.runway.low` event + Health-Score signal.** This slice
  exposes the runway number and `runway_low` flag only; nothing emits an event or feeds the Health
  Score yet.
- **Slice 3 — invoices.** **Slice 4 — expenses / budgets.** **Slice 5 — financial model.**
  **Slice 6 — bank / accounting / Stripe integrations** (uses the reserved `source` values and
  `external_ref`).
- **Multi-currency conversion** — not done; sums add unlike currencies as one unit.
- **Mixed-currency summary** — `currency` is only the most common one; decide on a reporting currency
  and FX source, and add a test (none exists today).
- **Revenue vs financing split** — `monthly_revenue` counts all inflows, including a fundraise inside
  the window; needs a category- or type-based split.
- **Transactions pagination** — the list is unpaginated.
- **Missing live captures / tests** — DELETE, the remaining list filters and the error bodies are
  unit- or source-verified only.
