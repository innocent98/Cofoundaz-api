from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.document import Document
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    doc = Document(startup_id=s.id, created_by_id=u.id, title="Plan")
    db.add(doc)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, doc, h


def test_share_create_returns_link_and_public_open_works(client, db):
    _u, _s, doc, h = _member(db)
    r = client.post(
        f"/api/v1/documents/{doc.id}/shares",
        json={"email": "tayo@lawfirm.ng", "access_level": "view"},
        headers=h,
    )
    assert r.status_code == 201
    link = r.json()["data"]["link"]
    token = link.rsplit("/", 1)[-1]
    # public open — NO auth headers at all
    opened = client.get(f"/api/v1/shared/{token}")
    assert opened.status_code == 200
    assert opened.json()["data"]["document"]["title"] == "Plan"
    assert opened.json()["data"]["access_level"] == "view"


def test_list_and_revoke_then_open_404(client, db):
    _u, _s, doc, h = _member(db)
    link = client.post(
        f"/api/v1/documents/{doc.id}/shares",
        json={"email": "a@y.com"},
        headers=h,
    ).json()["data"]["link"]
    token = link.rsplit("/", 1)[-1]
    sid = client.get(f"/api/v1/documents/{doc.id}/shares", headers=h).json()["data"]["shares"][0][
        "id"
    ]
    assert client.delete(f"/api/v1/documents/{doc.id}/shares/{sid}", headers=h).status_code == 200
    assert client.get(f"/api/v1/shared/{token}").status_code == 404


def test_unknown_token_404(client, db):
    assert client.get("/api/v1/shared/not-a-real-token").status_code == 404


def test_mentor_cannot_share_403(client, db):
    _u, _s, doc, h = _member(db, role=MembershipRole.mentor)
    r = client.post(f"/api/v1/documents/{doc.id}/shares", json={"email": "a@y.com"}, headers=h)
    assert r.status_code == 403


def test_workspace_overview_lists_shares(client, db):
    _u, _s, doc, h = _member(db)
    client.post(f"/api/v1/documents/{doc.id}/shares", json={"email": "a@y.com"}, headers=h)
    r = client.get("/api/v1/documents/shares", headers=h)
    assert r.status_code == 200 and len(r.json()["data"]["shares"]) == 1
    assert r.json()["data"]["shares"][0]["document_id"] == str(doc.id)


def test_shares_overview_not_shadowed_by_document_id(client, db):
    _u, _s, _doc, h = _member(db)
    assert client.get("/api/v1/documents/shares", headers=h).status_code == 200


def test_cross_tenant_revoke_404(client, db):
    _ua, _sa, _doca, ha = _member(db)
    _ub, _sb, docb, hb = _member(db)
    client.post(f"/api/v1/documents/{docb.id}/shares", json={"email": "a@y.com"}, headers=hb)
    # startup A tries to revoke startup B's share -> 404
    share_id = client.get(f"/api/v1/documents/{docb.id}/shares", headers=hb).json()["data"][
        "shares"
    ][0]["id"]
    assert (
        client.delete(f"/api/v1/documents/{docb.id}/shares/{share_id}", headers=ha).status_code
        == 404
    )
