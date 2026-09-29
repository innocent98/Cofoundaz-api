# SOP — Module 12 Finance Hub, Slice 2: Runway & Scenarios (persisted assumptions, 3-scenario projection, low-runway alert, informational Health Score signal)

**What shipped** — Module 12's second slice (PRD 12.2, Runway). **Slice 2 of 6.**

1. **Runway settings** — one `finance_runway_settings` row per workspace holding the three
   user-editable assumptions (`mom_growth_percent`, `hiring_spend_minor`, `one_off_costs_minor`) plus
   the system-owned alert dedup state (`alert_is_low`, `alert_last_fired_at`).
2. **`GET /finance/runway` + `PUT /finance/runway/assumptions`** — a single call returns
   `assumptions` + `baseline` (the Slice-1 actuals) + `horizon_months: 12` + three scenarios
   (`base` / `best` / `worst`), each with `runway_months`, `cash_out_date`, `avg_net_burn_minor` and a
   12-point `by_month` projection. The PUT is a partial update and returns the same recomputed shape.
3. **`finance.runway.low` event + in-app notification** — fires **once per transition** into low
   runway; recovery re-arms it. In-app only.
4. **`money.runway_live` Health Score signal** — informational, non-scoring (`contribution` 0),
   visible on `GET /health-score/dimensions/money`.

Migration **`0040_finance_runway`** (one new table), new modules under `app/services/finance/`
(`runway_math.py`, `scenario_config.py`, `runway.py`), 2 new routes under `/finance` behind the same
`require_role(founder, team_member, accountant)` as Slice 1.

Commits (branch `feat/module-12-finance-slice2`, off `develop` at `b973548`; not yet merged, no PR
opened yet), oldest to newest:
`d758e03` (design spec) → `d22a3ce` (implementation plan) →
`66ce13c` (`finance_runway_settings` model + migration `0040`) →
`753a86f` (scenario projection engine + shared `_runway_is_low` predicate) →
`aa14810` (fix: use actual monthly costs and zero-cash runway in the projection) →
`c4fd43d` (`GET /finance/runway` + `PUT /finance/runway/assumptions`) →
`f07379d` (test: guard the runway baseline split against a `monthly_burn` regression) →
`e054318` (`finance.runway.low` event + notification on transition) →
`02ff300` (informational `money.runway_live` health signal) →
`5db85c7` (e2e runway journey + 21 captures) → this docs commit.

## Why

Slice 2 of 6 in the Module 12 plan, PRD 12.2 **Runway**. Slice 1 exposed the *current* runway number
and a `runway_low` flag; founders' next question is "what happens if we hire / grow / spend a lump
sum?", and "tell me before it's too late". Slice 1 deliberately deferred all three: what-if
scenarios, the `finance.runway.low` event, and the finance signal into the Health Score.

The FE Runway screen exposes hiring and one-off costs as **₦ thousands**, while every money value in
the API is integer **minor units (kobo)**. The API stays minor-units-only (consistent with Slice 1);
the FE converts (`× 100 000` in, `÷ 100 000` out). This is the single most likely integration bug and
is called out first in the FE guide.

## How

**The projection model** (`app/services/finance/runway_math.py`, pure — no DB/ORM). For each
scenario, month `i = 1..12`: `revenue_i = round(monthly_revenue × (1 + growth)^i)`,
`cost_i = round(monthly_costs × cost_mult + hiring_spend)`, `net_i = revenue_i − cost_i`, balance
accumulates from `starting_cash = cash_on_hand − one_off_costs`. `monthly_revenue` / `monthly_costs`
are the trailing-3-calendar-month averages from Slice 1. Scenarios are fixed constants
(`scenario_config.py`): **base** = assumptions as saved; **best** = growth +10 pp, costs × 0.90;
**worst** = growth −10 pp (floored at 0), costs × 1.15. `runway_months` is the fractional month at
which the balance first crosses zero (linear interpolation inside that month), rounded to 1 dp for
display; `cash_out_date` (`"YYYY-MM"`) is derived from the **unrounded** value. `by_month[0]` is the
month after the current month; `by_month[i].cash_balance` is the projected end-of-month balance.

**`runway_months` semantics.** `null` = the balance never reaches zero inside the 12-month horizon
(not "unknown", and not necessarily "profitable today"); `0.0` = already out of cash (starting cash
after one-offs `<= 0`, in every scenario); otherwise the fractional months. `avg_net_burn_minor` is
clamped at 0.

**Projection uses the true (unclamped) monthly costs.** The first cut fed the projection Slice 1's
`monthly_burn`, which is clamped at 0 for profitable startups — so a profitable startup's costs
were silently treated as zero. Fixed in `aa14810`: `trailing_monthly_flows()` in `cashflow.py`
returns `(monthly_revenue, monthly_costs)` unclamped and shares the 3-month window helper with
`cash_flow_summary`, so the two can never diverge; `f07379d` adds a regression guard. `baseline` in
the response still reports the Slice-1 display values (clamped `monthly_burn`).

**Shared `_runway_is_low` predicate** (`runway_math.py`): `monthly_burn > 0 and (runway_months is
None or runway_months < 6)`. Single source of truth for **both** `cash_flow_summary`'s `runway_low`
flag and the alert, so the banner and the notification can never disagree. It lives in `runway_math`
(not `runway`) to keep the import graph acyclic: `cashflow → runway_math`, `runway → cashflow,
runway_math`.

**Fire-once-on-transition dedup** (`runway.py::evaluate_runway_alert`). State lives on the settings
row: `alert_is_low`. On every transaction write, `low_now` is recomputed; `low_now and not
alert_is_low` → set the flag, stamp `alert_last_fired_at`, flush, publish `finance.runway.low`;
`not low_now and alert_is_low` → clear the flag (**re-arm**); otherwise nothing. A still-low
startup therefore never re-notifies per transaction, and a recover-then-relapse fires again. The
`finance.runway.low` spec in `notifications/registry.py` targets `_all_active_members` (no actor
exclusion); there is deliberately **no** entry in `notifications/categories.py`, so it is in-app only.
*Alternative rejected:* time-based dedup (e.g. once per 24h) — it would re-notify a startup that has
been continuously low and suppress a genuine relapse inside the window.

**Non-scoring health signal** (`runway.py::build_runway_signal` / `upsert_runway_signal`). A
`HealthSignal` row (`dimension="money"`, `key="money.runway_live"`, `value` = months capped at 999.99
and rounded to 2 dp, `contribution=0.00`, `source_ref="finance:cash-flow"`) is refreshed on every
transaction write and rebuilt inside `health_score.service`'s recompute (whose blanket signal delete
would otherwise wipe it). Health Score scoring never reads signals, so it cannot move the overall
score. **`value` is `0` for both cash-positive (`runway_months` null) and out-of-cash** — a known
ambiguity; the FE guide tells the FE to key on the signal `key` and use `GET /finance/cash-flow` for
danger, never read 0 as either state. No signal is stored for a workspace with no transactions.
*Alternative rejected:* feeding runway into the money-dimension **score** — a scoring change that
needs its own design and would silently move existing customers' scores.

**Settings row semantics.** `GET /finance/runway` never creates the row (defaults are synthesized as
`0/0/0`). The row is created lazily by the first `PUT` **or** the first transaction mutation (the
alert needs somewhere to keep `alert_is_low`). `_get_or_create_settings` does `db.add()` **inside**
`db.begin_nested()` so a concurrent first-writer race on `unique(startup_id)` rolls back cleanly to a
savepoint and adopts the winner's row instead of a 500.

**Validation.** `AssumptionsUpdate`: `mom_growth_percent` `0..1000`, the two minor-unit fields
`0..2_147_483_647` (Postgres `Integer`; an unbounded value would 500 at flush). An explicit `null`
(the columns are `NOT NULL`) is rejected as a 422 by a `model_validator` using `model_fields_set`,
mirroring Slice 1's `TransactionUpdate`. Omitted fields are a valid partial update.

**RBAC** unchanged from Slice 1: `founder` / `team_member` / `accountant`; other roles → 403.

## What's involved

**Migration `0040_finance_runway`** (`alembic/versions/0040_finance_runway.py`, chains off
`0039_finance_transactions`, single alembic head) — one additive `create_table`
(`finance_runway_settings`; `startup_id` FK `CASCADE`) plus a **unique** index on `startup_id`. No
existing table touched; downgrade drops the table.

**Model** — `app/db/models/finance_runway.py`: `FinanceRunwaySettings` (registered in
`app/db/models/__init__.py`).

**Schemas** — `app/schemas/finance_runway.py`: `AssumptionsUpdate`, `AssumptionsOut`,
`RunwayBaseline`, `ScenarioPoint`, `Scenario`, `RunwayResponse`.

**Services** — `app/services/finance/runway_math.py` (`project_scenarios`, `_project_one`,
`_runway_is_low`, `_add_months`), `scenario_config.py` (`RUNWAY_HORIZON_MONTHS`, `SCENARIOS`),
`runway.py` (`runway_payload`, `upsert_assumptions`, `evaluate_runway_alert`,
`build_runway_signal`, `upsert_runway_signal`), and `cashflow.py` (`trailing_monthly_flows`, shared
3-month window helper; `runway_low` now uses the shared predicate).

**Integration touch-points** — `app/api/v1/endpoints/finance.py`: `POST` / `PATCH` / `DELETE
/transactions` now call `evaluate_runway_alert` + `upsert_runway_signal` before `db.commit()` (same
transaction); `app/services/notifications/registry.py` (one `SPECS` row);
`app/services/health_score/service.py` (rebuilds the signal after its blanket delete).

**Endpoints** (`app/api/v1/endpoints/finance.py`, mounted at `/finance`):

| Route | Purpose |
|---|---|
| `GET /finance/runway` | assumptions + baseline + 3 scenarios (read-only; creates no row) |
| `PUT /finance/runway/assumptions` | partial update of the 3 assumptions → same recomputed payload |

**Tests**
- `tests/services/test_runway_scenarios.py` (11) — worst ≤ base ≤ best ordering; fractional crossing
  month; already-out-of-cash → 0.0; never-runs-out → None; one-off reduces starting cash; horizon
  length / overflow on extreme growth; `_runway_is_low` predicate; profitable startup not shown as
  burning; zero cash / one-off > cash → out of cash now; `cash_out_date` from unrounded runway.
- `tests/api/test_finance_runway.py` (19) — accountant allowed; RBAC 403 (parametrized) on GET+PUT;
  auth required; default with no row; PUT round-trip changes scenarios; partial PUT; GET creates no
  row; explicit-null 422 (parametrized); over-int32 / growth > 1000 / negative → 422; cross-tenant
  isolation; profitable projection uses true costs; transaction into low runway notifies once;
  healthy transaction notifies nothing; PATCH/DELETE run the alert hook.
- `tests/services/test_runway_alert.py` (3) — fires once, re-arms on recovery, re-fires; healthy never
  fires and the row stays disarmed; in-app notification for active members.
- `tests/services/test_runway_health_signal.py` (7) — non-scoring signal on recompute; cash-positive
  stores 0; upsert refreshes a single row and leaves assessment signals alone; delete-all-transactions
  removes it; no transactions → no signal; rebuilt after the blanket delete; endpoints maintain it.
- `tests/db/test_finance_runway_model.py` (2) + `tests/test_finance_runway_migration.py` (1).
- `e2e/test_finance_runway.py::test_finance_runway_journey` (new) — see Verification.

**Docs**
- `docs/fe-integration-guide-finance-runway.md` (new) — every body pasted verbatim from the captures;
  the ₦-thousands / kobo trap, null-vs-`0.0` runway semantics, the value-0 signal ambiguity, and the
  fire-once notification rules called out up front; verification table.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 12 Slice 2 marked done.

## Verification

**Unit / static.** Full unit suite **1632 passed** (`poetry run pytest`); the six Slice-2 test files
above hold 43 tests, and run together with `tests/api/test_finance.py` (which gained a Slice-2 guard)
they are **58 passed**. CI parity, run through the project's own
toolchain: `poetry run black --check app tests`, `isort --check-only app tests`,
`ruff check app tests`, `mypy app` — all clean (`mypy`: no issues in 196 source files).

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 60 passed**, including the new
`test_finance_runway_journey` (no Resend-429 flake this run). The journey seeds a raise, an invoice,
payroll and hosting (cash 79 000 000; burn 7 000 000; revenue 3 000 000; base runway 11.3) and then:
`GET /finance/runway` (3 scenarios, horizon 12, `best` runway `null`) → `PUT` all three assumptions
(base runway 11.3 → 9.1; baseline unchanged) → partial `PUT` → three 422s (negative, explicit null,
growth > 1000) → unauthenticated 401 → a ₦500,000 outflow drives runway to 1.2 months and **exactly
one** `finance.runway.low` notification appears → a further outflow while still low adds **none**
→ a ₦3,000,000 inflow makes the workspace cash-positive (all scenarios `null`, signal value 0) → a
₦4,000,000 outflow takes it out of cash (all scenarios `0.0`, signal value 0) and the alert fires a
**second** time (re-arm proven live). The numbers reconcile by hand. 21 captures under
`e2e/_captures/finance_runway/` are the verbatim source of the FE guide. Migration `0040` was applied
from zero by the runner; `alembic heads` — single head `0040_finance_runway`; develop still tops out
at `0039_finance_transactions` (checked against `origin/develop` before this commit), so no
renumbering was needed.

**Honest gap disclosure (unit-only, not e2e-captured).** 403 for non-finance roles and accountant
access; the over-int32 422; PATCH/DELETE running the alert hook; the alert reaching *other* members;
absence of the signal before the first transaction; the empty-workspace response; that
`finance.runway.low` is in-app only (source-derived from `categories.py`, never exercised).

## Operate / roll back

**New deploy-time requirement: none.** No new worker job, no config. Run `alembic upgrade head` to
apply `0040_finance_runway`. Nothing else has to be seeded: workspaces with no settings row read as
`0/0/0` assumptions. Existing workspaces with transactions get the `money.runway_live` signal and
their alert state lazily on their **next transaction write** (there is no backfill); a workspace
already in low runway at deploy time will get its one `finance.runway.low` on the first write after
deploy, not at deploy.

**Rollback:** revert this slice's commits as a unit (`66ce13c..5db85c7`, plus this docs commit) and
downgrade (`poetry run alembic downgrade 0039_finance_transactions`), which drops
`finance_runway_settings` (saved assumptions and alert state are lost). Downgrade the migration only
*after* the app code is rolled back, same ordering as every slice. Any `money.runway_live` rows left
in `health_signals` are harmless (non-scoring) and are wiped by the next Health Score recompute of
that workspace once the code no longer rebuilds them.

## Follow-ups / known non-blocking gaps

- **Concurrent double-fire race on the alert.** `evaluate_runway_alert` reads `alert_is_low`
  without a row lock, so two transactions committed concurrently can both observe "not yet low" and
  each publish `finance.runway.low`. Fix: `select(...).with_for_update()` on the settings row
  (with `populate_existing`) inside `_get_or_create_settings` for the alert path. Low impact — a
  duplicate in-app notification, no data corruption.
- **First mutation creates a settings row without a PUT.** The alert path calls
  `_get_or_create_settings`, so any workspace's first transaction write inserts a
  `finance_runway_settings` row even if the user never touched Runway. Harmless (defaults `0/0/0`),
  but not "row only when the user saved assumptions".
- **+1 `cash_flow_summary` per mutation.** Each transaction `POST`/`PATCH`/`DELETE` now runs the
  cash-flow aggregate for the alert and again for the signal. Fine at current volumes; consolidate
  into one summary computation if it shows up in profiles.
- **`money.runway_live` has no `unique(startup_id, key)` constraint.** The upsert is delete-then-add
  in one transaction; a concurrent pair could leave two rows. It self-heals on the next write or
  recompute.
- **Signal can appear before a completed assessment.** The money dimension can show
  `money.runway_live` with `score: null` (verified live). The FE must not assume a signal implies a
  score.
- **`money.runway_live` value-0 ambiguity** (cash-positive vs out-of-cash) — by design for a
  non-scoring row; documented in the FE guide. Could be resolved later by storing a sentinel or a
  second key.
- **No email category for `finance.runway.low`** — in-app only. Add a category + copy if founders
  should be emailed.
- **Fundraises count as revenue.** Inherited from Slice 1: `monthly_revenue` includes every inflow
  in the window, so a raise inflates every scenario's revenue line (visible in the out-of-cash
  capture: `monthly_revenue` 103 000 000 and `by_month` recovering). Needs the revenue-vs-financing
  split (Slice 1 follow-up).
- **Slice 3 — invoices. Slice 4 — expenses / budgets. Slice 5 — financial model. Slice 6 —
  bank / accounting / Stripe integrations.**
