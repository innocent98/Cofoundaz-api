# Module 12 — Finance Hub, Slice 2 (Runway & Scenarios) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist per-startup runway assumptions, project Base/Best/Worst cash-balance scenarios, and turn a low runway into a `finance.runway.low` event + notification and an informational Health-Score signal.

**Architecture:** One new per-startup table (`finance_runway_settings`) holding assumptions + alert-dedup state. A deterministic projection engine (`runway.py` + `scenario_config.py`) builds on Slice-1's `cash_flow_summary`. A single shared `_runway_is_low` predicate keeps the cash-flow banner and the alert in lockstep. Alert evaluation + health-signal refresh hook into the existing Slice-1 transaction mutation endpoints; the event flows through the existing `event_bus` → notifications registry.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres, Pydantic v2, pytest.

**Spec:** docs/superpowers/specs/2026-09-29-module-12-finance-slice2-design.md

## Global Constraints

- Money in integer minor units; every money input bounded `0 … 2_147_483_647` at validation (422, never 500 at flush).
- Services `db.flush()` only; endpoints `db.commit()`. `get_db` does not auto-commit.
- Tenancy: every query `filter_by(startup_id=...)`; `startup_id` from `membership.startup_id`, never the request body.
- RBAC on every finance route via the Slice-1 `_finance = require_role(founder, team_member, accountant)` dep.
- Enum/money storage & migration: migration id ≤ 32 chars, single Alembic head, reversible, additive.
- **Migration number:** `0040_finance_runway`, `down_revision="0039_finance_transactions"`. Re-verify develop head before the final push; bump to `0041_*` if another `0040_*` merged first.
- **No import cycle** (CodeQL required check): the `_runway_is_low` predicate lives in `app/services/finance/runway.py`; `cashflow.py` imports it downward. Nothing imports `cashflow.py` back from `runway.py` except the `cash_flow_summary` call (one direction only — verify no cycle).
- CodeQL test hygiene: no mutating call inside `assert`; no implicit string-concat inside a list literal.
- No AI attribution in any commit message or PR/issue/comment body.
- `import datetime as dt` in schema modules (Py3.14 annotation-shadow gotcha), consistent with Slice 1.

## Review Focus

- **Runway crosses zero mid-horizon** — interpolation must yield a fractional month, not the integer index or `None`. Test: balance positive at month 3, negative at month 4 → `runway_months` between 3 and 4. (Task 2)
- **Already out of cash while burning** — `cash_0 ≤ 0` and burning → `runway_months = 0.0`, `cash_out_date` = current month, and the alert fires. Test in Task 2 + Task 4.
- **Never runs out within horizon** — cash-positive/growing → `runway_months=None`, `cash_out_date=None`, and `runway_low=False` so no alert. (Task 2 + Task 4)
- **Alert re-fire suppression** — saving another transaction while already low emits no second event; recovery then re-crossing fires again. (Task 4)
- **Over-int32 assumption** — `hiring_spend_minor`/`one_off_costs_minor` above int32, or `mom_growth_percent` above 1000 → 422 at validation. (Task 3)

---

### Task 1: Model + migration

**Files:**
- Create: `app/db/models/finance_runway.py`
- Modify: `app/db/models/__init__.py` (register model import if the project imports models there — check and match Slice-1 `finance.py` registration)
- Create: `alembic/versions/0040_finance_runway.py`
- Test: `tests/db/test_finance_runway_model.py`, `tests/test_finance_runway_migration.py`

**Interfaces:**
- Produces: `FinanceRunwaySettings` ORM model (`startup_id` unique; `mom_growth_percent`, `hiring_spend_minor`, `one_off_costs_minor` ints; `alert_is_low` bool; `alert_last_fired_at` datetime|None).

- [ ] **Step 1: Write the model**

```python
# app/db/models/finance_runway.py
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class FinanceRunwaySettings(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_runway_settings"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # user-editable assumptions
    mom_growth_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hiring_spend_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    one_off_costs_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # system-owned alert dedup state
    alert_is_low: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alert_last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```
(Note: `Integer` columns store the int32-bounded assumption values; the schema layer enforces `0 … 2_147_483_647`. Do not use `BigInteger` — the remove the unused import if not needed.)

- [ ] **Step 2: Write the migration** `alembic/versions/0040_finance_runway.py`

```python
"""finance runway settings

Revision ID: 0040_finance_runway
Revises: 0039_finance_transactions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_finance_runway"
down_revision = "0039_finance_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_runway_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mom_growth_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hiring_spend_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("one_off_costs_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_is_low", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("alert_last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("startup_id", name="uq_finance_runway_startup"),
    )
    op.create_index("ix_finance_runway_settings_startup_id", "finance_runway_settings", ["startup_id"])


def downgrade() -> None:
    op.drop_index("ix_finance_runway_settings_startup_id", table_name="finance_runway_settings")
    op.drop_table("finance_runway_settings")
```
(Match server_default columns to the model exactly so `alembic check` reports no drift — mirror the Slice-1 `0039` migration's `created_at/updated_at` handling.)

- [ ] **Step 3: Model test** — persist a row, assert defaults (`alert_is_low is False`, growth/spend `0`), unique `startup_id` raises on a second row for the same startup. Use the autoflush-aware pattern from memory `savepoint-add-inside-begin-nested` if testing the dup constraint.

- [ ] **Step 4: Migration test** (`tests/test_finance_runway_migration.py`) — single-head + revision-in-history form (memory `migration-head-test-brittle`): assert exactly one head, and `"0040_finance_runway"` is in `alembic history`. Do NOT assert it is THE head.

- [ ] **Step 5: Run** `poetry run pytest tests/db/test_finance_runway_model.py tests/test_finance_runway_migration.py -v` → PASS; `poetry run alembic upgrade head && poetry run alembic check` → no drift, single head.

- [ ] **Step 6: Commit** `feat(finance): finance_runway_settings model + migration 0040`

---

### Task 2: Scenario config + projection engine + shared low-predicate

**Files:**
- Create: `app/services/finance/scenario_config.py`, `app/services/finance/runway.py`
- Modify: `app/services/finance/cashflow.py` (import `_runway_is_low` from `runway.py`; use it for the `runway_low` field)
- Test: `tests/services/test_runway_scenarios.py`

**Interfaces:**
- Consumes: `cash_flow_summary(db, *, startup_id) -> dict` (Slice 1).
- Produces:
  - `RUNWAY_HORIZON_MONTHS: int`; scenario constant dict.
  - `_runway_is_low(monthly_burn: int, runway_months: float | None) -> bool`
  - `project_scenarios(baseline: dict, assumptions: dict, *, today: date) -> dict[str, dict]`
    returning `{"base": {...}, "best": {...}, "worst": {...}}`, each `{runway_months, cash_out_date, avg_net_burn_minor, by_month: [{month, cash_balance, net}]}`.

- [ ] **Step 1: Write `scenario_config.py`**

```python
RUNWAY_HORIZON_MONTHS = 12

# (growth_delta_pp, cost_multiplier) per scenario, applied to the stored assumptions.
SCENARIOS: dict[str, tuple[int, float]] = {
    "base": (0, 1.00),
    "best": (10, 0.90),
    "worst": (-10, 1.15),
}
```

- [ ] **Step 2: Write the failing scenario test** (`tests/services/test_runway_scenarios.py`) — cover the Review-Focus rows:

```python
from datetime import date

from app.services.finance.runway import project_scenarios, _runway_is_low


def _baseline(cash, burn, rev, currency="NGN"):
    return {"cash_on_hand": cash, "monthly_burn": burn, "monthly_revenue": rev, "currency": currency}


def _assume(g=0, hire=0, oneoff=0):
    return {"mom_growth_percent": g, "hiring_spend_minor": hire, "one_off_costs_minor": oneoff}


def test_worst_le_base_le_best_runway():
    s = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(), today=date(2026, 4, 1))
    # burning -> all finite; ordering holds
    assert s["worst"]["runway_months"] <= s["base"]["runway_months"] <= s["best"]["runway_months"]


def test_crosses_zero_gives_fractional_month():
    s = project_scenarios(_baseline(3_500_000, 2_000_000, 1_000_000), _assume(), today=date(2026, 4, 1))
    r = s["base"]["runway_months"]
    assert r is not None and 3 < r < 5  # not the integer index, not None


def test_already_out_of_cash_while_burning_is_zero():
    s = project_scenarios(_baseline(0, 2_000_000, 500_000), _assume(), today=date(2026, 4, 1))
    assert s["base"]["runway_months"] == 0.0
    assert s["base"]["cash_out_date"] == "2026-04"


def test_never_runs_out_is_none():
    s = project_scenarios(_baseline(50_000_000, 0, 5_000_000), _assume(g=20), today=date(2026, 4, 1))
    assert s["base"]["runway_months"] is None and s["base"]["cash_out_date"] is None


def test_one_off_reduces_starting_cash():
    with_oneoff = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(oneoff=1_000_000), today=date(2026, 4, 1))
    without = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(), today=date(2026, 4, 1))
    assert with_oneoff["base"]["runway_months"] < without["base"]["runway_months"]


def test_horizon_length_and_no_overflow_on_extreme_growth():
    s = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(g=1000), today=date(2026, 4, 1))
    assert len(s["base"]["by_month"]) == 12  # RUNWAY_HORIZON_MONTHS


def test_runway_is_low_predicate():
    assert _runway_is_low(2_000_000, None) is True       # burning, no runway number -> low
    assert _runway_is_low(2_000_000, 3.0) is True         # < 6 months
    assert _runway_is_low(2_000_000, 12.0) is False       # healthy
    assert _runway_is_low(0, None) is False               # not burning -> not low
```

- [ ] **Step 3: Run to verify it fails** — `ModuleNotFoundError`/`ImportError`.

- [ ] **Step 4: Implement `runway.py` projection functions**

```python
# app/services/finance/runway.py  (projection half; DB half added in Task 3/4)
from datetime import date

from app.services.finance.scenario_config import RUNWAY_HORIZON_MONTHS, SCENARIOS


def _runway_is_low(monthly_burn: int, runway_months: float | None) -> bool:
    # Single source of truth shared with cash_flow_summary's runway_low flag.
    return monthly_burn > 0 and (runway_months is None or runway_months < 6)


def _add_months(d: date, n: int) -> str:
    y, m = d.year, d.month + n
    while m > 12:
        m -= 12
        y += 1
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _project_one(cash0: int, monthly_rev: int, monthly_costs: int, growth_pct: int, hire: int,
                 cost_mult: float, horizon: int) -> tuple[list[dict], float | None, int]:
    balances: list[dict] = []
    prev = cash0
    runway: float | None = None
    net_sum = 0
    if cash0 <= 0 and monthly_costs > monthly_rev:  # already out while burning
        runway = 0.0
    for i in range(1, horizon + 1):
        rev_i = round(monthly_rev * ((1 + growth_pct / 100) ** i))
        cost_i = round(monthly_costs * cost_mult + hire)
        net_i = rev_i - cost_i
        net_sum += cost_i - rev_i  # burn = costs - revenue
        cur = prev + net_i
        balances.append({"month": None, "cash_balance": cur, "net": net_i})  # month filled by caller
        if runway is None and prev > 0 >= cur:  # crossed zero this month
            frac = prev / (prev - cur) if prev != cur else 0.0
            runway = round((i - 1) + frac, 1)
        prev = cur
    avg_net_burn = max(0, round(net_sum / horizon))
    return balances, runway, avg_net_burn


def project_scenarios(baseline: dict, assumptions: dict, *, today: date) -> dict[str, dict]:
    monthly_rev = int(baseline["monthly_revenue"])
    monthly_costs = int(baseline["monthly_burn"]) + monthly_rev  # exact reconstruction
    cash0 = int(baseline["cash_on_hand"]) - int(assumptions["one_off_costs_minor"])
    g = int(assumptions["mom_growth_percent"])
    hire = int(assumptions["hiring_spend_minor"])
    out: dict[str, dict] = {}
    for name, (delta_pp, cost_mult) in SCENARIOS.items():
        growth = max(0, g + delta_pp)
        balances, runway, avg_burn = _project_one(
            cash0, monthly_rev, monthly_costs, growth, hire, cost_mult, RUNWAY_HORIZON_MONTHS
        )
        for idx, b in enumerate(balances, start=1):
            b["month"] = _add_months(today, idx)
        cash_out = _add_months(today, int(runway)) if runway is not None else None
        out[name] = {
            "runway_months": runway,
            "cash_out_date": cash_out,
            "avg_net_burn_minor": avg_burn,
            "by_month": balances,
        }
    return out
```
(Reviewer: verify the crossing predicate `prev > 0 >= cur` and the `runway=0.0` already-out branch against the tests; adjust interpolation rounding if a test pins an exact value.)

- [ ] **Step 5: Refactor `cashflow.py`** to import and use `_runway_is_low` for its `runway_low` field (replace the inline boolean expression), so the two never diverge. Run the Slice-1 cash-flow tests to confirm no behavior change.

- [ ] **Step 6: Run** the scenario tests + `tests/api/test_finance.py` (Slice-1 cash-flow) → all PASS. Then `poetry run ruff check app/services/finance && poetry run mypy app/services/finance`.

- [ ] **Step 7: Commit** `feat(finance): runway scenario projection engine + shared low-runway predicate`

---

### Task 3: Settings persistence + endpoints

**Files:**
- Create: `app/schemas/finance_runway.py`
- Modify: `app/services/finance/runway.py` (add DB fns), `app/api/v1/endpoints/finance.py` (2 routes)
- Test: `tests/api/test_finance_runway.py`

**Interfaces:**
- Consumes: `project_scenarios`, `cash_flow_summary`, `FinanceRunwaySettings`.
- Produces: `get_or_default_settings(db, *, startup_id) -> FinanceRunwaySettings | None`,
  `upsert_assumptions(db, *, startup_id, data) -> FinanceRunwaySettings`,
  `runway_payload(db, *, startup_id) -> dict`.

- [ ] **Step 1: Schemas** (`app/schemas/finance_runway.py`)

```python
import datetime as dt

from pydantic import BaseModel, Field, model_validator

INT32_MAX = 2_147_483_647


class AssumptionsUpdate(BaseModel):
    mom_growth_percent: int | None = Field(default=None, ge=0, le=1000)
    hiring_spend_minor: int | None = Field(default=None, ge=0, le=INT32_MAX)
    one_off_costs_minor: int | None = Field(default=None, ge=0, le=INT32_MAX)

    @model_validator(mode="after")
    def _no_explicit_null(self) -> "AssumptionsUpdate":
        for name in ("mom_growth_percent", "hiring_spend_minor", "one_off_costs_minor"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class ScenarioPoint(BaseModel):
    month: str
    cash_balance: int
    net: int


class Scenario(BaseModel):
    runway_months: float | None
    cash_out_date: str | None
    avg_net_burn_minor: int
    by_month: list[ScenarioPoint]


class AssumptionsOut(BaseModel):
    mom_growth_percent: int
    hiring_spend_minor: int
    one_off_costs_minor: int


class RunwayBaseline(BaseModel):
    cash_on_hand: int
    monthly_burn: int
    monthly_revenue: int
    currency: str


class RunwayResponse(BaseModel):
    assumptions: AssumptionsOut
    baseline: RunwayBaseline
    horizon_months: int
    scenarios: dict[str, Scenario]
```

- [ ] **Step 2: DB service fns in `runway.py`** — `get_or_default_settings` (returns the row or None, never creates on read), `upsert_assumptions` (get-or-create, apply set fields via `model_dump(exclude_unset=True)`, `db.flush()`), `runway_payload` (assemble baseline via `cash_flow_summary`, assumptions from row-or-zero-defaults, `project_scenarios`, `RUNWAY_HORIZON_MONTHS`).

- [ ] **Step 3: Endpoints** in `finance.py`:

```python
@router.get("/runway", response_model=dict[str, Any])
def get_runway(membership=Depends(_finance), user=Depends(get_verified_user), db=Depends(get_db)):
    data = runway_svc.runway_payload(db, startup_id=membership.startup_id)
    return success_response(RunwayResponse(**data).model_dump(mode="json"))


@router.put("/runway/assumptions", response_model=dict[str, Any])
def put_runway_assumptions(payload: AssumptionsUpdate, membership=Depends(_finance),
                           user=Depends(get_verified_user), db=Depends(get_db)):
    runway_svc.upsert_assumptions(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    data = runway_svc.runway_payload(db, startup_id=membership.startup_id)
    return success_response(RunwayResponse(**data).model_dump(mode="json"))
```
(Keep the `Depends(...)  # noqa: B008` + typed params exactly like the Slice-1 routes.)

- [ ] **Step 4: API tests** (`tests/api/test_finance_runway.py`), reusing the Slice-1 `_member`/`_headers` helpers pattern:
  - RBAC: accountant 200 on GET; mentor & investor 403 (parametrized).
  - GET default (no settings row): assumptions all-zero, 3 scenarios present, `horizon_months == 12`.
  - PUT upsert roundtrip: set growth/hiring/one-off → GET reflects them; scenarios change.
  - Explicit null on any assumption → 422; over-int32 hiring → 422; growth 1001 → 422.
  - Cross-tenant: startup A's PUT never affects startup B's GET.

- [ ] **Step 5: Run** `poetry run pytest tests/api/test_finance_runway.py -v` → PASS; register the router (already mounted at `/finance` from Slice 1 — no api.py change needed; confirm).

- [ ] **Step 6: Commit** `feat(finance): GET /finance/runway + PUT /finance/runway/assumptions`

---

### Task 4: runway.low event + notification (fire-once-on-transition)

**Files:**
- Modify: `app/services/finance/runway.py` (`evaluate_runway_alert`), `app/api/v1/endpoints/finance.py` (hook into 3 mutations), `app/services/notifications/registry.py` (SPEC row), `app/services/notifications/categories.py` (category mapping if `category_for` has no default)
- Test: `tests/services/test_runway_alert.py`, extend `tests/api/test_finance_runway.py`

**Interfaces:**
- Consumes: `cash_flow_summary`, `_runway_is_low`, `event_bus`, `FinanceRunwaySettings`.
- Produces: `evaluate_runway_alert(db, *, startup_id) -> None` (publishes `finance.runway.low` on the False→True transition, re-arms on True→False).

- [ ] **Step 1: Failing test** (`tests/services/test_runway_alert.py`): use a throwaway `DispatchingEventBus` or assert on `event_bus.published`. Seed transactions that make the startup burn with < 6 months runway; call `evaluate_runway_alert`; assert one `finance.runway.low` published and `alert_is_low is True`. Call again (still low) → no new event. Add an inflow to recover → call → no event, `alert_is_low is False`. Re-cross → fires again.

- [ ] **Step 2: Implement `evaluate_runway_alert`**

```python
from datetime import UTC, datetime

from app.platform.events import event_bus


def evaluate_runway_alert(db, *, startup_id):
    summary = cash_flow_summary(db, startup_id=startup_id)
    low_now = _runway_is_low(int(summary["monthly_burn"]), summary["runway_months"])
    row = _get_or_create_settings(db, startup_id=startup_id)  # creates the row if absent
    if low_now and not row.alert_is_low:
        row.alert_is_low = True
        row.alert_last_fired_at = datetime.now(UTC)
        db.flush()
        event_bus.publish(db, "finance.runway.low", {
            "startup_id": str(startup_id),
            "runway_months": summary["runway_months"],
            "monthly_burn": int(summary["monthly_burn"]),
            "currency": summary["currency"],
        })
    elif not low_now and row.alert_is_low:
        row.alert_is_low = False
        db.flush()
```

- [ ] **Step 3: SPEC row** in `registry.py` `SPECS`:

```python
"finance.runway.low": _s(_all_active_members, "Your runway is running low"),
```
And confirm `category_for("finance.runway.low")` returns a sane category; if `categories.py` has no default, add a `finance`/`money` mapping there. (Reviewer: verify the notification is created — the registry auto-subscribes every SPECS key.)

- [ ] **Step 4: Hook the mutations** — in `create_transaction`, `update_transaction`, `delete_transaction` endpoints, call `runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)` **after** the service call and **before** `db.commit()`.

- [ ] **Step 5: API test** — POST a transaction that pushes the startup into low runway → assert a notification row exists for the founder (query the notifications table / list endpoint). POST another while low → no duplicate `finance.runway.low` notification.

- [ ] **Step 6: Run** `poetry run pytest tests/services/test_runway_alert.py tests/api/test_finance_runway.py tests/api/test_finance.py -v` → PASS (Slice-1 transaction tests must stay green with the new hook).

- [ ] **Step 7: Commit** `feat(finance): finance.runway.low event + notification on transition`

---

### Task 5: Health-Score informational runway signal

**Files:**
- Modify: `app/services/finance/runway.py` (`build_runway_signal`, `upsert_runway_signal`), `app/services/health_score/service.py` (add signal in `recompute_health_score`), `app/api/v1/endpoints/finance.py` (call `upsert_runway_signal` in the 3 mutations, next to the alert)
- Test: `tests/services/test_runway_health_signal.py`

**Interfaces:**
- Consumes: `cash_flow_summary`, `HealthSignal`, `Dimension`.
- Produces: `build_runway_signal(db, *, startup_id) -> dict | None` (fields for a `money`/`money.runway_live` HealthSignal, `contribution=0`); `upsert_runway_signal(db, *, startup_id) -> None` (delete-by-key-then-add for that startup).

- [ ] **Step 1: Failing test** (`tests/services/test_runway_health_signal.py`): seed a completed assessment + transactions; call `recompute_health_score`; assert a `money.runway_live` signal exists with `contribution == 0` and the overall score equals the score computed without it (signals are display-only). Then mutate a transaction → `upsert_runway_signal` refreshes the row without touching assessment signals. Assert exactly one `money.runway_live` row (no duplicates).

- [ ] **Step 2: Implement** `build_runway_signal` (value = `min(999.99, round(runway_months, 2))` or `0` when `None`; `dimension="money"`, `key="money.runway_live"`, `contribution=0.00`, `source_ref="finance:cash-flow"`) and `upsert_runway_signal` (delete existing `money.runway_live` for the startup, add fresh; `db.flush()`).

- [ ] **Step 3: Wire into `recompute_health_score`** — after the assessment-signal loop (which deletes+re-adds all signals), add the runway signal via the shared builder so a full recompute rebuilds it. Guard: only add when `cash_flow_summary` has data (else skip; no crash on empty finance).

- [ ] **Step 4: Wire into the 3 transaction mutations** — call `runway_svc.upsert_runway_signal(db, startup_id=...)` next to `evaluate_runway_alert`, before `db.commit()`.

- [ ] **Step 5: Run** `poetry run pytest tests/services/test_runway_health_signal.py tests/services -k "health or runway" -v` and the health-score suite → PASS (overall scores unchanged). `poetry run mypy app`.

- [ ] **Step 6: Commit** `feat(finance): informational money.runway_live health signal`

---

### Task 6: e2e + docs (FE guide from captures, SOP, checklist)

**Files:**
- Create: `e2e/test_finance_runway.py`, `e2e/_captures/finance_runway/*.json`
- Create: `docs/fe-integration-guide-finance-runway.md`, `docs/sop/2026-09-29-finance-slice2.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: e2e journey** modeled on `e2e/test_finance.py`: sign up → onboard → seed transactions (mix in/out) → `GET /finance/runway` (assert 3 scenarios, horizon 12, ordering) → `PUT /finance/runway/assumptions` (growth/hiring/one-off) → `GET` reflects change → push into low runway via an outflow transaction → assert a `finance.runway.low` notification is listed. **Capture every request/response body to `e2e/_captures/finance_runway/`.**

- [ ] **Step 2: Run** `bash scripts/e2e_run.sh` (Docker DB/Redis per memory `local-db-is-docker-not-homebrew`) → green (modulo known Resend-429 auth flake). Commit captures.

- [ ] **Step 3: FE integration guide** (`docs/fe-integration-guide-finance-runway.md`) — payloads pasted **verbatim from the captures**. Must call out: the ₦-thousands (FE) vs minor-units (API) conversion for hiring/one-off; `runway_months`/`cash_out_date` null semantics; the one-call GET shape; RBAC incl. accountant; the notification the FE should surface. Verification table at the end (verified-live vs unit-only).

- [ ] **Step 4: SOP** (`docs/sop/2026-09-29-finance-slice2.md`) — what shipped, why, how, files, verification, follow-ups. **Checklist** — mark Module 12 Slice 2 items done; keep Slice 3–6 open.

- [ ] **Step 5: Commit** `docs(finance): FE guide + SOP + checklist for Module 12 Slice 2 (Runway)`

---

## Self-review notes
- Spec coverage: assumptions persistence (T1/T3), full projection incl. series (T2/T3), event fire-once + notification (T4), informational non-scoring health signal (T5), FE guide/SOP/checklist (T6). All spec §s mapped.
- Review Focus rows each pinned to a test (T2 crossing/zero/none/one-off/overflow; T3 over-int32→422; T4 re-fire suppression).
- Type consistency: `project_scenarios`/`runway_payload`/`RunwayResponse` field names align across tasks; `_runway_is_low` signature identical in T2 def and T4 use.
- Migration number + no-cycle + attribution constraints carried in Global Constraints.
