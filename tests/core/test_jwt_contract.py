"""The access-token contract, pinned independently of the JWT library.

Every token here is built by hand from the RFC 7519 / RFC 7515 wire format
(base64url JSON segments + HMAC-SHA256 over ``header.payload``) using only the
standard library. That makes these tests a guard on the python-jose -> PyJWT
swap: a token any standards-compliant HS256 library minted - including tokens
python-jose issued to users who are still logged in when the swap deploys - must
keep authenticating, and every malformed or hostile token must keep failing.
"""

import ast
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.rate_limit import _rate_limit_key
from app.core.security import create_access_token
from app.db.models.user import User
from app.db.session import get_db
from tests.factories import create_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _segment(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def _hand_built_token(
    payload: dict,
    *,
    alg: str = "HS256",
    key: str | None = None,
    digest=hashlib.sha256,
) -> str:
    signing_input = f"{_segment({'alg': alg, 'typ': 'JWT'})}.{_segment(payload)}"
    secret = (key if key is not None else settings.SECRET_KEY).encode("utf-8")
    signature = hmac.new(secret, signing_input.encode("ascii"), digest).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _future_exp() -> int:
    return int(time.time()) + 300


def _whoami_client(db) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):  # noqa: B008
        return {"email": user.email}

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _status_for(db, token: str) -> int:
    resp = _whoami_client(db).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    return resp.status_code


class _Req:
    def __init__(self, auth: str):
        self.headers = {"Authorization": auth}
        self.client = type("C", (), {"host": "9.9.9.9"})()


# --- accepted ------------------------------------------------------------- #


def test_independently_built_hs256_token_authenticates(db):
    """Tokens minted before the library swap must survive the deploy."""
    user = create_user(db, email="compat@jwt.test")
    db.commit()
    token = _hand_built_token({"sub": str(user.id), "exp": _future_exp()})
    assert _status_for(db, token) == 200


def test_issued_token_is_standard_hs256_with_string_sub():
    subject = uuid.uuid4()
    token = create_access_token(subject, expires_delta=timedelta(minutes=5))
    header_seg, payload_seg, signature_seg = token.split(".")

    header = json.loads(_b64url_decode(header_seg))
    payload = json.loads(_b64url_decode(payload_seg))
    expected_sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"{header_seg}.{payload_seg}".encode("ascii"),
        hashlib.sha256,
    ).digest()

    assert header["alg"] == "HS256"
    assert payload["sub"] == str(subject)
    assert isinstance(payload["exp"], int)
    assert hmac.compare_digest(_b64url_decode(signature_seg), expected_sig)


# --- rejected ------------------------------------------------------------- #


def test_expired_token_401(db):
    user = create_user(db, email="expired@jwt.test")
    db.commit()
    token = _hand_built_token({"sub": str(user.id), "exp": int(time.time()) - 60})
    assert _status_for(db, token) == 401


def test_tampered_payload_401(db):
    user = create_user(db, email="tamper@jwt.test")
    other = create_user(db, email="victim@jwt.test")
    db.commit()
    header_seg, _, signature_seg = _hand_built_token(
        {"sub": str(user.id), "exp": _future_exp()}
    ).split(".")
    forged_payload = _segment({"sub": str(other.id), "exp": _future_exp()})
    assert _status_for(db, f"{header_seg}.{forged_payload}.{signature_seg}") == 401


def test_wrong_secret_401(db):
    user = create_user(db, email="wrongkey@jwt.test")
    db.commit()
    token = _hand_built_token(
        {"sub": str(user.id), "exp": _future_exp()}, key="not-the-server-secret"
    )
    assert _status_for(db, token) == 401


def test_alg_none_401(db):
    user = create_user(db, email="none@jwt.test")
    db.commit()
    unsigned = (
        f"{_segment({'alg': 'none', 'typ': 'JWT'})}."
        f"{_segment({'sub': str(user.id), 'exp': _future_exp()})}."
    )
    assert _status_for(db, unsigned) == 401


def test_other_hmac_algorithm_with_correct_secret_401(db):
    """The verifier pins HS256; a validly signed HS512 token is still refused."""
    user = create_user(db, email="hs512@jwt.test")
    db.commit()
    token = _hand_built_token(
        {"sub": str(user.id), "exp": _future_exp()}, alg="HS512", digest=hashlib.sha512
    )
    assert _status_for(db, token) == 401


def test_rs256_header_with_hmac_signature_401(db):
    """Algorithm confusion: an asymmetric alg header must not be verified as HMAC."""
    user = create_user(db, email="rs256@jwt.test")
    db.commit()
    token = _hand_built_token({"sub": str(user.id), "exp": _future_exp()}, alg="RS256")
    assert _status_for(db, token) == 401


def test_non_string_sub_401(db):
    """A validly signed token whose `sub` is not a string is refused, not a 500.
    (Under python-jose this reached uuid.UUID(123) and raised AttributeError.)"""
    assert _status_for(db, _hand_built_token({"sub": 123, "exp": _future_exp()})) == 401


def test_missing_sub_401(db):
    assert _status_for(db, _hand_built_token({"exp": _future_exp()})) == 401


def test_non_uuid_sub_401(db):
    assert _status_for(db, _hand_built_token({"sub": "not-a-uuid", "exp": _future_exp()})) == 401


def test_garbage_token_401(db):
    assert _status_for(db, "this.is.not-a-jwt") == 401


def test_rate_limit_key_falls_back_to_ip_for_expired_token():
    token = _hand_built_token({"sub": str(uuid.uuid4()), "exp": int(time.time()) - 60})
    assert _rate_limit_key(_Req(f"Bearer {token}")) == "9.9.9.9"


def test_rate_limit_key_falls_back_to_ip_for_garbage_token():
    assert _rate_limit_key(_Req("Bearer garbage")) == "9.9.9.9"


# --- the dependency itself ------------------------------------------------ #


def test_app_does_not_import_python_jose():
    """python-jose carries CVE-2026-85394 (no fix) and pulls in ecdsa
    (PYSEC-2026-1325); the app must verify tokens with PyJWT instead."""
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "jose":
                offenders.append(str(path.relative_to(APP_DIR.parent)))
            elif isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "jose" for alias in node.names
            ):
                offenders.append(str(path.relative_to(APP_DIR.parent)))
    assert not offenders, f"python-jose still imported by: {offenders}"
