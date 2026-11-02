# SOP — Module 12 Finance Hub, Slice 5: Financial Model (AI-assisted 12-month 3-statement projection)

**What shipped** — Module 12's fifth build slice (PRD 12.5, Financial Model). **This completes the in-scope
build of Module 12: 6 of 6 slices are built** (1 Cash Flow, 2 Runway, 3 Invoices, 4a Expenses, 4b Budgets,
5 Financial Model). The "6th" slice referenced in earlier SOPs, **Integrations** (external bank / accounting /
Stripe connectors, needs OAuth infrastructure), was the deferred external-connectors slice from the module design
and is **not built**; the module's in-scope build is complete pending that deferred connectors work.

1. **`financial_models` table** — one row per generation: `status` (`generating` / `complete` / `failed`),
   `horizon_months`, `currency`, `assumptions` + the three statements as JSON, `error`, `generated_at`.
2. **3 endpoints under `/finance/model`** — `POST /generate` (**202**, `{id, status: "generating"}`),
   `GET /model` (latest; `{status: "none"}` when never generated), `GET /model/{id}`.
3. **`ai.finance.model` worker job** — reads actuals, asks the LLM for bounded assumptions, runs the
   deterministic engine, stores the result (`complete`) or marks the row `failed` (AI budget exhausted).
4. **Deterministic 3-statement engine** (`P&L`, `cash flow`, `balance sheet`) with an **exact** balance-sheet
   invariant.

Migration **`0045_finance_model`** (one new table). Same RBAC as the rest of `/finance`
(`founder` / `team_member` / `accountant`; others 403; cross-tenant 404).

Commits (branch `feat/module-12-finance-slice5`, off `develop` after Slice 4b merged as PR #112; not yet merged,
no PR opened yet), oldest to newest: `a4fbb90` (design spec) → `f008fdc` (spec: defer XLSX) → `11283dd`
(implementation plan) → `2c7e963` (model + migration 0045 + enum) → `e774e33` (engine) → `02b0bb6`
(assumptions schema + prompt + validation) → `4c216a0` (202 + GET endpoints) → `c8f157b` (worker) →
`74cbf8e` (over-budget consistently fails) → e2e + docs commits (this task).

## Why

The Finance UI has a Financial Model screen: three projected statements, an AI-authored assumptions panel and
a revenue-sensitivity slider. Founders need a projection seeded from their **real** numbers, not a blank
spreadsheet, and the projection must be internally consistent (a balance sheet that does not balance destroys
trust in every other number). An LLM is good at picking plausible growth/cost assumptions and bad at
arithmetic, so the design splits the two.

## How

| Decision | Choice | Alternatives rejected |
|---|---|---|
| Execution | **Async AI job** (202 + poll) via the existing job queue + worker, like Marketing's AI generations | Synchronous LLM call in the request (slow, holds a connection, no retry story) |
| What the LLM decides | **Only six assumptions**: monthly revenue growth %, COGS % of revenue, monthly opex growth %, AR days, AP days, plus a `narrative` | Letting the LLM emit the statements (arithmetic errors, unbalanced sheets) |
| Guarding bad LLM output | `validate_assumptions` **never raises**: coerces (non-numeric / NaN / inf → default) and **clamps** into fixed ranges (growth −50…100, COGS 0…100, days 0…120, narrative ≤ 600 chars), so bad output can't break the engine | Trusting the schema alone; failing the job on out-of-range values |
| Numbers | **Deterministic pure-Python integer engine** — no I/O, no LLM. Same inputs → same statements | Floats (rounding drift breaks the invariant) |
| Balance invariant | `Cash + AR == AP + Retained earnings + Paid-in capital` holds **exactly, every month, by construction**: `net_income = revenue − costs`, `collections = revenue − ΔAR`, `payments = costs − ΔAP`, retained earnings accumulate net income, and **paid-in capital** is an opening **plug** (`cash₀ + AR₀ − AP₀ − RE₀`). Also asserted per month in the engine. Verified by a **20k-case fuzz in review** | Reconciling after the fact; fudge rows |
| Starting point | **Seeded from actuals** — trailing 3-month revenue and costs and cash on hand from the Cash Flow ledger (`cash_flow_summary`, `trailing_monthly_flows`) | Asking the user to type starting figures |
| Over budget | `metered_complete_json` returns `None` when the workspace's AI budget is exhausted → the row is marked **`failed`** with `error: "AI budget exceeded"`, **consistently** (fixed in `74cbf8e` so a row can never be stuck `generating`) | Silently falling back to zero assumptions (would present a flat fake model as real) |
| Sensitivity | **Client-side** (the FE recomputes from the returned numbers) | A server sensitivity endpoint (deferred) |
| Export | **XLSX deferred** (no `openpyxl` dependency; v1 is JSON-only) | Adding `openpyxl` + an export route now |

## What's involved

| Path | Role |
|---|---|
| `alembic/versions/0045_finance_model.py` | migration: `financial_models` table |
| `app/db/models/financial_model.py`, `app/db/models/enums.py` (`FinancialModelStatus`) | ORM model + status enum |
| `app/services/finance/model_config.py` | horizon (12) + row-label constants for the three statements |
| `app/services/finance/model_engine.py` | `project_three_statements` — pure deterministic engine |
| `app/services/finance/model_prompt.py` | `ASSUMPTIONS_SCHEMA`, `build_model_messages`, `validate_assumptions` |
| `app/services/finance/model_service.py` | `create_model` (row + enqueue), `get_model`, `latest_model`, `serialize_model` |
| `app/schemas/financial_model.py` | `FinancialModelResponse`, `ModelStatement(Row)` |
| `app/api/v1/endpoints/finance.py` | the 3 routes (`/model/generate` 202, `/model`, `/model/{id}`) |
| `app/worker/handlers/finance_model.py` (+ registered in `app/worker/__main__.py`) | `ai.finance.model` handler |
| `e2e/test_financial_model.py`, `e2e/_captures/financial_model/*.json` | live journey + 5 captures |
| `docs/fe-integration-guide-finance-model.md` | FE contract (bodies pasted from the captures) |

Response shape note: `months` (12 × `"YYYY-MM"`) is **top-level only**; each statement is `{rows: [{label, values}]}`
(the worker stores `months` alongside each statement, but `serialize_model` hoists it to the top level and the
`ModelStatement` schema exposes only `rows`). The e2e caught this against the original wording of the task brief
(which described `months` inside each statement) — the FE guide documents the real shape.

## Verification

- **Unit/API:** 60 tests across the slice (`tests/api/test_financial_model.py`,
  `tests/db/test_financial_model_model.py`, `tests/services/test_model_engine.py`,
  `tests/services/test_model_prompt.py`, `tests/test_financial_model_migration.py`,
  `tests/worker/test_finance_model_handler.py`) — all pass. Full suite: 2077 passed, 2 failed; both failures
  (`test_forgot_is_throttled_60s_per_email`, `test_invites_dedupe_pending`) are pre-existing, unrelated auth/
  onboarding tests that call the **real Resend API** with the local `.env` key, which is over its monthly quota
  (`429 monthly_quota_exceeded`); they pass with `EMAIL_BACKEND=console`.
- **Live e2e:** `bash scripts/e2e_run.sh` → **65 passed** (`test_financial_model_journey`, 5 captures): onboard →
  seed transactions → `GET /model` = `none` → `POST /generate` **202** `generating` → `GET /model` still
  `generating` → in-process worker drain → `GET /model` `complete` (12 months, 3 statements with rows) → **the
  captured balance sheet balances in all 12 months**, cash-flow closing cash equals balance-sheet cash →
  `GET /model/{id}` identical. Stub LLM (all-zero assumptions → flat projection).
- **Not exercised live:** the `failed` path (unit-verified — stub LLM always completes), RBAC 403 / cross-tenant
  404, real-LLM numbers.
- **Migration:** single alembic head `0045_finance_model`; `origin/develop` re-checked and still tops out at
  `0044_finance_budgets`, so no renumber is needed.
- black / ruff clean on the new e2e file.

## Operate / roll back

- Needs the job worker running (`python -m app.worker`) — otherwise a generated model stays `generating`.
- Needs a configured LLM provider and per-workspace budget (`LLM_PROVIDER`, existing LLM budget settings). With
  the budget exhausted, models come back `failed`, by design.
- Roll back: `alembic downgrade -1` drops `financial_models`; the endpoints and handler are additive.
  (Round-trip up/down/up was verified when the migration landed, `2c7e963`.)

## Follow-ups / known non-blocking gaps

- **XLSX export deferred** (`openpyxl`); the FE Export button is FE-only for now.
- **36-month horizon** — v1 is 12 months (`horizon_months` column already exists).
- **Server-side sensitivity** — v1 sensitivity is client-side.
- **Itemised opex** — opex is one line; per-category opex from Expenses/Budgets is not modelled.
- **Bank-account actuals** — actuals come from the ledger only; bank-account balances arrive via the deferred
  Integrations slice.
- **No concurrent-generation dedup** — two POSTs create two rows and two LLM jobs (FE should disable the button
  while `generating`).
- **Currency hardcoded `NGN`** (no multi-currency conversion).
- **Paid-in-capital plug** — a pragmatic startup-model plug, **not full GAAP**.
- **Financing counted as revenue** — the trailing-3-month revenue seed counts *every* inflow (a fresh raise
  included), as Cash Flow's `monthly_revenue` does; the e2e dates its raise outside the window. A revenue vs
  financing split is a cross-slice change (already tracked under Slice 1).
- **Integrations (external connectors)** — the deferred remainder of Module 12.
