from unittest.mock import patch

from app.platform.storage import CloudinaryStorage, LocalStorage, get_storage


def test_local_storage_saves_and_returns_path(tmp_path):
    s = LocalStorage(base_dir=str(tmp_path))
    url = s.save("logos/x.png", b"bytes", "image/png")
    assert url.endswith("logos/x.png")
    assert (tmp_path / "logos" / "x.png").read_bytes() == b"bytes"


def test_local_storage_save_then_delete(tmp_path):
    s = LocalStorage(base_dir=str(tmp_path))
    s.save("docs/x/a.txt", b"hello", "text/plain")
    assert (tmp_path / "docs/x/a.txt").read_bytes() == b"hello"
    s.delete("docs/x/a.txt")
    assert not (tmp_path / "docs/x/a.txt").exists()
    s.delete("docs/x/a.txt")  # idempotent: no error if already gone


def test_get_storage_selects_backend(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    assert isinstance(get_storage(), LocalStorage)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "cloudinary")
    assert isinstance(get_storage(), CloudinaryStorage)


def test_cloudinary_save_uploads_and_returns_secure_url():
    s = CloudinaryStorage()
    with patch("app.platform.storage.cloudinary.uploader.upload") as up:
        up.return_value = {"secure_url": "https://res.cloudinary.com/x/raw/upload/k.pdf"}
        url = s.save("documents/s1/k.pdf", b"%PDF-1.4", "application/pdf")
    assert url == "https://res.cloudinary.com/x/raw/upload/k.pdf"
    assert up.call_args.kwargs["public_id"] == "documents/s1/k.pdf"
    assert up.call_args.kwargs["resource_type"] == "raw"


def test_cloudinary_delete_calls_destroy():
    s = CloudinaryStorage()
    with patch("app.platform.storage.cloudinary.uploader.destroy") as dz:
        s.delete("documents/s1/k.pdf")
    assert dz.call_args.args[0] == "documents/s1/k.pdf"
    assert dz.call_args.kwargs["resource_type"] == "raw"
