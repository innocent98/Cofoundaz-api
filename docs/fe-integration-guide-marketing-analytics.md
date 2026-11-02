# FE Integration Guide — Marketing Hub: Performance Analytics (Module 10, Slice 5)

> **Provenance.** `e2e/test_marketing.py::test_marketing_analytics_journey` was run
> (`bash scripts/e2e_run.sh`, 58/58 e2e passed) and every **success response body** below is pasted
> **verbatim** from the 3 captures it wrote to `e2e/_captures/marketing/`:
> `metrics_ingested.json`, `analytics.json`, `overview_with_fills.json`. Nothing was written from
> the schema or from memory. The **request** body in §1 is the exact `points` list the e2e journey
> sends (read from `e2e/test_marketing.py`; the harness does not capture request bodies), with
> dates shown as concrete values that match the captured weeks. Two things are **not**
> e2e-captured and are labelled ⚠️ inline: error responses (422/403) and the empty-workspace
> shape — those are unit-tested (`tests/api/test_marketing_analytics.py`) or read from source, and
> the verification table at the end says which.

> ### ⚠️ Read this first — API-first / provisional ingestion
>
> | Section | Status |
> |---|---|
> | §2 **`GET /marketing/analytics`** (the four widgets) | **FE-aligned** — the read side matches the current Analytics UI. Safe to build against. |
> | §3 **Overview fills** (`GET /marketing`) | **FE-aligned.** Safe to build against. |
> | §1 **`POST /marketing/metrics`** (ingestion) | **API-FIRST / PROVISIONAL — no FE ingestion screen exists yet.** Today this is how data gets in (scripts, future ad-platform sync, admin tooling); the shape may change once a real ingestion UI or auto-ingestion exists. |

This extends the earlier Marketing Hub guides — **`docs/fe-integration-guide-marketing-calendar.md`**
(Slice 1), **`-campaigns.md`** (Slice 2), **`-copy.md`** (Slice 3a), **`-channel-ai.md`** (Slice 3b)
and **`-seo.md`** (Slice 4). Same base path, same auth, same envelope.

Base path: `/api/v1/marketing`. Every route requires `Authorization: Bearer <token>` and an
`X-Workspace-Id` header. **Access: founder or team_member only** — the same
`require_role(founder, team_member)` dependency as every `/marketing` route; any other membership
role (e.g. `mentor`, `investor`) gets **403 `FORBIDDEN`** on both new routes. ⚠️ Not e2e-captured;
`POST /marketing/metrics` is unit-verified by
`tests/api/test_marketing_analytics.py::test_ingest_rbac_forbidden` (parametrized over the
non-marketing roles). `GET /marketing/analytics` has **no dedicated 403 test** — it is guarded by
the same `_marketing` dependency in `app/api/v1/endpoints/marketing.py`, so the behaviour follows
from source. Every success response is the standard envelope
`{"data": …, "meta": null}`.

---

## Semantics you MUST understand before building (read all five)

These are behaviours of the data model, not bugs. Each one produces a wrong-looking chart if the FE
(or whatever ingests data) is unaware of it.

**(a) Metrics are ADDITIVE, and the funnel sums ALL rows regardless of scope.**
A metric point can be unscoped, channel-scoped (`channel`), or campaign-scoped (`campaign_id`).
The **funnel** (`impressions` / `clicks` / `conversions`) adds up **every** row of that metric in
range, whatever its scope. So a `conversions` point counts toward the funnel whether it is
channel-scoped, campaign-scoped, or unscoped. **If you ingest the same real-world events at two
scopes — e.g. a channel total *and* a per-campaign breakdown of those same conversions — the funnel
double-counts them.**
The captured sample shows exactly this: `funnel.conversions` is **52** because the journey ingested
12 + 9 (email channel) + 10 (search channel) + 21 (campaign-scoped) conversions, while
`funnel.clicks` is **640** because clicks were ingested **campaign-scoped only**. So
`conversion_rate` = 52 / 640 = **8.1** — a mixed-scope number, not a true conversion rate.
**Guidance: ingest each real event at exactly ONE scope** (pick channel *or* campaign, never both
for the same event), or treat the funnel as a raw sum of whatever was ingested.
By contrast `cac_by_channel` only reads channel-scoped rows and `leaderboard` only reads
campaign-scoped rows, so those two widgets are never double-counted by the other scope.

**(b) `traffic_by_week` OMITS weeks with no visits.** It returns one row per ISO week that has at
least one `visits` point in range; a week with zero visits is **absent, not `{visits: 0}`**. The FE
must **gap-fill** the missing weeks (each Monday between the first and last `week_start`, and out to
the range edges if you want a full-width chart) to draw a continuous line/bars.

**(c) `cac_by_channel` may include a channel with only some metrics.** A channel appears if it has
*any* channel-scoped row in range. A channel with `spend` but no `conversions` shows up with
`conversions: 0` and **`cac: null`** — render "—", do not divide. (Channels with no channel-scoped
rows at all do not appear.) ⚠️ The `cac: null` case is unit-verified
(`test_analytics_cac_null_when_no_conversions`), not in the e2e capture.

**(d) `range` covers the last N days INCLUSIVE OF TODAY, in UTC.** The server keeps rows with
`ts >= (today_utc − N days)`, so **`7d` spans 8 calendar days** (today plus the previous 7), `30d`
spans 31, `90d` spans 91. "Today" and all bucketing use **UTC dates**, not the viewer's timezone —
a viewer far from UTC can see "today" differ by a day at the boundary. Label the control as
"last 7 / 30 / 90 days", not "exactly N days".

**(e) The FE leaderboard must consume THIS endpoint's `leaderboard` array — NOT
`campaign.metrics`.** `metrics` on a campaign object (Slice 2) **stays `{}`** and is never filled;
Slice 5 deliberately put the per-campaign numbers in `GET /marketing/analytics` instead. Do not read
`campaigns[].metrics` for anything.

---

## 1. `POST /marketing/metrics` — ingest metric points (API-first / provisional)

Bulk-inserts time-series points. **No FE screen yet** — see the banner above.

**Request** — `{ "points": [ ... ] }`. Each point:

| Field | Type | Notes |
|---|---|---|
| `ts` | date, `YYYY-MM-DD` | **Required.** A calendar date (UTC day), not a datetime. |
| `metric` | enum | **Required.** One of `visits`, `impressions`, `clicks`, `conversions`, `spend`. |
| `value` | integer | **Required.** `0 … 2147483647` (Postgres int32 max). **`spend` is in CENTS.** |
| `channel` | `ChannelKey` \| omitted/null | Optional scope. One of the 8 channel keys (`organic_social`, `paid_social`, `search`, `email`, `content_seo`, `partnerships`, `events`, `referral`). |
| `campaign_id` | uuid \| omitted/null | Optional scope. Must be a campaign **in this workspace**. |

A point may set `channel`, `campaign_id`, both, or neither (see semantics (a)).

Request body (the exact metrics/values/scopes the e2e journey sends; the journey computes `ts`
relative to today as `today − 2 days` and `today − 9 days`, so the concrete dates below are
**inferred** from the captured `week_start` values (2026-09-21 and 2026-09-14 weeks) rather than
recorded, and `<campaign-uuid>` is a placeholder for the id returned by `POST /marketing/campaigns`):

```json
{
  "points": [
    { "ts": "2026-09-27", "metric": "visits", "value": 420 },
    { "ts": "2026-09-20", "metric": "visits", "value": 310 },
    { "ts": "2026-09-27", "metric": "spend", "value": 12000, "channel": "email" },
    { "ts": "2026-09-27", "metric": "conversions", "value": 12, "channel": "email" },
    { "ts": "2026-09-20", "metric": "spend", "value": 9000, "channel": "email" },
    { "ts": "2026-09-20", "metric": "conversions", "value": 9, "channel": "email" },
    { "ts": "2026-09-27", "metric": "spend", "value": 30000, "channel": "search" },
    { "ts": "2026-09-27", "metric": "conversions", "value": 10, "channel": "search" },
    { "ts": "2026-09-27", "metric": "impressions", "value": 8000, "campaign_id": "<campaign-uuid>" },
    { "ts": "2026-09-27", "metric": "clicks", "value": 640, "campaign_id": "<campaign-uuid>" },
    { "ts": "2026-09-27", "metric": "conversions", "value": 21, "campaign_id": "<campaign-uuid>" },
    { "ts": "2026-09-27", "metric": "spend", "value": 25000, "campaign_id": "<campaign-uuid>" }
  ]
}
```

**Response `200`** — verbatim from `e2e/_captures/marketing/metrics_ingested.json`:

```json
{
  "data": {
    "created": 12
  },
  "meta": null
}
```

`created` is the number of points inserted (12 points sent → 12). Ingestion is **append-only and
additive**: posting the same point twice inserts two rows and doubles the totals. There is no
de-duplication and no update/delete endpoint — send each real event once (semantics (a)).

**Validation → `422 VALIDATION_ERROR`.** The whole batch is rejected (nothing is inserted) if any
point is invalid:

| Cause | Where enforced |
|---|---|
| `metric` or `channel` not in the enums above | request schema |
| `value` negative | request schema (`ge=0`) |
| `value` above `2147483647` (int32) | request schema (`le`) — a **422, not a 500** |
| `ts` not a valid `YYYY-MM-DD` date | request schema |
| More than **1000** points in one request | request schema (`max_length=1000`) — split into batches |
| `campaign_id` not a campaign in **this** workspace (foreign or nonexistent) | service; checked for all points before any insert |

⚠️ **Status codes are unit-verified, not e2e-captured**
(`test_ingest_rejects_negative_value`, `::test_ingest_rejects_value_over_int32`,
`::test_ingest_rejects_foreign_campaign`, all asserting `422`). The **body** shape below is from
source, not a live capture: schema failures go through the app's request-validation handler
(`app/core/errors.py`) and produce

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [ { "field": "<loc path>", "message": "<pydantic message>" } ]
  }
}
```

(the `field` path and pydantic `message` text vary by failure — do not string-match them), while the
foreign-campaign case is raised by the service with `field: "campaign_id"` and message
`campaign_id must reference a campaign in this workspace.` (⚠️ from source
`app/services/marketing/analytics.py`, not captured). The 1000-point cap has **no dedicated test**;
it is read from `MetricsIngest` in `app/schemas/marketing.py`.

**Auth:** founder/team_member only → otherwise **403 `FORBIDDEN`** (⚠️ unit-verified, not captured).
An empty batch (`{"points": []}`) is valid and returns `created: 0` for a permitted role.

---

## 2. `GET /marketing/analytics?range=7d|30d|90d` — the four widgets

`range` is optional and defaults to **`30d`**. Allowed values: `7d`, `30d`, `90d`. Anything else →
**422 `VALIDATION_ERROR`** (⚠️ status unit-verified by `test_analytics_bad_range_422`; the
service raises `field: "range"`, message `range must be one of 7d, 30d, 90d.` — from source
`_range_cutoff`, not captured). Founder/team_member only → otherwise **403** (⚠️ not captured).

**Response `200`** for `?range=30d` — verbatim from `e2e/_captures/marketing/analytics.json`
(the data behind it is the 12 points in §1):

```json
{
  "data": {
    "range": "30d",
    "traffic_by_week": [
      {
        "week_start": "2026-09-14",
        "visits": 310
      },
      {
        "week_start": "2026-09-21",
        "visits": 420
      }
    ],
    "cac_by_channel": [
      {
        "channel": "email",
        "spend": 21000,
        "conversions": 21,
        "cac": 1000
      },
      {
        "channel": "search",
        "spend": 30000,
        "conversions": 10,
        "cac": 3000
      }
    ],
    "funnel": {
      "impressions": 8000,
      "clicks": 640,
      "conversions": 52,
      "click_through_rate": 8.0,
      "conversion_rate": 8.1
    },
    "leaderboard": [
      {
        "campaign_id": "2e91564d-1265-4d3f-a06b-1ffbd6697702",
        "name": "Q4 launch push",
        "clicks": 640,
        "conversions": 21,
        "spend": 25000,
        "cac": 1190
      }
    ]
  },
  "meta": null
}
```

(The `campaign_id` above is a real id from a throwaway e2e run database, not personal data.)

### Field reference

| Path | Type | Notes |
|---|---|---|
| `data.range` | string | Echo of the requested range (`"7d"`, `"30d"` or `"90d"`). |
| `data.traffic_by_week[]` | array | Ordered oldest → newest. **Weeks with no visits are omitted — gap-fill** (semantics (b)). |
| `…traffic_by_week[].week_start` | date `YYYY-MM-DD` | The **Monday** of the ISO week (UTC). In the capture, `2026-09-14` and `2026-09-21` are both Mondays. |
| `…traffic_by_week[].visits` | integer | Sum of `visits` points in that week. |
| `data.cac_by_channel[]` | array | One row per channel with any channel-scoped row in range. Order is **not guaranteed** — sort client-side. |
| `…cac_by_channel[].channel` | `ChannelKey` string | One of the 8 channel keys. |
| `…cac_by_channel[].spend` | integer, **CENTS** | `21000` = $210.00. |
| `…cac_by_channel[].conversions` | integer | |
| `…cac_by_channel[].cac` | integer **cents** \| **`null`** | `round(spend / conversions)`; **`null` when `conversions` is 0** (semantics (c)). `1000` = $10.00. |
| `data.funnel.impressions` / `.clicks` / `.conversions` | integer | **Sum of ALL rows of that metric, any scope** (semantics (a)). |
| `data.funnel.click_through_rate` | number | **Percentage, 1 decimal place** (`8.0` = 8.0%, not 0.08). `clicks / impressions × 100`; `0.0` when impressions is 0. |
| `data.funnel.conversion_rate` | number | **Percentage, 1 decimal place** (`8.1` = 8.1%). `conversions / clicks × 100`; `0.0` when clicks is 0. |
| `data.leaderboard[]` | array | Campaign-scoped rows only, **ordered by `conversions` descending** (ties broken by `name`). |
| `…leaderboard[].campaign_id` | uuid string | |
| `…leaderboard[].name` | string | Campaign name. |
| `…leaderboard[].clicks` / `.conversions` | integer | |
| `…leaderboard[].spend` | integer, **CENTS** | |
| `…leaderboard[].cac` | integer cents \| **`null`** | `round(spend / conversions)`; `null` when the campaign has 0 conversions. |

**Money is always integer cents** (`spend`, `cac`, both widgets). Convert to a display currency on
the client. **Rates are already percentages** — do not multiply by 100 again.

### How to read the sample capture (worked example)

- `cac_by_channel.email`: spend 12000 + 9000 = **21000**, conversions 12 + 9 = **21**, cac
  21000 / 21 = **1000**.
- `funnel.conversions` = **52** = 12 + 9 + 10 (channel-scoped) + 21 (campaign-scoped) — the
  **double-count** described in semantics (a). `funnel.clicks` = 640 is campaign-scoped only.
  Hence `conversion_rate` 8.1 = 52 / 640.
- `leaderboard[0].cac` = 25000 / 21 = 1190.47… → **1190** (rounded).
- `traffic_by_week` has two rows because visits were ingested on two dates in two different weeks;
  any other week in the 30-day window is simply absent.

### Empty workspace / no data in range

⚠️ Not e2e-captured (unit-verified by `test_analytics_empty_is_zeroed`; shape from source): with no
points in range the response is `200` with `traffic_by_week: []`, `cac_by_channel: []`,
`leaderboard: []`, and a **zeroed funnel** (`impressions: 0`, `clicks: 0`, `conversions: 0`,
`click_through_rate: 0.0`, `conversion_rate: 0.0`) — never a 404 and never a divide-by-zero.

### Tenancy

Every widget is scoped to the caller's workspace (`X-Workspace-Id`). The leaderboard additionally
joins campaigns on the same workspace, so another workspace's campaign can never appear. ⚠️
unit-verified (`test_analytics_is_tenant_scoped`).

### UX notes

- Render `cac: null` as "—" (or "No conversions yet"), not `$0`.
- Because of semantics (b), do not draw a line chart straight from `traffic_by_week` without
  gap-filling — a quiet week would silently disappear from the x-axis.
- Because of semantics (a), do not present `funnel.conversion_rate` as authoritative unless you
  control how data is ingested (one scope per event).

---

## 3. Overview fills — `GET /marketing`

Slice 1 shipped the Overview with three fields as explicit `null`; Slice 5 fills them. Same route,
same shape, no new fields.

**Response `200`** — verbatim from `e2e/_captures/marketing/overview_with_fills.json` (taken right
after the §1 ingestion, in a workspace with one launched campaign):

```json
{
  "data": {
    "scheduled_this_week": 0,
    "active_channels": 0,
    "active_campaigns": 1,
    "top_channel_by_conversions": "email",
    "ai_content_ideas": 0
  },
  "meta": null
}
```

| Field | Type | Meaning |
|---|---|---|
| `active_campaigns` | integer | Count of the workspace's campaigns with status `active` (in the capture: the one campaign the journey launched). |
| `top_channel_by_conversions` | `ChannelKey` string \| **`null`** | The channel-scoped channel with the most total `conversions`. In the capture `email` wins with 21 vs `search`'s 10. **`null` when no channel-scoped conversions exist.** Note it is computed over **all time**, **not** the `range` used on the analytics endpoint, and campaign-scoped/unscoped conversions do not count toward it. |
| `ai_content_ideas` | integer | Total number of gap suggestions across the workspace's **ready** content-gap generations (Slice 4). `0` in the capture because the journey ran no content-gap generation. Failed/in-flight generations are not counted. |
| `scheduled_this_week`, `active_channels` | integer | Unchanged from Slice 1 (both `0` in this capture — the analytics journey creates no calendar entries and activates no channels). |

**Field-nesting / null traps:** in Slices 1–4 `active_campaigns`, `top_channel_by_conversions` and
`ai_content_ideas` were always `null`. They are now `int`, `ChannelKey | null` and `int`
respectively — `top_channel_by_conversions` is the only one that can still be `null`. Treat the
other two as always-present integers, but keep null-tolerant rendering for old cached responses.
The Overview values are **not** range-filtered; the analytics widgets are.

---

## Errors

Standard envelope, same shape as Slices 1–4 (see `docs/fe-integration-guide-marketing-seo.md` →
Errors). Not re-captured for the new routes.

| Status | Code | When (Slice 5 routes) |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not a founder/team_member of this workspace |
| 422 | `VALIDATION_ERROR` | Ingest: bad `metric`/`channel` enum, negative or > int32 `value`, bad `ts`, > 1000 points, foreign/unknown `campaign_id`. Analytics: `range` not `7d`/`30d`/`90d` |

There are no 404s on these routes and no async/`over_budget` behaviour (nothing here calls the LLM).

---

## Verification

| Claim | Status | Source |
|---|---|---|
| `POST /marketing/metrics` 12-point batch → 200 `{"created": 12}` | ✅ verified live | `metrics_ingested.json` |
| The 12-point request body | ✅ metrics/values/scopes exact from the journey source; concrete dates inferred from the captured weeks | `e2e/test_marketing.py::test_marketing_analytics_journey` |
| `GET /marketing/analytics?range=30d` full body (all four widgets) | ✅ verified live | `analytics.json` |
| `traffic_by_week.week_start` is a Monday | ✅ verified live (2026-09-14, 2026-09-21) | `analytics.json` |
| `spend`/`cac` in cents; `cac` = round(spend/conversions) | ✅ verified live | `analytics.json` (21000/21 = 1000; 25000/21 → 1190) |
| Funnel rates are 1-dp percentages; funnel sums all scopes (52 conversions / 640 clicks = 8.1) | ✅ verified live | `analytics.json` |
| Leaderboard row shape + ordering | ✅ shape live (1 row); conversions-desc ordering unit-only | `analytics.json`; `test_analytics_leaderboard_orders_by_conversions` |
| Overview fills (`active_campaigns` 1, `top_channel_by_conversions` `"email"`, `ai_content_ideas` 0) | ✅ verified live | `overview_with_fills.json` |
| `ai_content_ideas` counts ready content-gap suggestions (non-zero case) | ⚠️ unit only | `test_overview_ai_content_ideas_counts_ready_gaps_only` |
| `top_channel_by_conversions` `null` when empty | ⚠️ unit only | `test_overview_top_channel_null_when_empty` |
| `active_campaigns` counts only `active` campaigns | ⚠️ unit only | `test_overview_active_campaigns_counts_only_active` |
| `traffic_by_week` omits empty weeks | ⚠️ unit only (live capture happens to have 2 non-empty weeks) | `test_analytics_traffic_by_week` |
| `cac: null` when channel has spend but 0 conversions | ⚠️ unit only | `test_analytics_cac_null_when_no_conversions` |
| Empty workspace → zeroed funnel, empty arrays | ⚠️ unit only | `test_analytics_empty_is_zeroed` |
| `range` = last N days inclusive of today (UTC); out-of-range rows excluded | ⚠️ unit only (boundary); rule read from `_range_cutoff` | `test_analytics_excludes_out_of_range`; `analytics.py` |
| Analytics tenancy (no cross-workspace rows) | ⚠️ unit only | `test_analytics_is_tenant_scoped` |
| Ingest 422s: negative value, > int32 value, foreign `campaign_id` (status) | ⚠️ unit only (status); bodies from source | `test_ingest_rejects_*` |
| Ingest 422 body shapes / message strings | ⚠️ from source, not captured | `app/core/errors.py`, `analytics.py` |
| 1000-point batch cap | ⚠️ source only (no test) | `MetricsIngest.points` `max_length=1000` |
| Bad `range` → 422 (status) | ⚠️ unit only; message from source | `test_analytics_bad_range_422` |
| RBAC 403 for non-marketing roles | ⚠️ ingest unit-verified; analytics has no dedicated test (same `_marketing` dependency) | `test_ingest_rbac_forbidden` |
| Ingestion FE contract | ⚠️ **provisional** — no FE screen yet | — |
