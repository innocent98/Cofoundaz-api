# Module 08 Business Builder Slice 2 (Typed Artifacts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the four typed Business Builder artifacts (Personas, Revenue Streams, Competitors, Pricing) as a generic `business_records` collection with uniform per-kind CRUD, a deferred AI-fill job, and integration into the Slice-1 completion overview.

**Architecture:** One `business_records` table (`startup_id, kind, data JSONB, position`) + a per-kind Pydantic-schema registry (`RECORD_SCHEMAS`) validates each kind's `data`. All four kinds are record collections addressed by a `{kind}` path segment; Pricing is a one-record collection whose `data` carries nested `tiers`. Reuses Slice-1's endpoint file, helpers, and deferred job-enqueue pattern.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 (typed `Mapped`) / Pydantic v2 / Alembic / PostgreSQL / Poetry / pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-09-04-business-builder-records-design.md`

## Global Constraints

- **Branch:** `feat/business-builder-records`, base **`develop`** (feature PRs target `develop`, not `main`). **Migration `0012_business_records`, `down_revision = "0011_business_canvases"`** (current `develop` head). Single head after.
- **Envelope:** `success_response(data)` from `app.core.envelope` → `{ "data": …, "meta": null }`. Errors via `AppError` / `app.core.errors` — **no new error codes**.
- **Access:** reads = member (`require_workspace`); writes (`POST`/`PUT`/`DELETE`/`ai-fill`) = editor (`_editor = require_role(MembershipRole.founder, MembershipRole.team_member)`, mentor → 403). `get_verified_user` on every route. Unknown `{kind}` → **404** (via `_parse_kind`, never 422). Unknown/cross-tenant record id → uniform **404**.
- **Enum convention:** all enum columns use `Enum(X, native_enum=False, length=N)` (VARCHAR-backed) — 22/22 existing columns do; NO native Postgres enum, NO `CREATE TYPE`.
- **FK index convention:** `startup_id` gets a **standalone index** in addition to any composite index (matches `assessment_answers`, `business_canvases`, etc.).
- **PUT is full-replace** of `data` (same semantics as Slice-1 canvases) — validated, whole `data` replaced.
- **No AI-attribution** in any commit message or artifact. **TDD**, real Postgres + per-test rollback.
- **Deferred (do NOT build):** real AI-generate (the `ai-fill` job stays unconsumed), "send to financial model" (Module 12), the competitor positioning-map builder (Slice 3), record reordering.

## File Structure

**Create:**
- `app/services/business/record_defs.py` — `RECORD_SCHEMAS`, the 4 Pydantic data models + `PricingTier`, `fields(kind)`.
- `app/services/business/records.py` — `validate`, `list/create/update/delete_record`, `serialize_record`, `_record`.
- `alembic/versions/0012_business_records.py`.
- Tests: `tests/services/business/test_records.py`, `tests/api/test_business_records.py`, and extend `e2e/test_business_builder.py`.
- Docs: SOP `docs/sop/2026-09-04-business-builder-records.md`; extend `docs/fe-integration-guide-business-builder.md`.

**Modify (additive):**
- `app/db/models/enums.py` — add `RecordKind`, `ThreatLevel`, `PricingModelType`.
- `app/db/models/business.py` — add `BusinessRecord` model (alongside `BusinessCanvas`).
- `app/db/models/__init__.py` — register `BusinessRecord`.
- `app/api/v1/endpoints/business.py` — add the 5 `{kind}` routes + `_parse_kind` (AFTER the existing `/overview` and `/canvases/...` routes).
- `app/services/business/service.py` — extend `overview()` with the 4 record rows.
- `tests/factories.py` — add `create_business_record`.
- `e2e/test_smoke.py`, `docs/checklist/PROJECT_CHECKLIST.md`.

---

## Task 1: enums + `business_records` model + migration `0012` + registry + factory

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/business.py`, `app/db/models/__init__.py`, `tests/factories.py`
- Create: `app/services/business/record_defs.py`, `alembic/versions/0012_business_records.py`
- Test: `tests/services/business/test_record_defs.py`

**Interfaces:**
- Produces: `RecordKind` (`persona`,`revenue_stream`,`competitor`,`pricing`), `ThreatLevel` (`low`,`medium`,`high`), `PricingModelType` (`subscription`,`one_time`,`usage`,`freemium`,`tiered`); `BusinessRecord` model; `RECORD_SCHEMAS: dict[RecordKind, type[BaseModel]]`; `fields(kind: RecordKind) -> list[dict]`; `create_business_record(db, *, startup, kind=RecordKind.persona, data=None, position=0) -> BusinessRecord`.

- [ ] **Step 1: Write the failing registry test** — `tests/services/business/test_record_defs.py`

```python
import pytest
from pydantic import ValidationError
from app.db.models.enums import RecordKind, ThreatLevel, PricingModelType
from app.services.business.record_defs import RECORD_SCHEMAS, fields


def test_every_kind_has_a_schema():
    assert set(RECORD_SCHEMAS) == set(RecordKind)


def test_persona_requires_name_defaults_the_rest():
    m = RECORD_SCHEMAS[RecordKind.persona](name="Busy Founder")
    assert m.name == "Busy Founder"
    assert m.goals == [] and m.quote == ""
    with pytest.raises(ValidationError):
        RECORD_SCHEMAS[RecordKind.persona]()  # name required


def test_competitor_threat_level_enum():
    m = RECORD_SCHEMAS[RecordKind.competitor](name="Acme", threat_level="high")
    assert m.threat_level == ThreatLevel.high
    with pytest.raises(ValidationError):
        RECORD_SCHEMAS[RecordKind.competitor](name="Acme", threat_level="apocalyptic")


def test_pricing_nested_tiers_validate():
    m = RECORD_SCHEMAS[RecordKind.pricing](
        model_type="tiered",
        tiers=[{"name": "Pro", "price": "$29", "features": ["A", "B"]}],
    )
    assert m.model_type == PricingModelType.tiered
    assert m.tiers[0].name == "Pro" and m.tiers[0].features == ["A", "B"]
    with pytest.raises(ValidationError):
        RECORD_SCHEMAS[RecordKind.pricing](model_type="not-a-model")


def test_fields_descriptor_lists_keys():
    keys = {f["key"] for f in fields(RecordKind.persona)}
    assert {"name", "goals", "quote"} <= keys
```

- [ ] **Step 2: Run to verify it fails** — `poetry run pytest tests/services/business/test_record_defs.py -v` → FAIL (module/enum missing).

- [ ] **Step 3: Add the enums** — append to `app/db/models/enums.py` (match the existing `class X(enum.StrEnum):` style — check an existing enum like `CanvasType`):

```python
class RecordKind(enum.StrEnum):
    persona = "persona"
    revenue_stream = "revenue_stream"
    competitor = "competitor"
    pricing = "pricing"


class ThreatLevel(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class PricingModelType(enum.StrEnum):
    subscription = "subscription"
    one_time = "one_time"
    usage = "usage"
    freemium = "freemium"
    tiered = "tiered"
```

- [ ] **Step 4: Create the registry** — `app/services/business/record_defs.py`

```python
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.models.enums import PricingModelType, RecordKind, ThreatLevel


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields -> ValidationError


class PersonaData(_Base):
    name: str
    demographics: str = ""
    goals: list[str] = []
    frustrations: list[str] = []
    watering_holes: list[str] = []
    quote: str = ""


class RevenueStreamData(_Base):
    name: str
    pricing_basis: str = ""
    est_monthly: float = 0
    assumptions: str = ""


class CompetitorData(_Base):
    name: str
    positioning: str = ""
    price: str = ""
    strengths: list[str] = []
    weaknesses: list[str] = []
    threat_level: ThreatLevel = ThreatLevel.medium


class PricingTier(_Base):
    name: str
    price: str = ""
    features: list[str] = []


class PricingData(_Base):
    model_type: PricingModelType
    tiers: list[PricingTier] = []


RECORD_SCHEMAS: dict[RecordKind, type[BaseModel]] = {
    RecordKind.persona: PersonaData,
    RecordKind.revenue_stream: RevenueStreamData,
    RecordKind.competitor: CompetitorData,
    RecordKind.pricing: PricingData,
}


def fields(kind: RecordKind) -> list[dict[str, Any]]:
    """FE field descriptors for a kind: key, required, type, enum choices."""
    out: list[dict[str, Any]] = []
    for key, info in RECORD_SCHEMAS[kind].model_fields.items():
        out.append(
            {
                "key": key,
                "required": info.is_required(),
                "type": str(info.annotation),
            }
        )
    return out
```

(Note the Pydantic-v2 `model_config = ConfigDict(extra="forbid")` — used so an unknown field is a 422, not silently dropped. Confirm the codebase is on Pydantic v2 by checking any existing schema in `app/schemas/`.)

- [ ] **Step 5: Add the model** — in `app/db/models/business.py`, alongside `BusinessCanvas` (mirror its column style, incl. the standalone `startup_id` index):

```python
class BusinessRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_records"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[RecordKind] = mapped_column(
        Enum(RecordKind, native_enum=False, length=20), nullable=False
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_business_records_startup_kind", "startup_id", "kind", "position"),
    )
```

Import whatever isn't already imported in `business.py` (`Integer`, `Index`, `Enum`, `JSONB`, `RecordKind`). Register in `app/db/models/__init__.py` (add `BusinessRecord` next to `BusinessCanvas`, same `# noqa: F401` style).

- [ ] **Step 6: Create the migration** — `alembic/versions/0012_business_records.py`. Prefer `poetry run alembic revision --autogenerate -m business_records` then hand-verify, OR copy `0011_business_canvases.py`'s structure. Set `revision = "0012_business_records"`, `down_revision = "0011_business_canvases"`. `upgrade()` creates `business_records` (columns per the model: `id` UUID PK, `startup_id` UUID NOT NULL FK→startups ON DELETE CASCADE, `kind` VARCHAR(20) NOT NULL, `data` JSONB NOT NULL, `position` INTEGER NOT NULL, `created_at`/`updated_at`), plus `op.create_index(op.f("ix_business_records_startup_id"), ...)` and `op.create_index("ix_business_records_startup_kind", "business_records", ["startup_id","kind","position"])`. `downgrade()` drops the two indexes then the table.

- [ ] **Step 7: Add the factory** — append to `tests/factories.py`:

```python
def create_business_record(
    db: Session,
    *,
    startup: Startup,
    kind: "RecordKind" = None,  # default set below to avoid import-time enum ref
    data: dict | None = None,
    position: int = 0,
) -> "BusinessRecord":
    from app.db.models.business import BusinessRecord
    from app.db.models.enums import RecordKind

    row = BusinessRecord(
        startup_id=startup.id,
        kind=kind or RecordKind.persona,
        data=data if data is not None else {"name": "Sample"},
        position=position,
    )
    db.add(row)
    db.flush()
    return row
```

(Match the real factory style in `tests/factories.py` — if it uses top-level imports, use those instead of the local import; check how `create_business_canvas` does it.)

- [ ] **Step 8: Run + migration round-trip** — `poetry run pytest tests/services/business/test_record_defs.py -v` (PASS); then `poetry run alembic upgrade head` (single head `0012_business_records`) → `poetry run alembic check` (No new upgrade operations) → `downgrade -1` → `upgrade head` clean.

- [ ] **Step 9: Gates + commit**

Run `poetry run black app tests && poetry run isort app tests && poetry run ruff check . && poetry run mypy app` — clean.
```bash
git add app/db/models/enums.py app/db/models/business.py app/db/models/__init__.py app/services/business/record_defs.py alembic/versions/0012_business_records.py tests/factories.py tests/services/business/test_record_defs.py
git commit -m "feat(business-builder): RecordKind + business_records model + migration 0012 + record schemas"
```

---

## Task 2: records service + overview extension

**Files:**
- Create: `app/services/business/records.py`
- Modify: `app/services/business/service.py` (`overview`)
- Test: `tests/services/business/test_records.py`

**Interfaces:**
- Consumes: `RECORD_SCHEMAS` (Task 1), `BusinessRecord`, `RecordKind`.
- Produces: `validate(kind, data) -> dict`; `list_records(db, startup, kind) -> list[BusinessRecord]`; `create_record(db, startup, kind, data) -> BusinessRecord`; `update_record(db, record, data) -> BusinessRecord`; `delete_record(db, record) -> None`; `serialize_record(record) -> dict`; `_record(db, membership, kind, record_id) -> BusinessRecord`. `overview` now appends 4 record rows.

- [ ] **Step 1: Write the failing tests** — `tests/services/business/test_records.py`

```python
import pytest
from app.core.errors import AppError
from app.db.models.enums import RecordKind
from app.services.business.records import (
    create_record, update_record, delete_record, list_records, validate, serialize_record,
)
from app.services.business.service import overview
from tests.factories import create_user, create_startup


def _startup(db):
    u = create_user(db)
    return create_startup(db, owner=u)


def test_validate_rejects_bad_data(db):
    with pytest.raises(AppError) as e:
        validate(RecordKind.persona, {"goals": "not-a-list"})  # missing name + wrong type
    assert e.value.code == "VALIDATION_ERROR"
    assert e.value.http_status == 422


def test_create_appends_position_and_lists_ordered(db):
    s = _startup(db)
    a = create_record(db, s, RecordKind.persona, {"name": "A"})
    b = create_record(db, s, RecordKind.persona, {"name": "B"})
    assert a.position == 0 and b.position == 1
    rows = list_records(db, s, RecordKind.persona)
    assert [r.data["name"] for r in rows] == ["A", "B"]


def test_update_full_replaces_data(db):
    s = _startup(db)
    r = create_record(db, s, RecordKind.persona, {"name": "A", "quote": "hi"})
    update_record(db, r, {"name": "A2"})
    assert r.data == {"name": "A2", "demographics": "", "goals": [], "frustrations": [],
                      "watering_holes": [], "quote": ""}  # quote reset -> full replace


def test_delete_removes(db):
    s = _startup(db)
    r = create_record(db, s, RecordKind.persona, {"name": "A"})
    delete_record(db, r)
    assert list_records(db, s, RecordKind.persona) == []


def test_overview_includes_record_rows(db):
    s = _startup(db)
    rows = {r["type"]: r for r in overview(db, s)}
    assert rows["persona"]["status"] == "start" and rows["persona"]["count"] == 0
    create_record(db, s, RecordKind.persona, {"name": "A"})
    rows = {r["type"]: r for r in overview(db, s)}
    assert rows["persona"]["status"] == "complete" and rows["persona"]["count"] == 1
    assert rows["persona"]["completion_pct"] == 100
```

- [ ] **Step 2: Run to verify fail** — `poetry run pytest tests/services/business/test_records.py -v` → FAIL.

- [ ] **Step 3: Implement `records.py`**

```python
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.services.business.record_defs import RECORD_SCHEMAS


def validate(kind: RecordKind, data: dict[str, Any]) -> dict[str, Any]:
    try:
        model = RECORD_SCHEMAS[kind](**data)
    except ValidationError as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Some fields are invalid.",
            422,
            field_errors=[
                {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ],
        ) from exc
    return model.model_dump(mode="json")


def list_records(db: Session, startup: Startup, kind: RecordKind) -> list[BusinessRecord]:
    return (
        db.query(BusinessRecord)
        .filter_by(startup_id=startup.id, kind=kind)
        .order_by(BusinessRecord.position.asc())
        .all()
    )


def create_record(db: Session, startup: Startup, kind: RecordKind, data: dict) -> BusinessRecord:
    clean = validate(kind, data)
    count = db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=kind).count()
    row = BusinessRecord(startup_id=startup.id, kind=kind, data=clean, position=count)
    db.add(row)
    db.flush()
    if count == 0:
        from app.platform.events import event_bus
        event_bus.publish(
            "business.artifact.completed",
            {"startup_id": str(startup.id), "artifact": kind.value},
        )
    return row


def update_record(db: Session, record: BusinessRecord, data: dict) -> BusinessRecord:
    record.data = validate(record.kind, data)
    db.flush()
    return record


def delete_record(db: Session, record: BusinessRecord) -> None:
    db.delete(record)
    db.flush()


def serialize_record(record: BusinessRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "kind": record.kind.value,
        "data": record.data,
        "position": record.position,
    }


def _record(
    db: Session, membership: Membership, kind: RecordKind, record_id: Any
) -> BusinessRecord:
    row = (
        db.query(BusinessRecord)
        .filter_by(id=record_id, startup_id=membership.startup_id, kind=kind)
        .first()
    )
    if row is None:
        raise NotFound()
    return row
```

(Confirm `event_bus.publish` import path against Slice-1's `save_canvas` — mirror exactly how it emits `business.artifact.completed`. Confirm `AppError`'s positional signature `(code, message, http_status, field_errors=...)` against `app/core/errors.py`.)

- [ ] **Step 4: Extend `overview`** in `app/services/business/service.py` — after the canvas-row loop, before `return rows`, append:

```python
from app.db.models.business import BusinessRecord  # add to imports
from app.db.models.enums import RecordKind          # add to imports

# ... inside overview(), after building canvas rows:
counts = dict(
    db.query(BusinessRecord.kind, func.count(BusinessRecord.id))  # func from sqlalchemy
    .filter_by(startup_id=startup.id)
    .group_by(BusinessRecord.kind)
    .all()
)
for kind in RecordKind:
    count = counts.get(kind, 0)
    complete = count >= 1
    rows.append(
        {
            "type": kind.value,
            "label": kind.value.replace("_", " ").title(),
            "status": "complete" if complete else "start",
            "completion_pct": 100 if complete else 0,
            "count": count,
        }
    )
```

Import `func` from `sqlalchemy` if not already. Keep the existing canvas rows untouched (they keep their `filled_blocks`/`total_blocks` shape).

- [ ] **Step 5: Run** — `poetry run pytest tests/services/business/test_records.py -v` (PASS), then `poetry run pytest -q` (full suite green; watch the Slice-1 overview tests — the new rows are additive, existing canvas rows unchanged).

- [ ] **Step 6: Gates + commit**

```bash
git add app/services/business/records.py app/services/business/service.py tests/services/business/test_records.py
git commit -m "feat(business-builder): records service (validate/CRUD) + overview record rows"
```

---

## Task 3: `GET /{kind}` (list) + `POST /{kind}` (create) + `_parse_kind`

**Files:**
- Modify: `app/api/v1/endpoints/business.py`, `app/schemas/business.py`
- Test: `tests/api/test_business_records.py`

**Interfaces:**
- Consumes: `list_records`, `create_record`, `serialize_record` (Task 2), `fields` (Task 1).
- Produces: `_parse_kind(kind: str) -> RecordKind`; `GET`/`POST /business-builder/{kind}`; schema `RecordCreate(data: dict)`.

- [ ] **Step 1: Write failing tests** — `tests/api/test_business_records.py`

Reuse the `_member(db, *, role=...)` harness from `tests/api/test_business_canvases.py` (copy it). Then:

```python
def test_list_empty_and_fields(client, founder):
    headers, _ = founder
    r = client.get("/api/v1/business-builder/personas", headers=headers)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["records"] == []
    assert any(f["key"] == "name" for f in body["fields"])


def test_create_returns_201(client, founder):
    headers, _ = founder
    r = client.post("/api/v1/business-builder/personas",
                    json={"data": {"name": "Busy Founder", "goals": ["ship"]}}, headers=headers)
    assert r.status_code == 201
    rec = r.json()["data"]
    assert rec["kind"] == "persona" and rec["data"]["name"] == "Busy Founder"


def test_create_bad_data_422(client, founder):
    headers, _ = founder
    r = client.post("/api/v1/business-builder/personas",
                    json={"data": {"goals": "not-a-list"}}, headers=headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_kind_404(client, founder):
    headers, _ = founder
    assert client.get("/api/v1/business-builder/gremlins", headers=headers).status_code == 404


def test_create_mentor_forbidden_403(client, db):
    from app.db.models.enums import MembershipRole
    from tests.api.test_business_canvases import _member  # or copy the helper
    _, _, headers = _member(db, role=MembershipRole.mentor)
    r = client.post("/api/v1/business-builder/personas", json={"data": {"name": "x"}}, headers=headers)
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify fail** — FAIL (routes missing).

- [ ] **Step 3: Add the schema** — `app/schemas/business.py`, alongside `CanvasSave`:

```python
class RecordCreate(BaseModel):
    data: dict = Field(default_factory=dict)
```

- [ ] **Step 4: Add `_parse_kind` + the two routes** — in `app/api/v1/endpoints/business.py`, **after** the existing `/overview` and `/canvases/...` routes (so the literal routes win). Add imports for `RecordKind`, `RecordCreate`, and the record service fns + `fields`.

```python
_KIND_PATHS = {
    "personas": RecordKind.persona,
    "revenue-streams": RecordKind.revenue_stream,
    "competitors": RecordKind.competitor,
    "pricing": RecordKind.pricing,
}


def _parse_kind(kind: str) -> RecordKind:
    try:
        return _KIND_PATHS[kind]
    except KeyError:
        raise NotFound() from None


@router.get("/{kind}")
def list_kind(
    kind: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rk = _parse_kind(kind)
    startup = _startup(db, membership)
    return success_response(
        {
            "records": [serialize_record(r) for r in list_records(db, startup, rk)],
            "fields": fields(rk),
        }
    )


@router.post("/{kind}", status_code=status.HTTP_201_CREATED)
def create_kind(
    kind: str,
    payload: RecordCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rk = _parse_kind(kind)
    startup = _startup(db, membership)
    record = create_record(db, startup, rk, payload.data)
    db.commit()
    return success_response(serialize_record(record))
```

**Ordering check:** confirm `GET /overview` and `GET /canvases/{type}` are declared earlier in the file than `GET /{kind}` — FastAPI matches in declaration order, so the literals must precede the `{kind}` catch-all. (Verify by running the smoke/overview test after adding.)

- [ ] **Step 5: Run** — `poetry run pytest tests/api/test_business_records.py -v` (PASS) + `poetry run pytest -q` (full suite green — especially confirm the Slice-1 `GET /overview` and `GET /canvases/business_model` tests still pass, proving no route shadowing).

- [ ] **Step 6: Gates + commit**

```bash
git add app/api/v1/endpoints/business.py app/schemas/business.py tests/api/test_business_records.py
git commit -m "feat(business-builder): GET/POST /{kind} record collection endpoints"
```

---

## Task 4: `PUT /{kind}/{id}` + `DELETE /{kind}/{id}`

**Files:**
- Modify: `app/api/v1/endpoints/business.py`
- Test: `tests/api/test_business_records.py`

**Interfaces:**
- Consumes: `_record`, `update_record`, `delete_record`, `serialize_record`, `_parse_kind`, `RecordCreate`.

- [ ] **Step 1: Write failing tests**

```python
def test_put_full_replaces(client, founder):
    headers, _ = founder
    rid = client.post("/api/v1/business-builder/personas",
                      json={"data": {"name": "A", "quote": "hi"}}, headers=headers).json()["data"]["id"]
    r = client.put(f"/api/v1/business-builder/personas/{rid}",
                   json={"data": {"name": "A2"}}, headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["data"]["name"] == "A2" and r.json()["data"]["data"]["quote"] == ""


def test_delete(client, founder):
    headers, _ = founder
    rid = client.post("/api/v1/business-builder/personas",
                      json={"data": {"name": "A"}}, headers=headers).json()["data"]["id"]
    assert client.delete(f"/api/v1/business-builder/personas/{rid}", headers=headers).status_code == 200
    assert client.get("/api/v1/business-builder/personas", headers=headers).json()["data"]["records"] == []


def test_put_cross_tenant_404(client, db, founder):
    import uuid
    headers, _ = founder
    r = client.put(f"/api/v1/business-builder/personas/{uuid.uuid4()}",
                   json={"data": {"name": "x"}}, headers=headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify fail** — FAIL.

- [ ] **Step 3: Add the routes** — in `business.py` (after the POST `/{kind}`):

```python
@router.put("/{kind}/{record_id}")
def update_kind(
    kind: str,
    record_id: uuid.UUID,
    payload: RecordCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rk = _parse_kind(kind)
    record = _record(db, membership, rk, record_id)
    update_record(db, record, payload.data)
    db.commit()
    return success_response(serialize_record(record))


@router.delete("/{kind}/{record_id}")
def delete_kind(
    kind: str,
    record_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rk = _parse_kind(kind)
    record = _record(db, membership, rk, record_id)
    delete_record(db, record)
    db.commit()
    return success_response({"deleted": True})
```

Add `import uuid` if not present.

- [ ] **Step 4: Run** — `poetry run pytest tests/api/test_business_records.py -v` (PASS) + `poetry run pytest -q`.

- [ ] **Step 5: Gates + commit**

```bash
git add app/api/v1/endpoints/business.py tests/api/test_business_records.py
git commit -m "feat(business-builder): PUT/DELETE /{kind}/{id} record endpoints"
```

---

## Task 5: `POST /{kind}/ai-fill` (deferred job)

**Files:**
- Modify: `app/api/v1/endpoints/business.py`
- Test: `tests/api/test_business_records.py`

**Interfaces:** Consumes `job_dispatcher.enqueue`, `_parse_kind`, `_startup`, `_editor` (mirror the Slice-1 `ai_fill_canvas` route exactly).

- [ ] **Step 1: Write failing tests**

```python
def test_ai_fill_enqueues_job_writes_no_record(client, db, founder):
    from app.db.models.job import Job
    from app.db.models.business import BusinessRecord
    headers, startup = founder
    r = client.post("/api/v1/business-builder/personas/ai-fill", headers=headers)
    assert r.status_code == 202 and r.json()["data"]["status"] == "queued"
    job = db.query(Job).filter(Job.type == "business.persona.ai_fill").one()
    assert job.payload["kind"] == "persona" and job.payload["startup_id"] == str(startup.id)
    assert db.query(BusinessRecord).filter_by(startup_id=startup.id).count() == 0


def test_ai_fill_mentor_forbidden_403(client, db):
    from app.db.models.enums import MembershipRole
    from tests.api.test_business_canvases import _member
    _, _, headers = _member(db, role=MembershipRole.mentor)
    assert client.post("/api/v1/business-builder/personas/ai-fill", headers=headers).status_code == 403
```

(Confirm `Job.type`/`Job.payload` field names + how `founder` fixture exposes the startup — align to the Task-3 harness.)

- [ ] **Step 2: Run to verify fail** — FAIL.

- [ ] **Step 3: Add the route** — after the POST `/{kind}` route, BEFORE it can be shadowed by `/{kind}/{record_id}` (a literal `/ai-fill` segment; FastAPI matches the more specific literal, but declare `ai-fill` before the `{record_id}` PUT/DELETE to be safe — verify with the test):

```python
@router.post("/{kind}/ai-fill", status_code=status.HTTP_202_ACCEPTED)
def ai_fill_kind(
    kind: str,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rk = _parse_kind(kind)
    startup = _startup(db, membership)
    job = job_dispatcher.enqueue(
        db,
        type=f"business.{rk.value}.ai_fill",
        payload={"startup_id": str(startup.id), "kind": rk.value},
        startup_id=startup.id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})
```

**Routing note:** `POST /personas/ai-fill` (literal `ai-fill`) vs `PUT/DELETE /{kind}/{record_id}` — these are different methods, so no collision. But `GET /{kind}` won't match `/personas/ai-fill` (two segments). The one risk is if a `POST /{kind}/{record_id}` existed — it doesn't (POST is only `/{kind}` and `/{kind}/ai-fill`). The test `test_ai_fill_enqueues_job_writes_no_record` proves the route resolves to ai-fill, not create.

- [ ] **Step 4: Run** — `poetry run pytest tests/api/test_business_records.py -v` (all PASS) + `poetry run pytest -q`.

- [ ] **Step 5: Gates + commit**

```bash
git add app/api/v1/endpoints/business.py tests/api/test_business_records.py
git commit -m "feat(business-builder): POST /{kind}/ai-fill — deferred job enqueue"
```

---

## Task 6: Live E2E + smoke + SOP + FE guide + checklist reconcile

**Files:**
- Modify: `e2e/test_business_builder.py`, `e2e/test_smoke.py`, `docs/fe-integration-guide-business-builder.md`, `docs/checklist/PROJECT_CHECKLIST.md`
- Create: `docs/sop/2026-09-04-business-builder-records.md`
- Captures: `e2e/_captures/business/` (records set)

- [ ] **Step 1: Extend the E2E journey** — in `e2e/test_business_builder.py`, add a records journey (reuse the same fixtures + `capture` helper): create a persona (`POST /personas`) → list (`GET /personas`, ordered, with `fields`) → update it (`PUT /personas/{id}`) → `GET /overview` (assert `persona` row is `complete`, `count` 1) → create a competitor (`POST /competitors` with `threat_level`) and a pricing record (`POST /pricing` with `model_type` + `tiers`) → `POST /personas/ai-fill` (202 queued) → `GET /jobs/{id}` (queued). Capture each body to `e2e/_captures/business/` (e.g. `record_create.json`, `record_list.json`, `record_update.json`, `overview_records.json`, `pricing_create.json`, `record_ai_fill.json`).

- [ ] **Step 2: Smoke** — extend `e2e/test_smoke.py` asserted-paths with `/api/v1/business-builder/{kind}` and `/api/v1/business-builder/{kind}/{record_id}` and `/api/v1/business-builder/{kind}/ai-fill` (match the registered placeholder names — `{kind}`, `{record_id}`).

- [ ] **Step 3: Run E2E + unit** — `COMPOSE_PROJECT_NAME=cofoundaz-api make e2e` (Docker up; migrates `cofoundaz_e2e` through `0012`) — report pass count; `poetry run pytest -q` green. Commit only the business captures + the 2 e2e files (restore other modules' regenerated captures).

- [ ] **Step 4: SOP** — `docs/sop/2026-09-04-business-builder-records.md`, matching `docs/sop/2026-09-01-business-builder-canvas.md`: what shipped (+ commit range), why (Slice 2 typed artifacts on the generic-records pattern; AI deferred), how (generic table + per-kind Pydantic registry, uniform `{kind}` CRUD, overview integration), files/migration `0012`/the 5 routes, verification (real unit + e2e counts, gates), rollback (`alembic downgrade -1`), follow-ups (Slice 3 suggestions + plan + positioning-map, real ai-fill worker, Module-12 revenue sync, reordering).

- [ ] **Step 5: FE guide** — extend `docs/fe-integration-guide-business-builder.md` with a Slice-2 section: **every payload copied verbatim from `e2e/_captures/business/`**; the per-kind `fields` contract (FE renders forms from it); the CRUD contract per kind; the **full-replace PUT** warning (same as Slice-1 canvases — send the complete `data`); the overview's new record rows (`count`, shared `status`/`completion_pct`); the ai-fill deferral (202 → poll `GET /jobs/{id}`, stays `queued`). Error shapes (422/404/403) labeled derived where not captured. Verification table.

- [ ] **Step 6: Checklist** — in `docs/checklist/PROJECT_CHECKLIST.md`, check off Module 08 Slice 2, mark it shipped on branch `feat/business-builder-records` (not yet merged), update the snapshot tally + `_Last reconciled_` line honestly.

- [ ] **Step 7: Final gates + commit**

`poetry run pytest -q && poetry run black --check app tests && poetry run isort --check-only app tests && poetry run ruff check . && poetry run mypy app` — all green (state the numbers). `make e2e` verified in Step 3.
```bash
git add e2e/test_business_builder.py e2e/test_smoke.py e2e/_captures/business/ docs/sop/2026-09-04-business-builder-records.md docs/fe-integration-guide-business-builder.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(business-builder): Slice 2 live E2E + smoke + SOP + FE guide + checklist reconcile"
```

---

## Self-Review notes

- **Spec coverage:** §3 model → Task 1; §4 registry → Task 1; §5 service + overview → Task 2; §6 endpoints → Tasks 3–5; §7 errors/events → Tasks 2 (event) & 3 (422/404); §8 testing → every task + Task 6 e2e/FE guide; §9 plan shape → these 6 tasks.
- **Names/types consistent:** `RecordKind`/`ThreatLevel`/`PricingModelType`, `BusinessRecord`, `RECORD_SCHEMAS`/`fields`, `validate`/`list_records`/`create_record`/`update_record`/`delete_record`/`serialize_record`/`_record`, `_parse_kind`/`_KIND_PATHS`, `RecordCreate`, migration `0012_business_records` ← `0011_business_canvases`, event `business.artifact.completed`, job `business.{kind}.ai_fill`.
- **Routing risk called out (Tasks 3 & 5):** the `/{kind}` catch-all must be declared AFTER the literal `/overview` and `/canvases/...` routes; the Slice-1 overview/canvas tests re-run in Task 3 Step 5 prove no shadowing. `_parse_kind` → 404 backstops any unknown segment.
- **Verify-before-code flags for implementers:** Pydantic v2 (`model_config`/`model_fields`/`model_dump`) — confirm against existing schemas; `event_bus.publish` import + `business.artifact.completed` payload — mirror Slice-1 `save_canvas`; `AppError(code, message, http_status, field_errors=)` positional signature; `Job.type`/`Job.payload` field names; the `_member` harness location in `tests/api/test_business_canvases.py`.
- **The only edits to Slice-1 code** are additive: the `overview()` extension (Task 2) and new routes appended to `business.py` (Tasks 3–5). Full suite re-run at Tasks 2, 3, 5, 6 guards the Slice-1 behavior.
