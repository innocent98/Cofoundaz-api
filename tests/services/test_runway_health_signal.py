from datetime import UTC, datetime, timedelta

from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus, TransactionDirection
from app.db.models.health_score import HealthSignal
from app.schemas.finance import TransactionCreate
from app.services.finance import runway as runway_svc
from app.services.finance import service as finance_svc
from app.services.health_score.service import recompute_health_score
from tests.factories import create_assessment, create_startup, create_user

KEY = "money.runway_live"
SCORES = {"product": 80, "market": 60, "money": 40, "legal": 100, "team": 20}


def _startup_with_assessment(db, scores=SCORES):
    user = create_user(db)
    startup = create_startup(db, owner=user)
    assessment = create_assessment(db, startup, status=AssessmentStatus.completed)
    db.add(
        AssessmentResult(
            assessment_id=assessment.id,
            dimension_scores=scores,
            overall_provisional=sum(scores.values()) // 5,
            narrative="n",
        )
    )
    db.flush()
    return startup


def _add_tx(db, startup, amount, direction, *, days_ago=0):
    finance_svc.create_transaction(
        db,
        startup_id=startup.id,
        data=TransactionCreate(
            date=datetime.now(UTC).date() - timedelta(days=days_ago),
            description="seed",
            amount_minor=amount,
            currency="NGN",
            direction=direction,
        ),
    )


def _burning_startup(db):
    """1M raised long ago (outside the burn window), 300k out recently: cash 700k, burn 100k/month
    -> 7.0 months runway."""
    startup = _startup_with_assessment(db)
    _add_tx(db, startup, 1_000_000, TransactionDirection.inflow, days_ago=200)
    _add_tx(db, startup, 300_000, TransactionDirection.outflow)
    return startup


def _runway_rows(db, startup):
    return db.query(HealthSignal).filter_by(startup_id=startup.id, key=KEY).all()


def test_runway_signal_scores_now(db):
    """Runway is a real, scoring live signal (it used to be display-only)."""
    startup = _burning_startup(db)
    recompute_health_score(db, startup)
    (row,) = _runway_rows(db, startup)
    assert float(row.value) == 7.0
    assert row.source_ref == "/finance/runway"
    # The contribution is 0 on assessment day (live share ramps in, D-HEA-1b) and never negative.
    assert float(row.contribution) >= 0


def test_profitable_startup_scores_full_marks(db):
    startup = _startup_with_assessment(db)
    _add_tx(db, startup, 900_000, TransactionDirection.inflow)
    _add_tx(db, startup, 100_000, TransactionDirection.outflow)
    recompute_health_score(db, startup)
    (row,) = _runway_rows(db, startup)
    assert float(row.value) == 999.0  # not burning


def test_no_finance_data_means_no_runway_signal(db):
    startup = _startup_with_assessment(db)
    recompute_health_score(db, startup)
    assert _runway_rows(db, startup) == []


def test_money_write_refreshes_health_immediately(db):
    """upsert_runway_signal (called after every money write) forces a recompute, bypassing
    the 15-minute event throttle."""
    startup = _startup_with_assessment(db)
    recompute_health_score(db, startup)
    assert _runway_rows(db, startup) == []
    _add_tx(db, startup, 1_000_000, TransactionDirection.inflow, days_ago=200)
    _add_tx(db, startup, 300_000, TransactionDirection.outflow)
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)
    (row,) = _runway_rows(db, startup)
    assert float(row.value) == 7.0


def test_money_write_before_assessment_is_a_noop(db):
    user = create_user(db)
    startup = create_startup(db, owner=user)
    _add_tx(db, startup, 1_000, TransactionDirection.inflow)
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)  # no assessment yet: no score
    assert db.query(HealthSignal).filter_by(startup_id=startup.id).count() == 0
