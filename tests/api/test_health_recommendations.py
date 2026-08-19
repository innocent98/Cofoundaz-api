from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
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
# tests/api/test_health_score.py, tests/services/test_health_recompute.py, and
# tests/services/assessment/test_complete_concurrency.py, so the assessment is
# fully (and minimally) answered and weak dimensions generate recommendations.
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


def _founder_with_rec(db):
    """A founder whose workspace has a completed assessment (so at least one
    pending recommendation exists) plus their auth headers."""
    u, s, h = _founder(db)
    a = create_assessment(db, s, creator=u, bank_version=ASSESSMENT_BANK.version)
    for key, value in _MINIMAL_ANSWERS:
        create_answer(db, a, question_key=key, value=value)
    db.flush()
    complete_assessment(db, a, s)
    db.commit()
    return u, s, h


def _first_rec_id(client, headers):
    r = client.get("/api/v1/health-score/recommendations", headers=headers)
    return r.json()["data"][0]["id"]


def test_accept_then_idempotent(client, db):
    _u, _s, h = _founder_with_rec(db)
    rid = _first_rec_id(client, h)

    r1 = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["status"] == "accepted"

    r2 = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=h)
    assert r2.status_code == 200, r2.text  # same-status idempotent
    assert r2.json()["data"]["status"] == "accepted"


def test_dismiss_then_idempotent(client, db):
    _u, _s, h = _founder_with_rec(db)
    rid = _first_rec_id(client, h)

    r1 = client.post(f"/api/v1/health-score/recommendations/{rid}/dismiss", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["status"] == "dismissed"

    r2 = client.post(f"/api/v1/health-score/recommendations/{rid}/dismiss", headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["status"] == "dismissed"


def test_cross_transition_409(client, db):
    _u, _s, h = _founder_with_rec(db)
    rid = _first_rec_id(client, h)

    accepted = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=h)
    assert accepted.status_code == 200, accepted.text

    r = client.post(f"/api/v1/health-score/recommendations/{rid}/dismiss", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "RECOMMENDATION_RESOLVED"


def test_member_cannot_accept_403(client, db):
    owner, s, founder_h = _founder_with_rec(db)
    tm = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, tm, s, role=MembershipRole.team_member)
    db.commit()
    member_h = {
        "Authorization": f"Bearer {create_access_token(str(tm.id))}",
        "X-Workspace-Id": str(s.id),
    }

    rid = _first_rec_id(client, founder_h)
    r = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=member_h)
    assert r.status_code == 403, r.text


def test_cross_workspace_404(client, db):
    _u_a, _s_a, h_a = _founder_with_rec(db)
    # A different founder, in their own separate workspace, with their own valid
    # X-Workspace-Id header -- this must clear the require_workspace gate (200-level
    # access to their own workspace) so the 404 below comes from the resource-id
    # lookup inside resolve_recommendation, not from the workspace gate.
    _u_b, _s_b, h_b = _founder(db)

    rid = _first_rec_id(client, h_a)
    r = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=h_b)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
