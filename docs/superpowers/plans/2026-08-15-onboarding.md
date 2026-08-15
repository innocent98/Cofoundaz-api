# Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Module 01's onboarding surface — the 6-step resumable wizard (autosave), logo upload, team invites, email-bound invitation acceptance, and `POST /onboarding/complete` enqueuing the `roadmap.generate` + `healthscore.initialize` stub jobs — on top of the merged Foundation + Auth work.

**Architecture:** Thin FastAPI routers under `app/api/v1/endpoints/onboarding/` + `.../invitations.py`, delegating to services in `app/services/onboarding/`. Onboarding operates on the caller's own **draft workspace** (the `startups` row they `created_by` with `onboarding_completed_at IS NULL`), lazily created with a founder membership on first `GET /state` — no `X-Workspace-Id` pre-completion. Invites use opaque SHA-256-hashed tokens; acceptance is bound to the invited email.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL, python-multipart (logo upload), pytest + real Postgres.

**Spec:** `docs/superpowers/specs/2026-08-15-onboarding-design.md` — read it alongside this plan.

## Global Constraints

- **Response envelope** (existing `app/core/envelope.py`): endpoints return `success_response(data)`; errors via raised `AppError` subclasses. Never hand-roll the dict.
- **Auth:** every `/onboarding/*` and `POST /invitations/accept` endpoint requires an authenticated, **email-verified** user (`get_verified_user`, added in Task 3). `GET /invitations/{token}` is **public**.
- **Tenancy:** onboarding mutations touch ONLY the caller's own draft workspace (resolved by `created_by`), never a header-supplied one.
- **Tokens never raw:** invite tokens stored SHA-256-hashed via the existing `hash_token` (`app/services/auth/sessions.py`). 14-day TTL.
- **Enums:** `Enum(PyEnum, native_enum=False, length=…)`. Timezone-aware UTC (`datetime.now(UTC)`), never naive.
- **PKs:** UUID v4 (`UUIDMixin`); every table `TimestampMixin`.
- **`complete` gate (verbatim required fields):** founder `full_name`, startup `name`, `industry`, `stage`, and ≥1 `goal`. Missing → `422 ONBOARDING_INCOMPLETE` with `field_errors`.
- **Idempotency:** natural only — `complete` self-guards via `onboarding_completed_at`; `invites` dedupes by email. No generic `Idempotency-Key` header (deferred to Module 24).
- **`startups.name` becomes nullable** (draft workspaces have no name until step 2); `complete` re-enforces it. `startup_profiles.assessment_pending` (bool, default `True`) is added and drives the FE limited-mode banner.
- **Jobs/events/notifications:** `complete` enqueues via `JobDispatcher.enqueue(db, type, {"startup_id": …}, startup_id)`; events via `event_bus.publish`; the completion "notification" is an `event_bus` emission (real delivery = Module 20). Invite email is real (`EmailSender`).
- **Do NOT add `Co-Authored-By` or any AI-attribution trailer to commit messages.**

### Existing interfaces (carry into every task brief)

```python
# app/api/deps.py → get_current_user(credentials, db) -> User; class Unauthorized(AppError) 401
# app/core/errors.py → AppError(code,message,http_status,field_errors); subclasses incl. NotFound(404), Forbidden(403), TokenInvalid(400)
# app/core/envelope.py → success_response(data, meta=None)
# app/db/models/user.py → User(email, email_verified_at, status, profile: UserProfile); UserProfile(user_id PK, full_name, role_title="Founder & CEO", country, phone, how_heard, avatar_url)
# app/db/models/startup.py → Startup(id,name,description,website,logo_url,industry,business_model,stage,country,created_by, profile: StartupProfile); StartupProfile(startup_id PK, goals: list[str], notes, onboarding_step:int=1, onboarding_completed_at)
# app/db/models/membership.py → Membership(user_id, startup_id, role: MembershipRole, status: MembershipStatus, invited_by, joined_at); UNIQUE(user_id, startup_id)
# app/db/models/enums.py → MembershipRole(founder|team_member|mentor|accountant|legal_advisor|business_consultant|investor), MembershipStatus(active|suspended|removed), BusinessModel(b2b|b2c|b2b2c|marketplace|hardware|services), StartupStage(idea|validation|build|launch|growth|scale)
# app/services/auth/sessions.py → hash_token(raw: str) -> str  (SHA-256 hex)
# app/platform/jobs.py → job_dispatcher.enqueue(db, type: str, payload: dict, startup_id: uuid.UUID|None=None) -> Job (Job.id)
# app/platform/storage.py → get_storage() -> Storage; Storage.save(key: str, content: bytes, content_type: str) -> str
# app/platform/email.py → EmailMessage(to,subject,html); get_email_sender() -> EmailSender(.send(msg))
# app/platform/events.py → event_bus.publish(event: str, payload: dict); event_bus.published (list, tests)
# app/api/v1/api.py → api_router.include_router(...) (health, jobs, auth mounted)
# tests/factories.py → create_user(db,*,email=None,**kw), create_startup(db,*,owner,name="Acme",**kw), create_membership(db,user,startup,role=founder)
# tests/conftest.py → db (rolled-back Session), client (TestClient sharing db, limiter disabled)
```

**Test-client note:** the `client` fixture overrides `get_db` to the rolled-back `db`; seed via factories + `db.flush()` (not commit). To authenticate an endpoint test, mint a token with `create_access_token(str(user.id))` from `app/core/security.py` and pass `headers={"Authorization": f"Bearer {token}"}`; the user must have `email_verified_at` set for onboarding routes.

Latest migration: `0002_auth_tables`. New migration `down_revision = "0002_auth_tables"`.

---

## File Structure

**Create:**
- `app/db/models/invitation.py` — `Invitation` model.
- `app/services/onboarding/__init__.py`, `workspace.py`, `steps.py`, `invites.py`, `complete.py`.
- `app/schemas/onboarding.py` — request models.
- `app/api/v1/endpoints/onboarding/__init__.py`, `state.py`, `logo.py`, `invites.py`, `complete.py`.
- `app/api/v1/endpoints/invitations.py` — public preview + accept.
- `tests/services/onboarding/*`, `tests/api/onboarding/*`, `e2e/test_onboarding.py`.

**Modify:**
- `app/db/models/enums.py` — add `InvitationStatus`.
- `app/db/models/startup.py` — `name` nullable + `StartupProfile.assessment_pending`.
- `app/db/models/__init__.py` — register `Invitation`.
- `app/core/errors.py` — add `OnboardingIncomplete`, `InviteEmailMismatch`, `AlreadyMember`, `EmailNotVerified`.
- `app/api/deps.py` — add `get_verified_user`.
- `app/api/v1/api.py` — mount onboarding + invitations routers.
- `tests/factories.py` — add `create_invitation`.
- `alembic/versions/` — `0003_onboarding`.

---

## Task 1: Invitation model + enum + schema changes + factory

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/startup.py`, `app/db/models/__init__.py`, `tests/factories.py`
- Create: `app/db/models/invitation.py`
- Test: `tests/db/test_invitation_model.py`

**Interfaces:**
- Produces: `InvitationStatus(pending|accepted|expired|revoked)`; `Invitation(startup_id, email, role, token_hash[unique], status, invited_by, expires_at, accepted_at, accepted_user_id)`; `Startup.name` nullable; `StartupProfile.assessment_pending: bool` (default True); factory `create_invitation(db, startup, *, email, role=MembershipRole.team_member, inviter=None, token_hash=None, status=InvitationStatus.pending, expires_at=None) -> Invitation`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_invitation_model.py
import uuid
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy.exc import IntegrityError
from app.db.models.invitation import Invitation
from app.db.models.enums import InvitationStatus, MembershipRole
from tests.factories import create_user, create_startup


def test_invitation_persists(db):
    owner = create_user(db)
    s = create_startup(db, owner=owner)
    inv = Invitation(startup_id=s.id, email="new@x.com", role=MembershipRole.team_member,
                     token_hash="h1", invited_by=owner.id,
                     expires_at=datetime.now(UTC) + timedelta(days=14))
    db.add(inv); db.flush()
    assert inv.status == InvitationStatus.pending
    assert inv.accepted_at is None

def test_token_hash_unique(db):
    owner = create_user(db); s = create_startup(db, owner=owner)
    db.add(Invitation(startup_id=s.id, email="a@x.com", role=MembershipRole.mentor,
                      token_hash="dup", invited_by=owner.id,
                      expires_at=datetime.now(UTC) + timedelta(days=14))); db.flush()
    db.add(Invitation(startup_id=s.id, email="b@x.com", role=MembershipRole.mentor,
                      token_hash="dup", invited_by=owner.id,
                      expires_at=datetime.now(UTC) + timedelta(days=14)))
    with pytest.raises(IntegrityError):
        db.flush()

def test_startup_name_nullable_and_assessment_pending_default(db):
    owner = create_user(db)
    s = create_startup(db, owner=owner, name=None)  # draft workspace, no name yet
    db.flush()
    assert s.name is None
    assert s.profile.assessment_pending is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_invitation_model.py -v`
Expected: FAIL — `app.db.models.invitation` missing / `name` not nullable / no `assessment_pending`.

- [ ] **Step 3: Add the enum**

```python
# app/db/models/enums.py  (append)
class InvitationStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    expired = "expired"
    revoked = "revoked"
```

- [ ] **Step 4: Make `startups.name` nullable + add `assessment_pending`**

```python
# app/db/models/startup.py
# change: name column -> nullable
    name: Mapped[str | None] = mapped_column(String, nullable=True)
# in StartupProfile, add (import Boolean, text from sqlalchemy):
    assessment_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
```
Add `Boolean` and `text` to the `from sqlalchemy import ...` line in that file.

- [ ] **Step 5: Implement the Invitation model + register it**

```python
# app/db/models/invitation.py
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import InvitationStatus, MembershipRole


class Invitation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "invitations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, native_enum=False, length=30), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, native_enum=False, length=20),
        default=InvitationStatus.pending, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
```

```python
# app/db/models/__init__.py  (append)
from app.db.models.invitation import Invitation  # noqa: F401
```

- [ ] **Step 6: Add the factory**

```python
# tests/factories.py  (append; ensure datetime/timedelta imported)
from app.db.models.invitation import Invitation
from app.db.models.enums import InvitationStatus


def create_invitation(db, startup, *, email="invitee@example.com",
                      role=MembershipRole.team_member, inviter=None,
                      token_hash="invtok", status=InvitationStatus.pending, expires_at=None):
    from datetime import UTC, datetime, timedelta
    inv = Invitation(
        startup_id=startup.id, email=email, role=role, token_hash=token_hash,
        status=status, invited_by=(inviter.id if inviter else startup.created_by),
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=14),
    )
    db.add(inv); db.flush()
    return inv
```

- [ ] **Step 7: Run test to verify it passes**

Run: `poetry run pytest tests/db/test_invitation_model.py -v`
Expected: PASS (3 tests). Run full suite (`poetry run pytest -q`) — the `startups.name` nullability must not break existing tests.

- [ ] **Step 8: Commit**

```bash
git add app/db/models/ tests/factories.py tests/db/test_invitation_model.py
git commit -m "feat(db): invitations model + assessment_pending + nullable startup name"
```

---

## Task 2: Alembic migration 0003

**Files:**
- Create: `alembic/versions/0003_onboarding.py`
- Test: `tests/test_onboarding_migration.py`

**Interfaces:**
- Produces: migration adding `invitations`, `startup_profiles.assessment_pending`, and `startups.name` nullability; `alembic upgrade head` clean; autogenerate reports no drift after.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboarding_migration.py
import subprocess

def test_onboarding_migration_applies():
    r = subprocess.run(["poetry", "run", "alembic", "upgrade", "head"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    check = subprocess.run(
        ["poetry", "run", "python", "-c",
         "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
         "e=create_engine(settings.DATABASE_URL); i=inspect(e); "
         "assert 'invitations' in i.get_table_names(); "
         "cols={c['name']: c for c in i.get_columns('startup_profiles')}; "
         "assert 'assessment_pending' in cols; "
         "nm={c['name']: c for c in i.get_columns('startups')}['name']; "
         "assert nm['nullable'] is True; print('ok')"],
        capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_onboarding_migration.py -v`
Expected: FAIL — migration not present.

- [ ] **Step 3: Autogenerate + verify the migration**

```bash
poetry run alembic revision --autogenerate -m "onboarding" --rev-id 0003_onboarding
```
Open the file; confirm `down_revision = "0002_auth_tables"` and `upgrade()` does exactly: `op.create_table("invitations", …)` (+ its indexes/unique), `op.add_column("startup_profiles", sa.Column("assessment_pending", sa.Boolean(), server_default=sa.text("true"), nullable=False))`, and `op.alter_column("startups", "name", existing_type=sa.String(), nullable=True)`. Remove any spurious ops. Ensure `downgrade()` reverses them.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_onboarding_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Verify no drift**

Run: `poetry run alembic revision --autogenerate -m "verify" --rev-id _tmp`
Expected: empty `upgrade()`. **Delete the temp file.**

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0003_onboarding.py tests/test_onboarding_migration.py
git commit -m "feat(db): migration 0003 — onboarding (invitations, assessment_pending, nullable name)"
```

---

## Task 3: Errors + verified-user dep + workspace resolver

**Files:**
- Modify: `app/core/errors.py`, `app/api/deps.py`
- Create: `app/services/onboarding/__init__.py`, `app/services/onboarding/workspace.py`
- Test: `tests/api/test_verified_user.py`, `tests/services/onboarding/test_workspace.py`

**Interfaces:**
- Consumes: `get_current_user`, `User`, `Startup`, `StartupProfile`, `Membership`, `MembershipRole/Status`, `event_bus`.
- Produces:
  - Errors: `OnboardingIncomplete`(422 `ONBOARDING_INCOMPLETE`), `InviteEmailMismatch`(403 `INVITE_EMAIL_MISMATCH`), `AlreadyMember`(409 `ALREADY_MEMBER`), `EmailNotVerified`(403 `EMAIL_NOT_VERIFIED`).
  - `get_verified_user(user=Depends(get_current_user)) -> User` — raises `EmailNotVerified` if `email_verified_at is None`.
  - `resolve_or_create_workspace(db, user) -> Startup` — returns the user's most-recent `created_by` startup; if none, creates a draft (`name=None`) + `StartupProfile` + active `founder` `Membership` and emits `workspace.created`.
  - `serialize_state(db, startup, user) -> dict` — the `GET /state` body (see spec §4.1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_verified_user.py
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from app.api.deps import get_verified_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.db.models.user import User
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _app():
    app = FastAPI()
    from app.core.errors import register_exception_handlers
    register_exception_handlers(app)

    @app.get("/vok")
    def vok(u: User = Depends(get_verified_user)):
        return {"e": u.email}
    return app

def test_verified_user_ok(db):
    from datetime import UTC, datetime
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC)); db.commit()
    app = _app(); app.dependency_overrides[get_db] = lambda: db
    r = TestClient(app).get("/vok", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"})
    assert r.status_code == 200

def test_unverified_user_403(db):
    u = create_user(db, status=UserStatus.pending_verification); db.commit()
    app = _app(); app.dependency_overrides[get_db] = lambda: db
    r = TestClient(app).get("/vok", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
```

```python
# tests/services/onboarding/test_workspace.py
from app.services.onboarding.workspace import resolve_or_create_workspace, serialize_state
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.enums import MembershipRole
from tests.factories import create_user


def test_creates_draft_workspace_and_founder_membership(db):
    u = create_user(db); db.flush()
    s = resolve_or_create_workspace(db, u)
    assert s.created_by == u.id and s.name is None
    m = db.query(Membership).filter(Membership.user_id == u.id, Membership.startup_id == s.id).one()
    assert m.role == MembershipRole.founder and m.status == MembershipStatus.active

def test_resolve_is_idempotent(db):
    u = create_user(db); db.flush()
    s1 = resolve_or_create_workspace(db, u)
    s2 = resolve_or_create_workspace(db, u)
    assert s1.id == s2.id  # does not create a second workspace

def test_serialize_state_shape(db):
    u = create_user(db); db.flush()
    s = resolve_or_create_workspace(db, u)
    state = serialize_state(db, s, u)
    for key in ("step", "completed", "assessment_pending", "founder_profile", "startup", "goals", "notes", "invites"):
        assert key in state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/api/test_verified_user.py tests/services/onboarding/test_workspace.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Add the errors**

```python
# app/core/errors.py  (append the four subclasses, following the existing pattern)
class OnboardingIncomplete(AppError):  # noqa: N818
    code, http_status = "ONBOARDING_INCOMPLETE", 422
    message = "A few things are still needed before we can build your workspace."

class InviteEmailMismatch(AppError):  # noqa: N818
    code, http_status = "INVITE_EMAIL_MISMATCH", 403
    message = "This invitation was sent to a different email address."

class AlreadyMember(AppError):  # noqa: N818
    code, http_status = "ALREADY_MEMBER", 409
    message = "That person is already on this workspace."

class EmailNotVerified(AppError):  # noqa: N818
    code, http_status = "EMAIL_NOT_VERIFIED", 403
    message = "Please verify your email first."
```

- [ ] **Step 4: Add `get_verified_user`**

```python
# app/api/deps.py  (append)
from app.core.errors import EmailNotVerified  # add to imports

def get_verified_user(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    if user.email_verified_at is None:
        raise EmailNotVerified()
    return user
```

- [ ] **Step 5: Implement the workspace service**

```python
# app/services/onboarding/workspace.py
from datetime import UTC, datetime
from sqlalchemy.orm import Session
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.invitation import Invitation
from app.db.models.startup import Startup, StartupProfile
from app.db.models.user import User
from app.platform.events import event_bus


def resolve_or_create_workspace(db: Session, user: User) -> Startup:
    existing = (db.query(Startup)
                .filter(Startup.created_by == user.id, Startup.deleted_at.is_(None))
                .order_by(Startup.created_at.desc()).first())
    if existing is not None:
        return existing
    startup = Startup(name=None, created_by=user.id)
    startup.profile = StartupProfile()
    db.add(startup)
    db.flush()
    db.add(Membership(user_id=user.id, startup_id=startup.id, role=MembershipRole.founder,
                      status=MembershipStatus.active, joined_at=datetime.now(UTC)))
    db.flush()
    event_bus.publish("workspace.created", {"startup_id": str(startup.id), "created_by": str(user.id)})
    return startup


def serialize_state(db: Session, startup: Startup, user: User) -> dict:
    p = startup.profile
    prof = user.profile
    invites = db.query(Invitation).filter_by(startup_id=startup.id).all()
    return {
        "step": p.onboarding_step,
        "completed": p.onboarding_completed_at is not None,
        "assessment_pending": p.assessment_pending,
        "founder_profile": {
            "full_name": prof.full_name if prof else None,
            "role_title": prof.role_title if prof else None,
            "country": prof.country if prof else None,
            "phone": prof.phone if prof else None,
            "how_heard": prof.how_heard if prof else None,
        },
        "startup": {
            "id": str(startup.id), "name": startup.name, "description": startup.description,
            "website": startup.website, "logo_url": startup.logo_url, "industry": startup.industry,
            "business_model": startup.business_model.value if startup.business_model else None,
            "stage": startup.stage.value if startup.stage else None,
        },
        "goals": p.goals or [], "notes": p.notes,
        "invites": [{"email": i.email, "role": i.role.value, "status": i.status.value} for i in invites],
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/api/test_verified_user.py tests/services/onboarding/test_workspace.py -v`
Expected: PASS (5 tests). Create empty `tests/services/onboarding/__init__.py`.

- [ ] **Step 7: Commit**

```bash
git add app/core/errors.py app/api/deps.py app/services/onboarding/ tests/api/test_verified_user.py tests/services/onboarding/
git commit -m "feat(onboarding): errors + verified-user dep + draft-workspace resolver"
```

---

## Task 4: GET /onboarding/state

**Files:**
- Create: `app/api/v1/endpoints/onboarding/__init__.py`, `app/api/v1/endpoints/onboarding/state.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/onboarding/test_state.py`

**Interfaces:**
- Consumes: `get_verified_user`, `resolve_or_create_workspace`, `serialize_state`.
- Produces: onboarding `router` mounted at `/api/v1/onboarding`; `GET /onboarding/state`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_state.py
from datetime import UTC, datetime
from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db, **kw):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC), **kw)
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}

def test_get_state_creates_draft(client, db):
    u, h = _auth(db); db.commit()
    r = client.get("/api/v1/onboarding/state", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["step"] == 1 and data["completed"] is False
    assert data["startup"]["id"] and data["startup"]["name"] is None

def test_get_state_requires_verified(client, db):
    u = create_user(db, status=UserStatus.pending_verification); db.commit()
    r = client.get("/api/v1/onboarding/state",
                   headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_state.py -v`
Expected: FAIL — router not mounted.

- [ ] **Step 3: Implement the endpoint + mount**

```python
# app/api/v1/endpoints/onboarding/state.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.services.onboarding.workspace import resolve_or_create_workspace, serialize_state

router = APIRouter()


@router.get("/state")
def get_state(user: User = Depends(get_verified_user), db: Session = Depends(get_db)):  # noqa: B008
    startup = resolve_or_create_workspace(db, user)
    body = serialize_state(db, startup, user)
    db.commit()
    return success_response(body)
```

```python
# app/api/v1/endpoints/onboarding/__init__.py
from fastapi import APIRouter
from app.api.v1.endpoints.onboarding import state

router = APIRouter()
router.include_router(state.router)
```

```python
# app/api/v1/api.py  (add)
from app.api.v1.endpoints.onboarding import router as onboarding_router
api_router.include_router(onboarding_router, prefix="/onboarding", tags=["onboarding"])
```

Create empty `tests/api/onboarding/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_state.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/onboarding/ app/api/v1/api.py tests/api/onboarding/
git commit -m "feat(onboarding): GET /onboarding/state"
```

---

## Task 5: PATCH /onboarding/state (autosave)

**Files:**
- Create: `app/schemas/onboarding.py`, `app/services/onboarding/steps.py`
- Modify: `app/api/v1/endpoints/onboarding/state.py`, `app/api/v1/endpoints/onboarding/__init__.py` (no change if same router)
- Test: `tests/api/onboarding/test_state_patch.py`

**Interfaces:**
- Consumes: `resolve_or_create_workspace`, `serialize_state`, enums `BusinessModel`/`StartupStage`.
- Produces: `OnboardingStatePatch` schema; `apply_step(db, startup, user, step, patch) -> None`; `PATCH /onboarding/state`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_state_patch.py
from datetime import UTC, datetime
from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}

def test_patch_step1_saves_founder_profile(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)  # create draft
    r = client.patch("/api/v1/onboarding/state", headers=h,
                     json={"step": 1, "full_name": "Ada Founder", "country": "NG"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["founder_profile"]["full_name"] == "Ada Founder"
    assert r.json()["data"]["step"] >= 1

def test_patch_step2_3_saves_startup(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
    r = client.patch("/api/v1/onboarding/state", headers=h,
                     json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"})
    d = r.json()["data"]["startup"]
    assert d["name"] == "Cofoundaz" and d["business_model"] == "b2b" and d["stage"] == "idea"

def test_patch_goals_max_3(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.patch("/api/v1/onboarding/state", headers=h,
                     json={"step": 4, "goals": ["a", "b", "c", "d"]})
    assert r.status_code == 422  # more than 3 goals rejected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_state_patch.py -v`
Expected: FAIL — PATCH route missing.

- [ ] **Step 3: Implement schema + steps service**

```python
# app/schemas/onboarding.py
from pydantic import BaseModel, Field
from app.db.models.enums import BusinessModel, StartupStage


class OnboardingStatePatch(BaseModel):
    step: int = Field(ge=1, le=6)
    # step 1
    full_name: str | None = None
    role_title: str | None = None
    country: str | None = None
    phone: str | None = None
    how_heard: str | None = None
    # step 2
    name: str | None = None
    description: str | None = None
    website: str | None = None
    # step 3
    industry: str | None = None
    business_model: BusinessModel | None = None
    stage: StartupStage | None = None
    # step 4
    goals: list[str] | None = Field(default=None, max_length=3)
    notes: str | None = None


class InviteItem(BaseModel):
    email: str
    role: str


class InvitesRequest(BaseModel):
    invites: list[InviteItem]


class AcceptRequest(BaseModel):
    token: str
```

```python
# app/services/onboarding/steps.py
from sqlalchemy.orm import Session
from app.db.models.startup import Startup
from app.db.models.user import User
from app.schemas.onboarding import OnboardingStatePatch

_PROFILE_FIELDS = ("full_name", "role_title", "country", "phone", "how_heard")
_STARTUP_FIELDS = ("name", "description", "website", "industry", "business_model", "stage")
_SPROFILE_FIELDS = ("goals", "notes")


def apply_step(db: Session, startup: Startup, user: User, patch: OnboardingStatePatch) -> None:
    data = patch.model_dump(exclude_unset=True, exclude={"step"})
    for f in _PROFILE_FIELDS:
        if f in data:
            setattr(user.profile, f, data[f])
    for f in _STARTUP_FIELDS:
        if f in data:
            setattr(startup, f, data[f])
    for f in _SPROFILE_FIELDS:
        if f in data:
            setattr(startup.profile, f, data[f])
    startup.profile.onboarding_step = max(startup.profile.onboarding_step, patch.step)
    db.flush()
```

- [ ] **Step 4: Implement the PATCH endpoint**

```python
# app/api/v1/endpoints/onboarding/state.py  (add)
from app.schemas.onboarding import OnboardingStatePatch
from app.services.onboarding.steps import apply_step


@router.patch("/state")
def patch_state(payload: OnboardingStatePatch, user: User = Depends(get_verified_user),  # noqa: B008
                db: Session = Depends(get_db)):  # noqa: B008
    startup = resolve_or_create_workspace(db, user)
    apply_step(db, startup, user, payload)
    body = serialize_state(db, startup, user)
    db.commit()
    return success_response(body)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_state_patch.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/onboarding.py app/services/onboarding/steps.py app/api/v1/endpoints/onboarding/state.py tests/api/onboarding/test_state_patch.py
git commit -m "feat(onboarding): PATCH /onboarding/state autosave per step"
```

---

## Task 6: POST /onboarding/logo

**Files:**
- Create: `app/api/v1/endpoints/onboarding/logo.py`
- Modify: `app/api/v1/endpoints/onboarding/__init__.py`
- Test: `tests/api/onboarding/test_logo.py`

**Interfaces:**
- Consumes: `get_verified_user`, `resolve_or_create_workspace`, `get_storage`.
- Produces: `POST /onboarding/logo` (multipart `file`) → `{ logo_url }`; sets `startups.logo_url`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_logo.py
import io
from datetime import UTC, datetime
from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}

def test_logo_upload_sets_url(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post("/api/v1/onboarding/logo", headers=h,
                    files={"file": ("logo.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["logo_url"]

def test_logo_rejects_non_image(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post("/api/v1/onboarding/logo", headers=h,
                    files={"file": ("x.txt", io.BytesIO(b"nope"), "text/plain")})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_logo.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement**

```python
# app/api/v1/endpoints/onboarding/logo.py
import uuid
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.platform.storage import get_storage
from app.services.onboarding.workspace import resolve_or_create_workspace

router = APIRouter()
_ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp"}
_MAX_BYTES = 2 * 1024 * 1024


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...),  # noqa: B008
                      user: User = Depends(get_verified_user),  # noqa: B008
                      db: Session = Depends(get_db)):  # noqa: B008
    if file.content_type not in _ALLOWED:
        raise AppError("VALIDATION_ERROR", "Logo must be a PNG, JPG, SVG, or WebP image.", 422)
    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise AppError("VALIDATION_ERROR", "Logo must be 2 MB or smaller.", 422)
    startup = resolve_or_create_workspace(db, user)
    ext = _ALLOWED[file.content_type]
    key = f"logos/{startup.id}/{uuid.uuid4().hex}.{ext}"
    startup.logo_url = get_storage().save(key, content, file.content_type)
    db.commit()
    return success_response({"logo_url": startup.logo_url})
```

```python
# app/api/v1/endpoints/onboarding/__init__.py  (add)
from app.api.v1.endpoints.onboarding import logo
router.include_router(logo.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_logo.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/onboarding/logo.py app/api/v1/endpoints/onboarding/__init__.py tests/api/onboarding/test_logo.py
git commit -m "feat(onboarding): POST /onboarding/logo via Storage seam"
```

---

## Task 7: POST /onboarding/invites

**Files:**
- Create: `app/services/onboarding/invites.py`, `app/api/v1/endpoints/onboarding/invites.py`
- Modify: `app/api/v1/endpoints/onboarding/__init__.py`
- Test: `tests/api/onboarding/test_invites.py`

**Interfaces:**
- Consumes: `resolve_or_create_workspace`, `hash_token`, `Invitation`, `InvitationStatus`, `MembershipRole`, `Membership`, `get_email_sender`, `EmailMessage`, `event_bus`, `User`.
- Produces: `create_invitations(db, startup, inviter, items: list[dict]) -> dict` (`{created, skipped}`); `POST /onboarding/invites`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_invites.py
from datetime import UTC, datetime
from app.core.security import create_access_token
from app.db.models.enums import UserStatus, InvitationStatus
from app.db.models.invitation import Invitation
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}

def test_invites_create_rows_and_email(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post("/api/v1/onboarding/invites", headers=h,
                    json={"invites": [{"email": "teammate@x.com", "role": "team_member"}]})
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["created"]) == 1
    rows = db.query(Invitation).filter(Invitation.email == "teammate@x.com").all()
    assert len(rows) == 1 and rows[0].status == InvitationStatus.pending

def test_invites_dedupe_pending(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    body = {"invites": [{"email": "dup@x.com", "role": "mentor"}]}
    client.post("/api/v1/onboarding/invites", headers=h, json=body)
    r = client.post("/api/v1/onboarding/invites", headers=h, json=body)  # again
    assert r.status_code == 200
    assert r.json()["data"]["skipped"] == ["dup@x.com"]
    assert db.query(Invitation).filter(Invitation.email == "dup@x.com").count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_invites.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement the invites service**

```python
# app/services/onboarding/invites.py
import secrets
from datetime import UTC, datetime, timedelta
from sqlalchemy.orm import Session
from app.db.models.enums import InvitationStatus, MembershipRole, MembershipStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.email import EmailMessage, get_email_sender
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token

_TTL = timedelta(days=14)


def _is_active_member_email(db: Session, startup: Startup, email: str) -> bool:
    return db.query(Membership).join(User, User.id == Membership.user_id).filter(
        Membership.startup_id == startup.id, Membership.status == MembershipStatus.active,
        User.email == email).first() is not None


def create_invitations(db: Session, startup: Startup, inviter: User, items: list[dict]) -> dict:
    created, skipped = [], []
    for item in items:
        email, role = item["email"], MembershipRole(item["role"])
        pending = db.query(Invitation).filter(
            Invitation.startup_id == startup.id, Invitation.email == email,
            Invitation.status == InvitationStatus.pending).first()
        if pending is not None or _is_active_member_email(db, startup, email):
            skipped.append(email)
            continue
        raw = secrets.token_urlsafe(32)
        db.add(Invitation(startup_id=startup.id, email=email, role=role,
                          token_hash=hash_token(raw), invited_by=inviter.id,
                          expires_at=datetime.now(UTC) + _TTL))
        get_email_sender().send(EmailMessage(
            to=email, subject=f"You're invited to join {startup.name or 'a startup'} on Cofoundaz",
            html=f'<p>Accept your invitation — token: <code>{raw}</code></p>'))
        event_bus.publish("workspace.member.invited",
                          {"startup_id": str(startup.id), "email": email, "role": role.value})
        created.append(email)
    db.flush()
    return {"created": created, "skipped": skipped}
```

- [ ] **Step 4: Implement the endpoint + mount**

```python
# app/api/v1/endpoints/onboarding/invites.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.onboarding import InvitesRequest
from app.services.onboarding.invites import create_invitations
from app.services.onboarding.workspace import resolve_or_create_workspace

router = APIRouter()


@router.post("/invites")
def post_invites(payload: InvitesRequest, user: User = Depends(get_verified_user),  # noqa: B008
                 db: Session = Depends(get_db)):  # noqa: B008
    startup = resolve_or_create_workspace(db, user)
    result = create_invitations(db, startup, user, [i.model_dump() for i in payload.invites])
    db.commit()
    return success_response(result)
```

```python
# app/api/v1/endpoints/onboarding/__init__.py  (add)
from app.api.v1.endpoints.onboarding import invites
router.include_router(invites.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_invites.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/services/onboarding/invites.py app/api/v1/endpoints/onboarding/invites.py app/api/v1/endpoints/onboarding/__init__.py tests/api/onboarding/test_invites.py
git commit -m "feat(onboarding): POST /onboarding/invites (create + dedupe + email)"
```

---

## Task 8: POST /onboarding/complete

**Files:**
- Create: `app/services/onboarding/complete.py`, `app/api/v1/endpoints/onboarding/complete.py`
- Modify: `app/api/v1/endpoints/onboarding/__init__.py`
- Test: `tests/api/onboarding/test_complete.py`

**Interfaces:**
- Consumes: `resolve_or_create_workspace`, `job_dispatcher.enqueue`, `event_bus`, `OnboardingIncomplete`.
- Produces: `complete_onboarding(db, startup, user) -> dict`; `POST /onboarding/complete`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_complete.py
from datetime import UTC, datetime
from app.core.security import create_access_token
from app.db.models.enums import UserStatus, JobStatus
from app.db.models.job import Job
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}

def _fill(client, h):
    client.get("/api/v1/onboarding/state", headers=h)
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 1, "full_name": "Ada"})
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
    client.patch("/api/v1/onboarding/state", headers=h,
                 json={"step": 3, "industry": "Fintech", "stage": "idea"})
    client.patch("/api/v1/onboarding/state", headers=h,
                 json={"step": 4, "goals": ["Get first customers"]})

def test_complete_gate_blocks_incomplete(client, db):
    u, h = _auth(db); db.commit()
    client.get("/api/v1/onboarding/state", headers=h)  # nothing filled
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ONBOARDING_INCOMPLETE"
    assert r.json()["error"]["field_errors"]

def test_complete_enqueues_two_jobs(client, db):
    u, h = _auth(db); db.commit()
    _fill(client, h)
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["job_ids"]) == 2 and data["assessment_pending"] is True
    types = {j.type for j in db.query(Job).filter(Job.status == JobStatus.queued).all()}
    assert {"roadmap.generate", "healthscore.initialize"} <= types

def test_complete_is_idempotent(client, db):
    u, h = _auth(db); db.commit()
    _fill(client, h)
    client.post("/api/v1/onboarding/complete", headers=h)
    before = db.query(Job).count()
    r = client.post("/api/v1/onboarding/complete", headers=h)  # again
    assert r.status_code == 200 and r.json()["data"]["completed"] is True
    assert db.query(Job).count() == before  # no new jobs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_complete.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement the complete service**

```python
# app/services/onboarding/complete.py
from datetime import UTC, datetime
from sqlalchemy.orm import Session
from app.core.errors import OnboardingIncomplete
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher


def _gate(startup: Startup, user: User) -> list[dict]:
    errs = []
    if not (user.profile and user.profile.full_name):
        errs.append({"field": "full_name", "message": "Tell us your name (step 1)."})
    if not startup.name:
        errs.append({"field": "name", "message": "Name your startup (step 2)."})
    if not startup.industry:
        errs.append({"field": "industry", "message": "Pick your industry (step 3)."})
    if not startup.stage:
        errs.append({"field": "stage", "message": "Pick your stage (step 3)."})
    if not (startup.profile.goals):
        errs.append({"field": "goals", "message": "Choose at least one goal (step 4)."})
    return errs


def complete_onboarding(db: Session, startup: Startup, user: User) -> dict:
    if startup.profile.onboarding_completed_at is not None:
        return {"completed": True, "assessment_pending": startup.profile.assessment_pending,
                "job_ids": []}
    errs = _gate(startup, user)
    if errs:
        raise OnboardingIncomplete(field_errors=errs)
    startup.profile.onboarding_completed_at = datetime.now(UTC)
    startup.profile.assessment_pending = True
    j1 = job_dispatcher.enqueue(db, "roadmap.generate", {"startup_id": str(startup.id)}, startup.id)
    j2 = job_dispatcher.enqueue(db, "healthscore.initialize", {"startup_id": str(startup.id)}, startup.id)
    event_bus.publish("onboarding.completed", {"startup_id": str(startup.id), "user_id": str(user.id)})
    event_bus.publish("notification.onboarding_complete",
                      {"startup_id": str(startup.id), "user_id": str(user.id)})
    db.flush()
    return {"completed": True, "assessment_pending": True, "job_ids": [str(j1.id), str(j2.id)]}
```

- [ ] **Step 4: Implement the endpoint + mount**

```python
# app/api/v1/endpoints/onboarding/complete.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.services.onboarding.complete import complete_onboarding
from app.services.onboarding.workspace import resolve_or_create_workspace

router = APIRouter()


@router.post("/complete")
def post_complete(user: User = Depends(get_verified_user),  # noqa: B008
                  db: Session = Depends(get_db)):  # noqa: B008
    startup = resolve_or_create_workspace(db, user)
    result = complete_onboarding(db, startup, user)
    db.commit()
    return success_response(result)
```

```python
# app/api/v1/endpoints/onboarding/__init__.py  (add)
from app.api.v1.endpoints.onboarding import complete
router.include_router(complete.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_complete.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/services/onboarding/complete.py app/api/v1/endpoints/onboarding/complete.py app/api/v1/endpoints/onboarding/__init__.py tests/api/onboarding/test_complete.py
git commit -m "feat(onboarding): POST /onboarding/complete (gate + job dispatch + idempotency)"
```

---

## Task 9: GET /invitations/{token} (public preview)

**Files:**
- Create: `app/api/v1/endpoints/invitations.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/onboarding/test_invitation_preview.py`

**Interfaces:**
- Consumes: `hash_token`, `Invitation`, `Startup`, `UserProfile`.
- Produces: `preview_invitation(db, token) -> dict`; `GET /invitations/{token}`; invitations `router` mounted at `/api/v1/invitations`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_invitation_preview.py
from app.services.auth.sessions import hash_token
from app.db.models.enums import MembershipRole
from tests.factories import create_user, create_startup, create_invitation


def test_preview_returns_invite_details(client, db):
    owner = create_user(db, email="founder@x.com")
    owner.profile.full_name = "Ada Founder"
    s = create_startup(db, owner=owner, name="Cofoundaz")
    create_invitation(db, s, email="invitee@x.com", role=MembershipRole.mentor,
                      inviter=owner, token_hash=hash_token("rawtok"))
    db.commit()
    r = client.get("/api/v1/invitations/rawtok")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["startup_name"] == "Cofoundaz" and d["role"] == "mentor"
    assert d["email"] == "invitee@x.com" and d["inviter_name"] == "Ada Founder"

def test_preview_unknown_token_404(client):
    assert client.get("/api/v1/invitations/nope").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_invitation_preview.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement preview service + endpoint + mount**

```python
# app/services/onboarding/invites.py  (append)
from app.core.errors import NotFound
from app.db.models.user import User, UserProfile


def preview_invitation(db: Session, token: str) -> dict:
    inv = db.query(Invitation).filter(Invitation.token_hash == hash_token(token)).first()
    if inv is None:
        raise NotFound()
    startup = db.query(Startup).filter(Startup.id == inv.startup_id).first()
    inviter = db.query(UserProfile).filter(UserProfile.user_id == inv.invited_by).first()
    return {
        "startup_name": startup.name if startup else None,
        "role": inv.role.value,
        "inviter_name": inviter.full_name if inviter else None,
        "email": inv.email,
        "status": inv.status.value,
    }
```

```python
# app/api/v1/endpoints/invitations.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.envelope import success_response
from app.db.session import get_db
from app.services.onboarding.invites import preview_invitation

router = APIRouter()


@router.get("/{token}")
def get_invitation(token: str, db: Session = Depends(get_db)):  # noqa: B008
    return success_response(preview_invitation(db, token))
```

```python
# app/api/v1/api.py  (add)
from app.api.v1.endpoints import invitations
api_router.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_invitation_preview.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/onboarding/invites.py app/api/v1/endpoints/invitations.py app/api/v1/api.py tests/api/onboarding/test_invitation_preview.py
git commit -m "feat(onboarding): GET /invitations/{token} public preview"
```

---

## Task 10: POST /invitations/accept (email-bound)

**Files:**
- Modify: `app/services/onboarding/invites.py`, `app/api/v1/endpoints/invitations.py`
- Test: `tests/api/onboarding/test_invitation_accept.py`

**Interfaces:**
- Consumes: `hash_token`, `Invitation`, `InvitationStatus`, `Membership`, `MembershipStatus`, `InviteEmailMismatch`, `TokenInvalid`, `event_bus`.
- Produces: `accept_invitation(db, user, token) -> Membership`; `POST /invitations/accept`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/onboarding/test_invitation_accept.py
from datetime import UTC, datetime, timedelta
from app.core.security import create_access_token
from app.services.auth.sessions import hash_token
from app.db.models.enums import UserStatus, MembershipRole, MembershipStatus, InvitationStatus
from app.db.models.membership import Membership
from app.db.models.invitation import Invitation
from tests.factories import create_user, create_startup, create_invitation


def _verified(db, email):
    return create_user(db, email=email, status=UserStatus.active, email_verified_at=datetime.now(UTC))

def test_accept_creates_membership(client, db):
    owner = create_user(db, email="f@x.com"); s = create_startup(db, owner=owner, name="Cofoundaz")
    invitee = _verified(db, "invitee@x.com")
    create_invitation(db, s, email="invitee@x.com", role=MembershipRole.mentor,
                      inviter=owner, token_hash=hash_token("tok1"))
    db.commit()
    r = client.post("/api/v1/invitations/accept", json={"token": "tok1"},
                    headers={"Authorization": f"Bearer {create_access_token(str(invitee.id))}"})
    assert r.status_code == 200, r.text
    m = db.query(Membership).filter(Membership.user_id == invitee.id, Membership.startup_id == s.id).one()
    assert m.role == MembershipRole.mentor and m.status == MembershipStatus.active
    assert db.query(Invitation).filter(Invitation.token_hash == hash_token("tok1")).one().status == InvitationStatus.accepted

def test_accept_wrong_email_403(client, db):
    owner = create_user(db, email="f2@x.com"); s = create_startup(db, owner=owner, name="X")
    other = _verified(db, "someoneelse@x.com")
    create_invitation(db, s, email="intended@x.com", role=MembershipRole.mentor,
                      inviter=owner, token_hash=hash_token("tok2"))
    db.commit()
    r = client.post("/api/v1/invitations/accept", json={"token": "tok2"},
                    headers={"Authorization": f"Bearer {create_access_token(str(other.id))}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"

def test_accept_expired_400(client, db):
    owner = create_user(db, email="f3@x.com"); s = create_startup(db, owner=owner, name="X")
    invitee = _verified(db, "late@x.com")
    create_invitation(db, s, email="late@x.com", role=MembershipRole.mentor, inviter=owner,
                      token_hash=hash_token("tok3"), expires_at=datetime.now(UTC) - timedelta(days=1))
    db.commit()
    r = client.post("/api/v1/invitations/accept", json={"token": "tok3"},
                    headers={"Authorization": f"Bearer {create_access_token(str(invitee.id))}"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/onboarding/test_invitation_accept.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement accept service**

```python
# app/services/onboarding/invites.py  (append)
from datetime import UTC, datetime as _dt
from app.core.errors import InviteEmailMismatch, TokenInvalid
from app.db.models.enums import MembershipStatus
from app.db.models.membership import Membership


def accept_invitation(db: Session, user: User, token: str) -> Membership:
    inv = db.query(Invitation).filter(Invitation.token_hash == hash_token(token)).first()
    now = _dt.now(UTC)
    if inv is None or inv.status != InvitationStatus.pending or inv.expires_at < now:
        raise TokenInvalid()
    if user.email.lower() != inv.email.lower():
        raise InviteEmailMismatch()
    existing = db.query(Membership).filter(
        Membership.user_id == user.id, Membership.startup_id == inv.startup_id).first()
    if existing is None:
        existing = Membership(user_id=user.id, startup_id=inv.startup_id, role=inv.role,
                              status=MembershipStatus.active, invited_by=inv.invited_by, joined_at=now)
        db.add(existing)
    inv.status = InvitationStatus.accepted
    inv.accepted_at = now
    inv.accepted_user_id = user.id
    db.flush()
    event_bus.publish("workspace.member.joined",
                      {"startup_id": str(inv.startup_id), "user_id": str(user.id), "role": inv.role.value})
    return existing
```

- [ ] **Step 4: Implement the endpoint**

```python
# app/api/v1/endpoints/invitations.py  (add)
from app.api.deps import get_verified_user
from app.db.models.user import User
from app.schemas.onboarding import AcceptRequest
from app.services.onboarding.invites import accept_invitation


@router.post("/accept")
def accept(payload: AcceptRequest, user: User = Depends(get_verified_user),  # noqa: B008
           db: Session = Depends(get_db)):  # noqa: B008
    m = accept_invitation(db, user, payload.token)
    db.commit()
    return success_response({"startup_id": str(m.startup_id), "role": m.role.value})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/api/onboarding/test_invitation_accept.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/services/onboarding/invites.py app/api/v1/endpoints/invitations.py tests/api/onboarding/test_invitation_accept.py
git commit -m "feat(onboarding): POST /invitations/accept (email-bound)"
```

---

## Task 11: Live E2E onboarding extension + SOP

**Files:**
- Create: `e2e/test_onboarding.py`, `docs/sop/2026-08-15-onboarding.md`
- Test: the e2e file itself (run via `make e2e`)

**Interfaces:**
- Consumes: the running server + the `make_verified_user`, `mailbox`, `http`, `unique_email` fixtures in `e2e/conftest.py`.
- Produces: a full onboarding journey test + the shipment SOP.

- [ ] **Step 1: Write the E2E journey**

```python
# e2e/test_onboarding.py
"""Live onboarding journey: founder walks the wizard, invites a teammate who
signs up + accepts, then completes onboarding and gets the two queued jobs.
"""
import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_onboarding_journey(base_url, make_verified_user, mailbox, unique_email):
    with httpx.Client(base_url=base_url, timeout=10.0) as founder:
        u = make_verified_user(founder)
        access = founder.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _auth_header(access)

        st = founder.get("/api/v1/onboarding/state", headers=h)
        assert st.status_code == 200 and st.json()["data"]["step"] == 1

        founder.patch("/api/v1/onboarding/state", headers=h, json={"step": 1, "full_name": "Ada Founder"})
        founder.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
        founder.patch("/api/v1/onboarding/state", headers=h,
                      json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"})
        founder.patch("/api/v1/onboarding/state", headers=h,
                      json={"step": 4, "goals": ["Get first customers"]})

        teammate_email = unique_email("mate")
        inv = founder.post("/api/v1/onboarding/invites", headers=h,
                           json={"invites": [{"email": teammate_email, "role": "team_member"}]})
        assert inv.status_code == 200 and teammate_email in inv.json()["data"]["created"]
        invite_token = mailbox.latest_token_for(teammate_email, subject_contains="invited")

    # Teammate signs up + verifies + accepts (separate client / user). Their email
    # MUST equal the invited email, because acceptance is email-bound — so sign up
    # explicitly with teammate_email rather than a random one.
    with httpx.Client(base_url=base_url, timeout=10.0) as mate:
        pw = "Mate-" + teammate_email.split("@")[0] + "-9"
        mate.post("/api/v1/auth/signup", json={"email": teammate_email, "password": pw})
        vtok = mailbox.latest_token_for(teammate_email, subject_contains="Verify")
        mate.post("/api/v1/auth/verify", json={"token": vtok})
        maccess = mate.post("/api/v1/auth/login", json={"email": teammate_email, "password": pw}).json()["data"]["access_token"]
        acc = mate.post("/api/v1/invitations/accept", json={"token": invite_token},
                        headers=_auth_header(maccess))
        assert acc.status_code == 200, acc.text
        me = mate.get("/api/v1/auth/me", headers=_auth_header(maccess)).json()["data"]
        assert any(ms["name"] == "Cofoundaz" for ms in me["memberships"])

    # Founder completes onboarding -> two queued jobs.
    with httpx.Client(base_url=base_url, timeout=10.0) as founder:
        access = founder.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        done = founder.post("/api/v1/onboarding/complete", headers=_auth_header(access))
        assert done.status_code == 200, done.text
        data = done.json()["data"]
        assert len(data["job_ids"]) == 2 and data["assessment_pending"] is True
        for jid in data["job_ids"]:
            job = founder.get(f"/api/v1/jobs/{jid}")
            assert job.status_code == 200 and job.json()["data"]["status"] == "queued"
```
Note: `make_verified_user(mate)` line creates a throwaway user; the real invited teammate is created by the explicit signup with `teammate_email` right after. Remove the throwaway `make_verified_user(mate)` call if it complicates — the dedicated signup is what matters. Keep the test's intent: the accepting user's email MUST equal the invited email.

- [ ] **Step 2: Run the E2E**

Run: `make e2e`
Expected: all prior e2e + the new onboarding journey PASS.

- [ ] **Step 3: Run the full unit suite + lint**

Run: `poetry run pytest -q && make lint`
Expected: all pass; `make lint` clean. Note the final test count + coverage.

- [ ] **Step 4: Write the SOP**

Create `docs/sop/2026-08-15-onboarding.md` per the repo convention: what shipped (the endpoints + invitations table), why, how (draft-workspace model, email-bound accept, complete→jobs), what's involved (files), verification (unit + e2e counts), follow-ups (AI panel Module 03, notifications Module 20, assessment Module 07).

- [ ] **Step 5: Commit**

```bash
git add e2e/test_onboarding.py docs/sop/2026-08-15-onboarding.md
git commit -m "test(e2e): onboarding journey + onboarding SOP"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §3 data model → T1/T2; §4.1 state → T3/T4/T5; §4.1 logo → T6; §4.2 invites → T7; §4.3 complete → T8; §4.4 preview/accept → T9/T10; §5 jobs/events → T8 (+ workspace.created in T3, member.invited in T7, member.joined in T10); §6 errors → T3 (defined) + used across; §7 testing → each task + T11 e2e.
- **Type consistency:** `resolve_or_create_workspace`, `serialize_state`, `apply_step`, `create_invitations`, `preview_invitation`, `accept_invitation`, `complete_onboarding`, `get_verified_user`, and the `OnboardingStatePatch`/`InvitesRequest`/`AcceptRequest` schemas are referenced consistently across tasks.
- **Deferred (by design):** onboarding AI panel (Module 03), real notifications (Module 20), assessment flow (Module 07), async worker draining jobs (Modules 05/06).
