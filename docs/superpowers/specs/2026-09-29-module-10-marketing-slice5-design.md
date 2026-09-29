# Module 10 — Marketing Hub, Slice 5: Performance Analytics

**Status:** design approved (2026-09-29), ready for implementation planning. **Final slice of Module 10.**

## Context

Module 10 slices merged to `develop`: S1 (Calendar + Channels), S2 (Campaigns + Segments), S3a
(AI copy + plan-week), S3b (AI channel-plan + fit notes), S4 (SEO Tools). Slice 5 is the last:
**Performance Analytics (PRD 10.8, `/app/marketing/analytics`)** — a cross-channel dashboard, plus
filling the Overview stats earlier slices stubbed.

### FE cross-check (per the standing rule)

The built FE screen `cofoundaz/app/(dashboard)/marketing/analytics/page.tsx` and the design comp
render four widgets: a **date-range selector** (7d/30d/90d), a **"Traffic by week"** line, **"CAC by
channel"** bars, and a **campaign leaderboard** (name / clicks / conversions / CAC). It is a **mock
prototype** — no backend calls; the traffic line and CAC bars are hardcoded literals; the
leaderboard reads `campaign.metrics {spend, impressions, clicks, conversions, ctr}` with fake
fallbacks. **No analytics data source exists** anywhere (no pixel, no ad-platform integration), and
there is **no FE ingestion UI**. So: the analytics *read* is FE-aligned to the dashboard; **ingestion
is API-first / provisional** (flagged in the FE guide). The FE leaderboard will consume this slice's
`GET /marketing/analytics` leaderboard array rather than `campaign.metrics`.

## Goal

Ship a `marketing_metrics` time-series store with manual/API ingestion and a single aggregation read
that powers traffic-by-week, CAC-by-channel, a conversion funnel, and the campaign leaderboard, and
fill the Overview stubs — completing Module 10's data surface.

## Global constraints (carried verbatim where they bind)

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by
  `startup_id` (from membership, never the body); cross-tenant id → `NotFound` (404) / not counted.
- **RBAC:** every route behind `_marketing = require_role(MembershipRole.founder,
  MembershipRole.team_member)`; other roles → 403 before any lookup.
- **`AppError.http_status`** (not `.status_code`); `get_db` does NOT auto-commit; validation errors
  use the `_validation(field, message)` helper (422 with `field_errors`).
- **CodeQL (required check):** no mutating call inside an `assert` (bind to a var first); no implicit
  string concatenation in a list literal.
- **Migration:** SAVEPOINT pattern (`with db.begin_nested(): db.add(...); db.flush()`) for any
  unique-constraint→422 path; migration-head tests assert **single head + revision-in-history**, not
  "my revision is THE head" (see the recurring-gotcha memory). Migration id ≤ 32 chars.
- **No AI attribution** in any commit message, PR body, or review comment.

## Design

### 1. Metrics store — `marketing_metrics` (migration `0038_marketing_metrics`)

New model `MarketingMetric`:
- `startup_id` (FK startups CASCADE, index)
- `ts` (`Date`, NN) — daily granularity, sufficient for weekly rollups + range filters
- `channel` (`Enum(ChannelKey, native_enum=False, length=20)`, **nullable**) — channel-scoped points
- `campaign_id` (FK campaigns SET NULL, **nullable**, index) — campaign-scoped points (leaderboard)
- `metric` (new `Enum(MarketingMetricName, native_enum=False, length=12)`, NN)
- `value` (`Integer`, NN) — **spend in cents**; all others are counts
- Index on `(startup_id, ts)`.

A data point is channel-scoped, campaign-scoped, or both — one store powers all widgets.

New enum `MarketingMetricName` (StrEnum): `visits`, `impressions` (11), `clicks`, `conversions`
(11), `spend` — all ≤ 12 chars, fit the column.

### 2. Ingestion — `POST /marketing/metrics`

- Body `MetricsIngest`: `{points: [MetricPoint]}` where `MetricPoint = {ts: date, channel?:
  ChannelKey, campaign_id?: uuid, metric: MarketingMetricName, value: int (ge=0)}`. Bulk insert
  (one flush).
- Validation: `channel`/`metric` are enum-coerced (bad value → 422); `campaign_id`, when present,
  must belong to the caller's startup (else `_validation("campaign_id", ...)` → 422); `value ≥ 0`.
- Founder/team_member gated; commits. Returns `{"created": <n>}`.
- **Provisional / API-first:** no FE ingestion screen yet; flagged in the FE guide.

### 3. Analytics read — `GET /marketing/analytics?range=7d|30d|90d`

- `range` default `30d`; filters `ts >= today − range`. Unknown range value → 422.
- Response `AnalyticsResponse`:
  - `range`: the echoed range.
  - `traffic_by_week`: `[{week_start: date, visits: int}]` — `sum(value) filter metric=visits`
    grouped by `date_trunc('week', ts)`, ordered by week.
  - `cac_by_channel`: `[{channel: ChannelKey, spend: int (cents), conversions: int, cac: int | None}]`
    — over channel-scoped rows (channel not null), per channel: `sum(spend)`, `sum(conversions)`,
    `cac = round(spend / conversions)` (cents) or `None` when conversions = 0.
  - `funnel`: `{impressions: int, clicks: int, conversions: int, click_through_rate: float,
    conversion_rate: float}` — totals over the range; rates are 0.0 when the denominator is 0
    (no divide-by-zero), rounded to 1 dp, as a percentage.
  - `leaderboard`: `[{campaign_id: uuid, name: str, clicks: int, conversions: int, spend: int,
    cac: int | None}]` — campaign-scoped rows grouped by `campaign_id`, joined to `Campaign.name`,
    ordered by conversions desc then name.
- Empty data → empty lists and zeroed funnel (never a 500 / divide-by-zero).

### 4. Overview fills (`app/services/marketing/service.py::overview`)

Fill the three currently-stubbed fields (`OverviewResponse` already types them):
- `top_channel_by_conversions`: the `ChannelKey` value with the greatest total `conversions` in
  `marketing_metrics` for the startup (all-time), or `None` if none.
- `ai_content_ideas`: an int — total AI content-gap ideas = `sum(len(g.output["gaps"]))` over the
  startup's `content_gap` generations whose `status = ready` (from Slice 4), `0` if none.
- `active_campaigns` (lingering S2 stub, folded in): count of `Campaign` with `status = active` for
  the startup.

### Data flow / tenancy / transactions

Standard. Ingestion validates `campaign_id` ownership before insert; all aggregations filter by
`startup_id`. The leaderboard join is `startup_id`-scoped on both sides. No add-then-select
in-txn issues (ingestion is insert-only; analytics is read-only). Campaign `metrics` JSONB stays
`{}` (vestigial — the leaderboard is served by the aggregation; noted as a deferred cleanup, not
revived here).

## Components / files

- `app/db/models/enums.py` — add `MarketingMetricName`.
- `app/db/models/marketing.py` — add `MarketingMetric`.
- `alembic/versions/0038_marketing_metrics.py` — one table (single head off `0037_seo_tools`).
- `app/schemas/marketing.py` — `MetricPoint`, `MetricsIngest`, `AnalyticsResponse` (+ nested
  `TrafficPoint`, `ChannelCac`, `Funnel`, `LeaderboardRow`).
- `app/services/marketing/analytics.py` (new — keeps `service.py` focused) — `ingest_metrics`,
  `get_analytics` (the four aggregations), and the overview helpers `top_channel_by_conversions`,
  `count_ai_content_ideas`.
- `app/services/marketing/service.py` — `overview()` fills the three fields via the analytics
  helpers + an active-campaign count.
- `app/api/v1/endpoints/marketing.py` — `POST /marketing/metrics`, `GET /marketing/analytics`.
- Docs: FE guide `docs/fe-integration-guide-marketing-analytics.md` (analytics read FE-aligned;
  ingestion flagged API-first/provisional); SOP `docs/sop/2026-09-29-marketing-slice5.md`;
  `docs/checklist/PROJECT_CHECKLIST.md` (mark Slice 5 + **Module 10 COMPLETE**).

## Review focus (inputs the spec implies but happy-path tests may miss)

- **Ingestion validation:** an out-of-enum `metric`/`channel`, a negative `value`, or a
  `campaign_id` from another startup → 422 (not 500, not stored). — ingestion task.
- **Empty-data analytics:** `GET /marketing/analytics` with zero metrics → empty lists + zeroed
  funnel with 0.0 rates (no `ZeroDivisionError`); a channel with spend but 0 conversions → `cac:
  null` (not a crash). — analytics task.
- **Range filtering + weekly bucketing:** points outside the range excluded; `traffic_by_week`
  buckets by ISO week correctly across a month boundary. — analytics task.
- **Leaderboard tenancy + join:** only the caller's campaigns appear; a `campaign_id` whose campaign
  was deleted (FK SET NULL) doesn't crash the name join. — analytics task.
- **Overview fills:** `top_channel_by_conversions` null when no metrics; `ai_content_ideas` 0 when
  no ready content_gap generations; `active_campaigns` counts only `active`. — overview task.
- **RBAC:** 403 for mentor/investor on both new routes. — endpoints task.

## Decisions (rulings settled during brainstorming)

- **D1 — data model:** `marketing_metrics` time-series store (PRD entity) + manual/API ingestion +
  aggregation reads. Metrics `value` integer, spend in cents.
- **D2 — campaign dimension:** the PRD entity has no campaign column; add an optional `campaign_id`
  so one store powers channel-level (traffic, CAC) AND campaign-level (leaderboard) analytics.
- **D3 — Overview fills:** `top_channel_by_conversions` (max-conversions channel, all-time),
  `ai_content_ideas` (count of ready content_gap gaps), and fold in the lingering `active_campaigns`
  count.
- **D4 — export deferred:** CSV/report export → Module 22 (not built).
- **D5 — ingestion is API-first/provisional:** no FE ingestion screen; the analytics read is
  FE-aligned; flagged in the FE guide.

## Out of scope

CSV/report export (→ Module 22); ad-platform / analytics-pixel auto-ingestion (manual/API only);
reviving campaign `metrics` JSONB; an FE ingestion screen; per-day (vs per-week) traffic granularity
on the read.
