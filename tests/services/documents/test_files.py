import uuid

import pytest

from app.core.config import settings
from app.core.errors import NotFound
from app.db.models.document import DocumentFile
from app.services.documents.files import (
    delete_file,
    get_file,
    list_files,
    serialize_file,
    upload_file,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s)
    db.flush()
    return u, s, m


def test_upload_persists_row_and_writes_asset(db, tmp_path, monkeypatch):
    u, s, _m = _ctx(db, tmp_path, monkeypatch)
    f = upload_file(
        db,
        s,
        uploaded_by_id=u.id,
        filename="nda.pdf",
        content_type="application/pdf",
        size_bytes=8,
        content=b"%PDF-1.4",
        folder="Legal",
    )
    assert f.filename == "nda.pdf" and f.size_bytes == 8 and f.url
    assert (tmp_path / f.storage_key).read_bytes() == b"%PDF-1.4"


def test_list_filters_by_folder(db, tmp_path, monkeypatch):
    u, s, _m = _ctx(db, tmp_path, monkeypatch)
    upload_file(
        db,
        s,
        uploaded_by_id=u.id,
        filename="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        content=b"a",
        folder="Legal",
    )
    upload_file(
        db,
        s,
        uploaded_by_id=u.id,
        filename="b.pdf",
        content_type="application/pdf",
        size_bytes=1,
        content=b"b",
        folder="Finance",
    )
    assert len(list_files(db, s, folder=None)) == 2
    assert [f.filename for f in list_files(db, s, folder="Legal")] == ["a.pdf"]


def test_get_cross_tenant_404(db, tmp_path, monkeypatch):
    _u, _s, m = _ctx(db, tmp_path, monkeypatch)
    with pytest.raises(NotFound):
        get_file(db, m, uuid.uuid4())


def test_delete_removes_row_and_asset(db, tmp_path, monkeypatch):
    u, s, _m = _ctx(db, tmp_path, monkeypatch)
    f = upload_file(
        db,
        s,
        uploaded_by_id=u.id,
        filename="x.pdf",
        content_type="application/pdf",
        size_bytes=1,
        content=b"x",
        folder=None,
    )
    key_path = tmp_path / f.storage_key
    assert key_path.exists()
    delete_file(db, f)
    assert db.query(DocumentFile).filter_by(id=f.id).first() is None
    assert not key_path.exists()


def test_serialize_shape(db, tmp_path, monkeypatch):
    u, s, _m = _ctx(db, tmp_path, monkeypatch)
    f = upload_file(
        db,
        s,
        uploaded_by_id=u.id,
        filename="x.pdf",
        content_type="application/pdf",
        size_bytes=3,
        content=b"xyz",
        folder="F",
    )
    out = serialize_file(f)
    assert out["filename"] == "x.pdf" and out["content_type"] == "application/pdf"
    assert out["size_bytes"] == 3 and out["folder"] == "F" and out["url"]
    assert "storage_key" not in out  # internal, not exposed
