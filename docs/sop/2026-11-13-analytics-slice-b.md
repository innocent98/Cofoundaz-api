# SOP: Analytics & Reports, slice B (reports, frozen runs, shares)

| | |
|---|---|
| **What shipped** | Saved reports made of blocks over the metric catalog: 3 template chips, CRUD with an optimistic lock, stateless preview, a run job that stores a frozen snapshot, Founder-granted shares, and two notification kinds. Branch `feature/analytics-slice-b`, migration `0108_analytics_reports` (on `0107_funding_grants`). PR to `develop`, not merged. |
| **Date** | 2026-11-13 |
| **Design** | `cofoundaz/docs/superpowers/specs/2026-11-12-analytics-reports-design.md` sections 3, 4, 6, 10, 11 |

## Why
Slice A shows live numbers. Founders need a report they can name, keep, freeze and show to a mentor or accountant without handing over numbers that person may not see.

## How (and decisions)
- **Three layers, each fails closed** (`services/analytics/reports.py`): visibility (founder all; team member own + shared; accountant/mentor/consultant only shared; otherwise 404, same as another workspace), edit rights (founder any, team member own; visible but read-only is 403), per-metric authorisation (`catalog.report_can_see`).
- **Per-metric rule for readers:** mentor and business consultant get sales, marketing, validation, health, mission/roadmap; never finance, funding or custom metrics. Accountant gets finance only (slice A's rule).
- **Redaction happens on read, from the hub stored on each block/result** (`hidden_hub`), so a run the Founder made never shows Finance to a mentor, and a custom metric deleted later cannot un-redact. An unknown hub is visible only to founder/team member (Opus-style review finding, fixed before PR).
- **Frozen runs** (`runs.py`): `POST /reports/{id}/run` creates a `pending` row holding the definition and explicit dates, enqueues `analytics.report.run`; the worker resolves under the requester's role at job time (membership re-checked; lost access gives `failed`), then marks `ready` and publishes `analytics.report.generated`. A finished run is never touched again (`execute` no-ops unless pending). A double click reuses the pending run.
- **Optimistic lock:** `PATCH` carries `version`; the UPDATE matches `version` so two overlapping saves cannot both win (409 `VERSION_CONFLICT`).
- **Text hygiene:** names, descriptions and text blocks are plain text only (HTML tags rejected, control characters stripped). Caps: 30 blocks, 100 reports, 20 shares. Preview/run share slice A's 40 heavy reads a minute per workspace.
- Rejected: storing computed numbers on the report (that is what runs are for); per-role caches.

## What's involved
Models `app/db/models/analytics.py`; migration `alembic/versions/0108_analytics_reports.py`; services `app/services/analytics/{reports,runs}.py`, `catalog.py` (roles, templates), `service.resolve(visible=)`; routes `app/api/v1/endpoints/analytics_reports.py`; worker `app/worker/handlers/analytics.py`; notifications `registry.py`/`audience.py`. Added beyond the spec's endpoint list: `GET /reports/{id}/runs/{run_id}` (reading a run).

## Verification
`tests/api/test_analytics_reports.py` (role matrix, tenancy 404s, 409, preview per role, frozen immutability, share scope, redaction, lost-access run, notifications), `tests/test_analytics_reports_migration.py` (one head, round trip), live `e2e/test_analytics_reports.py` with captures in `e2e/_captures/analytics_reports/`.

## Operate / roll back
The worker must import `app.worker.handlers.analytics` (done in `app/worker/__main__.py`). Roll back with `alembic downgrade 0106_legal` (drops three tables; runs and shares are lost).

## Follow-ups
Restore of a soft-deleted report; Preferences row for the new kinds (in-app only today); Analytics grant for team members (no grant system); purge of runs older than 90 days (slice C); product calls for Adebayo listed in the PR.


**Review fixes (Opus, 2026-11-13):** starting a run is founder/team member only (readers read existing runs, redacted; they cannot burn the run rate limit); share inserts use ON CONFLICT DO NOTHING so two concurrent saves cannot 500 on `uq_analytics_report_share`.
