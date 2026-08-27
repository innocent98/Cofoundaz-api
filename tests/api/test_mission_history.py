from datetime import UTC, date, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, MissionStatus, MissionTaskStatus, StartupStage
from tests.factories import (
    create_membership,
    create_mission,
    create_mission_task,
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


def test_history_lists_missions_reverse_chronological(client, db):
    _u, s, h = _member(db)
    older = create_mission(db, s, mission_date=date.today() - timedelta(days=3))
    newer = create_mission(db, s, mission_date=date.today() - timedelta(days=1))
    db.commit()

    r = client.get("/api/v1/missions/history", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["missions"]
    assert [row["mission_date"] for row in rows] == [
        newer.mission_date.isoformat(),
        older.mission_date.isoformat(),
    ]


def test_history_row_reports_completed_total_and_status(client, db):
    _u, s, h = _member(db)
    m = create_mission(db, s, mission_date=date.today() - timedelta(days=1))
    create_mission_task(db, m, title="A", status=MissionTaskStatus.done, order=0)
    create_mission_task(db, m, title="B", status=MissionTaskStatus.done, order=1)
    create_mission_task(db, m, title="C", status=MissionTaskStatus.todo, order=2)
    db.commit()

    r = client.get("/api/v1/missions/history", headers=h)
    assert r.status_code == 200, r.text
    row = r.json()["data"]["missions"][0]
    assert row == {
        "mission_date": m.mission_date.isoformat(),
        "completed": 2,
        "total": 3,
        "status": "pending",
    }


def test_history_total_excludes_rejected_tasks(client, db):
    # A mission of 1 done + 1 rejected is `complete` (completion ignores rejected),
    # so history must read 1/1 complete -- never 1/2 incomplete.
    _u, s, h = _member(db)
    m = create_mission(
        db,
        s,
        mission_date=date.today() - timedelta(days=1),
        status=MissionStatus.complete,
    )
    create_mission_task(db, m, title="Done", status=MissionTaskStatus.done, order=0)
    create_mission_task(db, m, title="Rejected", status=MissionTaskStatus.rejected, order=1)
    db.commit()

    r = client.get("/api/v1/missions/history", headers=h)
    assert r.status_code == 200, r.text
    row = r.json()["data"]["missions"][0]
    assert row["completed"] == 1
    assert row["total"] == 1
    assert row["status"] == "complete"


def test_weekly_completion_pct_over_rolling_seven_days(client, db):
    # Window = the last 7 days ending today (inclusive). 4 missions inside the
    # window (3 complete, 1 pending) => 75%. A complete mission 10 days back is
    # outside the window and must not move the number.
    _u, s, h = _member(db)
    for d, status in (
        (1, MissionStatus.complete),
        (2, MissionStatus.complete),
        (3, MissionStatus.complete),
        (4, MissionStatus.pending),
    ):
        create_mission(db, s, mission_date=date.today() - timedelta(days=d), status=status)
    create_mission(
        db, s, mission_date=date.today() - timedelta(days=10), status=MissionStatus.complete
    )
    db.commit()

    r = client.get("/api/v1/missions/history", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["weekly_completion_pct"] == 75


def test_history_empty_returns_zero_pct_no_crash(client, db):
    _u, _s, h = _member(db)
    db.commit()

    r = client.get("/api/v1/missions/history", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"missions": [], "weekly_completion_pct": 0}


def test_history_requires_auth(client):
    assert client.get("/api/v1/missions/history").status_code == 401


def test_history_mentor_can_read(client, db):
    _u, s, h = _member(db, role=MembershipRole.mentor)
    create_mission(db, s, mission_date=date.today() - timedelta(days=1))
    db.commit()

    r = client.get("/api/v1/missions/history", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["missions"]) == 1


def test_history_cross_workspace_is_forbidden(client, db):
    _u1, s1, _h1 = _member(db)
    create_mission(db, s1, mission_date=date.today() - timedelta(days=1))
    _u2, _s2, h2 = _member(db)
    db.commit()

    # u2 asks for s1's history by passing s1's workspace id -- not a member -> 403.
    h2_cross = {**h2, "X-Workspace-Id": str(s1.id)}
    r = client.get("/api/v1/missions/history", headers=h2_cross)
    assert r.status_code == 403, r.text
