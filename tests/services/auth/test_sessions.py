from datetime import UTC, datetime, timedelta

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


def _age_rotation(db, raw: str, seconds: float) -> None:
    """Pretend `raw` was rotated `seconds` ago."""
    row = db.query(AuthSession).filter(AuthSession.refresh_token_hash == hash_token(raw)).one()
    row.rotated_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.flush()


def _live_heads(db, user_id) -> list[AuthSession]:
    return (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user_id,
            AuthSession.rotated_at.is_(None),
            AuthSession.revoked_at.is_(None),
        )
        .all()
    )


def test_reuse_revokes_family(db):
    u = create_user(db)
    _, refresh = issue_token_pair(db, u)
    rotate_refresh(db, refresh)  # refresh now rotated
    _age_rotation(db, refresh, settings.REFRESH_GRACE_SECONDS + 5)  # past the grace window
    with pytest.raises(AppError):
        rotate_refresh(db, refresh)  # reuse -> boom
    # entire family revoked
    sessions = db.query(AuthSession).filter(AuthSession.user_id == u.id).all()
    assert all(s.revoked_at is not None for s in sessions)


def test_previous_token_inside_grace_gets_a_working_pair_and_keeps_one_live_head(db):
    u = create_user(db)
    _, t0 = issue_token_pair(db, u)
    _, t1, _ = rotate_refresh(db, t0)
    _, t2, who = rotate_refresh(db, t0)  # t0 again, moments later: a lost response / second tab
    assert who.id == u.id and t2 not in (t0, t1)
    heads = _live_heads(db, u.id)
    assert [h.refresh_token_hash for h in heads] == [hash_token(t2)]  # one live head, the new one
    assert all(h.revoked_at is None for h in db.query(AuthSession).filter_by(user_id=u.id))
    # the sign-in is still one family
    assert len({s.family_id for s in db.query(AuthSession).filter_by(user_id=u.id)}) == 1
    # and the newest token rotates normally
    rotate_refresh(db, t2)


def test_grace_token_of_a_pair_the_other_tab_already_holds_still_works(db):
    u = create_user(db)
    _, t0 = issue_token_pair(db, u)
    _, t1, _ = rotate_refresh(db, t0)
    rotate_refresh(db, t0)  # second tab, in grace: rotates t1 away
    _, t3, _ = rotate_refresh(db, t1)  # first tab presents t1, itself just rotated: in grace
    assert len(_live_heads(db, u.id)) == 1 and t3


def test_grace_edge_just_inside_and_just_outside(db):
    u = create_user(db)
    _, t0 = issue_token_pair(db, u)
    rotate_refresh(db, t0)
    _age_rotation(db, t0, settings.REFRESH_GRACE_SECONDS - 2)
    rotate_refresh(db, t0)  # still inside
    _age_rotation(db, t0, settings.REFRESH_GRACE_SECONDS + 1)
    with pytest.raises(AppError):  # just outside: reuse
        rotate_refresh(db, t0)
    assert _live_heads(db, u.id) == []


def test_grace_does_not_resurrect_a_revoked_or_expired_family(db):
    u = create_user(db)
    _, t0 = issue_token_pair(db, u)
    rotate_refresh(db, t0)
    revoke_all_for_user(db, u.id)  # sign-out everywhere right after the rotation
    with pytest.raises(AppError):
        rotate_refresh(db, t0)
    u2 = create_user(db)
    _, e0 = issue_token_pair(db, u2)
    rotate_refresh(db, e0)
    row = db.query(AuthSession).filter(AuthSession.refresh_token_hash == hash_token(e0)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    with pytest.raises(AppError):
        rotate_refresh(db, e0)


def test_grace_refuses_a_suspended_account(db):
    from app.db.models.enums import UserStatus

    u = create_user(db)
    _, t0 = issue_token_pair(db, u)
    rotate_refresh(db, t0)
    u.status = UserStatus.disabled
    db.flush()
    with pytest.raises(AppError):
        rotate_refresh(db, t0)


def test_grace_can_be_switched_off(db, monkeypatch):
    monkeypatch.setattr(settings, "REFRESH_GRACE_SECONDS", 0)
    u = create_user(db)
    _, t0 = issue_token_pair(db, u)
    rotate_refresh(db, t0)
    _age_rotation(db, t0, 1)
    with pytest.raises(AppError):
        rotate_refresh(db, t0)


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
