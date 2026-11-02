# Module 12 — Finance Hub, Slice 4a (Expenses) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record operational spend with categories, recurring flags, and receipts (Cloudinary), where each expense posts an outflow `Transaction` that feeds Cash Flow and Runway (symmetric with Slice 3's paid-invoice inflow), plus a per-category monthly breakdown.

**Architecture:** New `expenses` table with a 1:1 `transaction_id` link to a `source=expense` outflow. A service (`app/services/finance/expenses.py`) owns CRUD, the outflow create/sync/reverse, receipts, and the category summary. The managed-transaction guard from Slice 3 is generalized to `{invoice, expense}`. Receipts reuse the existing `app/platform/storage.py` backend.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Pydantic v2, existing storage/runway platform, pytest.

**Spec:** docs/superpowers/specs/2026-09-30-module-12-finance-slice4a-design.md

## Global Constraints

- Money in integer minor units; `amount_minor` bounded `0 … 2_147_483_647` at validation (422, never 500).
- Services `db.flush()`; endpoints `db.commit()`. `get_db` does not auto-commit.
- Tenancy: every query `filter_by(startup_id=...)`; `startup_id` from `membership.startup_id`, never body.
- RBAC on every route via the Slice-1 `_finance = require_role(founder, team_member, accountant)` dep.
- Enum: `TransactionSource.expense` added to the varchar-backed enum (`length=12`; "expense"=7) — NO DDL.
- **Migration:** `0043_finance_expenses`, `down_revision="0042_finance_invoices"`. Re-verify develop head before final push; bump if something merged first. Robust single-head test.
- Money mutations (create/edit/delete expense) run `evaluate_runway_alert` + `upsert_runway_signal` after the service, before commit (as Slice 3).
- Managed-transaction guard: direct PATCH/DELETE of a transaction with `source ∈ {invoice, expense}` → 422.
- Receipts: image/PDF allowlist + size cap; reuse `get_storage()`; store `receipt_key` for delete/replace.
- CodeQL test hygiene (no mutating call in assert; no implicit str-concat in list literal). No AI attribution. `import datetime as dt` in schemas.

## Review Focus

- **One outflow per expense, always in sync** — create posts exactly one `source=expense` outflow; edit changes it in place (stable `transaction_id`); delete removes it. Test: create → one txn; edit amount → same txn id, new amount; delete → txn gone; `cash_on_hand` reflects each step. (Task 2/3)
- **Managed-transaction guard covers expenses** — `PATCH`/`DELETE /finance/transactions/{id}` on a `source=expense` txn → 422; manual still editable. (Task 3)
- **Receipt content-type + size gating** — a non image/PDF upload → 422; oversize → rejected; replace deletes the old key; delete-expense deletes the file. (Task 4)
- **Category summary math** — per-category totals for the month sum to the total; percentages ~100; empty month → zeros, no divide-by-zero. (Task 2)
- **int32 bound** — `amount_minor` over int32 → 422 at validation, not 500 at flush. (Task 2)

---

### Task 1: Enum + model + migration

**Files:** Create `app/db/models/expense.py`, `alembic/versions/0043_finance_expenses.py`; Modify `app/db/models/enums.py`, `app/db/models/__init__.py`; Test `tests/db/test_expense_model.py`, `tests/test_expense_migration.py`

**Interfaces — Produces:** `Expense` model; `TransactionSource.expense`.

- [ ] **Step 1: Enum** — add `expense = "expense"` to `TransactionSource` in `enums.py` (after `invoice`).

- [ ] **Step 2: Model** `app/db/models/expense.py` — mirror `finance.py`/`invoice.py` house style:

```python
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class Expense(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "expenses"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    receipt_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    receipt_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True, unique=True,
    )

    __table_args__ = (UniqueConstraint("id", name="uq_expenses_id_placeholder"),)  # REMOVE — see note
```
(Note: drop that placeholder `__table_args__`; add instead a composite index
`Index("ix_expenses_startup_date", "startup_id", "expense_date")`. Register `Expense` in
`app/db/models/__init__.py` beside `Invoice`.)

- [ ] **Step 3: Migration** `0043_finance_expenses.py` (`revision="0043_finance_expenses"`,
  `down_revision="0042_finance_invoices"`) — one `create_table` matching the model (both FKs — the
  `users` SET NULL and `startups` CASCADE and `transactions` SET NULL; the composite index; unique
  `transaction_id`), reversible `downgrade()`. Read `0042_finance_invoices.py` first and match style so
  `alembic check` shows no drift.

- [ ] **Step 4: Model test** — persist an expense, assert defaults (`recurring False`, `receipt_url None`,
  `currency "NGN"`); the unique `transaction_id` rejects two expenses linking the same txn (insert 2nd
  inside `with db.begin_nested():`). No mutating call in assert.

- [ ] **Step 5: Migration test** — single-head count + `"0043_finance_expenses"` in `alembic history`.

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/db/test_expense_model.py tests/test_expense_migration.py -v`; `poetry run alembic upgrade head && alembic check` (single head, no drift). Commit `feat(finance): expenses model + migration 0043 + TransactionSource.expense`.

---

### Task 2: Schemas + create (with outflow) + list + get + category summary

**Files:** Create `app/schemas/expense.py`, `app/services/finance/expenses.py`; Modify `app/api/v1/endpoints/finance.py`; Test `tests/api/test_expenses.py`, `tests/services/test_expense_cashflow.py`

**Interfaces — Consumes:** `Transaction`, `TransactionDirection.outflow`, `TransactionSource.expense` (`app/db/models/finance.py`); `runway_svc.evaluate_runway_alert`/`upsert_runway_signal`. **Produces:** `create_expense`, `list_expenses`, `get_expense`, `serialize_expense`, `category_summary`; `_post_outflow`.

- [ ] **Step 1: Schemas** `app/schemas/expense.py` — `import datetime as dt`; `ExpenseCreate` (vendor 1..200, category 1..120, expense_date `dt.date`, amount_minor `Field(ge=0, le=2_147_483_647)`, currency default NGN 1..3, recurring bool default False, notes str|None), `ExpenseResponse` (all fields + `has_receipt: bool`, `transaction_id`, timestamps), `CategorySummaryRow` (category, total_minor, percent), `CategorySummary` (month, currency, total_minor, rows).

- [ ] **Step 2: Service — create posts the outflow** in `app/services/finance/expenses.py`:

```python
from datetime import UTC, datetime
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.expense import Expense


def _post_outflow(db, exp: Expense) -> Transaction:
    txn = Transaction(
        startup_id=exp.startup_id, date=exp.expense_date,
        description=f"Expense: {exp.vendor}", category=exp.category,
        amount_minor=exp.amount_minor, currency=exp.currency,
        direction=TransactionDirection.outflow, source=TransactionSource.expense,
    )
    db.add(txn)
    db.flush()
    return txn


def create_expense(db, *, startup_id, created_by, data) -> Expense:
    exp = Expense(startup_id=startup_id, created_by=created_by, **data.model_dump())
    db.add(exp)
    db.flush()
    txn = _post_outflow(db, exp)
    exp.transaction_id = txn.id
    db.flush()
    return exp
```

- [ ] **Step 3: Service — list/get/summary** — `list_expenses(db, *, startup_id, category=None, month=None, date_from=None, date_to=None, recurring=None)` newest-first with filters (`month="YYYY-MM"` → that calendar month range); `get_expense(...)` → row or `NotFound`; `serialize_expense(exp)` (`has_receipt = exp.receipt_url is not None`); `category_summary(db, *, startup_id, month) -> dict` — sum `amount_minor` grouped by `category` for the month, compute `percent = round(100*cat_total/total, 1)` (guard total==0 → empty rows, no divide-by-zero), prevailing currency.

- [ ] **Step 4: Endpoints** in `finance.py` — `POST /expenses` (create → **then `runway_svc.evaluate_runway_alert` + `upsert_runway_signal`** → commit → serialize), `GET /expenses` (filters), `GET /expenses/{id}`, `GET /expenses/summary?month=`. `_finance` dep; `startup_id`/`created_by` from membership/user.

- [ ] **Step 5: Tests** (`tests/api/test_expenses.py` + `tests/services/test_expense_cashflow.py`, reuse Slice-1 `_member`/`_headers`):
  - create returns the expense + posts exactly ONE `source=expense` outflow (amount/date/category match); `transaction_id` set.
  - `GET /finance/cash-flow` cash_on_hand drops by the amount after create (composition).
  - list newest-first + filters (category, month, recurring); get; cross-tenant 404.
  - RBAC (accountant 200, mentor/investor 403).
  - 422s: over-int32 amount, empty vendor/category, explicit-null PATCH later.
  - summary: 2 categories → correct totals + percents summing ~100; empty month → zeros, no error.
  - Bind calls to vars before assert.

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/api/test_expenses.py tests/services/test_expense_cashflow.py tests/api/test_finance.py -v`; ruff+mypy. Commit `feat(finance): expenses create/list/get/summary with outflow into cash-flow`.

---

### Task 3: Edit + delete (sync/reverse outflow) + generalized managed-guard

**Files:** Modify `app/services/finance/expenses.py`, `app/api/v1/endpoints/finance.py`, `app/services/finance/service.py`; Test `tests/services/test_expense_cashflow.py` (extend), `tests/api/test_expenses.py` (extend), `tests/api/test_finance.py` (guard)

**Interfaces — Produces:** `update_expense`, `delete_expense`; generalized `_reject_managed` in `service.py`.

- [ ] **Step 1: Failing tests** — edit amount/date/category/vendor syncs the linked transaction (same `transaction_id`, new values); `GET /finance/cash-flow` reflects the edit; delete removes the linked transaction and reverts cash; explicit-null on a required PATCH field → 422; PATCH/DELETE `/finance/transactions/{id}` where that txn is `source=expense` → 422 (invoice case still 422; manual still 200).

- [ ] **Step 2: `update_expense`** — load via `get_expense`; `ExpenseUpdate` schema (all optional; `model_validator` rejects explicit null on required fields → 422, per Slice-1). Apply set fields; if `amount_minor`/`expense_date`/`category`/`vendor`/`currency` changed AND `transaction_id` is set, load that Transaction and update its matching fields (amount/date/category/currency/description) to stay in sync; `db.flush()`.

- [ ] **Step 3: `delete_expense`** — load; if `transaction_id` set, `db.delete(that Transaction)`; if `receipt_key` set, `get_storage().delete(receipt_key)` (guard None); `db.delete(exp)`; `db.flush()`.

- [ ] **Step 4: Generalize the guard** in `service.py` — rename/extend `_reject_invoice_managed` → `_reject_managed(row)` rejecting `row.source in {TransactionSource.invoice, TransactionSource.expense}` with a message naming the owner ("managed by an invoice/expense; edit that instead"). Keep both `update_transaction` and `delete_transaction` calling it. (Preserve the invoice message/behavior for the invoice case.)

- [ ] **Step 5: Endpoints** — `PATCH /expenses/{id}`, `DELETE /expenses/{id}`: after the service call and before `db.commit()`, run `runway_svc.evaluate_runway_alert` + `upsert_runway_signal` (money changed). `_finance`; commit.

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/services/test_expense_cashflow.py tests/api/test_expenses.py tests/api/test_finance.py -v` (Slice-1 + invoice guard tests stay green); ruff+mypy. Commit `feat(finance): expense edit/delete sync outflow + generalized managed-transaction guard`.

---

### Task 4: Receipts (Cloudinary upload/replace/delete)

**Files:** Modify `app/services/finance/expenses.py`, `app/api/v1/endpoints/finance.py`; Test `tests/api/test_expense_receipts.py`

**Interfaces — Consumes:** `get_storage()` (`app/platform/storage.py`). **Produces:** `attach_receipt`, `remove_receipt`.

- [ ] **Step 1: Failing test** — upload a PNG/PDF to `POST /finance/expenses/{id}/receipt` → 200, `receipt_url` set, `has_receipt True`; a disallowed content-type (e.g. `text/plain`) → 422; oversize (> cap) → rejected; a second upload replaces (old key deleted — assert via a fake/local storage that records deletes); `DELETE /finance/expenses/{id}/receipt` clears it and deletes the file; deleting the expense removes the file. Use the local/stub storage backend the other upload tests use (check `tests/` for how `documents` receipt/file tests fake `get_storage`).

- [ ] **Step 2: `attach_receipt`** in `expenses.py`:

```python
from app.platform.storage import get_storage

_RECEIPT_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}
_MAX_RECEIPT_BYTES = 10 * 1024 * 1024


def attach_receipt(db, *, startup_id, expense_id, filename, content_type, size_bytes, content) -> Expense:
    exp = get_expense(db, startup_id=startup_id, expense_id=expense_id)
    ext = _RECEIPT_EXT.get(content_type)
    if ext is None:
        raise _validation("receipt", "Receipt must be a PNG, JPEG, or PDF.")
    if size_bytes > _MAX_RECEIPT_BYTES:
        raise _validation("receipt", "Receipt exceeds the 10 MB limit.")
    if exp.receipt_key:  # replace
        get_storage().delete(exp.receipt_key)
    key = f"receipts/{startup_id}/{uuid.uuid4().hex}{ext}"
    url = get_storage().save(key, content, content_type)
    exp.receipt_url, exp.receipt_key = url, key
    db.flush()
    return exp
```
(`remove_receipt` — delete the key if set, null both fields, flush.)

- [ ] **Step 3: Endpoints** — `POST /finance/expenses/{id}/receipt` (FastAPI `UploadFile = File(...)`; read `await file.read()` for content + `len` for size; pass `file.content_type`/`file.filename`) → commit → serialize; `DELETE /finance/expenses/{id}/receipt`. `_finance`. (Mirror the documents upload endpoint in `app/api/v1/endpoints/documents.py` for the multipart handling.)

- [ ] **Step 4: Verify + commit** — `poetry run pytest tests/api/test_expense_receipts.py -v`; ruff+mypy. Commit `feat(finance): expense receipt upload/replace/delete via storage backend`.

---

### Task 5: e2e + docs

**Files:** Create `e2e/test_expenses.py`, `e2e/_captures/expenses/*.json`, `docs/fe-integration-guide-finance-expenses.md`, `docs/sop/2026-09-30-finance-slice4a.md`; Modify `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: e2e journey** (model on `e2e/test_finance.py`): onboard founder → create 2–3 expenses (different categories) → `GET /finance/expenses` + filter by category/month → `GET /finance/expenses/summary?month=` (assert per-category totals + percents) → `GET /finance/cash-flow` (assert burn/cash reflect the expenses) → upload a receipt (assert `receipt_url`, using the e2e file/stub storage) → PATCH an amount (cash-flow reflects) → DELETE one (cash reverts). Capture every body to `e2e/_captures/expenses/`.

- [ ] **Step 2: Run** `bash scripts/e2e_run.sh` (Docker DB/Redis) → green (retry on known Resend-429 auth flake). Commit captures (only the new `expenses/*`, not the pre-existing capture noise).

- [ ] **Step 3: FE guide** `docs/fe-integration-guide-finance-expenses.md` — payloads VERBATIM from captures. Cover all 8 endpoints; the expense→outflow→cash-flow/runway consequence + edit-sync + delete-reversal; the **document-once** UX rule (expense OR manual transaction, not both); that the expense's outflow shows in `GET /finance/transactions` but is 422-guarded from direct edit/delete; money-minor-units; receipt content-type/size limits; the category-summary shape; the single-currency limitation. Verification table.

- [ ] **Step 4: SOP** `docs/sop/2026-09-30-finance-slice4a.md` (match Slice-1/2/3 style) — what shipped + commits, why, how (outflow 1:1 sync, generalized guard, receipts, summary), files/migration 0043, verification, follow-ups (Budgets 4b, recurring scheduler, controlled category vocab, multi-currency). **Checklist** — mark Slice 4a done, keep 4b/5/6 open, update tally.

- [ ] **Step 5: Commit** `docs(finance): FE guide + SOP + checklist for Module 12 Slice 4a (Expenses)`.

---

## Self-review notes
- Spec coverage: model+enum (T1), create+outflow+list+get+summary (T2), edit/delete sync+guard (T3), receipts (T4), e2e+docs (T5). All spec §s mapped.
- Review Focus each pinned: one-outflow-in-sync (T2/T3), managed-guard incl. expense (T3), receipt gating (T4), summary math (T2), int32 (T2).
- Type consistency: `_post_outflow`/`create_expense`/`update_expense`/`serialize_expense`/`category_summary` names align across tasks; `_reject_managed` used by both transaction mutators.
- Migration/no-attribution/CodeQL-hygiene carried in Global Constraints.
