import uuid
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    return (
        u,
        s,
        {
            "Authorization": f"Bearer {create_access_token(str(u.id))}",
            "X-Workspace-Id": str(s.id),
        },
    )


def test_list_empty(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/documents", headers=h)
    assert r.status_code == 200 and r.json()["data"]["documents"] == []


def test_templates_catalog(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/document-templates", headers=h)
    assert r.status_code == 200
    keys = {t["key"] for t in r.json()["data"]["templates"]}
    assert "business_plan" in keys and "one_pager" in keys
    assert client.get("/api/v1/document-templates/nope", headers=h).status_code == 404


def test_create_from_template(client, db):
    _u, _s, h = _member(db)
    r = client.post("/api/v1/documents", json={"template_key": "business_plan"}, headers=h)
    assert r.status_code == 201
    doc = r.json()["data"]
    assert doc["kind"] == "business_plan" and doc["title"] == "Business Plan"
    assert doc["sections"][0]["heading"] == "Executive Summary" and doc["version"] == 1


def test_create_blank_and_bad_sections_422(client, db):
    _u, _s, h = _member(db)
    ok = client.post("/api/v1/documents", json={"title": "Blank"}, headers=h)
    assert ok.status_code == 201 and ok.json()["data"]["kind"] == "custom"
    bad = client.post(
        "/api/v1/documents",
        json={"sections": [{"heading": 1, "body": "x"}]},
        headers=h,
    )
    assert bad.status_code == 422


def test_get_edit_version_conflict_delete(client, db):
    _u, _s, h = _member(db)
    doc_id = client.post("/api/v1/documents", json={"template_key": "one_pager"}, headers=h).json()[
        "data"
    ]["id"]

    # full-replace edit
    edit = client.put(
        f"/api/v1/documents/{doc_id}",
        json={
            "title": "Edited",
            "sections": [{"heading": "H", "body": "B"}],
            "status": "final",
            "folder": "Pitches",
            "version": 1,
        },
        headers=h,
    )
    assert edit.status_code == 200 and edit.json()["data"]["version"] == 2
    assert edit.json()["data"]["status"] == "final"

    # stale version -> 409
    stale = client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "x", "sections": [], "status": "draft", "version": 1},
        headers=h,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "DOCUMENT_VERSION_CONFLICT"

    # the stale write must not have partially mutated the row
    after_stale = client.get(f"/api/v1/documents/{doc_id}", headers=h).json()["data"]
    assert after_stale["title"] == "Edited"
    assert after_stale["version"] == 2
    assert after_stale["status"] == "final"

    # filter by folder
    lst = client.get("/api/v1/documents?folder=Pitches", headers=h)
    assert len(lst.json()["data"]["documents"]) == 1
    assert "sections" not in lst.json()["data"]["documents"][0]  # summary only

    assert client.delete(f"/api/v1/documents/{doc_id}", headers=h).status_code == 200
    assert client.get(f"/api/v1/documents/{doc_id}", headers=h).status_code == 404


def test_bad_kind_filter_404(client, db):
    _u, _s, h = _member(db)
    assert client.get("/api/v1/documents?kind=bogus", headers=h).status_code == 404


def test_mentor_cannot_write_403(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor)
    assert client.post("/api/v1/documents", json={"title": "x"}, headers=h).status_code == 403
    # but a member can read
    assert client.get("/api/v1/documents", headers=h).status_code == 200


def test_cross_tenant_get_404(client, db):
    _u, _s, h = _member(db)
    assert client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=h).status_code == 404


def test_cross_tenant_get_real_document_404(client, db):
    # A document that genuinely exists, but under a different startup: must 404,
    # not leak via a lookup that only checks the id.
    _u1, _s1, h1 = _member(db)
    _u2, _s2, h2 = _member(db)

    other_doc_id = client.post(
        "/api/v1/documents", json={"template_key": "one_pager"}, headers=h2
    ).json()["data"]["id"]

    assert client.get(f"/api/v1/documents/{other_doc_id}", headers=h1).status_code == 404
