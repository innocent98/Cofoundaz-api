# Foundation & Tenancy Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared platform layer + multi-tenant data spine (users, workspaces, memberships) that every one of the 26 modules inherits — envelope, errors, provider seams, and RBAC/tenancy — with no auth *endpoints* yet.

**Architecture:** Modular monolith. Cross-cutting concerns live in `app/platform/`; the tenancy spine models live in `app/db/models/`. Deferred subsystems (async worker, event bus, AI panel, email/storage providers) are shipped as Protocols with dev-only implementations, so later modules depend on the interface, not the impl.

**Tech Stack:** Python 3.11, FastAPI 0.115 (sync SQLAlchemy), SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL (psycopg2), Redis, python-jose (JWT), passlib[bcrypt], slowapi, Jinja2, pytest + real Postgres.

## Global Constraints

- **Response envelope:** success `{ "data": …, "meta": {…}|null }`; error `{ "error": { "code", "message", "field_errors": [{"field","message"}] } }`. `code` is a machine-readable SCREAMING_SNAKE string.
- **PKs:** UUID v4, application-generated (`default=uuid4`). No sequential/bigint PKs.
- **Every table** inherits `TimestampMixin` (`created_at`, `updated_at`, both `timestamptz`, server-defaulted). Soft-delete (`deleted_at`) is opt-in per table via `SoftDeleteMixin`.
- **Tables:** snake_case, plural. **Constraint naming convention** on `Base.metadata` (for clean Alembic autogenerate).
- **Enums:** SQLAlchemy `Enum(PyEnum, native_enum=False)` → VARCHAR + CHECK (avoids fragile native-PG-enum migrations).
- **Tenancy:** every workspace-scoped query filters by `startup_id` resolved from the `X-Workspace-Id` header, validated against `memberships`. Never trusted from the JWT alone.
- **Tests:** real Postgres, per-test transaction rollback. Coverage gate ~85% on `app/` (configured, not yet enforced-blocking until endpoints exist).
- **Money/secret values** are never stored raw — hashing/encryption helpers live in `app/core/security.py`.
- **Timestamps** use timezone-aware UTC (`datetime.now(timezone.utc)`), never naive `utcnow()`.

Reference spec: `docs/superpowers/specs/2026-08-12-auth-onboarding-foundation-design.md`.

---

## File Structure

**Create:**
- `app/core/redis.py` — Redis client singleton.
- `app/core/envelope.py` — `success_response`, `error_response`, `Meta`.
- `app/core/errors.py` — `AppError` hierarchy + FastAPI exception handlers.
- `app/core/pagination.py` — opaque cursor encode/decode + `paginate`.
- `app/db/mixins.py` — `UUIDMixin`, `TimestampMixin`, `SoftDeleteMixin`.
- `app/db/models/__init__.py` — imports every model (Alembic discovery).
- `app/db/models/enums.py` — shared Python enums.
- `app/db/models/user.py`, `startup.py`, `membership.py` — tenancy spine.
- `app/db/models/job.py`, `audit.py` — platform tables.
- `app/db/tenancy.py` — `resolve_workspace`, `tenant_scope`.
- `app/platform/email.py`, `storage.py`, `events.py`, `ai.py`, `jobs.py`, `audit.py`, `ratelimit.py` — seams.
- `app/api/v1/endpoints/jobs.py` — `GET /jobs/{id}`.
- `tests/conftest.py` (replace), `tests/factories.py`, plus `tests/**` per task.

**Modify:**
- `app/db/base.py` — typed `Base` + naming convention.
- `app/db/session.py` — expose `Session` type for tests.
- `app/core/config.py` — new settings (§9 of spec).
- `app/api/deps.py` — real `get_current_user`.
- `app/api/v1/api.py` — mount jobs router.
- `app/main.py` — register exception handlers + rate limiter.
- `alembic/env.py` — import `app.db.models`.
- `pyproject.toml` — add `pyotp` is NOT here (auth plan); add `cryptography` (Fernet) already present via jose extras? add explicitly. Add `[tool.pytest]`/coverage config.

---

## Task 1: Postgres test harness + typed Base

**Files:**
- Modify: `app/db/base.py`
- Modify: `app/core/config.py` (add `TEST_DATABASE_URL`)
- Create/replace: `tests/conftest.py`
- Test: `tests/test_harness.py`

**Interfaces:**
- Produces: `Base` (typed declarative base with naming convention); pytest fixtures `engine` (session-scoped), `db` (function-scoped, transaction-rolled-back `Session`), `client` (function-scoped `TestClient` sharing `db`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness.py
from sqlalchemy import text

def test_db_fixture_is_postgres(db):
    version = db.execute(text("SELECT version()")).scalar()
    assert "PostgreSQL" in version

def test_rollback_isolation_first(db):
    db.execute(text("CREATE TEMP TABLE t_iso (n int)"))
    db.execute(text("INSERT INTO t_iso VALUES (1)"))
    assert db.execute(text("SELECT count(*) FROM t_iso")).scalar() == 1

def test_rollback_isolation_second(db):
    # Previous test's TEMP table must not survive rollback.
    exists = db.execute(text("SELECT to_regclass('t_iso')")).scalar()
    assert exists is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_harness.py -v`
Expected: FAIL — old SQLite conftest, `version()` returns SQLite / import errors.

- [ ] **Step 3: Add `TEST_DATABASE_URL` to settings**

```python
# app/core/config.py  (inside Settings, after DATABASE_URL block)
    TEST_DATABASE_URL: Optional[str] = None  # e.g. postgresql://.../cofoundaz_test
```

- [ ] **Step 4: Replace `app/db/base.py` with a typed base + naming convention**

```python
# app/db/base.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

- [ ] **Step 5: Replace `tests/conftest.py` with a Postgres transaction-rollback harness**

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
import app.db.models  # noqa: F401  (registers all tables on Base.metadata)
from app.db.session import get_db
from app.main import app

TEST_URL = settings.TEST_DATABASE_URL or "postgresql://user:password@localhost:5433/cofoundaz_test"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL, pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine):
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
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

Note: `import app.db.models` will fail until Task 12 creates it — for this task, temporarily create an empty `app/db/models/__init__.py` (Task 12 fills it).

- [ ] **Step 6: Create empty models package so the import resolves**

```bash
mkdir -p app/db/models && touch app/db/models/__init__.py
```

- [ ] **Step 7: Ensure a test database exists**

The compose Postgres (service `db`) runs on host port **5433** with user/password
`user`/`password`, and `.env` already sets
`TEST_DATABASE_URL=postgresql://user:password@localhost:5433/cofoundaz_test`.
The `cofoundaz_test` database has been pre-created by the controller. If you need
to recreate it:
```bash
docker compose up -d db redis
docker exec cofoundaz-api_db psql -U user -d cofoundaz-api_db -c "CREATE DATABASE cofoundaz_test;" || true
```

- [ ] **Step 8: Run test to verify it passes**

Run: `poetry run pytest tests/test_harness.py -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Commit**

```bash
git add app/db/base.py app/core/config.py tests/conftest.py tests/test_harness.py app/db/models/__init__.py
git commit -m "test: real Postgres harness with per-test rollback + typed Base"
```

---

## Task 2: Model mixins

**Files:**
- Create: `app/db/mixins.py`
- Test: `tests/db/test_mixins.py`

**Interfaces:**
- Produces: `UUIDMixin` (`id: Mapped[UUID]` PK, default uuid4), `TimestampMixin` (`created_at`, `updated_at`), `SoftDeleteMixin` (`deleted_at: Mapped[datetime|None]`).

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_mixins.py
import uuid
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin, SoftDeleteMixin


class _Sample(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "sample_mixin_rows"
    label: Mapped[str] = mapped_column()


def test_mixins_populate_defaults(db):
    row = _Sample(label="x")
    db.add(row); db.flush(); db.refresh(row)
    assert isinstance(row.id, uuid.UUID)
    assert isinstance(row.created_at, datetime)
    assert isinstance(row.updated_at, datetime)
    assert row.deleted_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_mixins.py -v`
Expected: FAIL — `app.db.mixins` missing.

- [ ] **Step 3: Implement the mixins**

```python
# app/db/mixins.py
import uuid
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/db/test_mixins.py -v`
Expected: PASS. (The temp `_Sample` table is created by the session-scoped `create_all`; acceptable for foundation. Remove the class after Task 12 if it lingers — it's test-only.)

- [ ] **Step 5: Commit**

```bash
git add app/db/mixins.py tests/db/test_mixins.py
git commit -m "feat(db): UUID/Timestamp/SoftDelete model mixins"
```

---

## Task 3: Config & settings additions

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces on `settings`: `ACCESS_TOKEN_EXPIRE_MINUTES=15`, `REFRESH_TOKEN_EXPIRE_DAYS=30`, `REFRESH_COOKIE_NAME`, `REFRESH_COOKIE_SECURE`, `REFRESH_COOKIE_SAMESITE`, `MFA_ENCRYPTION_KEY`, `LOGIN_MAX_FAILS=5`, `LOGIN_LOCKOUT_MINUTES=15`, `EMAIL_BACKEND`, `STORAGE_BACKEND`, `LOCAL_STORAGE_DIR`, `RATE_LIMIT_PER_MINUTE=120`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from app.core.config import settings

def test_auth_defaults():
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 30
    assert settings.LOGIN_MAX_FAILS == 5
    assert settings.LOGIN_LOCKOUT_MINUTES == 15
    assert settings.REFRESH_COOKIE_SAMESITE == "lax"
    assert settings.RATE_LIMIT_PER_MINUTE == 120
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_config.py -v`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Add the settings**

```python
# app/core/config.py  (inside Settings)
    # Auth / sessions
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_COOKIE_NAME: str = "cfz_refresh"
    REFRESH_COOKIE_SECURE: bool = True
    REFRESH_COOKIE_SAMESITE: str = "lax"
    LOGIN_MAX_FAILS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15
    MFA_ENCRYPTION_KEY: Optional[str] = None  # 32-byte urlsafe base64 (Fernet)

    # Providers
    EMAIL_BACKEND: str = "console"   # console | smtp
    STORAGE_BACKEND: str = "local"   # local
    LOCAL_STORAGE_DIR: str = "./var/storage"

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 120
```

Also change the existing `ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7` line — remove it (replaced above).

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/test_config.py
git commit -m "feat(config): auth/session/provider/rate-limit settings"
```

---

## Task 4: Response envelope

**Files:**
- Create: `app/core/envelope.py`
- Test: `tests/core/test_envelope.py`

**Interfaces:**
- Produces: `Meta` (pydantic model: `next_cursor: str|None`, `total_estimate: int|None`), `success_response(data, meta=None) -> dict`, `error_response(code, message, field_errors=None) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_envelope.py
from app.core.envelope import success_response, error_response, Meta

def test_success_shape():
    assert success_response({"a": 1}) == {"data": {"a": 1}, "meta": None}

def test_success_with_meta():
    out = success_response([1, 2], Meta(next_cursor="abc", total_estimate=9))
    assert out["meta"]["next_cursor"] == "abc"
    assert out["meta"]["total_estimate"] == 9

def test_error_shape():
    out = error_response("EMAIL_TAKEN", "taken", [{"field": "email", "message": "taken"}])
    assert out == {"error": {"code": "EMAIL_TAKEN", "message": "taken",
                             "field_errors": [{"field": "email", "message": "taken"}]}}

def test_error_defaults_empty_field_errors():
    assert error_response("X", "y")["error"]["field_errors"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/core/test_envelope.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the envelope**

```python
# app/core/envelope.py
from typing import Any, Optional
from pydantic import BaseModel


class Meta(BaseModel):
    next_cursor: Optional[str] = None
    total_estimate: Optional[int] = None


def success_response(data: Any, meta: Optional[Meta] = None) -> dict:
    return {"data": data, "meta": meta.model_dump() if meta else None}


def error_response(code: str, message: str, field_errors: Optional[list[dict]] = None) -> dict:
    return {"error": {"code": code, "message": message, "field_errors": field_errors or []}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/core/test_envelope.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/envelope.py tests/core/test_envelope.py
git commit -m "feat(core): response envelope helpers"
```

---

## Task 5: Error framework + handlers

**Files:**
- Create: `app/core/errors.py`
- Modify: `app/main.py`
- Test: `tests/core/test_errors.py`

**Interfaces:**
- Produces: `AppError(code, message, http_status=400, field_errors=None)` base; subclasses `EmailTaken`, `WeakPassword`, `InvalidCredentials`, `AccountLocked`, `TokenInvalid`, `MfaInvalidCode`, `FeatureNotEnabled`, `Forbidden`, `NotFound`; `register_exception_handlers(app)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_errors.py
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel
from app.core.errors import AppError, EmailTaken, register_exception_handlers


def _app():
    app = FastAPI()
    register_exception_handlers(app)
    r = APIRouter()

    class Body(BaseModel):
        n: int

    @r.get("/boom")
    def boom():
        raise EmailTaken()

    @r.get("/generic")
    def generic():
        raise AppError("CUSTOM", "nope", http_status=418)

    @r.post("/validate")
    def validate(body: Body):
        return {"ok": body.n}

    app.include_router(r)
    return app


def test_apperror_renders_envelope():
    c = TestClient(_app())
    resp = c.get("/boom")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_TAKEN"

def test_generic_apperror_status():
    resp = TestClient(_app()).get("/generic")
    assert resp.status_code == 418
    assert resp.json()["error"]["code"] == "CUSTOM"

def test_validation_error_remapped_to_field_errors():
    resp = TestClient(_app()).post("/validate", json={"n": "notint"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["field_errors"][0]["field"] == "n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/core/test_errors.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement errors + handlers**

```python
# app/core/errors.py
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.envelope import error_response


class AppError(Exception):
    code = "APP_ERROR"
    message = "Something went wrong."
    http_status = 400

    def __init__(self, code=None, message=None, http_status=None, field_errors=None):
        self.code = code or self.code
        self.message = message or self.message
        self.http_status = http_status or self.http_status
        self.field_errors = field_errors or []
        super().__init__(self.message)


class EmailTaken(AppError):
    code, http_status = "EMAIL_TAKEN", 409
    message = "That email already has an account — log in instead?"

class WeakPassword(AppError):
    code, http_status = "WEAK_PASSWORD", 422
    message = "Add a number and make it at least 8 characters."

class InvalidCredentials(AppError):
    code, http_status = "INVALID_CREDENTIALS", 401
    message = "That email and password don't match."

class AccountLocked(AppError):
    code, http_status = "ACCOUNT_LOCKED", 429
    message = "Too many attempts. Try again in a few minutes or reset your password."

class TokenInvalid(AppError):
    code, http_status = "TOKEN_INVALID", 400
    message = "That link is invalid or has expired."

class MfaInvalidCode(AppError):
    code, http_status = "MFA_INVALID_CODE", 401
    message = "That code isn't right. Try again."

class FeatureNotEnabled(AppError):
    code, http_status = "FEATURE_NOT_ENABLED", 501
    message = "This feature isn't available yet."

class Forbidden(AppError):
    code, http_status = "FORBIDDEN", 403
    message = "You don't have permission to do that."

class NotFound(AppError):
    code, http_status = "NOT_FOUND", 404
    message = "Not found."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(exc.code, exc.message, exc.field_errors),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        field_errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_response("VALIDATION_ERROR", "Please check the highlighted fields.", field_errors),
        )
```

- [ ] **Step 4: Wire handlers into the app**

```python
# app/main.py  (after `app = FastAPI(...)` block, before routers)
from app.core.errors import register_exception_handlers
register_exception_handlers(app)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/core/test_errors.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/core/errors.py app/main.py tests/core/test_errors.py
git commit -m "feat(core): AppError hierarchy + envelope exception handlers"
```

---

## Task 6: Cursor pagination

**Files:**
- Create: `app/core/pagination.py`
- Test: `tests/core/test_pagination.py`

**Interfaces:**
- Produces: `encode_cursor(created_at: datetime, id: UUID) -> str`, `decode_cursor(str) -> tuple[datetime, UUID]`, `CursorPage` dataclass `(items, next_cursor, total_estimate)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_pagination.py
import uuid
from datetime import datetime, timezone
from app.core.pagination import encode_cursor, decode_cursor

def test_cursor_roundtrip():
    ts = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    rid = uuid.uuid4()
    token = encode_cursor(ts, rid)
    assert isinstance(token, str)
    ts2, rid2 = decode_cursor(token)
    assert ts2 == ts and rid2 == rid

def test_bad_cursor_raises():
    import pytest
    with pytest.raises(ValueError):
        decode_cursor("not-base64!!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/core/test_pagination.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement pagination**

```python
# app/core/pagination.py
import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class CursorPage:
    items: list[Any]
    next_cursor: Optional[str]
    total_estimate: Optional[int]


def encode_cursor(created_at: datetime, id: uuid.UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "i": str(id)}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        data = json.loads(raw)
        return datetime.fromisoformat(data["t"]), uuid.UUID(data["i"])
    except Exception as exc:
        raise ValueError("Invalid cursor") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/core/test_pagination.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/pagination.py tests/core/test_pagination.py
git commit -m "feat(core): opaque cursor pagination helpers"
```

---

## Task 7: EmailSender seam (console + SMTP)

**Files:**
- Create: `app/platform/__init__.py`, `app/platform/email.py`
- Create: `app/templates/email/.gitkeep`
- Test: `tests/platform/test_email.py`

**Interfaces:**
- Produces: `EmailMessage(to, subject, html)` dataclass; `EmailSender` Protocol with `send(msg: EmailMessage) -> None`; `ConsoleEmailSender` (records to `.sent` list, logs); `SMTPEmailSender`; `get_email_sender()` factory keyed on `settings.EMAIL_BACKEND`.

- [ ] **Step 1: Write the failing test**

```python
# tests/platform/test_email.py
from app.platform.email import EmailMessage, ConsoleEmailSender, get_email_sender

def test_console_sender_records():
    s = ConsoleEmailSender()
    s.send(EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>"))
    assert s.sent[-1].to == "a@b.com"
    assert s.sent[-1].subject == "Hi"

def test_factory_returns_console_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "console")
    assert isinstance(get_email_sender(), ConsoleEmailSender)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/platform/test_email.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the email seam**

```python
# app/platform/email.py
from dataclasses import dataclass, field
from typing import Protocol
from app.core.config import settings
from app.core.logger import log


@dataclass
class EmailMessage:
    to: str
    subject: str
    html: str


class EmailSender(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


class ConsoleEmailSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)
        log.info(f"[email:console] to={msg.to} subject={msg.subject!r}\n{msg.html}")


class SMTPEmailSender:
    def send(self, msg: EmailMessage) -> None:
        import emails  # lazy import
        m = emails.Message(
            subject=msg.subject, html=msg.html,
            mail_from=(settings.EMAILS_FROM_NAME, settings.EMAILS_FROM_EMAIL),
        )
        m.send(
            to=msg.to,
            smtp={"host": settings.SMTP_HOST, "port": settings.SMTP_PORT,
                  "tls": settings.SMTP_TLS, "user": settings.SMTP_USER,
                  "password": settings.SMTP_PASSWORD},
        )


def get_email_sender() -> EmailSender:
    if settings.EMAIL_BACKEND == "smtp":
        return SMTPEmailSender()
    return ConsoleEmailSender()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/platform/test_email.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/platform/__init__.py app/platform/email.py tests/platform/test_email.py app/templates
git commit -m "feat(platform): EmailSender seam (console + smtp)"
```

---

## Task 8: Storage seam (local FS)

**Files:**
- Create: `app/platform/storage.py`
- Test: `tests/platform/test_storage.py`

**Interfaces:**
- Produces: `Storage` Protocol `save(key: str, content: bytes, content_type: str) -> str` (returns URL/path); `LocalStorage`; `get_storage()` factory.

- [ ] **Step 1: Write the failing test**

```python
# tests/platform/test_storage.py
from app.platform.storage import LocalStorage

def test_local_storage_saves_and_returns_path(tmp_path):
    s = LocalStorage(base_dir=str(tmp_path))
    url = s.save("logos/x.png", b"bytes", "image/png")
    assert url.endswith("logos/x.png")
    assert (tmp_path / "logos" / "x.png").read_bytes() == b"bytes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/platform/test_storage.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement storage**

```python
# app/platform/storage.py
import os
from pathlib import Path
from typing import Protocol
from app.core.config import settings


class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> str: ...


class LocalStorage:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir or settings.LOCAL_STORAGE_DIR

    def save(self, key: str, content: bytes, content_type: str) -> str:
        path = Path(self.base_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)


def get_storage() -> Storage:
    return LocalStorage()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/platform/test_storage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/platform/storage.py tests/platform/test_storage.py
git commit -m "feat(platform): Storage seam (local filesystem)"
```

---

## Task 9: EventBus + AIPanel seams

**Files:**
- Create: `app/platform/events.py`, `app/platform/ai.py`
- Test: `tests/platform/test_events.py`, `tests/platform/test_ai.py`

**Interfaces:**
- Produces: `EventBus` Protocol `publish(event: str, payload: dict) -> None`; `LogEventBus` (records `.published`); `event_bus` singleton. `AIPanel` Protocol `reply(context: dict) -> str`; `StubAIPanel` (canned string); `ai_panel` singleton.

- [ ] **Step 1: Write the failing tests**

```python
# tests/platform/test_events.py
from app.platform.events import LogEventBus

def test_logbus_records_events():
    bus = LogEventBus()
    bus.publish("auth.user.registered", {"user_id": "u1"})
    assert bus.published[-1] == ("auth.user.registered", {"user_id": "u1"})
```

```python
# tests/platform/test_ai.py
from app.platform.ai import StubAIPanel

def test_stub_ai_returns_canned_reply():
    out = StubAIPanel().reply({"industry": "fintech", "stage": "idea"})
    assert isinstance(out, str) and len(out) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/platform/test_events.py tests/platform/test_ai.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement seams**

```python
# app/platform/events.py
from typing import Protocol
from app.core.logger import log


class EventBus(Protocol):
    def publish(self, event: str, payload: dict) -> None: ...


class LogEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event: str, payload: dict) -> None:
        self.published.append((event, payload))
        log.info(f"[event] {event} {payload}")


event_bus: EventBus = LogEventBus()
```

```python
# app/platform/ai.py
from typing import Protocol


class AIPanel(Protocol):
    def reply(self, context: dict) -> str: ...


class StubAIPanel:
    def reply(self, context: dict) -> str:
        stage = context.get("stage", "your")
        industry = context.get("industry", "startup")
        return f"Got it — a {industry} startup at the {stage} stage. Let's calibrate your workspace."


ai_panel: AIPanel = StubAIPanel()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/platform/test_events.py tests/platform/test_ai.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/platform/events.py app/platform/ai.py tests/platform/test_events.py tests/platform/test_ai.py
git commit -m "feat(platform): EventBus + AIPanel stub seams"
```

---

## Task 10: Jobs table + JobDispatcher stub + read endpoint

**Files:**
- Create: `app/db/models/job.py`, `app/platform/jobs.py`, `app/api/v1/endpoints/jobs.py`
- Modify: `app/db/models/__init__.py`, `app/api/v1/api.py`
- Test: `tests/platform/test_jobs.py`, `tests/api/test_jobs_endpoint.py`

**Interfaces:**
- Produces: `Job` model (`type`, `status` [JobStatus enum], `startup_id`, `payload`, `result`, `error`); `JobStatus` enum `queued|running|succeeded|failed|cancelled`; `JobDispatcher.enqueue(db, type, payload, startup_id=None) -> Job` (writes a `queued` row); `GET /api/v1/jobs/{job_id}` returning enveloped job.

- [ ] **Step 1: Write the failing tests**

```python
# tests/platform/test_jobs.py
from app.platform.jobs import JobDispatcher
from app.db.models.job import JobStatus

def test_enqueue_writes_queued_row(db):
    job = JobDispatcher().enqueue(db, "roadmap.generate", {"startup_id": "s1"})
    db.flush()
    assert job.status == JobStatus.queued
    assert job.type == "roadmap.generate"
    assert job.payload == {"startup_id": "s1"}
```

```python
# tests/api/test_jobs_endpoint.py
from app.platform.jobs import JobDispatcher

def test_get_job_returns_envelope(client, db):
    job = JobDispatcher().enqueue(db, "healthscore.initialize", {})
    db.commit()
    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "queued"
    assert body["data"]["type"] == "healthscore.initialize"

def test_get_missing_job_404(client):
    import uuid
    resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/platform/test_jobs.py tests/api/test_jobs_endpoint.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement the Job model**

```python
# app/db/models/job.py
import enum
import uuid
from typing import Any, Optional
from sqlalchemy import Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class Job(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, native_enum=False, length=20), default=JobStatus.queued, nullable=False
    )
    startup_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Implement the dispatcher**

```python
# app/platform/jobs.py
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from app.db.models.job import Job, JobStatus


class JobDispatcher:
    """v1 stub: persists a queued row. A real worker drains it in Modules 05/06."""

    def enqueue(self, db: Session, type: str, payload: dict,
                startup_id: Optional[uuid.UUID] = None) -> Job:
        job = Job(type=type, payload=payload, startup_id=startup_id, status=JobStatus.queued)
        db.add(job)
        db.flush()
        return job


job_dispatcher = JobDispatcher()
```

- [ ] **Step 5: Register model + implement endpoint + mount router**

```python
# app/db/models/__init__.py
from app.db.models.job import Job  # noqa: F401
```

```python
# app/api/v1/endpoints/jobs.py
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models.job import Job
from app.core.envelope import success_response
from app.core.errors import NotFound

router = APIRouter()


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise NotFound()
    return success_response({
        "id": str(job.id), "type": job.type, "status": job.status.value,
        "result": job.result, "error": job.error,
    })
```

```python
# app/api/v1/api.py  (add)
from app.api.v1.endpoints import jobs
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/platform/test_jobs.py tests/api/test_jobs_endpoint.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/db/models/job.py app/platform/jobs.py app/api/v1/endpoints/jobs.py app/db/models/__init__.py app/api/v1/api.py tests/platform/test_jobs.py tests/api/test_jobs_endpoint.py
git commit -m "feat(platform): jobs table + stub dispatcher + GET /jobs/{id}"
```

---

## Task 11: Audit log + helper

**Files:**
- Create: `app/db/models/audit.py`, `app/platform/audit.py`
- Modify: `app/db/models/__init__.py`
- Test: `tests/platform/test_audit.py`

**Interfaces:**
- Produces: `AuditLog` model; `write_audit(db, action, *, actor_user_id=None, on_behalf_of_user_id=None, startup_id=None, entity_type=None, entity_id=None, before_hash=None, after_hash=None, ip=None, user_agent=None) -> AuditLog`.

- [ ] **Step 1: Write the failing test**

```python
# tests/platform/test_audit.py
from app.platform.audit import write_audit

def test_write_audit_persists(db):
    row = write_audit(db, "auth.login.success", ip="1.2.3.4")
    db.flush()
    assert row.action == "auth.login.success"
    assert row.ip == "1.2.3.4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/platform/test_audit.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement model**

```python
# app/db/models/audit.py
import uuid
from typing import Optional
from sqlalchemy import String, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin


class AuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audit_log"

    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    on_behalf_of_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    startup_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    before_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_audit_log_startup_created", "startup_id", "created_at"),
        Index("ix_audit_log_actor_created", "actor_user_id", "created_at"),
    )
```

- [ ] **Step 4: Implement helper + register model**

```python
# app/platform/audit.py
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from app.db.models.audit import AuditLog


def write_audit(db: Session, action: str, *,
                actor_user_id: Optional[uuid.UUID] = None,
                on_behalf_of_user_id: Optional[uuid.UUID] = None,
                startup_id: Optional[uuid.UUID] = None,
                entity_type: Optional[str] = None,
                entity_id: Optional[uuid.UUID] = None,
                before_hash: Optional[str] = None,
                after_hash: Optional[str] = None,
                ip: Optional[str] = None,
                user_agent: Optional[str] = None) -> AuditLog:
    row = AuditLog(
        action=action, actor_user_id=actor_user_id, on_behalf_of_user_id=on_behalf_of_user_id,
        startup_id=startup_id, entity_type=entity_type, entity_id=entity_id,
        before_hash=before_hash, after_hash=after_hash, ip=ip, user_agent=user_agent,
    )
    db.add(row)
    db.flush()
    return row
```

```python
# app/db/models/__init__.py  (add)
from app.db.models.audit import AuditLog  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/platform/test_audit.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/db/models/audit.py app/platform/audit.py app/db/models/__init__.py tests/platform/test_audit.py
git commit -m "feat(platform): audit_log model + write_audit helper"
```

---

## Task 12: Tenancy spine models + enums + factories

**Files:**
- Create: `app/db/models/enums.py`, `app/db/models/user.py`, `app/db/models/startup.py`, `app/db/models/membership.py`
- Modify: `app/db/models/__init__.py`
- Create: `tests/factories.py`
- Test: `tests/db/test_tenancy_models.py`

**Interfaces:**
- Produces:
  - `UserStatus` (`pending_verification|active|locked|disabled`), `MfaType` (`none|totp|sms`), `BusinessModel`, `StartupStage`, `MembershipRole` (`founder|team_member|mentor|accountant|legal_advisor|business_consultant|investor`), `MembershipStatus` (`active|suspended|removed`).
  - `User` (email CITEXT unique, password_hash nullable, status, email_verified_at, mfa_type, mfa_secret, mfa_enabled_at, failed_login_count, locked_until, last_login_at) + `UserProfile` (1:1).
  - `Startup` + `StartupProfile` (goals `ARRAY(String)`, onboarding_step, onboarding_completed_at).
  - `Membership` (unique `(user_id, startup_id)`).
  - Factories: `create_user(db, **kw) -> User`, `create_startup(db, owner: User, **kw) -> Startup`, `create_membership(db, user, startup, role=MembershipRole.founder) -> Membership`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_tenancy_models.py
import pytest
from sqlalchemy.exc import IntegrityError
from app.db.models.enums import MembershipRole, UserStatus
from tests.factories import create_user, create_startup, create_membership

def test_user_defaults(db):
    u = create_user(db, email="a@b.com")
    assert u.status == UserStatus.pending_verification
    assert u.failed_login_count == 0

def test_membership_unique_per_workspace(db):
    u = create_user(db, email="c@d.com")
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    create_membership(db, u, s, role=MembershipRole.team_member)
    with pytest.raises(IntegrityError):
        db.flush()

def test_startup_profile_goals_array(db):
    u = create_user(db, email="e@f.com")
    s = create_startup(db, owner=u)
    s.profile.goals = ["Validate my idea", "Get first customers"]
    db.flush()
    db.refresh(s.profile)
    assert s.profile.goals == ["Validate my idea", "Get first customers"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_tenancy_models.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Enable `citext` + implement enums**

`citext` needs the Postgres extension. Add to `app/db/base.py` bootstrap for tests via a conftest hook, OR use `String` for email with a case-insensitive unique index. **Decision: use `citext`** — add an event that creates the extension in the test `engine` fixture:

```python
# tests/conftest.py  (inside engine fixture, before create_all)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
```

```python
# app/db/models/enums.py
import enum

class UserStatus(str, enum.Enum):
    pending_verification = "pending_verification"
    active = "active"
    locked = "locked"
    disabled = "disabled"

class MfaType(str, enum.Enum):
    none = "none"
    totp = "totp"
    sms = "sms"

class BusinessModel(str, enum.Enum):
    b2b = "b2b"; b2c = "b2c"; b2b2c = "b2b2c"
    marketplace = "marketplace"; hardware = "hardware"; services = "services"

class StartupStage(str, enum.Enum):
    idea = "idea"; validation = "validation"; build = "build"
    launch = "launch"; growth = "growth"; scale = "scale"

class MembershipRole(str, enum.Enum):
    founder = "founder"
    team_member = "team_member"
    mentor = "mentor"
    accountant = "accountant"
    legal_advisor = "legal_advisor"
    business_consultant = "business_consultant"
    investor = "investor"

class MembershipStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    removed = "removed"
```

- [ ] **Step 4: Implement User + UserProfile**

```python
# app/db/models/user.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Enum as SAEnum, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import CITEXT, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin, SoftDeleteMixin
from app.db.models.enums import UserStatus, MfaType


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, native_enum=False, length=30),
        default=UserStatus.pending_verification, nullable=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_type: Mapped[MfaType] = mapped_column(
        SAEnum(MfaType, native_enum=False, length=10), default=MfaType.none, nullable=False)
    mfa_secret: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mfa_enabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False,
                                                   cascade="all, delete-orphan")


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    full_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role_title: Mapped[str] = mapped_column(String, default="Founder & CEO", nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    how_heard: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    user: Mapped["User"] = relationship(back_populates="profile")
```

- [ ] **Step 5: Implement Startup + StartupProfile**

```python
# app/db/models/startup.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Enum as SAEnum, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin, SoftDeleteMixin
from app.db.models.enums import BusinessModel, StartupStage


class Startup(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "startups"

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    business_model: Mapped[Optional[BusinessModel]] = mapped_column(
        SAEnum(BusinessModel, native_enum=False, length=20), nullable=True)
    stage: Mapped[Optional[StartupStage]] = mapped_column(
        SAEnum(StartupStage, native_enum=False, length=20), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    profile: Mapped["StartupProfile"] = relationship(back_populates="startup", uselist=False,
                                                     cascade="all, delete-orphan")


class StartupProfile(TimestampMixin, Base):
    __tablename__ = "startup_profiles"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), primary_key=True)
    goals: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    startup: Mapped["Startup"] = relationship(back_populates="profile")
```

- [ ] **Step 6: Implement Membership + register all models**

```python
# app/db/models/membership.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Enum as SAEnum, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin
from app.db.models.enums import MembershipRole, MembershipStatus


class Membership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        SAEnum(MembershipRole, native_enum=False, length=30), nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        SAEnum(MembershipStatus, native_enum=False, length=20),
        default=MembershipStatus.active, nullable=False)
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "startup_id", name="uq_memberships_user_id_startup_id"),
        Index("ix_memberships_startup_id", "startup_id"),
    )
```

```python
# app/db/models/__init__.py  (append)
from app.db.models.user import User, UserProfile  # noqa: F401
from app.db.models.startup import Startup, StartupProfile  # noqa: F401
from app.db.models.membership import Membership  # noqa: F401
```

- [ ] **Step 7: Implement factories**

```python
# tests/factories.py
import uuid
from sqlalchemy.orm import Session
from app.db.models.user import User, UserProfile
from app.db.models.startup import Startup, StartupProfile
from app.db.models.membership import Membership
from app.db.models.enums import MembershipRole, MembershipStatus


def create_user(db: Session, *, email: str | None = None, **kw) -> User:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(email=email, **kw)
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    return user


def create_startup(db: Session, *, owner: User, name: str = "Acme", **kw) -> Startup:
    s = Startup(name=name, created_by=owner.id, **kw)
    s.profile = StartupProfile()
    db.add(s)
    db.flush()
    return s


def create_membership(db: Session, user: User, startup: Startup,
                      role: MembershipRole = MembershipRole.founder) -> Membership:
    m = Membership(user_id=user.id, startup_id=startup.id, role=role,
                   status=MembershipStatus.active)
    db.add(m)
    return m
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `poetry run pytest tests/db/test_tenancy_models.py -v`
Expected: PASS (3 tests). (Re-run the full suite: `poetry run pytest -q`.)

- [ ] **Step 9: Commit**

```bash
git add app/db/models/ tests/factories.py tests/db/test_tenancy_models.py tests/conftest.py
git commit -m "feat(db): tenancy spine models (user/startup/membership) + factories"
```

---

## Task 13: Initial Alembic migration

**Files:**
- Modify: `alembic/env.py`
- Create: `alembic/versions/0001_initial_schema.py` (autogenerated)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: a migration that materializes every Base table; `alembic upgrade head` runs clean on an empty DB.

- [ ] **Step 1: Point Alembic at the models**

```python
# alembic/env.py  (replace the "Import all models here" comment block)
from app.db.base import Base
import app.db.models  # noqa: F401  (registers all tables)
target_metadata = Base.metadata
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_migrations.py
import subprocess

def test_alembic_upgrade_head_runs():
    # Requires a throwaway DB URL via env; see step 4.
    result = subprocess.run(["poetry", "run", "alembic", "upgrade", "head"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/test_migrations.py -v`
Expected: FAIL — no migration exists yet.

- [ ] **Step 4: Autogenerate the migration**

Ensure the citext extension is available in the migration. Create it explicitly, then generate:
```bash
poetry run alembic revision --autogenerate -m "initial schema" --rev-id 0001_initial_schema
```
Then **edit the generated migration**: add `op.execute("CREATE EXTENSION IF NOT EXISTS citext")` as the first line of `upgrade()`, before table creation. Verify the tables (`users`, `user_profiles`, `startups`, `startup_profiles`, `memberships`, `jobs`, `audit_log`) all appear.

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 6: Verify autogenerate is now clean (no drift)**

Run: `poetry run alembic revision --autogenerate -m "verify no drift" --rev-id _tmp`
Expected: the generated file's `upgrade()` is empty (`pass`). **Delete that temp file.** If not empty, reconcile models vs migration.

- [ ] **Step 7: Commit**

```bash
git add alembic/env.py alembic/versions/0001_initial_schema.py tests/test_migrations.py
git commit -m "feat(db): initial Alembic migration for foundation schema"
```

---

## Task 14: Real `get_current_user` dependency

**Files:**
- Modify: `app/api/deps.py`
- Test: `tests/api/test_current_user.py`

**Interfaces:**
- Consumes: `create_access_token` (existing in `app/core/security.py`), `User` model.
- Produces: `get_current_user(...) -> User` (401 on bad/missing/expired token or unknown user); `get_optional_user(...) -> User | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_current_user.py
from datetime import timedelta
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from app.api.deps import get_current_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.db.models.user import User
from tests.factories import create_user


def _mini_app():
    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):
        return {"email": user.email}

    return app


def test_valid_token_resolves_user(db):
    user = create_user(db, email="who@ami.com")
    db.commit()
    app = _mini_app()
    app.dependency_overrides[get_db] = lambda: db
    token = create_access_token(str(user.id), expires_delta=timedelta(minutes=5))
    resp = TestClient(app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "who@ami.com"


def test_missing_user_401(db):
    import uuid
    app = _mini_app()
    app.dependency_overrides[get_db] = lambda: db
    token = create_access_token(str(uuid.uuid4()))
    resp = TestClient(app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/test_current_user.py -v`
Expected: FAIL — current stub returns a dict, has no `.email`.

- [ ] **Step 3: Replace the stub with a DB-backed resolver**

```python
# app/api/deps.py
import uuid
from typing import Optional
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import get_db
from app.db.models.user import User

security = HTTPBearer(auto_error=False)


class Unauthorized(AppError):
    code, http_status, message = "UNAUTHORIZED", 401, "Not authenticated."


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise Unauthorized()
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY,
                             algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise Unauthorized()
    except JWTError:
        raise Unauthorized()
    user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    if user is None:
        raise Unauthorized()
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if credentials is None:
        return None
    try:
        return get_current_user(credentials, db)
    except AppError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/test_current_user.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/deps.py tests/api/test_current_user.py
git commit -m "feat(auth): DB-backed get_current_user dependency"
```

---

## Task 15: RBAC + tenancy resolution

**Files:**
- Create: `app/core/redis.py`, `app/db/tenancy.py`
- Test: `tests/db/test_tenancy.py`

**Interfaces:**
- Consumes: `get_current_user`, `Membership`, `Startup`.
- Produces:
  - `get_redis()` — Redis client from `settings.REDIS_URL`.
  - `resolve_workspace(db, user, workspace_id) -> Membership` — validates the user has an active membership in `workspace_id`; raises `Forbidden` otherwise. (FastAPI dependency `require_workspace` reads `X-Workspace-Id`.)
  - `require_role(*roles)` — FastAPI dependency factory that 403s unless the resolved membership role is in `roles`.
  - `tenant_scope(query, startup_id, model)` — applies `.filter(model.startup_id == startup_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_tenancy.py
import uuid
import pytest
from app.db.tenancy import resolve_workspace
from app.core.errors import Forbidden
from app.db.models.enums import MembershipRole
from tests.factories import create_user, create_startup, create_membership


def test_resolve_workspace_ok(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    m = resolve_workspace(db, u, s.id)
    assert m.role == MembershipRole.founder


def test_resolve_workspace_forbidden_for_non_member(db):
    u = create_user(db)
    other = create_user(db)
    s = create_startup(db, owner=other)
    create_membership(db, other, s, role=MembershipRole.founder)
    db.flush()
    with pytest.raises(Forbidden):
        resolve_workspace(db, u, s.id)


def test_resolve_workspace_forbidden_for_unknown_workspace(db):
    u = create_user(db)
    db.flush()
    with pytest.raises(Forbidden):
        resolve_workspace(db, u, uuid.uuid4())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_tenancy.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement Redis client**

```python
# app/core/redis.py
import redis
from app.core.config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
```

- [ ] **Step 4: Implement tenancy + RBAC**

```python
# app/db/tenancy.py
import uuid
from fastapi import Depends, Header
from sqlalchemy.orm import Query, Session
from app.api.deps import get_current_user
from app.core.errors import Forbidden
from app.db.session import get_db
from app.db.models.user import User
from app.db.models.membership import Membership, MembershipStatus


def resolve_workspace(db: Session, user: User, workspace_id: uuid.UUID) -> Membership:
    m = (db.query(Membership)
         .filter(Membership.user_id == user.id,
                 Membership.startup_id == workspace_id,
                 Membership.status == MembershipStatus.active)
         .first())
    if m is None:
        raise Forbidden()
    return m


def require_workspace(
    x_workspace_id: uuid.UUID = Header(..., alias="X-Workspace-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Membership:
    return resolve_workspace(db, user, x_workspace_id)


def require_role(*roles):
    def _dep(membership: Membership = Depends(require_workspace)) -> Membership:
        if roles and membership.role not in roles:
            raise Forbidden()
        return membership
    return _dep


def tenant_scope(query: Query, startup_id: uuid.UUID, model) -> Query:
    return query.filter(model.startup_id == startup_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/db/test_tenancy.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/core/redis.py app/db/tenancy.py tests/db/test_tenancy.py
git commit -m "feat(platform): RBAC role gate + workspace tenancy resolution"
```

---

## Task 16: Rate limiting + coverage gate

**Files:**
- Modify: `app/main.py`, `pyproject.toml`
- Test: `tests/api/test_rate_limit.py`

**Interfaces:**
- Consumes: `slowapi` (already a dep).
- Produces: a configured `Limiter` on the app (`RATE_LIMIT_PER_MINUTE`/min default) rendering `429` in the envelope shape via the error handler.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_rate_limit.py
from app.main import app

def test_limiter_registered():
    assert getattr(app.state, "limiter", None) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/test_rate_limit.py -v`
Expected: FAIL — no limiter on app.state.

- [ ] **Step 3: Wire slowapi + a 429 envelope handler**

```python
# app/main.py  (add near handler registration)
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse
from app.core.envelope import error_response
from app.core.config import settings

limiter = Limiter(key_func=get_remote_address,
                  default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def _rate_limit(_, exc):
    return JSONResponse(
        status_code=429,
        content=error_response("RATE_LIMITED", "Too many requests. Slow down a moment."),
        headers={"Retry-After": "60"},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/test_rate_limit.py -v`
Expected: PASS.

- [ ] **Step 5: Add coverage config**

```toml
# pyproject.toml  (append)
[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-q"

[tool.coverage.run]
source = ["app"]
omit = ["app/main.py", "alembic/*"]
```

- [ ] **Step 6: Run the full suite with coverage**

Run: `poetry run pytest --cov=app --cov-report=term`
Expected: all tests PASS; note the coverage % (gate enforcement lands with the auth plan once there are endpoints to cover).

- [ ] **Step 7: Commit**

```bash
git add app/main.py pyproject.toml tests/api/test_rate_limit.py
git commit -m "feat(platform): slowapi rate limiting + coverage config"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §3 architecture → Tasks 1–2, 12, 15. §4 platform table → envelope(4), errors(5), pagination(6), idempotency (deferred to auth plan — it's only exercised by side-effectful POSTs), RBAC(15), tenancy(15), jobs/events/ai(9,10), rate-limit(16), audit(11). §5 data model → the tenancy spine (12) + jobs/audit (10,11); `auth_sessions`, `auth_tokens`, `mfa_backup_codes`, `oauth_accounts`, `invitations` are **auth/onboarding-plan tables**, intentionally deferred. §8 testing → Task 1 harness; the security-case tests (lockout, refresh reuse, no-enumeration) live in the auth plan where those flows exist. §9 config → Task 3.
- **Deferred to later plans (by design):** `Idempotency-Key` middleware (auth plan), Fernet mfa_secret encryption helper (auth plan, needs `MFA_ENCRYPTION_KEY`), all auth/onboarding endpoints.
- **Type consistency:** `success_response`/`error_response`, `AppError` subclasses, `JobDispatcher.enqueue`, `write_audit`, `resolve_workspace`/`require_role`/`tenant_scope`, factory signatures are referenced consistently across tasks.
