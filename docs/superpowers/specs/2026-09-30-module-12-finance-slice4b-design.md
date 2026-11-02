# Module 12 — Finance Hub, Slice 4b: Budgets — Design

**Status:** Draft for review
**Date:** 2026-09-30
**Depends on:** Slice 4a (Expenses) — a budget's "spent" actual is the sum of expenses in that category for the month; reuses 4a's per-category summation. **Build starts once Slice 4a (PR #111) is merged to develop.**
**Spec author note:** every payload shape here is a target; the FE integration guide is re-derived verbatim from live e2e captures at build time.

---

## 1. Goal

Back the Finance → **Department Budgets** screen: set a spending **limit** per category per month, and show each against its **actual spend** (summed from Slice-4a expenses) with **variance** and an **over-budget** flag. Plus the two empty-state flows: **draft a budget from last month's actuals** and **copy last month's budget**.

## 2. FE cross-check (`../cofoundaz/app/(dashboard)/finance/budgets/page.tsx`)

The FE labels rows by "department" (Engineering, Marketing…), but per the agreed Slice-4 scope the backend dimension is **category** — the same free-text `category` string used by expenses (Software & IT, Rent…). The FE's department labels are just categories.

| FE element | Backend obligation |
| --- | --- |
| Month selector | budgets are per `period_month` (`YYYY-MM`) |
| Per-card: dept/category, limit, spent, variance, % used, "Over Budget" | `GET /finance/budgets?month=` returns limit + **computed** spent/variance/percent/over_budget |
| "New Budget" | `POST /finance/budgets` (category + month + limit) |
| "Draft budget from actuals" (empty state) | `POST /finance/budgets/draft-from-actuals` (seed from last month's actuals) |
| "Copy last month" | `POST /finance/budgets/copy-last-month` |

`spent`, `variance`, `over_budget`, `percent` are **derived on read** — never stored (they change as expenses change). Money in minor units.

## 3. Data model

**`budgets`** — one row per (startup, category, month).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | |
| `startup_id` | UUID FK→startups CASCADE, index | tenancy |
| `created_by` | UUID FK→users SET NULL, nullable | |
| `category` | String(120) | the shared dimension (matches `expenses.category`) |
| `period_month` | String(7) | `"YYYY-MM"` (stored as a validated string; simple + matches the FE) |
| `limit_minor` | Integer | `0 … 2_147_483_647` at validation |
| `currency` | String(3) | default NGN |
| `notes` | Text, nullable | |
| `created_at`/`updated_at` | timestamptz | |

Unique `(startup_id, category, period_month)` — one budget per category per month. Composite index `(startup_id, period_month)` for the month query. Migration **`0044_finance_budgets`**, `down_revision=0043_finance_expenses`. Re-verify develop head at build time (renumber if something merged first). No new enum, no transaction link — budgets are limits + derived actuals, they do NOT post transactions or touch cash-flow.

## 4. Actuals (agreed scope: sum of expenses by category)

`spent` for a `(category, month)` budget = **Σ `expenses.amount_minor` where `startup_id`, `category`, and `expense_date` in that month** (mirrors Slice-4a `category_summary`'s per-category month sum — reuse or factor that query so the two never diverge). Derived per read:
- `spent_minor` = the category's expense total for the month.
- `variance_minor` = `spent_minor − limit_minor` (positive = over).
- `over_budget` = `spent_minor > limit_minor`.
- `percent_used` = `round(100 * spent_minor / limit_minor, 1)` when `limit_minor > 0`, else `None` (guard divide-by-zero).
- The list endpoint computes all actuals for the month in **one** grouped expense query (not N+1): fetch the month's budgets, fetch the month's per-category expense sums once, join in Python.

**Consequence for the FE guide:** a manual outflow *transaction* not logged as an expense does NOT count toward a budget's spent (budgets track expenses only) — document this explicitly.

## 5. Endpoints (`/api/v1/finance`, gated by the Slice-1 `_finance` dep: founder, team_member, accountant)

| Method + path | Purpose |
| --- | --- |
| `POST /finance/budgets` | create a budget (category, `period_month`, limit) — dup `(category, month)` → 422/409 (unique) |
| `GET /finance/budgets?month=YYYY-MM` | list the month's budgets **with** computed spent/variance/percent/over_budget |
| `GET /finance/budgets/{id}` | one budget (with actuals) |
| `PATCH /finance/budgets/{id}` | edit `limit_minor` (and/or notes) |
| `DELETE /finance/budgets/{id}` | remove |
| `POST /finance/budgets/draft-from-actuals` | body `{period_month}` → for each category with spend in the **previous** month, create a budget for `period_month` with `limit = that category's previous-month actual`; SKIP categories that already have a budget for `period_month` (never overwrite); returns the created list |
| `POST /finance/budgets/copy-last-month` | body `{period_month}` → copy the previous month's budget rows (category+limit+currency) into `period_month`; SKIP categories already budgeted for `period_month`; returns the created list |

Validation: `limit_minor` `0 … int32` → 422; `period_month` matches `^\d{4}-(0[1-9]|1[0-2])$` → else 422; explicit-null on required PATCH fields → 422 (Slice-1 pattern); dup `(startup_id, category, period_month)` insert → 422 (guarded via SAVEPOINT + unique, per memory). Tenancy `filter_by(startup_id=...)` from membership; cross-tenant → 404.

## 6. Files (indicative)

**Create:** `app/db/models/budget.py`; `app/schemas/budget.py`; `app/services/finance/budgets.py` (CRUD, actuals join, draft-from-actuals, copy-last-month, `_prev_month` helper); `alembic/versions/0044_finance_budgets.py`; tests (`tests/db/test_budget_model.py`, `tests/test_budget_migration.py`, `tests/api/test_budgets.py`, `tests/services/test_budget_actuals.py`, `e2e/test_budgets.py` + captures); `docs/fe-integration-guide-finance-budgets.md`, `docs/sop/2026-09-30-finance-slice4b.md`.
**Modify:** `app/db/models/__init__.py` (register `Budget`); `app/api/v1/endpoints/finance.py` (7 routes); `docs/checklist/PROJECT_CHECKLIST.md`. **Reuse** the Slice-4a per-category month-sum query from `app/services/finance/expenses.py` (factor a shared helper if cleaner) so budget actuals and the expenses summary can't diverge.

## 7. Senior checkpoints (mostly decided)

1. **Actuals source = Σ expenses by category** (decided). Reuse the 4a summation; document that manual-only outflow transactions don't count.
2. **`period_month` as a `YYYY-MM` string vs a first-of-month `Date`** — string is simplest and matches the FE; confirm at build (a Date column is also fine but needs normalization to month-start).
3. **Over-budget notification** — the FE shows over-budget as a *derived* badge. An actual `finance.budget.exceeded` event/notification (fired when an expense pushes a category over its limit) is **deferred** to a follow-up (would add an evaluation hook on expense mutation, like the runway alert). v1 derives over_budget on read only.
4. **draft/copy overwrite policy** — SKIP categories already budgeted for the target month (never overwrite an existing limit). Confirm.

## 8. Deferred (later, tracked in checklist)

- **Over-budget event + notification** (`finance.budget.exceeded`) — v1 derives the flag on read.
- **AI** — "draft from actuals" is a deterministic seed (last month's actuals), not AI.
- **Rollover / annual budgets / department≠category modelling** — v1 is per-category monthly.
- **Multi-currency** — one currency per budget, not converted (as Slices 1–4a).

## 9. Cross-cutting rules (carried)

Money int32-bounded at validation (422 not 500); services flush / endpoints commit; tenancy from membership; SAVEPOINT for the unique-budget insert; no import cycle (`budgets.py` imports the Expense model / a shared expense-sum helper — nothing imports it back except endpoints); CodeQL test hygiene; migration id ≤32 chars, single head; **no AI attribution** in commits/PR; `import datetime as dt` in schemas.

## 10. Testing

- **Unit/service:** create/list/get/patch/delete; dup `(category, month)` → 422; `_prev_month` helper (Jan→prev-Dec year rollover); draft-from-actuals seeds from the previous month's per-category expense sums and skips existing; copy-last-month duplicates prior-month limits and skips existing; empty source → creates nothing.
- **Actuals join:** a budget's `spent`/`variance`/`over_budget`/`percent_used` computed correctly from expenses (over, under, exactly-equal, zero-limit → percent None, no divide-by-zero); the list computes all actuals in one grouped query (assert correctness, and that a category with expenses but no budget isn't invented, and a budget with no expenses shows spent 0).
- **Composition:** create a budget, add expenses in that category → the budget's `spent` rises; a manual outflow transaction in that category does NOT change `spent` (documents the expenses-only rule).
- **API:** RBAC (accountant 200, mentor/investor 403); cross-tenant 404; `period_month`/`limit` validation → 422.
- **e2e:** draft-from-actuals (after seeding last month's expenses) → list this month's budgets with spent/variance → PATCH a limit → add an expense that pushes over budget → list shows over_budget → copy-last-month into the next month. Capture bodies.
- Local CI parity green before push (pytest ≥95%, alembic single-head + check, e2e, static).
