"""Tests for the liveness / readiness split on /api/v1/health.

The distinction these lock in:
  /api/v1/health        - always 200 while the process is up, touches nothing.
  /api/v1/health/ready  - 200 only when Postgres AND Redis both answer; 503 otherwise.

The 503 path is what the CD deploy gate keys on, so it is worth real coverage:
if readiness wrongly returns 200 when the database is unreachable, a broken
deploy is promoted instead of rolled back.
"""

import pytest

from app.api.v1.endpoints import health as health_module


@pytest.fixture()
def redis_up(monkeypatch):
    """Default the Redis probe to healthy; individual tests override it.

    Unit tests run without a live Redis, so without this every readiness test
    would 503 for the wrong reason.
    """

    class _FakeRedis:
        def ping(self) -> bool:
            return True

    monkeypatch.setattr(health_module, "get_redis", lambda: _FakeRedis())
    return _FakeRedis


def test_liveness_is_ok_and_touches_no_dependency(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_ok_when_all_dependencies_answer(client, redis_up):
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"
    assert "revision" in body


def test_readiness_reports_revision_from_image_build_arg(client, redis_up, monkeypatch):
    monkeypatch.setenv("APP_GIT_SHA", "deadbeef")

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["revision"] == "deadbeef"


def test_readiness_503_when_redis_is_down(client, monkeypatch):
    class _DeadRedis:
        def ping(self) -> bool:
            raise ConnectionError("redis://cache:6379 connection refused")

    monkeypatch.setattr(health_module, "get_redis", lambda: _DeadRedis())

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["redis"]["status"] == "error"
    # Database is genuinely up in the test harness, so the failure must be
    # attributed to Redis alone rather than blanket-failing every check.
    assert body["checks"]["database"]["status"] == "ok"


def test_readiness_503_when_database_is_down(client, redis_up, monkeypatch):
    monkeypatch.setattr(health_module, "_check_database", lambda _db: (False, "unavailable"))

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "error"
    assert body["checks"]["redis"]["status"] == "ok"


def test_readiness_swallows_a_raising_database_driver(client, redis_up, monkeypatch):
    """A driver that raises must become a 503, not a 500.

    `_check_database` catches broadly on purpose: an uncaught OperationalError
    would surface as a 500, and a monitor keying on 503 would miss the outage.
    """
    from sqlalchemy.exc import OperationalError

    def _raise(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(health_module, "_check_redis", lambda: (True, None))

    class _DeadSession:
        def execute(self, *_args, **_kwargs):
            _raise()

    ok, reason = health_module._check_database(_DeadSession())
    assert ok is False
    assert reason == "unavailable"


def test_readiness_does_not_leak_connection_details_on_failure(client, monkeypatch):
    """An unauthenticated endpoint must not echo driver errors.

    Driver exceptions embed the full DSN - host, user, and often the password.
    Anyone on the internet can call this endpoint, so the response body must
    carry a generic reason and nothing else.
    """
    secret_dsn = "postgresql://admin:sup3rs3cret@db.internal:5432/cofoundaz"

    class _LeakyRedis:
        def ping(self) -> bool:
            raise ConnectionError(f"could not connect using {secret_dsn}")

    monkeypatch.setattr(health_module, "get_redis", lambda: _LeakyRedis())

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    raw = response.text
    assert "sup3rs3cret" not in raw
    assert "db.internal" not in raw
    assert secret_dsn not in raw
    assert response.json()["checks"]["redis"]["reason"] == "unavailable"
