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


def test_create_patch_delete_phase(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)

    r = client.post("/api/v1/roadmap/phases", headers=h, json={"name": "Extra phase"})
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/phases/{pid}", headers=h, json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "Renamed"

    assert client.delete(f"/api/v1/roadmap/phases/{pid}", headers=h).status_code == 200


def test_phase_write_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.idea)
    db.commit()

    assert client.post("/api/v1/roadmap/phases", headers=h, json={"name": "x"}).status_code == 403


def test_patch_phase_rejects_explicit_null_order(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)

    r = client.post("/api/v1/roadmap/phases", headers=h, json={"name": "Extra phase"})
    pid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/phases/{pid}", headers=h, json={"order": None})
    assert r.status_code == 422, r.text
    assert r.status_code != 500


def test_patch_phase_rejects_explicit_null_name(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)

    r = client.post("/api/v1/roadmap/phases", headers=h, json={"name": "Extra phase"})
    pid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/phases/{pid}", headers=h, json={"name": None})
    assert r.status_code == 422, r.text
    assert r.status_code != 500


def test_patch_phase_accepts_null_starts_on(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)

    r = client.post(
        "/api/v1/roadmap/phases",
        headers=h,
        json={"name": "Extra phase", "starts_on": "2026-01-01"},
    )
    pid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/phases/{pid}", headers=h, json={"starts_on": None})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["starts_on"] is None


def test_phase_cross_workspace_404(client, db):
    _ua, _sa, ha = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    _ub, _sb, hb = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, ha)

    r = client.post("/api/v1/roadmap/phases", headers=ha, json={"name": "p"})
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]

    # b tries to patch a's phase
    assert (
        client.patch(f"/api/v1/roadmap/phases/{pid}", headers=hb, json={"name": "z"}).status_code
        == 404
    )
