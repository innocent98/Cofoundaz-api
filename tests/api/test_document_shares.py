from datetime import UTC, datetime

from app.core.config import settings
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


def test_share_link_uses_app_base_url_frontend_origin(client, db, monkeypatch):
    """The emailed/returned share link must point at the FRONTEND origin
    (`APP_BASE_URL`) so the recipient lands on the FE `/shared/{token}` page —
    not the API origin. Mirrors the auth-email base pattern (see
    tests/services/auth/test_emails.py)."""
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    _u, _s, doc, h = _member(db)
    r = client.post(
        f"/api/v1/documents/{doc.id}/shares",
        json={"email": "tayo@lawfirm.ng", "access_level": "view"},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["data"]["link"].startswith("https://app.example.test/shared/")


def test_share_link_falls_back_to_server_host_when_app_base_url_empty(client, db, monkeypatch):
    """With no FE origin configured, the link falls back to `SERVER_HOST` and
    trims any trailing slash (no `//shared`)."""
    monkeypatch.setattr(settings, "APP_BASE_URL", "")
    monkeypatch.setattr(settings, "SERVER_HOST", "https://api.example.test/")
    _u, _s, doc, h = _member(db)
    link = client.post(
        f"/api/v1/documents/{doc.id}/shares",
        json={"email": "a@y.com"},
        headers=h,
    ).json()["data"]["link"]
    assert link.startswith("https://api.example.test/shared/")
    assert "https://api.example.test//shared" not in link


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


def test_share_create_survives_email_failure(client, db, monkeypatch):
    """A flaky mail backend must not 500 share creation — the share is still
    created and the link still returned (best-effort notification)."""
    import app.api.v1.endpoints.documents as documents_ep

    _u, _s, doc, h = _member(db)

    class _BoomSender:
        def send(self, msg):
            raise RuntimeError("smtp/resend down")

    monkeypatch.setattr(documents_ep, "get_email_sender", lambda: _BoomSender())
    r = client.post(
        f"/api/v1/documents/{doc.id}/shares",
        json={"email": "tayo@lawfirm.ng", "access_level": "view"},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["data"]["link"].endswith(r.json()["data"]["link"].rsplit("/", 1)[-1])
    # the share persisted despite the email failure
    assert (
        len(client.get(f"/api/v1/documents/{doc.id}/shares", headers=h).json()["data"]["shares"])
        == 1
    )
