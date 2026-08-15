from datetime import timedelta

import pytest

from app.core.errors import TokenInvalid
from app.db.models.enums import AuthTokenPurpose
from app.services.auth.tokens import consume_auth_token, issue_auth_token
from tests.factories import create_user


def test_issue_and_consume_once(db):
    u = create_user(db)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    who = consume_auth_token(db, AuthTokenPurpose.email_verification, raw)
    assert who.id == u.id
    with pytest.raises(TokenInvalid):  # single-use
        consume_auth_token(db, AuthTokenPurpose.email_verification, raw)


def test_wrong_purpose_rejected(db):
    u = create_user(db)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    with pytest.raises(TokenInvalid):
        consume_auth_token(db, AuthTokenPurpose.password_reset, raw)


def test_expired_rejected(db):
    u = create_user(db)
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(seconds=-1))
    with pytest.raises(TokenInvalid):
        consume_auth_token(db, AuthTokenPurpose.password_reset, raw)
