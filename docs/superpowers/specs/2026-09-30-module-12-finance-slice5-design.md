# Module 12 — Finance Hub, Slice 5: Financial Model — Design

**Status:** Draft for review
**Date:** 2026-09-30
**Depends on:** Slice 1 (transactions/cash-flow — the actuals the model starts from), Slice 2 (runway assumptions), Slice 4a (expenses — cost actuals). **Build starts once Slice 4b (PR #112) is merged** (branch off develop; migration numbers after 0044).
**Spec author note:** the FE integration guide is re-derived verbatim from live e2e captures at build time.

---

## 1. Goal

Back the Finance → **Financial Model** screen: generate an **AI-assisted, investor-grade 3-statement model** — a 12-month **P&L**, **Cash Flow**, and **Balance Sheet** — built from the startup's real actuals plus AI-generated assumptions, retrievable as **JSON** (the FE renders the tables; XLSX export is deferred — §6). "Investor-grade structure, your numbers."

## 2. FE cross-check (`../cofoundaz/app/(dashboard)/finance/model/page.tsx`)

Empty state → **Generate Model** (async, ~2s spinner in the mock) → three sub-tabs **P&L / Cash Flow / Balance Sheet**, each a line-items × months table; a **revenue-sensitivity slider (−20…+20%)** that recomputes **client-side**; and **Export XLSX**.

| FE element | Backend obligation |
| --- | --- |
| "Generate Model" | `POST /finance/model/generate` → 202 `{id, status:"generating"}` (async AI job) |
| Spinner / poll | `GET /finance/model` returns `status` (generating/complete/failed) + the statements when complete |
| P&L / Cash Flow / Balance Sheet tabs | three statements, each `{ rows: [{label, values:[12 monthly ints]}], months:[...] }` |
| Sensitivity slider | **client-side** (FE multiplies revenue rows) — no server endpoint this slice |
| Export XLSX | `GET /finance/model/{id}/export` → `.xlsx` (openpyxl) |

Money in minor units. Horizon fixed at **12 months** (v1; the FE's "36-month" copy is aspirational — deferred).

## 3. Approach (agreed scope: async AI job — LLM assumptions + deterministic engine)

The numbers are **deterministic accounting**; the AI generates the **assumptions**. This mirrors the codebase's async-AI-job pattern (`app/worker/handlers/plan.py` / `marketing_ai.py`, `app/platform/llm_budget.py`).

1. `POST /finance/model/generate` creates a `FinancialModel` row `status=generating`, enqueues job `ai.finance.model` (flush + enqueue + commit), returns **202** `{id, status:"generating"}`.
2. Worker `handle_finance_model(db, job)`:
   - Guard `status == generating` (else no-op). `if over_budget(db, startup_id): return` (leave generating for retry — same as `plan.py`).
   - Build **context** from actuals (deterministic, no LLM): starting `monthly_revenue`, `monthly_costs` (Slice-4a `expense_category_totals` / cash-flow trailing figures), `cash_on_hand` (Slice-1 `cash_flow_summary`), and the Slice-2 runway assumptions.
   - `assumptions = metered_complete_json(db, startup_id, messages, schema=ASSUMPTIONS_SCHEMA, max_tokens=...)` — the LLM returns a **bounded, validated** assumptions object (see §5). `None` (over budget) → `_fail_over_budget` (status=failed, reason).
   - `statements = project_three_statements(starting, assumptions, horizon=12)` — the deterministic engine (§4).
   - Store `assumptions` + `pnl`/`cash_flow`/`balance_sheet` (JSONB) on the row, `status=complete`, `generated_at`. No commit in the handler (runner commits).
   - Register `ai.finance.model`; import the handler in `app/worker/__main__.py`.
3. `GET /finance/model` → the latest model for the startup (status + statements when complete, else just status/`error`).
4. `GET /finance/model/{id}/export` → stream the stored model as `.xlsx` (§6). 404 if generating/not-found.

Regenerate: `POST /generate` again creates a fresh row (history kept; `GET /finance/model` returns the newest) — mirrors the marketing-AI generation pattern.

## 4. Deterministic 3-statement engine (`app/services/finance/model_engine.py`) — the crux

Pure function `project_three_statements(starting: dict, assumptions: dict, *, horizon=12) -> dict`. All money integer minor units. **The balance sheet MUST balance every month** (Assets = Liabilities + Equity) — this is the key correctness property and a required test.

Per month `i` (1..12):
- **P&L:** `revenue_i = round(revenue_0 * (1+g)^i)` (g = monthly revenue growth); `cogs_i = round(cogs_pct * revenue_i)`; `gross_i = revenue_i − cogs_i`; `opex_i = round(opex_0 * (1+opex_g)^i)`; `net_income_i = gross_i − opex_i`.
- **Cash Flow (indirect, simplified):** `collections_i` and `payments_i` derived from revenue/costs shifted by AR/AP days (`ar_days`, `ap_days` → fraction of the month's revenue/costs deferred to next month); `net_cash_i = collections_i − payments_i`; `closing_cash_i = closing_cash_{i-1} + net_cash_i`, `closing_cash_0 = cash_on_hand`.
- **Balance Sheet:** `cash_i = closing_cash_i`; `accounts_receivable_i = revenue_i − collections_i + AR_{i-1}` (uncollected); `accounts_payable_i = costs_i − payments_i + AP_{i-1}` (unpaid); `retained_earnings_i = retained_earnings_{i-1} + net_income_i`; `paid_in_capital` = the month-0 plug so **Assets_0 = Liabilities_0 + Equity_0** (`cash_0 + AR_0 = AP_0 + paid_in + RE_0`); assert `cash_i + AR_i == AP_i + paid_in_capital + retained_earnings_i` for every i (the balancing identity — enforced + tested).

Output shape: `{ months: ["YYYY-MM", …12], pnl: {rows:[{label, values:[…12]}]}, cash_flow: {…}, balance_sheet: {…}, currency }`. Constants (row labels, rounding) in a small config module.

**Overflow:** monthly figures are aggregates over a growing revenue base — store as **BigInteger**/JSON ints (Python ints are unbounded; JSONB holds them; the response serializes ints — note the JS 2^53 caveat in the FE guide, same as budgets).

## 5. Assumptions (LLM output — bounded + validated)

`ASSUMPTIONS_SCHEMA` (JSON schema for `complete_json`) — a small, closed object, each field range-checked after the LLM returns (reject/clamp out-of-range so a bad LLM response can't break the engine):
- `monthly_revenue_growth_pct` (−50…100), `cogs_pct_of_revenue` (0…100), `monthly_opex_growth_pct` (−50…100), `ar_days` (0…120), `ap_days` (0…120), plus a short `narrative` string (≤ 600 chars) shown as the model's rationale.
- Starting values (`revenue_0`, `cogs_0`/`opex_0`, `cash_on_hand`) come from **actuals**, NOT the LLM (the LLM only sets growth/ratios/timing). Prompt builder in `app/services/finance/model_prompt.py`; the LLM is told the actuals and asked for forward assumptions only.

## 6. XLSX export — DEFERRED (see §11)

**Decision (user):** XLSX export is **deferred**; v1 is **JSON-only** (the FE renders the P&L/CF/BS
tables from the stored JSON; the Export button is FE-only for now). **No `openpyxl` dependency is
added this slice.** There is no `model_export.py` and no `/export` endpoint. When export lands later,
it adds openpyxl + a `build_xlsx(model) -> bytes` + a `GET /finance/model/{id}/export` route.

## 7. Data model

**`financial_models`** — one row per generate (history kept; latest by `created_at`).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | |
| `startup_id` | UUID FK→startups CASCADE, index | |
| `created_by` | UUID FK→users SET NULL, nullable | |
| `status` | Enum(FinancialModelStatus) | `generating`/`complete`/`failed` (non-native, values_callable) |
| `horizon_months` | Integer, default 12 | |
| `currency` | String(3) | |
| `assumptions` | JSONB, nullable | the LLM output (+ narrative) |
| `pnl` / `cash_flow` / `balance_sheet` | JSONB, nullable | the three statements (set on complete) |
| `error` | String(300), nullable | set on failed (e.g. over-budget) |
| `generated_at` | timestamptz, nullable | |
| `created_at`/`updated_at` | timestamptz | |

Migration **`0045_finance_model`**, `down_revision` = develop head after 4b (expected `0044_finance_budgets`). New enum `FinancialModelStatus`. Re-verify head + renumber at build. No transaction/cash-flow coupling (read-only from actuals).

## 8. Endpoints (`/api/v1/finance`, gated by the Slice-1 `_finance` dep: founder, team_member, accountant)

| Method + path | Purpose |
| --- | --- |
| `POST /finance/model/generate` | create generating row + enqueue `ai.finance.model` → **202** `{id, status}` |
| `GET /finance/model` | latest model: status + statements (complete) or status/error |
| `GET /finance/model/{id}` | a specific model (for polling a known id) |

## 9. Files (indicative)

**Create:** `app/db/models/financial_model.py`; `app/schemas/financial_model.py`; `app/services/finance/model_engine.py` (deterministic projection), `model_prompt.py` (LLM messages + ASSUMPTIONS_SCHEMA + validation), `model_service.py` (create/get/latest); `app/worker/handlers/finance_model.py`; `alembic/versions/0045_finance_model.py`; tests (model, migration, engine incl. **balance-sheet-balances**, prompt/assumptions validation, worker handler incl. over-budget→failed, API 202/poll/get, RBAC/tenancy, e2e + captures); `docs/fe-integration-guide-finance-model.md`, `docs/sop/2026-09-30-finance-slice5.md`.
**Modify:** `app/db/models/enums.py` (`FinancialModelStatus`); `app/api/v1/endpoints/finance.py` (3 routes); `app/worker/__main__.py` (register handler); `docs/checklist/PROJECT_CHECKLIST.md`. (No `openpyxl`/`model_export.py` — XLSX deferred, §6/§11.)

## 10. Senior checkpoints (settle in brainstorming)

1. **Balance-sheet correctness** — the 3-statement linkage (cash from CF, AR/AP from timing, retained earnings, paid-in plug) MUST balance every month. This is the engine's crux and a required invariant test. Confirm the simplified linkage in §4 before building.
2. **LLM output safety** — the LLM sets only bounded assumptions (growth/ratios/timing), never the starting actuals or the computed statements; every field range-validated/clamped after return so a hallucinated value can't break or absurd-ify the model. Confirm the schema + clamps.
3. **History vs single model** — v1 keeps a row per generate, `GET /finance/model` returns the latest (mirrors marketing-AI). Confirm (vs overwrite one row).
   *(The openpyxl-dependency checkpoint is resolved: XLSX export is DEFERRED — §6/§11.)*

## 11. Deferred (later, tracked in checklist)

- **XLSX export** (deferred per user, §6) — adds `openpyxl` + `build_xlsx` + `GET /finance/model/{id}/export`; v1 is JSON-only.
- **36-month horizon** (v1 = 12); **server-side sensitivity scenarios** (v1 sensitivity is client-side); **itemized opex / multi-line assumptions**; **connected bank-account actuals** (the FE mentions them — Slice 6 Integrations); **scheduled auto-refresh**.

## 12. Cross-cutting rules (carried)

Async AI job pattern (202 → generating → worker → complete/failed; `metered_complete_json` → over-budget → failed); services flush / endpoints commit; the worker handler does NOT commit (runner does); tenancy `filter_by(startup_id=...)` from membership; RBAC; no import cycle; CodeQL test hygiene; migration id ≤32 chars, single head; **no AI attribution** in commits/PR; `import datetime as dt` in schemas; LLM stubbed in tests (`LLM_PROVIDER=stub`), so engine + handler tests are deterministic.

## 13. Testing

- **Engine (most important):** the balance sheet BALANCES every month for varied assumptions (growth, high/low AR/AP, zero growth, negative growth); P&L identities (gross=rev−cogs, net=gross−opex); cash-flow closing chains from `cash_on_hand`; 12 months; no crash on extreme growth.
- **Assumptions validation:** out-of-range LLM values clamped/rejected; a malformed LLM response → failed (not a 500/garbage model).
- **Worker:** generating→complete stores all 3 statements; over-budget → failed with reason, no partial model; stub LLM → deterministic.
- **API:** POST → 202 `{id, generating}`; GET latest reflects status transitions; GET one; RBAC (accountant 200, mentor/investor 403); cross-tenant 404.
- **e2e:** generate → poll to complete (stub LLM) → GET the 3 statements (assert the balance sheet balances in the captured output). Captures.
- Local CI parity green before push (pytest ≥95%, alembic single-head + check, e2e, static). (No new dependency this slice — XLSX/openpyxl deferred.)
