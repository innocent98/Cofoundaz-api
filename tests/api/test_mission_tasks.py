from datetime import UTC, date, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, MissionStatus, RoadmapStatus, StartupStage
from app.db.models.mission import Mission, MissionTask
from app.platform import events as events_mod
from app.services.mission.service import streak
from tests.factories import (
    create_membership,
    create_milestone,
    create_mission,
    create_mission_settings,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
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
    create_mission_settings(db, s, weekend_missions=True, mission_size=mission_size)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m = create_milestone(db, ph, title="Validate demand")
    for i in range(task_count):
        create_task(db, m, title=f"T{i}", status=RoadmapStatus.todo, order=i)


def _capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda db, e, p: events.append((e, p)))
    return events


def test_post_custom_task_appends_to_today(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=3, task_count=3)
    db.commit()

    # lazy-generate today's mission first
    r0 = client.get("/api/v1/missions/today", headers=h)
    assert r0.status_code == 200, r0.text
    existing_count = len(r0.json()["data"]["tasks"])

    r = client.post("/api/v1/missions/tasks", json={"title": "Call a customer"}, headers=h)
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["title"] == "Call a customer"
    assert task["roadmap_task_id"] is None
    assert task["status"] == "todo"
    assert task["order"] == existing_count

    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    rows = db.query(MissionTask).filter_by(mission_id=mission.id).all()
    assert len(rows) == existing_count + 1


def test_post_custom_task_requires_editor_role(client, db):
    _u, s, h = _member(db, role=MembershipRole.mentor)
    _seed_roadmap(db, s)
    db.commit()

    r = client.post("/api/v1/missions/tasks", json={"title": "Nope"}, headers=h)
    assert r.status_code == 403, r.text


def test_custom_task_with_no_roadmap_creates_mission(client, db):
    _u, s, h = _member(db)
    db.commit()

    r = client.post("/api/v1/missions/tasks", json={"title": "Jot this down"}, headers=h)
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["title"] == "Jot this down"
    assert task["roadmap_task_id"] is None
    assert task["status"] == "todo"
    assert task["order"] == 0

    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    assert mission is not None
    assert mission.generated_by == "user"
    assert mission.status == MissionStatus.pending
    rows = db.query(MissionTask).filter_by(mission_id=mission.id).all()
    assert len(rows) == 1


def test_complete_task_sets_done_and_emits_event(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=3, task_count=3)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    tasks = r0.json()["data"]["tasks"]
    assert len(tasks) == 3
    task_id = tasks[0]["id"]

    r = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "complete"}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "done"
    assert data["completed_at"] is not None

    assert any(
        e == "mission.task.completed" and p["startup_id"] == str(s.id) and p["task_id"] == task_id
        for e, p in events
    )
    # not all tasks done yet -> mission stays pending, no mission.completed event
    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    assert mission.status == MissionStatus.pending
    assert not any(e == "mission.completed" for e, _ in events)


def test_complete_last_task_completes_mission(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=1, task_count=1)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    tasks = r0.json()["data"]["tasks"]
    assert len(tasks) == 1
    task_id = tasks[0]["id"]

    r = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "complete"}, headers=h)
    assert r.status_code == 200, r.text

    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    assert mission.status == MissionStatus.complete

    assert any(
        e == "mission.completed"
        and p["startup_id"] == str(s.id)
        and p["mission_id"] == str(mission.id)
        and p["mission_date"] == date.today().isoformat()
        for e, p in events
    )
    # streak is only 1 here -- doesn't cross 7/30/100
    assert not any(e == "mission.streak.milestone" for e, _ in events)


def test_rejected_task_does_not_block_mission_completion(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=2, task_count=2)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    tasks = r0.json()["data"]["tasks"]
    assert len(tasks) == 2
    reject_id, complete_id = tasks[0]["id"], tasks[1]["id"]

    r1 = client.patch(
        f"/api/v1/missions/tasks/{reject_id}",
        json={"action": "reject", "reject_reason": "Wrong priority"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text

    r2 = client.patch(
        f"/api/v1/missions/tasks/{complete_id}", json={"action": "complete"}, headers=h
    )
    assert r2.status_code == 200, r2.text

    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    assert mission.status == MissionStatus.complete
    assert any(e == "mission.completed" and p["mission_id"] == str(mission.id) for e, p in events)


def test_reject_last_task_completes_mission(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=3, task_count=3)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    tasks = r0.json()["data"]["tasks"]
    assert len(tasks) == 3
    complete_ids = [tasks[0]["id"], tasks[1]["id"]]
    reject_id = tasks[2]["id"]

    for tid in complete_ids:
        r = client.patch(f"/api/v1/missions/tasks/{tid}", json={"action": "complete"}, headers=h)
        assert r.status_code == 200, r.text

    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    assert mission.status == MissionStatus.pending  # third task still open

    r = client.patch(
        f"/api/v1/missions/tasks/{reject_id}",
        json={"action": "reject", "reject_reason": "Doesn't apply"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    db.refresh(mission)
    assert mission.status == MissionStatus.complete

    completed_events = [
        p for e, p in events if e == "mission.completed" and p["mission_id"] == str(mission.id)
    ]
    assert len(completed_events) == 1
    # streak reflects the now-complete day
    assert streak(db, s) == 1


def test_reject_all_tasks_does_not_complete_mission(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=2, task_count=2)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    tasks = r0.json()["data"]["tasks"]
    assert len(tasks) == 2

    for t in tasks:
        r = client.patch(
            f"/api/v1/missions/tasks/{t['id']}",
            json={"action": "reject", "reject_reason": "Already done"},
            headers=h,
        )
        assert r.status_code == 200, r.text

    mission = db.query(Mission).filter_by(startup_id=s.id, mission_date=date.today()).first()
    assert mission.status == MissionStatus.pending
    assert not any(e == "mission.completed" for e, _ in events)


def test_complete_action_is_idempotent(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=3, task_count=3)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    r1 = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "complete"}, headers=h)
    assert r1.status_code == 200, r1.text
    completed_at_first = r1.json()["data"]["completed_at"]

    r2 = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "complete"}, headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["completed_at"] == completed_at_first

    completed_events = [e for e, _ in events if e == "mission.task.completed"]
    assert len(completed_events) == 1


def test_streak_milestone_fires_at_seven(client, db, monkeypatch):
    events = _capture_events(monkeypatch)
    _u, s, h = _member(db)
    _seed_roadmap(db, s, mission_size=1, task_count=1)

    today = date.today()
    for i in range(1, 7):
        create_mission(db, s, mission_date=today - timedelta(days=i), status=MissionStatus.complete)
    db.commit()

    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    r = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "complete"}, headers=h)
    assert r.status_code == 200, r.text

    assert any(
        e == "mission.streak.milestone" and p == {"startup_id": str(s.id), "streak": 7}
        for e, p in events
    )


def test_snooze_transition(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()
    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    r = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "snooze"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "snoozed"


def test_reorder_transition(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()
    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    r = client.patch(
        f"/api/v1/missions/tasks/{task_id}", json={"action": "reorder", "order": 5}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["order"] == 5


def test_reject_transition(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()
    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    r = client.patch(
        f"/api/v1/missions/tasks/{task_id}",
        json={"action": "reject", "reject_reason": "Wrong priority"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "rejected"


def test_reject_with_bad_reason_is_422(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()
    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    r = client.patch(
        f"/api/v1/missions/tasks/{task_id}",
        json={"action": "reject", "reject_reason": "Because I said so"},
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_task_requires_editor_role(client, db):
    _u, s, h_founder = _member(db, role=MembershipRole.founder)
    _seed_roadmap(db, s)
    db.commit()
    r0 = client.get("/api/v1/missions/today", headers=h_founder)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    mentor = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.commit()
    h_mentor = {
        "Authorization": f"Bearer {create_access_token(str(mentor.id))}",
        "X-Workspace-Id": str(s.id),
    }

    r = client.patch(
        f"/api/v1/missions/tasks/{task_id}", json={"action": "snooze"}, headers=h_mentor
    )
    assert r.status_code == 403, r.text


def test_patch_task_cross_workspace_is_404(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()
    r0 = client.get("/api/v1/missions/today", headers=h)
    task_id = r0.json()["data"]["tasks"][0]["id"]

    _u2, _s2, h2 = _member(db)
    db.commit()

    r = client.patch(f"/api/v1/missions/tasks/{task_id}", json={"action": "snooze"}, headers=h2)
    assert r.status_code == 404, r.text
