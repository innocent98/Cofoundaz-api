# Documents & Templates — Slice 1 (Document Library Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a structured document store — section-based `documents` with CRUD, filters, optimistic-concurrency editing, and an in-code template registry — as the dependency-free first slice of Module 18.

**Architecture:** A document is `title + ordered markdown sections`, sections stored as a JSONB array on the row with a `version` counter for full-replace optimistic concurrency (the canvas pattern). Templates are a read-only in-code registry that instantiates a document's sections. Read = any workspace member; write = editor.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL (JSONB), Pydantic v2, pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-09-09-documents-templates-design.md`

## Global Constraints

- **Enum columns:** `enum.StrEnum` in `app/db/models/enums.py`; column `Enum(X, native_enum=False, length=20)` — VARCHAR-backed, no Postgres `CREATE TYPE`.
- **FK index convention:** every FK column gets a standalone `index=True` in addition to any composite index.
- **`get_db()` does not auto-commit:** every write endpoint MUST call `db.commit()`.
- **Response envelope:** endpoints return `success_response(data)`; errors raise `AppError` subclasses.
- **Tenancy:** cross-tenant / missing → uniform **404** (`NotFound`). `require_workspace` = any member; `_editor = require_role(MembershipRole.founder, MembershipRole.team_member)`.
- **Path ids typed `uuid.UUID`** so a malformed id 422s at the framework before the service (Slice 3 lesson).
- **Migrations:** produced by `alembic revision --autogenerate` against the ORM model; single head; `alembic check` zero drift before push.
- **No AI attribution** in any commit message or PR/issue/review body — clean commits authored by Adebayo only. Binds every subagent.
- **CI green locally before push:** `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app`, `poetry run pytest`, then `scripts/e2e_run.sh` — all via `poetry run` (pinned toolchain).

---

### Task 1: Schema — enums, `Document` model, migration

**Files:**
- Modify: `app/db/models/enums.py` (add `DocumentKind`, `DocumentStatus`)
- Create: `app/db/models/document.py` (`Document`)
- Modify: `app/db/models/__init__.py` (register `Document`)
- Create: `alembic/versions/00NN_documents.py`
- Test: `tests/db/test_document_models.py`

**Interfaces:**
- Produces: `DocumentKind` (`business_plan`/`pitch_deck`/`financial_model`/`meeting_notes`/`one_pager`/`custom`), `DocumentStatus` (`draft`/`final`); ORM `Document` (cols: `id, created_at, updated_at, startup_id, created_by_id, kind, status, ai_generated, folder, template_key, title, sections, version`).

**Migration numbering — settle against the live head:** before writing the migration, run
`git fetch origin && poetry run alembic heads`. Chain onto the current single head: if Slice-3
PR #47 has merged (head `0015_business_positioning_maps`), use `0016_documents`; otherwise use
the next number after whatever head exists. `poetry run alembic heads` MUST show exactly one head
after.

- [ ] **Step 1: Add the two enums**

Append to `app/db/models/enums.py`:

```python
class DocumentKind(enum.StrEnum):
    business_plan = "business_plan"
    pitch_deck = "pitch_deck"
    financial_model = "financial_model"
    meeting_notes = "meeting_notes"
    one_pager = "one_pager"
    custom = "custom"


class DocumentStatus(enum.StrEnum):
    draft = "draft"
    final = "final"
```

- [ ] **Step 2: Create the model**

`app/db/models/document.py`:

```python
import uuid
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import DocumentKind, DocumentStatus


class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[DocumentKind] = mapped_column(
        Enum(DocumentKind, native_enum=False, length=20),
        nullable=False,
        server_default=DocumentKind.custom.value,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=20),
        nullable=False,
        server_default=DocumentStatus.draft.value,
    )
    ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    folder: Mapped[str | None] = mapped_column(String(120), nullable=True)
    template_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    sections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (Index("ix_documents_startup_kind", "startup_id", "kind"),)
```

- [ ] **Step 3: Register the model**

In `app/db/models/__init__.py`, add (keep alphabetical grouping near the other imports):

```python
from app.db.models.document import Document  # noqa: F401
```

- [ ] **Step 4: Autogenerate + number the migration**

```bash
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "documents"
```

Rename the generated file per the numbering rule above; set `revision`/`down_revision` to chain
onto the current head. Confirm `upgrade()` created the table with `ix_documents_startup_id`
(from `index=True`), `ix_documents_created_by_id`, and the composite `ix_documents_startup_kind`;
`server_default`s present on `kind`/`status`/`ai_generated`/`title`/`sections`/`version`; FK
ondelete CASCADE (startup) / SET NULL (user). Add a short module docstring (mirror
`0012_business_records.py`).

- [ ] **Step 5: Verify single head + zero drift**

```bash
poetry run alembic heads          # exactly one head
poetry run alembic upgrade head   # applies clean
poetry run alembic check          # "No new upgrade operations detected."
```

- [ ] **Step 6: Write the model round-trip test**

```python
# tests/db/test_document_models.py
from app.db.models.document import Document
from app.db.models.enums import DocumentKind, DocumentStatus
from tests.factories import create_startup, create_user


def test_document_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    doc = Document(startup_id=s.id, created_by_id=u.id, title="Plan")
    db.add(doc)
    db.flush()
    db.refresh(doc)
    assert doc.kind == DocumentKind.custom
    assert doc.status == DocumentStatus.draft
    assert doc.ai_generated is False
    assert doc.sections == []
    assert doc.version == 1


def test_document_stores_sections(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    doc = Document(
        startup_id=s.id, created_by_id=u.id, title="Plan",
        sections=[{"id": "s1", "heading": "Problem", "body": "text"}],
    )
    db.add(doc)
    db.flush()
    db.refresh(doc)
    assert doc.sections[0]["heading"] == "Problem"
```

- [ ] **Step 7: Run the model test**

Run: `poetry run pytest tests/db/test_document_models.py -v`
Expected: PASS (both).

- [ ] **Step 8: Commit**

```bash
git add app/db/models/enums.py app/db/models/document.py app/db/models/__init__.py alembic/versions/ tests/db/test_document_models.py
git commit -m "feat(documents): Slice 1 schema — documents table + enums"
```

---

### Task 2: Template registry (`template_defs.py`)

**Files:**
- Create: `app/services/documents/__init__.py` (empty package marker)
- Create: `app/services/documents/template_defs.py`
- Test: `tests/services/documents/__init__.py` (empty), `tests/services/documents/test_template_defs.py`

**Interfaces:**
- Consumes: `DocumentKind` (Task 1).
- Produces:
  - `DocumentTemplate` (frozen dataclass: `key: str`, `name: str`, `description: str`, `kind: DocumentKind`, `sections: tuple[str, ...]`)
  - `DOCUMENT_TEMPLATES: dict[str, DocumentTemplate]`
  - `instantiate(template_key: str) -> tuple[DocumentKind, str, list[dict]]` — returns `(kind, default_title, sections)` where sections = `[{"id": <uuid4 str>, "heading": h, "body": ""} …]`; unknown key → `NotFound`
  - `catalog() -> list[dict]` — `[{key, name, description, kind, sections:[headings]}]`
  - `template_view(template_key: str) -> dict` — one catalog entry; unknown key → `NotFound`

- [ ] **Step 1: Write failing tests**

```python
# tests/services/documents/test_template_defs.py
import pytest

from app.core.errors import NotFound
from app.db.models.enums import DocumentKind
from app.services.documents.template_defs import (
    DOCUMENT_TEMPLATES,
    catalog,
    instantiate,
    template_view,
)


def test_registry_has_the_five_templates():
    assert set(DOCUMENT_TEMPLATES) == {
        "business_plan", "pitch_deck", "financial_model", "meeting_notes", "one_pager",
    }


def test_instantiate_seeds_one_section_per_heading_with_ids():
    kind, title, sections = instantiate("business_plan")
    assert kind == DocumentKind.business_plan
    assert title == "Business Plan"
    headings = [s["heading"] for s in sections]
    assert headings[0] == "Executive Summary" and "The Ask" in headings
    assert all(s["body"] == "" and s["id"] for s in sections)
    assert len({s["id"] for s in sections}) == len(sections)  # unique ids


def test_instantiate_unknown_key_404():
    with pytest.raises(NotFound):
        instantiate("nope")


def test_catalog_shape_and_template_view():
    cat = {t["key"]: t for t in catalog()}
    assert cat["pitch_deck"]["name"] == "Pitch Deck"
    assert isinstance(cat["pitch_deck"]["sections"], list)  # headings only
    assert template_view("one_pager")["kind"] == "one_pager"
    with pytest.raises(NotFound):
        template_view("nope")
```

- [ ] **Step 2: Run to confirm failure**

Run: `poetry run pytest tests/services/documents/test_template_defs.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the registry**

`app/services/documents/template_defs.py`:

```python
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.errors import NotFound
from app.db.models.enums import DocumentKind


@dataclass(frozen=True)
class DocumentTemplate:
    key: str
    name: str
    description: str
    kind: DocumentKind
    sections: tuple[str, ...]


def _t(key: str, name: str, description: str, kind: DocumentKind, *sections: str) -> DocumentTemplate:
    return DocumentTemplate(key=key, name=name, description=description, kind=kind, sections=sections)


DOCUMENT_TEMPLATES: dict[str, DocumentTemplate] = {
    t.key: t
    for t in (
        _t(
            "business_plan", "Business Plan", "A full business plan.",
            DocumentKind.business_plan,
            "Executive Summary", "Problem", "Solution", "Market & Customer",
            "Business Model", "Go-to-Market", "Team", "Financials", "The Ask",
        ),
        _t(
            "pitch_deck", "Pitch Deck", "An investor pitch narrative.",
            DocumentKind.pitch_deck,
            "Hook", "Problem", "Solution", "Why Now", "Market", "Product", "Team", "The Ask",
        ),
        _t(
            "financial_model", "Financial Model", "A lightweight financial narrative.",
            DocumentKind.financial_model,
            "Assumptions", "Revenue", "Costs", "Runway", "Projections",
        ),
        _t(
            "meeting_notes", "Meeting Notes", "Structured meeting notes.",
            DocumentKind.meeting_notes,
            "Attendees", "Agenda", "Discussion", "Decisions", "Action Items",
        ),
        _t(
            "one_pager", "One-Pager", "A concise one-page overview.",
            DocumentKind.one_pager,
            "Overview", "Problem", "Solution", "Traction", "The Ask",
        ),
    )
}


def _get(template_key: str) -> DocumentTemplate:
    tmpl = DOCUMENT_TEMPLATES.get(template_key)
    if tmpl is None:
        raise NotFound()
    return tmpl


def instantiate(template_key: str) -> tuple[DocumentKind, str, list[dict[str, Any]]]:
    tmpl = _get(template_key)
    sections = [{"id": str(uuid.uuid4()), "heading": h, "body": ""} for h in tmpl.sections]
    return tmpl.kind, tmpl.name, sections


def _view(tmpl: DocumentTemplate) -> dict[str, Any]:
    return {
        "key": tmpl.key,
        "name": tmpl.name,
        "description": tmpl.description,
        "kind": tmpl.kind.value,
        "sections": list(tmpl.sections),
    }


def catalog() -> list[dict[str, Any]]:
    return [_view(t) for t in DOCUMENT_TEMPLATES.values()]


def template_view(template_key: str) -> dict[str, Any]:
    return _view(_get(template_key))
```

- [ ] **Step 4: Run to confirm pass**

Run: `poetry run pytest tests/services/documents/test_template_defs.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add app/services/documents/__init__.py app/services/documents/template_defs.py tests/services/documents/
git commit -m "feat(documents): in-code template registry + instantiate"
```

---

### Task 3: Document service + error

**Files:**
- Modify: `app/core/errors.py` (add `DocumentVersionConflict`)
- Create: `app/services/documents/service.py`
- Test: `tests/services/documents/test_service.py`

**Interfaces:**
- Consumes: `Document` (Task 1), `instantiate` (Task 2), `event_bus`, `Membership`, `Startup`, `DocumentKind`, `DocumentStatus`, `NotFound`, `AppError`.
- Produces:
  - `DocumentVersionConflict` (AppError, 409, `DOCUMENT_VERSION_CONFLICT`)
  - `validate_sections(sections: list) -> list[dict]`
  - `create_document(db, startup, *, created_by_id, kind, title, sections, folder, template_key, ai_generated=False) -> Document`
  - `list_documents(db, startup, *, kind, folder, status) -> list[Document]`
  - `get_document(db, membership, document_id) -> Document`
  - `update_document(db, doc, *, title, sections, status, folder, expected_version) -> Document`
  - `delete_document(db, doc) -> None`
  - `serialize_document(doc) -> dict`, `serialize_summary(doc) -> dict`

- [ ] **Step 1: Add the error class**

In `app/core/errors.py`, after `CanvasVersionConflict`:

```python
class DocumentVersionConflict(AppError):  # noqa: N818
    code, http_status = "DOCUMENT_VERSION_CONFLICT", 409
    message = "This document was changed elsewhere. Reload and reapply your edits."
```

- [ ] **Step 2: Write failing service tests**

```python
# tests/services/documents/test_service.py
import uuid

import pytest

from app.core.errors import AppError, DocumentVersionConflict, NotFound
from app.db.models.enums import DocumentKind, DocumentStatus
from app.db.models.membership import Membership
from app.services.documents.service import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    serialize_document,
    serialize_summary,
    update_document,
    validate_sections,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s)
    db.flush()
    return u, s, m


def _new(db, s, u, **kw):
    kw.setdefault("kind", DocumentKind.custom)
    kw.setdefault("title", "Doc")
    kw.setdefault("sections", [])
    kw.setdefault("folder", None)
    kw.setdefault("template_key", None)
    return create_document(db, s, created_by_id=u.id, **kw)


def test_validate_sections_assigns_ids_and_rejects_bad_shape(db):
    clean = validate_sections([{"heading": "H", "body": "B"}])
    assert clean[0]["heading"] == "H" and clean[0]["id"]
    with pytest.raises(AppError) as e:
        validate_sections([{"heading": 1, "body": "B"}])
    assert e.value.http_status == 422
    with pytest.raises(AppError) as e2:
        validate_sections(["not-an-object"])
    assert e2.value.http_status == 422


def test_create_blank(db):
    u, s, _m = _ctx(db)
    doc = _new(db, s, u, title="Blank")
    assert doc.title == "Blank" and doc.kind == DocumentKind.custom
    assert doc.ai_generated is False and doc.version == 1


def test_create_from_template_key_seeds_sections(db):
    u, s, _m = _ctx(db)
    from app.services.documents.template_defs import instantiate

    kind, title, sections = instantiate("business_plan")
    doc = _new(db, s, u, kind=kind, title=title, sections=sections, template_key="business_plan")
    assert doc.kind == DocumentKind.business_plan
    assert [x["heading"] for x in doc.sections][0] == "Executive Summary"


def test_create_seam_ai_generated(db):
    u, s, _m = _ctx(db)
    doc = _new(db, s, u, kind=DocumentKind.business_plan, ai_generated=True)
    assert doc.ai_generated is True


def test_list_filters_and_summary_has_no_sections(db):
    u, s, _m = _ctx(db)
    _new(db, s, u, kind=DocumentKind.one_pager, folder="A",
         sections=[{"heading": "H", "body": "B"}])
    _new(db, s, u, kind=DocumentKind.pitch_deck, folder="B")
    assert len(list_documents(db, s, kind=DocumentKind.one_pager, folder=None, status=None)) == 1
    assert len(list_documents(db, s, kind=None, folder="B", status=None)) == 1
    assert "sections" not in serialize_summary(
        list_documents(db, s, kind=DocumentKind.one_pager, folder=None, status=None)[0]
    )


def test_get_cross_tenant_404(db):
    u, s, m = _ctx(db)
    with pytest.raises(NotFound):
        get_document(db, m, uuid.uuid4())


def test_update_full_replace_and_version_bump(db):
    u, s, m = _ctx(db)
    doc = _new(db, s, u, title="v1", sections=[{"heading": "Old", "body": "x"}])
    updated = update_document(
        db, doc, title="v2", sections=[{"heading": "New", "body": "y"}],
        status=DocumentStatus.final, folder="F", expected_version=1,
    )
    assert updated.title == "v2" and updated.version == 2
    assert updated.status == DocumentStatus.final
    assert [x["heading"] for x in updated.sections] == ["New"]


def test_update_stale_version_409(db):
    u, s, m = _ctx(db)
    doc = _new(db, s, u)
    with pytest.raises(DocumentVersionConflict):
        update_document(db, doc, title="x", sections=[], status=DocumentStatus.draft,
                        folder=None, expected_version=99)


def test_delete_removes(db):
    u, s, m = _ctx(db)
    doc = _new(db, s, u)
    delete_document(db, doc)
    with pytest.raises(NotFound):
        get_document(db, m, doc.id)


def test_serialize_document_has_sections(db):
    u, s, _m = _ctx(db)
    doc = _new(db, s, u, sections=[{"heading": "H", "body": "B"}])
    out = serialize_document(doc)
    assert out["sections"][0]["heading"] == "H" and out["version"] == 1


def test_create_publishes_event_once(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.documents.service.event_bus.publish",
        lambda e, p: events.append((e, p)),
    )
    u, s, _m = _ctx(db)
    doc = _new(db, s, u)
    assert events == [
        ("document.created", {"startup_id": str(s.id), "document_id": str(doc.id),
                              "kind": doc.kind.value})
    ]
```

- [ ] **Step 3: Run to confirm failure**

Run: `poetry run pytest tests/services/documents/test_service.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.documents.service`).

- [ ] **Step 4: Implement the service**

`app/services/documents/service.py`:

```python
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, DocumentVersionConflict, NotFound
from app.db.models.document import Document
from app.db.models.enums import DocumentKind, DocumentStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus


def validate_sections(sections: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            raise AppError(
                "VALIDATION_ERROR", "Each section must be an object.", 422,
                field_errors=[{"field": f"sections.{i}", "message": "Expected an object."}],
            )
        heading, body = sec.get("heading"), sec.get("body")
        if not isinstance(heading, str) or not isinstance(body, str):
            raise AppError(
                "VALIDATION_ERROR", "Each section needs text heading and body.", 422,
                field_errors=[{"field": f"sections.{i}", "message": "heading/body must be text."}],
            )
        sid = sec.get("id")
        out.append({"id": str(sid) if sid else str(uuid.uuid4()), "heading": heading, "body": body})
    return out


def create_document(
    db: Session,
    startup: Startup,
    *,
    created_by_id: uuid.UUID,
    kind: DocumentKind,
    title: str,
    sections: list[Any],
    folder: str | None,
    template_key: str | None,
    ai_generated: bool = False,
) -> Document:
    doc = Document(
        startup_id=startup.id,
        created_by_id=created_by_id,
        kind=kind,
        title=title,
        sections=validate_sections(sections),
        folder=folder,
        template_key=template_key,
        ai_generated=ai_generated,
    )
    db.add(doc)
    db.flush()
    event_bus.publish(
        "document.created",
        {"startup_id": str(startup.id), "document_id": str(doc.id), "kind": doc.kind.value},
    )
    return doc


def list_documents(
    db: Session,
    startup: Startup,
    *,
    kind: DocumentKind | None,
    folder: str | None,
    status: DocumentStatus | None,
) -> list[Document]:
    q = db.query(Document).filter_by(startup_id=startup.id)
    if kind is not None:
        q = q.filter_by(kind=kind)
    if folder is not None:
        q = q.filter_by(folder=folder)
    if status is not None:
        q = q.filter_by(status=status)
    return q.order_by(Document.updated_at.desc()).all()


def get_document(db: Session, membership: Membership, document_id: Any) -> Document:
    doc = (
        db.query(Document)
        .filter_by(id=document_id, startup_id=membership.startup_id)
        .first()
    )
    if doc is None:
        raise NotFound()
    return doc


def update_document(
    db: Session,
    doc: Document,
    *,
    title: str,
    sections: list[Any],
    status: DocumentStatus,
    folder: str | None,
    expected_version: int,
) -> Document:
    if expected_version != doc.version:
        raise DocumentVersionConflict()
    doc.title = title
    doc.sections = validate_sections(sections)
    doc.status = status
    doc.folder = folder
    doc.version += 1
    db.flush()
    return doc


def delete_document(db: Session, doc: Document) -> None:
    db.delete(doc)
    db.flush()


def serialize_summary(doc: Document) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "kind": doc.kind.value,
        "title": doc.title,
        "status": doc.status.value,
        "ai_generated": doc.ai_generated,
        "folder": doc.folder,
        "template_key": doc.template_key,
        "version": doc.version,
        "updated_at": doc.updated_at.isoformat(),
    }


def serialize_document(doc: Document) -> dict[str, Any]:
    return {**serialize_summary(doc), "sections": doc.sections}
```

- [ ] **Step 5: Run to confirm pass**

Run: `poetry run pytest tests/services/documents/test_service.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/core/errors.py app/services/documents/service.py tests/services/documents/test_service.py
git commit -m "feat(documents): document service (CRUD + validate + serialize) + 409 error"
```

---

### Task 4: Schemas, endpoints, router registration

**Files:**
- Create: `app/schemas/document.py`
- Create: `app/api/v1/endpoints/documents.py`
- Modify: `app/api/v1/api.py` (register router)
- Test: `tests/api/test_documents.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3; `require_workspace`, `require_role`, `get_verified_user`, `get_db`, `success_response`, `NotFound`.
- Produces: routes `GET/POST /documents`, `GET/PUT/DELETE /documents/{id}`, `GET /document-templates`, `GET /document-templates/{key}`.

- [ ] **Step 1: Request schemas**

`app/schemas/document.py`:

```python
from pydantic import BaseModel, Field

from app.db.models.enums import DocumentKind, DocumentStatus


class DocumentCreate(BaseModel):
    kind: DocumentKind | None = None
    title: str | None = None
    folder: str | None = None
    template_key: str | None = None
    sections: list[dict] | None = None


class DocumentSave(BaseModel):
    title: str = ""
    sections: list[dict] = Field(default_factory=list)
    status: DocumentStatus = DocumentStatus.draft
    folder: str | None = None
    version: int
```

- [ ] **Step 2: Write failing API tests**

```python
# tests/api/test_documents.py
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


def test_list_empty(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/documents", headers=h)
    assert r.status_code == 200 and r.json()["data"]["documents"] == []


def test_templates_catalog(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/document-templates", headers=h)
    assert r.status_code == 200
    keys = {t["key"] for t in r.json()["data"]["templates"]}
    assert "business_plan" in keys and "one_pager" in keys
    assert client.get("/api/v1/document-templates/nope", headers=h).status_code == 404


def test_create_from_template(client, db):
    _u, _s, h = _member(db)
    r = client.post("/api/v1/documents", json={"template_key": "business_plan"}, headers=h)
    assert r.status_code == 201
    doc = r.json()["data"]
    assert doc["kind"] == "business_plan" and doc["title"] == "Business Plan"
    assert doc["sections"][0]["heading"] == "Executive Summary" and doc["version"] == 1


def test_create_blank_and_bad_sections_422(client, db):
    _u, _s, h = _member(db)
    ok = client.post("/api/v1/documents", json={"title": "Blank"}, headers=h)
    assert ok.status_code == 201 and ok.json()["data"]["kind"] == "custom"
    bad = client.post(
        "/api/v1/documents",
        json={"sections": [{"heading": 1, "body": "x"}]}, headers=h,
    )
    assert bad.status_code == 422


def test_get_edit_version_conflict_delete(client, db):
    _u, _s, h = _member(db)
    doc_id = client.post(
        "/api/v1/documents", json={"template_key": "one_pager"}, headers=h
    ).json()["data"]["id"]

    # full-replace edit
    edit = client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "Edited", "sections": [{"heading": "H", "body": "B"}],
              "status": "final", "folder": "Pitches", "version": 1},
        headers=h,
    )
    assert edit.status_code == 200 and edit.json()["data"]["version"] == 2
    assert edit.json()["data"]["status"] == "final"

    # stale version -> 409
    stale = client.put(
        f"/api/v1/documents/{doc_id}",
        json={"title": "x", "sections": [], "status": "draft", "version": 1},
        headers=h,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "DOCUMENT_VERSION_CONFLICT"

    # filter by folder
    lst = client.get("/api/v1/documents?folder=Pitches", headers=h)
    assert len(lst.json()["data"]["documents"]) == 1
    assert "sections" not in lst.json()["data"]["documents"][0]  # summary only

    assert client.delete(f"/api/v1/documents/{doc_id}", headers=h).status_code == 200
    assert client.get(f"/api/v1/documents/{doc_id}", headers=h).status_code == 404


def test_bad_kind_filter_404(client, db):
    _u, _s, h = _member(db)
    assert client.get("/api/v1/documents?kind=bogus", headers=h).status_code == 404


def test_mentor_cannot_write_403(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor)
    assert client.post("/api/v1/documents", json={"title": "x"}, headers=h).status_code == 403
    # but a member can read
    assert client.get("/api/v1/documents", headers=h).status_code == 200


def test_cross_tenant_get_404(client, db):
    import uuid

    _u, _s, h = _member(db)
    assert client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=h).status_code == 404
```

- [ ] **Step 3: Run to confirm failure**

Run: `poetry run pytest tests/api/test_documents.py -v`
Expected: FAIL (routes missing / 404).

- [ ] **Step 4: Implement the router**

`app/api/v1/endpoints/documents.py`:

```python
import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import DocumentKind, DocumentStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.schemas.document import DocumentCreate, DocumentSave
from app.services.documents.service import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    serialize_document,
    serialize_summary,
    update_document,
    validate_sections,
)
from app.services.documents.template_defs import catalog, instantiate, template_view

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


def _parse_kind(kind: str | None) -> DocumentKind | None:
    if kind is None:
        return None
    try:
        return DocumentKind(kind)
    except ValueError:
        raise NotFound() from None


def _parse_status(value: str | None) -> DocumentStatus | None:
    if value is None:
        return None
    try:
        return DocumentStatus(value)
    except ValueError:
        raise NotFound() from None


@router.get("/document-templates")
def list_templates(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"templates": catalog()})


@router.get("/document-templates/{key}")
def get_template(
    key: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response(template_view(key))


@router.get("/documents")
def list_documents_endpoint(
    kind: str | None = None,
    folder: str | None = None,
    status: str | None = None,  # noqa: A002 - query name is part of the FE contract
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_documents(
        db, _startup(db, membership),
        kind=_parse_kind(kind), folder=folder, status=_parse_status(status),
    )
    return success_response({"documents": [serialize_summary(r) for r in rows]})


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def create_document_endpoint(
    body: DocumentCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    if body.template_key:
        kind, title, sections = instantiate(body.template_key)  # 404 if unknown
        title = body.title or title
    else:
        kind = body.kind or DocumentKind.custom
        title = body.title or ""
        sections = validate_sections(body.sections or [])
    doc = create_document(
        db, startup, created_by_id=membership.user_id, kind=kind, title=title,
        sections=sections, folder=body.folder, template_key=body.template_key,
    )
    db.commit()
    return success_response(serialize_document(doc))


@router.get("/documents/{document_id}")
def get_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(serialize_document(get_document(db, membership, document_id)))


@router.put("/documents/{document_id}")
def update_document_endpoint(
    document_id: uuid.UUID,
    body: DocumentSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    update_document(
        db, doc, title=body.title, sections=body.sections, status=body.status,
        folder=body.folder, expected_version=body.version,
    )
    db.commit()
    return success_response(serialize_document(doc))


@router.delete("/documents/{document_id}")
def delete_document_endpoint(
    document_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    doc = get_document(db, membership, document_id)
    delete_document(db, doc)
    db.commit()
    return success_response({"deleted": True})
```

> **Route ordering:** `/document-templates` and `/document-templates/{key}` are declared before
> `/documents/{document_id}` — they live under a different path prefix so there is no shadowing,
> but keep the literal-before-parametrized ordering as written. There is no `/{kind}`-style
> catch-all here, so no Business-Builder-style guard is needed.

- [ ] **Step 5: Register the router**

In `app/api/v1/api.py`: add `documents` to the `from app.api.v1.endpoints import (...)` group and register (after the `journal` line):

```python
api_router.include_router(documents.router, tags=["documents"])
```

Note: **no `prefix=`** — the routes already carry their full `/documents` and `/document-templates`
paths (two path roots from one router), unlike the other modules which use a single prefix.

- [ ] **Step 6: Run the API tests**

Run: `poetry run pytest tests/api/test_documents.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/document.py app/api/v1/endpoints/documents.py app/api/v1/api.py tests/api/test_documents.py
git commit -m "feat(documents): endpoints (CRUD + templates) + router registration"
```

---

### Task 5: Live e2e, FE guide, SOP, checklist

**Files:**
- Create: `e2e/test_documents.py` (+ captures under `e2e/_captures/documents/`)
- Create: `docs/fe-integration-guide-documents-templates.md`
- Create: `docs/sop/2026-09-09-documents-templates-slice1.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Read the e2e harness**

Read `e2e/conftest.py` and an existing journey (e.g. `e2e/test_business_builder.py`) to reuse the
exact auth/workspace bootstrap helper and the `capture(...)` convention. Match them — do not
invent a new client fixture.

- [ ] **Step 2: Write the live journey**

Add `e2e/test_documents.py` that, against the live server: (a) `GET /document-templates`;
(b) `POST /documents` with `{"template_key":"business_plan"}`; (c) `GET /documents/{id}`;
(d) `PUT` a full-replace edit (version 1→2, status draft→final); (e) `PUT` again with stale
`version:1` → 409; (f) `GET /documents?folder=…` shows the summary (no `sections`);
(g) `DELETE` then `GET` → 404. Write each request+response body to `e2e/_captures/documents/*.json`
using the same helper the other e2e files use.

- [ ] **Step 3: Run the full e2e suite**

```bash
scripts/e2e_run.sh
```
Expected: green, including the new journey. A missing `db.commit()` surfaces here as "the edit
didn't persist" — verify every write endpoint commits.

- [ ] **Step 4: Write the FE integration guide**

Create `docs/fe-integration-guide-documents-templates.md`, **every body pasted verbatim from
`e2e/_captures/documents/`**. Cover: the 7 endpoints with real request/response bodies + status
codes + auth; the template catalog + `instantiate` result; the section JSON shape
(`{id, heading, body}`, order = display order); the `version`/`409 DOCUMENT_VERSION_CONFLICT`
optimistic-concurrency contract; the **summary-vs-full trap** (`sections` absent on list, present
on get); the member-read/editor-write rule (real `403` body); the `?kind`/`?status` unknown-value
`404`. End with a verification table citing capture filenames.

- [ ] **Step 5: Write the SOP**

Create `docs/sop/2026-09-09-documents-templates-slice1.md` in the project's SOP style: what
shipped (+ commit refs), why (Module 18 Library Core; the seam for Module 08's future plan
generator), how (canvas-pattern sections+version; in-code template registry; freeform folder),
what's involved (files/table/migration/endpoints with paths), verification (unit + e2e + captures),
operate/rollback (one migration, `downgrade` drops the table), follow-ups (Slice 2 Cloudinary
uploads; Slice 3 sharing; Slice 4 e-sign; `business_plans.document_id` FK wired when Module 08's
generator ships).

- [ ] **Step 6: Update the master checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, add a **Module 18** section: mark Slice 1 items done,
and list Slices 2-4 (Upload/Cloudinary, Sharing, E-signature) as planned. Note the
`create_document` seam is ready for Module 08's deferred AI plan generator.

- [ ] **Step 7: Reproduce every CI check locally, then commit**

```bash
poetry run ruff check app tests
poetry run black --check app tests
poetry run mypy app
poetry run pytest
scripts/e2e_run.sh
```
All green. Then:

```bash
git add e2e/ docs/fe-integration-guide-documents-templates.md docs/sop/2026-09-09-documents-templates-slice1.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(documents): Slice 1 live e2e + captures + FE guide + SOP + checklist"
```

---

## Self-Review

**1. Spec coverage:**
- §2 data model (enums + `documents` table + indexes) → Task 1. ✓
- §3 template registry (`DOCUMENT_TEMPLATES` + `instantiate` + `catalog`/`template_view`) → Task 2. ✓
- §4 endpoints (7 routes, member-read/editor-write, uuid path, unknown-filter 404, `db.commit()`) → Task 4; service (create/list/get/update/delete/serialize/validate + `create_document` seam) → Task 3. ✓
- §4 `DocumentVersionConflict` 409 → Task 3 (error) + Task 4 (surfaced) + tests. ✓
- §5 migration single-head/zero-drift → Task 1; `document.created` event → Task 3; errors → Task 3. ✓
- §6 testing (unit + live e2e + FE guide) → Tasks 1-5. ✓
- §7 file structure → matches Tasks 1-5. ✓
- §8 decisions (JSONB sections+version, in-code templates, freeform folder, no export, draft/final, seam-only) → reflected in Tasks 1-4 + SOP (Task 5). ✓

**2. Placeholder scan:** the only `00NN` is the migration filename, resolved by Task 1's explicit numbering rule (settle against live head). Every code/test step carries real content — no `TODO`/"handle edge cases"/bare "write tests".

**3. Type consistency:** `create_document(db, startup, *, created_by_id, kind, title, sections, folder, template_key, ai_generated=False)`, `update_document(db, doc, *, title, sections, status, folder, expected_version)`, `list_documents(db, startup, *, kind, folder, status)`, `serialize_summary`/`serialize_document`, `instantiate() -> (kind, title, sections)`, `catalog()`/`template_view()` are used identically across service, endpoints, and tests. `DocumentKind`/`DocumentStatus` members and their `.value` strings match across model, registry, schemas, endpoints, and tests. `DocumentCreate` (optional fields) vs `DocumentSave` (full-replace, `version` required) match their endpoint use.
