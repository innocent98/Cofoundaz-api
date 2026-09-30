# FE Integration Guide — Finance Hub: Budgets (Module 12, Slice 4b)

> **Provenance.** `e2e/test_budgets.py::test_finance_budgets_journey` was run against the live app
> (`bash scripts/e2e_run.sh`, **64 e2e passed**, no Resend-429 flake) and every **response body** below
> is pasted **verbatim** from the 29 captures it wrote to `e2e/_captures/budgets/` (named inline as
> `capture: <file>.json`). The guide was assembled by a script that substitutes each capture file's exact
> contents into the code blocks, so nothing was retyped from the schema, the DTO or memory. Nothing is
> trimmed.
>
> The harness captures **responses only**, so **request** bodies are the exact JSON the journey sends
> (read from `e2e/test_budgets.py`), not captures. Everything **not** exercised live is marked ⚠️ inline;
> the verification table at the end says where each shape came from. Amounts are **minor units** (kobo for
> NGN).
>
> All ids, timestamps and dates are throwaway values from the test run — treat them as **placeholders**,
> not real data. The captured run was on 2026-09-30, so "this month" is `2026-09`, "last month" is
> `2026-08` and "next month" is `2026-10`.

Budgets are the per-category **monthly spending limits** of the Finance Hub. The FE gets (1) a **list of a
month's budget cards** — each carrying its limit **and** what has actually been spent against it,
(2) **create / edit / delete** of a budget, and (3) two **one-click seeders** that pre-fill a month:
**draft from last month's actuals** and **copy last month's budgets**. Actuals are **derived from the
Expenses you already have** (Slice 4a); a budget never stores what was spent.

Base path: `/api/v1/finance`. Every route requires `Authorization: Bearer <token>` and an
`X-Workspace-Id` header (the workspace id from `GET /auth/me` → `active_workspace_id`). Every success
response is the standard envelope `{"data": …, "meta": null}`.

**Access: `founder`, `team_member` or `accountant`** (same as the rest of `/finance`). Any other membership
role gets **403 `FORBIDDEN`** on all seven budget routes. Budgets are workspace-scoped: another
workspace's budget id is **404 `NOT_FOUND`**, never readable, and a month's list only ever contains your
own workspace's budgets. ⚠️ 403, accountant-allowed and cross-tenant 404 are unit-verified
(`test_forbidden_roles_403`, `test_accountant_allowed`, `test_seed_endpoints_forbidden_roles_403`,
`test_seed_endpoints_accountant_allowed`, `test_cross_tenant_get_patch_delete_404_and_list_isolated`,
`test_seed_endpoints_cross_tenant_isolated`), not e2e-captured.

| # | Route | Purpose |
|---|---|---|
| 1 | `POST /finance/budgets` | create one budget (category + month + limit) |
| 2 | `GET /finance/budgets?month=YYYY-MM` | the month's budget cards, with derived actuals (**`month` is required**) |
| 3 | `GET /finance/budgets/{id}` | one budget card |
| 4 | `PATCH /finance/budgets/{id}` | edit the limit and/or notes |
| 5 | `DELETE /finance/budgets/{id}` | delete a budget |
| 6 | `POST /finance/budgets/draft-from-actuals` | seed a month from **last month's expense totals** |
| 7 | `POST /finance/budgets/copy-last-month` | seed a month by **copying last month's budgets** |

---

## Read this first — eight things that will produce a wrong-looking screen

**(1) `spent_minor`, `variance_minor`, `over_budget` and `percent_used` are DERIVED on every read. Never
send or PATCH them.** The row stores only `category`, `period_month`, `limit_minor`, `currency` and
`notes`. Everything else is recomputed from live expenses on **each** request: `variance_minor =
spent_minor − limit_minor`, `over_budget = spent_minor > limit_minor`, `percent_used = round(100 ×
spent ÷ limit, 1)`. `PATCH` accepts only `limit_minor` and `notes`; unknown fields (including the four
derived ones) are **silently ignored, not rejected**. Log an expense and the very next `GET` shows the new
spend — refetch after any expense write. Live proof: Marketing was 2,000,000 spent; after one more
1,000,000 expense the same budget read back 3,000,000 / 60.0 (`budget_get_after_new_expense.json`, §3).

**(2) "Spent" is the sum of EXPENSES in that category and month — nothing else.** A manual **outflow
transaction** (`POST /finance/transactions`, `direction: "out"`) that was **not** logged as an expense does
**not** count toward any budget. In the live run a 10,000,000 manual outflow in category `Marketing` was
posted, and Marketing's `spent_minor` stayed 2,000,000 (`budgets_list_this_month.json`, §2). Send users
who want a spend to count against a budget to **Expenses** (an expense also posts the outflow to the
ledger — Slice 4a's document-once rule), and label the manual-transaction form "does not count toward
budgets". Only expenses dated **inside the budget's month** count (by `expense_date`).

**(3) The FE's "department" cards are backed by `category`, and matching is case- and whitespace-EXACT.**
There is no department entity: a "department" card is simply a budget whose `category` string equals the
`category` string on the Expenses. **Send the SAME string the Expenses screen uses.** `"Infrastructure"` ≠
`"infrastructure"` ≠ `"Infrastructure "` (trailing space) — a mismatch is not an error, it just means
that budget shows **0 spent** and the expense counts toward nothing. Live proof: a 1,000,000 expense in
category `infrastructure` (lower-case) did **not** add to the `Infrastructure` budget (9,000,000, not
10,000,000), and creating a budget named `marketing` next to `Marketing` was **accepted** as a separate
budget (`budget_created_lowercase_category_is_distinct.json`, §1). Give the user one **category picker
shared by the Expenses form and the Budgets form** (seeded from prior categories) and trim on the FE; do
not free-type it in both places.

**(4) One budget per category per month.** A second budget for the same `(category, period_month)` is
**422 `VALIDATION_ERROR`** on field `category` (§ Errors). Show it as "You already have a budget for this
category this month — edit it instead". Expenses in a category **without** a budget are simply not
listed: the list returns budgets, not spend — an unbudgeted `Payroll` expense does not appear until you
create a `Payroll` budget (then it immediately shows the spend already made).

**(5) The two seeders SKIP categories that already have a budget for the target month — they never
overwrite a limit — and return ONLY the budgets they newly created.** `draft-from-actuals` and
`copy-last-month` both respond `{"month": …, "budgets": [<only what was just created>]}`. A second call
returns `"budgets": []`, and a partially-budgeted month returns just the missing ones. **Refetch
`GET /finance/budgets?month=` after seeding to render the full month; do not append the seeder response
to local state as if it were the whole list.** Live proof: copy-last-month into a month that already had a
`Marketing` budget (limit 999,000) returned five budgets, **not** Marketing, and Marketing kept 999,000.

**(6) Money is integer MINOR units; `limit_minor` is a 64-bit value.** `limit_minor: 8000000` is
₦80,000.00 (divide by 100 to display, multiply by 100 to send). Unlike a single expense or transaction
amount (capped at int32, 2,147,483,647), a budget limit is **`0 … 9,223,372,036,854,775,807`** because
budgets are aggregates: a category's monthly total (which the seeder uses as the limit) can exceed the
int32 range. Live: a `5000000000` limit was accepted and round-tripped. In JavaScript, values above
`2^53 − 1` (9,007,199,254,740,991) lose precision as a `Number` — realistic budgets are far below that,
but do not assume int32 anywhere in the FE model for `limit_minor`, `spent_minor` or `variance_minor`.

**(7) `percent_used` is `null` when `limit_minor` is `0`.** A zero-limit budget is allowed; its
`percent_used` is `null` (never `Infinity`/`NaN`), and `over_budget` is `spent_minor > 0`. Render `null`
as "—" and do not draw a progress bar. `percent_used` is **not capped at 100** (a 150.0 means 50% over)
and is rounded to **1 decimal place**. It is a JSON float: `0.0`, `40.0`, `112.5` — present, never
omitted.

**(8) `over_budget` is a derived flag, not an event.** Nothing is emitted or notified when a budget goes
over: no notification, no domain event, no email. The FE learns it only by reading the budget. If you
want a "you're over budget" banner, compute it from a fetch. See Known limitations.

## The budget object

Every budget endpoint (except delete) returns exactly this shape, **12 fields always present**
(nullable ones are present-as-`null`, never omitted):

| Field | Type | Writable? | Notes |
|---|---|---|---|
| `id` | uuid | no | |
| `category` | string | create only | 1–120 chars, free text, **case/whitespace-exact** (trap 3). Not editable after creation — delete and recreate to rename |
| `period_month` | `YYYY-MM` | create only | month `01–12`; the budget's month. Not editable after creation |
| `limit_minor` | int | **yes** | minor units, `0 … 9,223,372,036,854,775,807` (trap 6) |
| `currency` | string | create only | 1–3 chars, default `"NGN"` (not ISO-validated) |
| `notes` | string \| null | **yes** | free text; the only field that can be cleared with `null` |
| `spent_minor` | int | **derived** | sum of the category's expenses in `period_month` (trap 2). `0` when none |
| `variance_minor` | int | **derived** | `spent_minor − limit_minor`; **negative = under budget, positive = over** |
| `over_budget` | bool | **derived** | `spent_minor > limit_minor` |
| `percent_used` | float \| null | **derived** | `round(100 × spent ÷ limit, 1)`; `null` iff `limit_minor == 0`; not capped at 100 |
| `created_at` / `updated_at` | ISO datetime | no | UTC, `Z`-suffixed |

**Field-nesting traps.** (a) The list and both seeders return `data: {"month": …, "budgets": [...]}` — the
array is at **`data.budgets`**, not `data`. Every other budget route (create / get / patch) returns the
budget object **directly at `data`**. (b) `DELETE` returns `data: {"deleted": true}`, not a budget. (c)
`POST` returns **200**, not 201 — including the two seeders. (d) `notes` and `percent_used` are
present-but-`null` when unset / zero-limit. (e) The list's request param is `month`; the create and seeder
**body** field is `period_month` — same `YYYY-MM` value, different name. (f) The list is **sorted by
`category` ascending** (byte order: upper-case sorts before lower-case), not by spend or creation.

---

## 1. `POST /finance/budgets` — create

**Request** (the exact JSON the e2e journey sends for the Payroll budget):

```json
{
  "category": "Payroll",
  "period_month": "<this month, YYYY-MM>",
  "limit_minor": 5000000,
  "notes": "Q4 headcount plan"
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `category` | string | yes | 1–120 chars; must match the Expenses category string exactly (trap 3) |
| `period_month` | string | yes | `YYYY-MM`, month `01–12`; `2026-9` and `2026-13` → 422 |
| `limit_minor` | int | yes | `0 … 9,223,372,036,854,775,807`; negative → 422; `0` allowed |
| `currency` | string | no (default `"NGN"`) | 1–3 chars (not ISO-validated) |
| `notes` | string \| null | no | |

Unknown fields (including `spent_minor` and the other derived fields) are ignored, not rejected.

**Response `200`** — `capture: budget_created.json`. The workspace had already spent 3,000,000 in
`Payroll` this month, so the **new** budget shows it immediately (`spent_minor: 3000000`, 60.0%):

```json
{
  "data": {
    "id": "45f35c4b-55f9-4549-a9ea-934d6a956cc2",
    "category": "Payroll",
    "period_month": "2026-09",
    "limit_minor": 5000000,
    "currency": "NGN",
    "notes": "Q4 headcount plan",
    "spent_minor": 3000000,
    "variance_minor": -2000000,
    "over_budget": false,
    "percent_used": 60.0,
    "created_at": "2026-09-30T13:53:42.246073Z",
    "updated_at": "2026-09-30T13:53:42.246073Z"
  },
  "meta": null
}
```

**Zero limit** — request `{"category": "Legal", "period_month": <this month>, "limit_minor": 0}`,
`capture: budget_created_zero_limit.json`. Note `percent_used: null` and `over_budget: false`:

```json
{
  "data": {
    "id": "48ef7679-6317-4672-ba79-673081b97038",
    "category": "Legal",
    "period_month": "2026-09",
    "limit_minor": 0,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 0,
    "variance_minor": 0,
    "over_budget": false,
    "percent_used": null,
    "created_at": "2026-09-30T13:53:42.281378Z",
    "updated_at": "2026-09-30T13:53:42.281378Z"
  },
  "meta": null
}
```

**Limit above int32** — request `limit_minor: 5000000000` (category `Equipment`), accepted,
`capture: budget_created_limit_above_int32.json`:

```json
{
  "data": {
    "id": "47977ff5-37e2-4975-8b79-93e23fa9f1c9",
    "category": "Equipment",
    "period_month": "2026-09",
    "limit_minor": 5000000000,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 0,
    "variance_minor": -5000000000,
    "over_budget": false,
    "percent_used": 0.0,
    "created_at": "2026-09-30T13:53:42.290253Z",
    "updated_at": "2026-09-30T13:53:42.290253Z"
  },
  "meta": null
}
```

**Lower-case category is a different budget** — request `{"category": "marketing", …, "limit_minor":
1000}` while `Marketing` already exists that month: **200**, a separate card with `spent_minor: 0`
(`capture: budget_created_lowercase_category_is_distinct.json`). This is the trap-3 mismatch, working
"correctly":

```json
{
  "data": {
    "id": "8efff9cf-46a6-4739-8f67-8507123376d0",
    "category": "marketing",
    "period_month": "2026-09",
    "limit_minor": 1000,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 0,
    "variance_minor": -1000,
    "over_budget": false,
    "percent_used": 0.0,
    "created_at": "2026-09-30T13:53:42.399483Z",
    "updated_at": "2026-09-30T13:53:42.399483Z"
  },
  "meta": null
}
```

---

## 2. `GET /finance/budgets?month=YYYY-MM` — the month's cards

**`month` is required** (`YYYY-MM`, month `01–12`). Missing or malformed → **422** (§ Errors). There is no
"current month" default — the FE must send the month it is showing. The list is **unpaginated** and sorted
by `category` ascending. The month's spend is computed with **one grouped query** for the whole list, so
the list is cheap regardless of how many cards there are.

The live run had these budgets for this month, drafted from last month (Infrastructure 8,000,000,
Marketing 5,000,000, Software 1,500,000), then logged **this-month** expenses: Infrastructure 9,000,000
(plus a 1,000,000 lower-case `infrastructure` one), Marketing 2,000,000 (plus a 10,000,000 **manual
outflow**, not an expense), Payroll 3,000,000 (no budget yet), Software none. `capture:
budgets_list_this_month.json`:

```json
{
  "data": {
    "month": "2026-09",
    "budgets": [
      {
        "id": "5b3d4f6e-feeb-4966-9925-04cb166ddf81",
        "category": "Infrastructure",
        "period_month": "2026-09",
        "limit_minor": 8000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 9000000,
        "variance_minor": 1000000,
        "over_budget": true,
        "percent_used": 112.5,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      },
      {
        "id": "78e67809-cbd3-43d2-b3ee-e4b7f61a5111",
        "category": "Marketing",
        "period_month": "2026-09",
        "limit_minor": 5000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 2000000,
        "variance_minor": -3000000,
        "over_budget": false,
        "percent_used": 40.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      },
      {
        "id": "15cd0863-f301-438b-ae90-4d47d293acf6",
        "category": "Software",
        "period_month": "2026-09",
        "limit_minor": 1500000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -1500000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      }
    ]
  },
  "meta": null
}
```

Read it against the rules: **Infrastructure** `spent_minor` is 9,000,000 (the lower-case 1,000,000 is
**not** included — trap 3), `variance_minor` **+1,000,000**, `over_budget: true`, `percent_used: 112.5`.
**Marketing** `spent_minor` is 2,000,000 (the 10,000,000 manual outflow is **not** included — trap 2),
`variance_minor` −3,000,000, `over_budget: false`, `percent_used: 40.0`. **Software** has no spend:
`0` / −1,500,000 / `false` / `0.0`. **Payroll** is absent: it has spend but no budget (trap 4).

**Empty month** — `GET …?month=2020-01`, `capture: budgets_list_empty_month.json`. **`budgets` is `[]`**
(not an error, not `null`) — render an empty state with the two seeder buttons:

```json
{
  "data": {
    "month": "2020-01",
    "budgets": []
  },
  "meta": null
}
```

After the create / patch / delete steps below, this month's list (`capture: budgets_list_after_edits.json`,
six budgets, still sorted by `category`):

```json
{
  "data": {
    "month": "2026-09",
    "budgets": [
      {
        "id": "47977ff5-37e2-4975-8b79-93e23fa9f1c9",
        "category": "Equipment",
        "period_month": "2026-09",
        "limit_minor": 5000000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -5000000000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.290253Z",
        "updated_at": "2026-09-30T13:53:42.290253Z"
      },
      {
        "id": "5b3d4f6e-feeb-4966-9925-04cb166ddf81",
        "category": "Infrastructure",
        "period_month": "2026-09",
        "limit_minor": 8000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 9000000,
        "variance_minor": 1000000,
        "over_budget": true,
        "percent_used": 112.5,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      },
      {
        "id": "48ef7679-6317-4672-ba79-673081b97038",
        "category": "Legal",
        "period_month": "2026-09",
        "limit_minor": 0,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": 0,
        "over_budget": false,
        "percent_used": null,
        "created_at": "2026-09-30T13:53:42.281378Z",
        "updated_at": "2026-09-30T13:53:42.281378Z"
      },
      {
        "id": "78e67809-cbd3-43d2-b3ee-e4b7f61a5111",
        "category": "Marketing",
        "period_month": "2026-09",
        "limit_minor": 5000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 2000000,
        "variance_minor": -3000000,
        "over_budget": false,
        "percent_used": 40.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      },
      {
        "id": "45f35c4b-55f9-4549-a9ea-934d6a956cc2",
        "category": "Payroll",
        "period_month": "2026-09",
        "limit_minor": 2000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 3000000,
        "variance_minor": 1000000,
        "over_budget": true,
        "percent_used": 150.0,
        "created_at": "2026-09-30T13:53:42.246073Z",
        "updated_at": "2026-09-30T13:53:42.272182Z"
      },
      {
        "id": "15cd0863-f301-438b-ae90-4d47d293acf6",
        "category": "Software",
        "period_month": "2026-09",
        "limit_minor": 1500000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -1500000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      }
    ]
  },
  "meta": null
}
```

---

## 3. `GET /finance/budgets/{id}` — one card

`capture: budget_get_one.json` (the Infrastructure card; byte-identical to its element in the list — the
e2e asserts it):

```json
{
  "data": {
    "id": "5b3d4f6e-feeb-4966-9925-04cb166ddf81",
    "category": "Infrastructure",
    "period_month": "2026-09",
    "limit_minor": 8000000,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 9000000,
    "variance_minor": 1000000,
    "over_budget": true,
    "percent_used": 112.5,
    "created_at": "2026-09-30T13:53:42.094190Z",
    "updated_at": "2026-09-30T13:53:42.094190Z"
  },
  "meta": null
}
```

**It is derived on every read.** After logging one more 1,000,000 `Marketing` expense the same card reads
(`capture: budget_get_after_new_expense.json`) — `spent_minor` 2,000,000 → **3,000,000**,
`variance_minor` −3,000,000 → **−2,000,000**, `percent_used` 40.0 → **60.0**:

```json
{
  "data": {
    "id": "78e67809-cbd3-43d2-b3ee-e4b7f61a5111",
    "category": "Marketing",
    "period_month": "2026-09",
    "limit_minor": 5000000,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 3000000,
    "variance_minor": -2000000,
    "over_budget": false,
    "percent_used": 60.0,
    "created_at": "2026-09-30T13:53:42.094190Z",
    "updated_at": "2026-09-30T13:53:42.094190Z"
  },
  "meta": null
}
```

Unknown id, a deleted budget, or another workspace's budget → **404** (§ Errors).

---

## 4. `PATCH /finance/budgets/{id}` — edit

Partial update: send only what you change. **Only `limit_minor` and `notes` are writable** (`category`,
`period_month` and `currency` are not — delete and recreate to change them). **Explicit `null` on
`limit_minor` is rejected** (omit it instead); `notes: null` clears the note.

**Request** (drop the Payroll limit below what has been spent, and change the note):

```json
{
  "limit_minor": 2000000,
  "notes": "Hiring freeze"
}
```

**Response `200`** — `capture: budget_patched_over_budget.json`. **`over_budget` flips to `true`**
(3,000,000 spent vs a 2,000,000 limit; `variance_minor` +1,000,000; `percent_used` 150.0); `spent_minor`
is unchanged because the limit is not what drives it; `updated_at` moves:

```json
{
  "data": {
    "id": "45f35c4b-55f9-4549-a9ea-934d6a956cc2",
    "category": "Payroll",
    "period_month": "2026-09",
    "limit_minor": 2000000,
    "currency": "NGN",
    "notes": "Hiring freeze",
    "spent_minor": 3000000,
    "variance_minor": 1000000,
    "over_budget": true,
    "percent_used": 150.0,
    "created_at": "2026-09-30T13:53:42.246073Z",
    "updated_at": "2026-09-30T13:53:42.256233Z"
  },
  "meta": null
}
```

Confirming with a fresh `GET /finance/budgets/{id}` — `capture: budget_get_after_patch.json` (same
derived values):

```json
{
  "data": {
    "id": "45f35c4b-55f9-4549-a9ea-934d6a956cc2",
    "category": "Payroll",
    "period_month": "2026-09",
    "limit_minor": 2000000,
    "currency": "NGN",
    "notes": "Hiring freeze",
    "spent_minor": 3000000,
    "variance_minor": 1000000,
    "over_budget": true,
    "percent_used": 150.0,
    "created_at": "2026-09-30T13:53:42.246073Z",
    "updated_at": "2026-09-30T13:53:42.256233Z"
  },
  "meta": null
}
```

**Clear the note** — request `{"notes": null}` — `capture: budget_patched_notes_cleared.json`. The limit
is untouched (2,000,000) and `notes` is `null`:

```json
{
  "data": {
    "id": "45f35c4b-55f9-4549-a9ea-934d6a956cc2",
    "category": "Payroll",
    "period_month": "2026-09",
    "limit_minor": 2000000,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 3000000,
    "variance_minor": 1000000,
    "over_budget": true,
    "percent_used": 150.0,
    "created_at": "2026-09-30T13:53:42.246073Z",
    "updated_at": "2026-09-30T13:53:42.272182Z"
  },
  "meta": null
}
```

---

## 5. `DELETE /finance/budgets/{id}` — delete

**Response `200`** — `capture: budget_deleted.json`:

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

Only the budget is removed; **the expenses in that category are untouched** and cash-flow / runway are
not affected (a budget never posts to the ledger). Afterwards `GET /finance/budgets/{id}` is **404**
(`capture: error_get_deleted_budget.json`, same body as the 404 in Errors) and the month's list no longer
contains it. **Deletes are hard and immediate; there is no undo** — confirm in the UI.

---

## 6. `POST /finance/budgets/draft-from-actuals` — seed from last month's spending

Body: `{"period_month": "YYYY-MM"}` (**required**; the month to fill). For each category that had expenses
in the **previous calendar month** (January → the December before it) with a total **greater than 0**, it
creates a budget in `period_month` whose **`limit_minor` equals that category's previous-month expense
total** (all of the category's expenses summed). Created budgets have **`currency: "NGN"`**, no notes, and
are returned sorted by category with their derived actuals for the target month.

The journey logged last month: Infrastructure 6,000,000 + 2,000,000, Marketing 5,000,000, Software
1,500,000, then drafted this month. **Request:**

```json
{
  "period_month": "<this month, YYYY-MM>"
}
```

**Response `200`** — `capture: draft_from_actuals.json`. Infrastructure's limit is **8,000,000** — the
**sum** of its two expenses. Nothing has been spent this month yet, so every card is `0` /
`-limit` / `false` / `0.0`:

```json
{
  "data": {
    "month": "2026-09",
    "budgets": [
      {
        "id": "5b3d4f6e-feeb-4966-9925-04cb166ddf81",
        "category": "Infrastructure",
        "period_month": "2026-09",
        "limit_minor": 8000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -8000000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      },
      {
        "id": "78e67809-cbd3-43d2-b3ee-e4b7f61a5111",
        "category": "Marketing",
        "period_month": "2026-09",
        "limit_minor": 5000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -5000000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      },
      {
        "id": "15cd0863-f301-438b-ae90-4d47d293acf6",
        "category": "Software",
        "period_month": "2026-09",
        "limit_minor": 1500000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -1500000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.094190Z",
        "updated_at": "2026-09-30T13:53:42.094190Z"
      }
    ]
  },
  "meta": null
}
```

**Skips categories that already have a budget** — running the same call again creates nothing and
overwrites nothing. `capture: draft_from_actuals_again_skips_existing.json`:

```json
{
  "data": {
    "month": "2026-09",
    "budgets": []
  },
  "meta": null
}
```

**No source data** — a target month whose previous month has no expenses (`period_month: "2020-02"`)
returns an empty list, **200**, not an error (`capture: draft_from_actuals_empty_source.json`):

```json
{
  "data": {
    "month": "2020-02",
    "budgets": []
  },
  "meta": null
}
```

A malformed `period_month` → **422** (`error_seed_bad_month.json`, § Errors); a missing body → **422**
(⚠️ unit-only, `test_seed_endpoints_missing_body_422`).

**Limits of the draft (be honest in the UI).** It copies **last month's spend**, not a forecast — a
one-off large purchase becomes next month's limit, so let the user edit the cards. It labels every new
budget `NGN` regardless of the currency of the underlying expenses (see Known limitations). A category
that was only spent on **the target month itself** (no previous-month spend) is not drafted.

---

## 7. `POST /finance/budgets/copy-last-month` — seed by copying last month's budgets

Body: `{"period_month": "YYYY-MM"}` (**required**). Copies every budget from the **previous calendar
month** (January → the December before it) into `period_month`, carrying `category`, `limit_minor` and
`currency` **as they stand today** (so limits edited after a draft are copied as edited; **notes are not
copied**). Categories that already have a budget in the target month are **skipped and left as they are**.
Returns only the newly created budgets, sorted by category, with their derived actuals for the target
month.

The journey first created `Marketing` in **next month** with a `999000` limit (`capture:
budget_created_next_month_existing.json` — same shape as `budget_created.json`, not repeated), then
copied this month into it. **Request:**

```json
{
  "period_month": "<next month, YYYY-MM>"
}
```

**Response `200`** — `capture: copy_last_month.json`. **Five** budgets: this month had six, but
**`Marketing` is absent** because next month already had one. `Equipment` carries its 5,000,000,000 limit,
`Legal` its `0` (`percent_used: null`), and `Payroll` the **edited** 2,000,000, not the 5,000,000 it was
created with:

```json
{
  "data": {
    "month": "2026-10",
    "budgets": [
      {
        "id": "8c968072-edaa-4f6c-a2f1-0acbe46761f2",
        "category": "Equipment",
        "period_month": "2026-10",
        "limit_minor": 5000000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -5000000000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.336756Z",
        "updated_at": "2026-09-30T13:53:42.336756Z"
      },
      {
        "id": "e8466b65-1cf6-4ccf-9631-760a7878dcd4",
        "category": "Infrastructure",
        "period_month": "2026-10",
        "limit_minor": 8000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -8000000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.336756Z",
        "updated_at": "2026-09-30T13:53:42.336756Z"
      },
      {
        "id": "30016d5b-c0bc-4b4d-8328-3305de82055b",
        "category": "Legal",
        "period_month": "2026-10",
        "limit_minor": 0,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": 0,
        "over_budget": false,
        "percent_used": null,
        "created_at": "2026-09-30T13:53:42.336756Z",
        "updated_at": "2026-09-30T13:53:42.336756Z"
      },
      {
        "id": "3ae78eb6-0406-4e0c-a639-094323489895",
        "category": "Payroll",
        "period_month": "2026-10",
        "limit_minor": 2000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -2000000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.336756Z",
        "updated_at": "2026-09-30T13:53:42.336756Z"
      },
      {
        "id": "fd4a7ddc-9289-4cae-9b12-99795497385c",
        "category": "Software",
        "period_month": "2026-10",
        "limit_minor": 1500000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 0,
        "variance_minor": -1500000,
        "over_budget": false,
        "percent_used": 0.0,
        "created_at": "2026-09-30T13:53:42.336756Z",
        "updated_at": "2026-09-30T13:53:42.336756Z"
      }
    ]
  },
  "meta": null
}
```

The pre-existing next-month `Marketing` budget **kept its own 999,000 limit** (`capture:
budgets_list_next_month.json`, six cards — `GET /finance/budgets?month=<next month>`; same shape as the
list above, not repeated).

**Running it again is a no-op** — `capture: copy_last_month_again_skips_existing.json`:

```json
{
  "data": {
    "month": "2026-10",
    "budgets": []
  },
  "meta": null
}
```

A previous month with no budgets also returns `"budgets": []` (⚠️ unit-only,
`test_copy_endpoint_skips_existing_and_empty_source`); January → December rollover is unit-verified
(`test_copy_endpoint_january_rolls_back_to_december`) but was not exercised live (the live run happened in
September).

---

## Errors

All errors use the standard envelope (no `data`/`meta`). Bodies below are from live captures unless
marked ⚠️.

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | missing/invalid access token | `error_unauthenticated.json` |
| 403 | `EMAIL_NOT_VERIFIED` | email not verified | ⚠️ not captured |
| 403 | `FORBIDDEN` | membership role is not founder / team_member / accountant | ⚠️ not captured (unit) |
| 404 | `NOT_FOUND` | unknown id, deleted budget, or another workspace's budget | `error_unknown_budget.json`, `error_get_deleted_budget.json` |
| 422 | `VALIDATION_ERROR` | request-shape errors, duplicate category + month, explicit null limit | `error_duplicate_category_month.json`, `error_create_validation.json`, `error_patch_null_limit.json`, `error_list_bad_month.json`, `error_list_missing_month.json`, `error_seed_bad_month.json` |

**401** — `capture: error_unauthenticated.json`:

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

**404** — `capture: error_unknown_budget.json` (`NOT_FOUND` uses `"Not found."` with an **empty**
`field_errors` array; identical for a deleted budget):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**422 (duplicate category + month)** — `POST /finance/budgets` for `Marketing` in a month that already has
one — `capture: error_duplicate_category_month.json`. The `field` is `"category"`. The session is not
poisoned by the duplicate: the e2e reads a budget right after it and gets 200:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A budget for this category and month already exists.",
    "field_errors": [
      {
        "field": "category",
        "message": "A budget for this category and month already exists."
      }
    ]
  }
}
```

**422 (create validation)** — the journey posts `category: ""`, `period_month: "2026-13"`, `limit_minor:
-5`; one `field_errors` entry per bad field — `capture: error_create_validation.json`. Use `field` to
attach the message to an input; **do not string-match `message`**:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "category",
        "message": "String should have at least 1 character"
      },
      {
        "field": "period_month",
        "message": "String should match pattern '^\\d{4}-(0[1-9]|1[0-2])$'"
      },
      {
        "field": "limit_minor",
        "message": "Input should be greater than or equal to 0"
      }
    ]
  }
}
```

**422 (explicit null on the limit)** — `PATCH {"limit_minor": null}` — `capture:
error_patch_null_limit.json`. **Trap: `field` is the empty string `""`, not `"limit_minor"`** — the name is
only inside `message`. Omit `limit_minor` instead of sending `null` (`notes: null` is the one allowed
null):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, limit_minor may not be null"
      }
    ]
  }
}
```

**422 (bad month on the list)** — `?month=2026-13` — `capture: error_list_bad_month.json`. **Trap:
`field` is `"query.month"`** (the location is prefixed for query params):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.month",
        "message": "String should match pattern '^\\d{4}-(0[1-9]|1[0-2])$'"
      }
    ]
  }
}
```

**422 (month missing on the list)** — `GET /finance/budgets` with no `month` — `capture:
error_list_missing_month.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.month",
        "message": "Field required"
      }
    ]
  }
}
```

**422 (bad month on a seeder)** — `{"period_month": "2026-9"}` — `capture: error_seed_bad_month.json`. Here
the field is the **body** field name `"period_month"` (no `query.` / `body.` prefix), unlike the list:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "period_month",
        "message": "String should match pattern '^\\d{4}-(0[1-9]|1[0-2])$'"
      }
    ]
  }
}
```

Also 422 (⚠️ unit-verified, bodies not captured): `limit_minor` above 9,223,372,036,854,775,807 or an
empty/over-length `category` on create (`test_create_validation_422`), a missing seeder body
(`test_seed_endpoints_missing_body_422`). A duplicate the seeders race into (two clients seeding the same
month at once) is skipped rather than failing (⚠️ source-verified — each insert is in a savepoint; not
exercised live).

---

## UX consequences the FE must surface

- **Refetch the month's budgets after every expense create / edit / delete** (and after a
  manual-transaction edit **if** the user might expect it to count — it will not). Spent is derived; a
  cached list goes stale the moment an expense changes.
- **Say what "spent" means.** Label it "Spent (from Expenses)" and link to the Expenses screen. A user
  who logged a payment as a manual transaction will otherwise report the budget "isn't counting" it
  (trap 2).
- **One category picker, shared with Expenses.** Case- and whitespace-exact matching (trap 3); trim on the
  FE. A budget with zero spend despite obvious activity is almost always a category-string mismatch —
  consider a hint when a card shows 0 and the workspace has expenses in a near-match category.
- **Over-budget styling.** Drive it from `over_budget` (red) and show `variance_minor` as "₦X over"
  (positive) / "₦X left" (negative). Do not compute it yourself from `percent_used` (rounded, and `null`
  at zero limit).
- **Progress bars may exceed 100%.** Clamp the drawn bar to 100% but show the real number (`150.0%`).
- **Zero limit → no bar.** `percent_used: null` → render "—".
- **After a seeder, refetch the list** (trap 5). Say what happened: "Added 3 budgets from last month's
  spending" / "Nothing to add — every category already has a budget". An empty `budgets` from a seeder is
  a **valid** outcome, not an error.
- **Draft-from-actuals is a starting point.** Show it as an editable suggestion: it copies last month's
  spend (one-off purchases included).
- **Seeders never overwrite.** If the user wants to reset a month, they delete the cards first; do not
  promise "replace".
- **Edit limits inline via PATCH; rename = delete + create.** `category` and `period_month` are not
  editable.
- **A single currency per workspace.** Do not let users mix currencies (see limitations).
- **Disable submit while a create/seed request is in flight.** There is no idempotency key; a retry after
  a timeout is safe for the seeders and the duplicate-check protects create (422), but there is no reason
  to hammer them.
- **Deletes are hard** — confirm.

## Known limitations (be honest with users)

| Limitation | Effect |
|---|---|
| **Actuals = expenses only.** | A manual outflow transaction never counts toward a budget (trap 2). Spend must be logged as an expense |
| **Category matching is case- and whitespace-exact.** | `"Infrastructure"` ≠ `"infrastructure"` ≠ `"Infrastructure "`; a mismatch silently shows 0 spent (trap 3). No normalisation or controlled vocabulary yet |
| **Mixed-currency actuals are summed WITHOUT conversion.** Single-currency assumption, inherited from Slices 1–4a. | `spent_minor` adds raw minor units across currencies as if they were one; `limit_minor` is compared to that raw sum. Do not let users mix currencies until conversion ships |
| **`draft-from-actuals` labels every new budget `NGN`,** regardless of the currency of the category's expenses. | A workspace budgeting in another currency must edit/recreate the cards. (`copy-last-month` **does** preserve each source budget's `currency`) |
| **The over-budget flag is derived; there is NO over-budget notification or event this slice.** | Nothing fires when a budget is exceeded — no in-app notification, email or domain event. The FE only learns it on read |
| **No department entity.** | A "department" card is a `category` string (trap 3); there is no separate department model, roll-up or hierarchy |
| **Monthly, non-rolling.** | Each budget is one category in one calendar month. No rollover of unspent budget, no annual / quarterly budgets, no per-category totals across months |
| **Actuals are computed per read.** | A single-budget `GET` computes the whole month's category totals to pick one (a per-read group-by over that month's expenses). Fine at current volumes; the list computes it once for all cards |
| **`currency` is not ISO-validated** — only 1–3 characters. | `"ZZ"` is accepted; use a currency picker |
| **The list is unpaginated** (sorted by `category`). | Fine at current volumes |
| **No idempotency key on `POST /finance/budgets`** (module-wide gap; the seeders are naturally idempotent — skip-existing). | A double-submitted create hits the duplicate check and returns 422 on the second call; the first one succeeded |

---

## Verification

Legend: ✅ **verified live** = asserted in `e2e/test_budgets.py` against the running app and the body is in
`e2e/_captures/budgets/`. ⚠️ **unit-only** = asserted by the named unit test; not exercised live.

| Claim | Status | Source |
|---|---|---|
| `POST /budgets` → 200, 12-field body; `currency` defaults to `"NGN"`; new budget shows already-logged spend | ✅ verified live | `budget_created.json` |
| Zero limit accepted → `percent_used: null`, `over_budget: false` | ✅ verified live | `budget_created_zero_limit.json` |
| `limit_minor` above int32 (5,000,000,000) accepted and returned | ✅ verified live | `budget_created_limit_above_int32.json`; `test_limit_above_int32_accepted` |
| Lower-case category is a separate budget (exact matching) | ✅ verified live | `budget_created_lowercase_category_is_distinct.json` |
| Duplicate (category, month) → 422 `VALIDATION_ERROR` on field `category`; session survives | ✅ verified live | `error_duplicate_category_month.json`; `test_duplicate_category_month_is_422_and_session_survives` |
| Same category in a different month is allowed | ⚠️ unit-only (also implicit: `Marketing` created in the next month live) | `test_same_category_different_month_ok`; `budget_created_next_month_existing.json` |
| `GET /budgets?month=` → `data.budgets` sorted by category, with derived spent / variance / over_budget / percent_used | ✅ verified live | `budgets_list_this_month.json` |
| Spent = this month's expenses in the exact category; over-budget flag, variance, `112.5` / `40.0` / `0.0` | ✅ verified live | `budgets_list_this_month.json` |
| A manual outflow transaction does NOT count toward spent | ✅ verified live | `budgets_list_this_month.json` (10M manual outflow, Marketing spent stayed 2M); `test_manual_outflow_does_not_change_spent` |
| A lower-case category expense does NOT count toward the capitalised budget | ✅ verified live | `budgets_list_this_month.json` (Infrastructure 9M, not 10M) |
| Actuals reconcile with the expenses category summary | ⚠️ unit-only | `test_actuals_equal_sum_of_expenses_and_match_summary` |
| Category with expenses but no budget is not listed | ✅ verified live | `budgets_list_this_month.json` (Payroll absent) |
| Spent is derived live: a new expense changes the next read | ✅ verified live | `budget_get_after_new_expense.json` |
| Variance / over_budget / percent parametrised (under, exact, over, zero) | ⚠️ unit-only | `test_variance_over_budget_percent` |
| `month` missing → 422 (`field: "query.month"`, `Field required`); malformed → 422 | ✅ verified live | `error_list_missing_month.json`, `error_list_bad_month.json` |
| Empty month → 200 `{"month": …, "budgets": []}` | ✅ verified live | `budgets_list_empty_month.json` |
| `GET /budgets/{id}` equals its list element | ✅ verified live | `budget_get_one.json` |
| `PATCH` limit + notes → 200; `over_budget` flips; `spent_minor` unchanged; `updated_at` moves | ✅ verified live | `budget_patched_over_budget.json`, `budget_get_after_patch.json` |
| `PATCH {"notes": null}` clears the note, limit untouched | ✅ verified live | `budget_patched_notes_cleared.json` |
| `PATCH {"limit_minor": null}` → 422 with `field: ""` | ✅ verified live | `error_patch_null_limit.json`; `test_patch_explicit_null_limit_is_422` |
| Derived fields sent on PATCH/POST are ignored | ⚠️ from source only (`BudgetUpdate` / `BudgetCreate` declare no such fields; extras ignored) | `app/schemas/budget.py` |
| `DELETE` → 200 `{"deleted": true}`; subsequent GET → 404; list follows; expenses untouched | ✅ verified live (expenses-untouched from source) | `budget_deleted.json`, `error_get_deleted_budget.json` |
| `draft-from-actuals`: one budget per last-month category, limit = category's summed expenses, currency `NGN`, sorted, derived actuals | ✅ verified live | `draft_from_actuals.json` |
| `draft-from-actuals` skips existing categories (returns `[]`), and an empty source returns `[]` | ✅ verified live | `draft_from_actuals_again_skips_existing.json`, `draft_from_actuals_empty_source.json` |
| Draft returns only newly created; partial month drafts only the missing categories | ⚠️ unit-only (live covers the all-exist and none-exist ends) | `test_draft_endpoint_returns_only_created_and_skips_existing` |
| Draft sum above int32 does not 500 (BigInteger limit) | ⚠️ unit-only | `test_draft_sum_exceeding_int32_does_not_500` |
| `copy-last-month`: copies category / limit / currency as edited; skips an already-budgeted category and keeps its limit; only-new returned | ✅ verified live | `copy_last_month.json`, `budgets_list_next_month.json` |
| `copy-last-month` re-run is a no-op (`[]`) | ✅ verified live | `copy_last_month_again_skips_existing.json` |
| `copy-last-month` January → December year rollover | ⚠️ unit-only (live run was in September) | `test_copy_endpoint_january_rolls_back_to_december` |
| Seeder bad month → 422 (`field: "period_month"`); missing body → 422 | ✅ live (bad month) / ⚠️ unit (missing body) | `error_seed_bad_month.json`; `test_seed_endpoints_missing_body_422` |
| Create validation 422 (per-field `field_errors`) | ✅ verified live | `error_create_validation.json` |
| Notes are not copied by `copy-last-month` | ⚠️ from source only (the live source note was cleared before the copy, so the capture does not prove it) | `app/services/finance/budgets.py::copy_last_month` |
| 401 unauthenticated; 404 unknown id | ✅ verified live | `error_unauthenticated.json`, `error_unknown_budget.json` |
| RBAC: founder / team_member / accountant allowed; other roles → 403 | ⚠️ unit-only | `test_accountant_allowed`, `test_forbidden_roles_403`, `test_seed_endpoints_accountant_allowed`, `test_seed_endpoints_forbidden_roles_403` |
| Cross-workspace access → 404 (get / patch / delete); list and seeders isolated | ⚠️ unit-only | `test_cross_tenant_get_patch_delete_404_and_list_isolated`, `test_seed_endpoints_cross_tenant_isolated` |
| 403 / `EMAIL_NOT_VERIFIED` error bodies | ⚠️ not captured | `app/core/errors.py` |
| Concurrent seeders racing on the same month skip the loser instead of 500 | ⚠️ from source only (savepoint per insert) | `app/services/finance/budgets.py::_seed_missing` |
| Mixed-currency actuals summed without conversion; `draft-from-actuals` labels `NGN` | ⚠️ from source only (no test exercises it) | `app/services/finance/expenses.py::expense_category_totals`, `budgets.py::draft_from_actuals` |
| No over-budget notification / event | ⚠️ not built | — |
