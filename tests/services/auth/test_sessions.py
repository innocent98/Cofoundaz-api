import pytest
from fastapi import Response

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.auth import AuthSession
from app.services.auth.sessions import (
    clear_refresh_cookie,
    hash_token,
    issue_token_pair,
    revoke_all_for_user,
    revoke_session,
    rotate_refresh,
    set_refresh_cookie,
)
from tests.factories import create_user


def test_issue_creates_session_and_hashes(db):
    u = create_user(db)
    access, refresh = issue_token_pair(db, u)
    assert access and refresh
    row = db.query(AuthSession).filter(AuthSession.user_id == u.id).one()
    assert row.refresh_token_hash == hash_token(refresh)  # raw never stored


def test_rotate_supersedes_old(db):
    u = create_user(db)
    _, refresh = issue_token_pair(db, u)
    _, new_refresh, who = rotate_refresh(db, refresh)
    assert who.id == u.id and new_refresh != refresh
    old = db.query(AuthSession).filter(AuthSession.refresh_token_hash == hash_token(refresh)).one()
    assert old.rotated_at is not None


def test_reuse_revokes_family(db):
    u = create_user(db)
    _, refresh = issue_token_pair(db, u)
    rotate_refresh(db, refresh)  # refresh now rotated
    with pytest.raises(AppError):
        rotate_refresh(db, refresh)  # reuse -> boom
    # entire family revoked
    sessions = db.query(AuthSession).filter(AuthSession.user_id == u.id).all()
    assert all(s.revoked_at is not None for s in sessions)


def test_revoke_all(db):
    u = create_user(db)
    issue_token_pair(db, u)
    issue_token_pair(db, u)
    n = revoke_all_for_user(db, u.id)
    assert n >= 2


def test_rotate_unknown_token_raises(db):
    with pytest.raises(AppError):
        rotate_refresh(db, "not-a-real-token")


def test_revoke_session_marks_revoked(db):
    u = create_user(db)
    _, refresh = issue_token_pair(db, u)
    revoke_session(db, refresh)
    row = db.query(AuthSession).filter(AuthSession.refresh_token_hash == hash_token(refresh)).one()
    assert row.revoked_at is not None


def test_revoked_session_cannot_rotate(db):
    u = create_user(db)
    _, refresh = issue_token_pair(db, u)
    revoke_session(db, refresh)
    with pytest.raises(AppError):
        rotate_refresh(db, refresh)


def test_set_refresh_cookie_sets_expected_attributes():
    response = Response()
    set_refresh_cookie(response, "raw-token-value")
    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert f"{settings.REFRESH_COOKIE_NAME}=raw-token-value" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "Path=/api/v1/auth" in cookie_header


def test_clear_refresh_cookie_expires_it():
    response = Response()
    clear_refresh_cookie(response)
    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert f'{settings.REFRESH_COOKIE_NAME}=""' in cookie_header
    assert "Path=/api/v1/auth" in cookie_header
