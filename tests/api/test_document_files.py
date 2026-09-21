import io
from datetime import UTC, datetime

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, tmp_path, monkeypatch, *, role=MembershipRole.founder):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _upload(
    client, headers, *, name="nda.pdf", ct="application/pdf", body=b"%PDF-1.4", folder=None
):
    data = {"folder": folder} if folder is not None else {}
    return client.post(
        "/api/v1/documents/files",
        files={"file": (name, io.BytesIO(body), ct)},
        data=data,
        headers=headers,
    )


def test_upload_then_get_and_list(client, db, tmp_path, monkeypatch):
    _u, _s, h = _member(db, tmp_path, monkeypatch)
    r = _upload(client, h, folder="Legal")
    assert r.status_code == 201
    fid = r.json()["data"]["id"]
    assert r.json()["data"]["filename"] == "nda.pdf" and r.json()["data"]["url"]
    got = client.get(f"/api/v1/documents/files/{fid}", headers=h)
    assert got.status_code == 200 and got.json()["data"]["size_bytes"] == 8
    lst = client.get("/api/v1/documents/files?folder=Legal", headers=h)
    assert len(lst.json()["data"]["files"]) == 1


def test_upload_bad_type_422(client, db, tmp_path, monkeypatch):
    _u, _s, h = _member(db, tmp_path, monkeypatch)
    r = _upload(client, h, name="x.exe", ct="application/x-msdownload", body=b"MZ")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_upload_oversize_422(client, db, tmp_path, monkeypatch):
    _u, _s, h = _member(db, tmp_path, monkeypatch)
    big = b"x" * (15 * 1024 * 1024 + 1)
    r = _upload(client, h, name="big.pdf", body=big)
    assert r.status_code == 422


def test_mentor_cannot_upload_403(client, db, tmp_path, monkeypatch):
    _u, _s, h = _member(db, tmp_path, monkeypatch, role=MembershipRole.mentor)
    assert _upload(client, h).status_code == 403


def test_delete_removes(client, db, tmp_path, monkeypatch):
    _u, _s, h = _member(db, tmp_path, monkeypatch)
    fid = _upload(client, h).json()["data"]["id"]
    assert client.delete(f"/api/v1/documents/files/{fid}", headers=h).status_code == 200
    assert client.get(f"/api/v1/documents/files/{fid}", headers=h).status_code == 404


def test_cross_tenant_get_real_file_404(client, db, tmp_path, monkeypatch):
    _ua, _sa, ha = _member(db, tmp_path, monkeypatch)
    _ub, _sb, hb = _member(db, tmp_path, monkeypatch)
    fid = _upload(client, hb).json()["data"]["id"]  # owned by startup B
    assert client.get(f"/api/v1/documents/files/{fid}", headers=ha).status_code == 404


def test_files_route_not_shadowed_by_document_id(client, db, tmp_path, monkeypatch):
    """GET /documents/files must hit the files list, not /documents/{document_id}."""
    _u, _s, h = _member(db, tmp_path, monkeypatch)
    assert client.get("/api/v1/documents/files", headers=h).status_code == 200
