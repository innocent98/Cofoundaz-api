# Module 12 — Finance Hub, Slice 5 (Financial Model) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An AI-assisted 12-month 3-statement model (P&L, Cash Flow, Balance Sheet): an async job where the LLM sets bounded assumptions from the startup's actuals and a deterministic engine projects the statements (JSON only; XLSX deferred).

**Architecture:** `POST /finance/model/generate` → 202 `generating` row + enqueue `ai.finance.model`; the worker builds actuals context, calls `metered_complete_json` for validated assumptions, runs the pure `model_engine.project_three_statements`, stores the statements, `status=complete` (or `failed` on over-budget). Reuses the codebase's async-AI-job + LLM-budget pattern (`plan.py`, `marketing_ai.py`, `llm_budget.py`).

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Pydantic v2, the platform LLM/budget + job runner, pytest. **No new dependency** (XLSX/openpyxl deferred).

**Spec:** docs/superpowers/specs/2026-09-30-module-12-finance-slice5-design.md

## Global Constraints

- Money integer minor units; projected figures are Python ints (unbounded) stored as JSONB (note JS 2^53 in the FE guide).
- Async AI job: POST flushes the `generating` row, enqueues, commits, returns 202 `{id, status}`. The WORKER handler does `db.flush()` only — the runner commits (never `db.commit()` in the handler).
- `metered_complete_json(db, startup_id, ...)` → `None` on over budget → set `status=failed` + reason, no partial model (mirror `marketing_ai._fail_over_budget`). `over_budget(db, startup_id)` early-return guard (mirror `plan.py`).
- LLM sets ONLY bounded assumptions (growth/ratios/timing + narrative); starting values come from actuals. Every assumption range-validated/clamped after the LLM returns.
- Tenancy: every query `filter_by(startup_id=...)` from membership. RBAC via the Slice-1 `_finance` dep (founder, team_member, accountant).
- **Migration:** `0045_finance_model`, `down_revision="0044_finance_budgets"`. Re-verify develop head before final push; bump if something merged first. Robust single-head test.
- New enum `FinancialModelStatus(generating|complete|failed)` (`Enum(native_enum=False, values_callable=...)`).
- No import cycle; CodeQL test hygiene; migration id ≤32 chars; **no AI attribution**; `import datetime as dt` in schemas; tests run `LLM_PROVIDER=stub` (deterministic).

## Review Focus

- **The balance sheet balances EXACTLY every month** — `cash_i + AR_i == AP_i + paid_in_capital + RE_i` for all i, across varied assumptions (growth, zero, negative, high/low AR/AP). This is THE invariant. (Task 2)
- **Over-budget → failed, no partial model** — LLM over budget → `status=failed`, statements stay null, reason set; the row is never left half-populated. (Task 5)
- **Bad LLM output can't break the model** — out-of-range/malformed assumptions are clamped/rejected; a garbage response → `failed`, not a 500 or an absurd model. (Task 3/5)
- **202 + poll contract** — POST returns 202 `{id, status:"generating"}`; GET reflects generating→complete/failed; statements present only when complete. (Task 4)
- **Starting values from actuals, not the LLM** — revenue_0/costs_0/cash from cash-flow+expenses; the LLM never sets them. (Task 5)

---

### Task 1: Enum + model + migration

**Files:** Create `app/db/models/financial_model.py`, `alembic/versions/0045_finance_model.py`; Modify `app/db/models/enums.py`, `app/db/models/__init__.py`; Test `tests/db/test_financial_model_model.py`, `tests/test_financial_model_migration.py`

**Interfaces — Produces:** `FinancialModel` model; `FinancialModelStatus`.

- [ ] **Step 1: Enum** in `enums.py`: `class FinancialModelStatus(enum.StrEnum): generating="generating"; complete="complete"; failed="failed"`.

- [ ] **Step 2: Model** `app/db/models/financial_model.py` — mirror an existing AI-generation model + finance house style: `startup_id` (FK CASCADE, index), `created_by` (FK users SET NULL, nullable), `status` (Enum(FinancialModelStatus, native_enum=False, values_callable, length=12), default generating), `horizon_months` (Integer, default 12), `currency` (String(3), default NGN), `assumptions`/`pnl`/`cash_flow`/`balance_sheet` (JSONB, nullable), `error` (String(300), nullable), `generated_at` (DateTime(tz), nullable). `UUIDMixin, TimestampMixin, Base`. Register in `__init__.py`.

- [ ] **Step 3: Migration** `0045_finance_model.py` (`revision="0045_finance_model"`, `down_revision="0044_finance_budgets"`) — one create_table matching the model (FKs, JSONB columns), reversible downgrade. Match `0044`'s style so `alembic check` is clean.

- [ ] **Step 4: Model test** — persist a row, assert defaults (`status generating`, `horizon_months 12`, statements None); enum stored as value ("generating"). No mutating call in assert.

- [ ] **Step 5: Migration test** — single-head count + `"0045_finance_model"` in history.

- [ ] **Step 6: Verify + commit** — pytest the two files; `alembic upgrade head && check`. Commit `feat(finance): financial_model model + migration 0045 + FinancialModelStatus`.

---

### Task 2: Deterministic 3-statement engine (the crux)

**Files:** Create `app/services/finance/model_engine.py`, `app/services/finance/model_config.py`; Test `tests/services/test_model_engine.py`

**Interfaces — Produces:** `project_three_statements(starting: dict, assumptions: dict, *, today: date, horizon: int = 12) -> dict`.

- [ ] **Step 1: Write the failing tests** — the invariant across scenarios (this is the most important test in the slice):

```python
from datetime import date
from app.services.finance.model_engine import project_three_statements

def _assume(g=0.0, cogs=0.0, og=0.0, ar=0, ap=0):
    return {"monthly_revenue_growth_pct": g, "cogs_pct_of_revenue": cogs,
            "monthly_opex_growth_pct": og, "ar_days": ar, "ap_days": ap, "narrative": "x"}

def _start(rev=10_000_000, costs=8_000_000, cash=50_000_000):
    return {"revenue_0": rev, "monthly_costs_0": costs, "cash_on_hand": cash, "currency": "NGN"}

import pytest
@pytest.mark.parametrize("a", [
    _assume(), _assume(g=10, cogs=30, og=5, ar=30, ap=45),
    _assume(g=-5, cogs=60, og=0, ar=0, ap=0), _assume(g=100, cogs=90, og=20, ar=90, ap=120),
])
def test_balance_sheet_balances_every_month(a):
    m = project_three_statements(_start(), a, today=date(2026, 4, 1))
    bs = m["balance_sheet_raw"]  # engine exposes the raw per-month numbers for assertion
    for i in range(12):
        assert bs["cash"][i] + bs["accounts_receivable"][i] == \
               bs["accounts_payable"][i] + bs["paid_in_capital"] + bs["retained_earnings"][i]

def test_pnl_identities_and_cash_chain():
    m = project_three_statements(_start(rev=10_000_000, costs=8_000_000, cash=50_000_000),
                                 _assume(g=0, cogs=25, og=0, ar=0, ap=0), today=date(2026, 4, 1))
    p = m["pnl_raw"]
    for i in range(12):
        assert p["gross_profit"][i] == p["revenue"][i] - p["cogs"][i]
        assert p["net_income"][i] == p["gross_profit"][i] - p["opex"][i]
    # ar=ap=0 → cash moves by net income each month
    cf = m["cash_flow_raw"]
    assert cf["closing_cash"][0] == 50_000_000 + p["net_income"][0]

def test_twelve_months_and_no_overflow_on_extreme_growth():
    m = project_three_statements(_start(), _assume(g=100, cogs=50, og=50, ar=60, ap=60), today=date(2026, 4, 1))
    assert len(m["months"]) == 12 and len(m["pnl_raw"]["revenue"]) == 12  # Python ints, no crash
```

- [ ] **Step 2: Run — fails** (ImportError).

- [ ] **Step 3: Implement `model_engine.py`.** Pure integer arithmetic. Starting split: `cogs_0 = round(cogs_pct/100 * revenue_0)`; `opex_0 = max(0, monthly_costs_0 - cogs_0)`. Per month `i` in 1..12:
  - `revenue_i = round(revenue_0 * (1 + g/100)**i)`; `cogs_i = round(cogs_pct/100 * revenue_i)`; `gross_i = revenue_i - cogs_i`; `opex_i = round(opex_0 * (1 + og/100)**i)`; `net_income_i = gross_i - opex_i`; `total_costs_i = cogs_i + opex_i`.
  - `AR_i = round(revenue_i * ar_days/30)`; `AP_i = round(total_costs_i * ap_days/30)`.
  - `collections_i = revenue_i - (AR_i - AR_{i-1})`; `payments_i = total_costs_i - (AP_i - AP_{i-1})`; `net_cash_i = collections_i - payments_i`; `closing_cash_i = closing_cash_{i-1} + net_cash_i`.
  - `RE_i = RE_{i-1} + net_income_i`.
  - Month-0 seeds: `AR_0 = round(revenue_0 * ar_days/30)`, `AP_0 = round((cogs_0+opex_0) * ap_days/30)`, `closing_cash_0 = cash_on_hand`, `RE_0 = 0`, `paid_in_capital = cash_on_hand + AR_0 - AP_0 - RE_0` (a constant).
  - The identity holds by construction with exact integer arithmetic (net_income = revenue − total_costs; collections = revenue − ΔAR; payments = costs − ΔAP). **Add a defensive `assert` of the invariant per month inside the engine** (so a future edit can't silently break it).
  - Build the display statements: `months = [12 "YYYY-MM" from next month]`; `pnl.rows = [{label:"Revenue", values:revenue[1..12]}, COGS, Gross profit, Opex, Net income]`; `cash_flow.rows = [Opening cash, Collections, Payments, Net cash flow, Closing cash]`; `balance_sheet.rows = [Cash, Accounts receivable, Accounts payable, Retained earnings, Paid-in capital]`. Also return the `*_raw` dicts (per-line arrays + paid_in_capital) the tests assert on. Row labels/order in `model_config.py`.

- [ ] **Step 4: Run — pass** (all scenarios balance).

- [ ] **Step 5: Verify + commit** — `poetry run pytest tests/services/test_model_engine.py -v`; ruff+mypy. Commit `feat(finance): deterministic 3-statement projection engine (balance-sheet invariant)`.

---

### Task 3: Assumptions schema + prompt builder + validation

**Files:** Create `app/services/finance/model_prompt.py`; Test `tests/services/test_model_prompt.py`

**Interfaces — Produces:** `ASSUMPTIONS_SCHEMA` (JSON schema dict), `build_model_messages(context) -> list[LLMMessage]`, `validate_assumptions(raw: dict) -> dict` (clamp/normalize).

- [ ] **Step 1: Failing tests** — `validate_assumptions` clamps out-of-range (`monthly_revenue_growth_pct` to [−50,100], `cogs_pct_of_revenue` to [0,100], `monthly_opex_growth_pct` to [−50,100], `ar_days`/`ap_days` to [0,120]); a missing field → a safe default (e.g. 0 growth / 0 cogs); a non-numeric value → default (never raises); `narrative` truncated to 600 chars / defaulted if absent. The STUB LLM output (all numeric fields 0, narrative "[stub-llm] narrative") validates to a usable all-zero assumptions dict.

- [ ] **Step 2: Implement** `ASSUMPTIONS_SCHEMA` (object with the 5 numeric fields + `narrative` string), `build_model_messages(context)` (system + user messages stating the startup's actuals — revenue_0/monthly_costs_0/cash_on_hand/currency — and asking ONLY for forward assumptions; the schema is passed to `complete_json`), and `validate_assumptions(raw)` (coerce each field to float/int, clamp to range, default on missing/bad; truncate narrative). No network — pure.

- [ ] **Step 3: Verify + commit** — pytest; ruff+mypy. Commit `feat(finance): financial-model assumptions schema + prompt + validation`.

---

### Task 4: Service + endpoints (202 generate, get latest, get one)

**Files:** Create `app/schemas/financial_model.py`, `app/services/finance/model_service.py`; Modify `app/api/v1/endpoints/finance.py`; Test `tests/api/test_financial_model.py`

**Interfaces — Consumes:** `FinancialModel`, `job_dispatcher`. **Produces:** `create_model(db, *, startup_id, created_by) -> FinancialModel`, `get_model`, `latest_model`, `serialize_model`.

- [ ] **Step 1: Schemas** `app/schemas/financial_model.py` (`import datetime as dt`) — `ModelStatementRow` (label, values: list[int]), `ModelStatement` (rows: list[ModelStatementRow]), `FinancialModelResponse` (id, status: str, horizon_months, currency, assumptions: dict | None, pnl/cash_flow/balance_sheet: ModelStatement | None, months: list[str] | None, error: str | None, generated_at, created_at).

- [ ] **Step 2: Service** `model_service.py` — `create_model` inserts a `FinancialModel(status=generating, horizon_months=12, currency=<prevailing/NGN>)`, `db.flush()`, then `job_dispatcher.enqueue(db, "ai.finance.model", {"model_id": str(row.id)}, startup_id)`, returns the row; `get_model(db, *, startup_id, model_id)` → row or NotFound; `latest_model(db, *, startup_id)` → newest by created_at or None; `serialize_model(row)`.

- [ ] **Step 3: Endpoints** in `finance.py` — `POST /finance/model/generate` → `create_model` → `db.commit()` → **return 202** (`from fastapi import status`; set the route `status_code=status.HTTP_202_ACCEPTED`) with `{id, status:"generating"}`; `GET /finance/model` → `latest_model` serialized (or `{status:"none"}`/404 if none — pick one and test it); `GET /finance/model/{id}` → `get_model` serialized. `_finance` dep.

- [ ] **Step 4: Tests** (`tests/api/test_financial_model.py`, reuse `_member`/`_headers`) — POST → 202, body `{id, status:"generating"}`, a `FinancialModel` row + an `ai.finance.model` Job row exist; GET latest → generating; GET one; regenerate → a second row, latest returns the newest; RBAC (accountant 202/200, mentor/investor 403); cross-tenant GET → 404. (The job isn't drained here — status stays generating; Task 5 covers the worker.)

- [ ] **Step 5: Verify + commit** — pytest; ruff+mypy; confirm 202 status. Commit `feat(finance): POST /finance/model/generate (202) + GET model endpoints`.

---

### Task 5: Worker handler (context + LLM assumptions + engine → complete/failed)

**Files:** Create `app/worker/handlers/finance_model.py`; Modify `app/worker/__main__.py`; Test `tests/worker/test_finance_model_handler.py`

**Interfaces — Consumes:** `metered_complete_json`, `over_budget`, `cash_flow_summary`/`trailing_monthly_flows` (Slice 1/4a), `project_three_statements`, `build_model_messages`/`validate_assumptions`/`ASSUMPTIONS_SCHEMA`.

- [ ] **Step 1: Failing tests** (`LLM_PROVIDER=stub`) — a `FinancialModel(generating)` + a Job → `handle_finance_model` sets `status=complete`, stores `assumptions` + all 3 statements, `generated_at` set, and the stored `balance_sheet` balances (assert the invariant on the persisted raw values or recompute). Over-budget (monkeypatch `over_budget`→True OR `metered_complete_json`→None) → `status=failed`, statements stay None, `error` set, NO partial model. A model whose status is already complete/failed → no-op. Missing model/startup → no-op, no crash.

- [ ] **Step 2: Handler** `finance_model.py` — mirror `marketing_ai.py`/`plan.py`:
  - `model = db.get(FinancialModel, job.payload["model_id"])`; guard `model is None or model.status != generating` → return.
  - `startup = db.get(Startup, model.startup_id)`; None → return.
  - `if over_budget(db, startup.id): return` (leave generating for retry).
  - Build `starting` from actuals: `summary = cash_flow_summary(db, startup_id=startup.id)`; `rev, costs = trailing_monthly_flows(db, startup_id=startup.id)`; `starting = {"revenue_0": rev, "monthly_costs_0": costs, "cash_on_hand": summary["cash_on_hand"], "currency": summary["currency"]}`.
  - `raw = metered_complete_json(db, startup.id, build_model_messages(starting), schema=ASSUMPTIONS_SCHEMA, max_tokens=...)`; if `raw is None`: set `status=failed`, `error="AI budget exceeded"`, `db.flush()`, return.
  - `assumptions = validate_assumptions(raw)`; `statements = project_three_statements(starting, assumptions, today=datetime.now(UTC).date())`; set `model.assumptions=assumptions`, `model.pnl/cash_flow/balance_sheet` from the statements, `model.months`... (store `months` inside each statement or a column — keep it in the JSON), `model.currency`, `model.status=complete`, `model.generated_at=now`, `db.flush()`. NO commit (runner commits).
  - `register_handler("ai.finance.model", handle_finance_model)`; import in `app/worker/__main__.py`.

- [ ] **Step 3: Verify + commit** — `poetry run pytest tests/worker/test_finance_model_handler.py -v`; ruff+mypy; confirm registration import. Commit `feat(finance): ai.finance.model worker (LLM assumptions + engine → complete/failed)`.

---

### Task 6: e2e + docs

**Files:** Create `e2e/test_financial_model.py`, `e2e/_captures/financial_model/*.json`, `docs/fe-integration-guide-finance-model.md`, `docs/sop/2026-09-30-finance-slice5.md`; Modify `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: e2e journey** (model on `e2e/test_marketing.py`'s AI-job journeys — the closest 202/poll analog; `LLM_PROVIDER=stub` in `scripts/e2e_run.sh`): onboard founder → seed a few transactions/expenses (so actuals aren't all zero) → `POST /finance/model/generate` (assert 202 `{id, generating}`) → drain the worker / poll `GET /finance/model` until `complete` (see how the marketing-AI e2e drains jobs) → assert the 3 statements are present, 12 months, and **the captured balance_sheet balances** → `GET /finance/model/{id}`. Capture bodies.

- [ ] **Step 2: Run** `bash scripts/e2e_run.sh` → green (retry on known Resend-429 flake). Commit only the new `financial_model/*` captures.

- [ ] **Step 3: FE guide** `docs/fe-integration-guide-finance-model.md` — payloads VERBATIM from captures. Cover the 3 endpoints; the **202 + poll** contract (generating→complete/failed); the statement shape (rows × 12 months) + `months`; that the numbers are **AI-assisted but deterministic** (LLM sets assumptions, engine computes); the **assumptions** object + narrative; money-minor-units + JS 2^53 note; that **sensitivity is client-side** and **XLSX export is deferred** (Export button is FE-only for now); the `failed` state (AI budget) the FE must surface; RBAC. Verification table (incl. "balance sheet balances" verified-live).
- [ ] **Step 4: SOP** `docs/sop/2026-09-30-finance-slice5.md` — what shipped + commits, why, how (async AI job; LLM assumptions bounded+validated; deterministic engine with the exact balance invariant + paid-in-capital plug; actuals-seeded), files/migration 0045, verification, follow-ups (XLSX export deferred; 36-month; server-side sensitivity; itemized opex; bank-account actuals via Slice 6). **Checklist** — mark Slice 5 done. **Module 12 now 6/6 slices built** (1,2,3,4a,4b,5; only Slice 6 Integrations remains — note it's the deferred external-connectors slice). Update tally.
- [ ] **Step 5: Commit** `docs(finance): FE guide + SOP + checklist for Module 12 Slice 5 (Financial Model)`.

---

## Self-review notes
- Spec coverage: model+enum (T1), engine+invariant (T2), assumptions/prompt/validation (T3), service+202 endpoints (T4), worker (T5), e2e+docs (T6). XLSX deferred (no task). All non-deferred spec §s mapped.
- Review Focus pinned: BS-balances (T2), over-budget→failed (T5), bad-LLM→clamped/failed (T3/T5), 202+poll (T4), actuals-seed (T5).
- Type consistency: `project_three_statements`/`validate_assumptions`/`ASSUMPTIONS_SCHEMA`/`build_model_messages`/`create_model`/`serialize_model` names align across tasks; `ai.finance.model` job type enqueued (T4) and handled (T5) match.
- Migration/no-attribution/CodeQL-hygiene/stub-LLM carried in Global Constraints. No new dependency.
