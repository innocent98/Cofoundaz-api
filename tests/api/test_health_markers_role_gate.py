"""GET /health-score/history/markers reads the activity log, so each marker must pass the same
per-role visibility filter as the activity feed (assessment and document rows are founder + team
member only; roadmap and member rows stay open)."""

from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import ASSESSMENT_ROLES, DOCUMENT_ROLES, MembershipRole
from tests.factories import create_activity, create_membership, create_startup, create_user

ROWS = [
    ("assessment.completed", "assessment"),
    ("document.signature.completed", "signature_request"),
    ("roadmap.milestone.completed", "roadmap_milestone"),
    ("member.joined", "membership"),
]


@pytest.mark.parametrize("role", list(MembershipRole))
def test_markers_follow_the_activity_visibility_rules(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    viewer = owner
    if role != MembershipRole.founder:
        viewer = create_user(db, email_verified_at=datetime.now(UTC))
        create_membership(db, viewer, s, role=role)
    for action, entity in ROWS:
        create_activity(
            db, startup=s, actor=owner, action=action, entity_type=entity, summary=action
        )
    db.commit()

    h = {
        "Authorization": f"Bearer {create_access_token(str(viewer.id))}",
        "X-Workspace-Id": str(s.id),
    }
    r = client.get("/api/v1/health-score/history/markers", params={"range": "30d"}, headers=h)
    assert r.status_code == 200, r.text
    seen = {m["action"] for m in r.json()["data"]}
    assert "roadmap.milestone.completed" in seen and "member.joined" in seen
    assert ("assessment.completed" in seen) is (role in ASSESSMENT_ROLES)
    assert ("document.signature.completed" in seen) is (role in DOCUMENT_ROLES)
