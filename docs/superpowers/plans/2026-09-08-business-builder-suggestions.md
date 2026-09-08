# Business Builder Slice 3 — Suggestions Workflow + Positioning Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Business Builder's collaboration layer — a business-consultant *suggest → approve/reject* workflow over the existing canvas/record artifacts — plus a competitor 2×2 positioning map.

**Architecture:** A suggestion is a row storing `op + target + payload`; on approve it is applied through the *existing* `save_canvas`/`create_record`/`update_record`/`delete_record` service functions, so all validation, the canvas version-check, and the `business.artifact.completed` event are inherited. Positioning coordinates live on the competitor record (`map_x`/`map_y` in JSONB `data`); only the editable axis config is a new singleton table.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL (JSONB), Pydantic v2, pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-09-08-business-builder-suggestions-design.md`

## Global Constraints

- **Enum columns:** `enum.StrEnum` in `app/db/models/enums.py`, column type `Enum(X, native_enum=False, length=20)` — VARCHAR-backed, no Postgres `CREATE TYPE`. (22/22 existing columns follow this.)
- **FK index convention:** every FK column gets a standalone `index=True` *in addition to* any composite index.
- **`get_db()` does not auto-commit:** every write endpoint (and every lazy-create read endpoint) MUST call `db.commit()`.
- **Route-shadowing guard:** literal routes (`/suggestions`, `/positioning-map`) MUST be declared **before** the `/{kind}` catch-all in `app/api/v1/endpoints/business.py`.
- **Response envelope:** endpoints return `success_response(data)`; errors raise `AppError` subclasses.
- **Tenancy:** cross-tenant / missing resource → uniform **404** (`NotFound`). `require_workspace` = any member; `_editor = require_role(founder, team_member)`.
- **Migrations:** produced by `alembic revision --autogenerate` against the ORM models; single head; `alembic check` reports zero drift before push.
- **No AI attribution** in any commit message or PR/issue/review body — clean commits authored by Adebayo only.
- **CI locally green before push:** `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app`, `poetry run pytest`, then `scripts/e2e_run.sh` — all via the project's pinned toolchain.

---

### Task 1: Schema foundation — enums, models, two migrations

Creates both new tables and the coordinate change is deferred to Task 5 (it needs no migration). One reviewable schema unit gated by a clean `alembic check`.

**Files:**
- Modify: `app/db/models/enums.py` (add `SuggestionOp`, `SuggestionStatus`)
- Modify: `app/db/models/business.py` (add `BusinessSuggestion`, `BusinessPositioningMap`)
- Create: `alembic/versions/00NN_business_suggestions.py`
- Create: `alembic/versions/00NN_business_positioning_maps.py`
- Test: `tests/services/business/test_slice3_models.py`

**Interfaces:**
- Produces: `SuggestionOp` (`canvas_update`/`record_create`/`record_update`/`record_delete`), `SuggestionStatus` (`pending`/`approved`/`rejected`); ORM classes `BusinessSuggestion` (cols: `id, created_at, updated_at, startup_id, author_id, op, target, payload, base_version, note, status, resolved_by_id, resolved_at`) and `BusinessPositioningMap` (cols: `id, created_at, updated_at, startup_id (unique), axes`).

**Migration numbering — settle against the live head:** develop head at spec time is `0012_business_records`. The open journal PR #37 introduces `0013_journal`. Before writing the migrations, run `git fetch origin && git log --oneline origin/develop -1` and `poetry run alembic heads`. Chain the two new revisions onto the **current single head**: if it is `0012_business_records`, use ids `0013_business_suggestions` → `0014_business_positioning_maps`; if #37 has merged and the head is `0013_journal`, rebase this branch onto `origin/develop` first and use `0014_business_suggestions` → `0015_business_positioning_maps`. `poetry run alembic heads` MUST show exactly one head after.

- [ ] **Step 1: Add the two enums**

In `app/db/models/enums.py`, append:

```python
class SuggestionOp(enum.StrEnum):
    canvas_update = "canvas_update"
    record_create = "record_create"
    record_update = "record_update"
    record_delete = "record_delete"


class SuggestionStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
```

- [ ] **Step 2: Add the two models**

In `app/db/models/business.py`, add imports `DateTime`, `Text` to the existing `from sqlalchemy import ...` line and `import datetime` at top, then append:

```python
class BusinessSuggestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_suggestions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    op: Mapped[SuggestionOp] = mapped_column(
        Enum(SuggestionOp, native_enum=False, length=20), nullable=False
    )
    target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, native_enum=False, length=20),
        nullable=False,
        server_default="pending",
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_business_suggestions_startup_status", "startup_id", "status"),
    )


class BusinessPositioningMap(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_positioning_maps"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    axes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("startup_id", name="uq_business_positioning_map_startup"),
    )
```

Update the top-level import in `enums`-consuming line of `business.py`: `from app.db.models.enums import CanvasType, RecordKind, SuggestionOp, SuggestionStatus`.

- [ ] **Step 3: Update the dev DB to head, then autogenerate both migrations**

```bash
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "business_suggestions"
poetry run alembic revision --autogenerate -m "business_positioning_maps"
```

Rename the two generated files to the numbering settled above; set `revision`/`down_revision` so they chain onto the current head in order (suggestions first, positioning second). Confirm the autogenerated `upgrade()` for `business_suggestions` created `ix_business_suggestions_startup_id` (from `index=True`) **and** `ix_business_suggestions_startup_status` (composite); for `business_positioning_maps` created `ix_business_positioning_maps_startup_id` and the unique constraint `uq_business_positioning_map_startup`. Add a short module docstring to each (mirror `0012_business_records.py`).

- [ ] **Step 4: Verify single head + zero drift**

```bash
poetry run alembic heads          # exactly one head
poetry run alembic upgrade head   # both apply clean
poetry run alembic check          # "No new upgrade operations detected."
```
Expected: one head; clean apply; no drift.

- [ ] **Step 5: Write the model round-trip test**

```python
# tests/services/business/test_slice3_models.py
from app.db.models.business import BusinessPositioningMap, BusinessSuggestion
from app.db.models.enums import SuggestionOp, SuggestionStatus
from tests.factories import create_startup, create_user


def test_suggestion_defaults_to_pending(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = BusinessSuggestion(
        startup_id=s.id, author_id=u.id, op=SuggestionOp.canvas_update,
        target={"canvas_type": "business_model"}, payload={"blocks": {}}, base_version=1,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.status == SuggestionStatus.pending
    assert row.resolved_at is None


def test_positioning_map_stores_axes(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = BusinessPositioningMap(startup_id=s.id, axes={"x": {"label": "Price"}})
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.axes["x"]["label"] == "Price"
```

- [ ] **Step 6: Run the model test**

Run: `poetry run pytest tests/services/business/test_slice3_models.py -v`
Expected: PASS (both).

- [ ] **Step 7: Commit**

```bash
git add app/db/models/enums.py app/db/models/business.py alembic/versions/ tests/services/business/test_slice3_models.py
git commit -m "feat(business-builder): Slice 3 schema — suggestions + positioning-map tables"
```

---

### Task 2: Suggestions service — create, validate, list, serialize

The create-side of the workflow: validate a proposed change per-op and store it; list; serialize with the target's live `current` state for the FE diff.

**Files:**
- Create: `app/services/business/suggestions.py`
- Modify: `app/core/errors.py` (add `SuggestionNotPending`)
- Test: `tests/services/business/test_suggestions.py`

**Interfaces:**
- Consumes: `validate_blocks`, `get_or_create_canvas`, `save_canvas` (`app/services/business/service.py`); `validate`, `create_record`, `update_record`, `delete_record`, `_record` (`app/services/business/records.py`); `empty_blocks` (`app/services/business/canvas_defs.py`); `CanvasType`, `RecordKind`, `SuggestionOp`, `SuggestionStatus` (enums); `Membership`, `Startup`, `BusinessSuggestion`, `BusinessCanvas`, `BusinessRecord`, `User`.
- Produces:
  - `create_suggestion(db, membership: Membership, op: SuggestionOp, target: dict, payload: dict | None, note: str | None) -> BusinessSuggestion`
  - `list_suggestions(db, startup: Startup, status: SuggestionStatus | None) -> list[BusinessSuggestion]`
  - `serialize_suggestion(db, s: BusinessSuggestion) -> dict`
  - `_suggestion(db, membership: Membership, suggestion_id) -> BusinessSuggestion` (tenant-scoped, 404)
  - `SuggestionNotPending` (AppError, 409)

- [ ] **Step 1: Add the error class**

In `app/core/errors.py`, after `CanvasVersionConflict`:

```python
class SuggestionNotPending(AppError):  # noqa: N818
    code, http_status = "SUGGESTION_NOT_PENDING", 409
    message = "This suggestion has already been resolved."
```

- [ ] **Step 2: Write failing tests for create + validation**

```python
# tests/services/business/test_suggestions.py
import uuid

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import RecordKind, SuggestionOp, SuggestionStatus
from app.db.models.membership import Membership
from app.services.business.records import create_record
from app.services.business.suggestions import (
    create_suggestion,
    list_suggestions,
    serialize_suggestion,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db, role=None):
    from app.db.models.enums import MembershipRole

    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s, role=role or MembershipRole.business_consultant)
    db.flush()
    return u, s, m


def test_create_canvas_update_captures_base_version(db):
    _u, _s, m = _ctx(db)
    sug = create_suggestion(
        db, m, SuggestionOp.canvas_update,
        {"canvas_type": "business_model"}, {"blocks": {"key_partners": "Acme"}}, "tighten this",
    )
    assert sug.status == SuggestionStatus.pending
    assert sug.base_version == 1  # lazily-created canvas starts at version 1
    assert sug.author_id == m.user_id


def test_create_canvas_update_bad_block_422(db):
    _u, _s, m = _ctx(db)
    with pytest.raises(AppError) as e:
        create_suggestion(
            db, m, SuggestionOp.canvas_update,
            {"canvas_type": "business_model"}, {"blocks": {"not_a_block": "x"}}, None,
        )
    assert e.value.http_status == 422


def test_create_record_update_unknown_target_404(db):
    _u, _s, m = _ctx(db)
    with pytest.raises(NotFound):
        create_suggestion(
            db, m, SuggestionOp.record_update,
            {"kind": "competitor", "record_id": str(uuid.uuid4())}, {"data": {"name": "X"}}, None,
        )


def test_create_record_create_bad_data_422(db):
    _u, _s, m = _ctx(db)
    with pytest.raises(AppError) as e:
        create_suggestion(
            db, m, SuggestionOp.record_create, {"kind": "persona"}, {"data": {"goals": "no"}}, None,
        )
    assert e.value.http_status == 422


def test_serialize_includes_current_for_record_update(db):
    _u, s, m = _ctx(db)
    rec = create_record(db, s, RecordKind.competitor, {"name": "Acme"})
    db.flush()
    sug = create_suggestion(
        db, m, SuggestionOp.record_update,
        {"kind": "competitor", "record_id": str(rec.id)}, {"data": {"name": "Acme2"}}, None,
    )
    out = serialize_suggestion(db, sug)
    assert out["current"]["data"]["name"] == "Acme"
    assert out["payload"]["data"]["name"] == "Acme2"
    assert out["author"]["id"] == str(m.user_id)


def test_list_filters_by_status(db):
    _u, _s, m = _ctx(db)
    create_suggestion(
        db, m, SuggestionOp.canvas_update, {"canvas_type": "swot"}, {"blocks": {}}, None,
    )
    startup = db.query(Membership).filter_by(id=m.id).one().startup_id
    from app.db.models.startup import Startup

    s_obj = db.query(Startup).filter_by(id=startup).one()
    assert len(list_suggestions(db, s_obj, SuggestionStatus.pending)) == 1
    assert list_suggestions(db, s_obj, SuggestionStatus.approved) == []
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `poetry run pytest tests/services/business/test_suggestions.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.business.suggestions`).

- [ ] **Step 4: Implement create + validate + list + serialize**

```python
# app/services/business/suggestions.py
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.business import BusinessCanvas, BusinessRecord, BusinessSuggestion
from app.db.models.enums import CanvasType, RecordKind, SuggestionOp, SuggestionStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.business.canvas_defs import empty_blocks
from app.services.business.records import _record, validate
from app.services.business.service import get_or_create_canvas, validate_blocks


def _parse_canvas_type(target: dict) -> CanvasType:
    try:
        return CanvasType(target["canvas_type"])
    except (KeyError, ValueError):
        raise NotFound() from None


def _parse_kind(target: dict) -> RecordKind:
    try:
        return RecordKind(target["kind"])
    except (KeyError, ValueError):
        raise NotFound() from None


def create_suggestion(
    db: Session,
    membership: Membership,
    op: SuggestionOp,
    target: dict,
    payload: dict | None,
    note: str | None,
) -> BusinessSuggestion:
    startup = db.query(Startup).filter_by(id=membership.startup_id).one()
    base_version: int | None = None

    if op == SuggestionOp.canvas_update:
        ctype = _parse_canvas_type(target)
        blocks = (payload or {}).get("blocks", {})
        validate_blocks(ctype, blocks)  # 422 on bad block
        canvas = get_or_create_canvas(db, startup, ctype)
        base_version = canvas.version
        target = {"canvas_type": ctype.value}
    elif op == SuggestionOp.record_create:
        kind = _parse_kind(target)
        validate(kind, (payload or {}).get("data", {}))  # 422 on bad data
        target = {"kind": kind.value}
    elif op == SuggestionOp.record_update:
        kind = _parse_kind(target)
        rec = _record(db, membership, kind, target.get("record_id"))  # 404 if missing/cross-tenant
        validate(kind, (payload or {}).get("data", {}))
        target = {"kind": kind.value, "record_id": str(rec.id)}
    elif op == SuggestionOp.record_delete:
        kind = _parse_kind(target)
        rec = _record(db, membership, kind, target.get("record_id"))  # 404
        payload = None
        target = {"kind": kind.value, "record_id": str(rec.id)}

    row = BusinessSuggestion(
        startup_id=startup.id,
        author_id=membership.user_id,
        op=op,
        target=target,
        payload=payload,
        base_version=base_version,
        note=note,
    )
    db.add(row)
    db.flush()
    event_bus.publish(
        "business.suggestion.created",
        {"startup_id": str(startup.id), "suggestion_id": str(row.id), "op": op.value},
    )
    return row


def list_suggestions(
    db: Session, startup: Startup, status: SuggestionStatus | None
) -> list[BusinessSuggestion]:
    q = db.query(BusinessSuggestion).filter_by(startup_id=startup.id)
    if status is not None:
        q = q.filter_by(status=status)
    return q.order_by(BusinessSuggestion.created_at.desc()).all()


def _current(db: Session, s: BusinessSuggestion) -> dict | None:
    if s.op == SuggestionOp.canvas_update:
        ctype = CanvasType(s.target["canvas_type"])
        canvas = (
            db.query(BusinessCanvas).filter_by(startup_id=s.startup_id, type=ctype).first()
        )
        if canvas is None:
            return {"blocks": empty_blocks(ctype), "version": 0}
        return {"blocks": canvas.blocks, "version": canvas.version}
    if s.op in (SuggestionOp.record_update, SuggestionOp.record_delete):
        rec = (
            db.query(BusinessRecord)
            .filter_by(id=s.target["record_id"], startup_id=s.startup_id)
            .first()
        )
        return {"data": rec.data} if rec else None
    return None  # record_create has no prior state


def serialize_suggestion(db: Session, s: BusinessSuggestion) -> dict[str, Any]:
    author = db.query(User).filter_by(id=s.author_id).first()
    resolver = (
        db.query(User).filter_by(id=s.resolved_by_id).first() if s.resolved_by_id else None
    )
    return {
        "id": str(s.id),
        "op": s.op.value,
        "target": s.target,
        "payload": s.payload,
        "base_version": s.base_version,
        "current": _current(db, s),
        "note": s.note,
        "status": s.status.value,
        "author": {
            "id": str(s.author_id),
            "name": author.profile.full_name if author and author.profile else None,
            "email": author.email if author else None,
        },
        "resolved_by": (
            {
                "id": str(s.resolved_by_id),
                "name": resolver.profile.full_name if resolver and resolver.profile else None,
            }
            if s.resolved_by_id
            else None
        ),
        "resolved_at": s.resolved_at.isoformat() if s.resolved_at else None,
        "created_at": s.created_at.isoformat(),
    }


def _suggestion(db: Session, membership: Membership, suggestion_id: Any) -> BusinessSuggestion:
    row = (
        db.query(BusinessSuggestion)
        .filter_by(id=suggestion_id, startup_id=membership.startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row
```

> **Note on the display name:** `User` has no `name` column — the display name is `user.profile.full_name` (nullable, on the related `user_profiles` row via the `profile` relationship, `uselist=False` so it's `None` when absent), and `user.email` is always present. The serializer above guards `author.profile` for `None`.

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `poetry run pytest tests/services/business/test_suggestions.py -v`
Expected: PASS (all six).

- [ ] **Step 6: Commit**

```bash
git add app/services/business/suggestions.py app/core/errors.py tests/services/business/test_suggestions.py
git commit -m "feat(business-builder): suggestion create/validate/list/serialize service"
```

---

### Task 3: Suggestions service — approve (apply per op), reject, state machine

The resolve-side: apply an approved suggestion through the existing write functions, enforce the pending-only state machine, and handle the stale-canvas / target-gone edge cases.

**Files:**
- Modify: `app/services/business/suggestions.py` (add `approve_suggestion`, `reject_suggestion`, `_apply`)
- Test: `tests/services/business/test_suggestions_resolve.py`

**Interfaces:**
- Consumes: everything from Task 2 plus `create_record`, `update_record`, `delete_record`, `save_canvas`, `CanvasVersionConflict`.
- Produces:
  - `approve_suggestion(db, membership: Membership, suggestion_id) -> BusinessSuggestion` (raises `SuggestionNotPending` 409, `CanvasVersionConflict` 409, `NotFound` 404)
  - `reject_suggestion(db, membership: Membership, suggestion_id) -> BusinessSuggestion` (raises `SuggestionNotPending` 409)

- [ ] **Step 1: Write failing resolve tests**

```python
# tests/services/business/test_suggestions_resolve.py
import pytest

from app.core.errors import CanvasVersionConflict, NotFound, SuggestionNotPending
from app.db.models.enums import (
    CanvasType,
    MembershipRole,
    RecordKind,
    SuggestionOp,
    SuggestionStatus,
)
from app.services.business.records import create_record
from app.services.business.service import get_or_create_canvas, save_canvas
from app.services.business.suggestions import (
    approve_suggestion,
    create_suggestion,
    reject_suggestion,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s, m


def test_approve_canvas_update_applies_and_marks_approved(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db, m, SuggestionOp.canvas_update,
        {"canvas_type": "business_model"}, {"blocks": {"key_partners": "Acme"}}, None,
    )
    approve_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.approved
    assert sug.resolved_by_id == m.user_id and sug.resolved_at is not None
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    assert canvas.blocks["key_partners"] == "Acme"


def test_approve_record_create_inserts(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db, m, SuggestionOp.record_create, {"kind": "persona"}, {"data": {"name": "P1"}}, None,
    )
    approve_suggestion(db, m, sug.id)
    from app.db.models.business import BusinessRecord

    assert db.query(BusinessRecord).filter_by(startup_id=s.id, kind=RecordKind.persona).count() == 1


def test_approve_record_delete_removes(db):
    _u, s, m = _ctx(db)
    rec = create_record(db, s, RecordKind.competitor, {"name": "Gone"})
    db.flush()
    sug = create_suggestion(
        db, m, SuggestionOp.record_delete,
        {"kind": "competitor", "record_id": str(rec.id)}, None, None,
    )
    approve_suggestion(db, m, sug.id)
    from app.db.models.business import BusinessRecord

    assert db.query(BusinessRecord).filter_by(id=rec.id).first() is None


def test_reject_leaves_target_untouched(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db, m, SuggestionOp.canvas_update,
        {"canvas_type": "swot"}, {"blocks": {"strengths": ["x"]}}, None,
    )
    reject_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.rejected
    canvas = get_or_create_canvas(db, s, CanvasType.swot)
    assert canvas.blocks.get("strengths") in (None, [], "")


def test_approve_twice_409(db):
    _u, _s, m = _ctx(db)
    sug = create_suggestion(
        db, m, SuggestionOp.canvas_update, {"canvas_type": "lean"}, {"blocks": {}}, None,
    )
    approve_suggestion(db, m, sug.id)
    with pytest.raises(SuggestionNotPending):
        approve_suggestion(db, m, sug.id)


def test_approve_stale_canvas_conflict_leaves_pending(db):
    _u, s, m = _ctx(db)
    sug = create_suggestion(
        db, m, SuggestionOp.canvas_update,
        {"canvas_type": "business_model"}, {"blocks": {"key_partners": "A"}}, None,
    )  # base_version captured = 1
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    save_canvas(db, canvas, {"key_partners": "moved"}, 1)  # bumps to version 2
    with pytest.raises(CanvasVersionConflict):
        approve_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.pending  # still open


def test_approve_deleted_target_404_leaves_pending(db):
    _u, s, m = _ctx(db)
    rec = create_record(db, s, RecordKind.competitor, {"name": "X"})
    db.flush()
    sug = create_suggestion(
        db, m, SuggestionOp.record_update,
        {"kind": "competitor", "record_id": str(rec.id)}, {"data": {"name": "Y"}}, None,
    )
    db.delete(rec)
    db.flush()
    with pytest.raises(NotFound):
        approve_suggestion(db, m, sug.id)
    db.refresh(sug)
    assert sug.status == SuggestionStatus.pending
```

- [ ] **Step 2: Run to confirm failure**

Run: `poetry run pytest tests/services/business/test_suggestions_resolve.py -v`
Expected: FAIL (`ImportError: cannot import name 'approve_suggestion'`).

- [ ] **Step 3: Implement `_apply`, `approve_suggestion`, `reject_suggestion`**

Add imports at the top of `app/services/business/suggestions.py`:

```python
from datetime import UTC, datetime

from app.core.errors import SuggestionNotPending
from app.services.business.records import create_record, delete_record, update_record
from app.services.business.service import save_canvas
```

Append:

```python
def _apply(db: Session, membership: Membership, s: BusinessSuggestion) -> None:
    """Apply the suggestion through the existing write functions.

    Raises CanvasVersionConflict (409) if the canvas moved since the suggestion
    was made, or NotFound (404) if the target record was deleted meanwhile. The
    caller must let these propagate so the request transaction rolls back and
    the suggestion stays `pending`.
    """
    startup = db.query(Startup).filter_by(id=membership.startup_id).one()
    if s.op == SuggestionOp.canvas_update:
        ctype = CanvasType(s.target["canvas_type"])
        canvas = get_or_create_canvas(db, startup, ctype)
        save_canvas(db, canvas, (s.payload or {}).get("blocks", {}), s.base_version)
    elif s.op == SuggestionOp.record_create:
        create_record(db, startup, RecordKind(s.target["kind"]), (s.payload or {})["data"])
    elif s.op == SuggestionOp.record_update:
        rec = _record(db, membership, RecordKind(s.target["kind"]), s.target["record_id"])
        update_record(db, rec, (s.payload or {})["data"])
    elif s.op == SuggestionOp.record_delete:
        rec = _record(db, membership, RecordKind(s.target["kind"]), s.target["record_id"])
        delete_record(db, rec)


def _require_pending(s: BusinessSuggestion) -> None:
    if s.status != SuggestionStatus.pending:
        raise SuggestionNotPending()


def approve_suggestion(
    db: Session, membership: Membership, suggestion_id: Any
) -> BusinessSuggestion:
    s = _suggestion(db, membership, suggestion_id)
    _require_pending(s)
    _apply(db, membership, s)  # may raise 409/404 -> txn rolls back, stays pending
    s.status = SuggestionStatus.approved
    s.resolved_by_id = membership.user_id
    s.resolved_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(
        "business.suggestion.approved",
        {"startup_id": str(s.startup_id), "suggestion_id": str(s.id), "op": s.op.value},
    )
    return s


def reject_suggestion(
    db: Session, membership: Membership, suggestion_id: Any
) -> BusinessSuggestion:
    s = _suggestion(db, membership, suggestion_id)
    _require_pending(s)
    s.status = SuggestionStatus.rejected
    s.resolved_by_id = membership.user_id
    s.resolved_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(
        "business.suggestion.rejected",
        {"startup_id": str(s.startup_id), "suggestion_id": str(s.id), "op": s.op.value},
    )
    return s
```

> **Rollback note:** `_apply` raising after `db.flush()`-ing partial changes is fine for the *endpoint* path (the request rolls back the whole transaction). In the unit tests the assertion `status == pending` holds because the raise happens *before* `s.status` is set — the failed op's partial flush is invisible since `status` was never mutated. Do not add a nested savepoint here; the endpoint's single transaction is the unit of atomicity.

- [ ] **Step 4: Run to confirm pass**

Run: `poetry run pytest tests/services/business/test_suggestions_resolve.py -v`
Expected: PASS (all seven).

- [ ] **Step 5: Commit**

```bash
git add app/services/business/suggestions.py tests/services/business/test_suggestions_resolve.py
git commit -m "feat(business-builder): suggestion approve/reject with per-op apply + state machine"
```

---

### Task 4: Suggestions endpoints + request schema + route ordering

Wire the service to HTTP with correct role gating and route ordering; prove the role matrix and state machine over the wire.

**Files:**
- Modify: `app/schemas/business.py` (add `SuggestionCreate`)
- Modify: `app/api/v1/endpoints/business.py` (4 routes, before `/{kind}`)
- Test: `tests/api/test_business_suggestions.py`

**Interfaces:**
- Consumes: `create_suggestion`, `list_suggestions`, `serialize_suggestion`, `approve_suggestion`, `reject_suggestion`, `_suggestion` (Task 2/3); `_startup`, `_editor`, `require_workspace` (existing in `business.py`); `SuggestionCreate`.
- Produces: HTTP routes `POST /suggestions`, `GET /suggestions`, `POST /suggestions/{suggestion_id}/approve`, `POST /suggestions/{suggestion_id}/reject`.

- [ ] **Step 1: Add the request schema**

In `app/schemas/business.py`:

```python
from app.db.models.enums import SuggestionOp


class SuggestionCreate(BaseModel):
    op: SuggestionOp
    target: dict = Field(default_factory=dict)
    payload: dict | None = None
    note: str | None = None


class PositioningMapSave(BaseModel):
    axes: dict = Field(default_factory=dict)
```

(`PositioningMapSave` is added here now so Task 5 only touches the endpoint file.)

- [ ] **Step 2: Write failing API tests**

```python
# tests/api/test_business_suggestions.py
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _two_roles(db, role_a, role_b):
    """One workspace, two members with different roles; returns (startup, headers_a, headers_b)."""
    u_a = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u_a, stage=StartupStage.validation)
    create_membership(db, u_a, s, role=role_a)
    u_b = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u_b, s, role=role_b)
    db.flush()
    ha = {"Authorization": f"Bearer {create_access_token(str(u_a.id))}", "X-Workspace-Id": str(s.id)}
    hb = {"Authorization": f"Bearer {create_access_token(str(u_b.id))}", "X-Workspace-Id": str(s.id)}
    return s, ha, hb


def test_consultant_can_suggest_founder_can_approve(client, db):
    s, h_founder, h_bc = _two_roles(db, MembershipRole.founder, MembershipRole.business_consultant)
    created = client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "business_model"},
              "payload": {"blocks": {"key_partners": "Acme"}}, "note": "add Acme"},
        headers=h_bc,
    )
    assert created.status_code == 201
    sid = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "pending"

    approved = client.post(
        f"/api/v1/business-builder/suggestions/{sid}/approve", headers=h_founder
    )
    assert approved.status_code == 200 and approved.json()["data"]["status"] == "approved"

    canvas = client.get("/api/v1/business-builder/canvases/business_model", headers=h_founder)
    assert canvas.json()["data"]["blocks"]["key_partners"] == "Acme"


def test_consultant_cannot_approve_403(client, db):
    s, _h_founder, h_bc = _two_roles(db, MembershipRole.founder, MembershipRole.business_consultant)
    sid = client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "lean"}, "payload": {"blocks": {}}},
        headers=h_bc,
    ).json()["data"]["id"]
    assert client.post(
        f"/api/v1/business-builder/suggestions/{sid}/approve", headers=h_bc
    ).status_code == 403


def test_list_filters_by_status(client, db):
    _u, _s, h = _member(db)
    client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "swot"}, "payload": {"blocks": {}}},
        headers=h,
    )
    r = client.get("/api/v1/business-builder/suggestions?status=pending", headers=h)
    assert r.status_code == 200 and len(r.json()["data"]["suggestions"]) == 1
    assert client.get(
        "/api/v1/business-builder/suggestions?status=approved", headers=h
    ).json()["data"]["suggestions"] == []


def test_suggestions_not_shadowed_by_kind_route(client, db):
    """GET /suggestions must hit the suggestions route, not /{kind} (which would 404)."""
    _u, _s, h = _member(db)
    assert client.get("/api/v1/business-builder/suggestions", headers=h).status_code == 200


def test_approve_unknown_id_404(client, db):
    import uuid

    _u, _s, h = _member(db)
    assert client.post(
        f"/api/v1/business-builder/suggestions/{uuid.uuid4()}/approve", headers=h
    ).status_code == 404


def test_reject_then_approve_409(client, db):
    _u, _s, h = _member(db)
    sid = client.post(
        "/api/v1/business-builder/suggestions",
        json={"op": "canvas_update", "target": {"canvas_type": "lean"}, "payload": {"blocks": {}}},
        headers=h,
    ).json()["data"]["id"]
    assert client.post(
        f"/api/v1/business-builder/suggestions/{sid}/reject", headers=h
    ).status_code == 200
    r = client.post(f"/api/v1/business-builder/suggestions/{sid}/approve", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUGGESTION_NOT_PENDING"
```

- [ ] **Step 3: Run to confirm failure**

Run: `poetry run pytest tests/api/test_business_suggestions.py -v`
Expected: FAIL (404s / route missing).

- [ ] **Step 4: Add the routes (before the `/{kind}` block)**

In `app/api/v1/endpoints/business.py`, add imports:

```python
from app.db.models.enums import SuggestionStatus
from app.schemas.business import PositioningMapSave, SuggestionCreate
from app.services.business.suggestions import (
    approve_suggestion,
    create_suggestion,
    list_suggestions,
    reject_suggestion,
    serialize_suggestion,
)
```

Insert these routes immediately **before** the `@router.get("/{kind}")` declaration (currently line ~120), so the literal `/suggestions` paths are registered first:

```python
@router.post("/suggestions", status_code=status.HTTP_201_CREATED)
def create_suggestion_endpoint(
    body: SuggestionCreate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = create_suggestion(db, membership, body.op, body.target, body.payload, body.note)
    db.commit()
    return success_response(serialize_suggestion(db, s))


@router.get("/suggestions")
def list_suggestions_endpoint(
    status: str | None = None,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    parsed = SuggestionStatus(status) if status else None
    rows = list_suggestions(db, _startup(db, membership), parsed)
    return success_response({"suggestions": [serialize_suggestion(db, r) for r in rows]})


@router.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = approve_suggestion(db, membership, suggestion_id)
    db.commit()
    return success_response(serialize_suggestion(db, s))


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = reject_suggestion(db, membership, suggestion_id)
    db.commit()
    return success_response(serialize_suggestion(db, s))
```

> **`status` shadowing caution:** the query param is named `status` but the module imports `from fastapi import ... status`. Inside `list_suggestions_endpoint` the local `status: str | None` shadows the `fastapi.status` module — that function does not use `status.HTTP_*`, so it is safe. The other three endpoints still see the module-level `status`. If mypy/ruff complains, rename the query param to `status_filter` with `Query(alias="status")`.

- [ ] **Step 5: Handle an invalid `?status=` value**

An unknown `?status=bogus` should be a 422, not a 500. `SuggestionStatus("bogus")` raises `ValueError`. Add a guard in `list_suggestions_endpoint`:

```python
    try:
        parsed = SuggestionStatus(status) if status else None
    except ValueError:
        raise NotFound() from None  # unknown status filter -> 404 (uniform "no such view")
```

Add a test:

```python
def test_list_bad_status_404(client, db):
    _u, _s, h = _member(db)
    assert client.get(
        "/api/v1/business-builder/suggestions?status=bogus", headers=h
    ).status_code == 404
```

- [ ] **Step 6: Run all suggestion API tests**

Run: `poetry run pytest tests/api/test_business_suggestions.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/business.py app/api/v1/endpoints/business.py tests/api/test_business_suggestions.py
git commit -m "feat(business-builder): suggestion endpoints (create/list/approve/reject) + route ordering"
```

---

### Task 5: Competitor positioning map — coords, axes singleton, endpoints

Add per-competitor coordinates and the editable-axes singleton, plus the assemble/update endpoints.

**Files:**
- Modify: `app/services/business/record_defs.py` (`CompetitorData.map_x/map_y`)
- Create: `app/services/business/positioning.py`
- Modify: `app/api/v1/endpoints/business.py` (2 routes, before `/{kind}`)
- Test: `tests/services/business/test_positioning.py`, `tests/api/test_business_positioning.py`

**Interfaces:**
- Consumes: `BusinessPositioningMap`, `BusinessRecord`, `RecordKind`, `Startup`, `AppError`; `_startup`, `_editor`, `require_workspace`, `PositioningMapSave`.
- Produces:
  - `CompetitorData.map_x: float | None`, `CompetitorData.map_y: float | None`
  - `DEFAULT_AXES: dict`
  - `get_or_create_map(db, startup: Startup) -> BusinessPositioningMap`
  - `update_axes(db, row: BusinessPositioningMap, axes: dict) -> BusinessPositioningMap`
  - `assemble_map(db, startup: Startup) -> dict`
  - `serialize_map(row: BusinessPositioningMap) -> dict`
  - HTTP `GET /positioning-map`, `PUT /positioning-map`

- [ ] **Step 1: Write the failing coord-validation test**

```python
# tests/services/business/test_positioning.py
import pytest

from app.core.errors import AppError
from app.db.models.enums import RecordKind
from app.services.business.positioning import (
    assemble_map,
    get_or_create_map,
    update_axes,
)
from app.services.business.records import create_record, validate
from tests.factories import create_startup, create_user


def _startup(db):
    u = create_user(db)
    return create_startup(db, owner=u)


def test_competitor_accepts_coords(db):
    clean = validate(RecordKind.competitor, {"name": "Acme", "map_x": 0.25, "map_y": 0.8})
    assert clean["map_x"] == 0.25 and clean["map_y"] == 0.8


def test_competitor_rejects_out_of_range_coord(db):
    with pytest.raises(AppError) as e:
        validate(RecordKind.competitor, {"name": "Acme", "map_x": 1.5})
    assert e.value.http_status == 422


def test_get_or_create_map_returns_default_axes(db):
    s = _startup(db)
    row = get_or_create_map(db, s)
    assert row.axes["x"]["label"] == "Price" and row.axes["y"]["label"] == "Quality"
    # idempotent
    assert get_or_create_map(db, s).id == row.id


def test_update_axes_replaces(db):
    s = _startup(db)
    row = get_or_create_map(db, s)
    update_axes(db, row, {"x": {"label": "Reach", "low": "Niche", "high": "Mass"},
                          "y": {"label": "Trust", "low": "New", "high": "Proven"}})
    assert row.axes["x"]["label"] == "Reach"


def test_update_axes_bad_shape_422(db):
    s = _startup(db)
    row = get_or_create_map(db, s)
    with pytest.raises(AppError) as e:
        update_axes(db, row, {"x": "not-an-object"})
    assert e.value.http_status == 422


def test_assemble_lists_competitors_with_coords(db):
    s = _startup(db)
    create_record(db, s, RecordKind.competitor, {"name": "Acme", "map_x": 0.2, "map_y": 0.9})
    create_record(db, s, RecordKind.competitor, {"name": "Globex"})  # no coords
    out = assemble_map(db, s)
    by_name = {c["name"]: c for c in out["competitors"]}
    assert by_name["Acme"]["x"] == 0.2 and by_name["Acme"]["y"] == 0.9
    assert by_name["Globex"]["x"] is None and by_name["Globex"]["y"] is None
    assert out["axes"]["x"]["label"] == "Price"
```

- [ ] **Step 2: Run to confirm failure**

Run: `poetry run pytest tests/services/business/test_positioning.py -v`
Expected: FAIL (module missing; coord fields absent).

- [ ] **Step 3: Add coords to `CompetitorData`**

In `app/services/business/record_defs.py`, change the import line to
`from pydantic import BaseModel, ConfigDict, Field` and add two fields to `CompetitorData`:

```python
class CompetitorData(_Base):
    name: str
    positioning: str = ""
    price: str = ""
    strengths: list[str] = []
    weaknesses: list[str] = []
    threat_level: ThreatLevel = ThreatLevel.medium
    map_x: float | None = Field(default=None, ge=0, le=1)
    map_y: float | None = Field(default=None, ge=0, le=1)
```

- [ ] **Step 4: Implement `positioning.py`**

```python
# app/services/business/positioning.py
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.business import BusinessPositioningMap, BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.startup import Startup

DEFAULT_AXES: dict[str, Any] = {
    "x": {"label": "Price", "low": "Low", "high": "High"},
    "y": {"label": "Quality", "low": "Low", "high": "High"},
}


def get_or_create_map(db: Session, startup: Startup) -> BusinessPositioningMap:
    """Lazily fetch/create the singleton map row for a startup.

    Mirrors get_or_create_canvas: SAVEPOINT-guarded insert + re-select so two
    concurrent first-loads don't both INSERT past the unique(startup_id).
    """
    row = db.query(BusinessPositioningMap).filter_by(startup_id=startup.id).first()
    if row is None:
        try:
            with db.begin_nested():
                row = BusinessPositioningMap(startup_id=startup.id, axes=dict(DEFAULT_AXES))
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.query(BusinessPositioningMap).filter_by(startup_id=startup.id).one()
    return row


def validate_axes(axes: dict) -> None:
    for axis in ("x", "y"):
        cfg = axes.get(axis)
        if not isinstance(cfg, dict):
            raise AppError(
                "VALIDATION_ERROR",
                f"Axis '{axis}' must be an object with label/low/high.",
                422,
                field_errors=[{"field": axis, "message": "Expected an object."}],
            )
        for key in ("label", "low", "high"):
            if not isinstance(cfg.get(key), str):
                raise AppError(
                    "VALIDATION_ERROR",
                    f"Axis '{axis}.{key}' must be text.",
                    422,
                    field_errors=[{"field": f"{axis}.{key}", "message": "Expected text."}],
                )


def update_axes(db: Session, row: BusinessPositioningMap, axes: dict) -> BusinessPositioningMap:
    validate_axes(axes)
    row.axes = axes
    db.flush()
    return row


def serialize_map(row: BusinessPositioningMap) -> dict[str, Any]:
    return {"axes": row.axes}


def assemble_map(db: Session, startup: Startup) -> dict[str, Any]:
    row = get_or_create_map(db, startup)
    comps = (
        db.query(BusinessRecord)
        .filter_by(startup_id=startup.id, kind=RecordKind.competitor)
        .order_by(BusinessRecord.position.asc())
        .all()
    )
    return {
        "axes": row.axes,
        "competitors": [
            {
                "id": str(c.id),
                "name": c.data.get("name"),
                "x": c.data.get("map_x"),
                "y": c.data.get("map_y"),
                "threat_level": c.data.get("threat_level"),
            }
            for c in comps
        ],
    }
```

- [ ] **Step 5: Run service tests**

Run: `poetry run pytest tests/services/business/test_positioning.py -v`
Expected: PASS (all six).

- [ ] **Step 6: Write failing API tests**

```python
# tests/api/test_business_positioning.py
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    return u, s, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def test_get_positioning_map_defaults(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/business-builder/positioning-map", headers=h)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["axes"]["x"]["label"] == "Price" and body["competitors"] == []


def test_put_axes_then_coords_via_competitor(client, db):
    _u, _s, h = _member(db)
    put = client.put(
        "/api/v1/business-builder/positioning-map",
        json={"axes": {"x": {"label": "Reach", "low": "Niche", "high": "Mass"},
                       "y": {"label": "Trust", "low": "New", "high": "Proven"}}},
        headers=h,
    )
    assert put.status_code == 200 and put.json()["data"]["axes"]["x"]["label"] == "Reach"

    rid = client.post(
        "/api/v1/business-builder/competitors",
        json={"data": {"name": "Acme", "map_x": 0.3, "map_y": 0.7}},
        headers=h,
    ).json()["data"]["id"]

    got = client.get("/api/v1/business-builder/positioning-map", headers=h).json()["data"]
    acme = next(c for c in got["competitors"] if c["id"] == rid)
    assert acme["x"] == 0.3 and acme["y"] == 0.7


def test_put_axes_editor_only_403(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor)
    assert client.put(
        "/api/v1/business-builder/positioning-map", json={"axes": {}}, headers=h
    ).status_code == 403


def test_positioning_map_not_shadowed_by_kind(client, db):
    _u, _s, h = _member(db)
    assert client.get("/api/v1/business-builder/positioning-map", headers=h).status_code == 200
```

- [ ] **Step 7: Add the two endpoints (before `/{kind}`)**

Add import `from app.services.business.positioning import assemble_map, get_or_create_map, serialize_map, update_axes` and insert alongside the suggestion routes (before `@router.get("/{kind}")`):

```python
@router.get("/positioning-map")
def get_positioning_map(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    data = assemble_map(db, startup)  # lazily creates the axes row
    db.commit()
    return success_response(data)


@router.put("/positioning-map")
def put_positioning_map(
    body: PositioningMapSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    row = get_or_create_map(db, startup)
    update_axes(db, row, body.axes)
    db.commit()
    return success_response(serialize_map(row))
```

- [ ] **Step 8: Run the positioning API tests**

Run: `poetry run pytest tests/api/test_business_positioning.py -v`
Expected: PASS (all four).

- [ ] **Step 9: Commit**

```bash
git add app/services/business/record_defs.py app/services/business/positioning.py app/api/v1/endpoints/business.py tests/services/business/test_positioning.py tests/api/test_business_positioning.py
git commit -m "feat(business-builder): competitor positioning map — coords + editable axes"
```

---

### Task 6: Live e2e, FE integration guide, SOP, checklist

Prove the slice end-to-end against a real server, capture real payloads, and finish the shipping docs.

**Files:**
- Modify: `e2e/test_business_builder.py` (append suggestion + positioning journeys)
- Create: `docs/fe-integration-guide-business-builder-suggestions.md`
- Create: `docs/sop/2026-09-08-business-builder-slice3.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`
- Create: `e2e/_captures/` entries (written by the e2e run)

**Interfaces:**
- Consumes: the running app (via `scripts/e2e_run.sh`), the existing e2e client/helpers in `e2e/conftest.py` and `e2e/test_business_builder.py`.

- [ ] **Step 1: Read the existing e2e harness**

Read `e2e/conftest.py` and the top of `e2e/test_business_builder.py` to reuse the existing auth/workspace bootstrap helpers and the `_captures` writing convention. Match them exactly — do not invent a new client fixture.

- [ ] **Step 2: Append the suggestion round-trip journey**

Add a test that, against the live server: (a) as a founder, creates a canvas; (b) creates a second member with `business_consultant` role in the same workspace; (c) BC `POST /suggestions` a `canvas_update`; (d) BC gets 403 on approve; (e) founder `GET /suggestions?status=pending` sees it; (f) founder approves; (g) `GET /canvases/business_model` reflects the change; (h) approving again → 409. Write each request+response body to `e2e/_captures/business_suggestions_*.json` using the same helper the file already uses for captures.

- [ ] **Step 3: Append the positioning-map journey**

Live: `GET /positioning-map` (defaults), `PUT` new axes, `POST /competitors` with `map_x/map_y`, `GET /positioning-map` shows the competitor with coords. Capture bodies to `e2e/_captures/business_positioning_*.json`.

- [ ] **Step 4: Run the full e2e suite**

```bash
scripts/e2e_run.sh
```
Expected: green, including the two new journeys. Fix any real-server-only failures (e.g. a missing `db.commit()` surfaces here as "the change didn't persist" — exactly the journal bug; verify every write endpoint commits).

- [ ] **Step 5: Write the FE integration guide from the captures**

Create `docs/fe-integration-guide-business-builder-suggestions.md`. **Every payload/status/error body pasted verbatim from `e2e/_captures/`** — never from the schema. Cover: the four suggestion endpoints with real request/response bodies; the `op`/`target`/`payload` shapes per op; the `current`-vs-`payload` diff contract (call out that `current` is `null` for `record_create`); the state machine + `SUGGESTION_NOT_PENDING` (409) and `CANVAS_VERSION_CONFLICT` (409) real error bodies; the role rule (any member suggests, editor approves); the positioning-map endpoints and the **coordinate-on-competitor trap** (coords are edited via `PUT /competitors/{id}`, not the map endpoint). End with a verification table marking each behaviour verified-live (cite the capture filename).

- [ ] **Step 6: Write the SOP**

Create `docs/sop/2026-09-08-business-builder-slice3.md` in the project's SOP style: what shipped (one line + commit refs), why, how (op-based apply-through-existing-services decision + positioning coords-on-record decision), what's involved (files/tables/migrations/endpoints with paths), verification (unit + e2e + captures), operate/rollback (two migrations, `downgrade` drops both tables), follow-ups (reject-reason field; AI plan generator still gated on Modules 03+18).

- [ ] **Step 7: Update the master checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, mark Module 08 Slice 3 items done (suggestions workflow, positioning map), and record — honestly — that the AI Business Plan generator remains deferred (dependency-blocked on Modules 03 + 18), so Module 08 is "complete except the plan generator."

- [ ] **Step 8: Reproduce every CI check locally, then commit**

```bash
poetry run ruff check app tests
poetry run black --check app tests
poetry run mypy app
poetry run pytest
scripts/e2e_run.sh
```
All green (match pinned versions via `poetry run`). Then:

```bash
git add e2e/ docs/fe-integration-guide-business-builder-suggestions.md docs/sop/2026-09-08-business-builder-slice3.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(business-builder): Slice 3 live e2e + captures + FE guide + SOP + checklist"
```

---

## Self-Review

**1. Spec coverage:**
- §2.2 four ops → Task 2 (create/validate) + Task 3 (apply). ✓
- §2.3 table + enums → Task 1. ✓
- §2.4 create-time validation (per op, 404/422) → Task 2 tests. ✓
- §2.5 approve edge cases (409 stale canvas, 404 target gone, stays pending) → Task 3 tests. ✓
- §2.6 state machine (409 non-pending) → Task 3 + Task 4. ✓
- §2.7 endpoints + auth + route ordering → Task 4. ✓
- §2.8 `current` serialization → Task 2. ✓
- §3.1 coords on `CompetitorData` → Task 5. ✓
- §3.2 singleton axes table + lazy create → Task 1 (table) + Task 5 (service). ✓
- §3.3 map endpoints → Task 5. ✓
- §4.1 migrations single-head + numbering coordination → Task 1. ✓
- §4.2 three events → Task 2 (created) + Task 3 (approved/rejected). ✓
- §4.3 errors → `SuggestionNotPending` (Task 2), reused `CanvasVersionConflict`/`NotFound`/`VALIDATION_ERROR`. ✓
- §4.4 `db.commit()` on every write/lazy-create endpoint → Task 4 + Task 5. ✓
- §5 testing (unit + live e2e + FE guide) → Tasks 2–6. ✓
- §6 file structure → matches Tasks 1–6. ✓
- §7 decisions recorded in SOP → Task 6. ✓

**2. Placeholder scan:** the only `00NN` tokens are migration filenames, resolved by the explicit numbering rule in Task 1 (settle against live head). No `TODO`/"handle edge cases"/bare "write tests" — every code and test step carries real content.

**3. Type consistency:** `create_suggestion(db, membership, op, target, payload, note)`, `approve_suggestion(db, membership, suggestion_id)`, `serialize_suggestion(db, s)`, `get_or_create_map(db, startup)`, `update_axes(db, row, axes)`, `assemble_map(db, startup)` are used identically in their tests and endpoints. `SuggestionOp`/`SuggestionStatus` members and the `.value` strings match across model, service, tests, and API. The `User` display-name shape was verified against `app/db/models/user.py` (name lives on `user.profile.full_name`, nullable; `user.email` always present) and the serializer guards for a missing profile.
