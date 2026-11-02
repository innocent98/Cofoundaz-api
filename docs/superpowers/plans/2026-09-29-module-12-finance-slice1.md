# Module 12 Finance Hub — Slice 1 (Cash Flow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Finance Hub's Cash Flow spine — a per-startup `transactions` ledger (CRUD + inline categorize) and a derived `GET /finance/cash-flow` summary (cash on hand, burn, revenue, runway, monthly series).

**Architecture:** A new `finance` service package + `transactions` table (migration `0039`). Money = integer minor units + currency. Derived cash-flow aggregation via range-filtered SQL. Reuses the Module 10 CRUD + aggregation patterns.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Postgres, Alembic, Poetry, pytest.

**Spec:** docs/superpowers/specs/2026-09-29-module-12-finance-slice1-design.md

## Global Constraints

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by `startup_id` (from `membership.startup_id`, never the body); cross-tenant id → `NotFound` (404).
- **RBAC:** every route behind `_finance = require_role(MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant)`; other roles (mentor/investor/…) → 403. (Finance grant deferred.)
- **Endpoint conventions (match `marketing.py`):** `response_model=dict[str, Any]`, return `success_response(...)`; POST/PATCH/DELETE commit; deps `payload, membership=Depends(_finance), user=Depends(get_verified_user), db=Depends(get_db)` with `# noqa: B008`; DELETE returns `success_response({"deleted": True})`.
- **Money:** `amount_minor` non-negative int (minor units); `direction` carries sign; `currency` ISO-4217 string, default `"NGN"`.
- **Validation** via `_validation(field, message)` in a NEW dependency-free `app/services/finance/errors.py` (do NOT put it in service.py — keeps finance service modules cycle-free from the start).
- **`AppError.http_status`** (not `.status_code`); `get_db` does NOT auto-commit.
- **CodeQL (required):** no mutating call inside an `assert` (bind to a var first); no implicit string-concat in a list literal.
- **Migration:** id ≤32 (`0039_finance_transactions`), down_revision `0038_marketing_metrics`. Migration-head test asserts **single head + revision-in-`alembic history`**, not "my revision is THE head".
- **UTC:** trailing-window math uses `datetime.now(UTC).date()`.
- **No AI attribution** in any commit message.

## Review Focus

- **Bad money/enum:** `amount_minor < 0` (create or PATCH) → 422; out-of-enum `direction`, over-long `currency` → 422; body `source` is ignored (always `manual`). — Task 2.
- **Empty-data cash-flow:** no transactions → `cash_on_hand 0`, `monthly_burn 0`, `runway_months null`, `runway_low false`, `by_month` six zeroed months; no ZeroDivisionError. — Task 3.
- **Runway edges:** `monthly_burn == 0` (net-positive) → `runway_months null`; `cash_on_hand ≤ 0` → `runway_months null`; `runway_low` true only when runway not null and < 6. — Task 3.
- **Trailing window:** a transaction 4 months old is excluded from burn/revenue but counted in `cash_on_hand`; `by_month` buckets by calendar month. — Task 3.
- **Tenancy:** list/summary only the caller's transactions; cross-tenant id → 404. — Task 2.
- **RBAC:** 403 for mentor/investor; **accountant allowed (200)**. — Task 2.

---

## Task 1: Enums + Transaction model + errors + migration 0039

**Files:**
- Modify: `app/db/models/enums.py` (`TransactionDirection`, `TransactionSource`)
- Create: `app/db/models/finance.py` (`Transaction`)
- Create: `app/services/finance/__init__.py` (empty), `app/services/finance/errors.py` (`_validation`)
- Create: `alembic/versions/0039_finance_transactions.py`
- Test: `tests/db/test_finance_transaction_model.py`, `tests/test_finance_migration.py`

**Interfaces (produced):** `TransactionDirection` (inflow="in"/outflow="out"), `TransactionSource` (manual/bank/accounting/stripe); `Transaction` model; `app.services.finance.errors._validation`.

- [ ] **Step 1: Write failing model + enum tests**

```python
# tests/db/test_finance_transaction_model.py
from datetime import date

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from tests.factories import create_startup, create_user


def test_direction_source_values():
    assert TransactionDirection.inflow.value == "in"
    assert TransactionDirection.outflow.value == "out"
    assert {s.value for s in TransactionSource} == {"manual", "bank", "accounting", "stripe"}


def test_transaction_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = Transaction(
        startup_id=s.id, date=date(2026, 3, 10), description="Stripe payout",
        category=None, amount_minor=45000000, currency="NGN",
        direction=TransactionDirection.inflow, source=TransactionSource.manual,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.amount_minor == 45000000 and row.category is None
    assert row.direction == TransactionDirection.inflow and row.source == TransactionSource.manual
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_finance_transaction_model.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Add the enums** (`app/db/models/enums.py`, append)

```python
class TransactionDirection(enum.StrEnum):
    inflow = "in"
    outflow = "out"


class TransactionSource(enum.StrEnum):
    manual = "manual"
    bank = "bank"
    accounting = "accounting"
    stripe = "stripe"
```

- [ ] **Step 4: Create the model** (`app/db/models/finance.py`)

```python
import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import TransactionDirection, TransactionSource


class Transaction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "transactions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    direction: Mapped[TransactionDirection] = mapped_column(
        Enum(TransactionDirection, native_enum=False, length=8), nullable=False
    )
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource, native_enum=False, length=12),
        nullable=False, default=TransactionSource.manual,
    )
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (Index("ix_transactions_startup_date", "startup_id", "date"),)
```

Register it: add `from app.db.models.finance import Transaction  # noqa: F401` to `app/db/models/__init__.py` (match how marketing models are registered there).

- [ ] **Step 5: Create errors.py** (`app/services/finance/errors.py`) + `app/services/finance/__init__.py` (empty file)

```python
from app.core.errors import AppError


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR", message, 422, field_errors=[{"field": field, "message": message}]
    )
```

- [ ] **Step 6: Create the migration** (`alembic/versions/0039_finance_transactions.py`)

```python
"""finance transactions

Revision ID: 0039_finance_transactions
Revises: 0038_marketing_metrics
Create Date: 2026-09-29

Module 12 Slice 1 (Cash Flow): the transactions ledger — one new table, additive, no lock on
existing tables. Money is stored as amount_minor (integer minor units) + currency; direction
carries the sign.
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_finance_transactions"
down_revision = "0038_marketing_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        sa.Column(
            "direction",
            sa.Enum("in", "out", name="transactiondirection", native_enum=False, length=8),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum("manual", "bank", "accounting", "stripe", name="transactionsource",
                    native_enum=False, length=12),
            server_default="manual", nullable=False,
        ),
        sa.Column("external_ref", sa.String(length=200), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_transactions_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index(op.f("ix_transactions_startup_id"), "transactions", ["startup_id"], unique=False)
    op.create_index("ix_transactions_startup_date", "transactions", ["startup_id", "date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transactions_startup_date", table_name="transactions")
    op.drop_index(op.f("ix_transactions_startup_id"), table_name="transactions")
    op.drop_table("transactions")
```

- [ ] **Step 7: Migration round-trip + single-head test** (`tests/test_finance_migration.py`)

```python
import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_migration_chain_single_head_includes_0039():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert "0039_finance_transactions" in history.stdout


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0038_marketing_metrics")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr
```

- [ ] **Step 8: Run + drift check**

Run: `poetry run pytest tests/db/test_finance_transaction_model.py tests/test_finance_migration.py -v && poetry run alembic upgrade head && poetry run alembic check`
Expected: PASS; `alembic check` "No new upgrade operations detected."; single head `0039_finance_transactions`.

- [ ] **Step 9: Commit**

```bash
git add app/db/models/enums.py app/db/models/finance.py app/db/models/__init__.py app/services/finance/ alembic/versions/0039_finance_transactions.py tests/db/test_finance_transaction_model.py tests/test_finance_migration.py
git commit -m "feat(finance): transactions ledger table + direction/source enums (migration 0039)"
```

---

## Task 2: Transactions CRUD (schemas + service + endpoints + router)

**Files:**
- Create: `app/schemas/finance.py` (`TransactionCreate`, `TransactionUpdate`, `TransactionResponse`)
- Create: `app/services/finance/service.py` (CRUD + `serialize_transaction`)
- Create: `app/api/v1/endpoints/finance.py` (4 routes)
- Modify: `app/api/v1/api.py` (register the finance router at `/finance`)
- Test: `tests/api/test_finance.py` (new)

**Interfaces (produced):** `service.create_transaction/get_transaction/list_transactions/update_transaction/delete_transaction/serialize_transaction`; routes under `/finance/transactions`.

- [ ] **Step 1: Write failing tests**

```python
# tests/api/test_finance.py
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/finance"
FORBIDDEN_ROLES = [MembershipRole.mentor, MembershipRole.investor]


def _headers(user, startup):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}", "X-Workspace-Id": str(startup.id)}


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def test_transaction_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/transactions", json={
        "date": "2026-03-10", "description": "AWS", "amount_minor": 12050000,
        "currency": "NGN", "direction": "out"}, headers=h)
    assert created.status_code == 200, created.text
    tid = created.json()["data"]["id"]
    assert created.json()["data"]["source"] == "manual"  # body source ignored/forced

    listed = client.get(f"{BASE}/transactions", headers=h)
    assert listed.status_code == 200
    assert any(t["id"] == tid for t in listed.json()["data"]["transactions"])

    patched = client.patch(f"{BASE}/transactions/{tid}", json={"category": "Infrastructure"}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["category"] == "Infrastructure"

    uncat = client.get(f"{BASE}/transactions?uncategorized=true", headers=h)
    assert uncat.status_code == 200
    assert all(t["id"] != tid for t in uncat.json()["data"]["transactions"])  # now categorized

    deleted = client.delete(f"{BASE}/transactions/{tid}", headers=h)
    assert deleted.status_code == 200, deleted.text


def test_negative_amount_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/transactions", json={
        "date": "2026-03-10", "description": "x", "amount_minor": -5, "direction": "out"}, headers=h)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    posted = client.post(f"{BASE}/transactions", json={
        "date": "2026-03-10", "description": "x", "amount_minor": 1, "direction": "out"}, headers=h)
    assert posted.status_code == 403, posted.text


def test_accountant_allowed(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    listed = client.get(f"{BASE}/transactions", headers=h)
    assert listed.status_code == 200, listed.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_finance.py -v`
Expected: FAIL (404 — router not registered).

- [ ] **Step 3: Add schemas** (`app/schemas/finance.py`)

```python
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.db.models.enums import TransactionDirection


class TransactionCreate(BaseModel):
    date: date
    description: str = Field(min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    amount_minor: int = Field(ge=0)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    direction: TransactionDirection


class TransactionUpdate(BaseModel):
    date: date | None = None
    description: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=1, max_length=3)
    direction: TransactionDirection | None = None


class TransactionResponse(BaseModel):
    id: uuid.UUID
    date: date
    description: str
    category: str | None
    amount_minor: int
    currency: str
    direction: TransactionDirection
    source: str
    created_at: datetime
    updated_at: datetime
```

(Note: `category` explicit-null on PATCH is allowed — it re-marks a transaction uncategorized, which is valid, so no field validator is needed here — unlike keyword.keyword in Module 10.)

- [ ] **Step 4: Create the service** (`app/services/finance/service.py`)

```python
import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.schemas.finance import TransactionCreate, TransactionResponse, TransactionUpdate


def serialize_transaction(row: Transaction) -> TransactionResponse:
    return TransactionResponse.model_validate(row, from_attributes=True)


def create_transaction(db: Session, *, startup_id: uuid.UUID, data: TransactionCreate) -> Transaction:
    row = Transaction(
        startup_id=startup_id, source=TransactionSource.manual, **data.model_dump()
    )
    db.add(row)
    db.flush()
    return row


def get_transaction(db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID) -> Transaction:
    row = db.query(Transaction).filter_by(id=transaction_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def list_transactions(
    db: Session, *, startup_id: uuid.UUID,
    category: str | None = None, uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from=None, date_to=None,
) -> list[Transaction]:
    q = db.query(Transaction).filter_by(startup_id=startup_id)
    if uncategorized:
        q = q.filter(Transaction.category.is_(None))
    elif category is not None:
        q = q.filter(Transaction.category == category)
    if direction is not None:
        q = q.filter(Transaction.direction == direction)
    if date_from is not None:
        q = q.filter(Transaction.date >= date_from)
    if date_to is not None:
        q = q.filter(Transaction.date <= date_to)
    return q.order_by(Transaction.date.desc(), Transaction.created_at.desc()).all()


def update_transaction(
    db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction:
    row = get_transaction(db, startup_id=startup_id, transaction_id=transaction_id)
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    db.flush()
    return row


def delete_transaction(db: Session, *, startup_id: uuid.UUID, transaction_id: uuid.UUID) -> None:
    row = get_transaction(db, startup_id=startup_id, transaction_id=transaction_id)
    db.delete(row)
    db.flush()
```

Note: `create_transaction` passes `source=manual` explicitly and `**data.model_dump()` (which has no `source` key), so a client cannot set `source`.

- [ ] **Step 5: Add the endpoints** (`app/api/v1/endpoints/finance.py`)

Mirror `marketing.py`'s structure. Module-level: `router = APIRouter()`, `_finance = require_role(MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant)`, imports (`success_response`, `get_verified_user`, `get_db`, `Membership`, `User`, `finance` service, schemas, `TransactionDirection`). Routes:

```python
@router.post("/transactions", response_model=dict[str, Any])
def create_transaction(
    payload: TransactionCreate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = finance_svc.create_transaction(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(finance_svc.serialize_transaction(row).model_dump())


@router.get("/transactions", response_model=dict[str, Any])
def list_transactions(
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = finance_svc.list_transactions(
        db, startup_id=membership.startup_id, category=category, uncategorized=uncategorized,
        direction=direction, date_from=date_from, date_to=date_to,
    )
    return success_response({"transactions": [finance_svc.serialize_transaction(r).model_dump() for r in rows]})


@router.patch("/transactions/{transaction_id}", response_model=dict[str, Any])
def update_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = finance_svc.update_transaction(db, startup_id=membership.startup_id, transaction_id=transaction_id, data=payload)
    db.commit()
    return success_response(finance_svc.serialize_transaction(row).model_dump())


@router.delete("/transactions/{transaction_id}", response_model=dict[str, Any])
def delete_transaction(
    transaction_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    finance_svc.delete_transaction(db, startup_id=membership.startup_id, transaction_id=transaction_id)
    db.commit()
    return success_response({"deleted": True})
```

Register in `app/api/v1/api.py`: `from app.api.v1.endpoints import finance` and `api_router.include_router(finance.router, prefix="/finance", tags=["finance"])`.

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_finance.py -v`
Expected: PASS. `poetry run ruff check app tests && poetry run mypy app && poetry run black --check app tests` clean.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/finance.py app/services/finance/service.py app/api/v1/endpoints/finance.py app/api/v1/api.py tests/api/test_finance.py
git commit -m "feat(finance): transactions CRUD + inline categorize (founder/team_member/accountant)"
```

---

## Task 3: Cash-flow summary

**Files:**
- Modify: `app/schemas/finance.py` (`MonthPoint`, `CashFlowResponse`)
- Create: `app/services/finance/cashflow.py` (`cash_flow_summary`)
- Modify: `app/api/v1/endpoints/finance.py` (`GET /finance/cash-flow`)
- Test: `tests/api/test_finance.py` (extend)

**Interfaces (produced):** `cashflow.cash_flow_summary(db, *, startup_id) -> dict`; route `GET /finance/cash-flow`.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/api/test_finance.py
from datetime import date


def _seed(client, h, txns):
    for t in txns:
        r = client.post(f"{BASE}/transactions", json=t, headers=h)
        assert r.status_code == 200, r.text


def test_cash_flow_empty_is_zeroed(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/cash-flow", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["cash_on_hand"] == 0 and d["monthly_burn"] == 0
    assert d["runway_months"] is None and d["runway_low"] is False
    assert len(d["by_month"]) == 6


def test_cash_flow_runway_and_low_flag(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    # 1,000,000 minor cash on hand; outflow-only over the window → burn>0 → finite runway
    _seed(client, h, [
        {"date": today, "description": "raise", "amount_minor": 1_000_000, "currency": "NGN", "direction": "in"},
        {"date": today, "description": "spend", "amount_minor": 600_000, "currency": "NGN", "direction": "out"},
    ])
    d = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    assert d["cash_on_hand"] == 400_000
    assert d["monthly_burn"] > 0 and d["runway_months"] is not None
    assert d["runway_low"] is True  # tiny cash / burn -> < 6 months


def test_cash_flow_runway_null_when_net_positive(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    _seed(client, h, [
        {"date": today, "description": "raise", "amount_minor": 5_000_000, "currency": "NGN", "direction": "in"},
        {"date": today, "description": "spend", "amount_minor": 100_000, "currency": "NGN", "direction": "out"},
    ])
    d = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    assert d["monthly_burn"] == 0 and d["runway_months"] is None and d["runway_low"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_finance.py -v -k cash_flow`
Expected: FAIL (route 404).

- [ ] **Step 3: Add schemas** (`app/schemas/finance.py`)

```python
class MonthPoint(BaseModel):
    month: str  # "YYYY-MM"
    inflow: int
    outflow: int
    net: int


class CashFlowResponse(BaseModel):
    cash_on_hand: int
    monthly_burn: int
    monthly_revenue: int
    runway_months: float | None
    runway_low: bool
    currency: str
    by_month: list[MonthPoint]
```

- [ ] **Step 4: Implement `cash_flow_summary`** (`app/services/finance/cashflow.py`)

```python
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models.enums import TransactionDirection
from app.db.models.finance import Transaction

_IN = TransactionDirection.inflow
_OUT = TransactionDirection.outflow


def _months_back(today: date, n: int) -> date:
    """First day of the month n months before `today`'s month."""
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def cash_flow_summary(db: Session, *, startup_id: uuid.UUID) -> dict:
    today = datetime.now(UTC).date()
    base = db.query(Transaction).filter(Transaction.startup_id == startup_id)

    inflow = func.coalesce(func.sum(case((Transaction.direction == _IN, Transaction.amount_minor), else_=0)), 0)
    outflow = func.coalesce(func.sum(case((Transaction.direction == _OUT, Transaction.amount_minor), else_=0)), 0)

    # cash on hand: all-time
    all_in, all_out = base.with_entities(inflow, outflow).one()
    cash_on_hand = int(all_in) - int(all_out)

    # trailing 3 months (from the first day of the month 2 months ago through today)
    since_3mo = _months_back(today, 2)
    win_in, win_out = base.filter(Transaction.date >= since_3mo).with_entities(inflow, outflow).one()
    win_in, win_out = int(win_in), int(win_out)
    net_out = win_out - win_in
    monthly_burn = max(0, round(net_out / 3))
    monthly_revenue = round(win_in / 3)

    runway_months = (
        round(cash_on_hand / monthly_burn, 1)
        if monthly_burn > 0 and cash_on_hand > 0
        else None
    )
    runway_low = runway_months is not None and runway_months < 6

    # by_month: last 6 calendar months, ascending, zero-filled
    since_6mo = _months_back(today, 5)
    month = func.to_char(func.date_trunc("month", Transaction.date), "YYYY-MM")
    rows = (
        base.filter(Transaction.date >= since_6mo)
        .with_entities(month.label("m"), inflow, outflow)
        .group_by(month).all()
    )
    got = {m: (int(i), int(o)) for m, i, o in rows}
    by_month = []
    for k in range(5, -1, -1):
        d = _months_back(today, k)
        key = f"{d.year:04d}-{d.month:02d}"
        i, o = got.get(key, (0, 0))
        by_month.append({"month": key, "inflow": i, "outflow": o, "net": i - o})

    # prevailing currency: most common on the startup's transactions, else default
    cur_row = (
        base.with_entities(Transaction.currency, func.count())
        .group_by(Transaction.currency).order_by(func.count().desc()).first()
    )
    currency = cur_row[0] if cur_row is not None else "NGN"

    return {
        "cash_on_hand": cash_on_hand, "monthly_burn": monthly_burn,
        "monthly_revenue": monthly_revenue, "runway_months": runway_months,
        "runway_low": runway_low, "currency": currency, "by_month": by_month,
    }
```

- [ ] **Step 5: Add the endpoint** (`app/api/v1/endpoints/finance.py`)

```python
@router.get("/cash-flow", response_model=dict[str, Any])
def cash_flow(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    from app.services.finance import cashflow as cashflow_svc
    data = cashflow_svc.cash_flow_summary(db, startup_id=membership.startup_id)
    return success_response(CashFlowResponse(**data).model_dump(mode="json"))
```

(Import `CashFlowResponse`. A module-top `from app.services.finance import cashflow` is also fine — there is no cycle; the local import just mirrors the pattern and is optional.)

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_finance.py -v`
Expected: PASS. ruff/mypy/black clean.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/finance.py app/services/finance/cashflow.py app/api/v1/endpoints/finance.py tests/api/test_finance.py
git commit -m "feat(finance): GET /finance/cash-flow summary (cash/burn/revenue/runway + monthly series)"
```

---

## Task 4: E2E journey + captures

**Files:**
- Create: `e2e/test_finance.py`
- Produces: `e2e/_captures/finance/` — transaction create/list, cash-flow captures.

**Interfaces (consumed):** the e2e harness's founder-client + `capture` helpers (model on `e2e/test_marketing.py`).

- [ ] **Step 1: Write the journey**

Create `e2e/test_finance.py` modeled on `e2e/test_marketing.py` (same auth/onboard + `capture(group, name, resp)` helpers; bind every mutating call to a var; no mutating call inside an assert): authenticate a founder, then:
- `POST /api/v1/finance/transactions` a few points (inflow + outflow, one uncategorized) → capture `transaction_created`; `GET /finance/transactions` → capture `transactions_list`; PATCH one to categorize.
- `GET /api/v1/finance/cash-flow` → capture `cash_flow` (cash_on_hand / burn / runway_months / by_month populated).

- [ ] **Step 2: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the finance journey passes; captures written. Docker Compose pg/redis (Homebrew stopped); commit ONLY `e2e/test_finance.py` + `e2e/_captures/finance/` (restore other modules' capture churn).

- [ ] **Step 3: Commit**

```bash
git add e2e/test_finance.py e2e/_captures/finance/
git commit -m "test(e2e): finance cash-flow journey (transactions + summary) with captures"
```

---

## Task 5: Docs — FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-finance-cashflow.md`
- Create: `docs/sop/2026-09-29-finance-slice1.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE guide** — `docs/fe-integration-guide-finance-cashflow.md`, payloads **verbatim from the Task-4 captures**. Cover: transactions CRUD (`/finance/transactions`; **`amount_minor` is integer minor units + `currency` ISO — FE renders a symbol, map it**; `direction` `in`/`out`; `source` always `manual` this slice; `uncategorized=true` filter; DELETE → `{deleted:true}`); `GET /finance/cash-flow` (cash_on_hand / monthly_burn / monthly_revenue in minor units, `runway_months` float-or-**null**, `runway_low` bool for the danger banner, `by_month` 6-point series, `currency`). Auth: founder/team_member/accountant → others 403. Note runway **events + Health-Score signal are Slice 2** (this slice exposes the number only). Verification table.

- [ ] **Step 2: SOP** — `docs/sop/2026-09-29-finance-slice1.md`, matching the recent SOP style: what shipped (transactions ledger, CRUD+categorize, cash-flow summary; migration 0039; new finance service package), why (Module 12 Slice 1 of 6; PRD 12.1; FE cross-checked — cash-flow read aligned, money in minor units), how (amount_minor+direction sign; null-safe runway; trailing-3-mo burn/revenue; UTC; `_validation` in finance/errors.py to preempt cycles), files/migration, verification (unit + e2e), follow-ups (runway scenarios + event + Health-Score signal = S2; invoices S3; expenses/budgets S4; model S5; integrations S6; multi-currency conversion not done).

- [ ] **Step 3: Checklist** — `docs/checklist/PROJECT_CHECKLIST.md`: add **Module 12 (Finance Hub)** as a new 🟡 open module with the 6-slice plan, Slice 1 done; update the tally (13 fully complete, **1 open (12)**, 12 not started). Reconcile the snapshot + status table so they agree. Follow the file's format.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(finance): FE guide + SOP + checklist for Module 12 Slice 1 (Cash Flow)"
```

---

## Final verification (before opening the PR)

- [ ] **Full local CI parity, all green** (Homebrew pg/redis stopped, Docker up):

```bash
poetry run black --check app tests && poetry run isort --check-only app tests && \
poetry run ruff check app tests && poetry run mypy app && \
poetry run pylint app --fail-under=9.5 && poetry run bandit -q -r app && \
poetry run pytest --cov=app --cov-fail-under=95 && \
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads && \
bash scripts/e2e_run.sh
```

Expected: green; `alembic heads` = single head `0039_finance_transactions`. (Resend-429 auth/onboarding failures are the known external-quota flake.)

- [ ] **No AI attribution:** `git log develop..HEAD --format='%an <%ae>%n%b'` shows none.
- [ ] **CodeQL clean:** no mutating call inside an `assert`; no implicit string concat in a list literal.

---

## Self-Review

**Spec coverage:** transactions model/migration → T1; CRUD → T2; cash-flow summary → T3; e2e → T4; docs → T5. Decisions D1–D6 covered. Runway scenarios/events/Health-Score (S2), invoices (S3), expenses/budgets (S4), model (S5), integrations (S6), multi-currency conversion — all out of scope.

**Placeholder scan:** every code step carries real code; the only "model on the existing X" note is T4's e2e (copy the real harness helpers, as prior slices did).

**Type consistency:** `finance.service.*` (create/get/list/update/delete/serialize_transaction), `cashflow.cash_flow_summary`, `TransactionCreate/Update/Response`, `MonthPoint`, `CashFlowResponse`, `TransactionDirection`(in/out)/`TransactionSource`, and `Transaction` columns are consistent across T1–T4. `_validation` lives in `finance/errors.py` (no service cycle). `_finance` gate includes `accountant`.

**Review Focus:** negative amount / bad enum / source-ignored (T2), empty-data + runway null-safety + trailing window (T3), tenancy (T2), RBAC incl. accountant-allowed (T2) — each has an owning task's test.
