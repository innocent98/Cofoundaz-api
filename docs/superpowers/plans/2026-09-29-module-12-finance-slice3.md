# Module 12 — Finance Hub, Slice 3 (Invoices) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back the Invoices screen — line-item builder with server-computed totals, `draft→sent→paid` lifecycle, client email via Resend, and a paid invoice that posts an idempotent inflow transaction into cash-flow/runway.

**Architecture:** New `invoices` table (line items JSONB, server-computed money, sequential `INV-YYYY-NNN`). A service (`app/services/finance/invoices.py`) owns CRUD, number-gen, transitions, and the paid-invoice inflow. Send enqueues an async `email.invoice_sent` job (existing Resend backend). `overdue` is derived on read, never stored.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Pydantic v2, existing job/email platform, pytest.

**Spec:** docs/superpowers/specs/2026-09-29-module-12-finance-slice3-design.md

## Global Constraints

- Money in integer minor units; every money input/derived total bounded `0 … 2_147_483_647` at validation (422, never 500).
- Services `db.flush()`; endpoints `db.commit()`. `get_db` does not auto-commit.
- Tenancy: every query `filter_by(startup_id=...)`; `startup_id` from `membership.startup_id`, never body.
- RBAC on every invoice route via the Slice-1 `_finance = require_role(founder, team_member, accountant)` dep.
- Enum storage: `Enum(native_enum=False, values_callable=lambda e:[m.value for m in e], length=N)`.
- `overdue` is NEVER a stored status — derived on read (`status==sent AND due_on < today`).
- **Migration:** `0041_finance_invoices`, `down_revision="0040_finance_runway"`. Re-verify develop head before final push; if `0041_validation` (Victoria #103) merged first, bump to `0042_finance_invoices` (down_revision that head). Robust single-head test.
- **`TransactionSource.invoice`** added to the existing varchar-backed enum (len 12; "invoice"=7 chars) — NO migration/DDL needed.
- SAVEPOINT (`begin_nested`) for the unique-number insert. Async email is best-effort (never fails the transition); subject CR/LF-stripped.
- CodeQL test hygiene (no mutating call in assert; no implicit str-concat in list literal). No AI attribution in commits/PR.
- `import datetime as dt` in schema modules (Py3.14 shadow gotcha).

## Review Focus

- **Mark-paid is idempotent** — calling it twice must not post two inflow transactions or double cash-on-hand. Test: mark-paid ×2 → one transaction, `cash_on_hand` rose once. (Task 4)
- **Mark-unpaid fully reverses** — deletes the linked transaction, nulls `transaction_id`/`paid_at`; `cash_on_hand` returns to its pre-paid value. (Task 4)
- **Server ignores client-sent totals** — POST/PATCH recompute subtotal/tax/total from line items; a bogus `total_minor` in the body has no effect. Test: post mismatched total → response shows computed total. (Task 2)
- **Number collision under retry** — two invoices created "simultaneously" get distinct `INV-YYYY-NNN` (unique + SAVEPOINT retry), never a 500. (Task 2)
- **Illegal transition → 422 not 500** — send-from-paid, mark-paid-from-draft, edit/delete a sent invoice all 422 before mutating the row. (Task 3)

---

### Task 1: Enums + model + migration

**Files:** Create `app/db/models/invoice.py`, `alembic/versions/0041_finance_invoices.py`; Modify `app/db/models/enums.py`, `app/db/models/__init__.py`; Test `tests/db/test_invoice_model.py`, `tests/test_invoice_migration.py`

**Interfaces — Produces:** `Invoice` model; `InvoiceStatus` (`draft|sent|paid`), `InvoiceTerms` (`net_15|net_30|due_on_receipt`); `TransactionSource.invoice`.

- [ ] **Step 1: Enums** in `app/db/models/enums.py`

```python
class InvoiceStatus(enum.StrEnum):
    draft = "draft"
    sent = "sent"
    paid = "paid"


class InvoiceTerms(enum.StrEnum):
    net_15 = "net_15"
    net_30 = "net_30"
    due_on_receipt = "due_on_receipt"
```
And add to `TransactionSource`: `invoice = "invoice"` (after `stripe`).

- [ ] **Step 2: Model** `app/db/models/invoice.py`

```python
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import InvoiceStatus, InvoiceTerms

_vc = {"native_enum": False}


def _values(e):  # store enum VALUES
    return [m.value for m in e]


class Invoice(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_email: Mapped[str] = mapped_column(String(320), nullable=False)
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    subtotal_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    terms: Mapped[InvoiceTerms] = mapped_column(
        Enum(InvoiceTerms, native_enum=False, values_callable=_values, length=16),
        nullable=False, default=InvoiceTerms.net_30,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False, values_callable=_values, length=8),
        nullable=False, default=InvoiceStatus.draft,
    )
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_remind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True, unique=True,
    )

    __table_args__ = (UniqueConstraint("startup_id", "number", name="uq_invoices_startup_number"),)
```
Register in `app/db/models/__init__.py` beside `Transaction`.

- [ ] **Step 3: Migration** `0041_finance_invoices.py` (`revision="0041_finance_invoices"`, `down_revision="0040_finance_runway"`) — one `create_table` mirroring the model exactly (match Slice-1/2 server_default style for `created_at/updated_at`, `sa.false()`/`"0"` defaults, `sa.JSON`/`postgresql.JSONB`, the composite `uq_invoices_startup_number`, `startup_id` index, both FKs incl. `transactions` SET NULL). `downgrade()` drops the table. Read `0040_finance_runway.py` first and match its house style so `alembic check` shows no drift.

- [ ] **Step 4: Model test** — persist a draft, assert defaults (`status draft`, `auto_remind False`, `line_items []`); dup `(startup_id, number)` raises IntegrityError (insert the 2nd inside `with db.begin_nested():`); assert enum stored as VALUE (query raw `status`/`terms` == "draft"/"net_30").

- [ ] **Step 5: Migration test** — single-head count + `"0041_finance_invoices"` in `alembic history` (NOT "is THE head").

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/db/test_invoice_model.py tests/test_invoice_migration.py -v`; `poetry run alembic upgrade head && alembic check` (single head, no drift). Commit `feat(finance): invoices model + migration 0041 + InvoiceStatus/Terms enums`.

---

### Task 2: Schemas + create/list/get (number-gen + server-computed totals)

**Files:** Create `app/schemas/invoice.py`, `app/services/finance/invoices.py`; Modify `app/api/v1/endpoints/finance.py`; Test `tests/api/test_invoices.py`

**Interfaces — Produces:** `LineItem`, `InvoiceCreate`, `InvoiceResponse`; `create_invoice`, `list_invoices`, `get_invoice`, `serialize_invoice`, `_next_number`, `_compute_totals`, `_derived_status`.

- [ ] **Step 1: Schemas** `app/schemas/invoice.py`

```python
import datetime as dt
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

INT32 = 2_147_483_647


class LineItem(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(ge=1, le=100000)
    unit_price_minor: int = Field(ge=0, le=INT32)


class InvoiceCreate(BaseModel):
    client_name: str = Field(min_length=1, max_length=200)
    client_email: EmailStr
    line_items: list[LineItem] = Field(min_length=1, max_length=50)
    tax_percent: float = Field(default=0, ge=0, le=100)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    terms: str = Field(default="net_30")  # validated against InvoiceTerms

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: str) -> str:
        from app.db.models.enums import InvoiceTerms
        if v not in {t.value for t in InvoiceTerms}:
            raise ValueError("invalid terms")
        return v


class MoneyLine(BaseModel):
    description: str
    quantity: int
    unit_price_minor: int
    amount_minor: int


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    number: str
    client_name: str
    client_email: str
    line_items: list[MoneyLine]
    subtotal_minor: int
    tax_percent: float
    tax_minor: int
    total_minor: int
    currency: str
    terms: str
    status: str  # derived: draft|sent|paid|overdue
    issued_on: dt.date | None
    due_on: dt.date | None
    paid_at: dt.datetime | None
    auto_remind: bool
    transaction_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
```

- [ ] **Step 2: Service helpers** in `app/services/finance/invoices.py`

```python
import datetime as dt
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import InvoiceStatus, InvoiceTerms
from app.db.models.invoice import Invoice
from app.services.finance.errors import _validation  # dependency-free 422 helper (Slice 1)

_TERM_DAYS = {InvoiceTerms.net_15: 15, InvoiceTerms.net_30: 30, InvoiceTerms.due_on_receipt: 0}
_INT32 = 2_147_483_647


def _compute_totals(line_items: list[dict], tax_percent: float) -> tuple[int, int, int]:
    subtotal = sum(int(li["quantity"]) * int(li["unit_price_minor"]) for li in line_items)
    if subtotal > _INT32:
        raise _validation("line_items", "Invoice subtotal exceeds the maximum allowed amount.")
    tax = round(subtotal * float(tax_percent) / 100)
    total = subtotal + tax
    if total > _INT32:
        raise _validation("line_items", "Invoice total exceeds the maximum allowed amount.")
    return subtotal, tax, total


def _next_number(db: Session, *, startup_id: uuid.UUID, year: int) -> str:
    prefix = f"INV-{year}-"
    n = (
        db.query(Invoice)
        .filter(Invoice.startup_id == startup_id, Invoice.number.like(f"{prefix}%"))
        .count()
    )
    return f"{prefix}{n + 1:03d}"


def _derived_status(inv: Invoice, *, today: dt.date) -> str:
    if inv.status == InvoiceStatus.sent and inv.due_on is not None and inv.due_on < today:
        return "overdue"
    return inv.status.value
```

- [ ] **Step 3: create/list/get** in the same service — `create_invoice(db, *, startup_id, data)` computes totals, gets `_next_number`, inserts inside `with db.begin_nested():` and on `IntegrityError` recomputes the number and retries (bounded, e.g. 5 attempts) → `db.flush()`; `list_invoices(db, *, startup_id, status=None)` newest-first, filtering on the DERIVED status (compute in Python or SQL-derive; simplest: fetch then filter by `_derived_status`); `get_invoice(db, *, startup_id, invoice_id)` → row or `NotFound`; `serialize_invoice(inv, *, today)` → `InvoiceResponse` with per-line `amount_minor = quantity*unit_price_minor` and the derived `status`.

- [ ] **Step 4: Endpoints** in `finance.py` — `POST /invoices` (create draft, commit), `GET /invoices?status=` (list), `GET /invoices/{id}` (get). Gate with `_finance`. Use `datetime.now(UTC).date()` for `today`.

- [ ] **Step 5: Tests** (`tests/api/test_invoices.py`, reuse Slice-1 `_member`/`_headers`): create returns computed totals + `number INV-<year>-001` + status `draft`; **server ignores a bogus body `total_minor`** (not even a field → n/a; instead assert computed total matches hand-calc); second create → `-002`; list newest-first + `?status=draft`; get; RBAC (accountant 200, mentor/investor 403); cross-tenant get → 404; negative/over-int32 unit price → 422; empty line_items → 422; bad email → 422; bad terms → 422.

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/api/test_invoices.py -v`; ruff+mypy. Commit `feat(finance): create/list/get invoices with server-computed totals + INV numbering`.

---

### Task 3: Edit, auto-remind, delete, send (lifecycle, minus paid)

**Files:** Modify `app/services/finance/invoices.py`, `app/api/v1/endpoints/finance.py`; Test `tests/services/test_invoice_transitions.py`, extend `tests/api/test_invoices.py`

**Interfaces — Produces:** `update_invoice`, `delete_invoice`, `send_invoice`; enqueues `email.invoice_sent` (handler is Task 4).

- [ ] **Step 1: Failing transition tests** — edit a draft recomputes totals; editing a `sent`/`paid` invoice → `_validation`/422; `auto_remind` toggle allowed in any status; delete a draft OK, delete `sent`/`paid` → 422; `send` a draft sets `issued_on=today`, `due_on=issued_on+_TERM_DAYS[terms]` (due_on == issued_on for `due_on_receipt`), status→sent, and enqueues one `email.invoice_sent` job (assert via `job_dispatcher`/Job table); `send` from `sent`/`paid` → 422; `send` with no `client_email` can't happen (required at create) but `send` with zero line items can't happen either (min 1 at create) — still assert send only from draft.

- [ ] **Step 2: `update_invoice`** — `InvoiceUpdate` schema (all optional: client_name/client_email/line_items/tax_percent/currency/terms/auto_remind; a `model_validator` rejecting explicit null on any non-nullable field → 422, per Slice-1). Only `auto_remind` may change once status != draft; any other field change on a non-draft → `_validation("status", "Only a draft invoice can be edited.")`. On a draft, recompute totals when line_items/tax change. `db.flush()`.

- [ ] **Step 3: `delete_invoice`** — only `status == draft`; else `_validation`. Endpoint returns `{"deleted": True}`.

- [ ] **Step 4: `send_invoice`** — guard `status == draft` (else 422 before mutating); set `issued_on`, `due_on`, `status = sent`; `db.flush()`; then `job_dispatcher.enqueue(db, "email.invoice_sent", {"invoice_id": str(inv.id)}, inv.startup_id)`. (The handler in Task 4 does the actual send; enqueue is safe even before the handler exists — it just queues a Job row.)

- [ ] **Step 5: Endpoints** — `PATCH /invoices/{id}`, `DELETE /invoices/{id}`, `POST /invoices/{id}/send`. All `_finance`, commit after the service call.

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/services/test_invoice_transitions.py tests/api/test_invoices.py -v`; ruff+mypy. Commit `feat(finance): invoice edit/delete/send lifecycle + email enqueue`.

---

### Task 4: Mark-paid (+ idempotent inflow transaction) and mark-unpaid (reversal)

**Files:** Modify `app/services/finance/invoices.py`, `app/api/v1/endpoints/finance.py`; Test `tests/services/test_invoice_paid_cashflow.py`, extend `tests/api/test_invoices.py`

**Interfaces — Consumes:** `Transaction`, `TransactionDirection.inflow`, `TransactionSource.invoice`, `cash_flow_summary`. **Produces:** `mark_paid`, `mark_unpaid`.

- [ ] **Step 1: Failing tests (Review Focus)** — mark-paid on a `sent` invoice: status→paid, `paid_at` set, exactly ONE inflow `Transaction` created (`direction=in`, `amount_minor=total_minor`, `source=invoice`, `category="Revenue"`, `description` contains the number), `invoice.transaction_id` set; **idempotent**: mark-paid again → still ONE transaction, `cash_flow_summary` cash_on_hand rose by `total_minor` exactly once; mark-paid from `draft` → 422; mark-unpaid on a paid invoice → status→sent, the linked transaction DELETED, `transaction_id`/`paid_at` nulled, cash_on_hand back to pre-paid; mark-unpaid on a non-paid → 422.

- [ ] **Step 2: `mark_paid`**

```python
from datetime import UTC, datetime
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.transaction import Transaction  # confirm the actual module path for the Slice-1 model


def mark_paid(db, *, startup_id, invoice_id):
    inv = get_invoice(db, startup_id=startup_id, invoice_id=invoice_id)
    if inv.status == InvoiceStatus.paid:
        return inv  # idempotent no-op
    if inv.status != InvoiceStatus.sent:
        raise _validation("status", "Only a sent invoice can be marked paid.")
    now = datetime.now(UTC)
    txn = Transaction(
        startup_id=startup_id,
        date=now.date(),
        description=f"Invoice {inv.number} — {inv.client_name}",
        category="Revenue",
        amount_minor=inv.total_minor,
        currency=inv.currency,
        direction=TransactionDirection.inflow,
        source=TransactionSource.invoice,
    )
    db.add(txn)
    db.flush()  # assign txn.id
    inv.status = InvoiceStatus.paid
    inv.paid_at = now
    inv.transaction_id = txn.id
    db.flush()
    return inv
```
(Confirm the Slice-1 `Transaction` import path + its column names against `app/db/models/finance.py` before writing — match them exactly.)

- [ ] **Step 3: `mark_unpaid`** — guard `status == paid` (else 422); if `transaction_id` set, load + `db.delete()` that Transaction; set `transaction_id=None`, `paid_at=None`, `status=sent`; `db.flush()`.

- [ ] **Step 4: Endpoints** — `POST /invoices/{id}/mark-paid`, `POST /invoices/{id}/mark-unpaid`. `_finance`, commit.

- [ ] **Step 5: Cash-flow composition test** — create→send→mark-paid, then `GET /finance/cash-flow` shows cash_on_hand ≥ total_minor and the inflow in the month series; mark-unpaid → cash-flow reverts. (Reuse the Slice-1 cash-flow assertion helpers.)

- [ ] **Step 6: Verify + commit** — `poetry run pytest tests/services/test_invoice_paid_cashflow.py tests/api/test_invoices.py tests/api/test_finance.py -v` (Slice-1 cash-flow stays green). Commit `feat(finance): invoice mark-paid inflow + mark-unpaid reversal (cash-flow integration)`.

---

### Task 5: Client email job (Resend)

**Files:** Create `app/worker/handlers/invoice_email.py`; Modify `app/worker/__main__.py`; Test `tests/worker/test_invoice_email.py` (match the location of existing worker/email tests)

**Interfaces — Consumes:** `get_email_sender`, `EmailMessage`, `Invoice`, `register_handler`.

- [ ] **Step 1: Failing test** — build an `Invoice` (sent), a `Job` with `payload={"invoice_id": ...}`, call `handle_invoice_email(db, job)` with `EMAIL_BACKEND=console` (or inject a fake sender / use the ConsoleEmailSender and assert on `.sent`): assert one message sent, `to == client_email`, subject contains the number, html contains the total and each line-item description. Also: a job for a missing invoice → no send, no crash.

- [ ] **Step 2: Handler** `app/worker/handlers/invoice_email.py` — mirror `app/worker/handlers/email.py`:

```python
import html
import uuid

from sqlalchemy.orm import Session

from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.db.models.startup import Startup  # for the "from {startup name}" subject — confirm path
from app.platform.email import EmailMessage, get_email_sender
from app.worker.runner import register_handler


def render_invoice_email(inv: Invoice, startup_name: str) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(li['description'])} (x{li['quantity']})</td>"
        f"<td style='text-align:right'>{int(li['quantity']) * int(li['unit_price_minor'])}</td></tr>"
        for li in inv.line_items
    )
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:560px'>"
        f"<h2>Invoice {html.escape(inv.number)}</h2>"
        f"<p>From {html.escape(startup_name)}</p>"
        f"<table style='width:100%'>{rows}</table>"
        f"<p>Subtotal: {inv.subtotal_minor}<br>Tax: {inv.tax_minor}<br>"
        f"<strong>Total: {inv.total_minor} {html.escape(inv.currency)}</strong></p>"
        f"<p>Due: {inv.due_on}</p></div>"
    )


def handle_invoice_email(db: Session, job: Job) -> None:
    inv = db.get(Invoice, uuid.UUID(str(job.payload["invoice_id"])))
    if inv is None or not inv.client_email:
        return
    startup = db.get(Startup, inv.startup_id)
    name = startup.name if startup else "Cofoundaz"
    subject = f"Invoice {inv.number} from {name}".replace("\r", " ").replace("\n", " ")
    get_email_sender().send(EmailMessage(to=inv.client_email, subject=subject, html=render_invoice_email(inv, name)))


register_handler("email.invoice_sent", handle_invoice_email)
```
(Confirm `Job.payload`, `Startup` module + `.name`, and the `register_handler` import against the existing `handlers/email.py`.)

- [ ] **Step 3: Register** — import `app.worker.handlers.invoice_email` in `app/worker/__main__.py` alongside the other handler imports (so `register_handler` runs at worker boot). Match the existing import block.

- [ ] **Step 4: Verify + commit** — `poetry run pytest tests/worker/test_invoice_email.py -v`; ruff+mypy. Commit `feat(finance): email.invoice_sent handler (send invoice to client via Resend)`.

---

### Task 6: e2e + docs

**Files:** Create `e2e/test_invoices.py`, `e2e/_captures/invoices/*.json`, `docs/fe-integration-guide-finance-invoices.md`, `docs/sop/2026-09-29-finance-slice3.md`; Modify `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: e2e journey** (model on `e2e/test_finance.py`): onboard founder → `POST /invoices` (draft, assert computed totals + number) → `PATCH` edit a line → `POST /send` (assert status sent, issued_on/due_on set; with `EMAIL_BACKEND=file` assert the email file was written, `to == client_email`; per memory `resend-e2e-recipient` use `client_email = delivered+<uniq>@resend.dev` if the run exercises real Resend) → `POST /mark-paid` → `GET /finance/cash-flow` (assert cash_on_hand rose by the total) → `GET /invoices?status=paid` → `POST /mark-unpaid` → cash-flow reverts. **Capture every request/response body** to `e2e/_captures/invoices/`.

- [ ] **Step 2: Run** `bash scripts/e2e_run.sh` (Docker DB/Redis; memory `local-db-is-docker-not-homebrew`) → green (modulo known Resend-429 auth flake — retry). Commit captures.

- [ ] **Step 3: FE guide** `docs/fe-integration-guide-finance-invoices.md` — payloads VERBATIM from captures. Cover all 8 endpoints; the derived `overdue` status (FE must not PATCH status); server-computed totals (builder numbers advisory); the paid→cash-flow consequence (marking paid moves cash-flow/runway; mark-unpaid reverses); money-in-minor-units; that "Download PDF" is FE-only (deferred); RBAC incl. accountant; which email path was verified-live. Verification table at the end.

- [ ] **Step 4: SOP** `docs/sop/2026-09-29-finance-slice3.md` (match Slice-1/2 SOP style) — what shipped + commits, why, how (number-gen, server totals, lifecycle, paid-inflow idempotency+reversal, async Resend send), files/migration 0041, verification, follow-ups (reminder scheduler, PDF, CRM entity, invoice.* events — all deferred). **Checklist** — mark Module 12 Slice 3 done, keep 4–6 open, update the tally.

- [ ] **Step 5: Commit** `docs(finance): FE guide + SOP + checklist for Module 12 Slice 3 (Invoices)`.

---

## Self-review notes
- Spec coverage: model+numbering (T1/T2), server totals (T2), lifecycle+send (T3), paid-inflow idempotency+reversal (T4), Resend send (T5), e2e+docs (T6). All spec §s mapped.
- Review Focus each pinned: idempotent mark-paid + reversal (T4), server-ignores-total (T2), number retry (T2), illegal transition 422 (T3).
- Type consistency: `_compute_totals`/`_derived_status`/`serialize_invoice`/`InvoiceResponse` names align across tasks; `mark_paid`/`mark_unpaid` signatures identical in T4 def and endpoint use.
- Migration-number + no-attribution + CodeQL-hygiene carried in Global Constraints.
