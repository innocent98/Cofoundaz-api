# Module 12 — Finance Hub, Slice 2: Runway & Scenarios — Design

**Status:** Draft for review
**Date:** 2026-09-29
**Depends on:** Slice 1 (Cash Flow) — `Transaction` model, `cash_flow_summary`, `/finance` router, RBAC gate.
**Spec author note:** every payload shape here is a *target*; the FE integration guide will be re-derived verbatim from live e2e captures at build time (never from this doc).

---

## 1. Goal

Give the Finance → **Runway & Scenarios** screen a real backend: persist a startup's planning
assumptions, project cash balance forward under **Base / Best / Worst** scenarios (runway months,
cash-out date, monthly balance series), and turn a low runway into a first-class signal — a
`finance.runway.low` domain event + in-app notification (fired once on transition) and an
informational runway signal on the Health-Score **Financial** dimension.

## 2. FE cross-check (`../cofoundaz/app/(dashboard)/finance/runway/page.tsx`)

The screen is currently a pure client-side mock. It exposes exactly three assumption inputs and
renders four things off them:

| FE element | Today (mock) | What the backend must supply |
| --- | --- | --- |
| Scenario tabs Base/Best/Worst | local `useState` | three computed scenarios in one payload |
| Big "N.N Months" + "cash out: Mon YYYY" | `runwayMonths ± fudge` | `runway_months` + `cash_out_date` per scenario |
| "Avg Net Burn (Scenario)" | `burn × {0.85,1,1.2}` | `avg_net_burn_minor` per scenario |
| Cash Balance Projection chart | hard-coded SVG paths | `by_month` cash-balance series per scenario |
| Assumptions: MoM Growth %, Hiring Lines (₦K/mo), One-off Costs (₦K) | sliders/input | persisted `mom_growth_percent`, `hiring_spend_minor`, `one_off_costs_minor` |

**Consequence for the FE guide:** the FE stores hiring/one-off as **₦ thousands**; the API stores
**minor units** (kobo). The guide must state the unit conversion explicitly (this is exactly the
kind of field-nesting/unit trap the guide rule exists to catch).

## 3. Data model

One new table, **`finance_runway_settings`** — one row per startup, holding both the user-editable
assumptions and the system-owned alert-dedup state. (Kept as a single row/table for simplicity;
the two concerns are documented distinctly below.)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | `UUIDMixin` |
| `startup_id` | UUID FK→startups CASCADE, **unique**, index | one row per startup |
| `mom_growth_percent` | Integer, default 0 | assumption; `0 … 1000` (validated) |
| `hiring_spend_minor` | Integer, default 0 | assumption; added monthly cost; `0 … 2_147_483_647` |
| `one_off_costs_minor` | Integer, default 0 | assumption; deducted once at month 0; `0 … 2_147_483_647` |
| `alert_is_low` | Boolean, default False | **system** — last emitted runway-low state (dedup) |
| `alert_last_fired_at` | timestamptz, nullable | **system** — when the last `finance.runway.low` fired |
| `created_at` / `updated_at` | timestamptz | `TimestampMixin` |

Migration: **`0040_finance_runway`** (id ≤ 32 chars), additive single `create_table`, reversible.
`down_revision` = current develop head at build time (expected `0039_finance_transactions`).
**Migration-number watch:** re-check develop head immediately before building; if another
`0040_*` landed first, bump to `0041_finance_runway`. Use the robust single-head + revision-in-
history test form (memory `migration-head-test-brittle`).

Projection horizon is a **config constant** (`RUNWAY_HORIZON_MONTHS = 12`), not user input.

## 4. Scenario projection model

A transparent, deterministic model built on Slice-1's `cash_flow_summary` baseline
(`cash_on_hand`, `monthly_burn`, `monthly_revenue`, `currency`). All constants live in a documented
`app/services/finance/scenario_config.py` so they are tunable in one place.

**Baseline reconstruction** (from Slice-1 outputs, no new queries needed):
- `monthly_revenue_base = monthly_revenue`
- `monthly_costs_base = monthly_burn + monthly_revenue`
  (Slice-1 defines `monthly_burn = max(0, (out−in)/3)` and `monthly_revenue = in/3`, so
  total monthly outflow `out/3 = monthly_burn + monthly_revenue` — exact, not an estimate.)

**Per-scenario knobs** (`scenario_config.py`):

| Scenario | growth applied | cost multiplier |
| --- | --- | --- |
| Base | `g` | `1.00` |
| Best | `g + 10` (pp) | `0.90` |
| Worst | `max(0, g − 10)` (pp) | `1.15` |

where `g = mom_growth_percent`. Constants: `BEST_GROWTH_BONUS_PP=10`, `WORST_GROWTH_PENALTY_PP=10`,
`BEST_COST_MULT=0.90`, `WORST_COST_MULT=1.15`.

**Projection** (per scenario, `H = RUNWAY_HORIZON_MONTHS`):
```
cash_0     = cash_on_hand − one_off_costs_minor            # one-off applied up front
for i in 1..H:
    revenue_i = round(monthly_revenue_base × (1 + growth/100)^i)
    costs_i   = round(monthly_costs_base × cost_mult + hiring_spend_minor)
    net_i     = revenue_i − costs_i
    cash_i    = cash_{i-1} + net_i
by_month[i] = { month: "YYYY-MM", cash_balance: cash_i, net: net_i }
```
- `avg_net_burn_minor = max(0, round(mean(costs_i − revenue_i for i in 1..H)))` (a burn, so ≥0).
- **runway_months**: the fractional month where the running balance first crosses ≤ 0, by linear
  interpolation between `cash_{i-1}` and `cash_i`; `None` if the balance never goes ≤ 0 within `H`
  (cash-positive within horizon) **or** if `cash_0 ≤ 0` *and* the trajectory is non-burning.
  If `cash_0 ≤ 0` while burning → `runway_months = 0.0` (already out).
- **cash_out_date**: `today + runway_months` (month resolution, `"YYYY-MM"`), `None` when
  `runway_months is None`.

**Edge cases covered by tests:** no transactions (all zero, all scenarios `runway_months=None`,
`cash_out_date=None`); cash-positive/growing (never crosses → `None`); `growth=1000%`
(no overflow — Python ints); `hiring_spend`/`one_off` at int32 max (bounded at validation).

## 5. Endpoints (all under `/api/v1/finance`, gated by the Slice-1 `_finance` RBAC dep: founder, team_member, accountant)

### `GET /finance/runway`
Returns assumptions + baseline + the three scenarios in one call (powers the whole screen).
```jsonc
{ "data": {
  "assumptions": { "mom_growth_percent": 15, "hiring_spend_minor": 50000000, "one_off_costs_minor": 0 },
  "baseline": { "cash_on_hand": 2104200000, "monthly_burn": 335000000,
                "monthly_revenue": 160000000, "currency": "NGN" },
  "horizon_months": 12,
  "scenarios": {
    "base":  { "runway_months": 5.2, "cash_out_date": "2026-09", "avg_net_burn_minor": 335000000,
               "by_month": [ { "month": "2026-04", "cash_balance": 1769200000, "net": -335000000 }, ... ] },
    "best":  { "runway_months": 7.6, "cash_out_date": "2026-11", ... },
    "worst": { "runway_months": 3.9, "cash_out_date": "2026-08", ... }
  } } }
```
- Lazy default: if no settings row exists, treat assumptions as all-zero (no row is created on GET).

### `PUT /finance/runway/assumptions`
Upsert the three assumptions; returns the **same** payload as `GET /finance/runway` (recomputed).
```jsonc
// request
{ "mom_growth_percent": 20, "hiring_spend_minor": 75000000, "one_off_costs_minor": 500000000 }
```
- Validation: `mom_growth_percent` `0 … 1000`; `hiring_spend_minor`/`one_off_costs_minor`
  `0 … 2_147_483_647`. Explicit `null` on any field → 422 (reuse the Slice-1 `model_validator`
  approach). Partial update allowed (omitted fields keep stored value).
- Commits (endpoint), service flushes only (Slice-1 discipline).

## 6. `finance.runway.low` event + notification (fire-once-on-transition)

Runway-low is a **derived** state, so it is evaluated on the mutations that can change it — the
Slice-1 transaction `POST`/`PATCH`/`DELETE` endpoints — via a new
`evaluate_runway_alert(db, *, startup_id)` called after the service flush, before commit.

```
low_now = _runway_is_low(cash_flow_summary(db, startup_id))   # reuse Slice-1 runway_low logic
row = get-or-create finance_runway_settings(startup_id)
if low_now and not row.alert_is_low:      # transition into danger
    row.alert_is_low = True; row.alert_last_fired_at = now
    event_bus.publish(db, "finance.runway.low",
        { "startup_id": ..., "runway_months": ..., "monthly_burn": ..., "currency": ... })
elif (not low_now) and row.alert_is_low:  # recovery → re-arm, no event
    row.alert_is_low = False
# else: no change, no event
```
- `_runway_is_low` is the **single source of truth**, extracted from Slice-1's `cash_flow_summary`
  (`monthly_burn > 0 and (runway_months is None or runway_months < 6)`) into a shared helper so
  cash-flow and the alert never diverge.
- Notification: add one row to `app/services/notifications/registry.py` `SPECS`:
  `"finance.runway.low": _s(_all_active_members, "Your runway is running low", "…")`.
  The existing event→notification handler turns the published event into in-app notifications for
  all active members (email follows existing preference gating).
- Idempotence: evaluation runs on every transaction mutation but only writes/fires on a *change*,
  so re-saving transactions while already-low emits nothing.

## 7. Health-Score financial signal (informational, non-scoring)

Signals are **display-only** — `weighted_overall` scores from `dimension_scores`, never from
signals — so adding a runway signal cannot move the score. We add a `money.runway_live` signal:

- **Shared builder** `build_runway_signal(db, startup_id) -> HealthSignal-fields | None`:
  `dimension="money"`, `key="money.runway_live"`, `value=` runway months capped `0…999.99`
  (Numeric(6,2)); `None` when cash-positive stores `value=0` with a distinct `source_ref`;
  `contribution=0.00` (non-scoring); `source_ref="finance:cash-flow"`.
- **In `recompute_health_score`**: after the assessment-signal loop (which `delete()`s all signals
  then re-adds), also add the runway signal, so a full recompute rebuilds it too.
- **On transaction mutation** (same hook as §6): targeted upsert of *only* the `money.runway_live`
  row (delete-by-key-then-add for that startup) so the Financial breakdown reflects live cash-flow
  without waiting for an assessment recompute. Assessment signals are untouched.
- No new endpoint — the existing `GET /health-score/dimensions/money` detail surfaces it.

## 8. Files

**Create**
- `app/db/models/finance_runway.py` — `FinanceRunwaySettings` model.
- `app/services/finance/scenario_config.py` — horizon + scenario constants.
- `app/services/finance/runway.py` — `get_or_default_settings`, `upsert_assumptions`,
  `project_scenarios`, `evaluate_runway_alert`, `_runway_is_low`, `build_runway_signal`,
  `upsert_runway_signal`.
- `app/schemas/finance_runway.py` — `AssumptionsUpdate`, `ScenarioPoint`, `Scenario`,
  `RunwayResponse`.
- `alembic/versions/0040_finance_runway.py`.
- Tests: `tests/api/test_finance_runway.py`, `tests/db/test_finance_runway_model.py`,
  `tests/test_finance_runway_migration.py`, `tests/services/test_runway_scenarios.py`,
  `tests/services/test_runway_alert.py`, `e2e/test_finance_runway.py` (+ captures).

**Modify**
- `app/api/v1/endpoints/finance.py` — add `GET /runway`, `PUT /runway/assumptions`; call
  `evaluate_runway_alert` + `upsert_runway_signal` in the 3 transaction mutation routes.
- `app/services/finance/cashflow.py` — extract `_runway_is_low` (import from `runway.py` or a
  shared spot to avoid a cycle; put the predicate in `runway.py` and have cashflow import it, or
  keep it in a dependency-free module — **no service↔helper cycle**, per memory).
- `app/services/health_score/service.py` — add runway signal inside `recompute_health_score`.
- `app/services/notifications/registry.py` — add the `finance.runway.low` SPEC row.
- Docs: `docs/fe-integration-guide-finance-runway.md` (new, verbatim from captures),
  `docs/sop/2026-09-29-finance-slice2.md`, `docs/checklist/PROJECT_CHECKLIST.md`.

## 9. Cross-cutting rules (carried)

- **Cycle safety** (memory `codeql-required-check-test-patterns` / Module 10 lesson): the runway
  predicate is the shared dependency; place it so `cashflow.py`, `runway.py`, and health-score all
  import *downward* only — no A↔B import. Verified in pre-flight scan.
- Money bounded to int32 at validation (422, never 500). Response money fields are JSON ints.
- Services `flush()`, endpoints `commit()`; `get_db` does not auto-commit.
- Tenancy: every query `filter_by(startup_id=...)`; startup_id from membership, never the body.
- CodeQL test hygiene: no mutating-call-in-assert, no implicit str-concat in list literals.
- No AI attribution in commits/PR (global rule).

## 10. Testing

- **Unit (scenarios):** baseline reconstruction; Base/Best/Worst ordering (worst ≤ base ≤ best
  runway); one-off reduces cash_0; hiring raises costs; growth compounding; `None`/`0.0`/positive
  runway branches; interpolation correctness; horizon length; over-int32 assumption → 422;
  `growth=1000` no overflow.
- **Unit (alert):** False→True fires once (event published, state persisted); True→True no event;
  True→False re-arms silently; notification row created for active members.
- **Unit (health signal):** runway signal present after recompute; refreshed on transaction change;
  `contribution=0` so score unchanged; survives/rebuilds across recompute.
- **API:** RBAC (accountant allowed, mentor/investor 403); GET default (no row) all-zero;
  PUT upsert roundtrip; explicit-null → 422; cross-tenant isolation.
- **e2e:** seed transactions → GET runway (3 scenarios) → PUT assumptions → GET reflects change →
  push into low runway via a transaction → assert notification created. Commit captures.
- Local CI parity green before push (pytest ≥95% cov, alembic single-head + check, e2e, static).

## 11. Deferred (later slices, tracked in checklist)

- Multi-currency projection (single prevailing currency, as Slice 1).
- Runway signal as a **scoring** modifier (this slice is informational only).
- Assumption history / multiple saved scenarios (one settings row per startup for now).
- Financial Model AI (Slice 5), Invoices (Slice 3), Expenses/Budgets (Slice 4), Integrations (Slice 6).
```
