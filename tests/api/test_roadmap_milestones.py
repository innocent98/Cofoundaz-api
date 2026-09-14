from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _ensure_roadmap(client, headers):
    client.get("/api/v1/roadmap", headers=headers)  # ensure generated


def _first_phase_id(client, headers):
    data = client.get("/api/v1/roadmap", headers=headers).json()["data"]
    return data["phases"][0]["id"]


def test_milestone_create_and_complete_emits_event(client, db, monkeypatch):
    events = []
    from app.platform import events as events_mod

    monkeypatch.setattr(events_mod.event_bus, "publish", lambda db, e, p: events.append((e, p)))

    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones",
        headers=h,
        json={"phase_id": phase_id, "title": "New milestone"},
    )
    assert r.status_code == 201, r.text
    mid = r.json()["data"]["id"]

    events.clear()
    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"status": "done"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "done"
    assert r.json()["data"]["progress"] == 100
    assert any(e == "roadmap.milestone.completed" for e, _ in events)

    # re-patching done->done stays silent
    events.clear()
    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"status": "done"})
    assert r.status_code == 200, r.text
    assert not any(e == "roadmap.milestone.completed" for e, _ in events)


def test_milestone_foreign_phase_404(client, db):
    _ua, _sa, ha = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    _ub, _sb, hb = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, ha)
    a_phase = _first_phase_id(client, ha)

    r = client.post(
        "/api/v1/roadmap/milestones", headers=hb, json={"phase_id": a_phase, "title": "x"}
    )
    assert r.status_code == 404


def test_milestone_owner_must_be_member(client, db):
    import uuid

    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones",
        headers=h,
        json={"phase_id": phase_id, "title": "x", "owner_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_milestone_owner_accepts_active_member(client, db):
    u, s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    other = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, other, s, role=MembershipRole.team_member)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones",
        headers=h,
        json={"phase_id": phase_id, "title": "x", "owner_id": str(other.id)},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["owner"]["id"] == str(other.id)


def test_milestone_write_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.idea)
    db.commit()

    assert (
        client.post(
            "/api/v1/roadmap/milestones",
            headers=h,
            json={"phase_id": "00000000-0000-0000-0000-000000000000", "title": "x"},
        ).status_code
        == 403
    )


def test_patch_milestone_rejects_explicit_null_title(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones", headers=h, json={"phase_id": phase_id, "title": "m"}
    )
    mid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"title": None})
    assert r.status_code == 422, r.text


def test_patch_milestone_rejects_explicit_null_status(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones", headers=h, json={"phase_id": phase_id, "title": "m"}
    )
    mid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"status": None})
    assert r.status_code == 422, r.text


def test_patch_milestone_rejects_explicit_null_order(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones", headers=h, json={"phase_id": phase_id, "title": "m"}
    )
    mid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"order": None})
    assert r.status_code == 422, r.text


def test_patch_milestone_accepts_null_description_due_on_owner(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones",
        headers=h,
        json={
            "phase_id": phase_id,
            "title": "m",
            "description": "desc",
            "due_on": "2026-01-01",
        },
    )
    mid = r.json()["data"]["id"]

    r = client.patch(
        f"/api/v1/roadmap/milestones/{mid}",
        headers=h,
        json={"description": None, "due_on": None, "owner_id": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["description"] is None
    assert body["due_on"] is None
    assert body["owner"] is None


def test_milestone_cross_workspace_patch_404(client, db):
    _ua, _sa, ha = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    _ub, _sb, hb = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, ha)
    phase_id = _first_phase_id(client, ha)

    r = client.post(
        "/api/v1/roadmap/milestones", headers=ha, json={"phase_id": phase_id, "title": "m"}
    )
    mid = r.json()["data"]["id"]

    assert (
        client.patch(
            f"/api/v1/roadmap/milestones/{mid}", headers=hb, json={"title": "z"}
        ).status_code
        == 404
    )


def test_delete_milestone(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    phase_id = _first_phase_id(client, h)

    r = client.post(
        "/api/v1/roadmap/milestones", headers=h, json={"phase_id": phase_id, "title": "m"}
    )
    mid = r.json()["data"]["id"]

    assert client.delete(f"/api/v1/roadmap/milestones/{mid}", headers=h).status_code == 200
    assert (
        client.patch(
            f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"title": "z"}
        ).status_code
        == 404
    )
