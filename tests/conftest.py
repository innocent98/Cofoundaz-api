from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.db.models as _models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# Local-only fallback for developers without TEST_DATABASE_URL set; not a real secret.
TEST_URL = (
    settings.TEST_DATABASE_URL
    or "postgresql://user:password@localhost:5433/cofoundaz_test"  # noqa: S106
)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = create_engine(TEST_URL, pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    # Disable the live SlowAPI limiter for endpoint tests: as the auth suite grows,
    # many requests per test run could otherwise trip the shared 120/min default
    # limit and produce flaky 429s unrelated to what's under test.
    app.state.limiter.enabled = False
    with TestClient(app) as c:
        yield c
    app.state.limiter.enabled = True
    app.dependency_overrides.clear()
