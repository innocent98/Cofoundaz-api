from datetime import UTC, datetime

from sqlalchemy import text

from app.db.models.enums import FinancialModelStatus
from app.db.models.financial_model import FinancialModel
from tests.factories import create_startup, create_user


def test_financial_model_persists_with_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = FinancialModel(startup_id=s.id)
    db.add(row)
    db.flush()
    got = db.get(FinancialModel, row.id)
    assert got is not None
    assert got.status == FinancialModelStatus.generating
    assert got.horizon_months == 12
    assert got.currency == "NGN"
    assert got.created_by is None
    assert got.assumptions is None
    assert got.pnl is None
    assert got.cash_flow is None
    assert got.balance_sheet is None
    assert got.error is None
    assert got.generated_at is None
    assert got.created_at is not None


def test_financial_model_status_stored_as_value(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = FinancialModel(startup_id=s.id)
    db.add(row)
    db.flush()
    raw = db.execute(
        text("SELECT status FROM financial_models WHERE id = :id"), {"id": row.id}
    ).scalar_one()
    assert raw == "generating"


def test_financial_model_complete_roundtrips_jsonb(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    pnl = {"months": ["2026-10", "2026-11"], "revenue_minor": [100, 200]}
    row = FinancialModel(
        startup_id=s.id,
        created_by=u.id,
        status=FinancialModelStatus.complete,
        horizon_months=24,
        currency="USD",
        assumptions={"growth": 0.1},
        pnl=pnl,
        cash_flow={"closing_minor": [5, 6]},
        balance_sheet={"assets_minor": [7, 8]},
        generated_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    db.expire(row)
    got = db.get(FinancialModel, row.id)
    assert got is not None
    assert got.status == FinancialModelStatus.complete
    assert got.horizon_months == 24
    assert got.currency == "USD"
    assert got.created_by == u.id
    assert got.pnl == pnl
    assert got.assumptions == {"growth": 0.1}
    assert got.cash_flow == {"closing_minor": [5, 6]}
    assert got.balance_sheet == {"assets_minor": [7, 8]}
    assert got.generated_at == datetime(2026, 9, 30, tzinfo=UTC)
