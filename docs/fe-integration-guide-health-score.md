# FE Integration Guide — Health Score (Module 06)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_health_score.py` running against a real server (`make e2e`) — see
`e2e/_captures/health_score/*.json`. Nothing here is retyped from memory or invented; where a
capture is long it is trimmed with `// ...` and a note, never re-shaped.

Base path: `/api/v1/health-score`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active
workspace, same as every other tenant-scoped endpoint in this API (`GET /auth/me` →
`data.active_workspace_id` is the source for that header — see
`docs/sop/2026-08-16-assessment.md` for the discovery pattern).

---

## 1. The state machine: `pending_assessment` ↔ `ok`

`GET /api/v1/health-score` is the one overview call the FE needs to branch all Health Score UI
on. It returns exactly one of two shapes, discriminated by `data.status`:

- **`"pending_assessment"`** — no `HealthScore` row exists yet for this startup (the founder
  hasn't completed the kickoff assessment). `score`, `band` are `null`; `dimensions` and
  `top_recommendations` are empty arrays. Render the empty-state / "take your assessment" CTA
  using `data.message`.
- **`"ok"`** — a Health Score exists. `score` is an int 0–100, `band` is one of `at_risk` /
  `needs_work` / `healthy` / `thriving`, `dimensions` has exactly 5 entries, and
  `top_recommendations` carries the top-ranked pending recommendations (may be empty if the
  founder is scoring well everywhere).

There is no third state and no polling/loading status from this endpoint itself — it is
synchronous. **Polling guidance:** the Health Score is computed inline the moment the kickoff
assessment's `POST /assessments/{id}/complete` returns 200 (same request, same transaction) —
there is no async job to poll. The FE only needs to **re-fetch `GET /health-score` once, right
after the assessment-completion call succeeds**, to flip local state from
`pending_assessment` to `ok`. No interval polling is needed.

> ✅ **Update (2026-09-19, fixed & live-verified) — the "same transaction" claim above is now
> correct.** A `2026-09-19` earlier note flagged that `complete_assessment` recomputed the Health
> Score *before* the just-added `AssessmentResult` was visible to it (a `SessionLocal`
> `autoflush=False` quirk), so `POST /assessments/{id}/complete` alone did not reliably create the
> `HealthScore`/recommendation rows on the first call — only `GET /health-score`'s lazy-on-read
> fallback did. That ordering is **now fixed**: `complete_assessment` flushes the `AssessmentResult`
> before recomputing, so the `HealthScore`, pending recommendations, and the
> `ai.health.recommendations` job (§5) are all created by `POST /assessments/{id}/complete` itself.
> The `GET /health-score` re-fetch above is still the right FE step (it flips local state and
> remains a harmless idempotent fallback if the row somehow doesn't exist), but it is no longer
> load-bearing. See `docs/sop/2026-09-19-complete-assessment-recompute-flush.md`.

### 1a. `pending_assessment` — before the kickoff assessment

`e2e/_captures/health_score/overview_pending.json`:

```json
{
  "data": {
    "status": "pending_assessment",
    "score": null,
    "band": null,
    "message": "Complete your kickoff assessment to generate your Health Score.",
    "dimensions": [],
    "top_recommendations": []
  },
  "meta": null
}
```

### 1b. `ok` — after the kickoff assessment completes

`e2e/_captures/health_score/overview_ok.json`:

```json
{
  "data": {
    "status": "ok",
    "score": 31,
    "band": "at_risk",
    "delta_7d": 0,
    "computed_at": "2026-08-19T17:06:37.249921+00:00",
    "config_version": 1,
    "dimensions": [
      { "key": "team", "label": "Team", "score": 50, "band": "needs_work" },
      { "key": "legal", "label": "Legal", "score": 0, "band": "at_risk" },
      { "key": "money", "label": "Financial", "score": 43, "band": "needs_work" },
      { "key": "market", "label": "Market", "score": 38, "band": "at_risk" },
      { "key": "product", "label": "Product", "score": 25, "band": "at_risk" }
    ],
    "top_recommendations": [
      {
        "id": "3d1a3a35-a918-4e88-9ccb-858aae7197b5",
        "dimension": "legal",
        "key": "legal.incorporate",
        "title": "Complete incorporation",
        "body": "Register the company and issue founder shares. Operating unincorporated exposes founders personally and blocks fundraising.",
        "estimated_lift": 9,
        "effort": "high",
        "status": "pending",
        "priority": 1
      },
      {
        "id": "12ea1f3f-95d9-4d14-b5f3-06f2cf73188c",
        "dimension": "legal",
        "key": "legal.founder_agreement",
        "title": "Sign a founders' agreement",
        "body": "Put equity splits, vesting, and roles in writing before it is contentious. This prevents the most common founder disputes.",
        "estimated_lift": 7,
        "effort": "medium",
        "status": "pending",
        "priority": 2
      },
      {
        "id": "79bed755-5479-4307-b046-6d29ede340c0",
        "dimension": "product",
        "key": "product.define_mvp",
        "title": "Define your MVP scope",
        "body": "Write a one-page MVP definition: the single problem, the smallest feature set that solves it, and what you are deliberately leaving out.",
        "estimated_lift": 8,
        "effort": "medium",
        "status": "pending",
        "priority": 3
      }
    ],
    "summary": "Your Health Score is 31 (at risk). Your weakest area is Legal."
  },
  "meta": null
}
```

Note `dimensions` order is not alphabetical or fixed-weight order — treat it as an unordered
set of 5 and key off `key`, not array position, when rendering per-dimension tiles.

`key` vs `label`: internal dimension keys are `product` / `market` / `money` / `legal` /
`team`. Every label matches its key **except** `money`, whose display label is `"Financial"`.
Use `label` for UI copy, `key` for routing (`GET /health-score/dimensions/{key}`) and for
matching a recommendation's `dimension` field back to its parent tile.

---

## 2. Dimension drill-down

`GET /api/v1/health-score/dimensions/{dim}` — `{dim}` is one of `product`, `market`, `money`,
`legal`, `team`. An unrecognized `dim` 404s (`NOT_FOUND`).

`e2e/_captures/health_score/dimension_money.json` (`GET .../dimensions/money`):

```json
{
  "data": {
    "key": "money",
    "label": "Financial",
    "score": 43,
    "band": "needs_work",
    "signals": [
      {
        "key": "assessment.money",
        "value": 43.0,
        "contribution": 8.6,
        "source_ref": "assessment:ec87f76b-fd5c-4a8b-a9c1-a70ea4f20662"
      }
    ],
    "trend": [
      { "score": 43, "computed_at": "2026-08-19T17:06:37.249921+00:00" }
    ],
    "recommendations": [
      {
        "id": "e1330c60-d54b-4caf-b2af-af6f85be52bb",
        "dimension": "money",
        "key": "money.runway_model",
        "title": "Build a 12-month runway model",
        "body": "Model monthly cash in/out for 12 months so you know your runway and the month you must raise or break even.",
        "estimated_lift": 8,
        "effort": "medium",
        "status": "pending",
        "priority": 6
      },
      {
        "id": "329bd50d-ce60-47d0-b004-0f8520c7f49c",
        "dimension": "money",
        "key": "money.pricing_experiment",
        "title": "Run a pricing experiment",
        "body": "Test one concrete price point with real prospects. Willingness-to-pay evidence de-risks your whole model.",
        "estimated_lift": 6,
        "effort": "medium",
        "status": "pending",
        "priority": 9
      }
    ]
  },
  "meta": null
}
```

`signals` is *why* the dimension scored the way it did — currently one row per contributing
source (today, always the latest assessment's per-dimension score; more sources may land
later). `trend` is that dimension's own score history — **unbounded** today (every historical
point is returned, no `range` param like `/history` has); fine at current data volumes, but
don't assume it's capped.

---

## 3. History

`GET /api/v1/health-score/history?range=7d|30d|90d|all` (default not required by FE — always
pass an explicit `range`; an unrecognized value 422s).

`e2e/_captures/health_score/history.json` (`range=all`, one point — this founder has only
completed one assessment so far):

```json
{
  "data": [
    {
      "score": 31,
      "dimension_scores": { "team": 50, "legal": 0, "money": 43, "market": 38, "product": 25 },
      "delta": 0,
      "computed_at": "2026-08-19T17:06:37.249921+00:00"
    }
  ],
  "meta": null
}
```

`delta` is computed against the closest history point ≥7 days older (falling back to the
earliest point if none is that old yet) — it is `0` here because there is no older point to
diff against. Use this array directly to drive a trend chart; it's already sorted and
range-windowed server-side.

---

## 4. Benchmarks — cohort-size gate

`GET /api/v1/health-score/benchmarks` compares this startup's score against peers sharing the
same `stage` + `industry`. Below a minimum cohort size it returns an honest empty-state instead
of fabricating percentiles — **this is the expected response for almost every startup today**,
since real cohort aggregation hasn't shipped yet (see Follow-ups in the module SOP).

`e2e/_captures/health_score/benchmarks.json`:

```json
{
  "data": {
    "status": "insufficient_data",
    "cohort": { "stage": "idea", "industry": "Fintech" },
    "min_cohort_size": 5,
    "percentiles": null
  },
  "meta": null
}
```

Render this as "not enough startups like yours yet" rather than a chart with zeros —
`percentiles` is `null`, not an empty/zeroed object, specifically so the FE doesn't
accidentally plot fake data. There is currently no distinct "enough data" response shape to
design against yet; when real aggregation ships, expect `status` to gain a second value with
populated `percentiles` — treat any `status` other than `"insufficient_data"` defensively.

---

## 5. Recommendations — list, accept, and the 409 conflict

`GET /api/v1/health-score/recommendations?status=pending|accepted|dismissed` (defaults to
`pending` only if `status` is omitted).

`e2e/_captures/health_score/recommendations.json` (9 rows generated for a founder scoring low
across every dimension — trimmed to the first 3 of 9 below; every row has the same shape):

```json
{
  "data": [
    {
      "id": "3d1a3a35-a918-4e88-9ccb-858aae7197b5",
      "dimension": "legal",
      "key": "legal.incorporate",
      "title": "Complete incorporation",
      "body": "Register the company and issue founder shares. Operating unincorporated exposes founders personally and blocks fundraising.",
      "estimated_lift": 9,
      "effort": "high",
      "status": "pending",
      "priority": 1
    },
    {
      "id": "12ea1f3f-95d9-4d14-b5f3-06f2cf73188c",
      "dimension": "legal",
      "key": "legal.founder_agreement",
      "title": "Sign a founders' agreement",
      "body": "Put equity splits, vesting, and roles in writing before it is contentious. This prevents the most common founder disputes.",
      "estimated_lift": 7,
      "effort": "medium",
      "status": "pending",
      "priority": 2
    },
    {
      "id": "79bed755-5479-4307-b046-6d29ede340c0",
      "dimension": "product",
      "key": "product.define_mvp",
      "title": "Define your MVP scope",
      "body": "Write a one-page MVP definition: the single problem, the smallest feature set that solves it, and what you are deliberately leaving out.",
      "estimated_lift": 8,
      "effort": "medium",
      "status": "pending",
      "priority": 3
    }
    // ... 6 more rows, same shape, spanning product/market/money/team — see
    // e2e/_captures/health_score/recommendations.json for the full 9
  ],
  "meta": null
}
```

`priority` is the server's rank (1 = highest priority) — render the list in the order returned,
don't re-sort client-side.

### `estimated_lift` is a heuristic, not a guarantee

**`estimated_lift` is a rough, static, catalog-defined number** (currently: how far below the
recommendation's trigger threshold the dimension scored, weighted by that dimension's weight
in the overall score). It is **not** a promise that accepting the recommendation and doing the
work will move the score by that exact amount — there is no before/after measurement tying an
accepted recommendation to an actual score change. **UI copy must hedge it**, e.g. `"est. +9"`
or `"could help by roughly 9 points"` — never `"+9 guaranteed"` or bare `"+9"` presented as a
committed outcome.

### `body` — AI-personalized shortly after the health score loads

`body` starts life as the catalog's default sentence (the text embedded in
`RECOMMENDATION_CATALOG`, e.g. the `"Put equity splits, vesting, and roles in writing..."` body
shown above) the moment a recommendation row is generated. Completing the assessment (or any
later health recompute) enqueues an
`ai.health.recommendations` job (Module 03) that rewrites each **pending** recommendation's `body`
with an LLM-authored, startup-specific version; this typically lands within a few seconds. There
is no separate endpoint or webhook: **re-fetch `GET /health-score/recommendations`** (or the
`top_recommendations` slice on `GET /health-score`) to pick up the personalized text.

> **AI budget note:** if the workspace is over its daily LLM token budget, this personalization is
> skipped and `body` stays on the catalog default (see `docs/fe-integration-guide-ai-status.md` —
> `GET /ai/status` reports `over_budget`).

**`title` is the stable catalog headline and is never rewritten** — only `body` is
AI-personalized. Render `title` as a fixed label safe to cache/compare across reads; treat `body`
as the field that can change out from under a cached copy.

**`accepted` and `dismissed` recommendations are never rewritten.** The worker only touches rows
whose `status` is still `pending`, and it is idempotent by construction: it re-checks each pending
row's `body` against the catalog default and skips (no LLM call at all, for any row) once nothing
is left to personalize. Reloading the health score repeatedly is safe — it never rewrites an
accepted/dismissed row's `body`, and costs nothing once every pending row has already been
personalized.

`e2e/_captures/health_recommendations_ai/recommendations_after_drain.json` — `GET
/health-score/recommendations` after the `ai.health.recommendations` worker drained; the
`legal.founder_agreement` row's `body` is AI-personalized, `title` unchanged, every other row
still shows its catalog-default `body` (same one-item-per-call stub behavior as the mission
guide's AI reason section — not evidence only one recommendation is ever rewritten per run):

```json
{
  "id": "2767435f-5fb9-4b67-9f3a-9f89fbad1340",
  "dimension": "legal",
  "key": "legal.founder_agreement",
  "title": "Sign a founders' agreement",
  "body": "[stub-llm] body",
  "estimated_lift": 7,
  "effort": "medium",
  "status": "pending",
  "priority": 2
}
```

**Confirmed live: the envelope is a flat list.** `GET /health-score/recommendations` returns
`data` as a **flat array** of recommendation objects (`data: [...]`) — matching the
`recommendations.json` capture shape already shown above in this section, **not**
`data.recommendations`. Full capture (9 rows):
`e2e/_captures/health_recommendations_ai/recommendations_after_drain.json`.

### Accept — founder-only

`POST /api/v1/health-score/recommendations/{rec_id}/accept` (and the parallel `/dismiss`)
require the **founder** role — a workspace `member` gets a `403` (the same
`require_role(founder)` gate used elsewhere in this API, e.g. assessment write routes). Gate
the accept/dismiss buttons on the caller's membership role, not just on being an active member
of the workspace.

`e2e/_captures/health_score/accept.json` (`POST .../recommendations/<id>/accept`):

```json
{
  "data": {
    "id": "3d1a3a35-a918-4e88-9ccb-858aae7197b5",
    "dimension": "legal",
    "key": "legal.incorporate",
    "title": "Complete incorporation",
    "body": "Register the company and issue founder shares. Operating unincorporated exposes founders personally and blocks fundraising.",
    "estimated_lift": 9,
    "effort": "high",
    "status": "accepted",
    "priority": 1
  },
  "meta": null
}
```

### The 409 conflict — a resolved recommendation can't be re-resolved

Calling `dismiss` (or `accept` again) on a recommendation that has already moved out of
`pending` returns `409` with a specific error code the FE should branch on to show a
"someone already actioned this" message rather than a generic error toast:

`e2e/_captures/health_score/conflict.json` (`POST .../recommendations/<same id>/dismiss`,
immediately after the accept above):

```json
{
  "error": {
    "code": "RECOMMENDATION_RESOLVED",
    "message": "That recommendation has already been actioned.",
    "field_errors": []
  }
}
```

Repeating the **same** transition (accept → accept) is idempotent and returns `200`, not `409`
— only a *cross*-transition (accepted → dismissed or vice versa) conflicts. If the FE has
optimistically marked a recommendation accepted locally and the accept call race-loses to
another tab, a `200` on retrying accept is the expected, safe outcome; a `409` only means
someone dismissed it instead.

---

## 6. Cross-tenant / enumeration: uniform 404, never a distinguishable error

`GET /dimensions/{dim}` 404s on an unrecognized dimension key, and both
`POST /recommendations/{id}/accept` and `/dismiss` 404 whenever `{id}` doesn't belong to the
caller's active workspace — **including when the id is real but belongs to a different
workspace**. The server deliberately cannot distinguish "this id doesn't exist" from "this id
exists but isn't yours": both return the identical `NOT_FOUND` shape, so the FE (or a malicious
actor probing ids) can never learn that a recommendation id is valid for someone else's
startup.

`e2e/_captures/health_score/cross_tenant_404.json` — a second founder, in their own workspace,
calling `accept` on the **first** founder's recommendation id:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**FE implication:** never assume a 404 here means "stale/deleted" specifically — treat it the
same as any other not-found (remove the row from local state, don't retry). Do not build any
UI that tries to distinguish "doesn't exist" from "not yours" from this response; the API
won't give you that signal, by design.

---

## Verification table

Every row below was exercised **live**, over real HTTP, against a real Postgres-backed server
(`make e2e`, `e2e/test_health_score.py::test_health_score_journey`) — not just unit-tested
in-process.

| Endpoint / behavior | Verified live (`make e2e`)? |
|---|---|
| `GET /health-score` — `pending_assessment` empty-state | ✅ |
| `GET /health-score` — `ok` (score, 5 dimensions, top_recommendations) | ✅ |
| `GET /health-score/dimensions/{dim}` — happy path (`money` → "Financial", signals) | ✅ |
| `GET /health-score/dimensions/{dim}` — unknown `dim` → 404 | ⬜ (unit-tested only, not in the live E2E journey) |
| `GET /health-score/history?range=` | ✅ (`range=all`) |
| `GET /health-score/benchmarks` — `insufficient_data` gate | ✅ |
| `GET /health-score/recommendations` — non-empty, all pending | ✅ |
| `POST /recommendations/{id}/accept` — 200, `status: "accepted"` | ✅ |
| `POST /recommendations/{id}/dismiss` on a resolved id — 409 `RECOMMENDATION_RESOLVED` | ✅ |
| Founder-only gate on accept/dismiss (member → 403) | ⬜ (unit-tested only, not in the live E2E journey) |
| Cross-tenant enumeration guard — 404 on another workspace's recommendation id | ✅ |
| Recompute triggered inline by assessment completion (no job/poll) | ✅ fixed — `complete_assessment` now flushes before recomputing, so `HealthScore` + pending recommendations + the `ai.health.recommendations` job are created by `POST /assessments/{id}/complete` itself (`tests/services/assessment/test_complete_recompute_flush.py`, autoflush=False reproduction). See §1 update. |
| `recommendations[].body` AI-personalized via `ai.health.recommendations` worker (stub) within seconds of the lazy-read; `title` unchanged — `overview_after_complete.json` → `recommendations_after_drain.json` (`e2e/_captures/health_recommendations_ai/`) | ✅ |
| Recommendations envelope is a flat list (`data: [...]`), not `data.recommendations` — `recommendations_after_drain.json` | ✅ |
| `accepted`/`dismissed` recommendations never rewritten by the AI worker | ⬜ (unit-tested only — `tests/worker/test_health_recommendations_handler.py`; not re-exercised in the live e2e journey, which only has pending recommendations) |

Rows marked ⬜ are covered by the unit suite (`tests/api/test_health_score.py`,
`tests/api/test_health_recommendations.py`) but not independently re-asserted over live HTTP in
`e2e/test_health_score.py` — safe to build against, just not double-verified end-to-end.
