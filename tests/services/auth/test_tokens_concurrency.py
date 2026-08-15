"""Concurrency regression test for the consume_auth_token TOCTOU race.

Same rationale as `test_sessions_concurrency.py`: the per-test `db` fixture wraps
everything in one savepoint that never commits, so it can't reproduce a cross-connection
race. This test opens its own real, committing `Session`s against the session-scoped
`engine` fixture's connection pool instead.
"""

import threading
import uuid
from datetime import timedelta

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.errors import TokenInvalid
from app.db.models.auth import AuthToken
from app.db.models.enums import AuthTokenPurpose
from app.db.models.user import User
from app.services.auth.tokens import consume_auth_token, issue_auth_token
from tests.factories import create_user


def test_concurrent_consume_of_same_token_only_one_winner(engine: Engine):
    setup = Session(bind=engine)
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        raw = issue_auth_token(
            setup, user, AuthTokenPurpose.email_verification, timedelta(hours=24)
        )
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
                    consume_auth_token(session, AuthTokenPurpose.email_verification, raw)
                except TokenInvalid as exc:
                    session.rollback()
                    outcome = (False, exc)
                else:
                    session.commit()
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
        assert isinstance(failures[0][1], TokenInvalid)
    finally:
        cleanup = Session(bind=engine)
        try:
            if user_id is not None:
                cleanup.query(AuthToken).filter(AuthToken.user_id == user_id).delete()
                cleanup.query(User).filter(User.id == user_id).delete()
                cleanup.commit()
        finally:
            cleanup.close()
        setup.close()
