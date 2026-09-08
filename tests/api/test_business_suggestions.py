from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _two_roles(db, role_a, role_b):
    """One workspace, two members with different roles; returns (startup, headers_a, headers_b)."""
    u_a = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u_a, stage=StartupStage.validation)
    create_membership(db, u_a, s, role=role_a)
    u_b = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u_b, s, role=role_b)
    db.flush()
    ha = {
        "Authorization": f"Bearer {create_access_token(str(u_a.id))}",
        "X-Workspace-Id": str(s.id),
    }
    hb = {
        "Authorization": f"Bearer {create_access_token(str(u_b.id))}",
        "X-Workspace-Id": str(s.id),
    }
    return s, ha, hb


def test_consultant_can_suggest_founder_can_approve(client, db):
    s, h_founder, h_bc = _two_roles(db, MembershipRole.founder, MembershipRole.business_consultant)
    created = client.post(
        "/api/v1/business-builder/suggestions",
        json={
            "op": "canvas_update",
            "target": {"canvas_type": "business_model"},
            "payload": {"blocks": {"key_partners": ["Acme"]}},
            "note": "add Acme",
        },
        headers=h_bc,
    )
    assert created.status_code == 201
    sid = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "pending"

    approved = client.post(f"/api/v1/business-builder/suggestions/{sid}/approve", headers=h_founder)
    assert approved.status_code == 200 and approved.json()["data"]["status"] == "approved"

    canvas = client.get("/api/v1/business-builder/canvases/business_model", headers=h_founder)
    assert canvas.json()["data"]["blocks"]["key_partners"] == ["Acme"]


def test_consultant_cannot_approve_403(client, db):
    s, _h_founder, h_bc = _two_roles(db, MembershipRole.founder, MembershipRole.business_consultant)
    sid = client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "lean"}, "payload": {"blocks": {}}},
        headers=h_bc,
    ).json()["data"]["id"]
    assert (
        client.post(f"/api/v1/business-builder/suggestions/{sid}/approve", headers=h_bc).status_code
        == 403
    )


def test_list_filters_by_status(client, db):
    _u, _s, h = _member(db)
    client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "swot"}, "payload": {"blocks": {}}},
        headers=h,
    )
    r = client.get("/api/v1/business-builder/suggestions?status=pending", headers=h)
    assert r.status_code == 200 and len(r.json()["data"]["suggestions"]) == 1
    assert (
        client.get("/api/v1/business-builder/suggestions?status=approved", headers=h).json()[
            "data"
        ]["suggestions"]
        == []
    )


def test_suggestions_not_shadowed_by_kind_route(client, db):
    """GET /suggestions must hit the suggestions route, not /{kind} (which would 404)."""
    _u, _s, h = _member(db)
    assert client.get("/api/v1/business-builder/suggestions", headers=h).status_code == 200


def test_approve_unknown_id_404(client, db):
    import uuid

    _u, _s, h = _member(db)
    assert (
        client.post(
            f"/api/v1/business-builder/suggestions/{uuid.uuid4()}/approve", headers=h
        ).status_code
        == 404
    )


def test_reject_then_approve_409(client, db):
    _u, _s, h = _member(db)
    sid = client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "lean"}, "payload": {"blocks": {}}},
        headers=h,
    ).json()["data"]["id"]
    assert (
        client.post(f"/api/v1/business-builder/suggestions/{sid}/reject", headers=h).status_code
        == 200
    )
    r = client.post(f"/api/v1/business-builder/suggestions/{sid}/approve", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUGGESTION_NOT_PENDING"


def test_list_bad_status_404(client, db):
    _u, _s, h = _member(db)
    assert (
        client.get("/api/v1/business-builder/suggestions?status=bogus", headers=h).status_code
        == 404
    )


def test_create_malformed_record_id_404(client, db):
    """A syntactically invalid record_id must 404 cleanly, not 500 with a leaked DB error."""
    _u, _s, h = _member(db)
    r = client.post(
        "/api/v1/business-builder/suggestions",
        json={
            "op": "record_update",
            "target": {"kind": "competitor", "record_id": "not-a-uuid"},
            "payload": {"data": {"name": "X"}},
        },
        headers=h,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
