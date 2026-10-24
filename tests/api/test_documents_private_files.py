"""Security review of #185: private storage, signed links, public-route limits, delete race."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.db.models.document import DocumentFile, SignatureRequest
from app.db.models.enums import MembershipRole, ScanStatus, StartupStage
from app.db.models.membership import Membership
from app.main import app
from app.services.documents import files as files_svc
from tests.factories import create_membership, create_startup, create_user

PDF = b"%PDF-1.4 secret body"


@pytest.fixture
def ws(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "http://api.test")
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    m = create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return s, m, h


def _upload(client, h, body=PDF):
    r = client.post(
        "/api/v1/documents/files",
        files={"file": ("nda.pdf", io.BytesIO(body), "application/pdf")},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _request(client, h, fid):
    r = client.post(
        f"/api/v1/documents/files/{fid}/signature-requests",
        json={"signers": [{"email": "s@example.com", "name": "S"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"], r.json()["data"]["signer_links"][0].rsplit("/", 1)[-1]


def _path(url: str) -> str:
    return url.replace("http://api.test", "")


# ---- 1. private files, signed links ------------------------------------------------------------


def test_upload_is_private_and_exposes_no_url(client, db, ws):
    _s, _m, h = ws
    r = client.post(
        "/api/v1/documents/files",
        files={"file": ("nda.pdf", io.BytesIO(PDF), "application/pdf")},
        headers=h,
    )
    assert "url" not in r.json()["data"]
    row = db.query(DocumentFile).one()
    assert row.is_private and row.url is None
    for url in (f"/api/v1/documents/files/{row.id}", "/api/v1/documents/files"):
        assert "url" not in client.get(url, headers=h).text.replace("download_url", "")


def test_editor_gets_a_short_lived_link_that_serves_the_bytes(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    link = client.get(f"/api/v1/documents/files/{fid}/download", headers=h).json()["data"]
    assert link["content_type"] == "application/pdf" and link["filename"] == "nda.pdf"
    assert datetime.fromisoformat(link["expires_at"]) - datetime.now(UTC) <= timedelta(minutes=5)
    got = client.get(_path(link["url"]))  # no auth header: the signed token is the credential
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-disposition"].startswith("attachment")
    assert got.headers["x-content-type-options"] == "nosniff"


def test_tampered_or_expired_link_is_404(client, db, ws, monkeypatch):
    _s, _m, h = ws
    fid = _upload(client, h)
    url = _path(
        client.get(f"/api/v1/documents/files/{fid}/download", headers=h).json()["data"]["url"]
    )
    assert client.get(url + "x").status_code == 404
    real = files_svc.sign_key
    monkeypatch.setattr(files_svc, "sign_key", lambda *a, **k: real(*a, **{**k, "ttl_seconds": -5}))
    stale = _path(
        client.get(f"/api/v1/documents/files/{fid}/download", headers=h).json()["data"]["url"]
    )
    assert client.get(stale).status_code == 404


def test_role_change_stops_new_links_and_other_roles_get_403(client, db, ws):
    s, m, h = ws
    fid = _upload(client, h)
    assert client.get(f"/api/v1/documents/files/{fid}/download", headers=h).status_code == 200
    db.query(Membership).filter_by(id=m.id).update({"role": MembershipRole.mentor})
    db.flush()
    r = client.get(f"/api/v1/documents/files/{fid}/download", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_unclean_file_issues_no_link_and_kills_an_issued_one(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    url = _path(
        client.get(f"/api/v1/documents/files/{fid}/download", headers=h).json()["data"]["url"]
    )
    db.query(DocumentFile).update({"scan_status": ScanStatus.blocked})
    db.flush()
    r = client.get(f"/api/v1/documents/files/{fid}/download", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_CLEAN"
    assert client.get(url).status_code == 404


def test_deleted_file_link_is_404(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    url = _path(
        client.get(f"/api/v1/documents/files/{fid}/download", headers=h).json()["data"]["url"]
    )
    resp = client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert resp.status_code == 200
    assert client.get(url).status_code == 404


def test_cross_tenant_link_request_is_404(client, db, ws, tmp_path):
    _s, _m, h = ws
    fid = _upload(client, h)
    other = create_user(db, email_verified_at=datetime.now(UTC))
    s2 = create_startup(db, owner=other, stage=StartupStage.validation)
    create_membership(db, other, s2, role=MembershipRole.founder)
    db.flush()
    h2 = {
        "Authorization": f"Bearer {create_access_token(str(other.id))}",
        "X-Workspace-Id": str(s2.id),
    }
    assert client.get(f"/api/v1/documents/files/{fid}/download", headers=h2).status_code == 404


class _CloudStorage:
    """Cloudinary-shaped: private objects with provider-signed URLs; nothing served locally."""

    def __init__(self):
        self.saved, self.deleted, self.made_private = {}, [], []

    def save_private(self, key, content, ctype):
        self.saved[key] = content

    def delete_private(self, key):
        self.deleted.append(key)

    def delete(self, key):
        self.deleted.append(key)

    def private_url(self, key, ttl):
        return f"https://cdn.example/signed/{key}?exp={ttl}"

    def make_private(self, key):
        self.made_private.append(key)


def test_remote_backend_returns_provider_link_and_signers_are_redirected(
    client, db, ws, monkeypatch
):
    _s, _m, h = ws
    fake = _CloudStorage()
    monkeypatch.setattr(files_svc, "get_storage", lambda: fake)
    from app.api.v1.endpoints import documents as ep

    monkeypatch.setattr(ep, "get_storage", lambda: fake)
    fid = _upload(client, h)
    link = client.get(f"/api/v1/documents/files/{fid}/download", headers=h).json()["data"]
    assert link["url"].startswith("https://cdn.example/signed/documents/")
    _rid, token = _request(client, h, fid)
    r = client.get(f"/api/v1/sign/{token}/file", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("https://cdn.example/signed/")


# ---- signer access only while the token is active ---------------------------------------------


def test_signer_file_link_works_only_while_the_token_is_active(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    rid, token = _request(client, h, fid)
    view = client.get(f"/api/v1/sign/{token}").json()["data"]
    assert set(view["file"]) == {"filename", "content_type", "size_bytes", "download_url"}
    assert view["file"]["download_url"] == f"http://api.test/api/v1/sign/{token}/file"
    got = client.get(f"/api/v1/sign/{token}/file")
    assert got.status_code == 200 and got.content == PDF
    client.post(f"/api/v1/documents/signature-requests/{rid}/cancel", headers=h)
    assert client.get(f"/api/v1/sign/{token}/file").status_code == 404  # cancelled


def test_signer_file_link_dies_on_expiry_signing_and_block(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    rid, token = _request(client, h, fid)
    db.query(SignatureRequest).update({"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    db.flush()
    assert client.get(f"/api/v1/sign/{token}/file").status_code == 404  # expired
    db.query(SignatureRequest).update({"expires_at": None})
    db.flush()
    assert client.get(f"/api/v1/sign/{token}/file").status_code == 200
    db.query(DocumentFile).update({"scan_status": ScanStatus.blocked})
    db.flush()
    assert client.get(f"/api/v1/sign/{token}/file").status_code == 404  # blocked
    db.query(DocumentFile).update({"scan_status": ScanStatus.clean})
    db.flush()
    resp = client.post(f"/api/v1/sign/{token}", json={"typed_name": "S"})
    assert resp.status_code == 200
    assert client.get(f"/api/v1/sign/{token}/file").status_code == 404  # signed
    assert client.get("/api/v1/sign/nope/file").status_code == 404


def test_legacy_public_file_is_moved_private_when_first_served(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    db.query(DocumentFile).update({"is_private": False, "url": "https://public.example/x.pdf"})
    db.flush()
    assert client.get(f"/api/v1/documents/files/{fid}/download", headers=h).status_code == 200
    row = db.query(DocumentFile).one()
    assert row.is_private and row.url is None


# ---- public responses are trimmed -------------------------------------------------------------


def test_public_share_response_has_no_internal_id_or_folder(client, db, ws):
    _s, _m, h = ws
    doc = client.post(
        "/api/v1/documents", json={"title": "Plan", "folder": "Legal"}, headers=h
    ).json()["data"]
    assert doc["folder"] == "Legal" and doc["id"]
    share = client.post(
        f"/api/v1/documents/{doc['id']}/shares",
        json={"email": "r@example.com", "access_level": "view"},
        headers=h,
    ).json()["data"]
    token = share["link"].rsplit("/", 1)[-1]
    body = client.get(f"/api/v1/shared/{token}").json()["data"]["document"]
    assert "id" not in body and "folder" not in body
    assert body["title"] == "Plan" and "sections" in body


# ---- 429 on the public routes -----------------------------------------------------------------


@pytest.fixture
def limited():
    app.state.limiter.enabled = True
    limiter.reset()
    yield
    app.state.limiter.enabled = False
    limiter.reset()


def test_public_reads_are_limited_across_tokens(client, db, limited):
    codes = [client.get(f"/api/v1/shared/tok{i}").status_code for i in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429  # one bucket, however many tokens
    limiter.reset()
    codes = [client.get(f"/api/v1/sign/tok{i}").status_code for i in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429


def test_signer_file_route_shares_the_sign_read_bucket(client, db, limited):
    codes = [client.get(f"/api/v1/sign/tok{i}/file").status_code for i in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429


def test_signing_posts_are_limited_tighter(client, db, limited):
    codes = [
        client.post(f"/api/v1/sign/tok{i}", json={"typed_name": "A"}).status_code for i in range(11)
    ]
    assert codes[:10] == [404] * 10 and codes[10] == 429


def test_signed_link_download_is_limited(client, db, limited):
    codes = [
        client.get(f"/api/v1/documents/files/download?token=t{i}").status_code for i in range(31)
    ]
    assert codes[:30] == [404] * 30 and codes[30] == 429


# ---- delete race ------------------------------------------------------------------------------


def _statements(db):
    seen: list[str] = []

    def grab(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", grab)
    return seen, lambda: event.remove(db.get_bind(), "before_cursor_execute", grab)


def test_delete_and_request_create_lock_the_file_row(client, db, ws):
    _s, _m, h = ws
    fid = _upload(client, h)
    seen, stop = _statements(db)
    try:
        _request(client, h, fid)
        locked_on_create = [s for s in seen if "FROM document_files" in s and "FOR UPDATE" in s]
        seen.clear()
        other = _upload(client, h)
        seen.clear()
        resp = client.delete(f"/api/v1/documents/files/{other}", headers=h)
        assert resp.status_code == 200
        locked_on_delete = [s for s in seen if "FROM document_files" in s and "FOR UPDATE" in s]
    finally:
        stop()
    assert locked_on_create and locked_on_delete


def test_stored_object_is_removed_only_after_the_commit(client, db, ws, monkeypatch):
    _s, _m, h = ws
    fid = _upload(client, h)
    order: list[str] = []
    from app.api.v1.endpoints import documents as ep

    real_commit = type(db).commit
    monkeypatch.setattr(
        type(db), "commit", lambda self: (order.append("commit"), real_commit(self))[1]
    )
    monkeypatch.setattr(ep, "purge_object", lambda target: order.append("purge"))
    resp = client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert resp.status_code == 200
    assert order == ["commit", "purge"]


def test_failed_commit_keeps_the_stored_object(client, db, ws, tmp_path, monkeypatch):
    _s, _m, h = ws
    fid = _upload(client, h)
    stored = next(Path(tmp_path).rglob("*.pdf"))
    monkeypatch.setattr(
        type(db), "commit", lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert stored.exists()


def test_purge_failure_is_logged_not_raised(monkeypatch):
    class Broken:
        def delete_private(self, key):
            raise OSError("gone")

    monkeypatch.setattr(files_svc, "get_storage", lambda: Broken())
    files_svc.purge_object(("documents/x/y.pdf", True))  # must not raise


def test_dumped_file_json_never_carries_a_storage_location(client, db, ws):
    _s, _m, h = ws
    _upload(client, h)
    text = json.dumps(client.get("/api/v1/documents/files", headers=h).json())
    assert "storage_key" not in text and "var/storage" not in text and "documents/" not in text
