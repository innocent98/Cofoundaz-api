# Auth Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Module 01 auth HTTP surface (`/api/v1/auth/*`) — signup, email verification, login with lockout, TOTP MFA + backup codes, refresh-token rotation, password reset, and `GET /me` — on top of the shipped Foundation & Tenancy Spine.

**Architecture:** Thin FastAPI routers under `app/api/v1/endpoints/auth/`, delegating to services in `app/services/auth/`. Refresh tokens are opaque, stored hashed in `auth_sessions` with a `family_id` for rotation/reuse-detection; access tokens are the existing short JWT. Dual transport: browsers get the refresh token in an httpOnly cookie, all clients get it in the body. MFA secrets are Fernet-encrypted at rest. OAuth/SMS remain 501 seams.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL, Redis (MFA ticket + resend throttle), python-jose (access JWT), passlib[bcrypt], **pyotp** (TOTP), **cryptography.Fernet** (MFA secret encryption), pytest + real Postgres.

## Global Constraints

- **Response envelope** (existing `app/core/envelope.py`): success `success_response(data, meta=None)`; error via raised `AppError` subclasses → `{ "error": { "code", "message", "field_errors" } }`. Endpoints return `success_response(...)`; never hand-roll the dict.
- **Error codes are fixed** (existing `app/core/errors.py`): `EmailTaken`(409), `WeakPassword`(422), `InvalidCredentials`(401), `AccountLocked`(429), `TokenInvalid`(400), `MfaInvalidCode`(401), `FeatureNotEnabled`(501), `Forbidden`(403), `NotFound`(404). Do NOT invent new codes where one of these fits; add a new `AppError` subclass only when no existing code matches, following the same class pattern (with `# noqa: N818`).
- **Verbatim copy** (PRD Module 01) — use these exact strings:
  - signup email-taken: `"That email already has an account — log in instead?"` (already the `EmailTaken` message)
  - weak password: `"Add a number and make it at least 8 characters."` (already `WeakPassword`)
  - login 401: `"That email and password don't match."` (already `InvalidCredentials`)
  - lockout: use `AccountLocked` message.
  - forgot-password confirmation: `"If that email has an account, a reset link is on its way."`
  - verify-resend: identical generic response regardless of whether the email exists.
- **Password policy:** min 8 chars AND at least one digit → else raise `WeakPassword`.
- **No user enumeration:** `/auth/password/forgot` and `/auth/verify/resend` return the SAME success response whether or not the email exists.
- **Tokens are never stored raw:** refresh tokens, verification/reset tokens, and MFA backup codes are stored as SHA-256 hashes. Passwords use the existing bcrypt `get_password_hash`.
- **Timestamps:** timezone-aware UTC (`datetime.now(UTC)`), never naive `utcnow()`.
- **PKs:** UUID v4 (`UUIDMixin`); every table has `TimestampMixin`. Enums via `Enum(PyEnum, native_enum=False, length=…)`.
- **TTLs from settings:** access `ACCESS_TOKEN_EXPIRE_MINUTES=15`, refresh `REFRESH_TOKEN_EXPIRE_DAYS=30`, lockout `LOGIN_MAX_FAILS=5` / `LOGIN_LOCKOUT_MINUTES=15`, cookie `REFRESH_COOKIE_NAME="cfz_refresh"`, `REFRESH_COOKIE_SECURE`, `REFRESH_COOKIE_SAMESITE="lax"`, `MFA_ENCRYPTION_KEY`.
- **FE contract** (`../cofoundaz/app/(auth)/` + `content/auth.ts`): `POST /auth/login` → `{ access_token, refresh_token, mfa_required? }`; `POST /auth/signup {email,password}` → `201 { user, verification_sent: true }`.
- **Do NOT add `Co-Authored-By` or any AI-attribution trailer to commit messages.**

Reference spec: `docs/superpowers/specs/2026-08-12-auth-onboarding-foundation-design.md` (Sections C, D, E).

### Existing Plan 1 interfaces (carry into every task brief)

```python
# app/core/security.py
create_access_token(subject: str, expires_delta: timedelta | None = None) -> str
verify_password(plain: str, hashed: str) -> bool
get_password_hash(password: str) -> str

# app/api/deps.py
get_current_user(credentials, db) -> User        # 401 Unauthorized if bad/missing/deleted
get_optional_user(credentials, db) -> User | None
class Unauthorized(AppError): code="UNAUTHORIZED", http_status=401

# app/core/errors.py  → AppError(code,message,http_status,field_errors); subclasses listed above
# app/core/envelope.py → success_response(data, meta=None), error_response(...), Meta
# app/platform/email.py → EmailMessage(to,subject,html); get_email_sender() -> EmailSender (.send(msg)); ConsoleEmailSender().sent (list, for tests)
# app/platform/audit.py → write_audit(db, action, *, actor_user_id=None, startup_id=None, ip=None, user_agent=None, ...)
# app/platform/events.py → event_bus.publish(event: str, payload: dict); event_bus.published (list, for tests)
# app/core/redis.py → get_redis() -> redis.Redis   (decode_responses=True)
# app/db/models/user.py → User(email, password_hash|None, status:UserStatus, email_verified_at, mfa_type:MfaType, mfa_secret|None, mfa_enabled_at, failed_login_count:int, locked_until, last_login_at), UserProfile(user_id PK, full_name, role_title, ...)
# app/db/models/enums.py → UserStatus(pending_verification|active|locked|disabled), MfaType(none|totp|sms), MembershipRole(...), MembershipStatus(...)
# app/db/models/membership.py → Membership(user_id, startup_id, role:MembershipRole, status:MembershipStatus)
# app/db/models/startup.py → Startup(id,name,created_by,...)
# tests/factories.py → create_user(db, *, email=None, **kw) -> User; create_startup(db, *, owner, name="Acme", **kw); create_membership(db, user, startup, role=founder)
# tests/conftest.py → fixtures: db (rolled-back Session), client (TestClient sharing db), engine (session-scoped, citext enabled)
```

**Test client note:** the `client` fixture overrides `get_db` to the rolled-back `db` session. For endpoint tests, seed via factories on `db`, then `db.flush()` (NOT commit — the transaction rolls back after the test), then call `client`. Objects created inside the request share the same session, so they're visible.

---

## File Structure

**Create:**
- `app/db/models/auth.py` — `AuthSession`, `AuthToken`, `MfaBackupCode`, `OAuthAccount`.
- `app/services/auth/password.py` — `validate_password_strength`.
- `app/services/auth/sessions.py` — refresh-token issue/rotate/revoke + cookie helpers + `issue_token_pair`.
- `app/services/auth/tokens.py` — verification/reset token issue + consume.
- `app/services/auth/mfa.py` — TOTP secret encrypt/decrypt/verify + backup-code generate/verify + MFA ticket.
- `app/schemas/auth.py` — Pydantic request/response models.
- `app/api/v1/endpoints/auth/__init__.py` — aggregate `router`.
- `app/api/v1/endpoints/auth/{registration,login,mfa,sessions,password,me,seams}.py`.
- `tests/services/auth/*`, `tests/api/auth/*`.

**Modify:**
- `app/db/models/enums.py` — add `AuthTokenPurpose`, `OAuthProvider`.
- `app/db/models/__init__.py` — register the 4 new models.
- `tests/factories.py` — add auth factories.
- `app/api/v1/api.py` — mount the auth router.
- `app/api/deps.py` — reject `disabled` users in `get_current_user`.
- `app/main.py` — per-user rate-limit key.
- `pyproject.toml` — add `pyotp`, `cryptography`.
- `alembic/versions/` — new migration.

---

## Task 1: Auth models + enums + factories

**Files:**
- Modify: `app/db/models/enums.py`
- Create: `app/db/models/auth.py`
- Modify: `app/db/models/__init__.py`
- Modify: `tests/factories.py`
- Test: `tests/db/test_auth_models.py`

**Interfaces:**
- Produces:
  - `AuthTokenPurpose(email_verification|password_reset)`, `OAuthProvider(google|apple)` in `enums.py`.
  - `AuthSession(user_id, refresh_token_hash[unique], family_id:UUID, ip, user_agent, expires_at, rotated_at, revoked_at)`.
  - `AuthToken(user_id, purpose:AuthTokenPurpose, token_hash[unique], expires_at, consumed_at)`.
  - `MfaBackupCode(user_id, code_hash, consumed_at)`.
  - `OAuthAccount(user_id, provider:OAuthProvider, provider_account_id, email)` with `UNIQUE(provider, provider_account_id)`.
  - Factories: `create_auth_session(db, user, **kw) -> AuthSession`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_auth_models.py
import uuid
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy.exc import IntegrityError
from app.db.models.auth import AuthSession, AuthToken, MfaBackupCode, OAuthAccount
from app.db.models.enums import AuthTokenPurpose, OAuthProvider
from tests.factories import create_user


def test_auth_session_persists(db):
    u = create_user(db)
    s = AuthSession(user_id=u.id, refresh_token_hash="h1", family_id=uuid.uuid4(),
                    expires_at=datetime.now(UTC) + timedelta(days=30))
    db.add(s); db.flush()
    assert s.rotated_at is None and s.revoked_at is None

def test_refresh_hash_unique(db):
    u = create_user(db)
    fam = uuid.uuid4()
    db.add(AuthSession(user_id=u.id, refresh_token_hash="dup", family_id=fam,
                       expires_at=datetime.now(UTC) + timedelta(days=1))); db.flush()
    db.add(AuthSession(user_id=u.id, refresh_token_hash="dup", family_id=fam,
                       expires_at=datetime.now(UTC) + timedelta(days=1)))
    with pytest.raises(IntegrityError):
        db.flush()

def test_auth_token_and_backup_and_oauth(db):
    u = create_user(db)
    db.add(AuthToken(user_id=u.id, purpose=AuthTokenPurpose.email_verification,
                     token_hash="t1", expires_at=datetime.now(UTC) + timedelta(hours=24)))
    db.add(MfaBackupCode(user_id=u.id, code_hash="c1"))
    db.add(OAuthAccount(user_id=u.id, provider=OAuthProvider.google,
                        provider_account_id="g-123", email="a@b.com"))
    db.flush()

def test_oauth_provider_account_unique(db):
    u = create_user(db)
    db.add(OAuthAccount(user_id=u.id, provider=OAuthProvider.google,
                        provider_account_id="same", email="a@b.com")); db.flush()
    db.add(OAuthAccount(user_id=u.id, provider=OAuthProvider.google,
                        provider_account_id="same", email="c@d.com"))
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_auth_models.py -v`
Expected: FAIL — `app.db.models.auth` missing.

- [ ] **Step 3: Add enums**

```python
# app/db/models/enums.py  (append)
class AuthTokenPurpose(str, enum.Enum):
    email_verification = "email_verification"
    password_reset = "password_reset"


class OAuthProvider(str, enum.Enum):
    google = "google"
    apple = "apple"
```

- [ ] **Step 4: Implement the models**

```python
# app/db/models/auth.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import AuthTokenPurpose, OAuthProvider


class AuthSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthToken(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[AuthTokenPurpose] = mapped_column(
        Enum(AuthTokenPurpose, native_enum=False, length=30), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MfaBackupCode(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mfa_backup_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthAccount(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "oauth_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        Enum(OAuthProvider, native_enum=False, length=20), nullable=False
    )
    provider_account_id: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id",
                         name="uq_oauth_accounts_provider_provider_account_id"),
    )
```

- [ ] **Step 5: Register models + add factory**

```python
# app/db/models/__init__.py  (append)
from app.db.models.auth import (  # noqa: F401
    AuthSession,
    AuthToken,
    MfaBackupCode,
    OAuthAccount,
)
```

```python
# tests/factories.py  (append; add imports at top)
from datetime import UTC, datetime, timedelta
from app.db.models.auth import AuthSession


def create_auth_session(db, user, *, refresh_token_hash="hash", family_id=None, **kw):
    import uuid
    s = AuthSession(
        user_id=user.id,
        refresh_token_hash=refresh_token_hash,
        family_id=family_id or uuid.uuid4(),
        expires_at=kw.pop("expires_at", datetime.now(UTC) + timedelta(days=30)),
        **kw,
    )
    db.add(s); db.flush()
    return s
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/db/test_auth_models.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add app/db/models/auth.py app/db/models/enums.py app/db/models/__init__.py tests/factories.py tests/db/test_auth_models.py
git commit -m "feat(db): auth models (sessions, tokens, backup codes, oauth accounts)"
```

---

## Task 2: Alembic migration for auth tables

**Files:**
- Create: `alembic/versions/0002_auth_tables.py` (autogenerated)
- Test: `tests/test_auth_migration.py`

**Interfaces:**
- Produces: a migration adding the 4 auth tables; `alembic upgrade head` runs clean; autogenerate afterward reports no drift.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_migration.py
import subprocess

def test_auth_tables_present_after_upgrade():
    result = subprocess.run(["poetry", "run", "alembic", "upgrade", "head"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    check = subprocess.run(
        ["poetry", "run", "python", "-c",
         "from sqlalchemy import create_engine, inspect; "
         "from app.core.config import settings; "
         "e=create_engine(settings.DATABASE_URL); i=inspect(e); "
         "names=set(i.get_table_names()); "
         "req={'auth_sessions','auth_tokens','mfa_backup_codes','oauth_accounts'}; "
         "assert req <= names, req - names; print('ok')"],
        capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_auth_migration.py -v`
Expected: FAIL — auth tables not in a migration yet.

- [ ] **Step 3: Autogenerate the migration**

```bash
poetry run alembic revision --autogenerate -m "auth tables" --rev-id 0002_auth_tables
```
Open the generated file; confirm `upgrade()` creates `auth_sessions`, `auth_tokens`, `mfa_backup_codes`, `oauth_accounts` (and only those), and `down_revision = "0001_initial_schema"`. Remove any spurious ops.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_auth_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Verify no drift**

Run: `poetry run alembic revision --autogenerate -m "verify" --rev-id _tmp`
Expected: empty `upgrade()`. **Delete that temp file.**

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0002_auth_tables.py tests/test_auth_migration.py
git commit -m "feat(db): migration for auth tables"
```

---

## Task 3: Password-strength policy

**Files:**
- Create: `app/services/auth/__init__.py`, `app/services/auth/password.py`
- Test: `tests/services/auth/test_password.py`

**Interfaces:**
- Consumes: `WeakPassword` (errors).
- Produces: `validate_password_strength(password: str) -> None` — raises `WeakPassword` if <8 chars or no digit.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/auth/test_password.py
import pytest
from app.core.errors import WeakPassword
from app.services.auth.password import validate_password_strength

def test_accepts_strong():
    validate_password_strength("password1")  # no raise

@pytest.mark.parametrize("bad", ["short1", "nodigitspassword", "1234567"])
def test_rejects_weak(bad):
    with pytest.raises(WeakPassword):
        validate_password_strength(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/auth/test_password.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/services/auth/password.py
import re
from app.core.errors import WeakPassword


def validate_password_strength(password: str) -> None:
    if len(password) < 8 or not re.search(r"\d", password):
        raise WeakPassword()
```

Also create empty `app/services/auth/__init__.py` and `tests/services/auth/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/auth/test_password.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/auth/ tests/services/auth/
git commit -m "feat(auth): password-strength policy"
```

---

## Task 4: Session service — refresh issue/rotate/revoke + cookie helpers

**Files:**
- Create: `app/services/auth/sessions.py`
- Test: `tests/services/auth/test_sessions.py`

**Interfaces:**
- Consumes: `AuthSession`, `create_access_token`, settings, `Unauthorized`.
- Produces:
  - `hash_token(raw: str) -> str` (SHA-256 hex).
  - `issue_token_pair(db, user, *, ip=None, user_agent=None, family_id=None) -> tuple[str, str]` → `(access_token, raw_refresh)`; inserts an `AuthSession`.
  - `rotate_refresh(db, raw_refresh, *, ip=None, user_agent=None) -> tuple[str, str, User]`; reuse of an already-rotated/revoked token revokes the whole family and raises `Unauthorized`.
  - `revoke_session(db, raw_refresh) -> None`; `revoke_all_for_user(db, user_id) -> int`.
  - `set_refresh_cookie(response, raw_refresh) -> None`, `clear_refresh_cookie(response) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/auth/test_sessions.py
import pytest
from app.core.errors import AppError
from app.db.models.auth import AuthSession
from app.services.auth.sessions import (
    issue_token_pair, rotate_refresh, revoke_all_for_user, hash_token,
)
from tests.factories import create_user


def test_issue_creates_session_and_hashes(db):
    u = create_user(db)
    access, refresh = issue_token_pair(db, u)
    assert access and refresh
    row = db.query(AuthSession).filter(AuthSession.user_id == u.id).one()
    assert row.refresh_token_hash == hash_token(refresh)   # raw never stored

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
    rotate_refresh(db, refresh)          # refresh now rotated
    with pytest.raises(AppError):
        rotate_refresh(db, refresh)      # reuse → boom
    # entire family revoked
    sessions = db.query(AuthSession).filter(AuthSession.user_id == u.id).all()
    assert all(s.revoked_at is not None for s in sessions)

def test_revoke_all(db):
    u = create_user(db)
    issue_token_pair(db, u); issue_token_pair(db, u)
    n = revoke_all_for_user(db, u.id)
    assert n >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/auth/test_sessions.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/services/auth/sessions.py
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.auth import AuthSession
from app.db.models.user import User


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_refresh() -> str:
    return secrets.token_urlsafe(48)


def issue_token_pair(
    db: Session, user: User, *, ip: str | None = None,
    user_agent: str | None = None, family_id: uuid.UUID | None = None,
) -> tuple[str, str]:
    raw = _new_refresh()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_token(raw),
        family_id=family_id or uuid.uuid4(),
        ip=ip,
        user_agent=user_agent,
        expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(session)
    db.flush()
    access = create_access_token(str(user.id))
    return access, raw


def rotate_refresh(
    db: Session, raw_refresh: str, *, ip: str | None = None, user_agent: str | None = None,
) -> tuple[str, str, User]:
    session = (
        db.query(AuthSession)
        .filter(AuthSession.refresh_token_hash == hash_token(raw_refresh))
        .first()
    )
    now = datetime.now(UTC)
    if session is None:
        raise Unauthorized()
    # Reuse detection: a token already rotated/revoked, or expired → revoke the family.
    if session.rotated_at is not None or session.revoked_at is not None or session.expires_at < now:
        _revoke_family(db, session.family_id)
        raise Unauthorized()
    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None:
        raise Unauthorized()
    session.rotated_at = now
    access, raw = issue_token_pair(db, user, ip=ip, user_agent=user_agent, family_id=session.family_id)
    return access, raw, user


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    (db.query(AuthSession)
       .filter(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
       .update({AuthSession.revoked_at: now}))
    db.flush()


def revoke_session(db: Session, raw_refresh: str) -> None:
    session = (db.query(AuthSession)
               .filter(AuthSession.refresh_token_hash == hash_token(raw_refresh))
               .first())
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.flush()


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> int:
    now = datetime.now(UTC)
    n = (db.query(AuthSession)
           .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
           .update({AuthSession.revoked_at: now}))
    db.flush()
    return int(n)


def set_refresh_cookie(response: Response, raw_refresh: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=raw_refresh,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/api/v1/auth",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.REFRESH_COOKIE_NAME, path="/api/v1/auth")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/auth/test_sessions.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/auth/sessions.py tests/services/auth/test_sessions.py
git commit -m "feat(auth): refresh-token session service with rotation + reuse detection"
```

---

## Task 5: Verification / reset token service

**Files:**
- Create: `app/services/auth/tokens.py`
- Test: `tests/services/auth/test_tokens.py`

**Interfaces:**
- Consumes: `AuthToken`, `AuthTokenPurpose`, `TokenInvalid`.
- Produces:
  - `issue_auth_token(db, user, purpose, ttl: timedelta) -> str` → returns raw token, stores hash.
  - `consume_auth_token(db, purpose, raw) -> User` → validates unconsumed + unexpired + matching purpose, marks consumed, returns the user; else raises `TokenInvalid`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/auth/test_tokens.py
from datetime import timedelta
import pytest
from app.core.errors import TokenInvalid
from app.db.models.enums import AuthTokenPurpose
from app.services.auth.tokens import issue_auth_token, consume_auth_token
from tests.factories import create_user


def test_issue_and_consume_once(db):
    u = create_user(db)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    who = consume_auth_token(db, AuthTokenPurpose.email_verification, raw)
    assert who.id == u.id
    with pytest.raises(TokenInvalid):   # single-use
        consume_auth_token(db, AuthTokenPurpose.email_verification, raw)

def test_wrong_purpose_rejected(db):
    u = create_user(db)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    with pytest.raises(TokenInvalid):
        consume_auth_token(db, AuthTokenPurpose.password_reset, raw)

def test_expired_rejected(db):
    u = create_user(db)
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(seconds=-1))
    with pytest.raises(TokenInvalid):
        consume_auth_token(db, AuthTokenPurpose.password_reset, raw)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/auth/test_tokens.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/services/auth/tokens.py
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import TokenInvalid
from app.db.models.auth import AuthToken
from app.db.models.enums import AuthTokenPurpose
from app.db.models.user import User
from app.services.auth.sessions import hash_token


def issue_auth_token(db: Session, user: User, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(
        user_id=user.id, purpose=purpose, token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + ttl,
    ))
    db.flush()
    return raw


def consume_auth_token(db: Session, purpose: AuthTokenPurpose, raw: str) -> User:
    row = (db.query(AuthToken)
           .filter(AuthToken.token_hash == hash_token(raw), AuthToken.purpose == purpose)
           .first())
    now = datetime.now(UTC)
    if row is None or row.consumed_at is not None or row.expires_at < now:
        raise TokenInvalid()
    row.consumed_at = now
    db.flush()
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise TokenInvalid()
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/auth/test_tokens.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/auth/tokens.py tests/services/auth/test_tokens.py
git commit -m "feat(auth): verification/reset token service"
```

---

## Task 6: MFA service — TOTP + backup codes

**Files:**
- Modify: `pyproject.toml` (add `pyotp`, `cryptography`)
- Create: `app/services/auth/mfa.py`
- Test: `tests/services/auth/test_mfa.py`

**Interfaces:**
- Consumes: `MfaBackupCode`, `MfaInvalidCode`, settings, `get_redis`.
- Produces:
  - `generate_totp_secret() -> str` (base32).
  - `encrypt_secret(secret) -> str`, `decrypt_secret(token) -> str` (Fernet with `settings.MFA_ENCRYPTION_KEY`).
  - `provisioning_uri(secret, email) -> str` (otpauth://).
  - `verify_totp(secret, code) -> bool`.
  - `generate_backup_codes(db, user) -> list[str]` (10 plaintext, stores hashes, replaces prior).
  - `consume_backup_code(db, user, code) -> bool`.
  - `issue_mfa_ticket(user_id) -> str`, `resolve_mfa_ticket(ticket) -> uuid.UUID | None` (Redis, 5-min TTL).

- [ ] **Step 1: Add deps**

```bash
poetry add pyotp cryptography
```

- [ ] **Step 2: Write the failing test**

```python
# tests/services/auth/test_mfa.py
import pyotp
import pytest
from cryptography.fernet import Fernet
from app.core.config import settings
from app.services.auth import mfa
from tests.factories import create_user


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_roundtrip():
    secret = mfa.generate_totp_secret()
    assert mfa.decrypt_secret(mfa.encrypt_secret(secret)) == secret

def test_verify_totp():
    secret = mfa.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert mfa.verify_totp(secret, code) is True
    assert mfa.verify_totp(secret, "000000") is False

def test_backup_codes_generate_and_consume_once(db):
    u = create_user(db)
    codes = mfa.generate_backup_codes(db, u)
    assert len(codes) == 10
    assert mfa.consume_backup_code(db, u, codes[0]) is True
    assert mfa.consume_backup_code(db, u, codes[0]) is False  # single-use
    assert mfa.consume_backup_code(db, u, "not-a-code") is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/services/auth/test_mfa.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement**

```python
# app/services/auth/mfa.py
import secrets

import pyotp
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.auth import MfaBackupCode
from app.db.models.user import User
from app.services.auth.sessions import hash_token

_MFA_TICKET_TTL = 300  # 5 minutes


class MfaNotConfigured(AppError):  # noqa: N818
    code, http_status = "MFA_NOT_CONFIGURED", 500
    message = "MFA is not configured on the server."


def _fernet() -> Fernet:
    if not settings.MFA_ENCRYPTION_KEY:
        raise MfaNotConfigured()
    return Fernet(settings.MFA_ENCRYPTION_KEY.encode())


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Cofoundaz")


def verify_totp(secret: str, code: str) -> bool:
    return bool(pyotp.TOTP(secret).verify(code, valid_window=1))


def generate_backup_codes(db: Session, user: User) -> list[str]:
    db.query(MfaBackupCode).filter(MfaBackupCode.user_id == user.id).delete()
    codes: list[str] = []
    for _ in range(10):
        code = f"{secrets.randbelow(10**10):010d}"
        codes.append(code)
        db.add(MfaBackupCode(user_id=user.id, code_hash=hash_token(code)))
    db.flush()
    return codes


def consume_backup_code(db: Session, user: User, code: str) -> bool:
    row = (db.query(MfaBackupCode)
           .filter(MfaBackupCode.user_id == user.id,
                   MfaBackupCode.code_hash == hash_token(code),
                   MfaBackupCode.consumed_at.is_(None))
           .first())
    if row is None:
        return False
    from datetime import UTC, datetime
    row.consumed_at = datetime.now(UTC)
    db.flush()
    return True


def issue_mfa_ticket(user_id) -> str:
    ticket = secrets.token_urlsafe(32)
    get_redis().setex(f"mfa_ticket:{ticket}", _MFA_TICKET_TTL, str(user_id))
    return ticket


def resolve_mfa_ticket(ticket: str):
    import uuid
    val = get_redis().get(f"mfa_ticket:{ticket}")
    if val is None:
        return None
    get_redis().delete(f"mfa_ticket:{ticket}")
    return uuid.UUID(val)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/services/auth/test_mfa.py -v`
Expected: PASS (3 tests). (Redis-backed ticket functions are covered in Task 9's endpoint tests.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml poetry.lock app/services/auth/mfa.py tests/services/auth/test_mfa.py
git commit -m "feat(auth): MFA service (TOTP encrypt/verify + backup codes + ticket)"
```

---

## Task 7: Signup + verify + resend endpoints

**Files:**
- Create: `app/schemas/auth.py`
- Create: `app/api/v1/endpoints/auth/__init__.py`, `app/api/v1/endpoints/auth/registration.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/auth/test_registration.py`

**Interfaces:**
- Consumes: `validate_password_strength`, `get_password_hash`, `issue_auth_token`, `consume_auth_token`, `get_email_sender`, `EmailMessage`, `write_audit`, `event_bus`, `get_redis`, `EmailTaken`, `User`, `UserProfile`, `UserStatus`, `AuthTokenPurpose`.
- Produces: `auth` router mounted at `/api/v1/auth`; `POST /auth/signup`, `POST /auth/verify`, `POST /auth/verify/resend`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_registration.py
from app.db.models.user import User
from app.db.models.enums import UserStatus, AuthTokenPurpose
from app.db.models.auth import AuthToken
from app.services.auth.tokens import issue_auth_token
from datetime import timedelta
from tests.factories import create_user


def test_signup_creates_pending_user(client, db):
    r = client.post("/api/v1/auth/signup", json={"email": "New@x.com", "password": "password1"})
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["verification_sent"] is True
    assert body["user"]["email"] == "New@x.com"
    u = db.query(User).filter(User.email == "New@x.com").one()
    assert u.status == UserStatus.pending_verification

def test_signup_weak_password(client):
    r = client.post("/api/v1/auth/signup", json={"email": "a@x.com", "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"

def test_signup_duplicate_email(client, db):
    create_user(db, email="dup@x.com"); db.flush()
    r = client.post("/api/v1/auth/signup", json={"email": "dup@x.com", "password": "password1"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_TAKEN"

def test_verify_activates_user(client, db):
    u = create_user(db, email="v@x.com", status=UserStatus.pending_verification)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    db.flush()
    r = client.post("/api/v1/auth/verify", json={"token": raw})
    assert r.status_code == 200
    db.refresh(u)
    assert u.status == UserStatus.active and u.email_verified_at is not None

def test_verify_bad_token(client):
    r = client.post("/api/v1/auth/verify", json={"token": "nope"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "TOKEN_INVALID"

def test_resend_is_generic_for_unknown_email(client):
    r = client.post("/api/v1/auth/verify/resend", json={"email": "ghost@x.com"})
    assert r.status_code == 200  # no enumeration
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_registration.py -v`
Expected: FAIL — router not mounted.

- [ ] **Step 3: Implement schemas**

```python
# app/schemas/auth.py
from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class TokenRequest(BaseModel):
    token: str


class EmailRequest(BaseModel):
    email: EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaChallengeRequest(BaseModel):
    mfa_ticket: str
    code: str


class TotpVerifyRequest(BaseModel):
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str
```

- [ ] **Step 4: Implement registration router**

```python
# app/api/v1/endpoints/auth/registration.py
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.errors import EmailTaken
from app.core.security import get_password_hash
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User, UserProfile
from app.db.session import get_db
from app.platform.audit import write_audit
from app.platform.email import EmailMessage, get_email_sender
from app.platform.events import event_bus
from app.schemas.auth import EmailRequest, SignupRequest, TokenRequest
from app.services.auth.password import validate_password_strength
from app.services.auth.tokens import consume_auth_token, issue_auth_token

router = APIRouter()
_VERIFY_TTL = timedelta(hours=24)


def _send_verification(db: Session, user: User) -> None:
    raw = issue_auth_token(db, user, AuthTokenPurpose.email_verification, _VERIFY_TTL)
    get_email_sender().send(EmailMessage(
        to=user.email, subject="Verify your email",
        html=f'<p>Verify your email — token: <code>{raw}</code></p>',
    ))


@router.post("/signup", status_code=201)
def signup(payload: SignupRequest, request: Request, db: Session = Depends(get_db)):  # noqa: B008
    validate_password_strength(payload.password)
    if db.query(User).filter(User.email == payload.email).first():
        raise EmailTaken()
    user = User(email=payload.email, password_hash=get_password_hash(payload.password),
                status=UserStatus.pending_verification)
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    _send_verification(db, user)
    write_audit(db, "auth.user.registered", actor_user_id=user.id, ip=request.client.host if request.client else None)
    event_bus.publish("auth.user.registered", {"user_id": str(user.id)})
    db.commit()
    return success_response({"user": {"id": str(user.id), "email": user.email}, "verification_sent": True})


@router.post("/verify")
def verify(payload: TokenRequest, db: Session = Depends(get_db)):  # noqa: B008
    user = consume_auth_token(db, AuthTokenPurpose.email_verification, payload.token)
    user.status = UserStatus.active
    user.email_verified_at = datetime.now(UTC)
    db.flush()
    event_bus.publish("auth.user.verified", {"user_id": str(user.id)})
    db.commit()
    return success_response({"verified": True})


@router.post("/verify/resend")
def resend(payload: EmailRequest, db: Session = Depends(get_db)):  # noqa: B008
    user = db.query(User).filter(User.email == payload.email,
                                 User.status == UserStatus.pending_verification).first()
    if user is not None:
        _send_verification(db, user)
        db.commit()
    # Generic response regardless — no enumeration.
    return success_response({"sent": True})
```

- [ ] **Step 5: Mount the auth router**

```python
# app/api/v1/endpoints/auth/__init__.py
from fastapi import APIRouter
from app.api.v1.endpoints.auth import registration

router = APIRouter()
router.include_router(registration.router)
```

```python
# app/api/v1/api.py  (add)
from app.api.v1.endpoints.auth import router as auth_router
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
```

Create empty `tests/api/auth/__init__.py`.

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/api/auth/test_registration.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/auth.py app/api/v1/endpoints/auth/ app/api/v1/api.py tests/api/auth/
git commit -m "feat(auth): signup, verify, resend endpoints"
```

---

## Task 8: Login + lockout (+ disabled-user enforcement)

**Files:**
- Create: `app/api/v1/endpoints/auth/login.py`
- Modify: `app/api/v1/endpoints/auth/__init__.py`, `app/api/deps.py`
- Test: `tests/api/auth/test_login.py`

**Interfaces:**
- Consumes: `verify_password`, `issue_token_pair`, `set_refresh_cookie`, `issue_mfa_ticket`, `write_audit`, `InvalidCredentials`, `AccountLocked`, `User`, `UserStatus`, `MfaType`, settings.
- Produces: `POST /auth/login`; `get_current_user` rejects `status == disabled`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_login.py
from datetime import UTC, datetime
from app.core.security import get_password_hash
from app.db.models.enums import UserStatus, MfaType
from app.db.models.user import User
from tests.factories import create_user


def _active(db, email="l@x.com", pw="password1", **kw):
    return create_user(db, email=email, password_hash=get_password_hash(pw),
                       status=UserStatus.active, email_verified_at=datetime.now(UTC), **kw)


def test_login_success_returns_tokens(client, db):
    _active(db); db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "l@x.com", "password": "password1"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["access_token"] and body["refresh_token"]
    assert body.get("mfa_required") in (False, None)
    assert client.cookies.get("cfz_refresh")  # cookie set

def test_login_wrong_password_401_and_increments(client, db):
    u = _active(db, email="w@x.com"); db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "w@x.com", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    db.refresh(u)
    assert u.failed_login_count == 1

def test_login_lockout_after_5(client, db):
    _active(db, email="lock@x.com"); db.flush()
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "lock@x.com", "password": "bad"})
    r = client.post("/api/v1/auth/login", json={"email": "lock@x.com", "password": "password1"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"

def test_login_mfa_required_returns_ticket_not_tokens(client, db):
    _active(db, email="m@x.com", mfa_type=MfaType.totp); db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "m@x.com", "password": "password1"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["mfa_required"] is True and body.get("mfa_ticket")
    assert body.get("access_token") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_login.py -v`
Expected: FAIL — login route missing.

- [ ] **Step 3: Implement login**

```python
# app/api/v1/endpoints/auth/login.py
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AccountLocked, InvalidCredentials
from app.core.security import verify_password
from app.db.models.enums import MfaType, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import LoginRequest
from app.services.auth.mfa import issue_mfa_ticket
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie

router = APIRouter()


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response,  # noqa: B008
          db: Session = Depends(get_db)):  # noqa: B008
    now = datetime.now(UTC)
    user = db.query(User).filter(User.email == payload.email).first()
    ip = request.client.host if request.client else None

    if user and user.locked_until and user.locked_until > now:
        raise AccountLocked()
    if user is None or user.password_hash is None or not verify_password(payload.password, user.password_hash):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.LOGIN_MAX_FAILS:
                user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                user.failed_login_count = 0
            db.commit()
        write_audit(db, "auth.login.failed", actor_user_id=user.id if user else None, ip=ip)
        db.commit()
        raise InvalidCredentials()
    if user.status == UserStatus.disabled:
        raise InvalidCredentials()

    user.failed_login_count = 0
    user.locked_until = None

    if user.mfa_type != MfaType.none:
        ticket = issue_mfa_ticket(user.id)
        db.commit()
        return success_response({"mfa_required": True, "mfa_ticket": ticket, "access_token": None})

    user.last_login_at = now
    access, refresh = issue_token_pair(db, user, ip=ip,
                                       user_agent=request.headers.get("user-agent"))
    write_audit(db, "auth.login.success", actor_user_id=user.id, ip=ip)
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response({"access_token": access, "refresh_token": refresh, "mfa_required": False})
```

- [ ] **Step 4: Enforce disabled in get_current_user**

```python
# app/api/deps.py  — after fetching `user` in get_current_user, before returning:
    from app.db.models.enums import UserStatus
    if user.status == UserStatus.disabled:
        raise Unauthorized()
```
(Place the import at the top of the file with the others; the check goes right before `return user`.)

- [ ] **Step 5: Mount login router**

```python
# app/api/v1/endpoints/auth/__init__.py  (add)
from app.api.v1.endpoints.auth import login
router.include_router(login.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/api/auth/test_login.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/endpoints/auth/login.py app/api/v1/endpoints/auth/__init__.py app/api/deps.py tests/api/auth/test_login.py
git commit -m "feat(auth): login with lockout + MFA gate + disabled-user enforcement"
```

---

## Task 9: MFA TOTP setup/verify + challenge endpoints

**Files:**
- Create: `app/api/v1/endpoints/auth/mfa.py`
- Modify: `app/api/v1/endpoints/auth/__init__.py`
- Test: `tests/api/auth/test_mfa_endpoints.py`

**Interfaces:**
- Consumes: `get_current_user`, `mfa` service, `issue_token_pair`, `set_refresh_cookie`, `resolve_mfa_ticket`, `MfaInvalidCode`, `User`, `MfaType`.
- Produces: `POST /auth/mfa/totp/setup`, `POST /auth/mfa/totp/verify`, `POST /auth/mfa/challenge`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_mfa_endpoints.py
from datetime import UTC, datetime
import pyotp
import pytest
from cryptography.fernet import Fernet
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.db.models.enums import MfaType, UserStatus
from app.services.auth import mfa
from tests.factories import create_user


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _auth_headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def test_totp_setup_then_verify_enables(client, db):
    u = create_user(db, email="t@x.com", status=UserStatus.active,
                    password_hash=get_password_hash("password1"))
    db.flush()
    r = client.post("/api/v1/auth/mfa/totp/setup", headers=_auth_headers(u))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["secret"] and data["otpauth_uri"].startswith("otpauth://")
    code = pyotp.TOTP(data["secret"]).now()
    r2 = client.post("/api/v1/auth/mfa/totp/verify", json={"code": code}, headers=_auth_headers(u))
    assert r2.status_code == 200
    assert len(r2.json()["data"]["backup_codes"]) == 10
    db.refresh(u)
    assert u.mfa_type == MfaType.totp and u.mfa_secret and u.mfa_enabled_at

def test_challenge_with_totp_issues_tokens(client, db):
    secret = mfa.generate_totp_secret()
    u = create_user(db, email="c@x.com", status=UserStatus.active, mfa_type=MfaType.totp,
                    mfa_secret=mfa.encrypt_secret(secret), mfa_enabled_at=datetime.now(UTC),
                    password_hash=get_password_hash("password1"))
    db.flush()
    ticket = mfa.issue_mfa_ticket(u.id)
    code = pyotp.TOTP(secret).now()
    r = client.post("/api/v1/auth/mfa/challenge", json={"mfa_ticket": ticket, "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["access_token"]

def test_challenge_bad_code_401(client, db):
    secret = mfa.generate_totp_secret()
    u = create_user(db, email="c2@x.com", status=UserStatus.active, mfa_type=MfaType.totp,
                    mfa_secret=mfa.encrypt_secret(secret), mfa_enabled_at=datetime.now(UTC))
    db.flush()
    ticket = mfa.issue_mfa_ticket(u.id)
    r = client.post("/api/v1/auth/mfa/challenge", json={"mfa_ticket": ticket, "code": "000000"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "MFA_INVALID_CODE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_mfa_endpoints.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement MFA endpoints**

```python
# app/api/v1/endpoints/auth/mfa.py
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.envelope import success_response
from app.core.errors import MfaInvalidCode
from app.db.models.enums import MfaType
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import MfaChallengeRequest, TotpVerifyRequest
from app.services.auth import mfa
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie

router = APIRouter(prefix="/mfa")


@router.post("/totp/setup")
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):  # noqa: B008
    secret = mfa.generate_totp_secret()
    user.mfa_secret = mfa.encrypt_secret(secret)  # pending until verify
    db.commit()
    return success_response({"secret": secret, "otpauth_uri": mfa.provisioning_uri(secret, user.email)})


@router.post("/totp/verify")
def totp_verify(payload: TotpVerifyRequest, user: User = Depends(get_current_user),  # noqa: B008
                db: Session = Depends(get_db)):  # noqa: B008
    if not user.mfa_secret or not mfa.verify_totp(mfa.decrypt_secret(user.mfa_secret), payload.code):
        raise MfaInvalidCode()
    user.mfa_type = MfaType.totp
    user.mfa_enabled_at = datetime.now(UTC)
    codes = mfa.generate_backup_codes(db, user)
    db.commit()
    return success_response({"enabled": True, "backup_codes": codes})


@router.post("/challenge")
def challenge(payload: MfaChallengeRequest, request: Request, response: Response,  # noqa: B008
              db: Session = Depends(get_db)):  # noqa: B008
    user_id = mfa.resolve_mfa_ticket(payload.mfa_ticket)
    if user_id is None:
        raise MfaInvalidCode()
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.mfa_secret:
        raise MfaInvalidCode()
    ok = mfa.verify_totp(mfa.decrypt_secret(user.mfa_secret), payload.code) \
        or mfa.consume_backup_code(db, user, payload.code)
    if not ok:
        raise MfaInvalidCode()
    user.last_login_at = datetime.now(UTC)
    access, refresh = issue_token_pair(db, user, ip=request.client.host if request.client else None,
                                       user_agent=request.headers.get("user-agent"))
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response({"access_token": access, "refresh_token": refresh})
```

- [ ] **Step 4: Mount MFA router**

```python
# app/api/v1/endpoints/auth/__init__.py  (add)
from app.api.v1.endpoints.auth import mfa
router.include_router(mfa.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/api/auth/test_mfa_endpoints.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/auth/mfa.py app/api/v1/endpoints/auth/__init__.py tests/api/auth/test_mfa_endpoints.py
git commit -m "feat(auth): TOTP MFA setup/verify + challenge endpoints"
```

---

## Task 10: Refresh + logout endpoints

**Files:**
- Create: `app/api/v1/endpoints/auth/sessions.py`
- Modify: `app/api/v1/endpoints/auth/__init__.py`
- Test: `tests/api/auth/test_sessions_endpoints.py`

**Interfaces:**
- Consumes: `rotate_refresh`, `revoke_session`, `set_refresh_cookie`, `clear_refresh_cookie`, `Unauthorized`, settings (cookie name), `RefreshRequest`.
- Produces: `POST /auth/refresh`, `POST /auth/logout`. Reads refresh from cookie first, else request body.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_sessions_endpoints.py
from datetime import UTC, datetime
from app.core.security import get_password_hash
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _login(client, db, email="s@x.com"):
    create_user(db, email=email, password_hash=get_password_hash("password1"),
                status=UserStatus.active, email_verified_at=datetime.now(UTC))
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "password1"})
    return r.json()["data"]["refresh_token"]


def test_refresh_rotates(client, db):
    refresh = _login(client, db)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["access_token"] and data["refresh_token"] != refresh

def test_refresh_reuse_is_401(client, db):
    refresh = _login(client, db, email="s2@x.com")
    client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})  # rotate once
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})  # reuse
    assert r.status_code == 401

def test_logout_clears(client, db):
    refresh = _login(client, db, email="s3@x.com")
    r = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200
    # subsequent refresh with the revoked token fails
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_sessions_endpoints.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement**

```python
# app/api/v1/endpoints/auth/sessions.py
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized
from app.core.config import settings
from app.core.envelope import success_response
from app.db.session import get_db
from app.schemas.auth import RefreshRequest
from app.services.auth.sessions import (
    clear_refresh_cookie, revoke_session, rotate_refresh, set_refresh_cookie,
)

router = APIRouter()


def _read_refresh(request: Request, payload: RefreshRequest) -> str:
    return request.cookies.get(settings.REFRESH_COOKIE_NAME) or (payload.refresh_token or "")


@router.post("/refresh")
def refresh(payload: RefreshRequest, request: Request, response: Response,  # noqa: B008
            db: Session = Depends(get_db)):  # noqa: B008
    raw = _read_refresh(request, payload)
    if not raw:
        raise Unauthorized()
    access, new_refresh, _ = rotate_refresh(
        db, raw, ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    set_refresh_cookie(response, new_refresh)
    return success_response({"access_token": access, "refresh_token": new_refresh})


@router.post("/logout")
def logout(payload: RefreshRequest, request: Request, response: Response,  # noqa: B008
           db: Session = Depends(get_db)):  # noqa: B008
    raw = _read_refresh(request, payload)
    if raw:
        revoke_session(db, raw)
        db.commit()
    clear_refresh_cookie(response)
    return success_response({"logged_out": True})
```

- [ ] **Step 4: Mount router**

```python
# app/api/v1/endpoints/auth/__init__.py  (add)
from app.api.v1.endpoints.auth import sessions
router.include_router(sessions.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/api/auth/test_sessions_endpoints.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/auth/sessions.py app/api/v1/endpoints/auth/__init__.py tests/api/auth/test_sessions_endpoints.py
git commit -m "feat(auth): refresh rotation + logout endpoints"
```

---

## Task 11: Forgot / reset password endpoints

**Files:**
- Create: `app/api/v1/endpoints/auth/password.py`
- Modify: `app/api/v1/endpoints/auth/__init__.py`
- Test: `tests/api/auth/test_password_endpoints.py`

**Interfaces:**
- Consumes: `issue_auth_token`, `consume_auth_token`, `validate_password_strength`, `get_password_hash`, `revoke_all_for_user`, `get_email_sender`, `EmailMessage`, `AuthTokenPurpose`.
- Produces: `POST /auth/password/forgot` (generic 200), `POST /auth/password/reset`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_password_endpoints.py
from datetime import timedelta
from app.core.security import get_password_hash, verify_password
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.auth import AuthSession
from app.services.auth.tokens import issue_auth_token
from app.services.auth.sessions import issue_token_pair
from tests.factories import create_user


def test_forgot_is_generic_for_unknown(client):
    r = client.post("/api/v1/auth/password/forgot", json={"email": "ghost@x.com"})
    assert r.status_code == 200
    assert r.json()["data"]["sent"] is True

def test_reset_updates_password_and_revokes_sessions(client, db):
    u = create_user(db, email="r@x.com", password_hash=get_password_hash("password1"),
                    status=UserStatus.active)
    issue_token_pair(db, u)  # an active session
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    r = client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "newpass123"})
    assert r.status_code == 200, r.text
    db.refresh(u)
    assert verify_password("newpass123", u.password_hash)
    sessions = db.query(AuthSession).filter(AuthSession.user_id == u.id).all()
    assert all(s.revoked_at is not None for s in sessions)

def test_reset_weak_password_rejected(client, db):
    u = create_user(db, email="r2@x.com", status=UserStatus.active)
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    r = client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "weak"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_password_endpoints.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement**

```python
# app/api/v1/endpoints/auth/password.py
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.security import get_password_hash
from app.db.models.enums import AuthTokenPurpose
from app.db.models.user import User
from app.db.session import get_db
from app.platform.email import EmailMessage, get_email_sender
from app.schemas.auth import ForgotPasswordRequest, ResetPasswordRequest
from app.services.auth.password import validate_password_strength
from app.services.auth.sessions import revoke_all_for_user
from app.services.auth.tokens import consume_auth_token, issue_auth_token

router = APIRouter(prefix="/password")
_RESET_TTL = timedelta(hours=1)


@router.post("/forgot")
def forgot(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):  # noqa: B008
    user = db.query(User).filter(User.email == payload.email).first()
    if user is not None:
        raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, _RESET_TTL)
        get_email_sender().send(EmailMessage(
            to=user.email, subject="Reset your password",
            html=f'<p>Reset your password — token: <code>{raw}</code></p>',
        ))
        db.commit()
    # Same response either way — no enumeration.
    return success_response({"sent": True})


@router.post("/reset")
def reset(payload: ResetPasswordRequest, db: Session = Depends(get_db)):  # noqa: B008
    validate_password_strength(payload.password)
    user = consume_auth_token(db, AuthTokenPurpose.password_reset, payload.token)
    user.password_hash = get_password_hash(payload.password)
    revoke_all_for_user(db, user.id)  # force re-login everywhere
    db.commit()
    return success_response({"reset": True})
```

- [ ] **Step 4: Mount router**

```python
# app/api/v1/endpoints/auth/__init__.py  (add)
from app.api.v1.endpoints.auth import password
router.include_router(password.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/api/auth/test_password_endpoints.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/auth/password.py app/api/v1/endpoints/auth/__init__.py tests/api/auth/test_password_endpoints.py
git commit -m "feat(auth): forgot/reset password endpoints"
```

---

## Task 12: GET /auth/me

**Files:**
- Create: `app/api/v1/endpoints/auth/me.py`
- Modify: `app/api/v1/endpoints/auth/__init__.py`
- Test: `tests/api/auth/test_me.py`

**Interfaces:**
- Consumes: `get_current_user`, `Membership`, `Startup`.
- Produces: `GET /auth/me` → `{ user, profile, memberships:[{startup_id, name, role}], active_workspace_id }`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_me.py
from app.core.security import create_access_token
from app.db.models.enums import UserStatus, MembershipRole
from tests.factories import create_user, create_startup, create_membership


def test_me_returns_identity_and_memberships(client, db):
    u = create_user(db, email="me@x.com", status=UserStatus.active)
    u.profile.full_name = "Ada Founder"
    s = create_startup(db, owner=u, name="Cofoundaz")
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["user"]["email"] == "me@x.com"
    assert data["profile"]["full_name"] == "Ada Founder"
    assert data["memberships"][0]["role"] == "founder"
    assert data["active_workspace_id"] == str(s.id)

def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_me.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement**

```python
# app/api/v1/endpoints/auth/me.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.envelope import success_response
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter()


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):  # noqa: B008
    rows = (db.query(Membership, Startup)
            .join(Startup, Startup.id == Membership.startup_id)
            .filter(Membership.user_id == user.id, Membership.status == MembershipStatus.active)
            .all())
    memberships = [{"startup_id": str(s.id), "name": s.name, "role": m.role.value} for m, s in rows]
    profile = user.profile
    return success_response({
        "user": {"id": str(user.id), "email": user.email, "status": user.status.value},
        "profile": None if profile is None else {
            "full_name": profile.full_name, "role_title": profile.role_title,
            "country": profile.country, "avatar_url": profile.avatar_url,
        },
        "memberships": memberships,
        "active_workspace_id": memberships[0]["startup_id"] if memberships else None,
    })
```

- [ ] **Step 4: Mount router**

```python
# app/api/v1/endpoints/auth/__init__.py  (add)
from app.api.v1.endpoints.auth import me
router.include_router(me.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/api/auth/test_me.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/auth/me.py app/api/v1/endpoints/auth/__init__.py tests/api/auth/test_me.py
git commit -m "feat(auth): GET /auth/me identity endpoint"
```

---

## Task 13: OAuth + SMS 501 seams

**Files:**
- Create: `app/api/v1/endpoints/auth/seams.py`
- Modify: `app/api/v1/endpoints/auth/__init__.py`
- Test: `tests/api/auth/test_seams.py`

**Interfaces:**
- Consumes: `FeatureNotEnabled`.
- Produces: `POST /auth/oauth/{provider}`, `POST /auth/mfa/sms/setup`, `POST /auth/mfa/sms/verify` — all raise `FeatureNotEnabled` (501).

- [ ] **Step 1: Write the failing test**

```python
# tests/api/auth/test_seams.py
import pytest


@pytest.mark.parametrize("path", [
    "/api/v1/auth/oauth/google",
    "/api/v1/auth/oauth/apple",
    "/api/v1/auth/mfa/sms/setup",
    "/api/v1/auth/mfa/sms/verify",
])
def test_seams_return_501(client, path):
    r = client.post(path, json={})
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "FEATURE_NOT_ENABLED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/auth/test_seams.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement**

```python
# app/api/v1/endpoints/auth/seams.py
from fastapi import APIRouter
from app.core.errors import FeatureNotEnabled

router = APIRouter()


@router.post("/oauth/{provider}")
def oauth(provider: str):
    raise FeatureNotEnabled()


@router.post("/mfa/sms/setup")
def sms_setup():
    raise FeatureNotEnabled()


@router.post("/mfa/sms/verify")
def sms_verify():
    raise FeatureNotEnabled()
```

- [ ] **Step 4: Mount router**

```python
# app/api/v1/endpoints/auth/__init__.py  (add)
from app.api.v1.endpoints.auth import seams
router.include_router(seams.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/api/auth/test_seams.py -v`
Expected: PASS (4 params).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/auth/seams.py app/api/v1/endpoints/auth/__init__.py tests/api/auth/test_seams.py
git commit -m "feat(auth): OAuth + SMS 501 seams"
```

---

## Task 14: Per-user rate-limit key

**Files:**
- Modify: `app/main.py`
- Test: `tests/api/test_rate_limit_key.py`

**Interfaces:**
- Consumes: settings, JWT decode.
- Produces: limiter `key_func` that keys on the JWT `sub` when a valid Bearer token is present, else falls back to remote address.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_rate_limit_key.py
from app.main import _rate_limit_key
from app.core.security import create_access_token


class _Req:
    def __init__(self, auth=None):
        self.headers = {"Authorization": auth} if auth else {}
        self.client = type("C", (), {"host": "9.9.9.9"})()


def test_key_uses_user_sub_when_authenticated():
    token = create_access_token("11111111-1111-1111-1111-111111111111")
    assert _rate_limit_key(_Req(f"Bearer {token}")) == "user:11111111-1111-1111-1111-111111111111"

def test_key_falls_back_to_ip():
    assert _rate_limit_key(_Req()) == "9.9.9.9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/test_rate_limit_key.py -v`
Expected: FAIL — `_rate_limit_key` missing.

- [ ] **Step 3: Implement**

```python
# app/main.py — replace the Limiter block's key_func with a custom function.
from jose import JWTError, jwt
from slowapi.util import get_remote_address

def _rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass
    return get_remote_address(request)

limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
```
(Remove the old `key_func=get_remote_address` limiter definition and the now-stale TODO comment.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/api/test_rate_limit_key.py -v`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite + coverage**

Run: `poetry run pytest --cov=app --cov-report=term`
Expected: all tests PASS (Plan 1 + Plan 2). Note coverage %.

- [ ] **Step 6: Write the SOP doc**

Create `docs/sop/2026-08-13-auth-endpoints.md` per the repo SOP convention (what shipped, why, how, files, verification, follow-ups: SMS MFA, real OAuth, idempotency-key middleware).

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/api/test_rate_limit_key.py docs/sop/2026-08-13-auth-endpoints.md
git commit -m "feat(auth): per-user rate-limit key + auth SOP"
```

---

## Self-Review Notes (author)

- **Spec coverage (Section D):** signup/verify/resend → T7; login+lockout+mfa gate → T8; TOTP setup/verify + challenge → T6/T9; refresh/logout → T4/T10; forgot/reset → T5/T11; `/me` → T12; oauth/sms seams → T13. Envelope + error codes reused from Plan 1 (Global Constraints). Section E security cases: lockout (T8), refresh reuse revokes family (T4/T10), no-enumeration (T7 resend, T11 forgot), MFA TOTP+backup single-use (T6/T9), reset revokes sessions (T11).
- **Deferred (unchanged from spec):** SMS MFA, real Google/Apple OAuth, `Idempotency-Key` middleware — all called out as follow-ups; seams return 501 so the contract is honest.
- **Type consistency:** `hash_token`, `issue_token_pair`, `rotate_refresh`, `issue_auth_token`/`consume_auth_token`, `issue_mfa_ticket`/`resolve_mfa_ticket`, `set_refresh_cookie`/`clear_refresh_cookie`, and the `app/schemas/auth.py` request models are referenced consistently across tasks. `AuthTokenPurpose`/`OAuthProvider` added in T1 and used in T5/T7/T11/T1.
- **Cross-task ordering:** T1→T2 (models before migration); services T3–T6 before endpoint tasks T7–T13 that consume them; T14 last (touches app wiring + full-suite gate).
- **Commit messages carry no AI-attribution trailer** (per user preference).
