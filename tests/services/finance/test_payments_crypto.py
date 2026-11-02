import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.errors import FinanceConnectionsNotConfigured, FinanceConnectionUnreadable
from app.services.finance.payments.crypto import decrypt_secret, encrypt_secret


@pytest.fixture
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", k)
    return k


def test_round_trip(key):
    token = encrypt_secret("placeholder-secret")
    assert token != "placeholder-secret"
    assert decrypt_secret(token) == "placeholder-secret"


def test_unset_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", None)
    with pytest.raises(FinanceConnectionsNotConfigured) as exc:
        encrypt_secret("x")
    assert exc.value.http_status == 503
    assert exc.value.code == "FINANCE_CONNECTIONS_NOT_CONFIGURED"
    assert exc.value.message == "Payment connections are not configured on the server."
    with pytest.raises(FinanceConnectionsNotConfigured):
        decrypt_secret("x")


def test_invalid_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(FinanceConnectionsNotConfigured):
        encrypt_secret("x")


def test_garbage_token_raises_unreadable(key):
    with pytest.raises(FinanceConnectionUnreadable) as exc:
        decrypt_secret("garbage")
    assert exc.value.http_status == 500
    assert exc.value.code == "FINANCE_CONNECTION_UNREADABLE"
    assert "garbage" not in exc.value.message


def test_wrong_key_raises_unreadable(key, monkeypatch):
    token = encrypt_secret("s")
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    with pytest.raises(FinanceConnectionUnreadable):
        decrypt_secret(token)
