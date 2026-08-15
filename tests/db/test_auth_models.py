import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.auth import AuthSession, AuthToken, MfaBackupCode, OAuthAccount
from app.db.models.enums import AuthTokenPurpose, OAuthProvider
from tests.factories import create_user


def test_auth_session_persists(db):
    u = create_user(db)
    s = AuthSession(
        user_id=u.id,
        refresh_token_hash="h1",
        family_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(s)
    db.flush()
    assert s.rotated_at is None and s.revoked_at is None


def test_refresh_hash_unique(db):
    u = create_user(db)
    fam = uuid.uuid4()
    db.add(
        AuthSession(
            user_id=u.id,
            refresh_token_hash="dup",
            family_id=fam,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.flush()
    db.add(
        AuthSession(
            user_id=u.id,
            refresh_token_hash="dup",
            family_id=fam,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_auth_token_and_backup_and_oauth(db):
    u = create_user(db)
    db.add(
        AuthToken(
            user_id=u.id,
            purpose=AuthTokenPurpose.email_verification,
            token_hash="t1",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    db.add(MfaBackupCode(user_id=u.id, code_hash="c1"))
    db.add(
        OAuthAccount(
            user_id=u.id,
            provider=OAuthProvider.google,
            provider_account_id="g-123",
            email="a@b.com",
        )
    )
    db.flush()


def test_oauth_provider_account_unique(db):
    u = create_user(db)
    db.add(
        OAuthAccount(
            user_id=u.id,
            provider=OAuthProvider.google,
            provider_account_id="same",
            email="a@b.com",
        )
    )
    db.flush()
    db.add(
        OAuthAccount(
            user_id=u.id,
            provider=OAuthProvider.google,
            provider_account_id="same",
            email="c@d.com",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
