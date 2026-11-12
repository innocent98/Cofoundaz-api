# SOP: Analytics & Reports, slice A (metric catalog, Overview, custom metrics)

| | |
|---|---|
| **What shipped** | The live half of PRD Module 22: a metric catalog with per-metric authorisation, `GET /analytics/overview`, `/by-hub`, `/metrics/resolve`, `/metrics-catalog`, founder-entered custom metrics, a 5-minute cache and a nightly snapshot of point metrics. Branch `feature/analytics-slice-a`, migration `0105_analytics_custom_metrics`. |
| **Date** | 2026-11-12 (D-ANA-1..9 accepted by Adebayo the same day: "Accept all nine") |
| **Design** | FE repo PR #248: `docs/checklist/analytics-reports-prd-gaps.md`, `docs/superpowers/specs/2026-11-12-analytics-reports-design.md` |

## Why
Analytics had one mock page and no API. Slice A gives the Overview/By hub real numbers and lays the foundation (catalog, authorisation,
resolvers, history) that reports, exports, schedules, investor updates and decks build on.

## How (and decisions)
- **Catalog is code** (`app/services/analytics/catalog.py`), 19 platform metrics + founder-entered ones. Each metric belongs to a hub; a hub maps to the
  same role set as the hub's own routes (`HUB_ROLES`), so Analytics cannot become a side door (D-ANA-4). The accountant sees finance only.
  Mentor, consultant, investor and legal advisor have no live access (slice B adds shared reports for mentor/consultant).
- **Authorise first, then cache, then compute** (`service.resolve`): a locked metric returns only `{status: "locked", needs}`; the cache holds workspace-level numbers
  (never keyed by role) and is only read after authorisation.
- **Resolvers** (`resolvers.py`) reuse the hub services: Finance `_base_query` + `cash_flow_summary`, Sales `won_and_cycle`/`win_loss`/`pipeline_kpi`, Marketing metric
  rows, Mission/Roadmap/Validation/Health/Funding tables. They return a number or `None` (never a fake 0); revenue excludes Funding and payouts (D-FIN-11).
- **Periods** (`periods.py`): presets + custom (max 24 months), comparison previous/year, at most 12 series buckets. UTC calendar days (no workspace timezone exists).
- **Freshness (D-ANA-3 c):** live compute with a 5-minute Redis cache (`no_data` is never cached); nightly `capture` stores point metrics in the existing
  `kpi_snapshots` (hooked into `handle_kpi_snapshot`); flows are not stored because they recompute exactly. Report runs (slice B) will freeze numbers.
- **Custom metrics (D-ANA-2 b):** `custom_metrics` + `custom_metric_points` (one value per month), max 20 metrics, validated numbers, founder + team member write.
- **Insights:** rule-based Working/Watch notes, no AI (AI wording is slice G, D-ANA-9).
- Rejected: a metrics table (catalog in code is versionable and testable); storing flow metrics nightly; per-role caching (more keys, same safety).
- Deviations from the spec: `group_by` is empty in slice A (grouped blocks arrive with the builder); `GET /insights` is folded into `/overview`.

## What's involved
`app/db/models/analytics.py`, `alembic/versions/0105_analytics_custom_metrics.py`, `app/services/analytics/{catalog,periods,resolvers,service,overview,custom,snapshots,errors}.py`,
`app/api/v1/endpoints/analytics.py` (+ `app/api/v1/api.py`), `app/worker/handlers/scheduled.py` (snapshot hook). Plan gate: `assert_feature(..., "analytics")` (Growth), dark until `PLAN_ENFORCEMENT`.

## Verification
- 22 API tests (`tests/api/test_analytics_slice_a.py`): role matrix for all 7 roles on 4 routes, per-metric authorisation, locked-before-cache, revenue/funding maths,
  periods, cache hit/invalidate/no_data, custom lifecycle and validation, isolation, runway states, snapshot idempotency and comparison.
- Live e2e journey `e2e/test_analytics_slice_a.py` (founder, accountant, mentor, investor), 19 captures in `e2e/_captures/analytics_slice_a/`; FE guide `docs/fe-integration-guide-analytics.md`.
- Local gates: black, isort, ruff, mypy (whole `app`), migration upgrade/downgrade/upgrade and `alembic check` on a scratch DB, one head. CI runs the rest.

## Operate / roll back
No env vars. Migration `0105` is chained on `0101_marketplace_booking`; if Marketplace D lands first, re-chain `down_revision`. Roll back: `alembic downgrade 0101_marketplace_booking`
(drops the two custom-metric tables) or revert; snapshot rows in `kpi_snapshots` under keys like `health.score` are harmless and expire with the workspace.

## Follow-ups
- Slices B-G (reports, exports, schedules, investor updates, decks, benchmarks/AI wording/glue) per the spec.
- Workspace timezone for periods (gap 72); `group_by`; cache invalidation when Finance/Sales write (today: up to 5 minutes stale, labelled with `as_of`).
- Funding metric is only "active investors"; committed vs target and data room views join with the Funding analytics reads.
