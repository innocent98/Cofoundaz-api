# SOP: Funding inflows are cash, not revenue (D-FIN-11)

| | |
|---|---|
| **What shipped** | Ledger rows in the `Funding` category no longer count toward monthly revenue or offset monthly burn. They still count toward cash on hand, the cash-flow chart and the model's paid-in capital. Branch `fix/funding-not-revenue`. |
| **Date** | 2026-10-24 (decision D-FIN-11, Adebayo: "your recommendation ... is okay") |

## Why
Monthly revenue was "all money in over the trailing 3 months / 3", and monthly burn was "(money out - money in) / 3". A recorded raise therefore:
- showed as about a third of the raise per month in revenue for three months (Finance overview, runway baseline, dashboard Revenue tile, AI briefing, the financial model's starting revenue);
- counted twice in the model (starting revenue *and* paid-in capital);
- cancelled the burn, so runway read "not burning" (no number, no low-runway flag) right after a raise, which is exactly when a founder looks at it.

## How
- `app/services/finance/cashflow.py`: `_trailing_3mo_totals` sums inflows through `_revenue_inflow_sum()`, which skips rows whose category is `Funding` (case-insensitive, the same match `model_inputs.funding_total` uses). That window is the single source for `cash_flow_summary` (`monthly_revenue`, `monthly_burn`, `runway_months`, `runway_low`) and `trailing_monthly_flows` (runway scenarios, model `revenue_0` / `monthly_costs_0`), so every consumer changes together.
- Unchanged on purpose: `cash_on_hand` (all-time, Funding included), `by_month` (the cash-flow chart is money moving, so the raise still shows as money in), `funding_total` (paid-in capital).
- `app/services/dashboard/ai_briefing.py`: the prompt label now says "Average monthly revenue, funding excluded".
- Rejected: an `is_revenue` flag per transaction (a migration and an FE control for one rule); leaving uncategorized inflows out of revenue too (would zero revenue for founders who never categorise; they keep counting until categorised).

## What's involved
`app/services/finance/cashflow.py`, `app/services/dashboard/ai_briefing.py`; unit test `tests/api/test_finance.py::test_funding_is_cash_but_not_revenue_or_burn_offset`; live journey `e2e/test_finance_funding_not_revenue.py` with captures in `e2e/_captures/finance_funding/`; FE guide `docs/fe-integration-guide-finance-funding.md`. No migration, no schema or response-shape change.

## Verification
- Unit: the new test (raise + sales + costs in one month: revenue = sales / 3, burn = (costs - sales) / 3, runway 27.0, chart inflow still includes the raise, model starting figures exclude it). Existing finance, runway, Mono, dashboard KPI and invoice tests unchanged and green.
- Live e2e on an isolated `cofoundaz_e2e` DB: cash flow, runway, dashboard summary and a generated model, captured verbatim.
- Gates on per-run scratch DBs (Python 3.11 as CI): black, isort, ruff, mypy, pylint 9.88, bandit, migrations (one head, no drift, round trip), full unit suite with coverage, full e2e suite.

## Operate / roll back
Nothing to run; figures change on the next read. Existing saved models keep their old starting figures until regenerated or recalculated with `refresh_actuals`. Revert to roll back.

## Follow-ups
- Refund inflows (`Refunds` category) still count as revenue; a refund *out* is already an outflow. Decide if a refund in should net against revenue.
- A raise recorded uncategorized still counts as revenue until the founder sets the category to Funding; the FE could suggest Funding for large one-off inflows.
