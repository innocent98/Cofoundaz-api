# SOP: Analytics & Reports, slice C (exports)

| | |
|---|---|
| **What shipped** | Exports, which close wave 1 of Module 22. A report can be exported as PDF or XLSX, and catalog metrics as data tables (XLSX or CSV). Each file is rendered once from a frozen run, kept for 30 days, and listed in export history. The download is authorised and audited on every request. A daily purge deletes expired files, and an "export ready" notification goes to the requester. Branch `feature/analytics-exports`, migration `0109_analytics_exports` (on `0108_analytics_reports`). PR to `develop`, not merged. |
| **Date** | 2026-11-13 |
| **Design** | `cofoundaz/docs/superpowers/specs/2026-11-12-analytics-reports-design.md` sections 3, 4, 6, 7 (Exports), 8, 10; gap list rows 9, 43-48, 58, 66-68, 74 |

## Why
Founders need the report on paper: a PDF for the board, and a spreadsheet for the accountant. The mock's
export buttons only showed toasts. The design requires files to come from frozen runs (D-ANA-3 c, L5), to
respect per-metric authorisation (D-ANA-4), and to expire at 30 days.

## How (and decisions)
- **Render from the run, never re-query.** `POST /reports/{id}/export` creates a pending run (`triggered_by="export"`) without queuing a run job. The single `analytics.export.render` job executes the run (row-locked `execute_run`, shared with the run job, so it is computed once) and then renders the file. Data tables create a report-less run (`triggered_by="data_tables"`, `report_id` null), so no report route can ever read it.
- **Redaction happens before rendering.** The renderer only receives `runs.view_snapshot(..., requester_role)`, slice B's read-time redaction. `render.rows_from` also fails closed: a locked stub, a `locked` result or an unknown shape becomes a row with no value. So locked blocks reach the file as "Locked: X access needed" and never with a number.
- **Downloads are re-checked.** Each export records `hubs`, the hubs whose numbers are in the file. Every download re-checks the downloader's current role against them (`report_can_see`), so a role change after the export still cuts access (403).
- **Roles** (spec section 3, Exports column): founder sees all; team member and accountant see their own; mentor and business consultant get 403 (they read shared reports on screen only); other roles get 403. Someone else's export, or another workspace's, is a 404.
- **The accountant cannot start runs** (slice B's review fix). They export an existing finished run with `run_id`, and the file is redacted to Finance. Without `run_id` they get 403 with an explanation.
- **Data tables** accept only metrics the requester can see. An invisible metric gets the same 422 message as a non-existent one, so the route cannot be used to probe custom metric names.
- **File safety.** XLSX uses the house `finance/xlsx.py` writer (text is always inline, never a formula; quote-prefix style). CSV prefixes `= + - @ \t \r` cells with `'` and starts with a UTF-8 BOM. PDF text is escaped for reportlab's markup and reduced to cp1252 (`documents/export._pdf_text`). Filenames are sanitised, and `Content-Disposition` uses an ASCII fallback plus `filename*`.
- **Private storage only.** The file is saved with `Storage.save_private` and streamed through `read_private` after the check, with `Cache-Control: private, no-store`, `nosniff` and a sandbox CSP. No URL is ever returned (spec gap 47).
- **One notification.** A report export notifies "Your export is ready: {name}" (event `analytics.export.created`) and does **not** also send "Your report is ready" (`runs.SILENT_TRIGGERS`).
- **Product event `report_exported`** (D-ANA-L11): there is no product-analytics pipeline in the API, so `format` and `artifact` ride on the `analytics.export.created` domain event.
- **Money in files is in major units** (5000.00), with the currency in its own column. The JSON APIs stay in minor units. Spreadsheets are for people.
- Rejected: Cloudinary signed-URL redirects for downloads. Streaming keeps one authorisation point and no URL that could be forwarded. Also rejected: CSV for reports (the spec says one table → data tables only); `document_id` on exports (decks and updates are waves 2-3).

## What's involved
| Area | Path |
|---|---|
| Model | `app/db/models/analytics.py` (`AnalyticsExport`), `app/db/models/__init__.py` |
| Migration | `alembic/versions/0109_analytics_exports.py` |
| Service | `app/services/analytics/exports.py` (create, render, download check, list, purge) |
| Renderer | `app/services/analytics/render.py` (PDF, XLSX, CSV) |
| Runs | `app/services/analytics/runs.py` (`start_run(enqueue=False)`, `start_table_run`, `execute_run` with row lock, `SILENT_TRIGGERS`) |
| Roles | `app/services/analytics/catalog.py` (`EXPORT_ROLES`) |
| Routes | `app/api/v1/endpoints/analytics_exports.py`, registered in `app/api/v1/api.py` |
| Jobs | `app/worker/handlers/analytics.py` (render + purge), `app/worker/scheduler.py` (`scheduled.analytics.purge`, daily) |
| Notifications | `registry.py` + `audience.py` (`analytics.export.created`, `EXPORT_ROLES`) |

Endpoints: `POST /analytics/reports/{id}/export`, `POST /analytics/exports/data-tables`, `GET /analytics/exports`, `GET /analytics/exports/{id}`, `GET /analytics/exports/{id}/download`.

## Verification
- `tests/api/test_analytics_exports.py` (12 tests): role × route matrix, tenancy and ownership 404s, a planted marketing number absent from the accountant's XLSX and PDF (and present in the founder's control file), download re-check after a role change, 409 pending / failed / run not ready, 410 after expiry, purge (file deleted, row kept, 90-day runs deleted, idempotent), lost access → failed, double export shares one run, handlers and schedule wired.
- `tests/services/test_analytics_export_render.py`: fail-closed flattening, CSV formula guard, no `<f>` in XLSX, PDF escaping, money formatting.
- `tests/test_analytics_exports_migration.py`: one head, round trip. Run by hand on a scratch DB: upgrade → downgrade 0108 → upgrade, and `alembic check` reports no drift.
- Live `e2e/test_analytics_exports.py` against a real uvicorn server on scratch Postgres and Redis. Captures are in `e2e/_captures/analytics_exports/`, and the FE guide `docs/fe-integration-guide-analytics-exports.md` is built from them.

## Operate / roll back
The worker already imports `app.worker.handlers.analytics`; the render and purge handlers register there. The purge is claimed once per local day through `ScheduledRun` (`analytics.purge`/`all`/date). Files live under `analytics/exports/<startup>/<export>.<ext>` in private storage. Roll back with `alembic downgrade 0108_analytics_reports`. That drops `analytics_exports`, and the stored files stay in storage under that prefix (delete them by prefix if needed).

## Follow-ups
- Notification `module` is `"other"` for every Analytics kind (no `analytics` prefix in `notifications/categories.py`), and there is no Preferences row for them yet.
- Cloudinary download path not exercised in tests (local storage only); first staging export should be checked.
- Workspace timezone (gap 72): periods and the "as of" stamp are UTC.
- Overview "Export report" button (gap 58): the FE can save the Overview as a report and export it; there is no dedicated Overview export endpoint.
- Charts in the PDF are tables (vector charts come with decks, wave 3).
