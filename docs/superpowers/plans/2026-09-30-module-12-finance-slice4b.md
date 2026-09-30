# Module 12 — Finance Hub, Slice 4b (Budgets) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-category monthly spending limits shown against actuals summed from Slice-4a expenses, with derived variance/over-budget, plus draft-from-actuals and copy-last-month.

**Architecture:** New `budgets` table (one row per startup/category/month, storing only `limit_minor`). A service (`app/services/finance/budgets.py`) computes actuals by reusing a shared per-category expense-sum helper factored out of `expenses.py`, so budget "spent" and the expenses summary can never diverge. No transaction link, no cash-flow/runway coupling.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Pydantic v2, pytest.

**Spec:** docs/superpowers/specs/2026-09-30-module-12-finance-slice4b-design.md

## Global Constraints

- Money in integer minor units; `limit_minor` bounded `0 … 2_147_483_647` at validation (422, never 500).
- Services `db.flush()`; endpoints `db.commit()`. `get_db` does not auto-commit.
- Tenancy: every query `filter_by(startup_id=...)`; `startup_id` from `membership.startup_id`, never body.
- RBAC on every route via the Slice-1 `_finance = require_role(founder, team_member, accountant)` dep.
- **Migration:** `0044_finance_budgets`, `down_revision="0043_finance_expenses"`. Re-verify develop head before final push; bump if something merged first. Robust single-head test.
- `spent`/`variance`/`over_budget`/`percent_used` are DERIVED on read — never stored.
- Unique `(startup_id, category, period_month)`; dup insert → 422 via SAVEPOINT (`begin_nested`), per memory.
- `period_month` is a validated `"YYYY-MM"` string (regex `^\d{4}-(0[1-9]|1[0-2])$`).
- No import cycle: `budgets.py` imports the shared expense-sum helper from `expenses.py`; nothing imports `budgets.py` back except endpoints.
- CodeQL test hygiene (no mutating call in assert; no implicit str-concat in list literal). No AI attribution. `import datetime as dt` in schemas.

## Review Focus

- **Actuals reconcile with expenses** — a budget's `spent` == the Σ of that category's expenses for the month (same figure the expenses summary shows); a manual outflow transaction NOT logged as an expense does NOT change it. Test both. (Task 2)
- **List computes all actuals in one grouped query** (no N+1); a budget with no expenses shows `spent=0`; a category with expenses but no budget is not invented into the list. (Task 2)
- **Zero-limit guard** — `limit_minor==0` → `percent_used=None`, no ZeroDivisionError; `over_budget = spent>0`. (Task 2)
- **draft/copy skip existing, never overwrite** — a category already budgeted for the target month keeps its limit; year-rollover `_prev_month("2026-01")=="2025-12"`. (Task 3)
- **dup (category, month)** → 422, session not poisoned (SAVEPOINT). (Task 2)

---

### Task 1: Model + migration

**Files:** Create `app/db/models/budget.py`, `alembic/versions/0044_finance_budgets.py`; Modify `app/db/models/__init__.py`; Test `tests/db/test_budget_model.py`, `tests/test_budget_migration.py`

**Interfaces — Produces:** `Budget` model.

- [ ] **Step 1: Model** `app/db/models/budget.py` — mirror `expense.py`/`invoice.py` house style:

```python
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class Budget(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "budgets"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    limit_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("startup_id", "category", "period_month", name="uq_budgets_startup_category_month"),
        Index("ix_budgets_startup_period", "startup_id", "period_month"),
    )
```
Register `Budget` in `app/db/models/__init__.py`.

- [ ] **Step 2: Migration** `0044_finance_budgets.py` (`revision="0044_finance_budgets"`, `down_revision="0043_finance_expenses"`) — one `create_table` matching the model (startups CASCADE, users SET NULL FKs; the composite unique + composite index), reversible downgrade. Read `0043_finance_expenses.py` first, match style so `alembic check` shows no drift.

- [ ] **Step 3: Model test** — persist a budget, assert defaults (`currency "NGN"`, `notes None`); a second row with the SAME `(startup_id, category, period_month)` raises IntegrityError (insert the 2nd inside `with db.begin_nested():`); the same category in a DIFFERENT month is allowed. No mutating call in assert.

- [ ] **Step 4: Migration test** — single-head count + `"0044_finance_budgets"` in `alembic history`.

- [ ] **Step 5: Verify + commit** — `poetry run pytest tests/db/test_budget_model.py tests/test_budget_migration.py -v`; `alembic upgrade head && alembic check` (single head, no drift). Commit `feat(finance): budgets model + migration 0044`.

---

### Task 2: Schemas + CRUD with derived actuals

**Files:** Create `app/schemas/budget.py`, `app/services/finance/budgets.py`; Modify `app/services/finance/expenses.py` (factor a shared per-category month-sum helper), `app/api/v1/endpoints/finance.py`; Test `tests/api/test_budgets.py`, `tests/services/test_budget_actuals.py`

**Interfaces — Consumes:** the Slice-4a `Expense` model / per-category month sum. **Produces:** `expense_category_totals` (factored, shared), `create_budget`, `list_budgets`, `get_budget`, `update_budget`, `delete_budget`, `serialize_budget`.

- [ ] **Step 1: Factor the shared actuals helper** in `app/services/finance/expenses.py`:

```python
def expense_category_totals(db, *, startup_id, month: str) -> dict[str, int]:
    """category -> summed amount_minor of that category's expenses in `month` ("YYYY-MM")."""
    start, end = _month_range(month)  # reuse the existing month-range helper from Slice 4a
    rows = (
        db.query(Expense.category, func.coalesce(func.sum(Expense.amount_minor), 0))
        .filter(Expense.startup_id == startup_id, Expense.expense_date >= start, Expense.expense_date < end)
        .group_by(Expense.category)
        .all()
    )
    return {cat: int(total) for cat, total in rows}
```
Refactor `category_summary` (Slice 4a) to build on `expense_category_totals` so the two can't diverge (keep its existing output shape + tests green).

- [ ] **Step 2: Schemas** `app/schemas/budget.py` — `import datetime as dt`; `BudgetCreate` (category 1..120, `period_month` `Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")`, limit_minor `Field(ge=0, le=2_147_483_647)`, currency default NGN 1..3, notes str|None), `BudgetUpdate` (limit_minor/notes optional; `model_validator` rejects explicit null on limit_minor → 422), `BudgetResponse` (id, category, period_month, limit_minor, currency, notes, **spent_minor: int, variance_minor: int, over_budget: bool, percent_used: float|None**, created_at, updated_at).

- [ ] **Step 3: Service** `app/services/finance/budgets.py` — `create_budget` (insert inside `with db.begin_nested():`, on IntegrityError → `_validation("category", "A budget for this category and month already exists.")`); `get_budget` → row or `NotFound`; `update_budget` (limit/notes); `delete_budget`; `list_budgets(db, *, startup_id, month)` → fetch the month's budgets + `expense_category_totals(db, startup_id=..., month=month)` ONCE, join in Python; `serialize_budget(budget, *, spent_minor)` computing `variance_minor = spent_minor - limit_minor`, `over_budget = spent_minor > limit_minor`, `percent_used = round(100*spent_minor/limit_minor, 1) if limit_minor > 0 else None`.

- [ ] **Step 4: Endpoints** in `finance.py` — `POST /budgets`, `GET /budgets?month=YYYY-MM` (list with actuals), `GET /budgets/{id}` (with actuals — compute that one category's month total), `PATCH /budgets/{id}`, `DELETE /budgets/{id}`. `_finance` dep; `startup_id`/`created_by` from membership/user; `month` a required `Query` on the list (pattern-validated). Put any literal sub-paths before `/{id}` if added later.

- [ ] **Step 5: Tests** (`tests/api/test_budgets.py` + `tests/services/test_budget_actuals.py`, reuse Slice-1 `_member`/`_headers`):
  - create; dup `(category, month)` → 422 (and a follow-up read proves the session still works); create same category different month OK.
  - **actuals:** seed expenses in a category, create a budget for that category+month → the budget's `spent_minor` == Σ those expenses; `variance`/`over_budget`/`percent_used` correct (over, under, exactly-equal, zero-limit → percent None). A budget with no expenses → spent 0. A category with expenses but no budget is NOT in the list.
  - **expenses-only rule:** a manual outflow Transaction in that category (created via `POST /finance/transactions`) does NOT change the budget's spent.
  - list computes actuals for all cards (assert 2 budgets each get the right spent from one call).
  - RBAC (accountant 200, mentor/investor 403); cross-tenant get/patch/delete → 404; `period_month`/`limit` validation → 422; explicit-null PATCH → 422.
  - Bind calls to vars before assert; no implicit str-concat in list literals.
  - **Slice-4a regression:** `tests/api/test_expenses.py` (the `category_summary` tests) stay green after the `expense_category_totals` refactor.

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/api/test_budgets.py tests/services/test_budget_actuals.py tests/api/test_expenses.py -v`; ruff+mypy; confirm no import cycle. Commit `feat(finance): budgets CRUD with derived actuals from expenses`.

---

### Task 3: Draft-from-actuals + copy-last-month

**Files:** Modify `app/services/finance/budgets.py`, `app/api/v1/endpoints/finance.py`, `app/schemas/budget.py`; Test `tests/api/test_budgets.py` (extend), `tests/services/test_budget_seed.py`

**Interfaces — Produces:** `_prev_month`, `draft_from_actuals`, `copy_last_month`.

- [ ] **Step 1: Failing tests** — `_prev_month("2026-03")=="2026-02"`, `_prev_month("2026-01")=="2025-12"`; draft-from-actuals for month M seeds a budget per category that had spend in M-1 (limit == that category's M-1 actual) and SKIPS categories already budgeted for M; copy-last-month copies M-1's budget rows (category+limit+currency) into M, skipping existing; both return the created list; empty source (no prior expenses / no prior budgets) → creates nothing, returns `[]`.

- [ ] **Step 2: `_prev_month`** (pure): parse `"YYYY-MM"`, subtract one month with year rollover, return `"YYYY-MM"`.

- [ ] **Step 3: `draft_from_actuals(db, *, startup_id, created_by, period_month)`** — `prev = _prev_month(period_month)`; `totals = expense_category_totals(db, startup_id=..., month=prev)`; existing = the set of categories already budgeted for `period_month`; for each `(cat, total)` in totals with `total > 0` and `cat not in existing`: create a `Budget(category=cat, period_month=period_month, limit_minor=total, currency=<prevailing/NGN>)`. `db.flush()`; return created rows.

- [ ] **Step 4: `copy_last_month(db, *, startup_id, created_by, period_month)`** — `prev = _prev_month(period_month)`; fetch prev budgets; existing = categories already budgeted for `period_month`; for each prev budget whose category not in existing: create a Budget for `period_month` with the same category/limit/currency. `db.flush()`; return created rows.

- [ ] **Step 5: Schemas + endpoints** — a `SeedRequest` schema `{period_month: pattern}`; `POST /finance/budgets/draft-from-actuals` and `POST /finance/budgets/copy-last-month` (both take the body, `_finance`, commit, return the created budgets serialized WITH actuals for `period_month`). Declare these BEFORE `/budgets/{id}` (or they'd be captured as ids — but they're POST vs GET, so no clash; still put them logically grouped).

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/api/test_budgets.py tests/services/test_budget_seed.py -v`; ruff+mypy. Commit `feat(finance): budget draft-from-actuals + copy-last-month`.

---

### Task 4: e2e + docs

**Files:** Create `e2e/test_budgets.py`, `e2e/_captures/budgets/*.json`, `docs/fe-integration-guide-finance-budgets.md`, `docs/sop/2026-09-30-finance-slice4b.md`; Modify `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: e2e journey** (model on `e2e/test_finance.py` / `e2e/test_expenses.py`): onboard founder → seed expenses in ≥2 categories for last month → `POST /finance/budgets/draft-from-actuals {this month}` (assert budgets created seeded from last month's actuals) → add expenses THIS month → `GET /finance/budgets?month=` (assert spent/variance/over_budget reflect this month's expenses) → PATCH a limit (assert over_budget flips) → `POST /finance/budgets/copy-last-month {next month}` (assert copied). Capture every body to `e2e/_captures/budgets/`.

- [ ] **Step 2: Run** `bash scripts/e2e_run.sh` (Docker DB/Redis) → green (retry on known Resend-429 flake). Commit only the NEW `budgets/*` captures.

- [ ] **Step 3: FE guide** `docs/fe-integration-guide-finance-budgets.md` — payloads VERBATIM from captures. Cover the 7 endpoints; that `spent`/`variance`/`over_budget`/`percent_used` are **derived on read** (FE must not PATCH them); the **expenses-only actuals rule** (a manual transaction not logged as an expense doesn't count); the department-label-is-category note; draft/copy skip-existing behavior; money-minor-units; `percent_used` null when limit is 0; the single-currency limitation. Verification table.
- [ ] **Step 4: SOP** `docs/sop/2026-09-30-finance-slice4b.md` (match prior finance SOPs) — what shipped + commits, why, how (derived actuals via the shared `expense_category_totals`, draft/copy seeding, skip-existing), files/migration 0044, verification, follow-ups (over-budget event/notification deferred; multi-currency; department≠category modelling; rollover/annual budgets). **Checklist** — mark Slice 4b done (Module 12 now 5/6: Slices 1,2,3,4a,4b; 5 Financial Model + 6 Integrations open), update tally.
- [ ] **Step 5: Commit** `docs(finance): FE guide + SOP + checklist for Module 12 Slice 4b (Budgets)`.

---

## Self-review notes
- Spec coverage: model+migration (T1), CRUD + derived actuals via shared helper (T2), draft/copy seeding (T3), e2e+docs (T4). All spec §s mapped.
- Review Focus pinned: actuals reconcile + expenses-only (T2), one grouped query / no-invention (T2), zero-limit guard (T2), draft/copy skip-existing + year-rollover (T3), dup→422 (T2).
- Type consistency: `expense_category_totals`/`serialize_budget`/`BudgetResponse`/`_prev_month`/`draft_from_actuals`/`copy_last_month` names align across tasks.
- Migration/no-attribution/CodeQL-hygiene carried in Global Constraints. No cash-flow/runway coupling (budgets are limits + derived actuals).
