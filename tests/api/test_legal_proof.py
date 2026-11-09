import io
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.db.models.legal import ComplianceItem
from app.services.legal import compliance as compliance_svc
from tests.api.test_legal import BASE, FORBIDDEN_ROLES, _member


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))


def _make(client, h, **kw):
    body = {"title": "VAT filing", "authority": "FIRS", "due_on": "2026-11-30"}
    body.update(kw)
    resp = client.post(f"{BASE}/compliance-items", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


# Real magic bytes, not lookalikes. The service classifies by these and ignores the declared
# Content-Type, so a body that merely starts with the letters "PNG" is now correctly refused.
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
PDF = b"%PDF-"


def _upload(client, h, item_id, *, name="p.png", ct="image/png", body=PNG + b"png-bytes"):
    return client.post(
        f"{BASE}/compliance-items/{item_id}/proof",
        files={"file": (name, io.BytesIO(body), ct)},
        headers=h,
    )


def _files(tmp_path):
    return [p for p in Path(tmp_path).rglob("*") if p.is_file()]


def test_upload_png_stores_the_file_and_sets_the_url(client, db, tmp_path):
    _u, _s, h = _member(db)
    cid = _make(client, h)
    resp = _upload(client, h, cid)
    assert resp.status_code == 200, resp.text
    # The API route, never the storage address: the file is saved privately and the column stays
    # NULL, so a filesystem path (local) or a public URL (Cloudinary) can never reach the FE.
    assert resp.json()["data"]["proof_url"] == f"{BASE}/compliance-items/{cid}/proof"
    row = db.query(ComplianceItem).filter_by(id=cid).one()
    assert row.proof_url is None
    assert row.proof_key
    stored = _files(tmp_path)
    assert len(stored) == 1
    assert stored[0].suffix == ".png"
    assert stored[0].read_bytes() == PNG + b"png-bytes"
    # Keyed under its own prefix, not Finance's receipts/.
    assert "legal-proofs" in str(stored[0])


def test_upload_pdf_and_jpeg_accepted(client, db, tmp_path):
    _u, _s, h = _member(db)
    cid = _make(client, h)
    pdf = _upload(client, h, cid, name="p.pdf", ct="application/pdf", body=PDF + b"1.4")
    assert pdf.status_code == 200, pdf.text
    jpg = _upload(client, h, cid, name="p.jpg", ct="image/jpeg", body=JPEG + b"jpeg")
    assert jpg.status_code == 200, jpg.text
    # The second replaced the first, so only one file remains.
    assert [p.suffix for p in _files(tmp_path)] == [".jpg"]


def test_bytes_that_match_no_allowed_type_are_422_and_store_nothing(client, db, tmp_path):
    _u, s, h = _member(db)
    cid = _make(client, h)
    resp = _upload(client, h, cid, name="p.txt", ct="text/plain", body=b"hi")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    row = db.query(ComplianceItem).filter_by(startup_id=s.id).one()
    db.refresh(row)
    assert row.proof_url is None and row.proof_key is None
    assert _files(tmp_path) == []


def test_html_labelled_image_png_is_422(client, db, tmp_path):
    _u, s, h = _member(db)
    cid = _make(client, h)
    # The decisive one, mirroring Finance's test of the same name. The filename and the declared
    # type both say PNG and only the BYTES disagree, so this is the single test that fails if the
    # magic-byte sniff is ever removed - the one above would still pass on its declared type.
    resp = _upload(client, h, cid, name="p.png", ct="image/png", body=b"<html>nope</html>")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    row = db.query(ComplianceItem).filter_by(startup_id=s.id).one()
    db.refresh(row)
    assert row.proof_key is None
    assert _files(tmp_path) == []


def test_an_oversized_file_is_422_and_stores_nothing(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(compliance_svc, "MAX_PROOF_BYTES", 8)
    _u, s, h = _member(db)
    cid = _make(client, h)
    # Valid magic, over the cap: the size check runs BEFORE the sniff, so this proves the cap on
    # its own rather than passing for the wrong reason (mirrors Finance's oversize test).
    resp = _upload(client, h, cid, body=PNG + b"xx")
    assert resp.status_code == 422, resp.text
    row = db.query(ComplianceItem).filter_by(startup_id=s.id).one()
    db.refresh(row)
    assert row.proof_key is None
    assert _files(tmp_path) == []


def test_proof_url_is_null_without_a_proof(client, db):
    _u, _s, h = _member(db)
    cid = _make(client, h)
    resp = client.get(f"{BASE}/compliance-items/{cid}", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["proof_url"] is None


def test_replacing_a_proof_deletes_the_old_file(client, db, tmp_path):
    _u, s, h = _member(db)
    cid = _make(client, h)
    assert _upload(client, h, cid, body=PNG + b"first").status_code == 200
    row = db.query(ComplianceItem).filter_by(startup_id=s.id).one()
    db.refresh(row)
    old_key = row.proof_key
    assert old_key and (Path(tmp_path) / old_key).is_file()

    assert (
        _upload(
            client, h, cid, name="p.pdf", ct="application/pdf", body=PDF + b"second"
        ).status_code
        == 200
    )
    db.refresh(row)
    assert row.proof_key != old_key
    # The new asset is saved BEFORE the old key is dropped, so a failed upload never orphans
    # the row - and the old file is gone afterwards.
    assert not (Path(tmp_path) / old_key).exists()
    assert (Path(tmp_path) / row.proof_key).is_file()
    assert len(_files(tmp_path)) == 1


def test_deleting_the_item_removes_its_proof_file(client, db, tmp_path):
    _u, _s, h = _member(db)
    cid = _make(client, h)
    assert _upload(client, h, cid).status_code == 200
    assert len(_files(tmp_path)) == 1
    assert client.delete(f"{BASE}/compliance-items/{cid}", headers=h).status_code == 200
    # Mirrors delete_expense: the stored asset goes with its row rather than being orphaned.
    assert _files(tmp_path) == []


def test_marking_done_works_with_no_proof_at_all(client, db, tmp_path):
    _u, _s, h = _member(db)
    cid = _make(client, h)
    # Proof is OPTIONAL per the PRD: mark-done must not require one.
    done = client.patch(f"{BASE}/compliance-items/{cid}", json={"status": "done"}, headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["data"]["status"] == "done"
    assert done.json()["data"]["proof_url"] is None
    assert _files(tmp_path) == []


def test_a_legal_advisor_cannot_upload_a_proof(client, db, tmp_path):
    _f, startup, fh = _member(db)
    cid = _make(client, fh)
    _u, _s, h = _member(db, role=MembershipRole.legal_advisor, startup=startup)
    # Read-only on every surface, the upload included (spec D1).
    assert _upload(client, h, cid).status_code == 403
    assert _files(tmp_path) == []


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_upload_a_proof(client, db, role, tmp_path):
    _f, startup, fh = _member(db)
    cid = _make(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert _upload(client, h, cid).status_code == 403
    assert _files(tmp_path) == []


def test_uploading_to_another_workspaces_item_is_404(client, db, tmp_path):
    _u1, _s1, h1 = _member(db)
    cid = _make(client, h1)
    _u2, _s2, h2 = _member(db)
    assert _upload(client, h2, cid).status_code == 404
    assert _files(tmp_path) == []


def test_the_upload_requires_authentication(client):
    resp = client.post(
        f"{BASE}/compliance-items/00000000-0000-0000-0000-000000000000/proof",
        files={"file": ("p.png", io.BytesIO(b"x"), "image/png")},
    )
    assert resp.status_code in (401, 403)
