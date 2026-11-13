# FE integration guide: Analytics & Reports, slice C (exports)

Every body below is pasted from a real run of `e2e/test_analytics_exports.py` against a live server
(captures in `e2e/_captures/analytics_exports/`). IDs, names and timestamps come from that run and are
**placeholders**. All routes are under `/api/v1/analytics`, with the usual `Authorization` +
`X-Workspace-Id` headers. JSON routes use the `{data}` / `{error}` envelope. **The download route returns
the file itself, not JSON.** Slice B's guide (`fe-integration-guide-analytics-reports.md`) covers reports,
runs and shares. Slice A's guide (`fe-integration-guide-analytics.md`) covers the catalog and the Overview.

## 1. What it is, in the founder's terms

"Give me that report as a PDF or a spreadsheet, and let me find it again next week." Two things can be
exported:

- **A report** (`artifact_type: "report"`), as **PDF** or **XLSX**. Exporting makes a new frozen run of
  the report and renders the file from it. You can also export an existing finished run by passing `run_id`.
- **Data tables** (`artifact_type: "data_tables"`): pick catalog metrics and a range, and get **XLSX**
  (a Summary sheet, one sheet per metric series, and an "About" sheet) or **CSV** (one table: the Summary).
  This replaces the mock's "Export" tab. There is no JSON export (D-ANA-L12).

The file is made by a background job, so a new export starts as `pending` and becomes `ready` (or `failed`).
Files are kept **30 days**. After that the row stays in history as `expired` ("Expired. Generate again").

The numbers in a file are **frozen**: they are the run's numbers, never re-queried, so the PDF, the XLSX and
the run on screen always agree. The PDF header and the XLSX "About" sheet carry
`numbers as of <time> UTC`. Show that same stamp in the UI.

## 2. Who can do what

| Role | Export a report | Export data tables | See in history / download |
|---|---|---|---|
| Founder | yes (new run, or `run_id`) | yes, any metric | **every** export in the workspace |
| Team member | yes for reports they can see (new run, or `run_id`) | yes, metrics of hubs they can open | **their own** |
| Accountant | only with `run_id` of a finished run of a report shared with them (they cannot start runs). The file is redacted to Finance | Finance metrics only | **their own** |
| Mentor, business consultant | **403** (they read shared reports on screen only, never as a file) | 403 | 403 |
| Investor, legal advisor | 403 | 403 | 403 |

Someone else's export, or one from another workspace, is a **404** (`Export not found.`), never a 403.
TM access follows hub roles; per-member grants aren't modelled platform-wide yet.

## 3. Locked blocks never reach the file (UX consequence)

A file is rendered under the **requester's** role at render time, with the same per-metric rule as on screen
(D-ANA-4). A block over a hub the requester cannot open has **no number in the file**. The PDF shows a grey
box "Locked. Marketing access is needed to see this." and the XLSX/CSV Summary has a row whose Status is
`Locked: Marketing access needed` and whose value cells are empty.

Verified live: the accountant exported the founder's run of a report holding Revenue (finance) and Clicks
(marketing, value 4321). The accountant's XLSX had no `4321` anywhere, and it had
`Locked: Marketing access needed` and Revenue `5000.00`.

**FE must** tell an accountant before they export: "Blocks outside Finance will be locked in the file."
The report's `locked_hubs` (slice B) tells you which hubs.

**Role changes after the export:** every download re-checks the downloader's **current** role against the
hubs whose numbers are in the file. A team member who exported Marketing numbers and is later changed to an
accountant gets **403** on that download (unit-tested: `test_download_rechecks_role_after_export`).

## 4. Endpoints

### 4.1 `GET /exports` — export history

Query: `artifact_type` = `report` | `data_tables` (optional), `limit` 1-100 (default 50), `offset`. Newest first.

`list_empty.json`:
```json
{
  "data": {
    "exports": [],
    "total": 0,
    "limit": 50,
    "offset": 0,
    "retention_days": 30
  },
  "meta": null
}
```

The founder's list after the journey (`list_founder.json`) has 5 rows. Two are the team member's and the
accountant's (each shows `created_by.name` "Member" because those test users never set a name). The team
member's own list (`list_team_member.json`, `?artifact_type=data_tables`) has only their one export.
Each row has the same shape as 4.2.

### 4.2 `GET /exports/{id}` — one export (poll this while `pending`)

`report_pdf_ready.json`:
```json
{
  "data": {
    "id": "3d735a2c-c0b4-4e11-9ded-593f451909ec",
    "name": "Board pack",
    "artifact_type": "report",
    "format": "pdf",
    "status": "ready",
    "report_id": "4c077691-ad9b-4d89-8650-6458b0792c87",
    "run_id": "41927536-2aca-4687-97d1-35e66bd0463c",
    "period": {
      "to": "2026-11-13",
      "from": "2026-10-15"
    },
    "metrics": null,
    "filename": "Board pack 2026-10-15 to 2026-11-13.pdf",
    "size_bytes": 2202,
    "download_count": 0,
    "download_path": "/api/v1/analytics/exports/3d735a2c-c0b4-4e11-9ded-593f451909ec/download",
    "error": null,
    "created_by": {
      "id": "09061ddd-1de2-42a9-b61d-3b074b80a7cf",
      "name": "Ada F"
    },
    "created_at": "2026-11-13T20:28:35.131924+00:00",
    "ready_at": "2026-11-13T20:28:36.593708+00:00",
    "expires_at": "2026-12-13T20:28:35.141301+00:00"
  },
  "meta": null
}
```

A data-tables export (`tables_csv_ready.json`) differs only in `artifact_type: "data_tables"`,
`report_id: null`, `name: "Data tables"`, and `metrics` (the keys asked for, de-duplicated, in order):
```json
    "metrics": [
      "finance.revenue",
      "marketing.clicks"
    ],
    "filename": "Data tables 2026-10-15 to 2026-11-13.csv",
    "size_bytes": 513,
```

An export that has expired (`get_expired.json`). `filename`, `size_bytes` and `download_path` go **null**, and
the other fields stay:
```json
{
  "data": {
    "id": "3d735a2c-c0b4-4e11-9ded-593f451909ec",
    "name": "Board pack",
    "artifact_type": "report",
    "format": "pdf",
    "status": "expired",
    "report_id": "4c077691-ad9b-4d89-8650-6458b0792c87",
    "run_id": "41927536-2aca-4687-97d1-35e66bd0463c",
    "period": {
      "to": "2026-11-13",
      "from": "2026-10-15"
    },
    "metrics": null,
    "filename": null,
    "size_bytes": null,
    "download_count": 1,
    "download_path": null,
    "error": null,
    "created_by": {
      "id": "09061ddd-1de2-42a9-b61d-3b074b80a7cf",
      "name": "Ada F"
    },
    "created_at": "2026-11-13T20:28:35.131924+00:00",
    "ready_at": "2026-11-13T20:28:36.593708+00:00",
    "expires_at": "2026-11-13T20:27:37.112999+00:00"
  },
  "meta": null
}
```
(Its `expires_at` is in the past because the journey back-dated it. See section 7.)

`get_not_yours_404.json` (a team member asking for the founder's export):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Export not found.",
    "field_errors": []
  }
}
```

### 4.3 `POST /reports/{report_id}/export` — export a report (202)

Body: `{"format": "pdf" | "xlsx", "run_id"?: "<uuid of a ready run of THIS report>"}`. Without `run_id` a new
frozen run is made (founder and team member only). With `run_id` the existing run is rendered.

`report_pdf_start.json`:
```json
{
  "data": {
    "id": "3d735a2c-c0b4-4e11-9ded-593f451909ec",
    "name": "Board pack",
    "artifact_type": "report",
    "format": "pdf",
    "status": "pending",
    "report_id": "4c077691-ad9b-4d89-8650-6458b0792c87",
    "run_id": "41927536-2aca-4687-97d1-35e66bd0463c",
    "period": {
      "to": "2026-11-13",
      "from": "2026-10-15"
    },
    "metrics": null,
    "filename": null,
    "size_bytes": null,
    "download_count": 0,
    "download_path": null,
    "error": null,
    "created_by": {
      "id": "09061ddd-1de2-42a9-b61d-3b074b80a7cf",
      "name": "Ada F"
    },
    "created_at": "2026-11-13T20:28:35.131924+00:00",
    "ready_at": null,
    "expires_at": "2026-12-13T20:28:35.141301+00:00"
  },
  "meta": null
}
```

Errors (all captured):

`report_csv_422.json` (CSV is for data tables only):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "format",
        "message": "Input should be 'pdf' or 'xlsx'"
      }
    ]
  }
}
```

`accountant_new_run_403.json` (accountant without `run_id`):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Only the founder or a team member can run a report. Export one of its finished runs instead.",
    "field_errors": []
  }
}
```

`mentor_export_403.json` (mentor, even with a `run_id` of a report shared with them):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Not captured live, unit-tested only (`tests/api/test_analytics_exports.py`):
- A report you cannot see → 404 `Report not found.`.
- A `run_id` that belongs to a different report → 404 `Run not found.`.
- A `run_id` that is not `ready` yet → **409** `RUN_NOT_READY` "Pick a run that has finished.".
- 429 `RATE_LIMITED` "Too many report requests. Try again in a minute." is shared with preview and run
  (40 heavy reads per workspace per minute). Shape from slice A's code, not captured here.

Double-click safety: two report exports by the same person within 10 minutes share **one** pending run
(`run_id` is the same). Each still gets its own export row (unit-tested). An export never reuses a pending
manual run from `POST /reports/{id}/run`, so the requester gets only "Your export is ready", not also
"Your report is ready".

### 4.4 `POST /exports/data-tables` — export data tables (202)

Body:
```json
{"metrics": ["finance.revenue", "marketing.clicks"], "range": {"range": "30d"}, "format": "csv", "include_metadata": true}
```
- `metrics`: 1-24 catalog keys (duplicates dropped). Every key must be one the requester can see.
- `range` is optional (default `{"range": "30d", "compare": "previous"}`). Its shape is the slice B
  `default_range` (`range`, `from`, `to`, `compare`).
- `format`: `xlsx` or `csv`. `include_metadata` (default `true`): XLSX adds an "About" sheet, and CSV
  adds a short key/value header block, then a blank line, then the table.

The 202 response (`tables_csv_start.json`) has the same shape as 4.3, with `artifact_type: "data_tables"`.

Errors. `tables_unknown_metric_422.json` and `tables_accountant_marketing_422.json` are **identical** on
purpose: a metric you cannot see answers the same as one that does not exist.
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "One or more metrics are not available to you.",
    "field_errors": [
      {
        "field": "metrics",
        "message": "One or more metrics are not available to you."
      }
    ]
  }
}
```
`tables_pdf_422.json`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "format",
        "message": "Input should be 'xlsx' or 'csv'"
      }
    ]
  }
}
```
**FE must** build the metric picker from `GET /metrics-catalog` (slice A). It already lists only the hubs
the viewer can open, so the 422 should never show in normal use.

### 4.5 `GET /exports/{id}/download` — the file

**Not JSON.** Call it with `fetch` and the auth headers, read the body as a Blob, and save it under the
filename from `Content-Disposition`. A plain `<a href>` will not work, because the route needs the bearer
token. There is no public or signed URL for these files.

`download_pdf.json` (what the response looked like; the capture records the headers and the size, not the bytes):
```json
{
  "status_code": 200,
  "headers": {
    "content-type": "application/pdf",
    "content-disposition": "attachment; filename=\"Board pack 2026-10-15 to 2026-11-13.pdf\"; filename*=UTF-8''Board%20pack%202026-10-15%20to%202026-11-13.pdf",
    "cache-control": "private, no-store",
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox; default-src 'none'"
  },
  "size_bytes": 2202,
  "first_bytes": "%PDF-1.4"
}
```
XLSX: `content-type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (`download_xlsx.json`).
CSV: `content-type: text/csv; charset=utf-8`. The body starts with a UTF-8 BOM, so Excel shows `₦` and
accented names correctly. The full CSV from the run (`download_csv.json`, `text`):
```
Workspace,NovaPay
Report,Data tables
Period from,2026-10-15
Period to,2026-11-13
Numbers as of,2026-11-13 20:28 UTC
Generated by,Ada F
Money,"Major units (for example naira, not kobo); currency in its own column."
Note,Frozen at the time of the run. Locked rows hold no numbers by design.

Metric,Hub,Value,Unit,Currency,Previous period,Change,Change %,Status,From,To
Revenue,Finance,5000.00,money,NGN,0.00,5000.00,,OK,2026-10-15,2026-11-13
Clicks,Marketing,4321,count,,,,,OK,2026-10-15,2026-11-13
```
**Field trap: money in files is in MAJOR units** (5000.00 = ₦5,000.00), unlike every JSON API in
Analytics, which returns **minor** units (500000). The file is for humans and spreadsheets. The FE never
parses it, but don't describe the file as "kobo".

Download errors:

`download_pending_409.json`:
```json
{
  "error": {
    "code": "EXPORT_NOT_READY",
    "message": "This export is still being generated.",
    "field_errors": []
  }
}
```
`download_expired_410.json`:
```json
{
  "error": {
    "code": "EXPORT_EXPIRED",
    "message": "This export expired. Generate it again.",
    "field_errors": []
  }
}
```
Unit-tested only:
- A failed export → 409 `EXPORT_FAILED` "This export failed. Generate it again.".
- The downloader's current role cannot see a hub in the file → 403 `FORBIDDEN`.
- Someone else's export, or another workspace's → 404 `Export not found.`.
- More than 60 downloads a minute → 429 (from the shared limiter; not captured).

Every successful download writes an audit row (`analytics.export.downloaded`: actor, workspace, export id,
format, artifact, size, IP) and increments `download_count`.

## 5. Notification (to the requester only)

When the file is ready, the requester (and nobody else) gets one notification. A report export does **not**
also send "Your report is ready". From `notifications_founder.json` (one item of the list):
```json
{
  "id": "b82784c8-01f1-4246-8253-df1af745d902",
  "type": "analytics.export.created",
  "title": "Your export is ready: Data tables",
  "body": "Download",
  "data": {
    "format": "csv",
    "artifact": "data_tables",
    "user_ids": [
      "09061ddd-1de2-42a9-b61d-3b074b80a7cf"
    ],
    "export_id": "77b1051c-b52b-4ac5-bcf3-e714de7d0861",
    "report_id": null,
    "startup_id": "aa0dbd5c-a578-4be0-87b0-2cbac7fa2ddd",
    "export_name": "Data tables"
  },
  "read": false,
  "archived": false,
  "priority": "normal",
  "module": "other",
  "created_at": "2026-11-13T20:28:36.912831+00:00"
}
```
CTA "Download": go to `/analytics-reports/exports` and highlight `data.export_id` (or download it directly).
`module` is `"other"` for every Analytics kind today (slice B's too). See Follow-ups.

## 6. States and polling

| `status` | Meaning | FE shows |
|---|---|---|
| `pending` | job queued or rendering | spinner "Generating…"; poll `GET /exports/{id}` every 2 s, back off to 5 s, stop after 2 min and say "Still working, check Export history" |
| `ready` | file stored | Download button (uses `download_path`) and "Expires {expires_at}" |
| `failed` | render failed (including "The report could not be run. Try again." when a hub's data could not be read); `error` holds a plain sentence (for example "You no longer have access to exports in this workspace.") | the `error` text and "Generate again" |
| `expired` | past `expires_at`, or the file was purged | "Expired. Generate again" (re-export the report, or re-run the data tables) |

`status` is computed on read: a row past `expires_at` says `expired` even before the daily purge has run.
In the live run, rendering took about 1.5 s (`created_at` → `ready_at`).

## 7. What runs in the background

- `analytics.export.render` (job, one per export). It runs the frozen run first if that is still pending
  (row-locked, so it is computed once), then renders under the requester's current role, stores the file
  in private storage (`analytics/exports/<workspace>/<export>.<ext>`), marks the row ready and notifies.
  If the requester lost access before the job ran, the export is `failed` and the run is never resolved for them.
- `scheduled.analytics.purge` (daily, once per local day for all workspaces). It deletes files past 30 days
  (the row stays, `status: expired`) and deletes report runs older than 90 days (`run_id` on old exports
  becomes `null`). **Verified live** by back-dating one export's `expires_at` and calling the purge
  in-process. The 30-day timer itself was not waited out.

## 8. Verification table

| Behaviour | How verified |
|---|---|
| Report export PDF / XLSX: 202 pending → ready → download (headers, `%PDF`) | **live** (e2e captures) |
| Download 409 while pending | **live** |
| Accountant: 403 without `run_id`; export of founder run redacted (no marketing number, lock row present) | **live** + unit |
| Mentor 403 on export; investor 403 on history | **live**; full role × route matrix in unit tests |
| Data tables CSV (BOM, metadata block, table) and XLSX | **live** |
| 422s: report CSV, data-tables PDF, unknown metric = invisible metric (same message) | **live** |
| History: founder all, team member own, 404 on someone else's | **live** |
| "Your export is ready" notification to the requester only, no duplicate "report ready" | **live** + unit |
| 410 after expiry; purge deletes file, keeps row as `expired` | **live** with a back-dated `expires_at` (no 30-day wait) + unit |
| Download re-checks role after a role change (403) | unit only |
| Tenancy: another workspace's export / report / run → 404 | unit only |
| `RUN_NOT_READY` 409, `EXPORT_FAILED` 409 | unit only |
| 429 rate limits (heavy reads, downloads) | code only, not captured (shared limiters) |
| Formula injection: text never a formula in XLSX, `'` prefix in CSV; markup escaped in PDF | unit (`tests/services/test_analytics_export_render.py`) |
| Cloudinary storage backend (prod) | **not exercised**: local storage in tests and e2e. The route reads through `Storage.read_private`, which Cloudinary implements with a 60 s signed fetch |
