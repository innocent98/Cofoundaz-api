"""Concurrency tests for rotate_refresh: the claim TOCTOU race and the grace window.

The `db` fixture used elsewhere in this test suite is a single connection wrapped in a
savepoint-per-test transaction that never commits -- it can't reproduce a cross-connection
race, since nothing it does is ever visible to another connection. These tests instead open
their own real, committing `Session`s against the session-scoped `engine` fixture's connection
pool (real test database, schema already created by that fixture), create and commit their
own fixtures, and clean up after themselves.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.auth import AuthSession
from app.db.models.user import User
from app.services.auth.sessions import hash_token, issue_token_pair, rotate_refresh
from tests.factories import create_user


@pytest.fixture
def race(engine: Engine):
    """`race(rotated_seconds_ago)` -> (results, sessions). Two threads present the same refresh
    token at once. `rotated_seconds_ago=None` means the token is still unused; otherwise it was
    rotated that long ago by an earlier request."""
    created: list[uuid.UUID] = []

    def run(rotated_seconds_ago: float | None):
        setup = Session(bind=engine)
        try:
            user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
            _, raw = issue_token_pair(setup, user)
            setup.commit()
            user_id = user.id
            created.append(user_id)
            if rotated_seconds_ago is not None:
                rotate_refresh(setup, raw)
                old = setup.query(AuthSession).filter_by(refresh_token_hash=hash_token(raw)).one()
                old.rotated_at = datetime.now(UTC) - timedelta(seconds=rotated_seconds_ago)
                setup.commit()
        finally:
            setup.close()

        barrier = threading.Barrier(2)
        results: list[tuple[bool, BaseException | None]] = []
        lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            try:
                barrier.wait(timeout=5)
                try:
                    rotate_refresh(session, raw)
                except AppError as exc:
                    session.commit()  # persist the family-revoke side effect
                    outcome: tuple[bool, BaseException | None] = (False, exc)
                else:
                    session.commit()  # persist the newly-issued session row
                    outcome = (True, None)
            finally:
                session.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        verify = Session(bind=engine)
        try:
            rows = verify.query(AuthSession).filter(AuthSession.user_id == user_id).all()
            verify.expunge_all()
        finally:
            verify.close()
        return results, rows

    yield run

    cleanup = Session(bind=engine)
    try:
        for uid in created:
            cleanup.query(AuthSession).filter(AuthSession.user_id == uid).delete()
            cleanup.query(User).filter(User.id == uid).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def test_concurrent_double_submit_of_a_fresh_token_is_tolerated(race):
    """Two tabs refresh with the same token at once: one claims it, the other lands in the grace
    window. Both get a working pair, the family survives, and exactly one live head remains."""
    results, rows = race(None)
    assert len(results) == 2 and all(ok for ok, _ in results), results
    assert all(r.revoked_at is None for r in rows)
    assert len([r for r in rows if r.rotated_at is None]) == 1


def test_concurrent_replays_of_one_old_token_inside_grace_allow_only_one(race):
    """A grace use makes the old token a non-predecessor, so of two simultaneous replays the
    second is reuse: exactly one wins, and the family is then revoked."""
    results, rows = race(settings.REFRESH_GRACE_SECONDS - 5)
    assert len(results) == 2, results
    assert sorted(ok for ok, _ in results) == [False, True], results
    assert all(r.revoked_at is not None for r in rows)


def test_concurrent_replays_after_grace_are_reuse_and_revoke_the_family(race):
    results, rows = race(settings.REFRESH_GRACE_SECONDS + 5)
    assert len(results) == 2 and not any(ok for ok, _ in results), results
    assert all(isinstance(exc, AppError) for _, exc in results)
    assert len(rows) >= 2
    assert all(r.revoked_at is not None for r in rows), "reuse must log the whole family out"
