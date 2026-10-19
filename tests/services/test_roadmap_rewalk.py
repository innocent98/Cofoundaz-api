"""Roadmap fixes from the 2026-10-19 click-through re-walk."""

from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, RoadmapStatus
from app.db.models.roadmap import RoadmapTask
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def _setup(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    m = create_milestone(db, phase=create_phase(db, roadmap=create_roadmap(db, startup=s)))
    tasks = [
        create_task(db, milestone=m, title="A"),
        create_task(db, milestone=m, title="B", status=RoadmapStatus.done),
        create_task(db, milestone=m, title="C", status=RoadmapStatus.in_progress),
    ]
    db.commit()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return m, tasks, h


def test_completing_a_milestone_completes_its_open_tasks(client, db):
    """D-RM-5: "Mark Milestone Complete" left 6 open tasks and 0% progress, and those tasks
    kept coming back in Today's Mission."""
    m, tasks, h = _setup(db)
    r = client.patch(f"/api/v1/roadmap/milestones/{m.id}", json={"status": "done"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["progress"] == 100
    assert (
        db.query(RoadmapTask).filter_by(milestone_id=m.id, status=RoadmapStatus.done).count() == 3
    )


def test_reopening_a_milestone_leaves_its_tasks_alone(client, db):
    m, _tasks, h = _setup(db)
    client.patch(f"/api/v1/roadmap/milestones/{m.id}", json={"status": "done"}, headers=h)
    r = client.patch(f"/api/v1/roadmap/milestones/{m.id}", json={"status": "todo"}, headers=h)
    assert r.json()["data"]["status"] == "todo"
    assert r.json()["data"]["progress"] == 100  # the work stays done
