from datetime import UTC, datetime, timedelta

from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus, Dimension, TransactionDirection
from app.db.models.finance import Transaction
from app.db.models.health_score import HealthSignal
from app.schemas.finance import TransactionCreate
from app.services.finance import runway as runway_svc
from app.services.finance import service as finance_svc
from app.services.health_score.scoring import weighted_overall
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


def _assessment_rows(db, startup):
    return (
        db.query(HealthSignal)
        .filter(HealthSignal.startup_id == startup.id, HealthSignal.key.like("assessment.%"))
        .all()
    )


def test_recompute_adds_non_scoring_runway_signal(db):
    with_finance = _burning_startup(db)
    without_finance = _startup_with_assessment(db)

    hs_with = recompute_health_score(db, with_finance)
    hs_without = recompute_health_score(db, without_finance)

    rows = _runway_rows(db, with_finance)
    assert len(rows) == 1
    row = rows[0]
    assert row.dimension == "money"
    assert float(row.contribution) == 0
    assert row.source_ref == "finance:cash-flow"
    assert float(row.value) == 7.0
    assert _runway_rows(db, without_finance) == []

    # Signals are display-only: the overall score and dimension scores are identical with and
    # without the runway signal, and both equal the pure dim_scores computation.
    expected = weighted_overall(SCORES)
    assert hs_with.score == expected
    assert hs_without.score == expected
    assert hs_with.dimension_scores == hs_without.dimension_scores


def test_cash_positive_startup_stores_zero_value(db):
    startup = _startup_with_assessment(db)
    _add_tx(db, startup, 500_000, TransactionDirection.inflow)
    fields = runway_svc.build_runway_signal(db, startup_id=startup.id)
    assert fields is not None
    assert fields["key"] == KEY
    assert fields["value"] == 0
    assert fields["contribution"] == 0


def test_upsert_refreshes_single_row_and_leaves_assessment_signals(db):
    startup = _burning_startup(db)
    recompute_health_score(db, startup)
    assert float(_runway_rows(db, startup)[0].value) == 7.0

    # A further 300k outflow: cash 400k, burn 200k/month -> 2.0 months.
    _add_tx(db, startup, 300_000, TransactionDirection.outflow)
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)  # idempotent

    rows = _runway_rows(db, startup)
    assert len(rows) == 1
    assert float(rows[0].value) == 2.0

    assessment_rows = _assessment_rows(db, startup)
    assert len(assessment_rows) == len(list(Dimension))
    assert {r.dimension for r in assessment_rows} == {d.value for d in Dimension}


def test_deleting_all_transactions_removes_signal(db):
    startup = _burning_startup(db)
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)
    assert len(_runway_rows(db, startup)) == 1

    db.query(Transaction).filter_by(startup_id=startup.id).delete()
    db.flush()
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)
    assert _runway_rows(db, startup) == []


def test_no_transactions_means_no_signal_and_no_crash(db):
    startup = _startup_with_assessment(db)
    fields = runway_svc.build_runway_signal(db, startup_id=startup.id)
    assert fields is None

    hs = recompute_health_score(db, startup)
    assert hs is not None
    assert _runway_rows(db, startup) == []
    total = db.query(HealthSignal).filter_by(startup_id=startup.id).count()
    assert total == len(list(Dimension))

    runway_svc.upsert_runway_signal(db, startup_id=startup.id)
    assert _runway_rows(db, startup) == []


def test_recompute_rebuilds_runway_signal_after_blanket_delete(db):
    startup = _burning_startup(db)
    runway_svc.upsert_runway_signal(db, startup_id=startup.id)
    recompute_health_score(db, startup)
    recompute_health_score(db, startup)

    assert len(_runway_rows(db, startup)) == 1
    total = db.query(HealthSignal).filter_by(startup_id=startup.id).count()
    assert total == len(list(Dimension)) + 1


def test_transaction_endpoints_maintain_runway_signal(client, db):
    from app.core.security import create_access_token
    from app.db.models.enums import MembershipRole
    from tests.factories import create_membership

    user = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=user)
    create_membership(db, user, startup, role=MembershipRole.founder)
    db.flush()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }
    today = datetime.now(UTC).date()
    old = (today - timedelta(days=200)).isoformat()

    def post(direction, amount, on):
        resp = client.post(
            "/api/v1/finance/transactions",
            json={
                "date": on,
                "description": "seed",
                "amount_minor": amount,
                "currency": "NGN",
                "direction": direction,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    post("in", 1_000_000, old)
    out = post("out", 300_000, today.isoformat())

    rows = _runway_rows(db, startup)
    assert len(rows) == 1
    assert float(rows[0].value) == 7.0

    resp = client.delete(f"/api/v1/finance/transactions/{out['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    db.expire_all()
    rows = _runway_rows(db, startup)
    assert len(rows) == 1
    assert float(rows[0].value) == 0  # cash-positive, no burn -> no runway figure
