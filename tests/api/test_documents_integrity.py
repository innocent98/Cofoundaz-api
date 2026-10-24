"""Upload integrity, scan gate, file delete vs signature requests, audit fields (rows 5, 6, 25, 37)."""

import io
import zipfile
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.document import DocumentFile
from app.db.models.enums import MembershipRole, ScanStatus, StartupStage
from app.services.documents import scanner
from tests.factories import create_membership, create_startup, create_user

PDF = b"%PDF-1.4 body"


@pytest.fixture
def ws(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return s, h


def _upload(client, h, *, name="a.pdf", ct="application/pdf", body=PDF):
    return client.post(
        "/api/v1/documents/files", files={"file": (name, io.BytesIO(body), ct)}, headers=h
    )


def _sign_req(client, h, fid, email="a@example.com"):
    return client.post(
        f"/api/v1/documents/files/{fid}/signature-requests",
        json={"signers": [{"email": email, "name": "A"}]},
        headers=h,
    )


# ---- row 5: magic bytes -------------------------------------------------------------------


def test_exe_posing_as_pdf_is_refused_and_nothing_is_stored(client, db, ws):
    s, h = ws
    r = _upload(client, h, name="fake.pdf", body=b"MZ")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "don't match" in r.json()["error"]["message"]
    assert db.query(DocumentFile).filter_by(startup_id=s.id).count() == 0


def test_empty_file_is_refused(client, db, ws):
    assert _upload(client, ws[1], body=b"").status_code == 422


def test_png_bytes_declared_as_pdf_is_refused(client, db, ws):
    assert _upload(client, ws[1], body=b"\x89PNG\r\n\x1a\nxx").status_code == 422


def test_real_docx_is_accepted_and_stored_as_docx(client, db, ws):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "x")
        zf.writestr("word/document.xml", "x")
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = _upload(client, ws[1], name="a.docx", ct=ct, body=buf.getvalue())
    assert r.status_code == 201 and r.json()["data"]["content_type"] == ct


@pytest.mark.parametrize("ct", ["text/markdown", "text/x-markdown", "application/octet-stream"])
def test_markdown_is_accepted_row_25(client, db, ws, ct):
    r = _upload(client, ws[1], name="notes.md", ct=ct, body=b"# Title\n")
    assert r.status_code == 201
    assert r.json()["data"]["content_type"] == "text/markdown"


def test_octet_stream_is_only_allowed_for_markdown(client, db, ws):
    assert _upload(client, ws[1], name="a.txt", ct="application/octet-stream").status_code == 422


def test_fifteen_mb_limit_is_unchanged(client, db, ws):
    big = b"%PDF-" + b"x" * (15 * 1024 * 1024)
    r = _upload(client, ws[1], body=big)
    assert r.status_code == 422 and r.json()["error"]["message"] == "File must be 15 MB or smaller."


# ---- row 5: scan status -------------------------------------------------------------------


def test_upload_reports_scan_status_clean_with_the_noop_scanner(client, db, ws):
    r = _upload(client, ws[1])
    assert r.json()["data"]["scan_status"] == "clean"
    got = client.get(f"/api/v1/documents/files/{r.json()['data']['id']}", headers=ws[1])
    assert got.json()["data"]["scan_status"] == "clean"


@pytest.mark.parametrize("status", [ScanStatus.pending, ScanStatus.blocked])
def test_signing_is_blocked_until_clean(client, db, ws, status):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    db.query(DocumentFile).filter_by(id=fid).update({"scan_status": status})
    db.flush()
    r = _sign_req(client, h, fid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_CLEAN"


def test_pending_file_never_reaches_signers_when_scanner_is_async(client, db, ws, monkeypatch):
    class Later:
        def scan(self, content, filename):
            return ScanStatus.pending

    monkeypatch.setattr(scanner, "get_scanner", lambda: Later())
    _s, h = ws
    r = _upload(client, h)
    assert r.status_code == 201 and r.json()["data"]["scan_status"] == "pending"
    assert _sign_req(client, h, r.json()["data"]["id"]).status_code == 409


def test_file_blocked_after_the_request_went_out_stops_the_signer_page(client, db, ws):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    token = _sign_req(client, h, fid).json()["data"]["signer_links"][0].rsplit("/", 1)[-1]
    assert client.get(f"/api/v1/sign/{token}").status_code == 200
    db.query(DocumentFile).filter_by(id=fid).update({"scan_status": ScanStatus.blocked})
    db.flush()
    assert client.get(f"/api/v1/sign/{token}").status_code == 404
    resp = client.post(f"/api/v1/sign/{token}", json={"typed_name": "A"})
    assert resp.status_code == 404


# ---- row 6: delete never cascades ---------------------------------------------------------


def test_delete_file_with_a_signature_request_is_refused_409(client, db, ws):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    rid = _sign_req(client, h, fid).json()["data"]["id"]
    r = client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_HAS_SIGNATURE_REQUESTS"
    assert client.get(f"/api/v1/documents/files/{fid}", headers=h).status_code == 200
    assert client.get(f"/api/v1/documents/signature-requests/{rid}", headers=h).status_code == 200


def test_completed_request_survives_a_delete_attempt(client, db, ws):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    created = _sign_req(client, h, fid).json()["data"]
    token = created["signer_links"][0].rsplit("/", 1)[-1]
    signed = client.post(f"/api/v1/sign/{token}", json={"typed_name": "A"})
    assert signed.status_code == 200
    refused = client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert refused.status_code == 409
    got = client.get(f"/api/v1/documents/signature-requests/{created['id']}", headers=h)
    assert got.json()["data"]["status"] == "complete"
    assert (
        client.get("/api/v1/documents/signature-requests", headers=h).json()["data"]["requests"][0][
            "id"
        ]
        == created["id"]
    )


def test_cancelled_request_still_blocks_delete(client, db, ws):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    rid = _sign_req(client, h, fid).json()["data"]["id"]
    client.post(f"/api/v1/documents/signature-requests/{rid}/cancel", headers=h)
    refused = client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert refused.status_code == 409


def test_file_without_requests_still_deletes(client, db, ws):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    deleted = client.delete(f"/api/v1/documents/files/{fid}", headers=h)
    assert deleted.status_code == 200
    gone = client.get(f"/api/v1/documents/files/{fid}", headers=h)
    assert gone.status_code == 404


# ---- row 37: audit fields -----------------------------------------------------------------


def test_signer_ip_and_user_agent_reach_editors_only(client, db, ws):
    _s, h = ws
    fid = _upload(client, h).json()["data"]["id"]
    created = _sign_req(client, h, fid).json()["data"]
    token = created["signer_links"][0].rsplit("/", 1)[-1]
    assert created["signers"][0]["signed_ip"] is None
    signed = client.post(
        f"/api/v1/sign/{token}", json={"typed_name": "A"}, headers={"user-agent": "pytest-ua"}
    )
    assert "signers" not in signed.json()["data"]  # the public signer never sees the roster
    for url in (
        f"/api/v1/documents/signature-requests/{created['id']}",
        "/api/v1/documents/signature-requests",
    ):
        body = client.get(url, headers=h).json()["data"]
        signer = (body if "signers" in body else body["requests"][0])["signers"][0]
        assert signer["signed_user_agent"] == "pytest-ua" and signer["signed_ip"]
    assert "signed_ip" not in signed.text and "pytest-ua" not in signed.text
