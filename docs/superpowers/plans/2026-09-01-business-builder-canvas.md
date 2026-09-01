# Module 08 Business Builder · Slice 1 (Canvas Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the five block-based Business Builder artifacts (Business Model Canvas, Lean Canvas, Value Proposition, Mission/Vision, SWOT) through one generic canvas table + a block-definition registry, with lazy-get, versioned save (optimistic concurrency), a completion grid, and a deferred "Fill with AI" job seam.

**Architecture:** One `business_canvases` table (`type`, `blocks` JSONB, `version`) is driven by a static per-type block registry that supplies the empty scaffold, save-validation, and completion math. Writes use an integer `version` counter with optimistic concurrency (stale save → 409). "Fill with AI" enqueues a deferred job via the existing `job_dispatcher` stub — the API/FE contract exists now, a worker/Module 03 drains it later.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 (typed `Mapped`) / Alembic / PostgreSQL / Poetry / pytest (real Postgres, per-test transaction rollback).

**Spec:** `docs/superpowers/specs/2026-09-01-business-builder-canvas-design.md`

## Global Constraints

- **Branch:** `feat/business-builder-canvas`. **Migration:** `0011_business_canvases`, `down_revision = "0010_dashboard"` (current head — verify with `poetry run alembic heads`). Single head after.
- **Envelope:** every endpoint returns `success_response(data)` from `app.core.envelope` → `{ "data": …, "meta": null }`. Errors via `AppError` / the `app.core.errors` taxonomy.
- **Access:** reads = any active member (`membership: Membership = Depends(require_workspace)`); writes = editor `require_role(MembershipRole.founder, MembershipRole.team_member)` (mentor → 403). All routes `Depends(get_verified_user)` + `Depends(get_db)`. Cross-tenant / non-member → uniform 403; unknown `{type}` → 404.
- **`{type}` path param is a `str`**, converted with `CanvasType(type)` inside a `try/except ValueError → raise NotFound()` (mirrors `health_score.get_dimension`'s `dim not in … → NotFound()`). Do NOT bind `type: CanvasType` directly (that yields 422, not the spec's 404).
- **One domain-specific error only:** `CanvasVersionConflict` — code `"CANVAS_VERSION_CONFLICT"`, http `409` — added as an `AppError` subclass (consistent with `DependencyCycle`/`AlreadyMember`). All other cases reuse existing codes.
- **AI is deferred:** `ai-fill` enqueues a `business.canvas.ai_fill` job and returns it; it writes NO canvas and there is NO worker in this slice.
- **No AI-attribution** in any commit message or artifact. **TDD**, real Postgres + per-test rollback.

---

## File Structure

**Create:**
- `app/db/models/business.py` — `BusinessCanvas` model.
- `app/services/business/__init__.py`, `app/services/business/canvas_defs.py` — `BlockDef` + `CANVAS_BLOCKS` registry + `empty_blocks()`.
- `app/services/business/service.py` — `get_or_create_canvas`, `validate_blocks`, `save_canvas`, `completion`, `overview`, `serialize_canvas`.
- `app/api/v1/endpoints/business.py` — the four routes.
- `app/schemas/business.py` — `CanvasSave` request body.
- `alembic/versions/0011_business_canvases.py` — the migration.
- Tests: `tests/services/business/__init__.py`, `tests/services/business/test_canvas_defs.py`, `tests/services/business/test_service.py`, `tests/api/test_business_canvases.py`, `e2e/test_business_builder.py`.
- Docs (Task 6): `docs/sop/2026-09-01-business-builder-canvas.md`, `docs/fe-integration-guide-business-builder.md`.

**Modify (additive):**
- `app/db/models/enums.py` — add `CanvasType`.
- `app/db/models/__init__.py` — register `BusinessCanvas`.
- `app/core/errors.py` — add `CanvasVersionConflict`.
- `app/api/v1/api.py` — include the business router.
- `tests/factories.py` — add `create_business_canvas`.
- `e2e/test_smoke.py` — add the business-builder paths.
- `docs/checklist/PROJECT_CHECKLIST.md` — reconcile (Task 6).

---

## Task 1: Enum + model + migration `0011` + block registry + error + factory

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/__init__.py`, `app/core/errors.py`, `tests/factories.py`
- Create: `app/db/models/business.py`, `app/services/business/__init__.py`, `app/services/business/canvas_defs.py`, `alembic/versions/0011_business_canvases.py`, `tests/services/business/__init__.py`, `tests/services/business/test_canvas_defs.py`
- Test: `tests/services/business/test_canvas_defs.py`

**Interfaces:**
- Produces: `CanvasType` enum; `BusinessCanvas` model; `BlockDef(key,label,kind)`, `CANVAS_BLOCKS: dict[CanvasType, tuple[BlockDef,...]]`, `empty_blocks(canvas_type) -> dict`; `CanvasVersionConflict` error; `create_business_canvas(db, *, startup, type=CanvasType.business_model, blocks=None, version=1) -> BusinessCanvas`.

- [ ] **Step 1: Write the failing registry test** — `tests/services/business/test_canvas_defs.py`

```python
from app.db.models.enums import CanvasType
from app.services.business.canvas_defs import CANVAS_BLOCKS, empty_blocks


def test_every_canvas_type_has_blocks():
    assert set(CANVAS_BLOCKS) == set(CanvasType)
    assert len(CANVAS_BLOCKS[CanvasType.business_model]) == 9
    assert len(CANVAS_BLOCKS[CanvasType.lean]) == 9
    assert len(CANVAS_BLOCKS[CanvasType.value_prop]) == 6
    assert len(CANVAS_BLOCKS[CanvasType.swot]) == 4
    assert len(CANVAS_BLOCKS[CanvasType.mission_vision]) == 2


def test_block_keys_unique_per_type_and_kinds_valid():
    for canvas_type, blocks in CANVAS_BLOCKS.items():
        keys = [b.key for b in blocks]
        assert len(keys) == len(set(keys)), f"dup keys in {canvas_type}"
        for b in blocks:
            assert b.kind in ("text", "list")


def test_empty_blocks_scaffold_matches_kinds():
    scaffold = empty_blocks(CanvasType.business_model)
    assert scaffold == {b.key: [] for b in CANVAS_BLOCKS[CanvasType.business_model]}
    mv = empty_blocks(CanvasType.mission_vision)
    assert mv == {"mission": "", "vision": ""}
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/business/test_canvas_defs.py -v`
Expected: FAIL — `CanvasType`/module missing.

- [ ] **Step 3: Add the enum** — append to `app/db/models/enums.py` (match the `enum.StrEnum` style of `MissionTaskStatus`)

```python
class CanvasType(enum.StrEnum):
    business_model = "business_model"
    lean = "lean"
    value_prop = "value_prop"
    mission_vision = "mission_vision"
    swot = "swot"
```

- [ ] **Step 4: Create the registry** — `app/services/business/canvas_defs.py` (and empty `app/services/business/__init__.py`)

```python
from dataclasses import dataclass

from app.db.models.enums import CanvasType


@dataclass(frozen=True)
class BlockDef:
    key: str
    label: str
    kind: str  # "text" | "list"


def _list(*pairs: tuple[str, str]) -> tuple[BlockDef, ...]:
    return tuple(BlockDef(key=k, label=lbl, kind="list") for k, lbl in pairs)


CANVAS_BLOCKS: dict[CanvasType, tuple[BlockDef, ...]] = {
    CanvasType.business_model: _list(
        ("key_partners", "Key Partners"),
        ("key_activities", "Key Activities"),
        ("key_resources", "Key Resources"),
        ("value_propositions", "Value Propositions"),
        ("customer_relationships", "Customer Relationships"),
        ("channels", "Channels"),
        ("customer_segments", "Customer Segments"),
        ("cost_structure", "Cost Structure"),
        ("revenue_streams", "Revenue Streams"),
    ),
    CanvasType.lean: _list(
        ("problem", "Problem"),
        ("solution", "Solution"),
        ("key_metrics", "Key Metrics"),
        ("unique_value_proposition", "Unique Value Proposition"),
        ("unfair_advantage", "Unfair Advantage"),
        ("channels", "Channels"),
        ("customer_segments", "Customer Segments"),
        ("cost_structure", "Cost Structure"),
        ("revenue_streams", "Revenue Streams"),
    ),
    CanvasType.value_prop: _list(
        ("jobs", "Customer Jobs"),
        ("pains", "Pains"),
        ("gains", "Gains"),
        ("products_services", "Products & Services"),
        ("pain_relievers", "Pain Relievers"),
        ("gain_creators", "Gain Creators"),
    ),
    CanvasType.swot: _list(
        ("strengths", "Strengths"),
        ("weaknesses", "Weaknesses"),
        ("opportunities", "Opportunities"),
        ("threats", "Threats"),
    ),
    CanvasType.mission_vision: (
        BlockDef(key="mission", label="Mission", kind="text"),
        BlockDef(key="vision", label="Vision", kind="text"),
    ),
}


def empty_blocks(canvas_type: CanvasType) -> dict:
    return {b.key: ("" if b.kind == "text" else []) for b in CANVAS_BLOCKS[canvas_type]}
```

- [ ] **Step 5: Run the registry test** — `poetry run pytest tests/services/business/test_canvas_defs.py -v` → PASS.

- [ ] **Step 6: Create the model** — `app/db/models/business.py` (base it on `app/db/models/activity.py` for the mixin/JSONB imports)

```python
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CanvasType


class BusinessCanvas(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_canvases"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[CanvasType] = mapped_column(SAEnum(CanvasType, name="canvastype"), nullable=False)
    blocks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("startup_id", "type", name="uq_business_canvas_startup_type"),
    )
```

Check the exact `Enum` import/usage against an existing model that stores a StrEnum column (e.g. `app/db/models/mission.py` — match how it declares the `SAEnum`/`name=`). Register in `app/db/models/__init__.py`: add `from app.db.models.business import BusinessCanvas  # noqa: F401` the same way `ActivityLog` is registered.

- [ ] **Step 7: Add the error** — in `app/core/errors.py`, after the existing 409 classes (e.g. `AlreadyMember`)

```python
class CanvasVersionConflict(AppError):  # noqa: N818
    code, http_status = "CANVAS_VERSION_CONFLICT", 409
```

- [ ] **Step 8: Create the migration** — `alembic/versions/0011_business_canvases.py`

Copy the structure of `alembic/versions/0010_dashboard.py`. Set `revision = "0011_business_canvases"`, `down_revision = "0010_dashboard"`. `upgrade()` creates the `canvastype` enum (if your migration style creates enums explicitly — match how `0010`/`0009` handle their enums) and the `business_canvases` table (UUID `id` PK, `startup_id` UUID NOT NULL FK→`startups.id` ON DELETE CASCADE, `type` the enum NOT NULL, `blocks` JSONB NOT NULL, `version` Integer NOT NULL server_default `"1"`, `created_at`/`updated_at`) with `UniqueConstraint("startup_id","type", name="uq_business_canvas_startup_type")`. `downgrade()` drops the table then the enum type.

- [ ] **Step 9: Add the factory** — append to `tests/factories.py`

```python
def create_business_canvas(
    db: Session,
    *,
    startup: Startup,
    type: "CanvasType" = None,  # default set in body to avoid import-time enum ref
    blocks: dict | None = None,
    version: int = 1,
) -> "BusinessCanvas":
    from app.db.models.business import BusinessCanvas
    from app.db.models.enums import CanvasType
    from app.services.business.canvas_defs import empty_blocks

    ctype = type or CanvasType.business_model
    row = BusinessCanvas(
        startup_id=startup.id,
        type=ctype,
        blocks=blocks if blocks is not None else empty_blocks(ctype),
        version=version,
    )
    db.add(row)
    db.flush()
    return row
```

- [ ] **Step 10: Migration round-trip + gates + commit**

Run: `poetry run alembic upgrade head` (single head `0011_business_canvases`), then `downgrade -1` then `upgrade head` clean.
Run: `poetry run black app tests && poetry run isort app tests && poetry run ruff check . && poetry run mypy app`
```bash
git add app/db/models/enums.py app/db/models/business.py app/db/models/__init__.py app/services/business/ app/core/errors.py alembic/versions/0011_business_canvases.py tests/factories.py tests/services/business/
git commit -m "feat(business-builder): CanvasType + business_canvases model + migration 0011 + block registry"
```

---

## Task 2: Service — get_or_create / validate / save (versioning + event) / completion / overview

**Files:**
- Create: `app/services/business/service.py`
- Test: `tests/services/business/test_service.py`

**Interfaces:**
- Consumes: `BusinessCanvas`, `CanvasType`, `CANVAS_BLOCKS`, `empty_blocks`, `CanvasVersionConflict`, `event_bus` (`from app.platform.events import event_bus`).
- Produces: `get_or_create_canvas(db, startup, canvas_type: CanvasType) -> BusinessCanvas`; `validate_blocks(canvas_type: CanvasType, blocks: dict) -> None`; `save_canvas(db, canvas: BusinessCanvas, blocks: dict, expected_version: int) -> BusinessCanvas`; `completion(canvas_type: CanvasType, blocks: dict) -> dict`; `overview(db, startup) -> list[dict]`; `serialize_canvas(canvas: BusinessCanvas) -> dict`.

- [ ] **Step 1: Write the failing tests** — `tests/services/business/test_service.py`

```python
import pytest
from app.core.errors import AppError, CanvasVersionConflict
from app.db.models.enums import CanvasType
from app.services.business.service import (
    completion, get_or_create_canvas, overview, save_canvas, validate_blocks,
)
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def test_get_or_create_lazily_builds_full_scaffold(db):
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.business_model)
    assert c.version == 1
    assert set(c.blocks) == {b.key for b in __import__(
        "app.services.business.canvas_defs", fromlist=["CANVAS_BLOCKS"]
    ).CANVAS_BLOCKS[CanvasType.business_model]}
    assert get_or_create_canvas(db, s, CanvasType.business_model).id == c.id  # idempotent


def test_validate_rejects_unknown_key_and_wrong_kind(db):
    with pytest.raises(AppError):
        validate_blocks(CanvasType.swot, {"not_a_block": []})
    with pytest.raises(AppError):
        validate_blocks(CanvasType.swot, {"strengths": "should-be-a-list"})
    with pytest.raises(AppError):
        validate_blocks(CanvasType.mission_vision, {"mission": ["should-be-text"]})
    validate_blocks(CanvasType.swot, {"strengths": ["a", "b"]})  # ok, partial


def test_save_bumps_version_and_rejects_stale(db):
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.swot)
    saved = save_canvas(db, c, {"strengths": ["fast"]}, expected_version=1)
    assert saved.version == 2
    assert saved.blocks["strengths"] == ["fast"]
    with pytest.raises(CanvasVersionConflict):
        save_canvas(db, saved, {"weaknesses": ["slow"]}, expected_version=1)  # stale


def test_completion_transitions_and_emits_once(db, monkeypatch):
    events = []
    monkeypatch.setattr("app.services.business.service.event_bus.publish",
                        lambda e, p: events.append((e, p)))
    s = _startup(db)
    c = get_or_create_canvas(db, s, CanvasType.mission_vision)
    assert completion(CanvasType.mission_vision, c.blocks)["status"] == "start"
    c = save_canvas(db, c, {"mission": "Do good"}, expected_version=1)
    assert completion(CanvasType.mission_vision, c.blocks)["status"] == "continue"
    assert events == []
    c = save_canvas(db, c, {"mission": "Do good", "vision": "World wins"}, expected_version=2)
    assert completion(CanvasType.mission_vision, c.blocks)["status"] == "complete"
    assert [e for e, _ in events] == ["business.artifact.completed"]
    save_canvas(db, c, {"mission": "Do more good", "vision": "World wins"}, expected_version=3)
    assert [e for e, _ in events] == ["business.artifact.completed"]  # not re-emitted


def test_overview_is_read_only_and_covers_all_types(db):
    s = _startup(db)
    rows = overview(db, s)
    assert {r["type"] for r in rows} == {t.value for t in CanvasType}
    assert all(r["status"] == "start" for r in rows)
    # overview created no rows
    from app.db.models.business import BusinessCanvas
    assert db.query(BusinessCanvas).filter_by(startup_id=s.id).count() == 0
```

- [ ] **Step 2: Run to verify they fail** — `poetry run pytest tests/services/business/test_service.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** — `app/services/business/service.py`

```python
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, CanvasVersionConflict
from app.db.models.business import BusinessCanvas
from app.db.models.enums import CanvasType
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.business.canvas_defs import CANVAS_BLOCKS, empty_blocks


def _defs(canvas_type: CanvasType):
    return CANVAS_BLOCKS[canvas_type]


def get_or_create_canvas(db: Session, startup: Startup, canvas_type: CanvasType) -> BusinessCanvas:
    row = (
        db.query(BusinessCanvas)
        .filter_by(startup_id=startup.id, type=canvas_type)
        .first()
    )
    if row is None:
        row = BusinessCanvas(
            startup_id=startup.id, type=canvas_type, blocks=empty_blocks(canvas_type), version=1
        )
        db.add(row)
        db.flush()
    return row


def validate_blocks(canvas_type: CanvasType, blocks: dict) -> None:
    kinds = {b.key: b.kind for b in _defs(canvas_type)}
    for key, value in blocks.items():
        if key not in kinds:
            raise AppError("VALIDATION_ERROR", f"Unknown block '{key}' for this canvas.", 422,
                           field_errors=[{"field": key, "message": "Not a block on this canvas."}])
        if kinds[key] == "text" and not isinstance(value, str):
            raise AppError("VALIDATION_ERROR", f"Block '{key}' must be text.", 422,
                           field_errors=[{"field": key, "message": "Expected text."}])
        if kinds[key] == "list" and not (
            isinstance(value, list) and all(isinstance(i, str) for i in value)
        ):
            raise AppError("VALIDATION_ERROR", f"Block '{key}' must be a list of text items.", 422,
                           field_errors=[{"field": key, "message": "Expected a list of text."}])


def _is_filled(kind: str, value: Any) -> bool:
    return bool(value)  # non-empty str or non-empty list


def completion(canvas_type: CanvasType, blocks: dict) -> dict:
    defs = _defs(canvas_type)
    total = len(defs)
    filled = sum(1 for b in defs if _is_filled(b.kind, blocks.get(b.key)))
    if filled == 0:
        status = "start"
    elif filled == total:
        status = "complete"
    else:
        status = "continue"
    return {
        "filled_blocks": filled,
        "total_blocks": total,
        "completion_pct": round(filled / total * 100) if total else 0,
        "status": status,
    }


def save_canvas(
    db: Session, canvas: BusinessCanvas, blocks: dict, expected_version: int
) -> BusinessCanvas:
    validate_blocks(canvas.type, blocks)
    if expected_version != canvas.version:
        raise CanvasVersionConflict(
            "This canvas was changed elsewhere. Reload and reapply your edits."
        )
    was_complete = completion(canvas.type, canvas.blocks)["status"] == "complete"
    merged = empty_blocks(canvas.type)
    merged.update({k: v for k, v in blocks.items()})
    canvas.blocks = merged
    canvas.version += 1
    db.flush()
    now_complete = completion(canvas.type, canvas.blocks)["status"] == "complete"
    if now_complete and not was_complete:
        event_bus.publish(
            "business.artifact.completed",
            {"startup_id": str(canvas.startup_id), "canvas_type": canvas.type.value},
        )
    return canvas


def serialize_canvas(canvas: BusinessCanvas) -> dict:
    return {
        "type": canvas.type.value,
        "version": canvas.version,
        "blocks": canvas.blocks,
        "block_defs": [
            {"key": b.key, "label": b.label, "kind": b.kind} for b in _defs(canvas.type)
        ],
        "completion": completion(canvas.type, canvas.blocks),
    }


def overview(db: Session, startup: Startup) -> list[dict]:
    existing = {
        c.type: c
        for c in db.query(BusinessCanvas).filter_by(startup_id=startup.id).all()
    }
    rows = []
    for canvas_type in CanvasType:
        blocks = existing[canvas_type].blocks if canvas_type in existing else empty_blocks(canvas_type)
        rows.append({
            "type": canvas_type.value,
            "label": canvas_type.value.replace("_", " ").title(),
            **completion(canvas_type, blocks),
        })
    return rows
```

Confirm the `AppError(...)` positional/kwarg signature against how `app/api/v1/endpoints/mission.py::patch_task` raises `VALIDATION_ERROR` (code, message, http, `field_errors=`); match it. Confirm `CanvasVersionConflict(message)` works with the base `AppError.__init__` (it takes a message; `code`/`http_status` come from the class attrs).

- [ ] **Step 4: Run the tests** — `poetry run pytest tests/services/business/test_service.py -v` → PASS. Then `poetry run pytest -q` (full suite green).

- [ ] **Step 5: Gates + commit**

```bash
git add app/services/business/service.py tests/services/business/test_service.py
git commit -m "feat(business-builder): canvas service — lazy-get, validate, versioned save + completion"
```

---

## Task 3: `GET /overview` + `GET /canvases/{type}`

**Files:**
- Create: `app/api/v1/endpoints/business.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/test_business_canvases.py`

**Interfaces:**
- Consumes: `overview`, `get_or_create_canvas`, `serialize_canvas` (Task 2). Deps per Global Constraints.
- Produces: `GET /api/v1/business-builder/overview`, `GET /api/v1/business-builder/canvases/{type}`; the `_parse_type(type)->CanvasType` helper and `_startup(db, membership)` helper used by Tasks 4–5.

- [ ] **Step 1: Write the failing tests** — `tests/api/test_business_canvases.py` (reuse the API harness style from `tests/api/test_activity_wiring.py` — the `_member(db, *, role=...)` helper returning `(user, startup, headers)` with a verified founder + `create_access_token` + `X-Workspace-Id`; copy it).

```python
def test_overview_lists_all_types_as_start(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/business-builder/overview", headers=h)
    assert r.status_code == 200
    rows = r.json()["data"]
    assert {row["type"] for row in rows} == {
        "business_model", "lean", "value_prop", "mission_vision", "swot"}
    assert all(row["status"] == "start" for row in rows)


def test_get_canvas_lazy_creates_and_returns_defs(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/business-builder/canvases/swot", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["type"] == "swot" and data["version"] == 1
    assert [b["key"] for b in data["block_defs"]] == [
        "strengths", "weaknesses", "opportunities", "threats"]
    assert data["blocks"] == {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}


def test_get_unknown_type_404(client, db):
    _u, _s, h = _member(db)
    assert client.get("/api/v1/business-builder/canvases/not_a_type", headers=h).status_code == 404


def test_overview_requires_membership_403(client, db):
    outsider_headers = _outsider(db)  # a verified user who is not a member of the workspace
    assert client.get("/api/v1/business-builder/overview", headers=outsider_headers).status_code == 403
```

(Build `_member`/`_outsider` from the real factories — same pattern the dashboard API tests used.)

- [ ] **Step 2: Run to verify they fail** — FAIL (routes missing).

- [ ] **Step 3: Implement** — `app/api/v1/endpoints/business.py`

```python
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import CanvasType, MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.services.business.service import get_or_create_canvas, overview, serialize_canvas

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


def _parse_type(type: str) -> CanvasType:
    try:
        return CanvasType(type)
    except ValueError:
        raise NotFound()


@router.get("/overview")
def get_overview(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(overview(db, _startup(db, membership)))


@router.get("/canvases/{type}")
def get_canvas(
    type: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    canvas = get_or_create_canvas(db, _startup(db, membership), canvas_type)
    db.commit()  # lazy-create persists
    return success_response(serialize_canvas(canvas))
```

Register in `app/api/v1/api.py`: add `business` to the grouped `from app.api.v1.endpoints import (...)` import (keep alphabetical) and `api_router.include_router(business.router, prefix="/business-builder", tags=["business-builder"])`.

- [ ] **Step 4: Run** — `poetry run pytest tests/api/test_business_canvases.py -v` → PASS.

- [ ] **Step 5: Gates + commit**

```bash
git add app/api/v1/endpoints/business.py app/api/v1/api.py tests/api/test_business_canvases.py
git commit -m "feat(business-builder): GET /overview + GET /canvases/{type}"
```

---

## Task 4: `PUT /canvases/{type}` (validation + optimistic concurrency)

**Files:**
- Create: `app/schemas/business.py`
- Modify: `app/api/v1/endpoints/business.py`
- Test: `tests/api/test_business_canvases.py` (extend)

**Interfaces:**
- Consumes: `save_canvas`, `get_or_create_canvas`, `serialize_canvas`, `_parse_type`, `_startup`, `_editor`.
- Produces: `PUT /api/v1/business-builder/canvases/{type}`; `CanvasSave` schema.

- [ ] **Step 1: Write the failing tests** — extend `tests/api/test_business_canvases.py`

```python
def test_put_saves_and_bumps_version(client, db):
    _u, _s, h = _member(db)
    client.get("/api/v1/business-builder/canvases/business_model", headers=h)  # lazy-create v1
    r = client.put("/api/v1/business-builder/canvases/business_model",
                    json={"blocks": {"key_partners": ["Stripe"]}, "version": 1}, headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["version"] == 2
    assert data["blocks"]["key_partners"] == ["Stripe"]
    assert data["completion"]["status"] == "continue"


def test_put_stale_version_409(client, db):
    _u, _s, h = _member(db)
    client.get("/api/v1/business-builder/canvases/swot", headers=h)  # v1
    client.put("/api/v1/business-builder/canvases/swot",
               json={"blocks": {"strengths": ["fast"]}, "version": 1}, headers=h)  # -> v2
    r = client.put("/api/v1/business-builder/canvases/swot",
                   json={"blocks": {"weaknesses": ["slow"]}, "version": 1}, headers=h)  # stale
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CANVAS_VERSION_CONFLICT"


def test_put_bad_block_422(client, db):
    _u, _s, h = _member(db)
    client.get("/api/v1/business-builder/canvases/swot", headers=h)
    r = client.put("/api/v1/business-builder/canvases/swot",
                   json={"blocks": {"strengths": "not-a-list"}, "version": 1}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_mentor_forbidden_403(client, db):
    mentor_headers, s = _member_with_role(db, role="mentor")  # verified mentor member
    client_get_headers = ...  # ensure canvas exists via a founder if needed
    r = client.put("/api/v1/business-builder/canvases/swot",
                   json={"blocks": {"strengths": ["x"]}, "version": 1}, headers=mentor_headers)
    assert r.status_code == 403
```

(Adapt `_member_with_role` to the real harness — a mentor membership. If a mentor can't lazy-create, assert the 403 comes from `_editor` before any body processing.)

- [ ] **Step 2: Run to verify they fail** — FAIL (route missing).

- [ ] **Step 3: Implement the schema** — `app/schemas/business.py`

```python
from pydantic import BaseModel, Field


class CanvasSave(BaseModel):
    blocks: dict = Field(default_factory=dict)
    version: int
```

- [ ] **Step 4: Implement the route** — add to `app/api/v1/endpoints/business.py`

```python
from app.schemas.business import CanvasSave
from app.services.business.service import save_canvas

@router.put("/canvases/{type}")
def put_canvas(
    type: str,
    body: CanvasSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    canvas = get_or_create_canvas(db, _startup(db, membership), canvas_type)
    saved = save_canvas(db, canvas, body.blocks, body.version)
    db.commit()
    return success_response(serialize_canvas(saved))
```

- [ ] **Step 5: Run** — `poetry run pytest tests/api/test_business_canvases.py -v` → PASS. Then `poetry run pytest -q` green.

- [ ] **Step 6: Gates + commit**

```bash
git add app/schemas/business.py app/api/v1/endpoints/business.py tests/api/test_business_canvases.py
git commit -m "feat(business-builder): PUT /canvases/{type} with optimistic-concurrency 409"
```

---

## Task 5: `POST /canvases/{type}/ai-fill` (deferred job enqueue)

**Files:**
- Modify: `app/api/v1/endpoints/business.py`
- Test: `tests/api/test_business_canvases.py` (extend)

**Interfaces:**
- Consumes: `job_dispatcher` (`from app.platform.jobs import job_dispatcher`), `_parse_type`, `_startup`, `_editor`. `job_dispatcher.enqueue(db, type, payload, startup_id) -> Job` (Job has `.id`, `.status`).
- Produces: `POST /api/v1/business-builder/canvases/{type}/ai-fill`.

- [ ] **Step 1: Write the failing test** — extend `tests/api/test_business_canvases.py`

```python
from app.db.models.job import Job

def test_ai_fill_enqueues_job_and_writes_no_canvas(client, db):
    _u, s, h = _member(db)
    r = client.post("/api/v1/business-builder/canvases/lean/ai-fill", headers=h)
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["status"] == "queued" and data["job_id"]
    job = db.query(Job).filter(Job.id == data["job_id"]).one()
    assert job.type == "business.canvas.ai_fill"
    assert job.payload["canvas_type"] == "lean"
    assert job.payload["startup_id"] == str(s.id)
    from app.db.models.business import BusinessCanvas
    assert db.query(BusinessCanvas).filter_by(startup_id=s.id).count() == 0  # no canvas written


def test_ai_fill_mentor_forbidden_403(client, db):
    mentor_headers, _s = _member_with_role(db, role="mentor")
    assert client.post(
        "/api/v1/business-builder/canvases/lean/ai-fill", headers=mentor_headers
    ).status_code == 403
```

- [ ] **Step 2: Run to verify it fails** — FAIL (route missing).

- [ ] **Step 3: Implement** — add to `app/api/v1/endpoints/business.py`

```python
from fastapi import Response, status
from app.platform.jobs import job_dispatcher

@router.post("/canvases/{type}/ai-fill", status_code=status.HTTP_202_ACCEPTED)
def ai_fill_canvas(
    type: str,
    response: Response,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    startup = _startup(db, membership)
    job = job_dispatcher.enqueue(
        db,
        type="business.canvas.ai_fill",
        payload={"startup_id": str(startup.id), "canvas_type": canvas_type.value},
        startup_id=startup.id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})
```

Confirm `job.status` is a `JobStatus` enum with `.value == "queued"` (from `app/db/models/enums.py`) — if it's already a str, drop `.value`. Confirm returning a dict under `status_code=202` yields a 202 (it does; `success_response` returns a dict, FastAPI keeps the route's status_code).

- [ ] **Step 4: Run** — `poetry run pytest tests/api/test_business_canvases.py -v` → PASS. Then `poetry run pytest -q` green.

- [ ] **Step 5: Gates + commit**

```bash
git add app/api/v1/endpoints/business.py tests/api/test_business_canvases.py
git commit -m "feat(business-builder): POST /canvases/{type}/ai-fill — deferred job enqueue"
```

---

## Task 6: Live E2E + smoke surface + SOP + FE guide + checklist reconcile

**Files:**
- Create: `e2e/test_business_builder.py`, `docs/sop/2026-09-01-business-builder-canvas.md`, `docs/fe-integration-guide-business-builder.md`
- Modify: `e2e/test_smoke.py`, `docs/checklist/PROJECT_CHECKLIST.md`
- Captures: `e2e/_captures/business/`

- [ ] **Step 1: Write the E2E journey** — `e2e/test_business_builder.py`, mirroring `e2e/test_dashboard.py`'s structure (same fixtures + the `capture` helper writing to `e2e/_captures/<module>/`). Journey: onboard a founder → `GET /business-builder/overview` (assert all 5 rows `status=="start"`; capture `overview_empty.json`) → `GET /business-builder/canvases/business_model` (assert v1 + block_defs; capture `canvas_get.json`) → `PUT` it with `{"blocks": {"key_partners": ["Stripe","AWS"]}, "version": 1}` (assert v2; capture `canvas_put.json`) → `GET /business-builder/overview` again (assert that row is now `continue`; capture `overview_after.json`) → `POST /business-builder/canvases/business_model/ai-fill` (assert 202 + queued; capture `ai_fill.json`) → `GET /jobs/{id}` (assert the queued job; capture `ai_fill_job.json`). Capture every body verbatim.

- [ ] **Step 2: Extend smoke** — `e2e/test_smoke.py`, add to the asserted paths list:

```python
        "/api/v1/business-builder/overview",
        "/api/v1/business-builder/canvases/{type}",
        "/api/v1/business-builder/canvases/{type}/ai-fill",
```

(Match the registered path placeholder — it's `{type}`.)

- [ ] **Step 3: Run E2E + unit**

Run: `COMPOSE_PROJECT_NAME=cofoundaz-api make e2e` (Docker up; migrates `cofoundaz_e2e` through `0011`). Expected: green; captures written under `e2e/_captures/business/`.
Run: `poetry run pytest -q` → full unit suite green.

- [ ] **Step 4: SOP** — `docs/sop/2026-09-01-business-builder-canvas.md`, matching `docs/sop/2026-08-31-dashboard.md` style: what shipped (+ commit refs), why (Slice 1 of Business Builder; AI deferred behind the job seam), how (generic canvas table + registry, optimistic-concurrency versioning, deferred ai-fill job), files/migration `0011`/the four endpoints with paths, verification (real unit + e2e counts, gates), rollback (`alembic downgrade -1`), follow-ups (Slice 2 typed artifacts, Slice 3 suggestions + plan, real ai-fill worker, version history).

- [ ] **Step 5: FE integration guide** — `docs/fe-integration-guide-business-builder.md`, matching `docs/fe-integration-guide-dashboard.md`. **Every payload copied verbatim from `e2e/_captures/business/`.** Cover the four endpoints; the `block_defs` contract (FE renders blocks from these, doesn't hardcode); the **optimistic-concurrency trap** (send the `version` you fetched; on 409 `CANVAS_VERSION_CONFLICT` reload and reapply); the **ai-fill deferral** (202 → poll `GET /jobs/{id}`; the job stays `queued` until the AI worker ships — mark this "derived/known-deferred", not a bug). End with a verification table marking each behaviour verified-live (cite the capture) or derived.

- [ ] **Step 6: Checklist** — add a `## Module 08 — Business Builder` section to `docs/checklist/PROJECT_CHECKLIST.md` with Slice 1 checked off (marked **shipped on branch `feat/business-builder-canvas`, not yet merged**), Slices 2–3 listed as planned, and update the Snapshot tally + `_Last reconciled_` line honestly.

- [ ] **Step 7: Final gates + commit**

Run: `poetry run pytest -q && poetry run black --check app tests && poetry run isort --check app tests && poetry run ruff check . && poetry run mypy app` — all green (state the numbers). `make e2e` verified in Step 3.
```bash
git add e2e/test_business_builder.py e2e/test_smoke.py e2e/_captures/business/ docs/sop/2026-09-01-business-builder-canvas.md docs/fe-integration-guide-business-builder.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(business-builder): live E2E + smoke + SOP + FE guide + checklist reconcile"
```

---

## Self-Review notes

- **Spec coverage:** §3 model+enum → Task 1; §4 registry → Task 1; §5 service (get_or_create/validate/save/completion/overview) → Task 2; §6 endpoints → Tasks 3 (GET×2), 4 (PUT), 5 (ai-fill); §7 errors (`CANVAS_VERSION_CONFLICT`, reuse others) → Tasks 1+2+4, events (`business.artifact.completed`) → Task 2, config (job type string) → Task 5; §8 testing → each task's tests + Task 6 e2e + FE guide; §9 plan shape → these 6 tasks (the completion event lives in `save_canvas`/Task 2, not Task 5).
- **Names/types consistent:** `CanvasType`, `BusinessCanvas`, `CANVAS_BLOCKS`/`empty_blocks`/`BlockDef`, `get_or_create_canvas`/`validate_blocks`/`save_canvas`/`completion`/`overview`/`serialize_canvas`, `CanvasVersionConflict`/`CANVAS_VERSION_CONFLICT`, `CanvasSave`, migration `0011_business_canvases` ← `0010_dashboard`, router `prefix="/business-builder"`, job type `"business.canvas.ai_fill"`.
- **Verify-before-coding flags for implementers:** the `SAEnum(..., name=...)` declaration style (match an existing StrEnum column), the `AppError(...)` call signature, `JobStatus.value == "queued"`, and the migration's enum-creation style — all called out inline; confirm against real source, don't assume.
- **Read-only `overview`** (creates no rows) and **lazy-create on `GET /canvases/{type}`** are both asserted by tests (Task 2 `test_overview_is_read_only...`, Task 3 `test_get_canvas_lazy_creates...`). The full suite is re-run at the end of Tasks 2, 4, 5, 6.
