from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.document import DocumentFile
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    f = DocumentFile(
        startup_id=s.id,
        uploaded_by_id=u.id,
        filename="nda.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key=f"documents/{s.id}/x.pdf",
        url="file:///x.pdf",
    )
    db.add(f)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, f, h


def test_create_returns_signer_links_and_public_sign_completes(client, db):
    _u, _s, f, h = _member(db)
    created = client.post(
        f"/api/v1/documents/files/{f.id}/signature-requests",
        json={"signers": [{"email": "a@x.com", "name": "A"}, {"email": "b@x.com"}]},
        headers=h,
    )
    assert created.status_code == 201
    links = created.json()["data"]["signer_links"]  # raw links, returned once
    assert len(links) == 2
    tokens = [link.rsplit("/", 1)[-1] for link in links]

    # public view — no auth
    view = client.get(f"/api/v1/sign/{tokens[0]}")
    assert view.status_code == 200
    assert view.json()["data"]["file"]["filename"] == "nda.pdf"

    # first signer signs -> still awaiting
    r1 = client.post(f"/api/v1/sign/{tokens[0]}", json={"typed_name": "Alice"})
    assert r1.status_code == 200 and r1.json()["data"]["status"] == "awaiting"
    # second signs -> complete
    r2 = client.post(f"/api/v1/sign/{tokens[1]}", json={"typed_name": "Bob"})
    assert r2.status_code == 200 and r2.json()["data"]["status"] == "complete"
    # signing again -> 404
    assert client.post(f"/api/v1/sign/{tokens[0]}", json={"typed_name": "Alice"}).status_code == 404


def test_create_requires_signer_422(client, db):
    _u, _s, f, h = _member(db)
    r = client.post(
        f"/api/v1/documents/files/{f.id}/signature-requests", json={"signers": []}, headers=h
    )
    assert r.status_code == 422


def test_list_and_cancel(client, db):
    _u, _s, f, h = _member(db)
    rid = client.post(
        f"/api/v1/documents/files/{f.id}/signature-requests",
        json={"signers": [{"email": "a@x.com"}]},
        headers=h,
    ).json()["data"]["id"]
    lst = client.get("/api/v1/documents/signature-requests", headers=h)
    assert lst.status_code == 200 and len(lst.json()["data"]["requests"]) == 1
    assert (
        client.post(f"/api/v1/documents/signature-requests/{rid}/cancel", headers=h).status_code
        == 200
    )


def test_remind_rotates_and_new_link_works(client, db):
    _u, _s, f, h = _member(db)
    created = client.post(
        f"/api/v1/documents/files/{f.id}/signature-requests",
        json={"signers": [{"email": "a@x.com"}]},
        headers=h,
    ).json()["data"]
    rid, old_token = created["id"], created["signer_links"][0].rsplit("/", 1)[-1]
    r = client.post(f"/api/v1/documents/signature-requests/{rid}/remind", headers=h)
    assert r.status_code == 200 and r.json()["data"]["reminded"] == 1
    # old link is now dead (token rotated); we can't see the new raw link here (emailed),
    # so assert the old one 404s — the e2e journey exercises the fresh link end to end.
    assert client.get(f"/api/v1/sign/{old_token}").status_code == 404


def test_mentor_cannot_create_403(client, db):
    _u, _s, f, h = _member(db, role=MembershipRole.mentor)
    r = client.post(
        f"/api/v1/documents/files/{f.id}/signature-requests",
        json={"signers": [{"email": "a@x.com"}]},
        headers=h,
    )
    assert r.status_code == 403


def test_unknown_sign_token_404(client, db):
    assert client.get("/api/v1/sign/not-a-token").status_code == 404


def test_signature_requests_not_shadowed_by_document_id(client, db):
    _u, _s, _f, h = _member(db)
    assert client.get("/api/v1/documents/signature-requests", headers=h).status_code == 200


def test_cross_tenant_cancel_404(client, db):
    _ua, _sa, _fa, ha = _member(db)
    _ub, _sb, fb, hb = _member(db)
    rid = client.post(
        f"/api/v1/documents/files/{fb.id}/signature-requests",
        json={"signers": [{"email": "a@x.com"}]},
        headers=hb,
    ).json()["data"]["id"]
    assert (
        client.post(f"/api/v1/documents/signature-requests/{rid}/cancel", headers=ha).status_code
        == 404
    )
