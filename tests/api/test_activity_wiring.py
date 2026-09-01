from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.activity import ActivityLog
from app.db.models.enums import MembershipRole, StartupStage
from app.services.auth.sessions import hash_token
from tests.factories import (
    create_invitation,
    create_membership,
    create_mission_settings,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _seed_roadmap(db, s, *, mission_size=3, task_count=3):
    from tests.factories import create_milestone, create_task

    create_mission_settings(db, s, weekend_missions=True, mission_size=mission_size)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m = create_milestone(db, ph, title="Validate demand")
    from app.db.models.enums import RoadmapStatus

    for i in range(task_count):
        create_task(db, m, title=f"T{i}", status=RoadmapStatus.todo, order=i)


def _activity_rows(db, startup_id, action):
    return (
        db.query(ActivityLog)
        .filter(ActivityLog.startup_id == startup_id, ActivityLog.action == action)
        .all()
    )


def test_completing_mission_task_writes_activity(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    task = r0.json()["data"]["tasks"][0]

    r = client.patch(f"/api/v1/missions/tasks/{task['id']}", json={"action": "complete"}, headers=h)
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "mission.task.completed")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "mission_task"
    assert str(rows[0].entity_id) == task["id"]
    assert task["title"] in rows[0].summary

    # re-completing an already-done task is a no-op at the service layer --
    # must not write a second activity row.
    r2 = client.patch(
        f"/api/v1/missions/tasks/{task['id']}", json={"action": "complete"}, headers=h
    )
    assert r2.status_code == 200, r2.text
    assert len(_activity_rows(db, s.id, "mission.task.completed")) == 1


def test_snoozing_mission_task_writes_activity(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    task = r0.json()["data"]["tasks"][0]

    r = client.patch(f"/api/v1/missions/tasks/{task['id']}", json={"action": "snooze"}, headers=h)
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "mission.task.snoozed")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "mission_task"
    assert str(rows[0].entity_id) == task["id"]
    assert task["title"] in rows[0].summary


def test_rejecting_mission_task_writes_activity(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    task = r0.json()["data"]["tasks"][0]

    r = client.patch(
        f"/api/v1/missions/tasks/{task['id']}",
        json={"action": "reject", "reject_reason": "Wrong priority"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "mission.task.rejected")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "mission_task"
    assert str(rows[0].entity_id) == task["id"]
    assert task["title"] in rows[0].summary


def test_reordering_mission_task_writes_no_activity(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    task = r0.json()["data"]["tasks"][0]

    r = client.patch(
        f"/api/v1/missions/tasks/{task['id']}", json={"action": "reorder", "order": 5}, headers=h
    )
    assert r.status_code == 200, r.text
    assert db.query(ActivityLog).filter(ActivityLog.startup_id == s.id).count() == 0


def test_adding_custom_mission_task_writes_activity(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()
    client.get("/api/v1/missions/today", headers=h)  # lazy-generate today's mission

    r = client.post("/api/v1/missions/tasks", json={"title": "Call a customer"}, headers=h)
    assert r.status_code == 200, r.text
    task = r.json()["data"]

    rows = _activity_rows(db, s.id, "mission.task.added")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "mission_task"
    assert str(rows[0].entity_id) == task["id"]
    assert task["title"] in rows[0].summary


def test_completing_milestone_writes_activity(client, db):
    _u, s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    client.get("/api/v1/roadmap", headers=h)  # ensure generated
    data = client.get("/api/v1/roadmap", headers=h).json()["data"]
    phase_id = data["phases"][0]["id"]

    r = client.post(
        "/api/v1/roadmap/milestones",
        headers=h,
        json={"phase_id": phase_id, "title": "New milestone"},
    )
    mid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"status": "done"})
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "roadmap.milestone.completed")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "roadmap_milestone"
    assert str(rows[0].entity_id) == mid
    assert "New milestone" in rows[0].summary

    # re-patching done->done stays silent -- no second activity row.
    r2 = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"status": "done"})
    assert r2.status_code == 200, r2.text
    assert len(_activity_rows(db, s.id, "roadmap.milestone.completed")) == 1


def test_applying_replan_writes_activity(client, db):
    from datetime import date

    _u, s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    data = client.get("/api/v1/roadmap", headers=h).json()["data"]
    mid = data["phases"][0]["milestones"][0]["id"]
    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"due_on": past})

    pv = client.post("/api/v1/roadmap/replan/preview", headers=h)
    cid = pv.json()["data"]["changes"][0]["change_id"]

    r = client.post("/api/v1/roadmap/replan/apply", headers=h, json={"change_ids": [cid]})
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "roadmap.replanned")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "roadmap"
    assert "1 milestone" in rows[0].summary


def test_completing_assessment_writes_activity(client, db):
    _u, s, h = _member(db, role=MembershipRole.founder)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    values = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    while True:
        nq = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h).json()["data"][
            "next_question"
        ]
        if nq is None:
            break
        if nq["qtype"] == "single_choice":
            val = nq["options"][0]["value"]
        elif nq["qtype"] == "multi_choice":
            val = [nq["options"][0]["value"]]
        else:
            val = values[nq["qtype"]]
        client.post(
            f"/api/v1/assessments/{aid}/answers",
            headers=h,
            json={"question_key": nq["key"], "value": val},
        )

    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "assessment.completed")
    assert len(rows) == 1
    assert rows[0].actor_user_id == _u.id
    assert rows[0].entity_type == "assessment"
    assert str(rows[0].entity_id) == aid

    # idempotent re-complete must not duplicate the activity row.
    r2 = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    assert r2.status_code == 200, r2.text
    assert len(_activity_rows(db, s.id, "assessment.completed")) == 1


def test_accepting_invitation_writes_activity(client, db):
    owner = create_user(db, email="owner@x.com")
    s = create_startup(db, owner=owner, name="Cofoundaz")
    invitee = create_user(db, email="invitee@x.com", email_verified_at=datetime.now(UTC))
    create_invitation(
        db,
        s,
        email="invitee@x.com",
        role=MembershipRole.mentor,
        inviter=owner,
        token_hash=hash_token("wiretok"),
    )
    db.commit()

    r = client.post(
        "/api/v1/invitations/accept",
        json={"token": "wiretok"},
        headers={"Authorization": f"Bearer {create_access_token(str(invitee.id))}"},
    )
    assert r.status_code == 200, r.text

    rows = _activity_rows(db, s.id, "member.joined")
    assert len(rows) == 1
    assert rows[0].actor_user_id == invitee.id
    assert rows[0].entity_type == "membership"
