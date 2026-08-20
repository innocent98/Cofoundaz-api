# Health Score (Module 06) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the explainable 0–100 Health Score — overall + band, 5 dimension sub-scores with a drill-down signal table, trend history, cold-start benchmarks, and rule-based accept/dismiss recommendations — computed inline at assessment-complete and lazily on read.

**Architecture:** A pure, deterministic `recompute_health_score(db, startup)` service reads the latest completed `AssessmentResult`, writes `health_signals` (replaced each run), upserts `health_scores`, appends `health_score_history`, regenerates rule-based `health_recommendations` (user decisions durable, unacted pendings ephemeral), and emits `healthscore.updated/.dropped/.record`. It is called inline from `complete_assessment` and lazily from `GET /health-score`. No async worker. Weights, bands, and the recommendation catalog are static versioned config.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL (psycopg2 + `postgresql.insert` for upsert), pytest (real Postgres, per-test rollback), httpx (E2E).

**Spec:** `docs/superpowers/specs/2026-08-19-health-score-design.md`

## Global Constraints

- **Dimension keys** reuse the existing `Dimension` enum verbatim: `product, market, money, legal, team`. "Financial" is `money`'s **display label only** — never a stored key.
- **No fabricated scores.** A `health_scores` row exists **only** when ≥1 assessment is completed. Pre-assessment, `GET /health-score` returns `200` with `status:"pending_assessment"`, not a 404 and not a neutral 50.
- **Reads** = any active member (`require_workspace` + `get_verified_user`). **Mutations** (accept/dismiss) = **founder only** (`require_role(MembershipRole.founder)`).
- **Cross-workspace access returns a uniform `404 NOT_FOUND`** — never 403 — so ids can't be enumerated across tenants.
- **Config versioning:** `HEALTH_CONFIG_VERSION` is stamped on every `health_scores` and `health_score_history` row.
- **No AI-attribution trailer** in any commit message (`Co-Authored-By` etc. are forbidden — repo rule).
- **Unit schema** is built by `Base.metadata.create_all` (register new models in `app/db/models/__init__.py`); **E2E schema** is built by `alembic upgrade head` (Task 2 migration must mirror the models exactly).
- Response bodies use `success_response(data, meta=None)` from `app.core.envelope`. Errors raise `AppError` subclasses from `app.core.errors`.

---

## File Structure

**Create:**
- `app/db/models/health_score.py` — the 4 ORM models (`HealthScore`, `HealthScoreHistory`, `HealthSignal`, `HealthRecommendation`).
- `app/services/health_score/__init__.py`
- `app/services/health_score/config.py` — `HEALTH_CONFIG_VERSION`, `DIMENSION_WEIGHTS`, `BANDS`, `MIN_COHORT_SIZE`, `RECOMMENDATION_CATALOG`.
- `app/services/health_score/scoring.py` — pure helpers: `weighted_overall`, `band_for`.
- `app/services/health_score/recommendations.py` — `generate_recommendations` (reconciliation).
- `app/services/health_score/service.py` — `latest_completed_result`, `recompute_health_score`, `get_overview`, `get_dimension`, `get_history`, `get_benchmarks`, serializers.
- `app/api/v1/endpoints/health_score.py` — the router (7 routes).
- `alembic/versions/0005_health_score.py` — migration.
- `tests/services/test_health_scoring.py`, `tests/services/test_health_recompute.py`, `tests/services/test_health_recommendations.py`, `tests/api/test_health_score.py`, `tests/api/test_health_recommendations.py` — unit/integration.
- `e2e/test_health_score.py` — live journey.
- `docs/sop/2026-08-19-health-score.md`, `docs/fe-integration-guide-health-score.md`.

**Modify:**
- `app/db/models/enums.py` — add `RecommendationEffort`, `RecommendationStatus`.
- `app/db/models/__init__.py` — import the new models so `create_all` sees them.
- `tests/factories.py` — add `create_health_score`, `create_history`, `create_recommendation`.
- `app/api/v1/api.py` — include the health-score router.
- `app/services/assessment/service.py:197` — replace the `healthscore.recalculate` enqueue with an inline `recompute_health_score` call.
- `e2e/test_smoke.py` — add the health-score paths to the openapi assertion.
- `docs/checklist/PROJECT_CHECKLIST.md` — check off Module 06 items.
- `docs/sop/2026-08-16-assessment.md`, `docs/sop/2026-08-15-onboarding.md` — one-line note that the healthscore stub jobs are retired.

---

### Task 1: Enums, models & factories

**Files:**
- Modify: `app/db/models/enums.py`
- Create: `app/db/models/health_score.py`
- Modify: `app/db/models/__init__.py`
- Modify: `tests/factories.py`
- Test: `tests/services/test_health_models.py`

**Interfaces:**
- Consumes: `Base`, `UUIDMixin`, `TimestampMixin`, `Dimension`, `Startup`.
- Produces: models `HealthScore`, `HealthScoreHistory`, `HealthSignal`, `HealthRecommendation`; enums `RecommendationEffort(low|medium|high)`, `RecommendationStatus(pending|accepted|dismissed)`; factories `create_health_score(db, startup, *, score=70, dimension_scores=None, band="healthy", config_version=1)`, `create_history(db, startup, *, score, delta=0, computed_at=None, trigger="test")`, `create_recommendation(db, startup, *, key, dimension="money", status=RecommendationStatus.pending, priority=1, estimated_lift=8, effort=RecommendationEffort.medium)`.

- [ ] **Step 1: Add the enums.** Append to `app/db/models/enums.py`:

```python
class RecommendationEffort(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RecommendationStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    dismissed = "dismissed"
```

- [ ] **Step 2: Write the failing model test.** Create `tests/services/test_health_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import RecommendationStatus
from app.db.models.health_score import (
    HealthRecommendation,
    HealthScore,
    HealthScoreHistory,
    HealthSignal,
)
from tests.factories import create_startup, create_user


def test_health_score_one_per_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(HealthScore(startup_id=s.id, score=70, band="healthy",
                        dimension_scores={"product": 70}, source="assessment", config_version=1))
    db.flush()
    db.add(HealthScore(startup_id=s.id, score=80, band="thriving",
                        dimension_scores={"product": 80}, source="assessment", config_version=1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_recommendation_key_unique_per_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    for _ in range(2):
        db.add(HealthRecommendation(
            startup_id=s.id, dimension="money", key="money.runway_model",
            title="t", body="b", estimated_lift=8, effort="medium",
            status=RecommendationStatus.pending, priority=1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_signal_and_history_insert(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(HealthSignal(startup_id=s.id, dimension="money", key="assessment.money",
                        value=40, contribution=8.0, source_ref="assessment:x"))
    db.add(HealthScoreHistory(startup_id=s.id, score=70, dimension_scores={"money": 40},
                              delta=0, trigger="test", config_version=1))
    db.flush()
    assert db.query(HealthSignal).count() == 1
    assert db.query(HealthScoreHistory).count() == 1
```

- [ ] **Step 3: Run the test — expect failure.** Run: `poetry run pytest tests/services/test_health_models.py -q`. Expected: FAIL (`ModuleNotFoundError: app.db.models.health_score`).

- [ ] **Step 4: Create the models.** Create `app/db/models/health_score.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import RecommendationEffort, RecommendationStatus


class HealthScore(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_scores"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="assessment")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)


class HealthScoreHistory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_score_history"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_health_history_startup_created", "startup_id", "created_at"),)


class HealthSignal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_signals"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    contribution: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)


class HealthRecommendation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_recommendations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_lift: Mapped[int] = mapped_column(Integer, nullable=False)
    effort: Mapped[RecommendationEffort] = mapped_column(
        Enum(RecommendationEffort, native_enum=False, length=10), nullable=False
    )
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, native_enum=False, length=12),
        nullable=False, default=RecommendationStatus.pending,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_recommendation_startup_key", "startup_id", "key", unique=True),
    )
```

- [ ] **Step 5: Register the models.** In `app/db/models/__init__.py`, add `from app.db.models.health_score import (HealthRecommendation, HealthScore, HealthScoreHistory, HealthSignal)  # noqa: F401` following the existing import style (and add to `__all__` if the file defines one).

- [ ] **Step 6: Add the factories.** Append to `tests/factories.py` (add `HealthScore, HealthScoreHistory, HealthRecommendation, HealthSignal` and `RecommendationEffort, RecommendationStatus` to the imports):

```python
def create_health_score(db, startup, *, score=70, dimension_scores=None,
                        band="healthy", config_version=1):
    hs = HealthScore(startup_id=startup.id, score=score, band=band,
                     dimension_scores=dimension_scores or {"product": 70, "market": 70,
                     "money": 70, "legal": 70, "team": 70},
                     source="assessment", config_version=config_version)
    db.add(hs); db.flush(); return hs


def create_history(db, startup, *, score, delta=0, computed_at=None, trigger="test",
                   dimension_scores=None, config_version=1):
    h = HealthScoreHistory(startup_id=startup.id, score=score, delta=delta, trigger=trigger,
                           dimension_scores=dimension_scores or {"money": score},
                           config_version=config_version)
    db.add(h); db.flush()
    if computed_at is not None:
        h.created_at = computed_at
        db.flush()
    return h


def create_recommendation(db, startup, *, key, dimension="money",
                          status=RecommendationStatus.pending, priority=1,
                          estimated_lift=8, effort=RecommendationEffort.medium,
                          title="t", body="b"):
    r = HealthRecommendation(startup_id=startup.id, dimension=dimension, key=key, title=title,
                             body=body, estimated_lift=estimated_lift, effort=effort,
                             status=status, priority=priority)
    db.add(r); db.flush(); return r
```

- [ ] **Step 7: Run the test — expect pass.** Run: `poetry run pytest tests/services/test_health_models.py -q`. Expected: PASS (3 tests).

- [ ] **Step 8: Commit.**

```bash
git add app/db/models/enums.py app/db/models/health_score.py app/db/models/__init__.py tests/factories.py tests/services/test_health_models.py
git commit -m "feat(health-score): enums, ORM models, factories"
```

---

### Task 2: Migration `0005_health_score`

**Files:**
- Create: `alembic/versions/0005_health_score.py`

**Interfaces:**
- Consumes: revision `0004_assessment` (the current head).
- Produces: DDL that exactly mirrors the Task 1 models (E2E parity).

- [ ] **Step 1: Confirm the current head.** Run: `poetry run alembic heads`. Expected: `0004_assessment (head)`.

- [ ] **Step 2: Write the migration.** Create `alembic/versions/0005_health_score.py` (model this on `alembic/versions/0004_assessment.py`):

```python
"""health score

Revision ID: 0005_health_score
Revises: 0004_assessment
Create Date: 2026-08-19

Module 06 (Health Score) schema. Four new tables — no lock on existing tables:
  - health_scores        (unique startup_id: one live score per startup)
  - health_score_history (append-only trend ledger)
  - health_signals       (current-state projection, replaced each recompute)
  - health_recommendations (unique (startup_id, key): dedupe identity)
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0005_health_score"
down_revision = "0004_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_scores",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("startup_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(20), nullable=False),
        sa.Column("dimension_scores", JSONB(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="assessment"),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_health_scores_startup_id", "health_scores", ["startup_id"], unique=True)

    op.create_table(
        "health_score_history",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("startup_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("dimension_scores", JSONB(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_health_score_history_startup_id", "health_score_history", ["startup_id"])
    op.create_index("ix_health_history_startup_created", "health_score_history",
                    ["startup_id", "created_at"])

    op.create_table(
        "health_signals",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("startup_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("value", sa.Numeric(6, 2), nullable=False),
        sa.Column("contribution", sa.Numeric(6, 2), nullable=False),
        sa.Column("source_ref", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_health_signals_startup_id", "health_signals", ["startup_id"])

    op.create_table(
        "health_recommendations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("startup_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("estimated_lift", sa.Integer(), nullable=False),
        sa.Column("effort", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_health_recommendations_startup_id", "health_recommendations", ["startup_id"])
    op.create_index("uq_recommendation_startup_key", "health_recommendations",
                    ["startup_id", "key"], unique=True)


def downgrade() -> None:
    op.drop_table("health_recommendations")
    op.drop_table("health_signals")
    op.drop_table("health_score_history")
    op.drop_table("health_scores")
```

- [ ] **Step 3: Apply it.** Run: `poetry run alembic upgrade head`. Expected: applies `0005_health_score` with no error.

- [ ] **Step 4: Verify model/migration parity.** Run: `poetry run alembic revision --autogenerate -m "parity-check" --sql` is overkill; instead run `poetry run alembic check` if available, else generate a throwaway autogenerate and confirm it reports "No changes detected" for the 4 tables, then delete it. Expected: no drift between models and migration. (If drift appears, fix the migration to match the models.)

- [ ] **Step 5: Verify downgrade.** Run: `poetry run alembic downgrade -1 && poetry run alembic upgrade head`. Expected: both succeed.

- [ ] **Step 6: Commit.**

```bash
git add alembic/versions/0005_health_score.py
git commit -m "feat(health-score): migration 0005 (4 tables + indexes)"
```

---

### Task 3: Static config + pure scoring

**Files:**
- Create: `app/services/health_score/__init__.py` (empty)
- Create: `app/services/health_score/config.py`
- Create: `app/services/health_score/scoring.py`
- Test: `tests/services/test_health_scoring.py`

**Interfaces:**
- Produces:
  - `config.HEALTH_CONFIG_VERSION: int` (= 1), `config.DIMENSION_WEIGHTS: dict[str, float]` (sums to 1.0), `config.BANDS: list[tuple[int, int, str]]`, `config.MIN_COHORT_SIZE: int` (= 5), `config.RECOMMENDATION_CATALOG: dict[str, list[dict]]`.
  - `scoring.weighted_overall(dimension_scores: dict[str, int]) -> int`
  - `scoring.band_for(score: int) -> str`

- [ ] **Step 1: Write the failing test.** Create `tests/services/test_health_scoring.py`:

```python
import pytest

from app.services.health_score.config import DIMENSION_WEIGHTS, RECOMMENDATION_CATALOG
from app.services.health_score.scoring import band_for, weighted_overall


def test_weights_sum_to_one():
    assert round(sum(DIMENSION_WEIGHTS.values()), 6) == 1.0
    assert set(DIMENSION_WEIGHTS) == {"product", "market", "money", "legal", "team"}


def test_weighted_overall_equal_weights():
    assert weighted_overall({"product": 80, "market": 60, "money": 40, "legal": 100, "team": 20}) == 60


def test_weighted_overall_clamps_and_rounds():
    assert weighted_overall({"product": 71, "market": 71, "money": 71, "legal": 71, "team": 72}) == 71


@pytest.mark.parametrize("score,band", [
    (0, "at_risk"), (39, "at_risk"), (40, "needs_work"), (59, "needs_work"),
    (60, "healthy"), (79, "healthy"), (80, "thriving"), (100, "thriving"),
])
def test_band_boundaries(score, band):
    assert band_for(score) == band


def test_catalog_covers_all_dimensions():
    assert set(RECOMMENDATION_CATALOG) == {"product", "market", "money", "legal", "team"}
    for entries in RECOMMENDATION_CATALOG.values():
        for e in entries:
            assert {"key", "title", "body", "estimated_lift", "effort", "triggers_below"} <= set(e)
```

- [ ] **Step 2: Run it — expect failure.** Run: `poetry run pytest tests/services/test_health_scoring.py -q`. Expected: FAIL (module missing).

- [ ] **Step 3: Write the config.** Create `app/services/health_score/__init__.py` (empty) and `app/services/health_score/config.py`:

```python
HEALTH_CONFIG_VERSION = 1

DIMENSION_WEIGHTS: dict[str, float] = {
    "product": 0.20, "market": 0.20, "money": 0.20, "legal": 0.20, "team": 0.20,
}

# (low_inclusive, high_inclusive, band_key)
BANDS: list[tuple[int, int, str]] = [
    (0, 39, "at_risk"),
    (40, 59, "needs_work"),
    (60, 79, "healthy"),
    (80, 100, "thriving"),
]

MIN_COHORT_SIZE = 5

DIMENSION_LABELS: dict[str, str] = {
    "product": "Product", "market": "Market", "money": "Financial",
    "legal": "Legal", "team": "Team",
}

RECOMMENDATION_CATALOG: dict[str, list[dict]] = {
    "product": [
        {"key": "product.define_mvp", "title": "Define your MVP scope",
         "body": "Write a one-page MVP definition: the single problem, the smallest feature set that solves it, and what you are deliberately leaving out.",
         "estimated_lift": 8, "effort": "medium", "triggers_below": 60},
        {"key": "product.user_feedback_loop", "title": "Set up a user feedback loop",
         "body": "Put a lightweight channel in front of real users (calls, a form, a Slack) and commit to reviewing it weekly.",
         "estimated_lift": 6, "effort": "low", "triggers_below": 50},
    ],
    "market": [
        {"key": "market.icp_definition", "title": "Write a one-page ICP",
         "body": "Define your ideal customer profile: who they are, the pain, and why now. Specificity beats reach at this stage.",
         "estimated_lift": 7, "effort": "low", "triggers_below": 60},
        {"key": "market.competitor_map", "title": "Map your top 5 competitors",
         "body": "List the five closest alternatives (including 'do nothing') and one sentence on how you differ from each.",
         "estimated_lift": 5, "effort": "low", "triggers_below": 50},
    ],
    "money": [
        {"key": "money.runway_model", "title": "Build a 12-month runway model",
         "body": "Model monthly cash in/out for 12 months so you know your runway and the month you must raise or break even.",
         "estimated_lift": 8, "effort": "medium", "triggers_below": 60},
        {"key": "money.pricing_experiment", "title": "Run a pricing experiment",
         "body": "Test one concrete price point with real prospects. Willingness-to-pay evidence de-risks your whole model.",
         "estimated_lift": 6, "effort": "medium", "triggers_below": 50},
    ],
    "legal": [
        {"key": "legal.incorporate", "title": "Complete incorporation",
         "body": "Register the company and issue founder shares. Operating unincorporated exposes founders personally and blocks fundraising.",
         "estimated_lift": 9, "effort": "high", "triggers_below": 60},
        {"key": "legal.founder_agreement", "title": "Sign a founders' agreement",
         "body": "Put equity splits, vesting, and roles in writing before it is contentious. This prevents the most common founder disputes.",
         "estimated_lift": 7, "effort": "medium", "triggers_below": 50},
    ],
    "team": [
        {"key": "team.roles_clarity", "title": "Clarify founder roles & equity",
         "body": "Write down who owns what decisions and the equity split with vesting. Ambiguity here compounds fast.",
         "estimated_lift": 7, "effort": "medium", "triggers_below": 60},
        {"key": "team.hiring_plan", "title": "Draft a 6-month hiring plan",
         "body": "List the next 2–3 critical hires, when, and why. A plan turns hiring from reactive to intentional.",
         "estimated_lift": 5, "effort": "low", "triggers_below": 50},
    ],
}
```

- [ ] **Step 4: Write the scoring helpers.** Create `app/services/health_score/scoring.py`:

```python
from app.db.models.enums import Dimension
from app.services.health_score.config import BANDS, DIMENSION_WEIGHTS


def weighted_overall(dimension_scores: dict[str, int]) -> int:
    total = sum(DIMENSION_WEIGHTS[d.value] * dimension_scores.get(d.value, 50) for d in Dimension)
    return max(0, min(100, round(total)))


def band_for(score: int) -> str:
    for low, high, key in BANDS:
        if low <= score <= high:
            return key
    return BANDS[-1][2]
```

- [ ] **Step 5: Run the tests — expect pass.** Run: `poetry run pytest tests/services/test_health_scoring.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/services/health_score/__init__.py app/services/health_score/config.py app/services/health_score/scoring.py tests/services/test_health_scoring.py
git commit -m "feat(health-score): static config + pure scoring (weights, bands)"
```

---

### Task 4: Recompute service (signals, upsert, history, events)

**Files:**
- Create: `app/services/health_score/service.py`
- Test: `tests/services/test_health_recompute.py`

**Interfaces:**
- Consumes: `weighted_overall`, `band_for`, `HEALTH_CONFIG_VERSION`, `DIMENSION_WEIGHTS`; models `HealthScore`, `HealthScoreHistory`, `HealthSignal`; `AssessmentResult`, `Assessment`, `AssessmentStatus`; `event_bus`.
- Produces:
  - `latest_completed_result(db, startup_id) -> AssessmentResult | None`
  - `recompute_health_score(db, startup, *, trigger: str = "assessment_complete") -> HealthScore | None`
  - (recommendations are added in Task 5 — this task leaves a call site comment, not a stub function.)

- [ ] **Step 1: Write the failing test.** Create `tests/services/test_health_recompute.py`:

```python
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus
from app.db.models.health_score import HealthScore, HealthScoreHistory, HealthSignal
from app.platform.events import event_bus
from app.services.health_score.service import recompute_health_score
from tests.factories import create_assessment, create_history, create_startup, create_user


def _complete_with_scores(db, startup, scores):
    a = create_assessment(db, startup, status=AssessmentStatus.completed)
    db.add(AssessmentResult(assessment_id=a.id, dimension_scores=scores,
                            overall_provisional=sum(scores.values()) // 5, narrative="n"))
    db.flush()
    return a


def test_recompute_writes_score_signals_history(db):
    u = create_user(db); s = create_startup(db, owner=u)
    _complete_with_scores(db, s, {"product": 80, "market": 60, "money": 40, "legal": 100, "team": 20})
    hs = recompute_health_score(db, s, trigger="assessment_complete")
    assert hs.score == 60
    assert hs.band == "healthy"
    assert db.query(HealthSignal).filter_by(startup_id=s.id).count() == 5
    assert db.query(HealthScoreHistory).filter_by(startup_id=s.id).count() == 1


def test_recompute_pending_when_no_assessment(db):
    u = create_user(db); s = create_startup(db, owner=u)
    assert recompute_health_score(db, s) is None
    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 0


def test_recompute_upserts_and_appends_history(db):
    u = create_user(db); s = create_startup(db, owner=u)
    _complete_with_scores(db, s, {"product": 50, "market": 50, "money": 50, "legal": 50, "team": 50})
    recompute_health_score(db, s)
    recompute_health_score(db, s)
    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 1  # upsert, not duplicate
    assert db.query(HealthScoreHistory).filter_by(startup_id=s.id).count() == 2  # appended twice


def test_recompute_emits_updated_and_record(db, monkeypatch):
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda e, p: published.append((e, p)))
    u = create_user(db); s = create_startup(db, owner=u)
    _complete_with_scores(db, s, {"product": 70, "market": 70, "money": 70, "legal": 70, "team": 70})
    recompute_health_score(db, s)
    events = [e for e, _ in published]
    assert "healthscore.updated" in events
    assert "healthscore.record" in events  # first real score is a record? no — see Step 3 rule


def test_recompute_dropped_event(db, monkeypatch):
    from datetime import UTC, datetime, timedelta
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda e, p: published.append((e, p)))
    u = create_user(db); s = create_startup(db, owner=u)
    # a prior history point 8 days ago at 80
    create_history(db, s, score=80, computed_at=datetime.now(UTC) - timedelta(days=8))
    _complete_with_scores(db, s, {"product": 60, "market": 60, "money": 60, "legal": 60, "team": 60})
    recompute_health_score(db, s)
    assert "healthscore.dropped" in [e for e, _ in published]
```

Note on `test_recompute_emits_updated_and_record`: adjust the assertion to the rule chosen in Step 3 — the **first** real score is **not** a record (there is no prior max to beat). Rewrite that test to seed a lower prior history point, then assert `healthscore.record` fires when the new score exceeds it, and a separate test asserts the first-ever score does **not** emit `record`.

- [ ] **Step 2: Run it — expect failure.** Run: `poetry run pytest tests/services/test_health_recompute.py -q`. Expected: FAIL (module missing).

- [ ] **Step 3: Write the service.** Create `app/services/health_score/service.py`:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.enums import AssessmentStatus, Dimension
from app.db.models.health_score import HealthScore, HealthScoreHistory, HealthSignal
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.health_score.config import DIMENSION_WEIGHTS, HEALTH_CONFIG_VERSION
from app.services.health_score.scoring import band_for, weighted_overall


def latest_completed_result(db: Session, startup_id) -> AssessmentResult | None:
    return db.execute(
        select(AssessmentResult)
        .join(Assessment, Assessment.id == AssessmentResult.assessment_id)
        .where(Assessment.startup_id == startup_id,
               Assessment.status == AssessmentStatus.completed)
        .order_by(Assessment.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _delta_7d(db: Session, startup_id, current: int, now: datetime) -> int:
    cutoff = now - timedelta(days=7)
    baseline = db.execute(
        select(HealthScoreHistory.score)
        .where(HealthScoreHistory.startup_id == startup_id,
               HealthScoreHistory.created_at <= cutoff)
        .order_by(HealthScoreHistory.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if baseline is None:  # nothing older than 7d — fall back to earliest point
        baseline = db.execute(
            select(HealthScoreHistory.score)
            .where(HealthScoreHistory.startup_id == startup_id)
            .order_by(HealthScoreHistory.created_at.asc()).limit(1)
        ).scalar_one_or_none()
    return current - baseline if baseline is not None else 0


def recompute_health_score(db: Session, startup: Startup, *,
                           trigger: str = "assessment_complete") -> HealthScore | None:
    result = latest_completed_result(db, startup.id)
    if result is None:
        return None

    now = datetime.now(UTC)
    dim_scores = {d.value: int(result.dimension_scores.get(d.value, 50)) for d in Dimension}
    overall = weighted_overall(dim_scores)
    band = band_for(overall)

    prev = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    previous_score = prev.score if prev else None
    prior_max = db.execute(
        select(HealthScoreHistory.score).where(HealthScoreHistory.startup_id == startup.id)
        .order_by(HealthScoreHistory.score.desc()).limit(1)
    ).scalar_one_or_none()

    # 1. Replace the signal set
    db.query(HealthSignal).filter_by(startup_id=startup.id).delete()
    for d in Dimension:
        v = dim_scores[d.value]
        db.add(HealthSignal(startup_id=startup.id, dimension=d.value,
                            key=f"assessment.{d.value}", value=v,
                            contribution=round(v * DIMENSION_WEIGHTS[d.value], 2),
                            source_ref=f"assessment:{result.assessment_id}"))

    # 2. Upsert the current score
    stmt = pg_insert(HealthScore).values(
        startup_id=startup.id, score=overall, band=band, dimension_scores=dim_scores,
        source="assessment", config_version=HEALTH_CONFIG_VERSION,
    ).on_conflict_do_update(
        index_elements=["startup_id"],
        set_={"score": overall, "band": band, "dimension_scores": dim_scores,
              "config_version": HEALTH_CONFIG_VERSION},
    )
    db.execute(stmt)

    # 3. Append history
    delta = _delta_7d(db, startup.id, overall, now)
    db.add(HealthScoreHistory(startup_id=startup.id, score=overall, dimension_scores=dim_scores,
                              delta=delta, trigger=trigger, config_version=HEALTH_CONFIG_VERSION))

    # 4. Recommendations — added in Task 5:
    #    generate_recommendations(db, startup.id, dim_scores)

    db.flush()
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()

    # 5. Events
    event_bus.publish("healthscore.updated", {
        "startup_id": str(startup.id), "score": overall, "previous_score": previous_score,
        "band": band, "delta_7d": delta, "computed_at": now.isoformat(),
        "config_version": HEALTH_CONFIG_VERSION})
    if delta <= -5:
        event_bus.publish("healthscore.dropped", {
            "startup_id": str(startup.id), "score": overall, "previous_score": previous_score,
            "delta_7d": delta, "computed_at": now.isoformat()})
    if prior_max is not None and overall > prior_max:
        event_bus.publish("healthscore.record", {
            "startup_id": str(startup.id), "score": overall, "previous_max": prior_max,
            "computed_at": now.isoformat()})
    return hs
```

Rule captured in code: `healthscore.record` fires only when a `prior_max` exists (`prior_max is not None`) and is beaten — so the first-ever score never emits `record`. The history row for the current score is added **before** `prior_max` is read? No — `prior_max` is read **before** the history append, so it reflects only *prior* points. Keep that ordering.

- [ ] **Step 4: Fix the record test per the rule.** Update `test_recompute_emits_updated_and_record` to seed a prior history point below the new score (e.g. `create_history(db, s, score=50, computed_at=now-2d)`) and assert `record` fires; add `test_first_score_is_not_a_record` asserting `record` is absent when there is no prior history.

- [ ] **Step 5: Run the tests — expect pass.** Run: `poetry run pytest tests/services/test_health_recompute.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/services/health_score/service.py tests/services/test_health_recompute.py
git commit -m "feat(health-score): recompute service (signals, upsert, history, events)"
```

---

### Task 5: Recommendation generation & reconciliation

**Files:**
- Create: `app/services/health_score/recommendations.py`
- Modify: `app/services/health_score/service.py` (call `generate_recommendations` at the marked site)
- Test: `tests/services/test_health_recommendations.py`

**Interfaces:**
- Consumes: `RECOMMENDATION_CATALOG`, `DIMENSION_WEIGHTS`; `HealthRecommendation`, `RecommendationStatus`.
- Produces: `generate_recommendations(db: Session, startup_id, dimension_scores: dict[str, int]) -> None` — mutates rows inside the caller's transaction.

- [ ] **Step 1: Write the failing test.** Create `tests/services/test_health_recommendations.py`:

```python
from app.db.models.enums import RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.services.health_score.recommendations import generate_recommendations
from tests.factories import create_recommendation, create_startup, create_user

WEAK = {"product": 30, "market": 30, "money": 30, "legal": 30, "team": 30}
STRONG = {"product": 90, "market": 90, "money": 90, "legal": 90, "team": 90}


def test_generates_for_weak_dimensions(db):
    u = create_user(db); s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, WEAK); db.flush()
    rows = db.query(HealthRecommendation).filter_by(startup_id=s.id).all()
    assert len(rows) == 10  # 2 catalog entries per dimension, all below threshold
    assert all(r.priority >= 1 for r in rows)


def test_no_recommendations_when_all_strong(db):
    u = create_user(db); s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, STRONG); db.flush()
    assert db.query(HealthRecommendation).filter_by(startup_id=s.id).count() == 0


def test_dedupe_by_key_on_regeneration(db):
    u = create_user(db); s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, WEAK); db.flush()
    generate_recommendations(db, s.id, WEAK); db.flush()
    assert db.query(HealthRecommendation).filter_by(startup_id=s.id).count() == 10  # no dupes


def test_never_resurrect_user_dismissed(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_recommendation(db, s, key="money.runway_model",
                          status=RecommendationStatus.dismissed)
    generate_recommendations(db, s.id, WEAK); db.flush()
    row = db.query(HealthRecommendation).filter_by(startup_id=s.id, key="money.runway_model").one()
    assert row.status == RecommendationStatus.dismissed  # not revived to pending


def test_accepted_left_untouched(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_recommendation(db, s, key="money.runway_model",
                          status=RecommendationStatus.accepted, priority=9)
    generate_recommendations(db, s.id, WEAK); db.flush()
    row = db.query(HealthRecommendation).filter_by(startup_id=s.id, key="money.runway_model").one()
    assert row.status == RecommendationStatus.accepted


def test_recovered_pending_is_deleted(db):
    u = create_user(db); s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, WEAK); db.flush()
    generate_recommendations(db, s.id, STRONG); db.flush()  # everything recovered
    remaining = db.query(HealthRecommendation).filter_by(
        startup_id=s.id, status=RecommendationStatus.pending).count()
    assert remaining == 0  # unacted pendings removed
```

- [ ] **Step 2: Run it — expect failure.** Run: `poetry run pytest tests/services/test_health_recommendations.py -q`. Expected: FAIL (module missing).

- [ ] **Step 3: Write the reconciliation.** Create `app/services/health_score/recommendations.py`:

```python
from sqlalchemy.orm import Session

from app.db.models.enums import RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.services.health_score.config import DIMENSION_WEIGHTS, RECOMMENDATION_CATALOG


def generate_recommendations(db: Session, startup_id, dimension_scores: dict[str, int]) -> None:
    # Candidate catalog entries for weak dimensions, ranked by weighted gap.
    candidates = []
    for dim, entries in RECOMMENDATION_CATALOG.items():
        score = dimension_scores.get(dim, 50)
        for e in entries:
            if score < e["triggers_below"]:
                weight = DIMENSION_WEIGHTS[dim]
                rank_val = (e["triggers_below"] - score) * weight
                candidates.append((rank_val, e["estimated_lift"], dim, e))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    candidate_keys = {e["key"] for *_, e in candidates}

    existing = {r.key: r for r in
                db.query(HealthRecommendation).filter_by(startup_id=startup_id).all()}

    # Delete unacted pendings whose dimension recovered (no longer a candidate).
    for key, row in list(existing.items()):
        if row.status == RecommendationStatus.pending and key not in candidate_keys:
            db.delete(row)
            del existing[key]

    for priority, (_, _, dim, e) in enumerate(candidates, start=1):
        row = existing.get(e["key"])
        if row is None:
            db.add(HealthRecommendation(
                startup_id=startup_id, dimension=dim, key=e["key"], title=e["title"],
                body=e["body"], estimated_lift=e["estimated_lift"], effort=e["effort"],
                status=RecommendationStatus.pending, priority=priority))
        elif row.status == RecommendationStatus.dismissed:
            continue  # never resurrect a user decision
        else:  # pending or accepted → refresh priority/lift, keep status
            row.priority = priority
            row.estimated_lift = e["estimated_lift"]
    db.flush()
```

- [ ] **Step 4: Wire it into recompute.** In `app/services/health_score/service.py`, replace the Task-4 comment block with:

```python
    from app.services.health_score.recommendations import generate_recommendations
    generate_recommendations(db, startup.id, dim_scores)
```

(placed where the `# 4. Recommendations` comment sits, before `db.flush()`).

- [ ] **Step 5: Run the tests — expect pass.** Run: `poetry run pytest tests/services/test_health_recommendations.py tests/services/test_health_recompute.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/services/health_score/recommendations.py app/services/health_score/service.py tests/services/test_health_recommendations.py
git commit -m "feat(health-score): rule-based recommendation generation + reconciliation"
```

---

### Task 6: Wire inline recompute into assessment-complete; retire the stub job

**Files:**
- Modify: `app/services/assessment/service.py` (around line 197)
- Modify: `docs/sop/2026-08-16-assessment.md`, `docs/sop/2026-08-15-onboarding.md` (one-line notes)
- Test: `tests/services/test_health_recompute.py` (add an integration test through `complete_assessment`)

**Interfaces:**
- Consumes: `recompute_health_score`, `complete_assessment`.
- Produces: after `complete_assessment` on an `initial` assessment, a `health_scores` row exists for the startup.

- [ ] **Step 1: Write the failing integration test.** Add to `tests/services/test_health_recompute.py` (reuse the assessment test helpers — mirror how `tests/api/test_assessments*.py` drives a full answer+complete; if a helper `complete_via_service` exists, use it, else answer every scored question then call `complete_assessment`):

```python
def test_complete_assessment_triggers_health_score(db):
    from app.db.models.health_score import HealthScore
    # Arrange a fully-answered in-progress initial assessment for a startup, then:
    #   complete_assessment(db, startup, assessment, user)
    # Assert a HealthScore row now exists (score derived from the assessment).
    ...
```

Fill the `...` by following the existing assessment completion test setup in `tests/` (find it with `grep -rn "complete_assessment(" tests/`). The assertion: `assert db.query(HealthScore).filter_by(startup_id=startup.id).count() == 1`.

- [ ] **Step 2: Run it — expect failure.** Run: `poetry run pytest tests/services/test_health_recompute.py::test_complete_assessment_triggers_health_score -q`. Expected: FAIL (no HealthScore row — not yet wired).

- [ ] **Step 3: Replace the stub enqueue with an inline recompute.** In `app/services/assessment/service.py`, change:

```python
    job_dispatcher.enqueue(db, "healthscore.recalculate", job_payload, startup.id)
    job_dispatcher.enqueue(db, "roadmap.replan", job_payload, startup.id)
```

to:

```python
    from app.services.health_score.service import recompute_health_score
    recompute_health_score(db, startup, trigger="assessment_complete")
    job_dispatcher.enqueue(db, "roadmap.replan", job_payload, startup.id)
```

(Keep `roadmap.replan` — Roadmap/Module 05 still consumes it. Only the `healthscore.recalculate` stub is retired.)

- [ ] **Step 4: Run it — expect pass.** Run: `poetry run pytest tests/services/test_health_recompute.py -q`. Expected: PASS. Then run the assessment suite to confirm no regression: `poetry run pytest tests/ -k assessment -q`. Expected: PASS.

- [ ] **Step 5: Note the retirement in the SOPs.** In `docs/sop/2026-08-16-assessment.md` and `docs/sop/2026-08-15-onboarding.md`, add one line under follow-ups: "As of Module 06, the `healthscore.recalculate`/`healthscore.initialize` stub jobs are retired — the Health Score is recomputed inline at assessment-complete (see `docs/sop/2026-08-19-health-score.md`)."

- [ ] **Step 6: Commit.**

```bash
git add app/services/assessment/service.py tests/services/test_health_recompute.py docs/sop/2026-08-16-assessment.md docs/sop/2026-08-15-onboarding.md
git commit -m "feat(health-score): recompute inline at assessment-complete; retire stub job"
```

---

### Task 7: `GET /health-score` overview (pending + lazy-on-read)

**Files:**
- Create: `app/api/v1/endpoints/health_score.py`
- Modify: `app/api/v1/api.py`
- Modify: `app/services/health_score/service.py` (add `get_overview`)
- Test: `tests/api/test_health_score.py`

**Interfaces:**
- Consumes: `require_workspace`, `get_verified_user`, `success_response`, `recompute_health_score`, `latest_completed_result`.
- Produces: `service.get_overview(db, startup) -> dict`; route `GET /api/v1/health-score`.

- [ ] **Step 1: Write the failing API test.** Create `tests/api/test_health_score.py` (follow the auth/header pattern in `tests/api/test_assessments*.py` — build a verified founder + workspace header helper; reuse whatever fixture those tests use):

```python
def test_overview_pending_before_assessment(client, founder_ctx):
    r = client.get("/api/v1/health-score", headers=founder_ctx.headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "pending_assessment"
    assert data["score"] is None


def test_overview_ok_after_assessment(client, founder_ctx, db):
    # complete an assessment for founder_ctx.startup (helper), then:
    r = client.get("/api/v1/health-score", headers=founder_ctx.headers)
    data = r.json()["data"]
    assert data["status"] == "ok"
    assert isinstance(data["score"], int)
    assert len(data["dimensions"]) == 5
    assert len(data["top_recommendations"]) <= 3


def test_overview_lazy_computes_when_row_missing(client, founder_ctx, db):
    # Arrange a completed AssessmentResult directly (no inline recompute), assert GET creates the row.
    ...
```

Replace `founder_ctx`/`client` with the actual fixtures the existing API tests use (discover via `grep -rn "def client\|X-Workspace-Id\|headers=" tests/api/test_assessments*.py`).

- [ ] **Step 2: Run it — expect failure.** Run: `poetry run pytest tests/api/test_health_score.py -q`. Expected: FAIL (route 404 / module missing).

- [ ] **Step 3: Add `get_overview` to the service.** Append to `app/services/health_score/service.py`:

```python
from app.db.models.health_score import HealthRecommendation
from app.db.models.enums import RecommendationStatus
from app.services.health_score.config import DIMENSION_LABELS


def get_overview(db: Session, startup: Startup) -> dict:
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    if hs is None:
        # lazy-on-read: compute if an assessment exists, else pending
        if latest_completed_result(db, startup.id) is not None:
            hs = recompute_health_score(db, startup, trigger="lazy_read")
            db.commit()
    if hs is None:
        return {"status": "pending_assessment", "score": None, "band": None,
                "message": "Complete your kickoff assessment to generate your Health Score.",
                "dimensions": [], "top_recommendations": []}
    now = datetime.now(UTC)
    delta = _delta_7d(db, startup.id, hs.score, now)
    dims = [{"key": k, "label": DIMENSION_LABELS[k], "score": v, "band": band_for(v)}
            for k, v in hs.dimension_scores.items()]
    recs = (db.query(HealthRecommendation)
            .filter_by(startup_id=startup.id, status=RecommendationStatus.pending)
            .order_by(HealthRecommendation.priority.asc()).limit(3).all())
    weakest = min(hs.dimension_scores, key=lambda k: hs.dimension_scores[k])
    summary = (f"Your Health Score is {hs.score} ({hs.band.replace('_', ' ')}). "
               f"Your weakest area is {DIMENSION_LABELS[weakest]}.")
    return {"status": "ok", "score": hs.score, "band": hs.band, "delta_7d": delta,
            "computed_at": hs.updated_at.isoformat(), "config_version": hs.config_version,
            "dimensions": dims,
            "top_recommendations": [_serialize_rec(r) for r in recs], "summary": summary}


def _serialize_rec(r) -> dict:
    return {"id": str(r.id), "dimension": r.dimension, "key": r.key, "title": r.title,
            "body": r.body, "estimated_lift": r.estimated_lift, "effort": r.effort.value,
            "status": r.status.value, "priority": r.priority}
```

- [ ] **Step 4: Create the router.** Create `app/api/v1/endpoints/health_score.py`:

```python
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.services.health_score import service as hs_service

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


@router.get("")
def get_health_score(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    return success_response(hs_service.get_overview(db, startup))
```

- [ ] **Step 5: Register the router.** In `app/api/v1/api.py`, add `health_score` to the endpoints import and: `api_router.include_router(health_score.router, prefix="/health-score", tags=["health-score"])`.

- [ ] **Step 6: Run the tests — expect pass.** Run: `poetry run pytest tests/api/test_health_score.py -q`. Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add app/api/v1/endpoints/health_score.py app/api/v1/api.py app/services/health_score/service.py tests/api/test_health_score.py
git commit -m "feat(health-score): GET /health-score overview (pending + lazy-on-read)"
```

---

### Task 8: `GET /dimensions/{dim}` + `GET /history`

**Files:**
- Modify: `app/api/v1/endpoints/health_score.py`, `app/services/health_score/service.py`
- Test: `tests/api/test_health_score.py` (extend)

**Interfaces:**
- Produces: `service.get_dimension(db, startup, dim) -> dict` (raises `NotFound` for a bad dim), `service.get_history(db, startup, range_key) -> list[dict]`; routes `GET /health-score/dimensions/{dim}`, `GET /health-score/history`.

- [ ] **Step 1: Write the failing tests.** Add to `tests/api/test_health_score.py`:

```python
def test_dimension_detail(client, founder_ctx, db):
    # after an assessment:
    r = client.get("/api/v1/health-score/dimensions/money", headers=founder_ctx.headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["key"] == "money"
    assert data["label"] == "Financial"
    assert isinstance(data["signals"], list)


def test_dimension_unknown_key_404(client, founder_ctx):
    r = client.get("/api/v1/health-score/dimensions/nope", headers=founder_ctx.headers)
    assert r.status_code == 404


def test_history_range(client, founder_ctx, db):
    r = client.get("/api/v1/health-score/history?range=30d", headers=founder_ctx.headers)
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)


def test_history_bad_range_422(client, founder_ctx):
    r = client.get("/api/v1/health-score/history?range=bogus", headers=founder_ctx.headers)
    assert r.status_code == 422
```

- [ ] **Step 2: Run — expect failure.** Run: `poetry run pytest tests/api/test_health_score.py -k "dimension or history" -q`. Expected: FAIL.

- [ ] **Step 3: Add service functions.** Append to `app/services/health_score/service.py`:

```python
from datetime import timedelta

from app.db.models.health_score import HealthSignal
from app.core.errors import NotFound

_RANGES = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def get_dimension(db: Session, startup: Startup, dim: str) -> dict:
    if dim not in DIMENSION_LABELS:
        raise NotFound()
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    score = hs.dimension_scores.get(dim) if hs else None
    signals = db.query(HealthSignal).filter_by(startup_id=startup.id, dimension=dim).all()
    recs = (db.query(HealthRecommendation)
            .filter_by(startup_id=startup.id, dimension=dim, status=RecommendationStatus.pending)
            .order_by(HealthRecommendation.priority.asc()).all())
    trend = [{"score": h.dimension_scores.get(dim), "computed_at": h.created_at.isoformat()}
             for h in db.query(HealthScoreHistory).filter_by(startup_id=startup.id)
             .order_by(HealthScoreHistory.created_at.asc()).all()]
    return {"key": dim, "label": DIMENSION_LABELS[dim], "score": score,
            "band": band_for(score) if score is not None else None,
            "signals": [{"key": s.key, "value": float(s.value),
                         "contribution": float(s.contribution), "source_ref": s.source_ref}
                        for s in signals],
            "trend": trend, "recommendations": [_serialize_rec(r) for r in recs]}


def get_history(db: Session, startup: Startup, range_key: str) -> list[dict]:
    days = _RANGES[range_key]
    q = db.query(HealthScoreHistory).filter_by(startup_id=startup.id)
    if days is not None:
        q = q.filter(HealthScoreHistory.created_at >= datetime.now(UTC) - timedelta(days=days))
    rows = q.order_by(HealthScoreHistory.created_at.asc()).all()
    return [{"score": h.score, "dimension_scores": h.dimension_scores, "delta": h.delta,
             "computed_at": h.created_at.isoformat()} for h in rows]
```

- [ ] **Step 4: Add the routes.** In `app/api/v1/endpoints/health_score.py` add (import `Query`, `AppError`):

```python
@router.get("/dimensions/{dim}")
def get_dimension(dim: str, membership: Membership = Depends(require_workspace),  # noqa: B008
                  user: User = Depends(get_verified_user),  # noqa: B008
                  db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    return success_response(hs_service.get_dimension(db, _startup(db, membership), dim))


@router.get("/history")
def get_history(range: str = Query("30d"),  # noqa: B008
                membership: Membership = Depends(require_workspace),  # noqa: B008
                user: User = Depends(get_verified_user),  # noqa: B008
                db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    if range not in ("7d", "30d", "90d", "all"):
        raise AppError("VALIDATION_ERROR", "Unknown range.", 422,
                       field_errors=[{"field": "range", "message": "Use 7d, 30d, 90d, or all."}])
    return success_response(hs_service.get_history(db, _startup(db, membership), range))
```

- [ ] **Step 5: Run — expect pass.** Run: `poetry run pytest tests/api/test_health_score.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/api/v1/endpoints/health_score.py app/services/health_score/service.py tests/api/test_health_score.py
git commit -m "feat(health-score): dimension drill-down + history endpoints"
```

---

### Task 9: `GET /benchmarks` (cohort gate) + `GET /recommendations`

**Files:**
- Modify: `app/api/v1/endpoints/health_score.py`, `app/services/health_score/service.py`
- Test: `tests/api/test_health_score.py` (extend)

**Interfaces:**
- Produces: `service.get_benchmarks(db, startup) -> dict`, `service.list_recommendations(db, startup, status_filter) -> list[dict]`; routes `GET /health-score/benchmarks`, `GET /health-score/recommendations`.

- [ ] **Step 1: Write the failing tests.** Add to `tests/api/test_health_score.py`:

```python
def test_benchmarks_insufficient_cohort(client, founder_ctx):
    r = client.get("/api/v1/health-score/benchmarks", headers=founder_ctx.headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "insufficient_data"
    assert data["percentiles"] is None
    assert "min_cohort_size" in data


def test_recommendations_list_default_pending(client, founder_ctx, db):
    # after an assessment with weak dimensions:
    r = client.get("/api/v1/health-score/recommendations", headers=founder_ctx.headers)
    assert r.status_code == 200
    assert all(x["status"] == "pending" for x in r.json()["data"])


def test_recommendations_bad_status_422(client, founder_ctx):
    r = client.get("/api/v1/health-score/recommendations?status=nope", headers=founder_ctx.headers)
    assert r.status_code == 422
```

- [ ] **Step 2: Run — expect failure.** Run: `poetry run pytest tests/api/test_health_score.py -k "benchmark or recommendations_list or bad_status" -q`. Expected: FAIL.

- [ ] **Step 3: Add service functions.** Append to `app/services/health_score/service.py`:

```python
from app.services.health_score.config import MIN_COHORT_SIZE


def get_benchmarks(db: Session, startup: Startup) -> dict:
    cohort = {"stage": startup.stage.value if startup.stage else None,
              "industry": startup.industry}
    # Cohort size counts startups sharing stage+industry that have a health score.
    peers = (db.query(HealthScore).join(Startup, Startup.id == HealthScore.startup_id)
             .filter(Startup.stage == startup.stage, Startup.industry == startup.industry).count())
    if peers < MIN_COHORT_SIZE:
        return {"status": "insufficient_data", "cohort": cohort,
                "min_cohort_size": MIN_COHORT_SIZE, "percentiles": None}
    # Real aggregation deferred; when a cohort exists, compute here. Until then, gate returns above.
    return {"status": "insufficient_data", "cohort": cohort,
            "min_cohort_size": MIN_COHORT_SIZE, "percentiles": None}


def list_recommendations(db: Session, startup: Startup, status_filter: str | None) -> list[dict]:
    q = db.query(HealthRecommendation).filter_by(startup_id=startup.id)
    if status_filter:
        q = q.filter(HealthRecommendation.status == RecommendationStatus(status_filter))
    else:
        q = q.filter(HealthRecommendation.status == RecommendationStatus.pending)
    rows = q.order_by(HealthRecommendation.priority.asc()).all()
    return [_serialize_rec(r) for r in rows]
```

- [ ] **Step 4: Add the routes.** In `app/api/v1/endpoints/health_score.py`:

```python
@router.get("/benchmarks")
def get_benchmarks(membership: Membership = Depends(require_workspace),  # noqa: B008
                   user: User = Depends(get_verified_user),  # noqa: B008
                   db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    return success_response(hs_service.get_benchmarks(db, _startup(db, membership)))


@router.get("/recommendations")
def list_recommendations(status: str | None = Query(None),  # noqa: B008
                         membership: Membership = Depends(require_workspace),  # noqa: B008
                         user: User = Depends(get_verified_user),  # noqa: B008
                         db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    if status is not None and status not in ("pending", "accepted", "dismissed"):
        raise AppError("VALIDATION_ERROR", "Unknown status.", 422,
                       field_errors=[{"field": "status", "message": "Use pending, accepted, or dismissed."}])
    return success_response(hs_service.list_recommendations(db, _startup(db, membership), status))
```

- [ ] **Step 5: Run — expect pass.** Run: `poetry run pytest tests/api/test_health_score.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/api/v1/endpoints/health_score.py app/services/health_score/service.py tests/api/test_health_score.py
git commit -m "feat(health-score): benchmarks cohort-gate + recommendations list"
```

---

### Task 10: `POST …/accept` + `…/dismiss` (idempotency / 409 / founder gate)

**Files:**
- Modify: `app/api/v1/endpoints/health_score.py`, `app/services/health_score/service.py`
- Modify: `app/core/errors.py` (add `RecommendationResolved`)
- Test: `tests/api/test_health_recommendations.py`

**Interfaces:**
- Produces: `service.resolve_recommendation(db, startup, rec_id, target: RecommendationStatus) -> dict`; routes `POST /health-score/recommendations/{id}/accept`, `.../dismiss`; error `RecommendationResolved` (409).

- [ ] **Step 1: Add the error class.** In `app/core/errors.py`, following the existing subclass pattern:

```python
class RecommendationResolved(AppError):  # noqa: N818
    code, http_status = "RECOMMENDATION_RESOLVED", 409
    message = "That recommendation has already been actioned."
```

- [ ] **Step 2: Write the failing API tests.** Create `tests/api/test_health_recommendations.py`:

```python
from app.db.models.enums import RecommendationStatus


def _first_rec_id(client, ctx):
    return client.get("/api/v1/health-score/recommendations", headers=ctx.headers).json()["data"][0]["id"]


def test_accept_then_idempotent(client, founder_ctx, db):
    rid = _first_rec_id(client, founder_ctx)
    r1 = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=founder_ctx.headers)
    assert r1.status_code == 200 and r1.json()["data"]["status"] == "accepted"
    r2 = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=founder_ctx.headers)
    assert r2.status_code == 200  # same-status idempotent


def test_cross_transition_409(client, founder_ctx, db):
    rid = _first_rec_id(client, founder_ctx)
    client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=founder_ctx.headers)
    r = client.post(f"/api/v1/health-score/recommendations/{rid}/dismiss", headers=founder_ctx.headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RECOMMENDATION_RESOLVED"


def test_member_cannot_accept_403(client, member_ctx, founder_ctx):
    rid = _first_rec_id(client, founder_ctx)
    r = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=member_ctx.headers)
    assert r.status_code == 403


def test_cross_workspace_404(client, founder_ctx, other_ctx):
    rid = _first_rec_id(client, founder_ctx)
    r = client.post(f"/api/v1/health-score/recommendations/{rid}/accept", headers=other_ctx.headers)
    assert r.status_code == 404
```

Use the same fixtures as Task 7; add `member_ctx` (a `team_member` in the founder's workspace) and `other_ctx` (a founder of a different workspace) — follow how `tests/api/test_assessments*.py` builds cross-tenant + role fixtures.

- [ ] **Step 3: Run — expect failure.** Run: `poetry run pytest tests/api/test_health_recommendations.py -q`. Expected: FAIL.

- [ ] **Step 4: Add the service function.** Append to `app/services/health_score/service.py`:

```python
from app.core.errors import RecommendationResolved


def resolve_recommendation(db: Session, startup: Startup, rec_id, target: RecommendationStatus) -> dict:
    row = (db.query(HealthRecommendation)
           .filter_by(id=rec_id, startup_id=startup.id).first())
    if row is None:
        raise NotFound()  # uniform 404 — never leak cross-tenant existence
    if row.status == target:
        return _serialize_rec(row)  # idempotent
    if row.status != RecommendationStatus.pending:
        raise RecommendationResolved()  # accepted<->dismissed cross-transition
    row.status = target
    row.resolved_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _serialize_rec(row)
```

- [ ] **Step 5: Add the routes.** In `app/api/v1/endpoints/health_score.py` (import `uuid`, `RecommendationStatus`, `require_role`, `MembershipRole`):

```python
@router.post("/recommendations/{rec_id}/accept")
def accept_recommendation(rec_id: uuid.UUID,
                          membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
                          user: User = Depends(get_verified_user),  # noqa: B008
                          db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    return success_response(hs_service.resolve_recommendation(
        db, _startup(db, membership), rec_id, RecommendationStatus.accepted))


@router.post("/recommendations/{rec_id}/dismiss")
def dismiss_recommendation(rec_id: uuid.UUID,
                           membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
                           user: User = Depends(get_verified_user),  # noqa: B008
                           db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    return success_response(hs_service.resolve_recommendation(
        db, _startup(db, membership), rec_id, RecommendationStatus.dismissed))
```

- [ ] **Step 6: Run — expect pass.** Run: `poetry run pytest tests/api/test_health_recommendations.py -q`. Expected: PASS.

- [ ] **Step 7: Full suite + lint/type.** Run: `poetry run pytest -q && poetry run black --check . && poetry run isort --check . && poetry run ruff check . && poetry run mypy app`. Expected: all pass.

- [ ] **Step 8: Commit.**

```bash
git add app/core/errors.py app/api/v1/endpoints/health_score.py app/services/health_score/service.py tests/api/test_health_recommendations.py
git commit -m "feat(health-score): accept/dismiss recommendations (idempotency, 409, founder gate)"
```

---

### Task 11: Live E2E + SOP + FE integration guide + checklist reconcile

**Files:**
- Create: `e2e/test_health_score.py`
- Modify: `e2e/test_smoke.py` (add the 6 health-score paths to the openapi assertion)
- Create: `docs/sop/2026-08-19-health-score.md`, `docs/fe-integration-guide-health-score.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:**
- Consumes: the whole HTTP surface via the live server (`make e2e`).

- [ ] **Step 1: Add health-score paths to the smoke openapi assertion.** In `e2e/test_smoke.py`, add to the `test_openapi_served` path list: `/api/v1/health-score`, `/api/v1/health-score/dimensions/{dim}`, `/api/v1/health-score/history`, `/api/v1/health-score/benchmarks`, `/api/v1/health-score/recommendations`, `/api/v1/health-score/recommendations/{rec_id}/accept`.

- [ ] **Step 2: Write the E2E journey.** Create `e2e/test_health_score.py`, reusing `e2e/conftest.py` fixtures (`http`, `make_verified_user`, `mailbox`) and the onboarding+assessment helpers from `e2e/test_assessment.py` (import or replicate the "onboard + complete assessment" helper). Capture each response body to `e2e/_captures/health_score/*.json` for the FE guide:

```python
def test_health_score_journey(http, make_verified_user, capture):
    # 1. Founder signs up, verifies, onboards, and BEFORE the assessment:
    #    GET /health-score -> 200 status == "pending_assessment"   [capture: overview_pending.json]
    # 2. Complete the kickoff assessment (answer all scored questions, POST /complete).
    # 3. GET /health-score -> status == "ok", score is int, 5 dimensions  [capture: overview_ok.json]
    # 4. GET /health-score/dimensions/money -> label "Financial", signals non-empty [capture]
    # 5. GET /health-score/history?range=all -> >= 1 point  [capture: history.json]
    # 6. GET /health-score/recommendations -> non-empty, all pending  [capture: recommendations.json]
    # 7. POST recommendations/{id}/accept -> status "accepted"  [capture: accept.json]
    #    POST the same id /dismiss -> 409 RECOMMENDATION_RESOLVED  [capture: conflict.json]
    # 8. GET /health-score/benchmarks -> status "insufficient_data"  [capture: benchmarks.json]
    # 9. Second founder (other workspace) POST accept on the first's rec id -> 404
    ...
```

Fill `...` following the exact request/verify style of `e2e/test_assessment.py`. Add a `capture` fixture (or a small local helper) that writes `resp.json()` to a file so the FE guide is derived from real bodies. If `e2e/conftest.py` lacks such a helper, add a minimal one there.

- [ ] **Step 3: Run the live E2E.** Run: `make e2e`. Expected: sanity (alembic upgrade head incl. `0005`) + smoke + the health-score journey all pass against the real server on the isolated DB.

- [ ] **Step 4: Write the SOP.** Create `docs/sop/2026-08-19-health-score.md` covering: what shipped (the 7 routes + inline recompute), why (Module 06), how (inline + lazy, signals-as-projection, rule-based recs, cohort-gated benchmarks, config versioning), what's involved (files/paths, migration `0005`, the assessment-complete wiring change), verification (unit counts + `make e2e`), operate/rollback (downgrade `0005`; the retired stub job), and follow-ups (async worker, real benchmark aggregation, AI summary/recs in Module 03).

- [ ] **Step 5: Write the FE integration guide.** Create `docs/fe-integration-guide-health-score.md` per the FE-guide standard — every payload **pasted verbatim from `e2e/_captures/health_score/*.json`**: overview (pending + ok), dimension drill-down, history, benchmarks gate, recommendations list, accept, and the 409 conflict body. Include: the pending↔ok state machine, "re-fetch after completing the assessment" polling guidance, the founder-only note on accept/dismiss, the uniform-404 cross-tenant note, and the explicit UX note that `estimated_lift` is a heuristic ("est. +N", not a promise). End with a verification table marking each row verified-live via `make e2e`.

- [ ] **Step 6: Reconcile the checklist.** In `docs/checklist/PROJECT_CHECKLIST.md`, move Module 06 to ✅, check every item, and update the snapshot counts.

- [ ] **Step 7: Commit.**

```bash
git add e2e/test_health_score.py e2e/test_smoke.py e2e/conftest.py e2e/_captures docs/sop/2026-08-19-health-score.md docs/fe-integration-guide-health-score.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(health-score): live E2E journey + SOP + FE integration guide"
```

---

## Self-Review (completed)

**Spec coverage:** every §6 route → Tasks 7–10; §3 data model → Tasks 1–2; §4 compute/weights/bands → Tasks 3–4; §4.3 recommendations → Task 5; §5 events → Task 4; §5 errors → Tasks 8–10; inline wiring + retired jobs → Task 6; §7 testing (unit + E2E + FE guide) → all tasks + Task 11. Pending/lazy/benchmarks empty-states → Tasks 7/9. No spec section is left without a task.

**Type consistency:** `recompute_health_score(db, startup, *, trigger)` and `generate_recommendations(db, startup_id, dimension_scores)` are used identically where referenced; `_serialize_rec` shape is shared by overview, dimension, and list endpoints; `band_for`/`weighted_overall` signatures match across Tasks 3/4/8.

**Known follow-ups (non-blocking, record in the SOP):** real benchmark aggregation past the cohort gate; async worker for the retired jobs (Module 05); AI summary/recommendations (Module 03); a concurrency test for two near-simultaneous recomputes is unnecessary today because the only writer (assessment-complete) is already atomically single-winner, but note it for when a second producer lands.
