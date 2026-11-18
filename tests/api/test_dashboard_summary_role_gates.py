"""Dashboard summary: the mission-task KPIs follow the mission read gate (D-MIS-8) and the
assessment calibration flag follows the assessment read gate (D-ASM-1). Other roles get nulls,
never the real figure. Role-matrix style: every role against the same seeded workspace."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import (
    ASSESSMENT_ROLES,
    MISSION_ROLES,
    AssessmentStatus,
    MembershipRole,
)
from tests.factories import (
    create_assessment,
    create_membership,
    create_mission,
    create_mission_task,
    create_startup,
    create_user,
)


def _seeded(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    mission = create_mission(db, startup=s)
    create_mission_task(db, mission=mission, status="done", completed_at=datetime.now(UTC))
    a = create_assessment(db, s, status=AssessmentStatus.completed)
    a.completed_at = datetime.now(UTC) - timedelta(days=1)
    db.add(
        AssessmentResult(
            assessment_id=a.id,
            dimension_scores={"product": 60, "market": 60, "money": 60, "legal": 60, "team": 60},
            overall_provisional=60,
            narrative="n",
        )
    )
    db.flush()
    return owner, s


def _summary(client, user, s):
    h = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(s.id),
    }
    r = client.get("/api/v1/dashboard/summary", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.parametrize("role", list(MembershipRole))
def test_task_kpis_and_calibration_follow_their_owning_read_gate(client, db, role):
    owner, s = _seeded(db)
    viewer = (
        owner
        if role == MembershipRole.founder
        else create_user(db, email_verified_at=datetime.now(UTC))
    )
    if role != MembershipRole.founder:
        create_membership(db, viewer, s, role=role)
    db.commit()

    data = _summary(client, viewer, s)
    kpis, calibration = data["kpis"], data["calibration"]
    if role in MISSION_ROLES:
        assert kpis["tasks_done_this_week"] == 1
        assert kpis["tasks_trend"] is not None
    else:
        assert kpis["tasks_done_this_week"] is None
        assert kpis["tasks_trend"] is None
    if role in ASSESSMENT_ROLES:
        assert calibration == {"assessment_complete": True}
    else:
        assert calibration == {"assessment_complete": None}
