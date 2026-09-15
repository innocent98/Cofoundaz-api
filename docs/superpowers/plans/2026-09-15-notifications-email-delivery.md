# Module 20 Slice 2 — Email Delivery + Preferences + Minimal Job Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver notifications by email — gated by per-`(user, workspace)` category preferences — through a new minimal background job worker that drains the existing `jobs` queue.

**Architecture:** The Slice 1 notification handler runs inside the triggering action's transaction. Slice 2 has it also enqueue an `email.notification` job (per opted-in recipient) *in that same transaction* — so an email job exists iff the action committed (no phantom email). A new separate worker process claims queued jobs with `FOR UPDATE SKIP LOCKED`, dispatches by type to a `JOB_HANDLERS` registry, and the email handler renders and sends via the existing `get_email_sender()` seam, with bounded retries + backoff and a stale-running reaper.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 (typed `Mapped`) / Alembic / PostgreSQL / Pydantic v2 / pytest (real Postgres, per-test savepoint rollback) / Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-15-notifications-email-delivery-design.md`

## Global Constraints

- **Envelope:** every endpoint returns `success_response(data)`; errors via `AppError(code, message, http_status, field_errors=)` (`NotFound` 404). Copy verbatim from existing endpoints.
- **RBAC/tenancy:** endpoints use `require_workspace` (returns `Membership`) + `get_verified_user`; scope every query to `membership.user_id` AND `membership.startup_id`.
- **Enum columns:** none added here. FK columns get a standalone `index=True` (FK-index convention).
- **Migrations:** one revision, chained onto the current develop head `0021_notifications`. Coordinate the number with Module 17 (PR #59) so there is exactly ONE alembic head; whoever merges second renumbers.
- **Same-transaction rule:** the handler enqueues email jobs inside its existing savepoint; no `db.commit()` in service/handler code (endpoints and the worker loop own commits).
- **In-app delivery is NEVER gated** — the feed always gets the row; only email is preference-controlled.
- **No AI attribution** in any commit message or PR/issue/review body (no `Co-Authored-By`, no "Generated with" footer). This overrides any tooling default.
- **CI locally before push:** reproduce black/isort/ruff/mypy/pylint/bandit + pytest (cov ≥95) + alembic single-head/upgrade/downgrade + e2e with the pinned `poetry` toolchain.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/db/models/notification_preference.py` | `NotificationPreference` model (per-`(user,workspace)` prefs row) |
| `app/db/models/job.py` (modify) | add `attempts`, `run_after` mapped columns |
| `app/db/models/__init__.py` (modify) | import the new model so metadata/migrations see it |
| `alembic/versions/00NN_notifications_email.py` | new: `notification_preferences` table + two `jobs` columns |
| `app/services/notifications/categories.py` | category keys, event→category map, defaults, `category_for` |
| `app/services/notifications/preferences.py` | `effective_preferences`, `set_preferences`, `email_enabled` |
| `app/services/notifications/registry.py` (modify) | `_handle` enqueues email jobs for opted-in recipients |
| `app/schemas/notification.py` | prefs `PUT` request model |
| `app/api/v1/endpoints/notifications.py` (modify) | `GET`/`PUT /notifications/preferences` |
| `app/worker/runner.py` | `JOB_HANDLERS`, `register`, `reap_stale`, `claim_batch`, `run_once`, `main_loop` |
| `app/worker/handlers/email.py` | `handle_email_notification`, `deep_link`, `render_email` |
| `app/worker/__main__.py` | process entrypoint (session, register, loop, SIGTERM) |
| `app/core/config.py` (modify) | `WORKER_*` + `APP_BASE_URL` settings |
| `docker-compose.prod.yml`, `docker-compose.yml` (modify) | new `worker` service |
| `tests/db/test_notification_preference_model.py`, `tests/services/notifications/test_preferences.py`, `tests/api/test_notification_preferences.py`, `tests/services/notifications/test_email_enqueue.py`, `tests/worker/test_runner.py`, `tests/worker/test_email_handler.py` | unit tests |
| `e2e/test_notifications_email.py` | live e2e journey |
| `e2e/conftest.py` (modify) | add `mailbox.latest_for(email)` helper |
| `docs/fe-integration-guide-notifications.md`, `docs/sop/…`, `docs/checklist/PROJECT_CHECKLIST.md` | docs |

---

## Task 1: Data model — `notification_preferences` table + `jobs` worker columns + migration

**Files:**
- Create: `app/db/models/notification_preference.py`
- Modify: `app/db/models/job.py`, `app/db/models/__init__.py`
- Create: `alembic/versions/00NN_notifications_email.py` (number settled at build time)
- Test: `tests/db/test_notification_preference_model.py`

**Interfaces:**
- Produces: `NotificationPreference(user_id, startup_id, master_email: bool, categories: dict)`; `Job.attempts: int`, `Job.run_after: datetime | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_notification_preference_model.py
import uuid
from datetime import UTC, datetime

from app.db.models.job import Job
from app.db.models.enums import JobStatus
from app.db.models.notification_preference import NotificationPreference
from tests.factories import create_startup, create_user


def test_preference_row_roundtrips(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    pref = NotificationPreference(
        user_id=u.id, startup_id=s.id, master_email=True, categories={"documents": False}
    )
    db.add(pref)
    db.flush()
    got = db.query(NotificationPreference).filter_by(user_id=u.id, startup_id=s.id).one()
    assert got.master_email is True and got.categories == {"documents": False}


def test_job_has_worker_columns(db):
    job = Job(type="email.notification", payload={"notification_id": str(uuid.uuid4())},
              status=JobStatus.queued)
    db.add(job)
    db.flush()
    assert job.attempts == 0 and job.run_after is None
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/db/test_notification_preference_model.py -v`
Expected: FAIL — `ModuleNotFoundError: app.db.models.notification_preference` / `Job` has no `attempts`.

- [ ] **Step 3: Add the two `jobs` columns**

```python
# app/db/models/job.py — add imports and two columns to class Job
from datetime import datetime
from sqlalchemy import DateTime, Integer, String  # extend existing import line

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Create the preferences model**

```python
# app/db/models/notification_preference.py
import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class NotificationPreference(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    master_email: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    categories: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    __table_args__ = (
        UniqueConstraint("user_id", "startup_id", name="uq_notification_preferences_user_startup"),
    )
```

Add to `app/db/models/__init__.py` (mirror the existing import lines):

```python
from app.db.models.notification_preference import NotificationPreference  # noqa: F401
```

- [ ] **Step 5: Generate + hand-finish the migration**

Run: `poetry run alembic revision --autogenerate -m "notifications email: preferences + jobs worker columns"`
Then in the new file, set (leave `upgrade()`/`downgrade()` bodies as generated, verify they contain the `notification_preferences` create + the two `op.add_column('jobs', ...)`):

```python
revision = "00NN_notifications_email"   # next free number after 0021 at build time (coordinate w/ Module 17)
down_revision = "0021_notifications"
```

Confirm `downgrade()` drops the two `jobs` columns and the `notification_preferences` table (op.drop_column / op.drop_table). The FK indexes are `ix_notification_preferences_user_id` / `ix_notification_preferences_startup_id` via `op.f(...)`.

- [ ] **Step 6: Run tests + migration round-trip**

Run:
```
poetry run pytest tests/db/test_notification_preference_model.py -v
poetry run alembic heads            # expect exactly one
poetry run alembic upgrade head && poetry run alembic check
poetry run alembic downgrade -1 && poetry run alembic upgrade head
```
Expected: tests PASS; one head; no drift; round-trip clean.

- [ ] **Step 7: Commit**

```bash
git add app/db/models/notification_preference.py app/db/models/job.py app/db/models/__init__.py alembic/versions/00NN_notifications_email.py tests/db/test_notification_preference_model.py
git commit -m "feat(notifications): preferences table + jobs worker columns (migration)"
```

---

## Task 2: Categories map + preferences service

**Files:**
- Create: `app/services/notifications/categories.py`, `app/services/notifications/preferences.py`
- Test: `tests/services/notifications/test_preferences.py`

**Interfaces:**
- Consumes: `NotificationPreference` (Task 1).
- Produces:
  - `CATEGORIES: tuple[str, ...]`, `EVENT_CATEGORY: dict[str, str]`, `CATEGORY_DEFAULTS: dict[str, bool]`, `category_for(event: str) -> str | None`
  - `effective_preferences(db, *, user_id, startup_id) -> dict` → `{"master_email": bool, "categories": {k: bool for k in CATEGORIES}}`
  - `set_preferences(db, *, user_id, startup_id, master_email: bool | None, categories: dict[str, bool] | None) -> dict`
  - `email_enabled(db, *, user_id, startup_id, category: str | None) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/notifications/test_preferences.py
from datetime import UTC, datetime

from app.services.notifications.categories import CATEGORIES, category_for
from app.services.notifications.preferences import (
    effective_preferences, email_enabled, set_preferences,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    return u, create_startup(db, owner=u)


def test_category_for_maps_events():
    assert category_for("document.shared") == "documents"
    assert category_for("mission.completed") == "roadmap_missions"
    assert category_for("workspace.member.joined") == "team"
    assert category_for("unknown.event") is None


def test_effective_defaults_all_on_when_unset(db):
    u, s = _ctx(db)
    eff = effective_preferences(db, user_id=u.id, startup_id=s.id)
    assert eff["master_email"] is True
    assert set(eff["categories"]) == set(CATEGORIES)
    assert all(eff["categories"].values())


def test_set_and_email_enabled(db):
    u, s = _ctx(db)
    set_preferences(db, user_id=u.id, startup_id=s.id, master_email=True,
                    categories={"documents": False})
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category="documents") is False
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category="team") is True
    # master off overrides everything
    set_preferences(db, user_id=u.id, startup_id=s.id, master_email=False, categories=None)
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category="team") is False
    # uncategorized -> never emails
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category=None) is False
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/services/notifications/test_preferences.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `categories.py`**

```python
# app/services/notifications/categories.py
"""Notification categories: the coarse groups the FE toggles email by (spec D3).

Single source of truth for event -> category. In-app delivery ignores this
(always on); it governs only the email channel.
"""

CATEGORIES: tuple[str, ...] = (
    "documents", "business", "roadmap_missions", "health_assessment", "team",
)

EVENT_CATEGORY: dict[str, str] = {
    "document.shared": "documents",
    "document.signature.requested": "documents",
    "document.signature.signed": "documents",
    "document.signature.completed": "documents",
    "business.suggestion.created": "business",
    "business.suggestion.approved": "business",
    "business.suggestion.rejected": "business",
    "business.artifact.completed": "business",
    "roadmap.replanned": "roadmap_missions",
    "roadmap.milestone.completed": "roadmap_missions",
    "mission.completed": "roadmap_missions",
    "mission.streak.milestone": "roadmap_missions",
    "healthscore.dropped": "health_assessment",
    "assessment.completed": "health_assessment",
    "workspace.member.joined": "team",
}

# All ON (opt-out model, spec D3). New categories default ON here.
CATEGORY_DEFAULTS: dict[str, bool] = {c: True for c in CATEGORIES}


def category_for(event: str) -> str | None:
    """The category an event emails under, or None (⇒ in-app only)."""
    return EVENT_CATEGORY.get(event)
```

- [ ] **Step 4: Implement `preferences.py`**

```python
# app/services/notifications/preferences.py
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.notification_preference import NotificationPreference
from app.services.notifications.categories import CATEGORIES, CATEGORY_DEFAULTS


def _row(db: Session, user_id: uuid.UUID, startup_id: uuid.UUID) -> NotificationPreference | None:
    return (
        db.query(NotificationPreference)
        .filter_by(user_id=user_id, startup_id=startup_id)
        .first()
    )


def effective_preferences(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID) -> dict[str, Any]:
    """Stored prefs merged over defaults; every category key present."""
    row = _row(db, user_id, startup_id)
    stored = row.categories if row is not None else {}
    return {
        "master_email": row.master_email if row is not None else True,
        "categories": {c: bool(stored.get(c, CATEGORY_DEFAULTS[c])) for c in CATEGORIES},
    }


def set_preferences(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID,
    master_email: bool | None, categories: dict[str, bool] | None,
) -> dict[str, Any]:
    """Upsert; master_email and/or a subset of category keys. Returns effective prefs."""
    row = _row(db, user_id, startup_id)
    if row is None:
        row = NotificationPreference(user_id=user_id, startup_id=startup_id,
                                     master_email=True, categories={})
        db.add(row)
    if master_email is not None:
        row.master_email = master_email
    if categories:
        merged = dict(row.categories)
        merged.update({k: bool(v) for k, v in categories.items() if k in CATEGORIES})
        row.categories = merged
    db.flush()
    return effective_preferences(db, user_id=user_id, startup_id=startup_id)


def email_enabled(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, category: str | None
) -> bool:
    """Whether this recipient wants email for this category (enqueue-time gate)."""
    if category is None:
        return False
    eff = effective_preferences(db, user_id=user_id, startup_id=startup_id)
    return bool(eff["master_email"] and eff["categories"].get(category, False))
```

- [ ] **Step 5: Run tests + verify pass**

Run: `poetry run pytest tests/services/notifications/test_preferences.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/notifications/categories.py app/services/notifications/preferences.py tests/services/notifications/test_preferences.py
git commit -m "feat(notifications): category map + email preferences service"
```

---

## Task 3: Preferences endpoints + request schema

**Files:**
- Modify: `app/schemas/notification.py`, `app/api/v1/endpoints/notifications.py`
- Test: `tests/api/test_notification_preferences.py`

**Interfaces:**
- Consumes: `effective_preferences`, `set_preferences` (Task 2), `require_workspace`, `get_verified_user`, `success_response`.
- Produces: `GET /notifications/preferences`, `PUT /notifications/preferences`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_notification_preferences.py
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user


def _member(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_get_returns_defaults(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/notifications/preferences", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["master_email"] is True and data["categories"]["documents"] is True


def test_put_upserts_and_roundtrips(client, db):
    _u, _s, h = _member(db)
    r = client.put("/api/v1/notifications/preferences", headers=h,
                   json={"categories": {"documents": False}})
    assert r.status_code == 200 and r.json()["data"]["categories"]["documents"] is False
    got = client.get("/api/v1/notifications/preferences", headers=h).json()["data"]
    assert got["categories"]["documents"] is False and got["categories"]["team"] is True


def test_put_unknown_category_422(client, db):
    _u, _s, h = _member(db)
    r = client.put("/api/v1/notifications/preferences", headers=h,
                   json={"categories": {"not_a_category": False}})
    assert r.status_code == 422
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/api/test_notification_preferences.py -v`
Expected: FAIL — 404 (routes absent).

- [ ] **Step 3: Add the request schema**

```python
# app/schemas/notification.py — add
from pydantic import BaseModel, field_validator

from app.services.notifications.categories import CATEGORIES


class PreferencesUpdate(BaseModel):
    master_email: bool | None = None
    categories: dict[str, bool] | None = None

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v:
            unknown = set(v) - set(CATEGORIES)
            if unknown:
                raise ValueError(f"unknown categories: {sorted(unknown)}")
        return v
```

- [ ] **Step 4: Add the endpoints**

```python
# app/api/v1/endpoints/notifications.py — add imports and two routes
from app.schemas.notification import PreferencesUpdate
from app.services.notifications.preferences import effective_preferences, set_preferences


@router.get("/notifications/preferences")
def get_preferences_endpoint(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        effective_preferences(db, user_id=membership.user_id, startup_id=membership.startup_id)
    )


@router.put("/notifications/preferences")
def put_preferences_endpoint(
    body: PreferencesUpdate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    eff = set_preferences(
        db, user_id=membership.user_id, startup_id=membership.startup_id,
        master_email=body.master_email, categories=body.categories,
    )
    db.commit()
    return success_response(eff)
```

- [ ] **Step 5: Run tests + verify pass**

Run: `poetry run pytest tests/api/test_notification_preferences.py -v`
Expected: PASS (incl. the 422 — a `ValueError` in a validator becomes a 422 in this app's envelope).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/notification.py app/api/v1/endpoints/notifications.py tests/api/test_notification_preferences.py
git commit -m "feat(notifications): GET/PUT notification preferences endpoints"
```

---

## Task 4: Handler enqueues email jobs for opted-in recipients

**Files:**
- Modify: `app/services/notifications/registry.py`
- Test: `tests/services/notifications/test_email_enqueue.py`

**Interfaces:**
- Consumes: `email_enabled`, `category_for` (Task 2), `create_notifications` (returns `list[Notification]`), `job_dispatcher.enqueue(db, type, payload, startup_id)`.
- Produces: an `email.notification` Job row (`payload={"notification_id": <str>}`) per opted-in recipient, in the action's transaction.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/notifications/test_email_enqueue.py
from datetime import UTC, datetime

from app.db.models.job import Job
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.preferences import set_preferences
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


def _two_members(db):
    a = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=a)
    create_membership(db, a, s)
    b = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, b, s)
    return a, b, s


def _email_jobs(db):
    return db.query(Job).filter(Job.type == "email.notification").all()


def test_enqueues_one_email_job_per_opted_in_recipient(db):
    register()
    a, b, s = _two_members(db)
    # A shares; recipient is B (members minus actor). B keeps default (documents ON).
    event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by_id": str(a.id)})
    jobs = _email_jobs(db)
    assert len(jobs) == 1
    nid = jobs[0].payload["notification_id"]
    assert db.query(Notification).filter_by(id=nid, user_id=b.id).one()


def test_category_off_still_creates_inapp_but_no_email(db):
    register()
    a, b, s = _two_members(db)
    set_preferences(db, user_id=b.id, startup_id=s.id, master_email=True,
                    categories={"documents": False})
    event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by_id": str(a.id)})
    assert _email_jobs(db) == []
    assert db.query(Notification).filter_by(user_id=b.id).count() == 1  # in-app still created


def test_rolled_back_action_leaves_no_email_job(db):
    register()
    a, _b, s = _two_members(db)
    with db.begin_nested() as sp:
        event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by_id": str(a.id)})
        sp.rollback()
    assert _email_jobs(db) == []
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/services/notifications/test_email_enqueue.py -v`
Expected: FAIL — no email jobs enqueued (current `_handle` only creates in-app rows).

- [ ] **Step 3: Modify `_handle` to enqueue email jobs**

```python
# app/services/notifications/registry.py
# add imports:
from app.platform.jobs import job_dispatcher
from app.services.notifications.categories import category_for
from app.services.notifications.preferences import email_enabled

# replace the create_notifications call in _handle with capturing rows + enqueue:
def _handle(db: Session, event: str, payload: dict) -> None:
    spec = SPECS.get(event)
    if spec is None or not payload.get("startup_id"):
        return
    user_ids = spec.recipients(db, payload)
    if not user_ids:
        return
    startup_id = uuid.UUID(str(payload["startup_id"]))
    rows = create_notifications(
        db, user_ids=user_ids, startup_id=startup_id,
        type=event, title=spec.title(payload), body=spec.body(payload), data=payload,
    )
    category = category_for(event)
    for n in rows:
        if email_enabled(db, user_id=n.user_id, startup_id=n.startup_id, category=category):
            job_dispatcher.enqueue(db, "email.notification", {"notification_id": str(n.id)}, startup_id)
```

- [ ] **Step 4: Run tests + verify pass**

Run: `poetry run pytest tests/services/notifications/test_email_enqueue.py -v`
Expected: PASS.

- [ ] **Step 5: Run the Slice 1 notification tests to confirm no regression**

Run: `poetry run pytest tests/services/notifications/ tests/api/test_notifications.py -q`
Expected: PASS (in-app behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/services/notifications/registry.py tests/services/notifications/test_email_enqueue.py
git commit -m "feat(notifications): enqueue email jobs for opted-in recipients (in-txn)"
```

---

## Task 5: Worker runner (claim / dispatch / retry / reaper) + config

**Files:**
- Create: `app/worker/__init__.py` (empty), `app/worker/runner.py`
- Modify: `app/core/config.py`
- Test: `tests/worker/__init__.py` (empty), `tests/worker/test_runner.py`

**Interfaces:**
- Consumes: `Job`, `JobStatus`, settings `WORKER_*`.
- Produces:
  - `Handler = Callable[[Session, Job], None]`, `JOB_HANDLERS: dict[str, Handler]`, `register_handler(type, handler)`
  - `reap_stale(db, *, stale_seconds) -> int`
  - `claim_batch(db, *, batch, stale_seconds) -> list[Job]`
  - `run_once(db) -> int` (number of jobs processed)
  - `backoff_seconds(attempts) -> int`

- [ ] **Step 1: Add config settings**

```python
# app/core/config.py — add near the Providers block
    # Background worker (Module 20 Slice 2)
    WORKER_POLL_INTERVAL: float = 2.0
    WORKER_BATCH_SIZE: int = 10
    WORKER_MAX_ATTEMPTS: int = 5
    WORKER_STALE_SECONDS: int = 300
    APP_BASE_URL: str = ""  # frontend origin for email deep links; falls back to SERVER_HOST
```

- [ ] **Step 2: Write the failing test**

```python
# tests/worker/test_runner.py
from datetime import UTC, datetime, timedelta

from app.db.models.enums import JobStatus
from app.db.models.job import Job
from app.worker import runner


def _job(db, **kw):
    job = Job(type=kw.pop("type", "t.ok"), payload=kw.pop("payload", {}),
              status=kw.pop("status", JobStatus.queued), **kw)
    db.add(job)
    db.flush()
    return job


def test_run_once_success_marks_succeeded(db):
    seen = []
    runner.JOB_HANDLERS.clear()
    runner.register_handler("t.ok", lambda d, j: seen.append(j.id))
    job = _job(db)
    assert runner.run_once(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.succeeded and seen == [job.id]


def test_run_once_retry_then_fail(db):
    runner.JOB_HANDLERS.clear()
    def boom(d, j): raise RuntimeError("nope")
    runner.register_handler("t.boom", boom)
    job = _job(db, type="t.boom")
    runner.run_once(db)                      # attempt 1 -> requeued
    db.refresh(job)
    assert job.status == JobStatus.queued and job.attempts == 1 and job.run_after is not None
    assert "nope" in (job.error or "")
    # exhaust attempts
    job.attempts = 4  # WORKER_MAX_ATTEMPTS - 1
    job.run_after = None
    db.flush()
    runner.run_once(db)                      # attempt 5 -> failed
    db.refresh(job)
    assert job.status == JobStatus.failed


def test_run_once_unknown_type_fails(db):
    runner.JOB_HANDLERS.clear()
    job = _job(db, type="t.nohandler")
    runner.run_once(db)
    db.refresh(job)
    assert job.status == JobStatus.failed and "no handler" in (job.error or "")


def test_claim_skips_future_run_after(db):
    runner.JOB_HANDLERS.clear()
    runner.register_handler("t.ok", lambda d, j: None)
    _job(db, run_after=datetime.now(UTC) + timedelta(hours=1))
    assert runner.run_once(db) == 0          # not due yet


def test_reap_stale_requeues_running(db):
    job = _job(db, status=JobStatus.running)
    job.updated_at = datetime.now(UTC) - timedelta(seconds=999)
    db.flush()
    assert runner.reap_stale(db, stale_seconds=300) == 1
    db.refresh(job)
    assert job.status == JobStatus.queued
```

- [ ] **Step 3: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_runner.py -v`
Expected: FAIL — `app.worker.runner` missing.

- [ ] **Step 4: Implement `runner.py`**

```python
# app/worker/runner.py
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.enums import JobStatus
from app.db.models.job import Job

Handler = Callable[[Session, Job], None]
JOB_HANDLERS: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    JOB_HANDLERS[job_type] = handler


def backoff_seconds(attempts: int) -> int:
    """Exponential backoff, capped at 1h. attempts is 1-based (post-increment)."""
    return min(30 * (2 ** (attempts - 1)), 3600)


def reap_stale(db: Session, *, stale_seconds: int) -> int:
    """Re-queue jobs stuck in RUNNING past the stale window (crashed worker)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    n = (
        db.query(Job)
        .filter(Job.status == JobStatus.running, Job.updated_at < cutoff)
        .update({Job.status: JobStatus.queued}, synchronize_session=False)
    )
    db.commit()
    return n


def claim_batch(db: Session, *, batch: int, stale_seconds: int) -> list[Job]:
    reap_stale(db, stale_seconds=stale_seconds)
    now = datetime.now(UTC)
    jobs = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.queued,
            (Job.run_after.is_(None)) | (Job.run_after <= now),
        )
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(batch)
        .all()
    )
    for job in jobs:
        job.status = JobStatus.running
        job.attempts = job.attempts + 1
    db.commit()  # publish the claim so a crash leaves rows RUNNING, not lost
    return jobs


def _finalize_success(db: Session, job: Job) -> None:
    job.status = JobStatus.succeeded
    job.error = None
    db.commit()


def _finalize_failure(db: Session, job: Job, exc: Exception) -> None:
    job.error = str(exc)
    if job.attempts < settings.WORKER_MAX_ATTEMPTS:
        job.status = JobStatus.queued
        job.run_after = datetime.now(UTC) + timedelta(seconds=backoff_seconds(job.attempts))
    else:
        job.status = JobStatus.failed
    db.commit()


def run_once(db: Session) -> int:
    """Claim one batch and run each job in its own transaction. Returns count processed."""
    jobs = claim_batch(db, batch=settings.WORKER_BATCH_SIZE, stale_seconds=settings.WORKER_STALE_SECONDS)
    for job in jobs:
        handler = JOB_HANDLERS.get(job.type)
        if handler is None:
            _finalize_failure(db, job, RuntimeError(f"no handler for {job.type!r}"))
            continue
        try:
            handler(db, job)
            _finalize_success(db, job)
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the loop
            db.rollback()
            db.refresh(job)
            log.warning(f"[worker] job {job.id} ({job.type}) failed: {exc}")
            _finalize_failure(db, job, exc)
    return len(jobs)
```

Create `app/worker/__init__.py` and `tests/worker/__init__.py` (empty files).

- [ ] **Step 5: Run tests + verify pass**

Run: `poetry run pytest tests/worker/test_runner.py -v`
Expected: PASS.

Note on the test DB: the `db` fixture runs in a savepoint and `db.commit()` there commits to the savepoint (not the real DB), so state transitions are observable within the test and rolled back after — correct for these unit tests. True cross-connection `SKIP LOCKED` contention is a production property (verified by design + the `with_for_update(skip_locked=True)` claim), not asserted here.

- [ ] **Step 6: Commit**

```bash
git add app/worker/__init__.py app/worker/runner.py app/core/config.py tests/worker/
git commit -m "feat(worker): job runner — claim (SKIP LOCKED), retry/backoff, stale reaper"
```

---

## Task 6: Email job handler + content (deep link, template)

**Files:**
- Create: `app/worker/handlers/__init__.py` (empty), `app/worker/handlers/email.py`
- Test: `tests/worker/test_email_handler.py`

**Interfaces:**
- Consumes: `Notification`, `User`, `get_email_sender()`, `EmailMessage`, `category_for`, settings `APP_BASE_URL`/`SERVER_HOST`, `register_handler` (Task 5).
- Produces: `handle_email_notification(db, job)`, `deep_link(notification) -> str`, `render_email(notification) -> str`, and registers `email.notification`.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_email_handler.py
from datetime import UTC, datetime

from app.db.models.job import Job
from app.db.models.enums import JobStatus
from app.platform import email as email_mod
from app.services.notifications.service import create_notifications
from app.worker.handlers import email as email_handler
from tests.factories import create_membership, create_startup, create_user


def test_handle_sends_one_email(db, monkeypatch):
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(email_handler, "get_email_sender", lambda: sender)
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    (n,) = create_notifications(db, user_ids=[u.id], startup_id=s.id,
                                type="document.shared", title="A document was shared",
                                body="", data={})
    job = Job(type="email.notification", payload={"notification_id": str(n.id)},
              status=JobStatus.running)
    email_handler.handle_email_notification(db, job)
    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg.to == u.email and msg.subject == "A document was shared"
    assert "/documents" in msg.html


def test_handle_missing_notification_is_noop(db, monkeypatch):
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(email_handler, "get_email_sender", lambda: sender)
    import uuid
    job = Job(type="email.notification", payload={"notification_id": str(uuid.uuid4())},
              status=JobStatus.running)
    email_handler.handle_email_notification(db, job)
    assert sender.sent == []
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_email_handler.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `handlers/email.py`**

```python
# app/worker/handlers/email.py
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.db.models.user import User
from app.platform.email import EmailMessage, get_email_sender
from app.services.notifications.categories import category_for
from app.worker.runner import register_handler

_CATEGORY_PATH = {
    "documents": "/documents",
    "business": "/business-builder",
    "roadmap_missions": "/roadmap",
    "health_assessment": "/health",
    "team": "/team",
}


def deep_link(notification: Notification) -> str:
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    path = _CATEGORY_PATH.get(category_for(notification.type) or "", "/notifications")
    return f"{base}{path}"


def render_email(notification: Notification) -> str:
    link = deep_link(notification)
    body = notification.body or ""
    return (
        f'<div style="font-family:system-ui,sans-serif;max-width:520px">'
        f"<h2>{notification.title}</h2>"
        f"<p>{body}</p>"
        f'<p><a href="{link}" style="display:inline-block;padding:10px 16px;'
        f'background:#4f46e5;color:#fff;border-radius:6px;text-decoration:none">Open Cofoundaz</a></p>'
        f'<hr><p style="font-size:12px;color:#666">'
        f'Manage your notification preferences in Settings.</p></div>'
    )


def handle_email_notification(db: Session, job: Job) -> None:
    notification = db.get(Notification, uuid.UUID(str(job.payload["notification_id"])))
    if notification is None:
        return  # deleted since enqueue — nothing to send
    user = db.get(User, notification.user_id)
    if user is None or not user.email:
        return
    get_email_sender().send(
        EmailMessage(to=user.email, subject=notification.title, html=render_email(notification))
    )


register_handler("email.notification", handle_email_notification)
```

Create `app/worker/handlers/__init__.py` (empty).

- [ ] **Step 4: Run tests + verify pass**

Run: `poetry run pytest tests/worker/test_email_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ tests/worker/test_email_handler.py
git commit -m "feat(worker): email.notification handler — render + deep link + send"
```

---

## Task 7: Worker entrypoint + docker-compose service

**Files:**
- Create: `app/worker/__main__.py`
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`
- Test: `tests/worker/test_entrypoint.py`

**Interfaces:**
- Consumes: `SessionLocal`, `run_once`, settings `WORKER_POLL_INTERVAL`; importing `app.worker.handlers.email` registers the handler.
- Produces: `register()` (imports handler modules), `main_loop(stop: Callable[[], bool])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_entrypoint.py
def test_register_wires_email_handler():
    from app.worker import runner
    from app.worker.__main__ import register
    runner.JOB_HANDLERS.clear()
    register()
    assert "email.notification" in runner.JOB_HANDLERS


def test_main_loop_runs_until_stop(db, monkeypatch):
    import app.worker.__main__ as entry
    calls = {"n": 0}
    monkeypatch.setattr(entry, "run_once", lambda d: calls.__setitem__("n", calls["n"] + 1) or 0)
    monkeypatch.setattr(entry, "SessionLocal", lambda: db)
    monkeypatch.setattr(entry.time, "sleep", lambda _s: None)
    stops = iter([False, False, True])
    entry.main_loop(stop=lambda: next(stops))
    assert calls["n"] == 2
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_entrypoint.py -v`
Expected: FAIL — `app.worker.__main__` missing.

- [ ] **Step 3: Implement `__main__.py`**

```python
# app/worker/__main__.py
import signal
import time
from collections.abc import Callable

from app.core.config import settings
from app.core.logger import log
from app.db.session import SessionLocal
from app.worker.runner import run_once


def register() -> None:
    """Import handler modules for their register_handler(...) side effects."""
    import app.worker.handlers.email  # noqa: F401


def main_loop(stop: Callable[[], bool]) -> None:
    while not stop():
        db = SessionLocal()
        try:
            run_once(db)
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad batch
            log.warning(f"[worker] run_once errored: {exc}")
            db.rollback()
        finally:
            db.close()
        time.sleep(settings.WORKER_POLL_INTERVAL)


def main() -> None:
    register()
    stopping = {"v": False}

    def _handle_sigterm(_signum, _frame):
        log.info("[worker] SIGTERM received; finishing and exiting")
        stopping["v"] = True

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    log.info("[worker] starting main loop")
    main_loop(stop=lambda: stopping["v"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests + verify pass**

Run: `poetry run pytest tests/worker/test_entrypoint.py -v`
Expected: PASS.

- [ ] **Step 5: Add the `worker` service to both compose files**

In `docker-compose.yml` (dev), add a service mirroring `api` but with the worker command (no ports):

```yaml
  worker:
    build: .
    command: ["python", "-m", "app.worker"]
    env_file: [.env]
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
```

In `docker-compose.prod.yml`, add a `worker` service modeled on `api` (same `image`, NO `build:` — the api service owns the build; `<<: *default-logging`; `depends_on: migrate: service_completed_successfully`, `db`, `redis`; `restart: unless-stopped`; `command: ["python", "-m", "app.worker"]`; no published ports). Give it a **modest** resource block within the confirmed 4 vCPU / 8 GB budget:

```yaml
    deploy:
      resources:
        limits: { cpus: "0.5", memory: 512M }
        reservations: { memory: 128M }
```

Update the resource-arithmetic comment header (steady state becomes `db 1.5 + redis 0.5 + api 2.0 + worker 0.5 = 4.5 CPU ceiling / 6144M`; note CPU limits are ceilings that throttle, and worker DB connections use a small pool — set `DATABASE_POOL_SIZE`/overflow so `api(40) + worker(≤10) + migrate(2)` stays under Postgres `max_connections=100`). Mirror the corresponding numbers in `docs/deployment/DEPLOYMENT_GUIDE.md`.

- [ ] **Step 6: Validate compose + smoke the entrypoint in the image path**

Run:
```
docker compose -f docker-compose.yml config >/dev/null && echo "dev compose valid"
docker compose -f docker-compose.prod.yml config >/dev/null && echo "prod compose valid"
poetry run python -c "import app.worker.__main__ as m; m.register(); print('worker imports + registers OK')"
```
Expected: both compose files parse; the worker module imports and registers cleanly. (Actual container run is exercised by CI's build job / deploy, noted in the SOP.)

- [ ] **Step 7: Commit**

```bash
git add app/worker/__main__.py tests/worker/test_entrypoint.py docker-compose.yml docker-compose.prod.yml docs/deployment/DEPLOYMENT_GUIDE.md
git commit -m "feat(worker): process entrypoint (SIGTERM) + worker compose service"
```

---

## Task 8: Live e2e + captures + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_notifications_email.py`
- Modify: `e2e/conftest.py` (add `mailbox.latest_for`), `docs/fe-integration-guide-notifications.md`, `docs/checklist/PROJECT_CHECKLIST.md`
- Create: `docs/sop/2026-09-15-notifications-email-delivery.md`

**Interfaces:**
- Consumes: everything above; `EMAIL_BACKEND=file` (the e2e harness default), `mailbox`, `capture`, `make_verified_user`, `unique_email`.

- [ ] **Step 1: Add the mailbox helper**

```python
# e2e/conftest.py — inside class _Mailbox, add:
        @staticmethod
        def latest_for(email: str) -> dict | None:
            files = sorted(Path(MAIL_DIR).glob("*.json"))
            for f in reversed(files):
                data = json.loads(f.read_text())
                if data["to"].lower() == email.lower():
                    return data
            return None
```

- [ ] **Step 2: Write the e2e journey**

```python
# e2e/test_notifications_email.py
"""Live Module 20 Slice 2 journey: email delivery + preferences + worker drain.

A founder (A) invites teammate B; A shares a document -> the in-txn handler
enqueues an `email.notification` job for B (Documents category ON by default).
We drain the queue once via the worker's run_once and assert B received the
email (EMAIL_BACKEND=file). Then B turns Documents email OFF and A shares
again: a new in-app row, but NO new email.
"""
import httpx

from app.db.session import SessionLocal
from app.worker import runner
from app.worker.handlers import email as _email  # noqa: F401  (registers handler)


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    db = SessionLocal()
    try:
        runner.run_once(db)
    finally:
        db.close()


def test_email_delivery_and_preferences(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as a, httpx.Client(base_url=base_url, timeout=10.0) as b:
        # (reuse the Slice 1 journey's setup helpers inline: A onboards, invites B,
        #  B signs up+verifies+accepts, A uploads a document file — see e2e/test_notifications.py)
        # ... build A (founder) + B (member), capture ids, upload a document as A ...
        # A shares the document with the workspace:
        # share = a.post(f"/api/v1/documents/{doc_id}/shares", ...); assert 201
        _drain()
        msg = mailbox.latest_for(b_email)
        assert msg is not None and "document" in msg["subject"].lower()
        capture("notifications_email", "delivered_email", _as_resp(msg))  # helper writes the dict

        # B turns Documents email OFF
        off = b.put("/api/v1/notifications/preferences", headers=_auth(b_token),
                    json={"categories": {"documents": False}})
        assert off.status_code == 200 and off.json()["data"]["categories"]["documents"] is False
        capture("notifications_email", "preferences_documents_off", off)

        before = mailbox.count_for(b_email)
        # A shares again:
        # a.post(f"/api/v1/documents/{doc_id}/shares", ...)
        _drain()
        assert mailbox.count_for(b_email) == before  # no new email
        prefs = b.get("/api/v1/notifications/preferences", headers=_auth(b_token))
        capture("notifications_email", "preferences_get", prefs)
```

> Implementer note: flesh out the A/B setup + document upload/share by mirroring `e2e/test_notifications.py` (same onboarding + invite flow) and `e2e/test_documents*.py` (upload + share). Keep the `_drain()` calls and the assertions exactly as above.

- [ ] **Step 3: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: all e2e pass, including the new journey; capture files written under `e2e/_captures/notifications_email/`.

- [ ] **Step 4: Re-read the captures and write the FE guide + SOP + checklist**

Extend `docs/fe-integration-guide-notifications.md` with a "Preferences & email (Slice 2)" section: paste the **verbatim** `preferences_get` / `preferences_documents_off` bodies (the `{master_email, categories:{…}}` shape), the category catalog + defaults, the `422` on an unknown category key, and an explicit note: **email is asynchronous & best-effort** (delivered by the worker, not inline) while **in-app is immediate**; the email CTA base is `APP_BASE_URL` (the FE origin). Add a verification table row per behavior (verified-live).

Write `docs/sop/2026-09-15-notifications-email-delivery.md` (what shipped, why, how — enqueue-in-txn + worker, files/migration/compose touched, verification, the `worker` container + `APP_BASE_URL`/`EMAIL_BACKEND=resend` deploy steps, the at-least-once double-send follow-up).

Update `docs/checklist/PROJECT_CHECKLIST.md`: mark **Module 20 Slice 1 → merged** (the pending one-word flip), and Slice 2 → shipped with this PR ref; note Slice 3 (scheduler) will enqueue into this worker.

- [ ] **Step 5: Full local CI reproduction**

Run:
```
poetry run black --check app tests && poetry run isort --check-only app tests && poetry run ruff check app tests
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads   # exactly one
./scripts/e2e_run.sh
```
Expected: all green; one alembic head.

- [ ] **Step 6: Commit**

```bash
git add e2e/ docs/
git commit -m "test(notifications): Slice 2 live e2e + captures + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:**
- §2 data flow → Task 4 (enqueue in-txn) + Task 5/6 (worker sends). ✓
- §3.1 prefs table, §3.2 jobs columns → Task 1. ✓
- §3.3 categories/defaults → Task 2. ✓
- §4 prefs service + endpoints → Task 2 (service) + Task 3 (endpoints, 422). ✓
- §5 handler change → Task 4. ✓
- §6 worker (claim/SKIP LOCKED/retry/backoff/reaper/registry/deploy/config) → Task 5 (+ config) + Task 7 (entrypoint + compose). ✓
- §7 email handler + deep link + template + APP_BASE_URL → Task 6. ✓
- §8 errors → Task 3 (422/403 via require_workspace) + Task 5 (worker isolation). ✓
- §9 testing (unit incl. same-txn rollback; e2e file mailbox; FE guide) → Tasks 2–6 unit + Task 8 e2e/guide. ✓
- §11 waivers (at-least-once double-send; migration coordination) → surfaced in Task 5 note + Task 1 migration step + Task 8 SOP. ✓

**Type consistency:** `email_enabled(..., category: str | None)`, `category_for -> str | None`, `effective_preferences -> {"master_email", "categories"}`, `Handler = Callable[[Session, Job], None]`, `run_once(db) -> int`, `handle_email_notification(db, job)` — used identically across Tasks 2/4/5/6. `job_dispatcher.enqueue(db, type, payload, startup_id)` matches the real stub signature. `create_notifications(...) -> list[Notification]` (returns rows) matches Task 4's usage.

**Placeholder scan:** the only intentional placeholder is the migration number `00NN` (settled at build time, coordinated with Module 17) — documented in Global Constraints and Task 1. The Task 8 e2e A/B setup references mirroring existing e2e files rather than repeating ~120 lines of onboarding/upload boilerplate verbatim; the drain + assertions (the Slice-2-specific logic) are given in full.
