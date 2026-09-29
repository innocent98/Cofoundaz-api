# Module 10 Marketing Hub — Slice 4 (SEO Tools) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SEO Tools to the Marketing Hub — a keyword tracker (CRUD), a brand positioning statement (upsert), an on-page checklist per tracked page, and an AI content-gap generator.

**Architecture:** Three new CRUD/persistence tables (`seo_keywords`, `tracked_pages`, `brand_positioning`) + one new AI generation kind (`content_gap`) on the existing `marketing_ai_generations` seam. Migration `0037_seo_tools`. Keyword + positioning shapes are matched to the FE UI handoff; content-gap + checklist are build-ahead (no FE yet). A new `app/services/marketing/seo.py` holds the CRUD service to keep `service.py` focused.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Alembic, Postgres, Redis, Poetry, pytest.

**Spec:** docs/superpowers/specs/2026-09-29-module-10-marketing-slice4-design.md

## Global Constraints

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by `startup_id` (from `membership.startup_id`, never the body); cross-tenant id → `NotFound` (404).
- **RBAC:** every route behind `_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)`; other roles → 403.
- **Endpoint conventions (match existing `marketing.py`):** routes return `success_response(...)` with `response_model=dict[str, Any]`; POST/PATCH/DELETE call `db.commit()`; DELETE returns `success_response({"deleted": True})` (the file's convention — NOT a bare 204). Dependencies in the order `payload, membership=Depends(_marketing), user=Depends(get_verified_user), db=Depends(get_db)` with `# noqa: B008`.
- **Async AI pattern (content-gap):** POST → **202** `{id, status:"generating"}` after `flush` + `job_dispatcher.enqueue`; endpoint commits. Worker `_load` guards `status != generating`; `metered_complete_json` → `None` → `_fail_over_budget` (terminal `failed`, `error="over_budget"`, `output={}`) + return; else write `output` + `ready`; flush only. `register_handler(...)`; `app/worker/__main__.py` already imports `app.worker.handlers.marketing_ai` — no change.
- **`AppError.http_status`** (not `.status_code`); `get_db` does NOT auto-commit.
- **CodeQL (required check):** never a mutating client call (`post/patch/delete`) inside an `assert` — bind to a variable first; no implicit string concat in a list literal.
- **No AI attribution** in any commit message.
- Migration id ≤ 32 chars (this plan uses `0037_seo_tools`).
- **FE alignment:** keyword `volume` is a **string** (FE type); response fields are snake_case (`current_rank`, `target_page`) — the FE guide maps to the FE's camelCase.

## Review Focus

- **Keyword `difficulty` out of range** (150 or -5) → 422, not stored. — Task 2.
- **Tracked-page duplicate `url`** for the same startup → 422 (unique constraint handled, not a 500). — Task 3.
- **Checklist PATCH unknown item key** → 422; a known key merges without dropping other items. — Task 3.
- **Positioning GET before any PUT** → 200 with all-null fields + `statement: null` (never 404); PUT twice updates the same single row. — Task 4.
- **content-gap over-budget** → terminal `failed`/`over_budget`; **kind-mismatch** poll → 404. — Task 5.
- **RBAC** 403 for mentor/investor across all new routes. — Tasks 2–5.

---

## Task 1: Enum + 3 models + migration 0037

**Files:**
- Modify: `app/db/models/enums.py` (add `MarketingGenerationKind.content_gap`)
- Modify: `app/db/models/marketing.py` (add `SeoKeyword`, `TrackedPage`, `BrandPositioning`)
- Create: `alembic/versions/0037_seo_tools.py`
- Test: `tests/db/test_seo_models.py`, `tests/test_seo_tools_migration.py`

**Interfaces (produced):** `MarketingGenerationKind.content_gap` (`"content_gap"`, 11 chars); models `SeoKeyword` (keyword, volume, difficulty, current_rank, target_page), `TrackedPage` (url, checklist JSONB, unique startup_id+url), `BrandPositioning` (audience/need/product/category/differentiator/statement, unique startup_id).

- [ ] **Step 1: Write failing model + enum tests**

```python
# tests/db/test_seo_models.py
from app.db.models.enums import MarketingGenerationKind
from app.db.models.marketing import BrandPositioning, SeoKeyword, TrackedPage
from tests.factories import create_startup, create_user


def test_content_gap_kind_exists():
    assert MarketingGenerationKind.content_gap.value == "content_gap"


def test_seo_keyword_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = SeoKeyword(
        startup_id=s.id, keyword="automated daily savings",
        volume="2.4K", difficulty=45, current_rank=12, target_page="/features/daily-saving",
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.volume == "2.4K" and row.difficulty == 45 and row.current_rank == 12


def test_tracked_page_and_positioning_persist(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    page = TrackedPage(startup_id=s.id, url="/pricing", checklist={"h1": True})
    pos = BrandPositioning(
        startup_id=s.id, audience="gig workers", need="save on irregular income",
        product="Kolo", category="savings app", differentiator="saves automatically",
        statement="For gig workers who save on irregular income, Kolo is the savings app that saves automatically.",
    )
    db.add_all([page, pos])
    db.flush()
    db.refresh(page)
    db.refresh(pos)
    assert page.checklist == {"h1": True}
    assert pos.statement.startswith("For gig workers")
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_seo_models.py -v`
Expected: FAIL (ImportError on the new models; `content_gap` missing).

- [ ] **Step 3: Add the enum member**

`app/db/models/enums.py`, `class MarketingGenerationKind`:

```python
class MarketingGenerationKind(enum.StrEnum):
    copy = "copy"
    plan_week = "plan_week"
    channel_plan = "channel_plan"
    channel_fit = "channel_fit"
    content_gap = "content_gap"
```

- [ ] **Step 4: Add the models**

`app/db/models/marketing.py` (append; imports `String`, `Integer`, `Text`, `JSONB`, `UniqueConstraint`, `ForeignKey`, `PGUUID`, `Mapped`, `mapped_column` are already present):

```python
class SeoKeyword(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "seo_keywords"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    volume: Mapped[str | None] = mapped_column(String(20), nullable=True)
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_page: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TrackedPage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tracked_pages"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    checklist: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (UniqueConstraint("startup_id", "url", name="uq_tracked_page_startup_url"),)


class BrandPositioning(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "brand_positioning"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    audience: Mapped[str | None] = mapped_column(String(300), nullable=True)
    need: Mapped[str | None] = mapped_column(String(300), nullable=True)
    product: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(300), nullable=True)
    differentiator: Mapped[str | None] = mapped_column(String(300), nullable=True)
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 5: Create the migration**

`alembic/versions/0037_seo_tools.py`:

```python
"""seo tools

Revision ID: 0037_seo_tools
Revises: 0036_channel_fit_notes
Create Date: 2026-09-29

Module 10 Slice 4 (SEO Tools): three new tables — seo_keywords, tracked_pages
(unique startup_id+url), brand_positioning (unique startup_id). All additive,
no lock on existing tables. content_gap generation kind reuses marketing_ai_generations
(no enum-length change; "content_gap" is 11 chars, fits the length=12 column).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0037_seo_tools"
down_revision = "0036_channel_fit_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seo_keywords",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("volume", sa.String(length=20), nullable=True),
        sa.Column("difficulty", sa.Integer(), nullable=True),
        sa.Column("current_rank", sa.Integer(), nullable=True),
        sa.Column("target_page", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_seo_keywords_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seo_keywords")),
    )
    op.create_index(op.f("ix_seo_keywords_startup_id"), "seo_keywords", ["startup_id"], unique=False)
    op.create_table(
        "tracked_pages",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_tracked_pages_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tracked_pages")),
        sa.UniqueConstraint("startup_id", "url", name="uq_tracked_page_startup_url"),
    )
    op.create_index(op.f("ix_tracked_pages_startup_id"), "tracked_pages", ["startup_id"], unique=False)
    op.create_table(
        "brand_positioning",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("audience", sa.String(length=300), nullable=True),
        sa.Column("need", sa.String(length=300), nullable=True),
        sa.Column("product", sa.String(length=300), nullable=True),
        sa.Column("category", sa.String(length=300), nullable=True),
        sa.Column("differentiator", sa.String(length=300), nullable=True),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_brand_positioning_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_positioning")),
        sa.UniqueConstraint("startup_id", name="uq_brand_positioning_startup"),
    )
    op.create_index(op.f("ix_brand_positioning_startup_id"), "brand_positioning", ["startup_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_brand_positioning_startup_id"), table_name="brand_positioning")
    op.drop_table("brand_positioning")
    op.drop_index(op.f("ix_tracked_pages_startup_id"), table_name="tracked_pages")
    op.drop_table("tracked_pages")
    op.drop_index(op.f("ix_seo_keywords_startup_id"), table_name="seo_keywords")
    op.drop_table("seo_keywords")
```

- [ ] **Step 6: Migration round-trip + single-head test**

```python
# tests/test_seo_tools_migration.py
import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_single_head_is_0037():
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert "0037_seo_tools" in result.stdout
    assert result.stdout.count("(head)") == 1


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0036_channel_fit_notes")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr
```

- [ ] **Step 7: Run tests + drift check**

Run: `poetry run pytest tests/db/test_seo_models.py tests/test_seo_tools_migration.py -v && poetry run alembic upgrade head && poetry run alembic check`
Expected: PASS; `alembic check` = "No new upgrade operations detected."; `alembic heads` = single head `0037_seo_tools`.

- [ ] **Step 8: Commit**

```bash
git add app/db/models/enums.py app/db/models/marketing.py alembic/versions/0037_seo_tools.py tests/db/test_seo_models.py tests/test_seo_tools_migration.py
git commit -m "feat(marketing): SEO tables (keywords, tracked_pages, brand_positioning) + content_gap kind (migration 0037)"
```

---

## Task 2: Keyword tracker (schema + service + endpoints)

**Files:**
- Modify: `app/schemas/marketing.py` (`KeywordCreate`, `KeywordUpdate`, `KeywordResponse`)
- Create: `app/services/marketing/seo.py` (keyword CRUD + `serialize_keyword`)
- Modify: `app/api/v1/endpoints/marketing.py` (4 keyword routes)
- Test: `tests/api/test_marketing_seo.py` (new)

**Interfaces (consumed):** `SeoKeyword` model (Task 1). **(Produced):** service `create_keyword/get_keyword/list_keywords/update_keyword/delete_keyword/serialize_keyword`; routes `GET/POST /marketing/keywords`, `PATCH/DELETE /marketing/keywords/{keyword_id}`.

- [ ] **Step 1: Write failing endpoint tests**

```python
# tests/api/test_marketing_seo.py
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"
NON_MARKETING_ROLES = [MembershipRole.mentor, MembershipRole.investor]


def _headers(user, startup):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}", "X-Workspace-Id": str(startup.id)}


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def test_keyword_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/keywords",
        json={"keyword": "automated daily savings", "volume": "2.4K", "difficulty": 45, "current_rank": 12, "target_page": "/x"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    kid = created.json()["data"]["id"]
    assert created.json()["data"]["volume"] == "2.4K"

    listed = client.get(f"{BASE}/keywords", headers=h)
    assert listed.status_code == 200
    assert any(k["id"] == kid for k in listed.json()["data"]["keywords"])

    patched = client.patch(f"{BASE}/keywords/{kid}", json={"current_rank": 4}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["current_rank"] == 4

    deleted = client.delete(f"{BASE}/keywords/{kid}", headers=h)
    assert deleted.status_code == 200, deleted.text


def test_keyword_difficulty_out_of_range_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/keywords", json={"keyword": "x", "difficulty": 150}, headers=h)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_keyword_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    posted = client.post(f"{BASE}/keywords", json={"keyword": "x"}, headers=h)
    assert posted.status_code == 403, posted.text
    listed = client.get(f"{BASE}/keywords", headers=h)
    assert listed.status_code == 403, listed.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_seo.py -v`
Expected: FAIL (routes 404).

- [ ] **Step 3: Add schemas**

`app/schemas/marketing.py` (uses `BaseModel`, `Field` already imported; `uuid`, `datetime` present):

```python
class KeywordCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    volume: str | None = Field(default=None, max_length=20)
    difficulty: int | None = Field(default=None, ge=0, le=100)
    current_rank: int | None = Field(default=None, ge=0)
    target_page: str | None = Field(default=None, max_length=500)


class KeywordUpdate(BaseModel):
    keyword: str | None = Field(default=None, min_length=1, max_length=200)
    volume: str | None = Field(default=None, max_length=20)
    difficulty: int | None = Field(default=None, ge=0, le=100)
    current_rank: int | None = Field(default=None, ge=0)
    target_page: str | None = Field(default=None, max_length=500)


class KeywordResponse(BaseModel):
    id: uuid.UUID
    keyword: str
    volume: str | None
    difficulty: int | None
    current_rank: int | None
    target_page: str | None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Create the service**

`app/services/marketing/seo.py`:

```python
import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.marketing import SeoKeyword
from app.schemas.marketing import KeywordCreate, KeywordResponse, KeywordUpdate


def serialize_keyword(row: SeoKeyword) -> KeywordResponse:
    return KeywordResponse.model_validate(row, from_attributes=True)


def create_keyword(db: Session, *, startup_id: uuid.UUID, data: KeywordCreate) -> SeoKeyword:
    row = SeoKeyword(startup_id=startup_id, **data.model_dump())
    db.add(row)
    db.flush()
    return row


def get_keyword(db: Session, *, startup_id: uuid.UUID, keyword_id: uuid.UUID) -> SeoKeyword:
    row = db.query(SeoKeyword).filter_by(id=keyword_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def list_keywords(db: Session, *, startup_id: uuid.UUID) -> list[SeoKeyword]:
    return (
        db.query(SeoKeyword).filter_by(startup_id=startup_id)
        .order_by(SeoKeyword.created_at.desc()).all()
    )


def update_keyword(
    db: Session, *, startup_id: uuid.UUID, keyword_id: uuid.UUID, data: KeywordUpdate
) -> SeoKeyword:
    row = get_keyword(db, startup_id=startup_id, keyword_id=keyword_id)
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    db.flush()
    return row


def delete_keyword(db: Session, *, startup_id: uuid.UUID, keyword_id: uuid.UUID) -> None:
    row = get_keyword(db, startup_id=startup_id, keyword_id=keyword_id)
    db.delete(row)
    db.flush()
```

- [ ] **Step 5: Add the endpoints**

`app/api/v1/endpoints/marketing.py` — import the new service (`from app.services.marketing import seo as seo_svc`) and `KeywordCreate, KeywordUpdate` from `app.schemas.marketing`; add:

```python
@router.post("/keywords", response_model=dict[str, Any])
def create_keyword(
    payload: KeywordCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seo_svc.create_keyword(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(seo_svc.serialize_keyword(row).model_dump())


@router.get("/keywords", response_model=dict[str, Any])
def list_keywords(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = seo_svc.list_keywords(db, startup_id=membership.startup_id)
    return success_response({"keywords": [seo_svc.serialize_keyword(r).model_dump() for r in rows]})


@router.patch("/keywords/{keyword_id}", response_model=dict[str, Any])
def update_keyword(
    keyword_id: uuid.UUID,
    payload: KeywordUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seo_svc.update_keyword(db, startup_id=membership.startup_id, keyword_id=keyword_id, data=payload)
    db.commit()
    return success_response(seo_svc.serialize_keyword(row).model_dump())


@router.delete("/keywords/{keyword_id}", response_model=dict[str, Any])
def delete_keyword(
    keyword_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seo_svc.delete_keyword(db, startup_id=membership.startup_id, keyword_id=keyword_id)
    db.commit()
    return success_response({"deleted": True})
```

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_seo.py -v`
Expected: PASS. No mutating client call inside an assert (each bound to a var first).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/seo.py app/api/v1/endpoints/marketing.py tests/api/test_marketing_seo.py
git commit -m "feat(marketing): SEO keyword tracker CRUD (FE-aligned shapes)"
```

---

## Task 3: On-page checklist (tracked pages)

**Files:**
- Modify: `app/schemas/marketing.py` (`TrackedPageCreate`, `TrackedPageUpdate`, `TrackedPageResponse`)
- Modify: `app/services/marketing/seo.py` (`ON_PAGE_ITEMS`, tracked-page CRUD + checklist merge + `serialize_page`)
- Modify: `app/api/v1/endpoints/marketing.py` (4 tracked-page routes)
- Test: `tests/api/test_marketing_seo.py` (extend)

**Interfaces (produced):** `ON_PAGE_ITEMS: tuple[str, ...]`; service `create_page/get_page/list_pages/update_page_checklist/delete_page/serialize_page`; routes `GET/POST /marketing/seo/pages`, `PATCH/DELETE /marketing/seo/pages/{page_id}`.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/api/test_marketing_seo.py
def test_tracked_page_create_seeds_checklist_and_toggles(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/seo/pages", json={"url": "/pricing"}, headers=h)
    assert created.status_code == 200, created.text
    pid = created.json()["data"]["id"]
    cl = created.json()["data"]["checklist"]
    assert cl["h1"] is False and created.json()["data"]["total"] == 8 and created.json()["data"]["completed"] == 0

    patched = client.patch(f"{BASE}/seo/pages/{pid}", json={"checklist": {"h1": True}}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["checklist"]["h1"] is True
    assert patched.json()["data"]["checklist"]["meta_description"] is False  # others preserved
    assert patched.json()["data"]["completed"] == 1


def test_tracked_page_duplicate_url_422(client, db):
    _u, _s, h = _member(db)
    first = client.post(f"{BASE}/seo/pages", json={"url": "/dup"}, headers=h)
    assert first.status_code == 200, first.text
    dup = client.post(f"{BASE}/seo/pages", json={"url": "/dup"}, headers=h)
    assert dup.status_code == 422, dup.text


def test_tracked_page_unknown_checklist_key_422(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/seo/pages", json={"url": "/p"}, headers=h)
    pid = created.json()["data"]["id"]
    bad = client.patch(f"{BASE}/seo/pages/{pid}", json={"checklist": {"not_a_real_item": True}}, headers=h)
    assert bad.status_code == 422, bad.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_seo.py -v -k tracked_page`
Expected: FAIL (routes 404).

- [ ] **Step 3: Add schemas**

`app/schemas/marketing.py`:

```python
class TrackedPageCreate(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class TrackedPageUpdate(BaseModel):
    checklist: dict[str, bool] = Field(default_factory=dict)


class TrackedPageResponse(BaseModel):
    id: uuid.UUID
    url: str
    checklist: dict[str, bool]
    completed: int
    total: int
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Extend the service**

`app/services/marketing/seo.py` — add (import `TrackedPage` from models, the three schemas, `IntegrityError` from `sqlalchemy.exc`, and `_validation` from `app.services.marketing.service`):

```python
ON_PAGE_ITEMS: tuple[str, ...] = (
    "title_tag", "meta_description", "h1", "keyword_in_intro",
    "image_alt", "internal_links", "url_slug", "mobile_friendly",
)


def _seed_checklist() -> dict[str, bool]:
    return {item: False for item in ON_PAGE_ITEMS}


def serialize_page(row: TrackedPage) -> TrackedPageResponse:
    checklist = {item: bool((row.checklist or {}).get(item, False)) for item in ON_PAGE_ITEMS}
    completed = sum(1 for v in checklist.values() if v)
    return TrackedPageResponse(
        id=row.id, url=row.url, checklist=checklist, completed=completed,
        total=len(ON_PAGE_ITEMS), created_at=row.created_at, updated_at=row.updated_at,
    )


def create_page(db: Session, *, startup_id: uuid.UUID, data: TrackedPageCreate) -> TrackedPage:
    row = TrackedPage(startup_id=startup_id, url=data.url, checklist=_seed_checklist())
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        raise _validation("url", "A tracked page with this url already exists.") from exc
    return row


def get_page(db: Session, *, startup_id: uuid.UUID, page_id: uuid.UUID) -> TrackedPage:
    row = db.query(TrackedPage).filter_by(id=page_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def list_pages(db: Session, *, startup_id: uuid.UUID) -> list[TrackedPage]:
    return (
        db.query(TrackedPage).filter_by(startup_id=startup_id)
        .order_by(TrackedPage.created_at.desc()).all()
    )


def update_page_checklist(
    db: Session, *, startup_id: uuid.UUID, page_id: uuid.UUID, data: TrackedPageUpdate
) -> TrackedPage:
    row = get_page(db, startup_id=startup_id, page_id=page_id)
    unknown = [k for k in data.checklist if k not in ON_PAGE_ITEMS]
    if unknown:
        raise _validation("checklist", f"Unknown checklist item(s): {', '.join(sorted(unknown))}.")
    merged = {item: bool((row.checklist or {}).get(item, False)) for item in ON_PAGE_ITEMS}
    merged.update({k: bool(v) for k, v in data.checklist.items()})
    row.checklist = merged
    db.flush()
    return row


def delete_page(db: Session, *, startup_id: uuid.UUID, page_id: uuid.UUID) -> None:
    row = get_page(db, startup_id=startup_id, page_id=page_id)
    db.delete(row)
    db.flush()
```

Note: `create_page` wraps the flush in `db.begin_nested()` so a duplicate-url `IntegrityError` is caught and converted to 422 without poisoning the outer transaction (same SAVEPOINT pattern `list_channels` uses).

- [ ] **Step 5: Add the endpoints**

`app/api/v1/endpoints/marketing.py` — import `TrackedPageCreate, TrackedPageUpdate`; add 4 routes mirroring the keyword routes but at `/seo/pages` and `/seo/pages/{page_id}`, calling `seo_svc.create_page/list_pages/update_page_checklist/delete_page` and `seo_svc.serialize_page`. List returns `{"pages": [...]}`.

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_seo.py -v -k tracked_page`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/seo.py app/api/v1/endpoints/marketing.py tests/api/test_marketing_seo.py
git commit -m "feat(marketing): on-page checklist via tracked_pages (seed + merge-toggle + dup-url 422)"
```

---

## Task 4: Brand positioning statement

**Files:**
- Modify: `app/schemas/marketing.py` (`PositioningUpsert`, `PositioningResponse`)
- Modify: `app/services/marketing/seo.py` (`get_positioning`, `upsert_positioning`, `compose_statement`, `serialize_positioning`)
- Modify: `app/api/v1/endpoints/marketing.py` (`GET`/`PUT /marketing/positioning`)
- Test: `tests/api/test_marketing_seo.py` (extend)

**Interfaces (produced):** service `get_positioning` (returns row or None), `upsert_positioning`, `compose_statement(fields) -> str`, `serialize_positioning(row | None)`; routes `GET/PUT /marketing/positioning`.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/api/test_marketing_seo.py
def test_positioning_get_before_put_is_empty(client, db):
    _u, _s, h = _member(db)
    got = client.get(f"{BASE}/positioning", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["statement"] is None
    assert got.json()["data"]["audience"] is None


def test_positioning_upsert_composes_statement_and_is_single_row(client, db):
    _u, _s, h = _member(db)
    body = {"audience": "gig workers", "need": "save on irregular income",
            "product": "Kolo", "category": "savings app", "differentiator": "saves automatically"}
    put1 = client.put(f"{BASE}/positioning", json=body, headers=h)
    assert put1.status_code == 200, put1.text
    assert put1.json()["data"]["statement"] == (
        "For gig workers who save on irregular income, Kolo is the savings app that saves automatically."
    )
    put2 = client.put(f"{BASE}/positioning", json={**body, "product": "KoloPay"}, headers=h)
    assert put2.status_code == 200, put2.text
    assert "KoloPay" in put2.json()["data"]["statement"]
    got = client.get(f"{BASE}/positioning", headers=h)
    assert got.json()["data"]["product"] == "KoloPay"
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_seo.py -v -k positioning`
Expected: FAIL (routes 404).

- [ ] **Step 3: Add schemas**

```python
class PositioningUpsert(BaseModel):
    audience: str | None = Field(default=None, max_length=300)
    need: str | None = Field(default=None, max_length=300)
    product: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=300)
    differentiator: str | None = Field(default=None, max_length=300)


class PositioningResponse(BaseModel):
    audience: str | None
    need: str | None
    product: str | None
    category: str | None
    differentiator: str | None
    statement: str | None
```

- [ ] **Step 4: Extend the service**

`app/services/marketing/seo.py` — add (import `BrandPositioning`, `PositioningUpsert`, `PositioningResponse`):

```python
def compose_statement(*, audience, need, product, category, differentiator) -> str:
    a, n, p, c, d = (v or "" for v in (audience, need, product, category, differentiator))
    return f"For {a} who {n}, {p} is the {c} that {d}."


def get_positioning(db: Session, *, startup_id: uuid.UUID) -> BrandPositioning | None:
    return db.query(BrandPositioning).filter_by(startup_id=startup_id).one_or_none()


def upsert_positioning(
    db: Session, *, startup_id: uuid.UUID, data: PositioningUpsert
) -> BrandPositioning:
    row = get_positioning(db, startup_id=startup_id)
    if row is None:
        row = BrandPositioning(startup_id=startup_id)
        db.add(row)
    fields = data.model_dump()
    for name, value in fields.items():
        setattr(row, name, value)
    row.statement = compose_statement(**fields)
    db.flush()
    return row


def serialize_positioning(row: BrandPositioning | None) -> PositioningResponse:
    if row is None:
        return PositioningResponse(
            audience=None, need=None, product=None, category=None,
            differentiator=None, statement=None,
        )
    return PositioningResponse.model_validate(row, from_attributes=True)
```

- [ ] **Step 5: Add the endpoints**

```python
@router.get("/positioning", response_model=dict[str, Any])
def get_positioning(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seo_svc.get_positioning(db, startup_id=membership.startup_id)
    return success_response(seo_svc.serialize_positioning(row).model_dump())


@router.put("/positioning", response_model=dict[str, Any])
def put_positioning(
    payload: PositioningUpsert,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seo_svc.upsert_positioning(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(seo_svc.serialize_positioning(row).model_dump())
```

(Import `PositioningUpsert` from `app.schemas.marketing`.)

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_seo.py -v -k positioning`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/seo.py app/api/v1/endpoints/marketing.py tests/api/test_marketing_seo.py
git commit -m "feat(marketing): brand positioning statement (upsert + composed statement, 1-per-startup)"
```

---

## Task 5: AI content-gap suggestions

**Files:**
- Modify: `app/services/marketing/ai_prompts.py` (`content_gap_schema`, `build_content_gap_messages`)
- Modify: `app/services/marketing/ai_content.py` (`create_content_gap_generation`, `list_content_gap_generations`)
- Modify: `app/worker/handlers/marketing_ai.py` (`handle_marketing_content_gap` + register)
- Modify: `app/api/v1/endpoints/marketing.py` (3 content-gap routes)
- Test: `tests/services/marketing/test_ai_prompts.py`, `tests/worker/test_marketing_ai_handlers.py`, `tests/api/test_marketing_seo.py` (extend each)

**Interfaces (produced):** `content_gap_schema()` → `{gaps: [{title, target_keyword, angle}]}` (array maxItems 7); `build_content_gap_messages(*, keywords: list[str], stage: str | None, industry: str | None)`; `create_content_gap_generation(db, *, startup_id, created_by)`; `list_content_gap_generations`; `handle_marketing_content_gap`; job `ai.marketing.content_gap`; routes `POST /marketing/seo/content-gaps/generate` (202), `GET /marketing/seo/content-gaps`, `GET /marketing/seo/content-gaps/{generation_id}`.

- [ ] **Step 1: Write failing builder + handler + endpoint tests**

```python
# add to tests/services/marketing/test_ai_prompts.py
from app.services.marketing.ai_prompts import build_content_gap_messages, content_gap_schema


def test_content_gap_schema_shape():
    schema = content_gap_schema()
    item = schema["properties"]["gaps"]["items"]
    assert set(item["properties"]) == {"title", "target_keyword", "angle"}
    assert schema["properties"]["gaps"]["maxItems"] == 7


def test_content_gap_messages_carry_keywords_and_stage():
    msgs = build_content_gap_messages(keywords=["daily savings"], stage="mvp", industry="fintech")
    joined = " ".join(m.content for m in msgs)
    assert "daily savings" in joined and "mvp" in joined
```

```python
# add to tests/api/test_marketing_seo.py
def test_content_gap_generate_202_and_pollable(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/seo/content-gaps/generate", headers=h)
    assert r.status_code == 202, r.text
    gid = r.json()["data"]["id"]
    got = client.get(f"{BASE}/seo/content-gaps/{gid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["kind"] == "content_gap"
    hist = client.get(f"{BASE}/seo/content-gaps", headers=h)
    assert hist.status_code == 200 and any(g["id"] == gid for g in hist.json()["data"]["generations"])


def test_content_gap_kind_mismatch_404(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/copy/generate", json={"asset_type": "ad", "tone": "bold", "key_message": "x"}, headers=h)
    copy_id = r.json()["data"]["id"]  # a copy id
    wrong = client.get(f"{BASE}/seo/content-gaps/{copy_id}", headers=h)
    assert wrong.status_code == 404, wrong.text
```

For the worker over-budget test in `tests/worker/test_marketing_ai_handlers.py`, mirror the existing `test_channel_plan_over_budget_fails` exactly (same `_job`/`_gen` helpers, patching the budget), asserting terminal `failed`/`over_budget` for `handle_marketing_content_gap`; and a happy-path test asserting `output["gaps"]` is a list.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/marketing/test_ai_prompts.py tests/api/test_marketing_seo.py tests/worker/test_marketing_ai_handlers.py -v -k content_gap`
Expected: FAIL (ImportError / routes 404).

- [ ] **Step 3: Add the prompt/schema builder**

`app/services/marketing/ai_prompts.py`:

```python
def content_gap_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "gaps": {
                "type": "array",
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "target_keyword": {"type": "string"},
                        "angle": {"type": "string"},
                    },
                    "required": ["title", "target_keyword", "angle"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["gaps"],
        "additionalProperties": False,
    }


def build_content_gap_messages(
    *, keywords: list[str], stage: str | None, industry: str | None
) -> list[LLMMessage]:
    system = (
        "You are an SEO content strategist. Propose up to 7 content ideas (articles/pages) this "
        "startup has NOT covered yet but should, to rank for its target keywords. Each idea has a "
        "short title, a target_keyword, and a one-line angle."
    )
    kw = ", ".join(keywords) if keywords else "none tracked yet"
    user = (
        f"Stage: {stage or 'early'}. Industry: {industry or 'unspecified'}. "
        f"Tracked keywords: {kw}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Add the service functions**

`app/services/marketing/ai_content.py` (mirror `create_channel_fit_generation`):

```python
def create_content_gap_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id, created_by=created_by,
        kind=MarketingGenerationKind.content_gap, inputs={},
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db, "ai.marketing.content_gap",
        {"generation_id": str(g.id), "startup_id": str(startup_id)}, startup_id,
    )
    return g


def list_content_gap_generations(db: Session, *, startup_id: uuid.UUID) -> list[MarketingAiGeneration]:
    return (
        db.query(MarketingAiGeneration)
        .filter_by(startup_id=startup_id, kind=MarketingGenerationKind.content_gap)
        .order_by(MarketingAiGeneration.created_at.desc()).all()
    )
```

- [ ] **Step 5: Add the worker handler**

`app/worker/handlers/marketing_ai.py` — import `build_content_gap_messages`, `content_gap_schema`, and `SeoKeyword`; add:

```python
def handle_marketing_content_gap(db: Session, job: Job) -> None:
    """Propose up to 7 SEO content-gap ideas grounded in the startup's tracked keywords. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    keywords = [
        k.keyword
        for k in db.query(SeoKeyword).filter_by(startup_id=g.startup_id)
        .order_by(SeoKeyword.created_at.desc()).limit(10).all()
    ]
    result = metered_complete_json(
        db, g.startup_id,
        build_content_gap_messages(
            keywords=keywords,
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
        ),
        schema=content_gap_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    gaps = [x for x in (result.get("gaps") or []) if isinstance(x, dict)][:7]
    g.output = {"gaps": gaps}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.content_gap", handle_marketing_content_gap)
```

- [ ] **Step 6: Add the endpoints**

`app/api/v1/endpoints/marketing.py` — add (mirror the channel-plan routes; reuse `ai_content_svc.serialize_generation` + `get_generation` with `kind=MarketingGenerationKind.content_gap`):

```python
@router.post("/seo/content-gaps/generate", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any])
def generate_content_gaps(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_content_gap_generation(db, startup_id=membership.startup_id, created_by=user.id)
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/seo/content-gaps", response_model=dict[str, Any])
def list_content_gaps(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = ai_content_svc.list_content_gap_generations(db, startup_id=membership.startup_id)
    return success_response({"generations": [ai_content_svc.serialize_generation(g).model_dump() for g in rows]})


@router.get("/seo/content-gaps/{generation_id}", response_model=dict[str, Any])
def get_content_gap(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(
        db, startup_id=membership.startup_id, generation_id=generation_id,
        kind=MarketingGenerationKind.content_gap,
    )
    return success_response(ai_content_svc.serialize_generation(g).model_dump())
```

- [ ] **Step 7: Run + RBAC check**

Run: `poetry run pytest tests/services/marketing/test_ai_prompts.py tests/worker/test_marketing_ai_handlers.py tests/api/test_marketing_seo.py -v`
Add an RBAC test asserting 403 for mentor/investor on `POST /seo/content-gaps/generate` and the poll GET (bind each call to a var first). Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/marketing/ai_prompts.py app/services/marketing/ai_content.py app/worker/handlers/marketing_ai.py app/api/v1/endpoints/marketing.py tests/services/marketing/test_ai_prompts.py tests/worker/test_marketing_ai_handlers.py tests/api/test_marketing_seo.py
git commit -m "feat(marketing): AI content-gap suggestions (ai.marketing.content_gap 202 + poll)"
```

---

## Task 6: E2E journey + captures

**Files:**
- Modify: `e2e/test_marketing.py`
- Produces: `e2e/_captures/marketing/` — keyword CRUD, tracked-page + checklist, positioning, content-gap captures.

**Interfaces (consumed):** the file's real request helpers + the `_drain()` worker-drain helper (copy from `test_marketing_channel_ai_journey`).

- [ ] **Step 1: Extend the live journey**

Add `test_marketing_seo_journey` modeled on the existing marketing journeys: authenticate a founder, then (bind every mutating call to a var; keep mutating calls out of asserts):
- create a keyword → capture `keyword_created`; list → capture `keywords_list`; patch rank; 
- create a tracked page → capture `tracked_page_created`; patch a checklist item → capture `tracked_page_checklist`;
- PUT positioning → capture `positioning_upserted`;
- POST content-gaps/generate → capture `content_gap_accepted`; `_drain()`; GET poll → assert status in (ready, failed) → capture `content_gap_ready`.

- [ ] **Step 2: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the SEO journey passes; new captures written. Ensure Homebrew pg/redis stopped (Docker owns 5432/6379); if the Docker daemon is down, start Docker Desktop and wait for healthy containers. Commit ONLY the new marketing captures + `e2e/test_marketing.py` (restore other modules' churn with `git checkout -- e2e/_captures/<other>`).

- [ ] **Step 3: Commit**

```bash
git add e2e/test_marketing.py e2e/_captures/marketing/
git commit -m "test(e2e): marketing SEO tools journey with captures"
```

---

## Task 7: Docs — FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-marketing-seo.md`
- Create: `docs/sop/2026-09-29-marketing-slice4.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE guide** — `docs/fe-integration-guide-marketing-seo.md`, every payload pasted **verbatim from the Task-6 captures**. Cover: keyword CRUD (map snake_case `current_rank`/`target_page` → FE camelCase `currentRank`/`targetPage`; note `volume` is a **string** matching `SEOKeyword.volume`); tracked-page checklist (the 8 `ON_PAGE_ITEMS`, merge-toggle semantics, `completed/total`); positioning (5 fields + composed `statement`, GET-before-PUT returns nulls); content-gap (202→poll, `{gaps:[{title,target_keyword,angle}]}`, `over_budget` failure — cross-ref `docs/fe-integration-guide-ai-status.md`, "Write it" = FE composition into `POST /marketing/copy/generate`). **Mark the on-page checklist and content-gap sections "build-ahead — no FE screen yet; shapes provisional until the FE is built."** Verification table at the end (live vs provisional).

- [ ] **Step 2: SOP** — `docs/sop/2026-09-29-marketing-slice4.md`, matching the Slice 3b SOP style: what shipped (keyword tracker, positioning, on-page checklist, AI content-gap; migration `0037`; `content_gap` worker), why (Module 10 Slice 4, SEO Tools per PRD 10.6, FE-aligned for keyword+positioning), how (manual metrics, `volume` as string, tracked_pages checklist, 1-per-startup positioning with composed statement, content-gap on the async seam grounded in keywords), files/migrations, verification (unit + e2e), follow-ups (external SEO-provider sync; content-gap + checklist are build-ahead pending FE; Slice 5 Analytics remains).

- [ ] **Step 3: Checklist** — `docs/checklist/PROJECT_CHECKLIST.md`: mark **Slice 4 (SEO Tools) complete**; note keyword+positioning FE-aligned and content-gap+checklist build-ahead; leave Slice 5 planned. Follow the file's format.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(marketing): FE guide + SOP + checklist for Module 10 Slice 4 (SEO Tools)"
```

---

## Final verification (before opening the PR)

- [ ] **Full local CI parity, all green** (Homebrew pg/redis stopped, Docker up):

```bash
poetry run black --check app tests && poetry run isort --check-only app tests && \
poetry run ruff check app tests && poetry run mypy app && \
poetry run pylint app --fail-under=9.5 && poetry run bandit -q -r app && \
poetry run pytest --cov=app --cov-fail-under=95 && \
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads && \
bash scripts/e2e_run.sh
```

Expected: green; `alembic heads` = single head `0037_seo_tools`. (The 5 Resend-429 auth/onboarding tests are the known external-quota flake, green in CI.)

- [ ] **No AI attribution:** `git log develop..HEAD --format='%an <%ae>%n%b'` shows none.
- [ ] **CodeQL clean:** no mutating call inside an `assert`; no implicit string concat in a list literal, anywhere new.

---

## Self-Review

**Spec coverage:** keyword tracker → T1(model/migration)+T2; on-page checklist → T1+T3; brand positioning → T1+T4; AI content-gap → T1(enum)+T5; docs → T7; e2e → T6. All spec decisions D1–D5 covered. `ai_content_ideas`/analytics/external-provider correctly out of scope.

**Placeholder scan:** every code step carries real code; the only "mirror the existing X" notes are for T5's worker over-budget test and T6's drain/request helpers, flagged to copy the existing forms (as prior slices did).

**Type consistency:** `seo_svc.create_keyword/update_keyword/delete_keyword/list_keywords/serialize_keyword`, `create_page/update_page_checklist/serialize_page` + `ON_PAGE_ITEMS`, `get_positioning/upsert_positioning/compose_statement/serialize_positioning`, `create_content_gap_generation/list_content_gap_generations`, `content_gap_schema/build_content_gap_messages`, `handle_marketing_content_gap`, and job `ai.marketing.content_gap` + payload `generation_id` are consistent across producing (T1–T5) and consuming tasks. Response field names (`current_rank`, `target_page`, `checklist`, `completed`, `total`, `statement`) match the models (T1) and schemas (T2–T4).

**Review Focus:** difficulty range (T2), duplicate url (T3), unknown checklist key (T3), positioning-before-PUT + single-row (T4), content-gap over-budget + kind-mismatch (T5), RBAC across routes (T2–T5) — each has an owning task's test.
