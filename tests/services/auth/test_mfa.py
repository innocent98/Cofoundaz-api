import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.auth import mfa
from tests.factories import create_user


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_roundtrip():
    secret = mfa.generate_totp_secret()
    assert mfa.decrypt_secret(mfa.encrypt_secret(secret)) == secret


def test_verify_totp():
    secret = mfa.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert mfa.verify_totp(secret, code) is True
    assert mfa.verify_totp(secret, "000000") is False


def test_backup_codes_generate_and_consume_once(db):
    u = create_user(db)
    codes = mfa.generate_backup_codes(db, u)
    assert len(codes) == 10
    assert mfa.consume_backup_code(db, u, codes[0]) is True
    assert mfa.consume_backup_code(db, u, codes[0]) is False  # single-use
    assert mfa.consume_backup_code(db, u, "not-a-code") is False
