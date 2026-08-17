import uuid
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_assessment, create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_next_question_for_resume(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    r = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["next_question"]["key"] == "product_stage"


def test_next_question_foreign_assessment_404(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.get(f"/api/v1/assessments/{uuid.uuid4()}/next-question", headers=h)
    assert r.status_code == 404


def test_next_question_cross_tenant_assessment_404(client, db):
    """An assessment that genuinely exists, but under a DIFFERENT workspace, must 404.

    Distinct from test_next_question_foreign_assessment_404 (which only probes a
    random, never-persisted UUID): this proves the _assessment() lookup filters on
    startup_id, not just id. A bare `Assessment.id == assessment_id` fetch would
    also pass the other test but would leak this one.
    """
    _u_b, s_b, h_b = _founder(db)
    b_assessment = create_assessment(db, s_b)
    db.commit()

    _u_a, _s_a, h_a = _founder(db)
    db.commit()

    r = client.get(f"/api/v1/assessments/{b_assessment.id}/next-question", headers=h_a)
    assert r.status_code == 404

    # Sanity: B's own founder CAN read it — proves the 404 above is tenancy, not a bug.
    r_b = client.get(f"/api/v1/assessments/{b_assessment.id}/next-question", headers=h_b)
    assert r_b.status_code == 200, r_b.text
