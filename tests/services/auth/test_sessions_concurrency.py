"""Concurrency regression test for the rotate_refresh TOCTOU race.

The `db` fixture used elsewhere in this test suite is a single connection wrapped in a
savepoint-per-test transaction that never commits -- it can't reproduce a cross-connection
race, since nothing it does is ever visible to another connection. This test instead opens
its own real, committing `Session`s against the session-scoped `engine` fixture's connection
pool (real test database, schema already created by that fixture), creates and commits its
own fixtures, and cleans up after itself.
"""

import threading
import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.auth import AuthSession
from app.db.models.user import User
from app.services.auth.sessions import issue_token_pair, rotate_refresh
from tests.factories import create_user


def test_concurrent_rotate_of_same_token_only_one_winner(engine: Engine):
    # Reuses the session-scoped `engine` fixture (real connection to the test DB, schema
    # already created) purely for its connection pool -- NOT the per-test `db` fixture,
    # which wraps everything in one savepoint that never commits and so can never be
    # visible across the two independent connections this test needs.
    setup = Session(bind=engine)
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        _, raw_refresh = issue_token_pair(setup, user)
        setup.commit()
        user_id = user.id

        barrier = threading.Barrier(2)
        results: list[tuple[bool, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            outcome: tuple[bool, BaseException | None]
            try:
                barrier.wait(timeout=5)
                try:
                    rotate_refresh(session, raw_refresh)
                except AppError as exc:
                    session.commit()  # persist the family-revoke side effect
                    outcome = (False, exc)
                else:
                    session.commit()  # persist the newly-issued session row
                    outcome = (True, None)
            finally:
                session.close()
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 2, "both threads must finish"
        successes = [r for r in results if r[0]]
        failures = [r for r in results if not r[0]]
        assert len(successes) == 1, f"expected exactly one winner, got {results}"
        assert len(failures) == 1, f"expected exactly one loser, got {results}"
        assert isinstance(failures[0][1], AppError)

        verify = Session(bind=engine)
        try:
            sessions = verify.query(AuthSession).filter(AuthSession.user_id == user_id).all()
            # Original (now-rotated) session + the winner's freshly-issued one.
            assert len(sessions) >= 2
            assert all(
                s.revoked_at is not None for s in sessions
            ), "a concurrent double-submit must log the whole family out"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if user_id is not None:
                cleanup.query(AuthSession).filter(AuthSession.user_id == user_id).delete()
                cleanup.query(User).filter(User.id == user_id).delete()
                cleanup.commit()
        finally:
            cleanup.close()
        setup.close()
