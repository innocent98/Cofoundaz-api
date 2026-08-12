from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
