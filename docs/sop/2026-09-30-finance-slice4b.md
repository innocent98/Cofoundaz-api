# SOP — Module 12 Finance Hub, Slice 4b: Budgets (per-category monthly limits, actuals derived from expenses, draft-from-actuals + copy-last-month)

**What shipped** — Module 12's fourth build slice, second half (PRD 12.4, Budgets; Expenses were Slice 4a).
**Slice 4b of the 4a/4b/5/6 remaining plan — Module 12 is now 5 of 6 slices done** (1 Cash Flow, 2 Runway,
3 Invoices, 4a Expenses, 4b Budgets; **5 Financial Model** and **6 Integrations** remain open).

1. **`budgets` table** — one row per `(startup, category, period_month)`: `limit_minor` (**BIGINT**),
   `currency`, `notes`, `created_by`. Unique on `(startup_id, category, period_month)`.
2. **7 endpoints under `/finance/budgets`** — create, list (`?month=`, required), get, `PATCH`, `DELETE`,
   `POST draft-from-actuals`, `POST copy-last-month`.
3. **Actuals are derived on every read, never stored.** `spent_minor` = the category's expense total for the
   budget's month, via the shared `expense_category_totals` helper; `variance_minor`, `over_budget` and
   `percent_used` are computed from it. **Expenses only** — a manual outflow transaction does not count.
4. **Two seeders** — `draft-from-actuals` (one budget per last-month expense category, limit = that total)
   and `copy-last-month` (copy last month's budgets). Both **skip categories already budgeted** for the
   target month, never overwrite, and return only what they newly created.

Migration **`0044_finance_budgets`** (one new table), `app/services/finance/budgets.py`,
`app/schemas/budget.py`, `app/db/models/budget.py`, 7 new routes behind the same
`require_role(founder, team_member, accountant)` as Slices 1–4a. No cash-flow / runway coupling: a budget is
a limit plus derived actuals; it posts nothing to the ledger.

Commits (branch `feat/module-12-finance-slice4b`, off `develop` after Slice 4a merged as PR #111; not yet
merged, no PR opened yet), oldest to newest:
`182f512` (design spec) → `3111186` (implementation plan) →
`d9f15f7` (budgets model + migration `0044`) →
`3936944` (budgets CRUD with derived actuals from expenses) →
`53189ef` (draft-from-actuals + copy-last-month) →
`c652835` (widen `limit_minor` to BigInteger + perf) →
`6fb59fc` (live e2e journey + 29 captures) → this docs commit.

## Why

Slice 4a gave founders the Expenses screen's per-category breakdown; the next FE screen is Budgets — a card per
"department" with a monthly limit, how much has been spent against it, and whether it is over. The value is the
**actuals** (spent vs limit), which must never disagree with the Expenses screen, and a fast way to fill a
month (nobody wants to type ten limits by hand every month). The FE handoff has no department entity, so a
"department" is a category.

The core correctness risks are **actuals drift** (a stored spent figure that diverges from the expenses),
**double counting / silent mismatches** (which spend counts, and how a budget matches an expense), **divide-by-
zero** on a zero limit, **overwriting a limit the user set**, and **overflow** (a category's monthly total
used as a limit exceeding int32). Each is a designed-in guard below.

## How

**Actuals derived via the shared `expense_category_totals`** (`app/services/finance/expenses.py`, now the
single source of truth; `category_summary` was refactored onto it). It returns `category → SUM(amount_minor)`
for the month in one `GROUP BY` over expenses. `list_budgets` calls it **once** for the whole month and pairs
each budget with `totals.get(category, 0)`; `spent_for` (single budget) calls the same helper. Because the
Expenses summary and the budgets both build on it, their numbers cannot diverge (unit-tested:
`test_actuals_equal_sum_of_expenses_and_match_summary`). *Alternative rejected:* storing `spent_minor` on the
budget and updating it from the expense create/edit/delete hooks — a second source of truth that drifts on
every missed hook, and Slice 4a's "expense edit/delete" paths would each need a budget write.
*Alternative rejected:* a SQL join per budget (N+1) — one grouped query serves the whole list.

**Expenses-only actuals** — the helper reads `expenses`, not `transactions`. So a manual outflow in the same
category does not count (`test_manual_outflow_does_not_change_spent`; live proof in the e2e: a 10,000,000
manual `Marketing` outflow left Marketing's spent at 2,000,000). This matches the document-once rule of
Slice 4a — a spend is logged as an expense (which itself posts the outflow) — and avoids double counting the
expense and its own ledger outflow.

**Category is the department; matching is exact.** A budget matches an expense when `category` strings are
**equal — case- and whitespace-exact**. No normalisation, no controlled vocabulary (Slice 4a's follow-up asked
this to be decided here; the decision is: exact match, FE owns a shared category picker). *Alternative
rejected:* case-insensitive / trimmed matching in SQL — it would diverge from the Expenses summary, which
groups on the raw string, and reintroduce the two-source-of-truth problem.

**Serialization** (`budgets.py::serialize_budget`) — `variance = spent − limit`, `over_budget = spent >
limit`, `percent_used = round(100 × spent ÷ limit, 1)` **or `None` when `limit == 0`** (zero-limit guard, no
divide-by-zero, `test_variance_over_budget_percent` is parametrised over the boundary cases). The four derived
fields are response-only: `BudgetUpdate` accepts only `limit_minor` and `notes`, so the FE cannot write them.

**Duplicate (category, month) → 422, session survives** (`create_budget`): `db.add()` is **inside**
`db.begin_nested()`, so the `IntegrityError` rolls back only the SAVEPOINT and the caller gets a 422 instead of
a poisoned-session `PendingRollbackError` 500 (`test_duplicate_category_month_is_422_and_session_survives`
follows up with a read; the e2e does too). `BudgetUpdate` also rejects an explicit `null` on `limit_minor` via
a `model_validator` (NOT NULL column would otherwise 500 at flush); only `notes` may be cleared.

**Seeding** (`_seed_missing`, `draft_from_actuals`, `copy_last_month`) —
- `_prev_month("YYYY-MM")` returns the previous calendar month, **rolling the year back across January**
  (`2027-01` → `2026-12`; `test_copy_endpoint_january_rolls_back_to_december`).
- `draft_from_actuals` takes the previous month's `expense_category_totals`, keeps categories with a total
  `> 0`, and seeds `(category, total, "NGN")`.
- `copy_last_month` reads the previous month's `Budget` rows and seeds `(category, limit, currency)` — notes
  are not copied.
- `_seed_missing` reads the target month's existing categories once, **skips** any candidate already present,
  and inserts the rest in category order, each inside its own `begin_nested()`; a concurrent request that
  inserted the same `(category, month)` after our read only rolls back that one savepoint and is skipped
  rather than 500ing. **It never updates an existing row.** The endpoints return **only** the budgets created
  (ids captured before commit — `expire_on_commit` would re-SELECT), each serialized with its actuals for the
  target month.

**BigInteger limit for aggregates** (`c652835`). A single expense/transaction amount is int32 (Slices 1–4a),
but a budget limit is a category's **monthly aggregate** — and `draft-from-actuals` uses that aggregate as the
limit, so the sum of several int32 expenses can exceed int32 and would have overflowed the column → 500.
`limit_minor` is `BigInteger`, the schema accepts `0 … 9,223,372,036,854,775,807`
(`test_limit_above_int32_accepted`, `test_draft_sum_exceeding_int32_does_not_500`; live e2e: a 5,000,000,000
limit round-trips). `spent_minor` / `variance_minor` are unbounded Python ints in the response. This commit
edited the (not-yet-merged, branch-local) migration `0044` in place from `Integer` to `BigInteger`; it was
never on `develop`, so no environment had applied the old version.

**RBAC** unchanged from Slices 1–4a: `founder` / `team_member` / `accountant`; other roles → 403. Every budget
query is scoped to `membership.startup_id`; a foreign id is 404, and the list / seeders only ever see the
caller's workspace.

## What's involved

**Migration `0044_finance_budgets`** (`alembic/versions/0044_finance_budgets.py`, chains off
`0043_finance_expenses`, single alembic head) — one additive `create_table("budgets")`: FK `startup_id →
startups` `CASCADE`, FK `created_by → users` `SET NULL`, `limit_minor BIGINT NOT NULL`, `currency` default
`'NGN'`, unique constraint `uq_budgets_startup_category_month`, index `ix_budgets_startup_id`, composite
`ix_budgets_startup_period (startup_id, period_month)` for the per-month list. No existing table touched;
downgrade drops the indexes and the table. **Round trip verified 2026-09-30** on the isolated e2e DB:
`downgrade -1` → `0043_finance_expenses`, `upgrade head` → `0044_finance_budgets (head)`. Confirmed against
`origin/develop` on 2026-09-30: develop still tops out at `0043_finance_expenses`, so no renumber was needed.

**Models** — `app/db/models/budget.py` (`Budget`, registered in `app/db/models/__init__.py`).

**Schemas** — `app/schemas/budget.py`: `BudgetCreate`, `BudgetUpdate` (explicit-`null`-limit validator),
`SeedRequest`, `BudgetResponse`; `INT64` bound and `MONTH_PATTERN`.

**Services** — `app/services/finance/budgets.py` (`create_budget`, `get_budget`, `update_budget`,
`delete_budget`, `spent_for`, `list_budgets`, `serialize_budget`, `_prev_month`, `_existing_categories`,
`_seed_missing`, `draft_from_actuals`, `copy_last_month`); `app/services/finance/expenses.py`
(`expense_category_totals` extracted; `category_summary` now builds on it — behaviour unchanged, the Slice 4a
suite is the regression net).

**Endpoints** (`app/api/v1/endpoints/finance.py`, mounted at `/finance`):

| Route | Purpose |
|---|---|
| `POST /finance/budgets` | create; 422 on duplicate (category, month) |
| `GET /finance/budgets?month=YYYY-MM` | the month's cards with derived actuals; `month` required; sorted by category |
| `GET /finance/budgets/{id}` | one card |
| `PATCH /finance/budgets/{id}` | edit `limit_minor` / `notes` only |
| `DELETE /finance/budgets/{id}` | hard delete; expenses untouched |
| `POST /finance/budgets/draft-from-actuals` | seed a month from last month's expense totals; returns only created |
| `POST /finance/budgets/copy-last-month` | seed a month from last month's budgets; returns only created |

The two seeder routes are declared **before** `/budgets/{budget_id}` so `draft-from-actuals` /
`copy-last-month` are never parsed as an id.

**Tests** (77 budget test cases across 5 files — 52 functions, several parametrised; full suite **2019 passed**)
- `tests/api/test_budgets.py` (30 functions, parametrised to more cases) — create with zero actuals, duplicate
  422 + session survives, same category different month, actuals = sum of expenses = summary, variance /
  over / percent boundaries, unbudgeted category not listed, per-card actuals, manual outflow does not count,
  PATCH limit/notes, explicit-null limit 422, delete, validation 422s, bad/missing month, RBAC 403 +
  accountant allowed, cross-tenant 404 + list isolation, unauthenticated, draft / copy endpoints
  (target-month cards with actuals, only-created + skip-existing, empty source, January rollover, RBAC,
  bad month, missing body, cross-tenant), limit above int32, draft sum above int32.
- `tests/services/test_budget_actuals.py` (5), `tests/services/test_budget_seed.py` (13) — service-level:
  inclusive month bounds, tenant scoping, summary and budget actuals share one source, update touches only set
  fields, `_prev_month` incl. year rollover, draft (per-category, skips zero-spend, skips existing and keeps the
  limit, empty source, January, tenant scope, idempotent re-run) and copy (category/limit/currency, skips
  existing, empty source, January, tenant scope). *Gap:* unlike Slice 4a's expense tests these do **not** have
  `autoflush=False` variants (prod `SessionLocal` is autoflush-off, the unit `db` fixture is autoflush-on); the
  live e2e, which runs the real server session config, exercises create/seed/patch under autoflush-off and
  passed, so this is a test-hardening follow-up rather than a known bug.
- `tests/db/test_budget_model.py` (3) + `tests/test_budget_migration.py` (1) — defaults, unique
  `(startup, category, month)`, same category in another month allowed, single alembic head + revision in
  history (deliberately **not** "0044 is THE head", so the next slice's migration does not break it).
- `e2e/test_budgets.py::test_finance_budgets_journey` (new) — see Verification.

**Docs**
- `docs/fe-integration-guide-finance-budgets.md` (new) — every body pasted verbatim from the 29 captures via a
  substitution script; derived-fields rule, expenses-only actuals, category-exact matching, seeder
  skip-existing / only-created semantics, 64-bit limit, error shapes, limitations; verification table
  (verified-live vs unit-only vs source-only).
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 12 Slice 4b marked done (Module 12: 5 of 6); 5 / 6 left open.

## Verification

**Unit / static.** Full unit suite **2019 passed** (`EMAIL_BACKEND=console poetry run pytest`; 1942 at Slice 4a).
The budget subset (`tests/api/test_budgets.py`, `tests/db/test_budget_model.py`,
`tests/test_budget_migration.py`) is 56 passed. CI parity through the project's own toolchain:
`poetry run black --check app tests e2e` (535 files unchanged), `isort --check-only app tests` (clean),
`ruff check app tests` (all checks passed), `mypy app` (no issues in 213 source files). *Environment note:*
the unit suite reads the developer `.env`, whose `EMAIL_BACKEND=resend` key is over its monthly quota
(Resend 429), which makes an unrelated auth test fail in a plain `poetry run pytest` (a different one each run
— `test_signup_creates_pending_user`, `test_forgot_is_throttled_60s_per_email`); forcing
`EMAIL_BACKEND=console` (what CI uses) gives the clean 2019-passed run. It is an environment quota issue, not a
code regression.

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 64 passed**, including the new
`test_finance_budgets_journey` (**no Resend-429 flake this run**). The runner applied `0044_finance_budgets`
from zero on an isolated DB. The journey: logs last month's expenses (Infrastructure 6M + 2M, Marketing 5M,
Software 1.5M) → `POST draft-from-actuals` for this month creates three budgets with limits **8,000,000 /
5,000,000 / 1,500,000** (Infrastructure = the two expenses summed), currency `NGN`, spent 0; a re-run creates
nothing; an empty-source month returns `[]` → logs this month's expenses (Infrastructure 9M, Marketing 2M,
Payroll 3M, a lower-case `infrastructure` 1M) **plus a 10M manual `Marketing` outflow** → `GET
/budgets?month=` : Infrastructure **spent 9,000,000 / variance +1,000,000 / over_budget true / 112.5%** (the
lower-case 1M not counted), Marketing **2,000,000 / −3,000,000 / false / 40.0%** (the manual outflow not
counted), Software **0 / −1,500,000 / false / 0.0%**, Payroll (no budget) not listed; `GET` one equals its
list element → create Payroll (limit 5M; 3M already spent → 60.0%) → `PATCH` limit to 2M → **`over_budget`
flips true** (150.0%, variance +1M) → clear notes → create a zero-limit budget (`percent_used: null`) and a
5,000,000,000 limit (accepted) → a new Marketing expense moves that card 2M → 3M (derived live) → create
`Marketing` in next month with limit 999,000, then `POST copy-last-month` for next month returns **five**
budgets (Marketing skipped, its 999,000 preserved; edited Payroll 2M copied; Legal 0; Equipment 5B) and a
re-run returns `[]` → `DELETE` a budget (200 `{deleted: true}` then 404) → 422 (duplicate, create
validation, explicit-null limit, bad / missing list month, bad seed month) / 404 / 401 shapes. All numbers
reconcile by hand. 29 captures under `e2e/_captures/budgets/` are the verbatim source of the FE guide. Single
alembic head after this slice: `0044_finance_budgets`, `down_revision = 0043_finance_expenses`;
`origin/develop` re-checked on 2026-09-30 still tops out at `0043_finance_expenses`.

**Honest gap disclosure (unit-only or source-only, not e2e-captured).** 403 for non-finance roles and
accountant access; cross-workspace 404 / list isolation; January → December year rollover on the seeders
(the live run was in September); a seeder call that creates only some categories (live covered "all" and
"none"); the draft sum above int32 (the live run used a directly-created 5B limit); a missing seeder body 422;
concurrent seeders racing into the unique constraint (source-verified savepoint); mixed-currency actuals; the
draft's `NGN` labelling.

## Operate / roll back

**Deploy-time requirements.** Run `alembic upgrade head` to apply `0044_finance_budgets`. Nothing else: no new
config, no storage, no worker job. Existing workspaces are unaffected (no backfill; budgets start empty).

**Rollback:** revert this slice's commits as a unit (`d9f15f7..6fb59fc`, plus this docs commit) and downgrade
(`poetry run alembic downgrade 0043_finance_expenses`), which drops the `budgets` table (all budgets are
lost; expenses, transactions, cash-flow and runway are untouched — budgets post nothing to the ledger, so
there is **no data hazard** like Slice 4a's expense-sourced transactions). Downgrade the migration only
*after* the app code is rolled back, same ordering as every slice.

## Follow-ups / known non-blocking gaps

- **Over-budget event / notification deferred.** `over_budget` is a derived flag on read only; nothing fires
  (no `finance.budget.over` domain event, in-app notification or email) when a budget is exceeded. Natural fit
  is a Module 20 notification on the transition into over-budget (same fire-once / re-arm shape as the
  `finance.runway.low` alert), triggered from the expense create/edit/delete hooks.
- **Draft currency is `NGN`, not the prevailing currency.** `draft_from_actuals` hard-labels new budgets `NGN`
  regardless of the category's expense currency. Fix alongside reporting-currency work (below).
- **Category matching is case- and whitespace-exact.** No normalisation or controlled vocabulary; a mismatch
  silently shows 0 spent. The FE owns a shared category picker for now. A real fix (normalised category keys or
  a `categories` table shared by expenses and budgets) is a cross-slice change.
- **Mixed currency is not converted.** Inherited single-currency assumption (Slices 1–4a): `spent_minor` sums
  raw minor units across currencies and is compared to `limit_minor` as if one currency. Needs a reporting
  currency + FX source (same follow-up as Slices 1–4a).
- **`spent_for` does a whole-month group-by per single-budget read.** `GET /budgets/{id}`, `POST` and `PATCH`
  compute the entire month's category totals to pick one category. Fine at current volumes; if it matters,
  filter the helper by category for the single-budget paths (the list already does one grouped query for all
  cards).
- **Department ≠ category modelling, rollover and annual budgets deferred.** No department entity, no
  roll-up hierarchy, no unspent rollover, no quarterly / annual budgets, no budget-vs-actual across months.
- **`currency` is not ISO-4217-validated** — only `1..3` chars; **no idempotency key on `POST
  /finance/budgets`** (module-wide gap; the duplicate check makes a double-submit a 422, the seeders are
  naturally idempotent); list is unpaginated.
- **Budget service tests lack `autoflush=False` variants** (see Tests) — add them for `create_budget` / `_seed_missing`.
- **Notes are not carried by `copy-last-month`**; the draft never sets notes.
- **Live e2e did not exercise** RBAC 403, cross-tenant 404, the January rollover, a partial-seed month, or a
  draft whose sum exceeds int32 — all unit- or source-verified; see the honest-gap disclosure above.
- **Slice 5 — financial model. Slice 6 — bank / accounting / Stripe integrations** (which will reuse the
  reserved `source` values alongside `invoice` and `expense`).
