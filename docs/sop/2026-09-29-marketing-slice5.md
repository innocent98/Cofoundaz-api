# SOP — Module 10 Marketing Hub, Slice 5: Performance Analytics (metrics store, ingestion, analytics aggregation, Overview fills)

**What shipped** — Module 10's Performance Analytics (PRD 10.8), the **final slice**. **This
completes Module 10 (Marketing Hub) — all 5 slices are built.**

1. **`marketing_metrics` store** — a per-workspace time-series of
   `(ts, channel?, campaign_id?, metric, value)` points; new `MarketingMetricName` enum
   (`visits`, `impressions`, `clicks`, `conversions`, `spend`).
2. **Ingestion** — `POST /marketing/metrics`: validated bulk insert (batch cap 1000).
3. **Analytics aggregation** — `GET /marketing/analytics?range=7d|30d|90d`: four widgets —
   `traffic_by_week`, `cac_by_channel`, `funnel`, `leaderboard`.
4. **Overview fills** — `GET /marketing` now returns real values for `top_channel_by_conversions`,
   `ai_content_ideas` and `active_campaigns` (all `null` placeholders since Slice 1).

Migration **`0038_marketing_metrics`** (one new table), 2 new `/marketing` routes behind the existing
`require_role(founder, team_member)` dependency.

Commits (branch `feat/module-10-marketing-slice5`, off Slice 4's merge (PR #105, `4f19b4b`); not yet
merged, no PR opened yet), oldest to newest:
`9de62ed` (design spec) → `e100cd3` (implementation plan) →
`05bc501` (`marketing_metrics` table + enum, migration `0038`) →
`98b76de` (metrics ingestion) →
`d42df91` (fix: bound `value` to int32 + cap batch — 422 not 500) →
`9440a7e` (analytics aggregation) →
`7adec5b` (tests: traffic/leaderboard/range/tenancy; UTC cutoff + tenant-scoped leaderboard join) →
`c949bdc` (Overview fills) →
`6a63054` (e2e analytics journey + 3 captures) → this docs commit.

## Why

Slice 5 of 5 in the Module 10 plan: **Performance Analytics** (PRD 10.8) — the last unbuilt part of
the Marketing Hub, and the reason `Overview.top_channel_by_conversions` and campaign `metrics` had
been placeholders since Slice 1. Before building, the sub-features were cross-checked against the
FE's current UI (the `cofoundaz` handoff) and split:

| Sub-feature | FE status | Consequence |
|---|---|---|
| Analytics read (`traffic_by_week`, `cac_by_channel`, `funnel`, `leaderboard`) | An FE analytics screen exists | **FE-aligned** — read shapes follow the UI's widgets |
| Overview fills | An FE overview strip exists | **FE-aligned** |
| Metrics ingestion (`POST /marketing/metrics`) | **No FE ingestion screen** | **API-first / provisional** — the only way data gets in today (scripts / future ad-platform sync); shape may change |

There is no ad-platform integration yet, so without an ingestion endpoint the analytics screen could
never show data. Ingestion was built API-first rather than deferred so the read side is testable
end to end.

**Deliberately deferred** (per the design spec): analytics **export** (Module 22 — Analytics &
Reports); automatic ingestion from ad platforms; an FE ingestion screen.

## How

**One narrow time-series table, optional dimensions.** `marketing_metrics` holds one row per
`(ts, metric, value)` with two **optional** dimensions: `channel` (a `ChannelKey`) and
`campaign_id` (FK to `campaigns`, `ON DELETE SET NULL`). One shape serves every widget: unscoped
rows feed site traffic, channel-scoped rows feed CAC-by-channel, campaign-scoped rows feed the
leaderboard. `ts` is a `Date` (day granularity — the analytics never needs sub-day resolution),
`value` is an `Integer` (`spend` in **cents**). `metric` and `channel` are stored inline
(`native_enum=False`, lengths 12/20), so no PG enum type to migrate. Index on
`(startup_id, ts)` for the range filter plus single-column indexes on `startup_id`/`campaign_id`.
*Alternative rejected:* per-widget tables, or filling the existing `campaign.metrics` JSONB — the
JSONB is not queryable across campaigns/time, and per-widget tables duplicate the same shape.

**Ingestion: validate everything, then insert.** `MetricsIngest.points` is capped at **1000**
(`max_length=1000`); each `MetricPoint.value` is `ge=0, le=2_147_483_647`. The int32 upper bound and
the batch cap came from fix `d42df91`: an oversized `value` used to pass Pydantic and blow up at
flush as a **500**; the bound makes it a **422**. The service also resolves every referenced
`campaign_id` against the caller's workspace **before inserting anything** — one foreign/unknown id
rejects the whole batch with a 422 (no partial inserts). Ingestion is append-only and additive; there
is no dedupe and no update/delete endpoint (deliberate — see semantics below and Follow-ups).

**Aggregation: range-filtered SQL, not Python loops.** `get_analytics` builds one filtered base query
(`startup_id` + `ts >= cutoff`) and derives the four widgets from it:

- `traffic_by_week` — `visits` rows grouped by `date_trunc('week', ts)` (a Monday in Postgres),
  ordered ascending.
- `cac_by_channel` — channel-scoped rows grouped by channel; `spend` and `conversions` are summed
  with **filtered `case()` sums** (`case((metric == X, value), else_=0)`), so one scan yields both.
  `cac = round(spend / conversions)` or `None` when conversions is 0.
- `funnel` — the same `case()` sums over **all** rows in range (any scope) for
  impressions/clicks/conversions; rates via `_rate()`.
- `leaderboard` — campaign-scoped rows **joined to `Campaign` on both `Campaign.id` and
  `Campaign.startup_id == startup_id`**, grouped by campaign, ordered by conversions descending
  (name as tiebreaker).

**Empty/zero-safe rates.** `_rate(num, den)` returns `round(100 * num / den, 1)` and `0.0` when the
denominator is 0, so an empty workspace yields a zeroed funnel, never a divide-by-zero. Rates are
**percentages to 1 dp** (FE-aligned), `spend`/`cac` are integer cents, `cac` is `null` at 0
conversions.

**UTC cutoff.** `_range_cutoff` is `datetime.now(UTC).date() - timedelta(days=N)` and the filter is
`ts >= cutoff`, so a range covers the last N days **inclusive of today** (`7d` = 8 calendar days) and
"today" is a UTC date, not the viewer's local date. Fixed in `7adec5b` (an earlier cut used local
`date.today()`). Unknown `range` → 422 `VALIDATION_ERROR` on field `range`.

**Tenant-scoped leaderboard join** (`7adec5b`). The campaign join carries `startup_id` explicitly.
`campaign_id` is validated at ingest, but the join is the second line of defence: a metric row that
somehow referenced another workspace's campaign can never surface that campaign's name.

**Overview fills reuse the analytics helpers.** `service.overview` calls
`analytics.top_channel_by_conversions` (channel-scoped `conversions` summed **all-time**, top channel
or `None`), `analytics.count_ai_content_ideas` (sum of `len(output["gaps"])` over the workspace's
**ready** `content_gap` generations from Slice 4) and a direct count of `active` campaigns.
`analytics.py` imports `_validation` from `service.py` and `service.py` needs `analytics`, so the
`analytics` import inside `service.py` is a **function-local import** to break the
service↔analytics import cycle. *Alternative rejected:* moving `_validation` to a shared module —
a wider refactor across all marketing services for one import.

### Data-model semantics worth knowing (documented in the FE guide)

| Semantic | Consequence |
|---|---|
| **Additive, and the funnel sums ALL rows regardless of scope** | Ingesting the same events at two scopes (channel total *and* per-campaign breakdown) **double-counts the funnel**. The e2e sample shows it: `funnel.conversions` 52 vs `clicks` 640 → `conversion_rate` 8.1, because conversions were ingested across three scopes and clicks campaign-only. Ingest each event at **one** scope. `cac_by_channel` (channel-scoped only) and `leaderboard` (campaign-scoped only) are unaffected. |
| `traffic_by_week` omits weeks with no visits | FE must gap-fill for a continuous chart |
| `cac_by_channel` may hold a channel with only some metrics | e.g. spend but no conversions → `cac: null` |
| Range is inclusive of today, in UTC | `7d` spans 8 calendar days |
| `top_channel_by_conversions` is all-time, not range-filtered | Differs from the analytics widgets |
| Campaign `metrics` stays `{}` | FE leaderboard must read `analytics.leaderboard`, not `campaign.metrics` |

## What's involved

**Migration `0038_marketing_metrics`** (`alembic/versions/0038_marketing_metrics.py`, chains off
`0037_seo_tools`, sole alembic head) — one additive `create_table` (`marketing_metrics`) plus indexes
on `startup_id`, `campaign_id` and `(startup_id, ts)`. No existing table touched; reversible
downgrade drops the table.

**Enums** — `app/db/models/enums.py`: new `MarketingMetricName`.

**Models** — `app/db/models/marketing.py`: `MarketingMetric` (`marketing_metrics`; FKs to `startups`
`CASCADE` and `campaigns` `SET NULL`).

**Schemas** — `app/schemas/marketing.py`: `MetricPoint`, `MetricsIngest`, `TrafficPoint`,
`ChannelCac`, `Funnel`, `LeaderboardRow`, `AnalyticsResponse`.

**Service** — `app/services/marketing/analytics.py` (new): `ingest_metrics`, `_range_cutoff`,
`_rate`, `get_analytics`, `top_channel_by_conversions`, `count_ai_content_ideas`.
`app/services/marketing/service.py` (extended): `overview` fills the three fields via a
function-local `analytics` import.

**Endpoints** — `app/api/v1/endpoints/marketing.py` (extended), both behind the existing
`_marketing = require_role(founder, team_member)`:

| Route | Purpose |
|---|---|
| `POST /marketing/metrics` | bulk-ingest metric points → `{"created": n}` |
| `GET /marketing/analytics?range=7d\|30d\|90d` (default `30d`) | the four widgets |
| `GET /marketing` (existing) | now fills `top_channel_by_conversions`, `ai_content_ideas`, `active_campaigns` |

**Tests**
- `tests/db/test_marketing_metric_model.py` + `tests/test_marketing_metrics_migration.py` — enum,
  model persists, single head `0038`, upgrade/downgrade round-trips.
- `tests/api/test_marketing_analytics.py` (17 tests) — bulk ingest; negative / over-int32 /
  foreign-campaign → 422; RBAC 403 on ingest; empty → zeroed; CAC + funnel math; `cac` null at 0
  conversions; bad `range` → 422; traffic-by-week bucketing; leaderboard ordering; out-of-range
  exclusion; tenant scoping; Overview fills (top channel, null when empty, active campaigns only,
  ready-gaps-only idea count).
- `e2e/test_marketing.py::test_marketing_analytics_journey` (new) — create + launch a campaign →
  ingest 12 points across two channels, two ISO weeks and the campaign → read analytics → re-read
  Overview; **3 live captures** under `e2e/_captures/marketing/` (`metrics_ingested`, `analytics`,
  `overview_with_fills`).

**Errors / API surface — additive only** (no existing route or shape changed; three previously-null
Overview fields now carry values): `VALIDATION_ERROR` (422) — bad enum, negative or > int32 `value`,
bad `ts`, > 1000 points, foreign `campaign_id`, bad `range`; `FORBIDDEN` (403) for non-marketing
roles. No 404s, no async/LLM behaviour.

**Docs**
- `docs/fe-integration-guide-marketing-analytics.md` (new) — every success body pasted verbatim from
  the 3 captures; error/empty/`cac: null` shapes labelled not-captured; ingestion marked
  **API-first / provisional**; the five data-model semantics called out up front; verification table.
- `docs/checklist/PROJECT_CHECKLIST.md` (Slice 5 marked complete; **Module 10 marked COMPLETE**).

## Verification

**Unit/integration:** the 17 API tests plus model and migration tests above, landed with each
feature commit. The whole-repo unit + CI-parity run is owned by the controller before the PR is
opened and is not reproduced by this docs commit.

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 58 passed**, including the new
`test_marketing_analytics_journey`. That journey's captures are the source of every success body in
the FE guide. `alembic heads` — single head `0038_marketing_metrics`.

**Honest gap disclosure.** The e2e journey only exercises the happy path. Not e2e-captured
(unit-tested or source-derived only, and labelled as such in the FE guide): all 422/403 responses
and their bodies; the empty-workspace zeroed shape; `cac: null`; leaderboard ordering with more than
one campaign; week-gap omission; the 7d/90d ranges; and the 1000-point batch cap (no dedicated test —
enforced by `max_length` on the schema). The analytics route's 403 has no dedicated test (same
dependency as ingest).

## Operate / roll back

**New deploy-time requirement: none.** No new worker job, no new config. Run `alembic upgrade head`
to apply `0038_marketing_metrics`. Nothing ingests automatically — analytics stays empty until
something calls `POST /marketing/metrics`.

**Rollback:** revert this slice's commits as a unit (`9de62ed..6a63054`, plus this docs commit) and
downgrade (`poetry run alembic downgrade 0037_seo_tools`), which drops `marketing_metrics`. Safe —
nothing else reads it; the Overview fields simply revert to `null`/placeholder values with the code
rollback. Downgrade the migration only *after* the app code is rolled back, same ordering as every
slice in this module. Ingested metric data is lost on downgrade.

## Follow-ups

- **Analytics export → Module 22 (Analytics & Reports)** — CSV/PDF export was scoped out of this
  slice and belongs with the reporting module.
- **Ad-platform auto-ingestion** — metrics only enter via the manual/API endpoint today; wiring
  Google/Meta/etc. sync (and a refresh job) is a separate future slice.
- **FE ingestion screen** — none exists; `POST /marketing/metrics` is API-first/provisional and its
  shape may change when a screen (or auto-ingestion) lands.
- **Funnel additive-scope guidance** — the funnel double-counts if the same events are ingested at
  more than one scope (see semantics). Decide whether to enforce one scope per event, compute the
  funnel from a single scope, or add an idempotency/dedupe key once real ingestion exists.
- **Metric dedupe / correction** — ingestion is append-only with no update/delete, so a bad
  submission cannot be corrected through the API.
- **Analytics-route 403 test** and a **1000-point cap test** — both enforced but untested.
- **`campaign.metrics` JSONB cleanup** — the column stays `{}` and is now dead weight; remove it (or
  populate it from the analytics helpers) once the FE confirms it reads `analytics.leaderboard`.
- **Module 10 is otherwise complete**; the older deferred items tracked in the checklist (auto-publish
  at `scheduled_at`, segment rule execution, external SEO-provider sync, etc.) remain non-blocking.
