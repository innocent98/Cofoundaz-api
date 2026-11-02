import importlib
import uuid
from datetime import UTC, datetime

from app.core.config import settings
from app.db.models.enums import FinancialModelStatus, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job, JobStatus
from app.platform import llm_budget
from app.services.finance.model_service import serialize_model
from app.worker import runner
from app.worker.handlers import finance_model as handler_mod
from app.worker.handlers.finance_model import handle_finance_model
from tests.factories import create_startup, create_user


def _setup(db, monkeypatch, *, status=FinancialModelStatus.generating):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = FinancialModel(startup_id=s.id, created_by=u.id, status=status, horizon_months=12)
    db.add(m)
    db.flush()
    return u, s, m


def _seed_actuals(db, startup_id):
    today = datetime.now(UTC).date()
    db.add_all(
        [
            Transaction(
                startup_id=startup_id,
                date=today,
                description="Sales",
                amount_minor=900_000,
                currency="NGN",
                direction=TransactionDirection.inflow,
                source=TransactionSource.manual,
            ),
            Transaction(
                startup_id=startup_id,
                date=today,
                description="Rent",
                amount_minor=300_000,
                currency="NGN",
                direction=TransactionDirection.outflow,
                source=TransactionSource.manual,
            ),
        ]
    )
    db.flush()


def _job(model_id):
    return Job(
        type="ai.finance.model", payload={"model_id": str(model_id)}, status=JobStatus.running
    )


def _row(stmt, label):
    return next(r["values"] for r in stmt["rows"] if r["label"] == label)


def test_happy_path_completes_and_balances(db, monkeypatch):
    _u, s, m = _setup(db, monkeypatch)
    _seed_actuals(db, s.id)
    handle_finance_model(db, _job(m.id))

    row = db.get(FinancialModel, m.id)
    assert row.status == FinancialModelStatus.complete
    assert row.error is None
    assert row.generated_at is not None
    assert row.assumptions is not None
    assert row.assumptions["monthly_revenue_growth_pct"] == 0
    for stmt in (row.pnl, row.cash_flow, row.balance_sheet):
        assert stmt is not None
        assert len(stmt["months"]) == 12
        assert stmt["rows"]
        assert set(stmt) == {"months", "rows"}  # no *_raw arrays stored

    bs = row.balance_sheet
    cash = _row(bs, "Cash")
    ar = _row(bs, "Accounts receivable")
    ap = _row(bs, "Accounts payable")
    re = _row(bs, "Retained earnings")
    pic = _row(bs, "Paid-in capital")
    for i in range(12):
        assert cash[i] + ar[i] == ap[i] + re[i] + pic[i]
    # starting cash on hand is the actuals net (900k - 300k), proving actuals fed the engine
    opening = _row(row.cash_flow, "Opening cash")
    assert opening[0] == 600_000


def test_stored_model_serializes_with_months(db, monkeypatch):
    _u, s, m = _setup(db, monkeypatch)
    _seed_actuals(db, s.id)
    handle_finance_model(db, _job(m.id))
    payload = serialize_model(db.get(FinancialModel, m.id))
    assert payload["status"] == "complete"
    assert payload["months"] is not None
    assert len(payload["months"]) == 12
    assert payload["pnl"] is not None
    assert payload["cash_flow"] is not None
    assert payload["balance_sheet"] is not None


def test_mid_call_none_marks_failed_without_partial_model(db, monkeypatch):
    _u, _s, m = _setup(db, monkeypatch)
    monkeypatch.setattr(handler_mod, "metered_complete_json", lambda *a, **k: None)
    handle_finance_model(db, _job(m.id))
    row = db.get(FinancialModel, m.id)
    assert row.status == FinancialModelStatus.failed
    assert row.error == "AI budget exceeded"
    assert row.pnl is None
    assert row.cash_flow is None
    assert row.balance_sheet is None
    assert row.assumptions is None
    assert row.generated_at is None


def test_over_budget_marks_failed(db, monkeypatch):
    _u, s, m = _setup(db, monkeypatch)
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    llm_budget.debit(db, s.id, 5)
    handle_finance_model(db, _job(m.id))
    row = db.get(FinancialModel, m.id)
    assert row.status == FinancialModelStatus.failed
    assert row.error == "AI budget exceeded"
    assert row.pnl is None
    assert row.assumptions is None
    assert row.generated_at is None


def test_resolved_models_are_noop(db, monkeypatch):
    for status in (FinancialModelStatus.complete, FinancialModelStatus.failed):
        _u, _s, m = _setup(db, monkeypatch, status=status)
        handle_finance_model(db, _job(m.id))
        row = db.get(FinancialModel, m.id)
        assert row.status == status
        assert row.pnl is None
        assert row.generated_at is None


def test_missing_model_is_noop(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    handle_finance_model(db, _job(uuid.uuid4()))


def test_missing_startup_is_noop(db, monkeypatch):
    _u, _s, m = _setup(db, monkeypatch)
    monkeypatch.setattr(db, "get", _get_without_startup(db.get))
    handle_finance_model(db, _job(m.id))
    assert m.status == FinancialModelStatus.generating


def _get_without_startup(real_get):
    from app.db.models.startup import Startup

    def fake(entity, ident, *a, **k):
        if entity is Startup:
            return None
        return real_get(entity, ident, *a, **k)

    return fake


def test_handler_is_registered():
    # Sibling tests clear JOB_HANDLERS, so re-run the import-time register_handler side effect.
    runner.JOB_HANDLERS.clear()
    importlib.reload(handler_mod)
    assert "ai.finance.model" in runner.JOB_HANDLERS
