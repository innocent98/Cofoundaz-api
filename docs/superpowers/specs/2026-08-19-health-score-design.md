# Design — Module 06 Health Score

> **Status:** Approved (brainstorm) · **Date:** 2026-08-19 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 06), the shipped Assessment
> (`docs/superpowers/specs/2026-08-16-assessment-design.md`), and the live FE contract in `../cofoundaz/`.
>
> This is the design spec for Module 06. It precedes the implementation plan (writing-plans)
> and the post-ship SOP (`docs/sop/`). Modules 01 (Foundation/Auth/Onboarding) and 07
> (Assessment) are merged to `main` (PRs #1–#5).

---

## 1. Scope

**In scope** — the explainable 0–100 Health Score: an overall score + band, a weekly delta, 5
dimension sub-scores with a drill-down signal table, trend history, stage/industry benchmarks
(cold-start empty-state), and rule-based ranked recommendations with accept/dismiss. The score
is computed **inline** when the kickoff assessment completes and **lazily on read** as a safety
net — no async worker.

**Deferred (behind the same seams, documented not built):**

| Deferred | Seam used now | Built in |
|---|---|---|
| Async worker draining `jobs` | inline recompute + `JobDispatcher` seam remains | Module 05 (Roadmap) |
| 15-min recompute throttle + nightly full pass | inline recompute is event-driven, infrequent | Module 05 |
| Live signals from other hubs (roadmap progress, runway, validation, legal hygiene, team activity) | `health_signals` architecture accepts them with **zero schema change** | Modules 04/05/08+ |
| AI-generated recommendations + AI summary | rule-based catalog + templated summary now | Module 03 (AI Co-Founder) |
| Real benchmark cohort aggregation | honest insufficient-data empty-state + gate | when cohort ≥ `MIN_COHORT_SIZE` |
| Real notification delivery for `healthscore.*` events | `event_bus` emission (truthful, no consumer) | Module 20 |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Computation / recompute model | **Inline recompute + lazy-on-read.** A pure `recompute_health_score(db, startup)` runs synchronously at assessment-complete and lazily on first GET if a row is missing. No async worker. |
| 2 | Signal architecture depth | **Build `health_signals` now** (assessment-derived, one signal/dimension), so the drill-down is real and future hubs append with no schema change. Weights/bands/catalog as **static versioned Python config** — **no `health_dimensions` table**. |
| 3 | Dimension naming | **Reuse the existing `Dimension` enum** (`product/market/money/legal/team`) as the internal keys — key-identical to `assessment_results`, zero mapping. The PRD's **"Financial" is `money`'s display label only**. |
| 4 | Pre-assessment state | **Honest pending empty-state.** No `health_scores` row exists until the first assessment completes; `GET /health-score` returns `200` with `status:"pending_assessment"`. `healthscore.initialize` is a no-op. No fabricated neutral score. |
| 5 | Recommendations | **Rule-based + persisted.** A static versioned catalog generates recommendations for weak dimensions; rows carry a `pending→accepted\|dismissed` lifecycle; regeneration dedupes by catalog key and **never resurrects a dismissed one**. |
| 6 | Benchmarks | **Honest empty-state with a cohort-size gate.** No aggregation pipeline now; the endpoint echoes the cohort definition and returns `insufficient_data` until the cohort clears `MIN_COHORT_SIZE`; contract is stable so it flips on later with no change. |

## 3. Data model (migration `0005_health_score`)

Four new tables, all `startup_id`-scoped, FK `ondelete=CASCADE`. Two new enums in
`app/db/models/enums.py`: `RecommendationEffort(low|medium|high)`,
`RecommendationStatus(pending|accepted|dismissed)`.

### `health_scores` — current score, one row per startup (exists ⇔ ≥1 assessment done)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | **unique**, indexed |
| `score` | Integer | overall 0–100 |
| `band` | String | machine key: `at_risk\|needs_work\|healthy\|thriving` |
| `dimension_scores` | JSONB | `{ "product":int, "market":int, "money":int, "legal":int, "team":int }` |
| `source` | String | provenance, e.g. `assessment` |
| `config_version` | Integer | which weighting/bands produced it |
| `computed_at` | timestamptz | |
| (TimestampMixin) | | created/updated |

Upserted via `ON CONFLICT (startup_id) DO UPDATE`.

### `health_score_history` — append-only trend ledger

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | indexed |
| `score` | Integer | |
| `dimension_scores` | JSONB | snapshot |
| `delta` | Integer | vs the immediately previous history row (0 for the first) |
| `trigger` | String | `assessment_complete\|lazy_read\|…` |
| `config_version` | Integer | |
| `computed_at` | timestamptz | indexed (range queries) |

Feeds `/history`, `delta_7d`, `healthscore.dropped`, `healthscore.record`. Immutable.

### `health_signals` — current-state projection, replaced each recompute

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | indexed |
| `dimension` | String | one of the 5 keys |
| `key` | String | e.g. `assessment.product` |
| `value` | Numeric | raw contributor value (the dim score 0–100) |
| `contribution` | Numeric | weighted points toward overall (`value × weight`) |
| `source_ref` | String | e.g. `assessment:{assessment_id}` |
| `computed_at` | timestamptz | |

The full set for a startup is **deleted and re-inserted** within the recompute transaction, so
signals always explain the *current* score. (History/audit lives in `health_score_history`.)

### `health_recommendations` — persisted lifecycle

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | indexed |
| `dimension` | String | the weak dimension it addresses |
| `key` | String | catalog key, e.g. `money.runway_model` — **dedupe identity** |
| `title` | String | |
| `body` | Text | |
| `estimated_lift` | Integer | heuristic points ("est. +N", not a guarantee) |
| `effort` | `RecommendationEffort` enum | |
| `status` | `RecommendationStatus` enum | default `pending` |
| `priority` | Integer | 1..N, refreshed each recompute |
| `resolved_at` | timestamptz? | set on accept/dismiss |
| (TimestampMixin) | | created/updated |

Unique index on `(startup_id, key)` — a catalog key has at most one row per startup at a time
(user decisions are durable; unacted pendings are deleted, never duplicated). Supports the dedupe lookup.

**Static config (no table):** `app/services/health_score/config.py` holds
`HEALTH_CONFIG_VERSION`, `DIMENSION_WEIGHTS`, `BANDS`, `MIN_COHORT_SIZE`,
`RECOMMENDATION_CATALOG`.

## 4. Compute service — `recompute_health_score(db, startup, *, trigger)`

Pure and deterministic (only `computed_at` stamps depend on the clock). Returns
`HealthScore | None`. All steps run in **one transaction**:

1. Load the **latest completed** `AssessmentResult` for the startup (join `assessments` on
   `startup_id`, `status='completed'`, newest `completed_at`). **If none → return `None`** — the
   workspace stays pending; no row is written.
2. Build **signals** — one `health_signals` row per dimension from the assessment's
   `dimension_scores` (`value`=dim score, `contribution`=`value × weight`,
   `source_ref="assessment:{id}"`). **Replace** the startup's signal set (delete + insert).
3. Compute **dimension scores** (v1 = assessment dim scores verbatim, already 0–100),
   **overall** = `round(Σ weight[d] × dim[d])` clamped 0–100, **band** via thresholds.
4. Read previous `health_scores` row for the **delta**; **upsert** `health_scores`
   (`ON CONFLICT (startup_id) DO UPDATE`); **append** a `health_score_history` row
   (`delta` vs previous history row, `trigger`, `config_version`).
5. **Regenerate recommendations** (§4.3).
6. **Emit events** (§5.1): `healthscore.updated` always; `healthscore.dropped` if trailing-7d
   delta ≤ −5; `healthscore.record` if new all-time max (first score is not a record).

**Wiring:**
- `app/services/assessment/service.py::complete_assessment` calls `recompute_health_score(...)`
  **inline**, replacing the `healthscore.recalculate` stub enqueue.
- `GET /health-score` is the **lazy-on-read** net: row exists → return; no row but a completed
  assessment exists → compute now → return; neither → pending empty-state.
- `healthscore.initialize` (onboarding) becomes a **no-op**.

### 4.1 Weights & overall (static config)

```python
HEALTH_CONFIG_VERSION = 1
DIMENSION_WEIGHTS = {"product":0.20,"market":0.20,"money":0.20,"legal":0.20,"team":0.20}  # sum 1.0
```
`overall = clamp(round(sum(DIMENSION_WEIGHTS[d] * dim[d] for d in DIMENSIONS)), 0, 100)`.
Equal weights for v1 (no evidence to justify asymmetry); tuning = config edit + version bump.

### 4.2 Bands

```python
BANDS = [(0,39,"at_risk"),(40,59,"needs_work"),(60,79,"healthy"),(80,100,"thriving")]
```
`band(score)` maps overall → label; each dimension also gets its own band (same thresholds) for
drill-down colour-coding. Labels are machine keys; the FE owns display copy.

### 4.3 Recommendation generation & reconciliation

Static versioned catalog keyed by dimension:
```python
RECOMMENDATION_CATALOG = {
  "money": [{"key":"money.runway_model","title":"…","body":"…",
             "estimated_lift":8,"effort":"medium","triggers_below":60}, …],
  "legal": [...], "product": [...], "market": [...], "team": [...],
}
```
Algorithm (step 5 of recompute):
1. For each dimension with `score < triggers_below`, collect matching catalog entries.
2. **Rank** candidates by `priority = (triggers_below − dim_score) × weight[dim]` desc,
   tiebreak `estimated_lift` desc; assign integer `priority` 1..N.
3. **Reconcile** against existing rows for the startup, keyed on catalog `key`. User decisions
   (accepted/dismissed) are durable; auto-generated pending cards are ephemeral:
   - key **user-dismissed** → **skip** (never resurrect a decision the founder made).
   - key **accepted** → **keep**, refresh `priority`/`estimated_lift` (no dup row).
   - key **pending** and still weak → **keep**, refresh `priority`.
   - key **new** (no row) → insert `pending`.
   - existing **pending** whose dimension **recovered** above threshold → **delete** (auto-generated,
     never acted on — nothing to preserve; may be regenerated later if the dimension weakens again).
     **Accepted rows are always left untouched.**

`estimated_lift` is a catalog-authored heuristic ("est. +N"), never presented as a guarantee —
called out in the FE guide.

## 5. Events, errors, versioning

### 5.1 Events (via `event_bus.publish`, fire-and-forget; no consumer built — Module 20 delivers)

| Event | When | Payload |
|---|---|---|
| `healthscore.updated` | every recompute that writes a row | `{startup_id, score, previous_score, band, delta_7d, computed_at, config_version}` |
| `healthscore.dropped` | trailing-7d delta ≤ −5 | `{startup_id, score, previous_score, delta_7d, computed_at}` |
| `healthscore.record` | new all-time max (not first score) | `{startup_id, score, previous_max, computed_at}` |

`healthscore.initialize`/`healthscore.recalculate` **jobs** are retired from the enqueue path
(superseded by inline recompute); a one-line note is added to the onboarding + assessment SOPs.

### 5.2 Errors (reuse `AppError` taxonomy; one new code)

| Code | HTTP | When |
|---|---|---|
| `NOT_FOUND` | 404 | unknown `{dim}`; recommendation id absent **or in another workspace** (uniform, non-leaky) |
| `VALIDATION_ERROR` | 422 | bad `range` / `status` query value |
| `RECOMMENDATION_RESOLVED` | 409 | cross-transition on an already-resolved recommendation |
| `FORBIDDEN` | 403 | non-founder on accept/dismiss |
| `EMAIL_NOT_VERIFIED` | 403 | unverified user (shared guard) |

Pending score and insufficient-cohort benchmarks are **`200` empty-states, not errors**.

### 5.3 Config versioning

`HEALTH_CONFIG_VERSION` is stamped on every `health_scores`/`health_score_history` row. Changing
weights, bands, or the catalog is a config edit + version bump — no migration; historical scores
remain interpretable under the weighting that produced them.

## 6. Endpoints

All under `/api/v1/health-score`. All require an authenticated, **email-verified** active member
of the workspace resolved from `X-Workspace-Id`. **Reads = any active member; mutations
(accept/dismiss) = founder only** (per-route 403 test).

| # | Route | Access | Returns |
|---|---|---|---|
| 1 | `GET /health-score` | member | Overview: pending state, **or** `{status:"ok", score, band, delta_7d, computed_at, config_version, dimensions:[{key,label,score,band}], top_recommendations:[≤3], summary}`. Triggers lazy compute if needed. |
| 2 | `GET /health-score/dimensions/{dim}` | member | `{key,label,score,band, signals:[{key,value,contribution,source_ref}], trend:[…], recommendations:[…]}`. `dim`∉5 keys → `404`. |
| 3 | `GET /health-score/history?range=7d\|30d\|90d\|all` | member | `[{score,dimension_scores,delta,computed_at}]` ascending. Default `30d`; bad range → `422`. |
| 4 | `GET /health-score/benchmarks` | member | `{status:"insufficient_data", cohort:{stage,industry}, min_cohort_size, percentiles:null}` until cohort ≥ min. |
| 5 | `GET /health-score/recommendations?status=` | member | Ranked list; default `pending` by `priority`; optional `status` filter. |
| 6 | `POST /health-score/recommendations/{id}/accept` | founder | `pending→accepted` (`resolved_at`). Same-status idempotent `200`; cross-transition `409`. Cross-workspace `404`. |
| 7 | `POST /health-score/recommendations/{id}/dismiss` | founder | `pending→dismissed` (`resolved_at`). Durable — never regenerated. Same idempotency/409/404 rules as accept. |

**Overview `summary`** is a templated string (assessment-narrative pattern) — e.g. naming the
weakest dimension and the top recommendation. AI summary deferred to Module 03.

## 7. Testing

- **TDD**, real Postgres + per-test rollback; add `create_health_score` / `create_recommendation`
  factories.
- **Scoring:** weighted overall to exact integers, clamp, each band boundary (39/40/59/60/79/80),
  `config_version` stamped.
- **Delta/drop/record:** seed history → assert `delta_7d`; `healthscore.dropped` at ≤−5;
  `healthscore.record` on new max and **not** on first score.
- **Recommendations:** weak-dimension selection, ranking order, **dedupe by key**,
  **no-resurrect of user-dismissed**, **delete of recovered unacted pendings** (and regenerate if
  weak again later), accepted-rows-left-untouched across recompute.
- **Lifecycle:** accept/dismiss happy paths, same-status idempotency `200`, cross-transition `409`.
- **States:** pending empty-state (no assessment), lazy-on-read compute, benchmarks cohort gate.
- **Tenancy + access:** cross-workspace `404` on dimension/recommendation/history; founder-only
  `403` on accept/dismiss; verified-user gate on all routes.
- **Live E2E** (`e2e/`): founder signup→verify→login→onboard→**assessment complete**→
  `GET /health-score` returns a **real** score derived from the assessment→drill one dimension
  (real signals)→`/history` ≥1 point→`/recommendations` non-empty→**accept one**→re-GET reflects
  `accepted`→`/benchmarks` shows the insufficient-data gate. Plus a second workspace cannot
  read/mutate the first's score.
- **FE integration guide** (`docs/fe-integration-guide-health-score.md`, per the FE-guide
  standard): every payload copied **verbatim from live E2E captures** — overview (pending + ok),
  dimension drill-down, history, benchmarks gate, recommendations, accept/dismiss (incl. `409`) —
  with the pending↔ok state machine, polling guidance, and the "est. lift is a heuristic, not a
  promise" UX note; verification table at the end.

## 8. Plan shape

One implementation plan (`writing-plans`), ~11 TDD tasks, subagent-driven (fresh implementer +
independent review + fix loop per task, then a whole-branch review), same rhythm as Modules 01/07:

1. Enums (`RecommendationEffort`, `RecommendationStatus`) + 4 models + factories
2. Migration `0005_health_score`
3. `config.py` (weights/bands/catalog/min-cohort) + pure `scoring` (overall/band/delta)
4. `recompute_health_score` service — signals, upsert, history, events
5. Recommendation generation + reconciliation
6. Wire inline recompute into `complete_assessment`; retire the stub job; note in SOPs
7. `GET /health-score` (overview + pending + lazy-on-read)
8. `GET /dimensions/{dim}` + `GET /history`
9. `GET /benchmarks` (cohort gate) + `GET /recommendations`
10. `POST …/accept` + `…/dismiss` (idempotency / 409 / founder gate)
11. Live E2E + SOP + FE integration guide + checklist reconcile
