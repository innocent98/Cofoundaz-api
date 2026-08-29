"""Legacy passlib hashes must still authenticate through the real login endpoint.

tests/core/test_password_hashing.py proves `verify_password` accepts hashes
written by the pre-migration passlib. This file proves the whole request path
does -- a row whose `password_hash` was written by passlib 1.7.4, POSTed to
POST /api/v1/auth/login, returning 200 and a token pair.

That is the production scenario: nobody rehashes on deploy, so every existing
row is a passlib row. If this file goes red, the migration locks out real users.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.models.enums import UserStatus
from tests.factories import create_user

_FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "passlib_golden_hashes.json"

with _FIXTURE.open(encoding="utf-8") as _fh:
    _GOLDEN = json.load(_fh)

# One representative hash per password case, using the CryptContext default --
# i.e. exactly what app/core/security.py used to write into the column.
_CASES = [
    (case["label"], case["password"], case["hashes"]["crypt_context_default"])
    for case in _GOLDEN["cases"]
]

# Legacy ident variants ($2a$/$2y$) that other tooling may have written.
_IDENT_CASES = [
    (f"{case['label']}-{ident}", case["password"], case["hashes"][f"ident_{ident}"])
    for case in _GOLDEN["cases"]
    for ident in ("2a", "2y")
    if case["label"] in {"ascii_simple", "ascii_100_bytes", "unicode_cjk_90_bytes"}
]


def _legacy_user(db, email, password_hash):
    return create_user(
        db,
        email=email,
        password_hash=password_hash,
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(("label", "password", "legacy_hash"), _CASES, ids=[c[0] for c in _CASES])
def test_login_succeeds_with_a_passlib_written_hash(client, db, label, password, legacy_hash):
    _legacy_user(db, f"legacy-{label}@x.com", legacy_hash)
    db.flush()
    r = client.post(
        "/api/v1/auth/login", json={"email": f"legacy-{label}@x.com", "password": password}
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["access_token"] and body["refresh_token"]


@pytest.mark.parametrize(
    ("label", "password", "legacy_hash"), _IDENT_CASES, ids=[c[0] for c in _IDENT_CASES]
)
def test_login_succeeds_with_legacy_ident_variants(client, db, label, password, legacy_hash):
    _legacy_user(db, f"ident-{label}@x.com", legacy_hash)
    db.flush()
    r = client.post(
        "/api/v1/auth/login", json={"email": f"ident-{label}@x.com", "password": password}
    )
    assert r.status_code == 200, r.text


def test_login_still_rejects_a_wrong_password_against_a_legacy_hash(client, db):
    label, password, legacy_hash = _CASES[0]
    _legacy_user(db, "legacy-wrong@x.com", legacy_hash)
    db.flush()
    r = client.post(
        "/api/v1/auth/login", json={"email": "legacy-wrong@x.com", "password": "Z" + password}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_accepts_an_over_72_byte_legacy_password(client, db):
    """The case a naive migration breaks with a 500 instead of a 200.

    This user's stored hash covers only the first 72 bytes of a 100-byte
    password. bcrypt 5 raises ValueError on the full secret, so without
    truncation on the verify path this request 500s and the user is locked out.
    """
    case = next(c for c in _GOLDEN["cases"] if c["label"] == "ascii_100_bytes")
    assert case["password_utf8_bytes"] == 100
    _legacy_user(db, "legacy-long@x.com", case["hashes"]["crypt_context_default"])
    db.flush()
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "legacy-long@x.com", "password": case["password"]},
    )
    assert r.status_code == 200, r.text


def test_login_accepts_a_multibyte_legacy_password_over_the_byte_limit(client, db):
    """30 CJK characters: under a 72-CHARACTER cap, over the 72-BYTE limit."""
    case = next(c for c in _GOLDEN["cases"] if c["label"] == "unicode_cjk_90_bytes")
    assert case["password_chars"] == 30 and case["password_utf8_bytes"] == 90
    _legacy_user(db, "legacy-cjk@x.com", case["hashes"]["crypt_context_default"])
    db.flush()
    r = client.post(
        "/api/v1/auth/login", json={"email": "legacy-cjk@x.com", "password": case["password"]}
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# The new set-password boundary, at the endpoint level.
# ---------------------------------------------------------------------------


def test_signup_rejects_an_over_72_byte_password_with_the_error_envelope(client, db):
    r = client.post(
        "/api/v1/auth/signup", json={"email": "toolong@x.com", "password": "a" * 72 + "1"}
    )
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "PASSWORD_TOO_LONG"
    assert error["message"]
    assert error["field_errors"] == []


def test_signup_rejects_a_multibyte_password_over_the_byte_limit(client, db):
    """30 CJK chars + a digit: 91 bytes. A character-based cap would let it in."""
    password = "1" + "密" * 30
    assert len(password) < 72 < len(password.encode("utf-8"))
    r = client.post("/api/v1/auth/signup", json={"email": "cjk@x.com", "password": password})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "PASSWORD_TOO_LONG"


def test_signup_accepts_a_password_at_exactly_the_byte_limit(client, db):
    r = client.post(
        "/api/v1/auth/signup", json={"email": "atlimit@x.com", "password": "a" * 71 + "1"}
    )
    assert r.status_code == 201, r.text
