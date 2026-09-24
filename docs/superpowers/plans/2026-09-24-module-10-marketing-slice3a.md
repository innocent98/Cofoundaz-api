# Module 10 Marketing Hub — Slice 3a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship on-demand AI content generation for the Marketing Hub — a Copy Generator (3 variants + history) and a Plan-my-week starter-calendar generator, both as async jobs.

**Architecture:** One unified `marketing_ai_generations` table (kind = copy | plan_week) with a `generating/ready/failed` status machine; two worker handlers on the `business.plan.generate` pattern (POST → 202 + enqueue → poll GET), metered through `llm_budget` (over-budget → `failed`). Extends the Slice 1–2 marketing module. Write-with-AI reuses the copy generator (no new backend).

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (`Mapped`, `Enum(X, native_enum=False, length=N)`, `JSONB`), Alembic, Postgres, pytest, Poetry.

**Spec:** `docs/superpowers/specs/2026-09-24-module-10-marketing-slice3a-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment. Binds every subagent; overrides any harness attribution reminder.
- **Reproduce CI locally and green before push** via the pinned toolchain (`poetry run …`): black, isort, ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95% coverage, **Migrations (round-trip + `alembic check` drift) — one new migration `0035`, single head off `0034_campaigns_segments`**, e2e, **CodeQL**.
- **CodeQL test-hygiene (enforced):** never put a mutating call (`post`/`patch`/`delete`) inside an `assert` — extract to a variable first; never write implicit string concatenation inside a list/tuple literal — lift multi-fragment strings to a named variable.
- **Enum style:** `enum.StrEnum` mapped via `Enum(Cls, native_enum=False, length=N)`. **FK columns** `index=True`. Models use `UUIDMixin, TimestampMixin, Base`. JSONB via `mapped_column(JSONB, nullable=False, default=dict)`.
- **Transaction discipline:** services end with `db.flush()` and never `commit`/`rollback`; **endpoints call `db.commit()`**; worker handlers end with `db.flush()` only (the runner owns the txn), fail-loud on real LLM errors — only a budget skip returns `None` (mapped to `failed`).
- **Access:** all routes gated `require_role(MembershipRole.founder, MembershipRole.team_member)`; every read/write scoped by `membership.startup_id`; cross-tenant / `kind` mismatch → `NotFound`.
- **AI jobs metered via `llm_budget`;** over-budget → generation ends `status=failed`, `error="over_budget"` (no fallback). `GET /ai/status` reports `over_budget`.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live captures.

## Review Focus

1. **Over-budget generation** — when the workspace is over its LLM budget, the worker sets `status=failed`, `error="over_budget"` (never stuck `generating`, never a crash). (Test in Task 4.)
2. **Cross-tenant / kind-mismatch poll** — polling another workspace's generation, or fetching a `plan_week` row via the copy endpoint (or vice-versa), must return `NotFound`/404. (Test in Task 3 service + Task 5 endpoint.)
3. **Foreign `audience_segment_id`** — a copy request referencing another workspace's (or an unknown) segment must return `422`. (Test in Task 3.)
4. **RBAC on every new route** — a `mentor`/`investor` membership must get `403` on all 5 copy/plan-week routes. (Test in Task 5.)
5. **Plan-week entry with an invalid channel** — the LLM returning an `entries[].channel` not in `ChannelKey` must be dropped, not stored or crash the handler. (Test in Task 4.)

---

## File Structure

- `app/db/models/enums.py` (modify) — add `MarketingGenerationKind`, `MarketingGenerationStatus`, `AssetType`, `CopyTone`.
- `app/db/models/marketing.py` (modify) — add `MarketingAiGeneration`.
- `app/db/models/__init__.py` (modify) — register it.
- `alembic/versions/0035_marketing_ai_generations.py` (create).
- `app/services/marketing/ai_prompts.py` (create) — schema + message builders.
- `app/schemas/marketing.py` (modify) — `CopyGenerateRequest`, `GenerationResponse`.
- `app/services/marketing/ai_content.py` (create) — create/get/list service.
- `app/worker/handlers/marketing_ai.py` (create) — two handlers; `app/worker/__main__.py` (modify) — import it.
- `app/api/v1/endpoints/marketing.py` (modify) — 5 routes.
- Tests under `tests/db/`, `tests/services/marketing/`, `tests/worker/`, `tests/api/`, `e2e/`.
- Docs: `docs/fe-integration-guide-marketing-copy.md`, `docs/sop/2026-09-24-marketing-slice3a.md`, `docs/checklist/PROJECT_CHECKLIST.md`.

---

## Task 1: Enums + model + migration 0035

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/marketing.py`, `app/db/models/__init__.py`
- Create: `alembic/versions/0035_marketing_ai_generations.py`
- Test: `tests/db/test_marketing_models.py` (extend), `tests/test_marketing_migration.py` (extend)

**Interfaces:**
- Produces: `MarketingGenerationKind(copy|plan_week)`, `MarketingGenerationStatus(generating|ready|failed)`, `AssetType(ad|social_post|email|landing_headline|product_description)`, `CopyTone(bold|friendly|expert|playful)`; `MarketingAiGeneration` (`marketing_ai_generations`).

- [ ] **Step 1: Write the failing model test**

Append to `tests/db/test_marketing_models.py`:

```python
def test_marketing_ai_generation_persists(db):
    from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
    from app.db.models.marketing import MarketingAiGeneration
    from tests.factories import create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    g = MarketingAiGeneration(
        startup_id=s.id, created_by=u.id, kind=MarketingGenerationKind.copy,
        inputs={"asset_type": "ad", "tone": "bold"}, status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    got = db.query(MarketingAiGeneration).filter_by(startup_id=s.id).one()
    assert got.kind == MarketingGenerationKind.copy
    assert got.status == MarketingGenerationStatus.generating
    assert got.output == {} and got.error is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_marketing_models.py -k marketing_ai_generation -v`
Expected: FAIL (imports missing).

- [ ] **Step 3: Add the enums**

Append to `app/db/models/enums.py`:

```python
class MarketingGenerationKind(enum.StrEnum):
    copy = "copy"
    plan_week = "plan_week"


class MarketingGenerationStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"
    failed = "failed"


class AssetType(enum.StrEnum):
    ad = "ad"
    social_post = "social_post"
    email = "email"
    landing_headline = "landing_headline"
    product_description = "product_description"


class CopyTone(enum.StrEnum):
    bold = "bold"
    friendly = "friendly"
    expert = "expert"
    playful = "playful"
```

- [ ] **Step 4: Add the model**

In `app/db/models/marketing.py`, extend the `from app.db.models.enums import (...)` block to add `MarketingGenerationKind, MarketingGenerationStatus`, and append:

```python
class MarketingAiGeneration(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketing_ai_generations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    kind: Mapped[MarketingGenerationKind] = mapped_column(
        Enum(MarketingGenerationKind, native_enum=False, length=12), nullable=False, index=True
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[MarketingGenerationStatus] = mapped_column(
        Enum(MarketingGenerationStatus, native_enum=False, length=12),
        nullable=False, default=MarketingGenerationStatus.generating,
    )
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
```

- [ ] **Step 5: Register the model**

In `app/db/models/__init__.py`, extend the marketing import to include `MarketingAiGeneration` (keep the others).

- [ ] **Step 6: Run the model test — verify pass**

Run: `poetry run pytest tests/db/test_marketing_models.py -k marketing_ai_generation -v`
Expected: PASS.

- [ ] **Step 7: Generate + adjust the migration**

Run `poetry run alembic revision --autogenerate -m "marketing_ai_generations"`, rename to `alembic/versions/0035_marketing_ai_generations.py`, set `revision = "0035_marketing_ai_generations"`, `down_revision = "0034_campaigns_segments"`. `upgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "marketing_ai_generations",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("kind", sa.Enum("copy", "plan_week", native_enum=False, length=12), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Enum("generating", "ready", "failed", native_enum=False, length=12), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_marketing_ai_generations_startup_id_startups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_marketing_ai_generations_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_ai_generations")),
    )
    op.create_index(op.f("ix_marketing_ai_generations_startup_id"), "marketing_ai_generations", ["startup_id"], unique=False)
    op.create_index(op.f("ix_marketing_ai_generations_created_by"), "marketing_ai_generations", ["created_by"], unique=False)
    op.create_index(op.f("ix_marketing_ai_generations_kind"), "marketing_ai_generations", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_marketing_ai_generations_kind"), table_name="marketing_ai_generations")
    op.drop_index(op.f("ix_marketing_ai_generations_created_by"), table_name="marketing_ai_generations")
    op.drop_index(op.f("ix_marketing_ai_generations_startup_id"), table_name="marketing_ai_generations")
    op.drop_table("marketing_ai_generations")
```

Ensure `from sqlalchemy.dialects import postgresql`, `import sqlalchemy as sa`, `from alembic import op` are present.

- [ ] **Step 8: Extend the migration assertion test**

In `tests/test_marketing_migration.py`, add a test asserting `marketing_ai_generations` exists after `upgrade head` (lift any multi-fragment `-c` script into a named variable — CodeQL).

- [ ] **Step 9: Verify round-trip, drift, single head**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads`
Expected: succeeds; no drift; single head `0035_marketing_ai_generations`. Then `poetry run pytest tests/db/test_marketing_models.py tests/test_marketing_migration.py -v` PASS.

- [ ] **Step 10: Commit**

```bash
git add app/db/models/enums.py app/db/models/marketing.py app/db/models/__init__.py \
  alembic/versions/0035_marketing_ai_generations.py tests/db/test_marketing_models.py tests/test_marketing_migration.py
git commit -m "feat(marketing): marketing_ai_generations table + generation enums (migration 0035)"
```

---

## Task 2: Prompt builders

**Files:**
- Create: `app/services/marketing/ai_prompts.py`
- Test: `tests/services/marketing/test_ai_prompts.py`

**Interfaces:**
- Consumes: `AssetType`, `CopyTone`, `ChannelKey` (enums); `LLMMessage` (`app/platform/llm.py`).
- Produces: `copy_schema() -> dict`; `build_copy_messages(*, asset_type, channel, tone, key_message, cta, segment_name) -> list[LLMMessage]`; `plan_week_schema() -> dict`; `build_plan_week_messages(*, stage, industry, name) -> list[LLMMessage]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/marketing/test_ai_prompts.py
from app.services.marketing.ai_prompts import (
    build_copy_messages, build_plan_week_messages, copy_schema, plan_week_schema,
)


def test_copy_schema_requires_three_variants():
    s = copy_schema()
    v = s["properties"]["variants"]
    assert v["type"] == "array" and v["minItems"] == 3 and v["maxItems"] == 3
    assert s["required"] == ["variants"]


def test_copy_messages_include_inputs():
    msgs = build_copy_messages(asset_type="ad", channel="email", tone="bold",
        key_message="Launch week is here", cta="Sign up", segment_name="SMB founders")
    user = msgs[1].content
    assert "ad" in user and "email" in user and "bold" in user
    assert "Launch week is here" in user and "Sign up" in user and "SMB founders" in user


def test_copy_messages_tolerate_optional_none():
    msgs = build_copy_messages(asset_type="social_post", channel=None, tone="friendly",
        key_message="Hi", cta=None, segment_name=None)
    assert len(msgs) == 2


def test_plan_week_schema_shape():
    s = plan_week_schema()
    item = s["properties"]["entries"]["items"]["properties"]
    assert set(item) == {"title", "channel", "body", "day_offset"}


def test_plan_week_messages_include_context():
    msgs = build_plan_week_messages(stage="build", industry="fintech", name="Acme")
    assert msgs[0].role == "system"
    assert "build" in msgs[1].content and "fintech" in msgs[1].content
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/marketing/test_ai_prompts.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the builders**

```python
# app/services/marketing/ai_prompts.py
from typing import Any

from app.platform.llm import LLMMessage


def copy_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "variants": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string"}}
        },
        "required": ["variants"],
        "additionalProperties": False,
    }


def build_copy_messages(
    *, asset_type: str, channel: str | None, tone: str, key_message: str,
    cta: str | None, segment_name: str | None,
) -> list[LLMMessage]:
    system = (
        "You are a senior marketing copywriter. Write exactly THREE distinct, ready-to-ship "
        "copy variants for the requested asset type, in the requested tone. Each variant is a "
        "single self-contained string. No numbering, no commentary."
    )
    user = (
        f"Asset type: {asset_type}. Channel: {channel or 'unspecified'}. Tone: {tone}.\n"
        f"Audience: {segment_name or 'general'}.\n"
        f"Key message: {key_message}\n"
        f"Call to action: {cta or 'none'}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def plan_week_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "channel": {"type": "string"},
                        "body": {"type": "string"},
                        "day_offset": {"type": "integer"},
                    },
                    "required": ["title", "channel", "body", "day_offset"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entries"],
        "additionalProperties": False,
    }


def build_plan_week_messages(
    *, stage: str | None, industry: str | None, name: str | None
) -> list[LLMMessage]:
    system = (
        "You are a startup marketing coach. Propose a 7-day starter content calendar (at most 7 "
        "entries). Each entry has a short title, a channel key (one of: organic_social, paid_social, "
        "search, email, content_seo, partnerships, events, referral), a short copy body, and a "
        "day_offset 0-6 (0 = today). Spread channels sensibly for the founder's stage."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'early'}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run tests — verify pass**

Run: `poetry run pytest tests/services/marketing/test_ai_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/marketing/ai_prompts.py tests/services/marketing/test_ai_prompts.py
git commit -m "feat(marketing): AI copy + plan-week prompt/schema builders"
```

---

## Task 3: Generation schemas + service

**Files:**
- Modify: `app/schemas/marketing.py`
- Create: `app/services/marketing/ai_content.py`
- Test: `tests/services/marketing/test_ai_content_service.py`

**Interfaces:**
- Consumes: `MarketingAiGeneration`, `MarketingGenerationKind` (Task 1); `AudienceSegment` (Slice 2); `AssetType`, `CopyTone`, `ChannelKey`; `job_dispatcher`; `NotFound`; `_validation` from `app/services/marketing/service.py`.
- Produces schemas: `CopyGenerateRequest`, `GenerationResponse`, `serialize_generation(g) -> GenerationResponse`. Produces service fns: `create_copy_generation(db, *, startup_id, created_by, data) -> MarketingAiGeneration`; `create_plan_week_generation(db, *, startup_id, created_by) -> MarketingAiGeneration`; `get_generation(db, *, startup_id, generation_id, kind) -> MarketingAiGeneration`; `list_copy_generations(db, *, startup_id) -> list`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/marketing/test_ai_content_service.py
import pytest
from app.core.errors import AppError, NotFound
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.job import Job
from app.schemas.marketing import CopyGenerateRequest
from app.services.marketing import ai_content as svc
from app.services.marketing import segments as seg_svc
from app.schemas.marketing import SegmentCreate
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _req(**kw):
    base = {"asset_type": "ad", "tone": "bold", "key_message": "Ship it"}
    base.update(kw)
    return CopyGenerateRequest(**base)


def test_create_copy_generation_enqueues(db):
    s = _startup(db)
    u = create_user(db)
    g = svc.create_copy_generation(db, startup_id=s.id, created_by=u.id, data=_req())
    assert g.kind == MarketingGenerationKind.copy and g.status == MarketingGenerationStatus.generating
    assert g.inputs["asset_type"] == "ad"
    jobs = db.query(Job).filter_by(type="ai.marketing.copy", startup_id=s.id).all()
    assert len(jobs) == 1 and jobs[0].payload["generation_id"] == str(g.id)


def test_copy_with_valid_segment(db):
    s = _startup(db)
    seg = seg_svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="SMB"))
    g = svc.create_copy_generation(db, startup_id=s.id, created_by=create_user(db).id,
        data=_req(audience_segment_id=seg.id))
    assert g.inputs["audience_segment_id"] == str(seg.id)


def test_copy_foreign_segment_rejected(db):
    s = _startup(db)
    other = _startup(db)
    seg = seg_svc.create_segment(db, startup_id=other.id, data=SegmentCreate(name="X"))
    with pytest.raises(AppError) as ei:
        svc.create_copy_generation(db, startup_id=s.id, created_by=create_user(db).id,
            data=_req(audience_segment_id=seg.id))
    assert ei.value.http_status == 422


def test_create_plan_week_enqueues(db):
    s = _startup(db)
    g = svc.create_plan_week_generation(db, startup_id=s.id, created_by=create_user(db).id)
    assert g.kind == MarketingGenerationKind.plan_week
    assert db.query(Job).filter_by(type="ai.marketing.plan_week", startup_id=s.id).count() == 1


def test_get_generation_kind_mismatch_not_found(db):
    s = _startup(db)
    g = svc.create_plan_week_generation(db, startup_id=s.id, created_by=create_user(db).id)
    with pytest.raises(NotFound):  # fetching a plan_week row via kind=copy
        svc.get_generation(db, startup_id=s.id, generation_id=g.id, kind=MarketingGenerationKind.copy)


def test_get_generation_other_tenant_not_found(db):
    s = _startup(db)
    g = svc.create_copy_generation(db, startup_id=s.id, created_by=create_user(db).id, data=_req())
    with pytest.raises(NotFound):
        svc.get_generation(db, startup_id=_startup(db).id, generation_id=g.id,
            kind=MarketingGenerationKind.copy)


def test_list_copy_generations_only_copy(db):
    s = _startup(db)
    u = create_user(db)
    svc.create_copy_generation(db, startup_id=s.id, created_by=u.id, data=_req())
    svc.create_plan_week_generation(db, startup_id=s.id, created_by=u.id)
    rows = svc.list_copy_generations(db, startup_id=s.id)
    assert len(rows) == 1 and rows[0].kind == MarketingGenerationKind.copy
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/marketing/test_ai_content_service.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Add the schemas**

Append to `app/schemas/marketing.py` (extend the enums import to add `AssetType`, `CopyTone`, `MarketingGenerationKind`, `MarketingGenerationStatus`):

```python
class CopyGenerateRequest(BaseModel):
    asset_type: AssetType
    channel: ChannelKey | None = None
    audience_segment_id: uuid.UUID | None = None
    tone: CopyTone
    key_message: str = Field(min_length=1, max_length=2000)
    cta: str | None = Field(default=None, max_length=200)


class GenerationResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    kind: MarketingGenerationKind
    status: MarketingGenerationStatus
    inputs: dict[str, Any]
    output: dict[str, Any]
    error: str | None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Implement the service**

```python
# app/services/marketing/ai_content.py
import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.marketing import AudienceSegment, MarketingAiGeneration
from app.platform.jobs import job_dispatcher
from app.schemas.marketing import CopyGenerateRequest, GenerationResponse
from app.services.marketing.service import _validation


def serialize_generation(g: MarketingAiGeneration) -> GenerationResponse:
    return GenerationResponse.model_validate(g, from_attributes=True)


def create_copy_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID, data: CopyGenerateRequest
) -> MarketingAiGeneration:
    if data.audience_segment_id is not None:
        seg = (
            db.query(AudienceSegment)
            .filter_by(id=data.audience_segment_id, startup_id=startup_id)
            .one_or_none()
        )
        if seg is None:
            raise _validation("audience_segment_id", "Unknown segment for this workspace.")
    g = MarketingAiGeneration(
        startup_id=startup_id, created_by=created_by, kind=MarketingGenerationKind.copy,
        inputs=data.model_dump(mode="json"), status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db, "ai.marketing.copy",
        {"generation_id": str(g.id), "startup_id": str(startup_id)}, startup_id,
    )
    return g


def create_plan_week_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id, created_by=created_by, kind=MarketingGenerationKind.plan_week,
        inputs={}, status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db, "ai.marketing.plan_week",
        {"generation_id": str(g.id), "startup_id": str(startup_id)}, startup_id,
    )
    return g


def get_generation(
    db: Session, *, startup_id: uuid.UUID, generation_id: uuid.UUID, kind: MarketingGenerationKind
) -> MarketingAiGeneration:
    g = (
        db.query(MarketingAiGeneration)
        .filter_by(id=generation_id, startup_id=startup_id, kind=kind)
        .one_or_none()
    )
    if g is None:
        raise NotFound()
    return g


def list_copy_generations(db: Session, *, startup_id: uuid.UUID) -> list[MarketingAiGeneration]:
    return (
        db.query(MarketingAiGeneration)
        .filter_by(startup_id=startup_id, kind=MarketingGenerationKind.copy)
        .order_by(MarketingAiGeneration.created_at.desc())
        .all()
    )
```

- [ ] **Step 5: Run tests — verify pass**

Run: `poetry run pytest tests/services/marketing/test_ai_content_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/ai_content.py tests/services/marketing/test_ai_content_service.py
git commit -m "feat(marketing): AI generation service (copy + plan-week create, poll, history)"
```

---

## Task 4: Worker handlers

**Files:**
- Create: `app/worker/handlers/marketing_ai.py`
- Modify: `app/worker/__main__.py`
- Test: `tests/worker/test_marketing_ai_handlers.py`

**Interfaces:**
- Consumes: `MarketingAiGeneration`, `MarketingGenerationStatus`, `ChannelKey` (enums); `metered_complete_json`; the Task 2 builders; `settings.LLM_MAX_TOKENS`; `Startup`, `Job`.
- Produces: `handle_marketing_copy(db, job)`, `handle_marketing_plan_week(db, job)`; registered `"ai.marketing.copy"`, `"ai.marketing.plan_week"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_marketing_ai_handlers.py
from app.core.config import settings
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.job import Job, JobStatus
from app.db.models.marketing import MarketingAiGeneration
from app.platform import llm_budget
from app.worker.handlers.marketing_ai import handle_marketing_copy, handle_marketing_plan_week
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        return self.payload


def _gen(db, startup_id, kind, created_by):
    g = MarketingAiGeneration(startup_id=startup_id, created_by=created_by, kind=kind,
        inputs={"asset_type": "ad", "tone": "bold", "key_message": "Ship it"},
        status=MarketingGenerationStatus.generating)
    db.add(g); db.flush()
    return g


def _job(kind, gid, sid):
    t = "ai.marketing.copy" if kind == MarketingGenerationKind.copy else "ai.marketing.plan_week"
    return Job(type=t, payload={"generation_id": str(gid), "startup_id": str(sid)}, status=JobStatus.running)


def test_copy_fills_three_variants(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db); s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.copy, u.id)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"variants": ["a", "b", "c", "d"]}))
    handle_marketing_copy(db, _job(MarketingGenerationKind.copy, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    assert row.output["variants"] == ["a", "b", "c"]  # truncated to 3


def test_copy_over_budget_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db); s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.copy, u.id)
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError()))
    handle_marketing_copy(db, _job(MarketingGenerationKind.copy, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.failed and row.error == "over_budget"
    assert row.output == {}


def test_copy_idempotent_when_not_generating(db, monkeypatch):
    u = create_user(db); s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.copy, u.id)
    g.status = MarketingGenerationStatus.ready; db.flush()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError()))
    handle_marketing_copy(db, _job(MarketingGenerationKind.copy, g.id, s.id))  # no-op, no raise


def test_plan_week_drops_invalid_channel(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db); s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.plan_week, u.id)
    payload = {"entries": [
        {"title": "A", "channel": "email", "body": "x", "day_offset": 0},
        {"title": "B", "channel": "not_a_channel", "body": "y", "day_offset": 1},
    ]}
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM(payload))
    handle_marketing_plan_week(db, _job(MarketingGenerationKind.plan_week, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    assert [e["channel"] for e in row.output["entries"]] == ["email"]  # invalid dropped
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/worker/test_marketing_ai_handlers.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the handlers**

```python
# app/worker/handlers/marketing_ai.py
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.enums import ChannelKey, MarketingGenerationStatus
from app.db.models.job import Job
from app.db.models.marketing import AudienceSegment, MarketingAiGeneration
from app.db.models.startup import Startup
from app.platform.llm_budget import metered_complete_json
from app.services.marketing.ai_prompts import (
    build_copy_messages,
    build_plan_week_messages,
    copy_schema,
    plan_week_schema,
)
from app.worker.runner import register_handler


def _load(db: Session, job: Job) -> MarketingAiGeneration | None:
    g = db.get(MarketingAiGeneration, job.payload["generation_id"])
    if g is None or g.status != MarketingGenerationStatus.generating:
        return None
    return g


def handle_marketing_copy(db: Session, job: Job) -> None:
    """Fill a copy generation with 3 variants via the LLM. No commit."""
    g = _load(db, job)
    if g is None:
        return
    inp = g.inputs
    segment_name = None
    seg_id = inp.get("audience_segment_id")
    if seg_id:
        seg = db.get(AudienceSegment, seg_id)
        segment_name = seg.name if seg is not None else None
    result = metered_complete_json(
        db, g.startup_id,
        build_copy_messages(
            asset_type=inp.get("asset_type", ""), channel=inp.get("channel"),
            tone=inp.get("tone", ""), key_message=inp.get("key_message", ""),
            cta=inp.get("cta"), segment_name=segment_name,
        ),
        schema=copy_schema(), max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        g.status = MarketingGenerationStatus.failed
        g.error = "over_budget"
        db.flush()
        return
    variants = [str(v) for v in (result.get("variants") or [])][:3]
    g.output = {"variants": variants}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.copy", handle_marketing_copy)


def handle_marketing_plan_week(db: Session, job: Job) -> None:
    """Fill a plan-week generation with up to 7 proposed calendar entries. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    result = metered_complete_json(
        db, g.startup_id,
        build_plan_week_messages(
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry, name=startup.name,
        ),
        schema=plan_week_schema(), max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        g.status = MarketingGenerationStatus.failed
        g.error = "over_budget"
        db.flush()
        return
    valid = {c.value for c in ChannelKey}
    entries = [
        e for e in (result.get("entries") or [])
        if isinstance(e, dict) and e.get("channel") in valid
    ][:7]
    g.output = {"entries": entries}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.plan_week", handle_marketing_plan_week)
```

- [ ] **Step 4: Register the handler module in the worker entrypoint**

In `app/worker/__main__.py`, add alongside the other handler imports:

```python
    import app.worker.handlers.marketing_ai  # noqa: F401
```

- [ ] **Step 5: Run tests — verify pass**

Run: `poetry run pytest tests/worker/test_marketing_ai_handlers.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/worker/handlers/marketing_ai.py app/worker/__main__.py tests/worker/test_marketing_ai_handlers.py
git commit -m "feat(marketing): ai.marketing.copy + ai.marketing.plan_week worker handlers"
```

---

## Task 5: Endpoints

**Files:**
- Modify: `app/api/v1/endpoints/marketing.py`
- Test: `tests/api/test_marketing_ai.py`

**Interfaces:**
- Consumes: `ai_content` service + `CopyGenerateRequest`/`GenerationResponse` schemas + `MarketingGenerationKind`; the existing `_marketing` dep + `success_response`.
- Produces: `POST /marketing/copy/generate` (202), `GET /marketing/copy/generations`, `GET /marketing/copy/generations/{id}`, `POST /marketing/calendar/plan-week` (202), `GET /marketing/calendar/plan-week/{id}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_marketing_ai.py
# Reuse the marketing API test suite's authenticated-client fixtures (see tests/api/test_marketing.py
# / conftest.py): a founder/team_member client for allowed cases, a mentor/investor client for RBAC.
# CodeQL: extract every mutating client call to a variable before asserting.

def test_copy_generate_is_202_and_pollable(founder_client):
    client, _ = founder_client
    r = client.post("/api/v1/marketing/copy/generate",
        json={"asset_type": "ad", "tone": "bold", "key_message": "Ship it"})
    assert r.status_code == 202
    gid = r.json()["data"]["id"]
    got = client.get(f"/api/v1/marketing/copy/generations/{gid}")
    assert got.status_code == 200 and got.json()["data"]["kind"] == "copy"
    hist = client.get("/api/v1/marketing/copy/generations")
    assert any(g["id"] == gid for g in hist.json()["data"]["generations"])


def test_plan_week_is_202_and_pollable(founder_client):
    client, _ = founder_client
    r = client.post("/api/v1/marketing/calendar/plan-week")
    assert r.status_code == 202
    gid = r.json()["data"]["id"]
    got = client.get(f"/api/v1/marketing/calendar/plan-week/{gid}")
    assert got.status_code == 200 and got.json()["data"]["kind"] == "plan_week"


def test_kind_mismatch_poll_404(founder_client):
    client, _ = founder_client
    r = client.post("/api/v1/marketing/calendar/plan-week")
    gid = r.json()["data"]["id"]
    wrong = client.get(f"/api/v1/marketing/copy/generations/{gid}")  # plan_week id via copy route
    assert wrong.status_code == 404


def test_rbac_forbidden(non_marketing_client):
    client, _ = non_marketing_client
    posted = client.post("/api/v1/marketing/copy/generate",
        json={"asset_type": "ad", "tone": "bold", "key_message": "x"})
    assert posted.status_code == 403
    assert client.get("/api/v1/marketing/copy/generations").status_code == 403
    assert client.post("/api/v1/marketing/calendar/plan-week").status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_marketing_ai.py -v`
Expected: FAIL (routes 404).

- [ ] **Step 3: Implement the endpoints**

In `app/api/v1/endpoints/marketing.py`, extend imports (`from fastapi import ... , status`; add `CopyGenerateRequest`, `GenerationResponse` to the schemas import; add `from app.db.models.enums import ... MarketingGenerationKind`; `from app.services.marketing import ai_content as ai_content_svc`) and append:

```python
@router.post("/copy/generate", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any])
def generate_copy(
    payload: CopyGenerateRequest,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_copy_generation(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload)
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/copy/generations", response_model=dict[str, Any])
def list_copy_generations(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = ai_content_svc.list_copy_generations(db, startup_id=membership.startup_id)
    return success_response(
        {"generations": [ai_content_svc.serialize_generation(g).model_dump() for g in rows]})


@router.get("/copy/generations/{generation_id}", response_model=dict[str, Any])
def get_copy_generation(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(db, startup_id=membership.startup_id,
        generation_id=generation_id, kind=MarketingGenerationKind.copy)
    return success_response(ai_content_svc.serialize_generation(g).model_dump())


@router.post("/calendar/plan-week", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any])
def generate_plan_week(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_plan_week_generation(
        db, startup_id=membership.startup_id, created_by=user.id)
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/calendar/plan-week/{generation_id}", response_model=dict[str, Any])
def get_plan_week(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(db, startup_id=membership.startup_id,
        generation_id=generation_id, kind=MarketingGenerationKind.plan_week)
    return success_response(ai_content_svc.serialize_generation(g).model_dump())
```

- [ ] **Step 4: Run tests — verify pass**

Run: `poetry run pytest tests/api/test_marketing_ai.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/marketing.py tests/api/test_marketing_ai.py
git commit -m "feat(marketing): copy + plan-week AI endpoints (202 + poll) + RBAC"
```

---

## Task 6: E2E journey + captures

**Files:**
- Modify: `e2e/test_marketing.py` (extend)
- Produces: `e2e/_captures/marketing/` copy + plan-week captures.

**Interfaces:**
- Consumes: the e2e harness fixtures + request helpers used by `e2e/test_marketing.py`; the worker-drain helper (the harness drains jobs — see how earlier AI e2e obtained results).

- [ ] **Step 1: Extend the live journey**

Add an AI-content journey (model on the existing marketing journeys + the drain pattern used by e2e AI tests): authenticate a founder client, then:

```python
    gen = post(client, "/api/v1/marketing/copy/generate",
        {"asset_type": "ad", "channel": "email", "tone": "bold", "key_message": "Launch week is here"})
    capture("marketing", "copy_generate_accepted", gen)
    gid = gen["data"]["id"]
    # drain jobs, then poll
    _drain()  # use the harness's drain helper (see e2e/test_dashboard_ai_briefing.py etc.)
    ready = get(client, f"/api/v1/marketing/copy/generations/{gid}")
    assert ready["data"]["status"] in ("ready", "failed")
    capture("marketing", "copy_generation_ready", ready)
    capture("marketing", "copy_generations_history", get(client, "/api/v1/marketing/copy/generations"))

    plan = post(client, "/api/v1/marketing/calendar/plan-week", {})
    capture("marketing", "plan_week_accepted", plan)
    pid = plan["data"]["id"]
    _drain()
    plan_ready = get(client, f"/api/v1/marketing/calendar/plan-week/{pid}")
    capture("marketing", "plan_week_ready", plan_ready)
```

Use the file's real request-helper style; copy the drain helper the existing AI e2e tests use. Under the stub provider, `output` carries `[stub-llm]` values — capture them and label in the FE guide. **CodeQL:** keep mutating calls out of `assert`.

- [ ] **Step 2: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the marketing AI journey passes; captures written. Commit ONLY the new marketing captures (restore other modules' churn). Report any machine-level change.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_marketing.py e2e/_captures/marketing/
git commit -m "test(e2e): marketing AI copy + plan-week generation journey with captures"
```

---

## Task 7: FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-marketing-copy.md`
- Create: `docs/sop/2026-09-24-marketing-slice3a.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:**
- Consumes: the Task 6 capture files (payloads pasted verbatim).

- [ ] **Step 1: FE guide**

Write `docs/fe-integration-guide-marketing-copy.md`: the two **POST-then-poll** flows (`POST …/copy/generate` and `…/calendar/plan-week` → **202** `{id, status}`, then `GET …/{id}` until `status` is `ready`/`failed`), the `generating/ready/failed` states and the `over_budget` failure (`error="over_budget"` — cross-ref `docs/fe-integration-guide-ai-status.md`), the copy inputs (`AssetType`, `CopyTone`, optional `channel`/`audience_segment_id`, `key_message`, `cta`), the copy `output.variants` (3 strings) + History list, the plan-week `output.entries` (`{title, channel, body, day_offset}`, ≤7), auth (founder/team_member → 403), and the FE compositions: **Save-to-calendar / Add-all** = `POST /marketing/calendar-entries` (S1), **Write-with-AI** = `copy/generate` then `PATCH /marketing/calendar-entries/{id}`. Payloads **verbatim from the captures** (stub `[stub-llm]` values labelled). Cross-reference the Slice 1 calendar + Slice 2 campaigns guides.

- [ ] **Step 2: SOP**

Write `docs/sop/2026-09-24-marketing-slice3a.md`: what shipped (AI copy generator + plan-week, `marketing_ai_generations` table migration `0035`, two `ai.marketing.*` workers), why (Module 10 Slice 3a of 6 — the content-generation half of the AI layer), how (business.plan.generate 202/poll pattern; unified kind-discriminated table; over-budget→failed; write-with-AI reuses copy-gen; metered via llm_budget), files/migrations touched, verification (unit + e2e), follow-ups (Slice 3b channel-plan recommend + fit notes; inline Refine; S5 weekly-readout reuses this table). Match the existing SOP style.

- [ ] **Step 3: Checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, update the Module 10 section: mark **Slice 3a complete** (this branch), note Slice 3b + 4 + 5 as planned. Follow the file's formatting; don't disturb unrelated sections.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(marketing): FE guide + SOP + checklist for Module 10 Slice 3a"
```

---

## Final verification (before opening the PR)

- [ ] **Full local CI parity, all green:**

```bash
poetry run black --check app tests && poetry run isort --check-only app tests && \
poetry run ruff check app tests && poetry run mypy app && \
poetry run pylint app --fail-under=9.5 && poetry run bandit -q -r app && \
poetry run pytest --cov=app --cov-fail-under=95 && \
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads && \
bash scripts/e2e_run.sh
```

Expected: every check passes; `alembic heads` shows the single head `0035_marketing_ai_generations`.

- [ ] **No AI attribution** on any commit: `git log develop..HEAD --format='%an <%ae>%n%b'` shows none.
- [ ] **CodeQL clean:** no mutating call inside an `assert`, no implicit string concat in a list literal, anywhere new.

---

## Self-Review

**Spec coverage:** enums+model+migration → T1; prompt builders → T2; schemas+service → T3; workers → T4; endpoints+RBAC → T5; e2e → T6; docs → T7. Unified table, over-budget→failed, kind-mismatch NotFound, audience validation, plan-week invalid-channel drop, write-with-AI-reuse (documented, no code) all covered. Channel-plan recommend/fit notes (3b), weekly readout (S5), Refine, save-to-calendar endpoint correctly out of scope.

**Placeholder scan:** every code/test step carries real content; the only conditional notes are Task 5/6 fixture + drain-helper names (flagged as illustrative with instructions to use the suite's real ones).

**Type consistency:** `serialize_generation`, `create_copy_generation`/`create_plan_week_generation`/`get_generation(...,kind)`/`list_copy_generations`, `CopyGenerateRequest`/`GenerationResponse`, and the builder signatures (`build_copy_messages(*, asset_type, channel, tone, key_message, cta, segment_name)`, `build_plan_week_messages(*, stage, industry, name)`, `copy_schema`/`plan_week_schema`) match across producing (T2/T3) and consuming (T4/T5) tasks. Job types `ai.marketing.copy`/`ai.marketing.plan_week` and the payload key `generation_id` match between enqueue (T3), handler (T4), and register (T4).

**Review Focus:** all five have owning tests — over-budget→failed (T4), cross-tenant/kind-mismatch (T3 service + T5 endpoint), foreign audience_segment_id (T3), RBAC (T5), plan-week invalid channel dropped (T4).
