# SOP — Dashboard KPI tiles read real Finance/Marketing data (backend)

**What shipped (one line):** `GET /dashboard/summary` now returns real Monthly Revenue, Runway and
Campaign CTR figures under a new `kpis.metrics` object, each with an honest `status`, role-gated
to match the Finance/Marketing routes. Pipeline stays `not_built` until Sales has a backend. Branch
`feat/dashboard-kpis` → PR to `develop`.

## Why
The dashboard's four money/marketing tiles always said "Coming Soon". The FE wasn't the cause:
`get_summary` hardcoded `revenue/runway/pipeline_value/campaign_performance` to `None`
(`app/services/dashboard/service.py`), even though Finance (cash flow, runway) and Marketing
(metrics ingestion, analytics) backends had shipped. A plain `null` also couldn't tell "nothing
recorded yet" apart from "module not built", so the FE couldn't render either case honestly.

## How (key decisions)
| Decision | Why | Rejected |
|---|---|---|
| Per-metric `status` (`ok` / `no_data` / `restricted` / `not_built`, plus runway `not_burning` / `out_of_cash`) | The FE renders each case differently. `0` is real data, `null` without a reason is not. | Scalars + nulls: ambiguous, which is exactly what produced the old bug |
| **Additive** `kpis.metrics`. Legacy scalar keys kept and still `null` | The old FE keeps rendering its placeholder until the FE PR merges. No NaN window between the BE and FE merges | Changing the scalar keys' type in place would break the deployed FE |
| Reuse `cash_flow_summary` (Finance) for revenue/runway | The dashboard number equals the Finance hub's number, from one source of truth | A separate dashboard-specific revenue query would drift |
| CTR via a 2-sum query over the trailing 30 days, `_rate` reused | `get_analytics` runs 4 grouped queries the tile doesn't need. `impressions == 0` returns `no_data`, because `_rate` reports `0.0` and that reads as "nobody clicked" | Calling `get_analytics` |
| **Role gate** with `FINANCE_ROLES` / `MARKETING_ROLES` constants, now also used by the Finance/Marketing routes | The dashboard allows any member. Without the gate an investor could read revenue there when `/finance` would 403 them. A shared constant can't drift | Duplicated role tuples |
| Each metric in its own SAVEPOINT → `{"error": true}` | One failing source must not blank the strip (same pattern as `_section_isolated`) | One try around all four |
| `get_summary(..., role=...)` is a required keyword | A default would fail **open** (to founder) | `role=founder` default |

## What's involved
- New: `app/services/dashboard/kpis.py` (`kpi_metrics`).
- `app/services/dashboard/service.py`: `get_summary` takes `role`, and `kpis` gains `metrics`.
- `app/api/v1/endpoints/dashboard.py`: passes `membership.role`.
- `app/db/models/enums.py`: `FINANCE_ROLES`, `MARKETING_ROLES`. `endpoints/finance.py` and
  `endpoints/marketing.py` now use them (no behaviour change).
- Tests: `tests/services/test_dashboard_kpis.py` (17), `tests/api/test_dashboard_summary.py`
  (+2 role tests), callers updated for the `role` kwarg.
- e2e: `e2e/test_dashboard_kpis.py` → `e2e/_captures/dashboard_kpis/*.json` (6 captures).
- Docs: `docs/fe-integration-guide-dashboard-kpis.md` (new), and the KPI row in
  `docs/fe-integration-guide-dashboard.md` now points to it.
- No migration, no new config.

## Verification
- `pytest` full suite: **2189 passed, coverage 97.43%** (`kpis.py` 100%).
- black / isort / ruff / mypy / bandit clean, pylint 9.88.
- `scripts/e2e_run.sh`: **67 passed**, including the new journey (empty → burning → investor →
  accountant → out_of_cash → not_burning, all asserted and captured).
- Local docker stack `:8000`: test founder's workspace returns the `no_data` shape.

## Operate / roll back
Read-only. No schema or data change. Revert the PR to roll back. The FE falls back to its legacy
"Coming Soon" because the scalar keys are unchanged.

## Follow-ups
- **Revenue counts every inflow**, raises and loans included (matches Finance's own
  `monthly_revenue`). Needs a revenue category or source split in Finance.
- FE Finance/Marketing hubs are still **mock**, so a founder can't yet create the data that lights
  these tiles from the UI. Wire those hubs next.
- Pipeline → real once the Sales backend ships (shape already final).
- Drop the legacy `kpis.revenue/runway/pipeline_value/campaign_performance` keys after the FE
  reads `metrics`.
- Deltas and sparklines (`KPISnapshot.delta/sparklineData`) are not provided yet.
