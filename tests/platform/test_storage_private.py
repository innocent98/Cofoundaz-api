"""Private storage: Cloudinary "authenticated" delivery, local files, signed tokens."""

import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.platform import signed_urls
from app.platform.storage import CloudinaryStorage, LocalStorage


@pytest.fixture(autouse=True)
def _cloudinary_creds(monkeypatch):
    monkeypatch.setattr(settings, "CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setattr(settings, "CLOUDINARY_API_KEY", "key")
    monkeypatch.setattr(settings, "CLOUDINARY_API_SECRET", "secret")


def test_cloudinary_save_private_uploads_as_authenticated():
    s = CloudinaryStorage()
    with patch("app.platform.storage.cloudinary.uploader.upload") as up:
        out = s.save_private("receipts/s/k.pdf", b"%PDF-", "application/pdf")
    assert out is None  # no URL is ever handed back
    kw = up.call_args.kwargs
    assert kw["type"] == "authenticated"
    assert kw["resource_type"] == "raw"
    assert kw["public_id"] == "receipts/s/k.pdf"


def test_cloudinary_delete_private_targets_the_authenticated_asset():
    s = CloudinaryStorage()
    with patch("app.platform.storage.cloudinary.uploader.destroy") as dz:
        s.delete_private("receipts/s/k.pdf")
    assert dz.call_args.kwargs == {"resource_type": "raw", "type": "authenticated"}


def test_cloudinary_private_url_is_signed_and_expires():
    s = CloudinaryStorage()
    now = int(time.time())
    url = s.private_url("receipts/s/k.pdf", 300)
    assert url is not None
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "api.cloudinary.com"
    assert parsed.path == "/v1_1/demo/raw/download"
    assert q["type"] == ["authenticated"]
    assert q["public_id"] == ["receipts/s/k.pdf"]
    assert now + 290 <= int(q["expires_at"][0]) <= now + 310
    assert q["signature"][0] and q["api_key"] == ["key"]
    assert "secret" not in url


def test_cloudinary_make_private_renames_to_authenticated():
    s = CloudinaryStorage()
    with patch("app.platform.storage.cloudinary.uploader.rename") as rn:
        s.make_private("receipts/s/k.pdf")
    assert rn.call_args.args == ("receipts/s/k.pdf", "receipts/s/k.pdf")
    kw = rn.call_args.kwargs
    assert (kw["type"], kw["to_type"], kw["resource_type"]) == ("upload", "authenticated", "raw")


def test_local_private_roundtrip_and_containment(tmp_path):
    s = LocalStorage(base_dir=str(tmp_path / "store"))
    s.save_private("receipts/s/a.png", b"png", "image/png")
    assert s.private_url("receipts/s/a.png", 300) is None  # we sign and serve local files
    assert s.open_private("receipts/s/a.png").read_bytes() == b"png"
    assert s.open_private("receipts/s/missing.png") is None
    (tmp_path / "secret.txt").write_text("nope")
    assert s.open_private("../secret.txt") is None
    assert s.open_private("receipts") is None  # a directory
    s.make_private("receipts/s/a.png")  # no-op
    s.delete_private("receipts/s/a.png")
    assert s.open_private("receipts/s/a.png") is None


def test_signed_token_roundtrip_and_failures():
    token, expires = signed_urls.sign_key("receipts/s/a.png", ttl_seconds=300)
    assert signed_urls.verify_key(token) == "receipts/s/a.png"
    assert expires > time.time()
    with pytest.raises(signed_urls.InvalidSignedToken):
        signed_urls.verify_key(token, now=expires + 1)
    body, mac = token.split(".")
    for bad in (f"{body}.{mac[::-1]}", body, f"{mac}.{body}", "", ".", "a.b"):
        with pytest.raises(signed_urls.InvalidSignedToken):
            signed_urls.verify_key(bad)


def test_signed_token_with_garbage_claims_is_rejected():
    import base64
    import json

    body = base64.urlsafe_b64encode(json.dumps({"k": "x"}).encode()).decode().rstrip("=")
    forged = f"{body}.{signed_urls._b64(signed_urls._mac(body))}"  # correctly signed, no expiry
    with pytest.raises(signed_urls.InvalidSignedToken):
        signed_urls.verify_key(forged)
