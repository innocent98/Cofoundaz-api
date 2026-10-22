"""Private receipts (gap row 2, security) and magic-byte upload checks (row 3)."""

import io
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.db.models.expense import Expense
from app.platform import signed_urls
from app.services.finance import expenses as expense_svc
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF = b"%PDF-1.7\n%%EOF"


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "http://api.test")


def _expense(client, h):
    resp = client.post(
        f"{BASE}/expenses",
        json={
            "vendor": "AWS",
            "category": "Infrastructure",
            "expense_date": "2026-03-10",
            "amount_minor": 250_000,
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _upload(client, h, eid, body=PNG, name="r.png", ct="image/png"):
    return client.post(
        f"{BASE}/expenses/{eid}/receipt", files={"file": (name, io.BytesIO(body), ct)}, headers=h
    )


def _token(url: str) -> str:
    return url.split("token=", 1)[1]


# ---- what the API returns --------------------------------------------------------------------- #


def test_responses_expose_the_route_never_the_storage_url_or_path(client, db, tmp_path):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    up = _upload(client, h, eid)
    assert up.status_code == 200, up.text
    data = up.json()["data"]
    assert data["has_receipt"] is True
    assert data["receipt_url"] == f"/api/v1/finance/expenses/{eid}/receipt"
    for resp in (
        up,
        client.get(f"{BASE}/expenses/{eid}", headers=h),
        client.get(f"{BASE}/expenses", headers=h),
    ):
        assert str(tmp_path) not in resp.text
        assert "receipts/" not in resp.text
        assert "cloudinary" not in resp.text


def test_receipt_url_null_without_receipt(client, db):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    data = client.get(f"{BASE}/expenses/{eid}", headers=h).json()["data"]
    assert data["has_receipt"] is False and data["receipt_url"] is None
    assert client.get(f"{BASE}/expenses/{eid}/receipt", headers=h).status_code == 404


def test_new_upload_is_marked_private_and_stores_no_raw_url(client, db):
    _u, s, h = _member(db)
    eid = _expense(client, h)
    _upload(client, h, eid)
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    db.refresh(exp)
    assert exp.receipt_private is True
    assert exp.receipt_url is None
    assert exp.receipt_key.startswith(f"receipts/{s.id}/")


# ---- the signed link -------------------------------------------------------------------------- #


def test_signed_link_serves_the_file_with_safe_headers(client, db):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    _upload(client, h, eid, body=PDF, name="r.pdf", ct="application/pdf")
    resp = client.get(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["content_type"] == "application/pdf"
    assert data["url"].startswith("http://api.test/api/v1/finance/receipts/download?token=")
    assert data["expires_at"]
    # The link needs no bearer token: it is the credential.
    got = client.get(f"{BASE}/receipts/download", params={"token": _token(data["url"])})
    assert got.status_code == 200
    assert got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["cache-control"] == "private, no-store"


def test_link_is_short_lived(client, db):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    _upload(client, h, eid)
    before = time.time()
    data = client.get(f"{BASE}/expenses/{eid}/receipt", headers=h).json()["data"]
    token = _token(data["url"])
    assert signed_urls.verify_key(token, now=before + 10)  # valid now
    with pytest.raises(signed_urls.InvalidSignedToken):
        signed_urls.verify_key(token, now=before + expense_svc.RECEIPT_LINK_TTL_SECONDS + 5)
    assert expense_svc.RECEIPT_LINK_TTL_SECONDS <= 600


def test_expired_link_is_404(client, db):
    _u, s, h = _member(db)
    eid = _expense(client, h)
    _upload(client, h, eid)
    key = db.query(Expense).filter_by(startup_id=s.id).one().receipt_key
    expired, _ = signed_urls.sign_key(key, ttl_seconds=60, now=time.time() - 3600)
    assert client.get(f"{BASE}/receipts/download", params={"token": expired}).status_code == 404


def test_tampered_link_is_404(client, db):
    _u, s, h = _member(db)
    eid = _expense(client, h)
    _upload(client, h, eid)
    key = db.query(Expense).filter_by(startup_id=s.id).one().receipt_key
    token, _ = signed_urls.sign_key(key, ttl_seconds=300)
    body, mac = token.split(".")
    other_body, _ = signed_urls.sign_key("receipts/other/x.png", ttl_seconds=300)
    flipped_mac = mac[:-2] + ("AA" if not mac.endswith("AA") else "BB")
    for bad in (
        f"{body}.{flipped_mac}",  # signature altered
        f"{other_body.split('.')[0]}.{mac}",  # payload swapped, signature kept
        body,  # signature stripped
        "not-a-token",
        "",
        f"{body}.{mac}.extra",
    ):
        resp = client.get(f"{BASE}/receipts/download", params={"token": bad or "x"})
        assert resp.status_code == 404, bad


def test_validly_signed_traversal_key_is_404(client, db):
    token, _ = signed_urls.sign_key("receipts/../../../etc/passwd", ttl_seconds=300)
    assert client.get(f"{BASE}/receipts/download", params={"token": token}).status_code == 404


def test_validly_signed_key_of_the_wrong_shape_is_404_even_if_the_file_exists(client, db, tmp_path):
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts" / "note.png").write_bytes(PNG)
    token, _ = signed_urls.sign_key("receipts/note.png", ttl_seconds=300)
    assert client.get(f"{BASE}/receipts/download", params={"token": token}).status_code == 404


def test_token_signed_with_another_secret_is_404(client, db, monkeypatch):
    token, _ = signed_urls.sign_key("receipts/x/a.png", ttl_seconds=300)
    monkeypatch.setattr(settings, "SECRET_KEY", "a-different-secret-key-for-this-test")
    assert client.get(f"{BASE}/receipts/download", params={"token": token}).status_code == 404


def test_download_requires_a_token(client):
    assert client.get(f"{BASE}/receipts/download").status_code == 422


# ---- who may ask for a link ------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role", [MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant]
)
def test_finance_roles_get_a_link(client, db, role):
    _f, startup, fh = _member(db)
    eid = _expense(client, fh)
    _upload(client, fh, eid)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert client.get(f"{BASE}/expenses/{eid}/receipt", headers=h).status_code == 200


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_mentor_and_investor_never_get_a_link(client, db, role):
    _f, startup, fh = _member(db)
    eid = _expense(client, fh)
    _upload(client, fh, eid)
    _u, _s, h = _member(db, role=role, startup=startup)
    resp = client.get(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert resp.status_code == 403
    assert "token=" not in resp.text


def test_unauthenticated_cannot_ask_for_a_link(client, db):
    _f, _startup, fh = _member(db)
    eid = _expense(client, fh)
    _upload(client, fh, eid)
    assert client.get(f"{BASE}/expenses/{eid}/receipt").status_code in (401, 403)


def test_other_workspaces_expense_is_404_not_403(client, db):
    _f, _s1, fh = _member(db)
    eid = _expense(client, fh)
    _upload(client, fh, eid)
    _x, _s2, other = _member(db)  # a founder of a different workspace
    resp = client.get(f"{BASE}/expenses/{eid}/receipt", headers=other)
    assert resp.status_code == 404
    assert "token=" not in resp.text


def test_removing_the_receipt_removes_the_link(client, db):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    _upload(client, h, eid)
    client.delete(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert client.get(f"{BASE}/expenses/{eid}/receipt", headers=h).status_code == 404


# ---- legacy rows (uploaded when receipts were public) ----------------------------------------- #


class _FakeStorage:
    def __init__(self):
        self.made_private: list[str] = []
        self.fail = False

    def make_private(self, key):
        if self.fail:
            raise RuntimeError("provider down")
        self.made_private.append(key)

    def private_url(self, key, ttl):
        return f"https://signed.example/{key}?exp={ttl}"


def _legacy(db, startup, **over):
    exp = Expense(
        startup_id=startup.id,
        vendor="Old",
        category="Software",
        expense_date=__import__("datetime").date(2026, 1, 5),
        amount_minor=1000,
        currency="NGN",
        receipt_url="https://res.cloudinary.com/demo/raw/upload/receipts/x/old.pdf",
        receipt_key=f"receipts/{startup.id}/old.pdf",
        receipt_private=False,
        **over,
    )
    db.add(exp)
    db.flush()
    return exp


def test_legacy_row_never_returns_its_public_url(client, db):
    _u, s, h = _member(db)
    exp = _legacy(db, s)
    for resp in (
        client.get(f"{BASE}/expenses/{exp.id}", headers=h),
        client.get(f"{BASE}/expenses", headers=h),
    ):
        assert "cloudinary" not in resp.text
        assert "res." not in resp.text
    data = client.get(f"{BASE}/expenses/{exp.id}", headers=h).json()["data"]
    assert data["has_receipt"] is True
    assert data["receipt_url"] == f"/api/v1/finance/expenses/{exp.id}/receipt"


def test_legacy_row_is_moved_behind_auth_on_first_open(client, db, monkeypatch):
    _u, s, h = _member(db)
    exp = _legacy(db, s)
    fake = _FakeStorage()
    monkeypatch.setattr(expense_svc, "get_storage", lambda: fake)
    resp = client.get(f"{BASE}/expenses/{exp.id}/receipt", headers=h)
    assert resp.status_code == 200, resp.text
    assert fake.made_private == [exp.receipt_key]
    assert resp.json()["data"]["url"].startswith("https://signed.example/")
    db.refresh(exp)
    assert exp.receipt_private is True and exp.receipt_url is None
    client.get(f"{BASE}/expenses/{exp.id}/receipt", headers=h)
    assert fake.made_private == [exp.receipt_key]  # not moved twice


def test_legacy_row_refuses_rather_than_leaking_when_the_move_fails(client, db, monkeypatch):
    _u, s, h = _member(db)
    exp = _legacy(db, s)
    fake = _FakeStorage()
    fake.fail = True
    monkeypatch.setattr(expense_svc, "get_storage", lambda: fake)
    with pytest.raises(RuntimeError):
        client.get(f"{BASE}/expenses/{exp.id}/receipt", headers=h)
    db.refresh(exp)
    assert exp.receipt_private is False


def test_replacing_a_legacy_receipt_deletes_the_public_asset(client, db, monkeypatch, tmp_path):
    _u, s, h = _member(db)
    exp = _legacy(db, s)
    calls = []

    class Store(_FakeStorage):
        def save_private(self, key, content, ctype):
            calls.append(("save_private", key))

        def delete(self, key):
            calls.append(("delete_public", key))

        def delete_private(self, key):
            calls.append(("delete_private", key))

    monkeypatch.setattr(expense_svc, "get_storage", lambda: Store())
    old_key = exp.receipt_key
    assert _upload(client, h, str(exp.id)).status_code == 200
    assert ("delete_public", old_key) in calls
    assert not any(c[0] == "delete_private" for c in calls)


# ---- upload sniffing (row 3) ------------------------------------------------------------------ #


def _files(tmp_path):
    return [p for p in Path(tmp_path).rglob("*") if p.is_file()]


def test_html_labelled_image_png_is_422(client, db, tmp_path):
    _u, s, h = _member(db)
    eid = _expense(client, h)
    resp = _upload(client, h, eid, body=b"<html><script>alert(1)</script></html>")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _files(tmp_path) == []
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    db.refresh(exp)
    assert exp.receipt_key is None


@pytest.mark.parametrize(
    ("body", "ext", "ctype"),
    [(PNG, ".png", "image/png"), (JPEG, ".jpg", "image/jpeg"), (PDF, ".pdf", "application/pdf")],
)
def test_type_comes_from_the_bytes_not_the_client(client, db, tmp_path, body, ext, ctype):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    # Wrong filename and wrong Content-Type: both ignored.
    resp = _upload(client, h, eid, body=body, name="invoice.exe", ct="text/plain")
    assert resp.status_code == 200, resp.text
    assert [p.suffix for p in _files(tmp_path)] == [ext]
    link = client.get(f"{BASE}/expenses/{eid}/receipt", headers=h).json()["data"]
    assert link["content_type"] == ctype


@pytest.mark.parametrize(
    "body",
    [b"", b"GIF89a....", b"PK\x03\x04zip", b"\x89PNG", b"%PD", b"MZ\x90\x00", b"  %PDF-1.4"],
)
def test_other_bytes_are_422(client, db, tmp_path, body):
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    resp = _upload(client, h, eid, body=body, ct="application/pdf")
    assert resp.status_code == 422, resp.text
    assert _files(tmp_path) == []


def test_oversize_is_422_even_with_valid_magic(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(expense_svc, "MAX_RECEIPT_BYTES", 64)
    _u, _s, h = _member(db)
    eid = _expense(client, h)
    assert _upload(client, h, eid, body=PNG + b"x" * 64).status_code == 422
    assert _files(tmp_path) == []


def test_size_cap_is_ten_megabytes():
    assert expense_svc.MAX_RECEIPT_BYTES == 10 * 1024 * 1024


def test_sniff_receipt_unit():
    assert expense_svc.sniff_receipt(PNG) == ("image/png", ".png")
    assert expense_svc.sniff_receipt(JPEG) == ("image/jpeg", ".jpg")
    assert expense_svc.sniff_receipt(PDF) == ("application/pdf", ".pdf")
    assert expense_svc.sniff_receipt(b"<svg/>") is None
    assert expense_svc.receipt_content_type("receipts/x/y.bin") == "application/octet-stream"
