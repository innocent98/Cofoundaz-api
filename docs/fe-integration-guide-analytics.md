# FE integration guide: Analytics & Reports, slice A (catalog, Overview, By hub, custom metrics)

Every body below is copied from a real response captured by `e2e/test_analytics_slice_a.py` against the running app
(files in `e2e/_captures/analytics_slice_a/`, named in brackets). Long arrays are trimmed with `...`; nothing is retyped.
Ids and emails in examples are placeholders. All money is **integer minor units (kobo)**, currency `NGN`.
Base path `/api/v1/analytics`, JSON envelope `{ "data": ..., "meta": null }`, errors `{ "error": { code, message, field_errors } }`.
Headers as everywhere: `Authorization: Bearer <access>` and `X-Workspace-Id: <workspace>`.

## 1. What this is, in the founder's terms
"How is the business doing, in numbers I can trust?" Slice A is the live half of Analytics: the **Overview** (four headline cards, a
chart, "Working / Watch" notes), **By hub** (tiles per hub), a **metric catalog** (what a report can use later), and **your own metrics**
(users, MRR, anything the platform cannot see). Reports, exports, schedules, investor updates and decks are later slices; the FE route is
`/analytics-reports` (the PRD's `/app/analytics`).

## 2. Who can call what
| Role | Catalog / Overview / By hub / Resolve | Custom metrics |
|---|---|---|
| Founder, Team member | every hub | read + write |
| Accountant | **finance only** (other hubs locked) | 403 |
| Mentor, Business consultant, Investor, Legal advisor | 403 on every route (mentors/consultants will get *shared reports* in slice B, never the live catalog) | 403 |

Authorisation is **per metric**: a locked metric's number is never computed or sent. 402 `PLAN_REQUIRED` applies when plan enforcement is on
(`analytics` is a Growth module). Plan gate is **not** exercised live (enforcement is off in e2e).

## 3. Endpoints
| Method + path | Purpose |
|---|---|
| `GET /metrics-catalog` | the picker, grouped by hub |
| `GET /overview?range=&from=&to=&compare=` | headline cards, chart, notes |
| `GET /by-hub?hub=&range=&from=&to=&compare=` | tiles per visible hub (or one) |
| `POST /metrics/resolve` | batch of up to 24 metric specs |
| `GET/POST /custom-metrics`, `PUT /custom-metrics/{id}/points`, `DELETE /custom-metrics/{id}` | your own metrics |

**Periods.** `range` = `7d | 30d | 90d | 12m | this_month | last_month | custom` (default `30d`); `custom` needs `from` and `to` (`YYYY-MM-DD`,
max 24 months, `from` no earlier than 2000-01-01 and `to` no more than a year from today). Days are **UTC calendar days** (workspaces have no stored timezone yet). `compare` = `none | previous | year`
(Overview and By hub default `previous`; resolve defaults `none`).

### 3.1 Metric object (Overview cards, tiles, resolve results) [`overview_founder`, `resolve_accountant`]
```json
{
  "key": "finance.revenue", "label": "Revenue", "unit": "money", "hub": "finance", "kind": "flow", "good": "up",
  "source_note": "Finance ledger",
  "definition": "Money in from customers over the period. Funding and payout transfers are excluded.",
  "currency": "NGN", "status": "ok", "value": 500000,
  "period": { "from": "2026-10-14", "to": "2026-11-12" },
  "as_of": "2026-11-12T18:36:27.398582+00:00", "hub_path": "/finance",
  "compare": { "period": { "from": "2026-09-14", "to": "2026-10-13" }, "value": 100000, "delta": 400000, "delta_pct": 400.0 },
  "series": [ { "from": "2026-10-14", "to": "2026-10-20", "value": 0 }, ..., { "from": "2026-11-04", "to": "2026-11-10", "value": 500000 } ]
}
```
- `unit`: `money` (kobo) · `count` · `percent` · `months` · `days` · `score`. `kind`: `flow` (summed over the period) · `point` (value as of period end).
- `good`: `up | down` tells you which direction is good news for the delta colour (burn, expenses and cycle days are `down`).
- `series` is at most 12 buckets (days, weeks or months), only when asked (`series: true` / on Overview cards); `[]` when there is no data.
  For a **point** metric the series comes from nightly snapshots, so it is `[]` until a snapshot exists.
- `as_of` is when the number was computed. **Numbers are cached for 5 minutes**: show "as of {time}" and do not expect an edit in Finance
  to appear instantly. A `no_data` answer is cached for only **30 seconds**, so the first data a founder adds shows within half a minute.

### 3.2 `status` values (render each differently)
| status | Meaning | Show |
|---|---|---|
| `ok` | a real figure (may be 0) | the number |
| `no_data` | nothing recorded yet [`overview_empty`: every card `value: null`] | "No data yet" + link to `hub_path` |
| `not_burning` | runway only: profitable or flat, nothing to count down [`overview_founder`] | "Not burning cash", no number |
| `out_of_cash` | runway only: burning with no cash, `value: 0` | warning state |
| `locked` | the viewer's role cannot open this hub: **no value, no label detail** [`resolve_accountant`] | lock icon + "{needs_label} access needed" |
| `unknown` | a saved key that no longer exists (e.g. a deleted custom metric), **or a custom metric the viewer's role cannot read** (an accountant asking for `custom.active_users` gets this, with no label, so a key's existence is never revealed) [`resolve_custom_accountant`] | "Metric no longer available" |

Locked and unknown shapes are small; do not read `value` from them:
```json
{ "key": "sales.pipeline_value", "label": "Open pipeline", "hub": "sales", "status": "locked", "needs": "sales", "needs_label": "Sales" }
{ "key": "custom.gone", "status": "unknown", "message": "Metric no longer available." }
```

### 3.3 `GET /overview` [`overview_founder`, `overview_accountant`, `overview_empty`]
```json
{ "data": { "period": {"from":"2026-10-14","to":"2026-11-12"}, "compare_period": {"from":"2026-09-14","to":"2026-10-13"}, "currency": "NGN",
  "cards": [ <metric>, ... ], "chart": { "series": [ <metric>, <metric> ] } | null,
  "insights": [ { "kind": "working", "text": "Revenue grew 400% against the comparison period.", "source": "finance.revenue", "hub_path": "/finance" },
               { "kind": "working", "text": "Burn fell 100% while revenue grew 400%.", "source": "finance.net_burn", "hub_path": "/finance" } ],
  "hubs": [ { "hub": "finance", "label": "Finance", "path": "/finance" }, ... ] } }
```
- Founder card order: `finance.revenue`, `finance.net_burn`, `finance.runway_months`, `sales.pipeline_value`, then the first custom metric if any.
  Cards for hubs the viewer cannot open are **omitted** (accountant: three finance cards, `hubs` = finance only).
- `chart.series` = two metrics to draw together (revenue + expenses for finance viewers); `null` when none is visible. Empty workspaces still
  return the series objects with `status: "no_data"`, so check each.
- `insights` is `[]` on an empty workspace. `kind` is `working` or `watch`; every note names its `source` metric. Wording is rule-based, no AI.
- `hubs` lists the hubs the viewer can open (use it for the By hub filter pills).

### 3.4 `GET /by-hub` [`by_hub_marketing`]
```json
{ "data": { "period": {...}, "compare_period": {...},
  "hubs": [ { "hub": "marketing", "label": "Marketing", "path": "/marketing",
    "tiles": [ { "key": "marketing.impressions", "status": "ok", "value": 2000, "unit": "count", "compare": { ..., "value": null, "delta": null, "delta_pct": null }, ... },
               { "key": "marketing.ctr", "status": "ok", "value": 2.5, "unit": "percent", ... } ] } ] } }
```
Without `hub` you get every hub the viewer can open, in order finance, sales, marketing, validation, health, execution, funding, custom.
`hub=<hub>` for a hub the viewer cannot open is **403** (see §5); an unknown hub is 422.

### 3.5 `GET /metrics-catalog` [`catalog_founder`, `catalog_accountant`]
```json
{ "data": { "currency": "NGN", "hubs": [
  { "hub": "finance", "label": "Finance", "locked": false, "metrics": [
    { "key": "finance.revenue", "label": "Revenue", "unit": "money", "hub": "finance", "kind": "flow",
      "definition": "Money in from customers over the period. Funding and payout transfers are excluded.",
      "source_note": "Finance ledger", "good": "up", "group_by": [], "entered_by_you": false }, ... ] },
  { "hub": "sales", "label": "Sales", "locked": true, "metrics": [] }, ... ] } }
```
Founder: finance 5, sales 5, marketing 4, validation 1, health 1, execution 2, funding 1, custom (your own) metrics. Accountant: finance open,
every other hub `locked: true` with `metrics: []`. `group_by` is empty in slice A. Custom entries have `entered_by_you: true` (label them "Entered by you").

### 3.6 `POST /metrics/resolve` [`resolve_founder`, `resolve_accountant`, `resolve_custom`]
Request `{ "metrics": [ { "key": "finance.revenue", "range": "30d", "from": null, "to": null, "compare": "year", "series": true }, ... ] }`
(1 to 24 specs; unknown fields are rejected 422). Response `{ "data": { "metrics": [ <metric | locked | unknown>, ... ] } }`, same order as asked.

### 3.7 Custom metrics [`custom_create`, `custom_points`, `custom_list`]
`POST /custom-metrics` `{ "label": "Active users", "unit": "count" }` (`unit` = `count | money | percent`) -> **201**
```json
{ "data": { "id": "2718193c-...-placeholder", "key": "custom.active_users", "label": "Active users", "unit": "count", "currency": null, "points": [] }, "meta": null }
```
`PUT /custom-metrics/{id}/points` `{ "points": [ { "period": "2026-09", "value": 120 }, { "period": "2026-10", "value": 180 } ] }` -> 200, returns the
metric with **all** its points (upsert per month; `period` is stored as the first of the month):
`"points": [ { "period": "2026-09-01", "value": 120 }, { "period": "2026-10-01", "value": 180 } ]`.
`GET /custom-metrics` -> `{ "data": { "metrics": [ <metric with points (last 24)> ] } }`. `DELETE /custom-metrics/{id}` -> `{ "data": { "deleted": true } }`.
Rules: up to 20 metrics per workspace and 36 points per call; `count` and `money` take whole numbers (money in kobo); no negatives except `percent`;
months up to the current one. A duplicate label gets the key `custom.active_users_2`. A custom metric resolves with `kind: "point"`:
the latest monthly value on or before the period end [`resolve_custom`: `value: 180`, `compare.value: 120`, `source_note: "Entered by you"`].

## 4. Field-nesting and null traps
- `compare.value`, `delta`, `delta_pct` are **present but null** when there is no value in the comparison period (not absent). `delta_pct` is also
  null when the comparison value is 0 [`resolve_accountant`: `value: 0`, `delta: 500000`, `delta_pct: null`]: show the delta, not a percent.
- `value: null` with `status: "no_data"` is not zero. A flow metric on a workspace that has a ledger but nothing in a bucket returns **0** in `series`.
- `locked` and `unknown` objects have no `unit`, `period` or `value`.
- `series[].value` can be `null` for a bucket with no source data (marketing, deals).
- `compare` is `null` (and `compare_period` null) when the comparison period would start before 2000-01-01.
- `currency` is `"NGN"` for money metrics and `null` otherwise.

## 5. Errors [captures `*_403`, `*_422`]
| Case | Status | Body |
|---|---|---|
| Mentor, consultant, investor, legal advisor on any route; accountant on custom metrics; accountant on `by-hub?hub=sales` | 403 | `{ "error": { "code": "FORBIDDEN", "message": "You don't have permission to do that.", "field_errors": [] } }` |
| Bad `range` | 422 | `{ "error": { "code": "VALIDATION_ERROR", "message": "Pick one of: 7d, 30d, 90d, 12m, this_month, last_month, custom.", "field_errors": [ { "field": "range", "message": "..." } ] } }` |
| `custom` range over 24 months | 422 | message `Pick a range of at most 24 months.`, field `to` |
| Bad month in custom points | 422 | message `Use a month like 2026-10.`, field `points.0.period` |
| `to` more than a year ahead [`overview_far_future_422`] | 422 | message `Pick an end date within a year from today.`, field `to` |
| `from` before 2000-01-01 | 422 | message `Pick a start date from 2000-01-01 onwards.`, field `from` (unit-tested; not captured) |
| More than 40 heavy reads (Overview, By hub, resolve) per workspace per minute | 429 | `{ "error": { "code": "RATE_LIMITED", "message": "Too many report requests. Try again in a minute.", ... } }` (unit-tested; not captured). Back off and retry after a minute; do not poll |
| Other workspace's metric id | 404 | `NOT_FOUND`, `Metric not found.` (unit-tested; not captured live) |

Other 422 messages (verified by unit tests, not captured): `The start date must be on or before the end date.` (field `from`), `A custom range needs both from and to.`,
`Pick one of: none, previous, year.` (field `compare`), `Ask for at most 24 metrics at a time.`, `You can keep up to 20 custom metrics.`, `Enter a number.`,
`That number is out of range.`, `Use a whole number.` / `Use whole numbers (money in kobo).`, `That month appears twice.`, `Pick a month up to this one.`.

## 6. UX consequences the FE must surface
1. **"As of" and the 5-minute cache.** Show the `as_of` time on every card; a number can be up to 5 minutes old. Offer no manual "refresh" that implies live.
2. **Funding is not revenue.** Revenue excludes the `Funding` category and Paystack payouts. Say so in the tooltip (use `definition`); a founder who just
   recorded a raise must not expect revenue to jump.
3. **Runway states.** `not_burning` is good news, not missing data; never render it as "No data yet".
4. **Locked is not empty.** An accountant sees locks for non-finance hubs; do not show "no data" for a locked metric.
5. **Entered-by-you numbers** are the founder's own and may be old: show the month and the "Entered by you" note (`source_note`).
6. **Mock vocabulary is gone.** There is no "Active Savers" or invented benchmark cohort; "users" exists only as a custom metric the founder enters.
7. Notes ("Working / Watch") are rule-based and always link to their source; do not present them as AI.

## 7. Verification table
| Behaviour | Status |
|---|---|
| Catalog for founder and accountant; Overview empty, with data, accountant; By hub; resolve (ok, locked, unknown, series, compare); custom create/points/list; 403 and 422 shapes | **verified live** (`e2e/test_analytics_slice_a.py`, 1 journey, captures committed) |
| Team member sees the same hubs and cards as the founder, and can create a custom metric | **verified live** (`catalog_team_member`, `overview_team_member`, `custom_create_team_member`) |
| Mentor / investor 403 on all routes; accountant finance only and custom key not revealed | **verified live** (`overview_mentor_403`, `overview_investor_403`, `resolve_custom_accountant`); legal advisor / consultant 403 unit-tested for every role |
| Cache (hit, invalidate, 30 s no_data TTL), locked-before-cache, 429 rate limit, extreme-date 422s | unit-tested with real Redis; `overview_far_future_422` live; 429 not captured |
| `not_burning` / `out_of_cash` runway | `not_burning` live; `out_of_cash` unit-tested |
| Nightly snapshot (point history, comparison for point metrics, point-metric `series`) | unit-tested; **not exercised live** (the job runs inside the worker at the nightly hour); point-metric `series` is `[]` live until a snapshot exists |
| Sales, Health, Mission, Roadmap, Validation, Funding metric values | resolvers unit-covered for health and mission; sales/validation/funding values not seeded live (they read the same tables their hubs already serve) |
| Plan gate (402) | **not exercised** (PLAN_ENFORCEMENT off) |
| Workspace isolation on custom metrics | unit-tested (404 for another workspace) |
