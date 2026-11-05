"""Gap 8: the MFA sign-in ticket survives a typo (up to 5 tries), then burns with
MFA_TICKET_EXPIRED so the FE can send the person back to /login."""

from datetime import UTC, datetime

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models.enums import MfaType, UserStatus
from app.services.auth import mfa
from tests.factories import create_user

CHALLENGE = "/api/v1/auth/mfa/challenge"


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _mfa_user(db, email):
    secret = mfa.generate_totp_secret()
    user = create_user(
        db,
        email=email,
        status=UserStatus.active,
        mfa_type=MfaType.totp,
        mfa_secret=mfa.encrypt_secret(secret),
        mfa_enabled_at=datetime.now(UTC),
        password_hash=get_password_hash("password1"),
    )
    db.flush()
    return user, secret


def _try(client, ticket, code):
    return client.post(CHALLENGE, json={"mfa_ticket": ticket, "code": code})


def test_a_typo_leaves_the_ticket_usable(client, db):
    user, secret = _mfa_user(db, "typo@x.com")
    ticket = mfa.issue_mfa_ticket(user.id)
    wrong = _try(client, ticket, "000000")
    assert wrong.status_code == 401
    err = wrong.json()["error"]
    assert err["code"] == "MFA_INVALID_CODE"
    assert err["attempts_remaining"] == settings.MFA_TICKET_MAX_ATTEMPTS - 1
    ok = _try(client, ticket, pyotp.TOTP(secret).now())
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["access_token"]


def test_a_backup_code_still_works_after_a_typo(client, db):
    user, _secret = _mfa_user(db, "backup-typo@x.com")
    codes = mfa.generate_backup_codes(db, user)
    ticket = mfa.issue_mfa_ticket(user.id)
    assert _try(client, ticket, "000000").status_code == 401
    assert _try(client, ticket, codes[0]).status_code == 200


def test_five_wrong_codes_burn_the_ticket(client, db):
    user, secret = _mfa_user(db, "burn@x.com")
    ticket = mfa.issue_mfa_ticket(user.id)
    maximum = settings.MFA_TICKET_MAX_ATTEMPTS
    for _ in range(maximum - 1):
        assert _try(client, ticket, "000000").json()["error"]["code"] == "MFA_INVALID_CODE"
    last = _try(client, ticket, "000000")
    assert last.status_code == 401
    assert last.json()["error"]["code"] == "MFA_TICKET_EXPIRED"
    assert last.json()["error"]["message"] == "Your sign-in timed out. Log in again."
    # dead even for a correct code
    after = _try(client, ticket, pyotp.TOTP(secret).now())
    assert after.status_code == 401 and after.json()["error"]["code"] == "MFA_TICKET_EXPIRED"


def test_the_fifth_attempt_may_still_be_the_right_one(client, db):
    user, secret = _mfa_user(db, "fifth@x.com")
    ticket = mfa.issue_mfa_ticket(user.id)
    for _ in range(settings.MFA_TICKET_MAX_ATTEMPTS - 1):
        _try(client, ticket, "000000")
    assert _try(client, ticket, pyotp.TOTP(secret).now()).status_code == 200


def test_a_ticket_is_single_use_after_success(client, db):
    user, secret = _mfa_user(db, "once@x.com")
    ticket = mfa.issue_mfa_ticket(user.id)
    assert _try(client, ticket, pyotp.TOTP(secret).now()).status_code == 200
    again = _try(client, ticket, "000000")
    assert again.json()["error"]["code"] == "MFA_TICKET_EXPIRED"


def test_an_unknown_ticket_is_expired_not_invalid_code(client):
    r = _try(client, "no-such-ticket", "123456")
    assert r.status_code == 401 and r.json()["error"]["code"] == "MFA_TICKET_EXPIRED"
