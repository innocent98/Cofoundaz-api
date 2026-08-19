from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus, MembershipRole
from app.db.models.health_score import HealthScore
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.service import complete_assessment
from tests.factories import (
    create_answer,
    create_assessment,
    create_membership,
    create_startup,
    create_user,
)

# Chosen to skip every conditional follow-up question, same set used by
# tests/services/test_health_recompute.py and tests/services/assessment/test_complete_concurrency.py,
# so the assessment is fully (and minimally) answered.
_MINIMAL_ANSWERS = [
    ("product_stage", "idea"),
    ("market_clarity", 3),
    ("market_research", "none"),
    ("has_revenue", "no"),
    ("runway_confidence", 3),
    ("incorporated", "no"),
    ("team_size", "solo"),
    ("team_confidence", 3),
]


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_overview_pending_before_assessment(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/health-score", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "pending_assessment"
    assert data["score"] is None
    assert data["band"] is None
    assert data["dimensions"] == []
    assert data["top_recommendations"] == []


def test_overview_ok_after_assessment(client, db):
    u, s, h = _founder(db)
    a = create_assessment(db, s, creator=u, bank_version=ASSESSMENT_BANK.version)
    for key, value in _MINIMAL_ANSWERS:
        create_answer(db, a, question_key=key, value=value)
    db.flush()

    complete_assessment(db, a, s)
    db.commit()

    r = client.get("/api/v1/health-score", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "ok"
    assert isinstance(data["score"], int)
    assert data["band"] is not None
    assert len(data["dimensions"]) == 5
    assert {d["key"] for d in data["dimensions"]} == {"product", "market", "money", "legal", "team"}
    for d in data["dimensions"]:
        assert set(d) == {"key", "label", "score", "band"}
    assert len(data["top_recommendations"]) <= 3
    assert data["summary"]


def test_overview_lazy_computes_when_row_missing(client, db):
    u, s, h = _founder(db)
    # A completed AssessmentResult exists, but nothing has ever called recompute --
    # simulates data that arrived out-of-band (e.g. a backfill) without a HealthScore row.
    a = create_assessment(db, s, creator=u, status=AssessmentStatus.completed)
    db.add(
        AssessmentResult(
            assessment_id=a.id,
            dimension_scores={"product": 80, "market": 70, "money": 60, "legal": 90, "team": 75},
            overall_provisional=75,
            narrative="n",
        )
    )
    db.flush()
    db.commit()

    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 0

    r = client.get("/api/v1/health-score", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "ok"
    assert isinstance(data["score"], int)

    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 1


def test_dimension_detail(client, db):
    u, s, h = _founder(db)
    a = create_assessment(db, s, creator=u, bank_version=ASSESSMENT_BANK.version)
    for key, value in _MINIMAL_ANSWERS:
        create_answer(db, a, question_key=key, value=value)
    db.flush()

    complete_assessment(db, a, s)
    db.commit()

    r = client.get("/api/v1/health-score/dimensions/money", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["key"] == "money"
    assert data["label"] == "Financial"
    assert isinstance(data["score"], int)
    assert data["band"] is not None
    assert isinstance(data["signals"], list)
    assert len(data["signals"]) >= 1
    for sig in data["signals"]:
        assert set(sig) == {"key", "value", "contribution", "source_ref"}
        assert isinstance(sig["value"], float)
        assert isinstance(sig["contribution"], float)
    assert isinstance(data["trend"], list)
    assert len(data["trend"]) >= 1
    assert isinstance(data["recommendations"], list)


def test_dimension_unknown_key_404(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/health-score/dimensions/nope", headers=h)
    assert r.status_code == 404, r.text


def test_history_range(client, db):
    u, s, h = _founder(db)
    a = create_assessment(db, s, creator=u, bank_version=ASSESSMENT_BANK.version)
    for key, value in _MINIMAL_ANSWERS:
        create_answer(db, a, question_key=key, value=value)
    db.flush()

    complete_assessment(db, a, s)
    db.commit()

    r = client.get("/api/v1/health-score/history?range=30d", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data, list)
    assert len(data) >= 1
    for row in data:
        assert set(row) == {"score", "dimension_scores", "delta", "computed_at"}


def test_history_default_range(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/health-score/history", headers=h)
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], list)


def test_history_bad_range_422(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/health-score/history?range=bogus", headers=h)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
