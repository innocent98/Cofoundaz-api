# Module 10 Marketing Hub — Slice 5 (Performance Analytics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Performance Analytics to the Marketing Hub — a `marketing_metrics` time-series store, a manual/API ingestion endpoint, an aggregation read (traffic-by-week, CAC-by-channel, conversion funnel, campaign leaderboard), and fills for the Overview stubs. Completes Module 10.

**Architecture:** One new table `marketing_metrics` (per-startup, `ts`/`channel`/`campaign_id`/`metric`/`value`), a new `MarketingMetricName` enum, migration `0038`. Ingestion (`POST /marketing/metrics`) + aggregation (`GET /marketing/analytics?range=`) in a new `app/services/marketing/analytics.py`. `overview()` gains three fills.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Postgres, Alembic, Poetry, pytest.

**Spec:** docs/superpowers/specs/2026-09-29-module-10-marketing-slice5-design.md

## Global Constraints

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by `startup_id` (from `membership.startup_id`, never the body).
- **RBAC:** every route behind `_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)`; other roles → 403.
- **Endpoint conventions (match `marketing.py`):** `response_model=dict[str, Any]`, return `success_response(...)`; POST commits; deps ordered `payload, membership=Depends(_marketing), user=Depends(get_verified_user), db=Depends(get_db)` with `# noqa: B008`.
- **Validation** via `_validation(field, message)` from `app.services.marketing.service` (422 + `field_errors`). `AppError.http_status` (not `.status_code`); `get_db` does NOT auto-commit.
- **CodeQL (required):** no mutating client call inside an `assert` (bind to a var first); no implicit string-concat in a list literal.
- **Migration:** id ≤ 32 chars (`0038_marketing_metrics`, 22); down_revision `0037_seo_tools`. Migration-head test asserts **single head + revision-in-`alembic history`**, NOT "my revision is THE head".
- **Money/counts:** metric `value` is an integer; **spend is in cents**.
- **No AI attribution** in any commit message.

## Review Focus

- **Ingestion validation:** out-of-enum `metric`/`channel`, negative `value`, or a `campaign_id` from another startup → 422 (not 500, not stored). — Task 2.
- **Empty-data analytics:** zero metrics → empty lists + zeroed funnel with 0.0 rates (no `ZeroDivisionError`); a channel with spend but 0 conversions → `cac: null`. — Task 3.
- **Range filter + weekly bucketing:** out-of-range points excluded; `traffic_by_week` buckets by ISO week across a month boundary. — Task 3.
- **Leaderboard tenancy + FK-null:** only the caller's campaigns; a metric whose campaign was deleted (SET NULL) doesn't crash. — Task 3.
- **Overview fills:** `top_channel_by_conversions` null with no metrics; `ai_content_ideas` 0 with no ready content_gap; `active_campaigns` counts only `active`. — Task 4.
- **RBAC:** 403 for mentor/investor on both routes. — Tasks 2/3.

---

## Task 1: Enum + MarketingMetric model + migration 0038

**Files:**
- Modify: `app/db/models/enums.py` (add `MarketingMetricName`)
- Modify: `app/db/models/marketing.py` (add `MarketingMetric`)
- Create: `alembic/versions/0038_marketing_metrics.py`
- Test: `tests/db/test_marketing_metric_model.py`, `tests/test_marketing_metrics_migration.py`

**Interfaces (produced):** `MarketingMetricName` (visits/impressions/clicks/conversions/spend); `MarketingMetric` (startup_id, ts Date, channel ChannelKey?, campaign_id uuid?, metric, value int).

- [ ] **Step 1: Write failing model + enum tests**

```python
# tests/db/test_marketing_metric_model.py
from datetime import date

from app.db.models.enums import ChannelKey, MarketingMetricName
from app.db.models.marketing import MarketingMetric
from tests.factories import create_startup, create_user


def test_metric_name_values():
    assert {m.value for m in MarketingMetricName} == {
        "visits", "impressions", "clicks", "conversions", "spend",
    }


def test_marketing_metric_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = MarketingMetric(
        startup_id=s.id, ts=date(2026, 9, 1), channel=ChannelKey.email,
        metric=MarketingMetricName.visits, value=1200,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.value == 1200 and row.channel == ChannelKey.email
    assert row.campaign_id is None  # nullable
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_marketing_metric_model.py -v`
Expected: FAIL (ImportError on `MarketingMetricName`/`MarketingMetric`).

- [ ] **Step 3: Add the enum**

`app/db/models/enums.py` (append):

```python
class MarketingMetricName(enum.StrEnum):
    visits = "visits"
    impressions = "impressions"
    clicks = "clicks"
    conversions = "conversions"
    spend = "spend"
```

- [ ] **Step 4: Add the model**

`app/db/models/marketing.py` (append; `Date`, `Integer`, `Enum`, `ForeignKey`, `PGUUID`, `Mapped`, `mapped_column` already imported; add `MarketingMetricName` to the enums import):

```python
class MarketingMetric(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketing_metrics"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ts: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[ChannelKey | None] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    metric: Mapped[MarketingMetricName] = mapped_column(
        Enum(MarketingMetricName, native_enum=False, length=12), nullable=False
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_marketing_metrics_startup_ts", "startup_id", "ts"),)
```

Ensure `date` is imported from `datetime` (it is) and `Index` from `sqlalchemy` (add to the import line if missing).

- [ ] **Step 5: Create the migration**

`alembic/versions/0038_marketing_metrics.py`:

```python
"""marketing metrics

Revision ID: 0038_marketing_metrics
Revises: 0037_seo_tools
Create Date: 2026-09-29

Module 10 Slice 5 (Performance Analytics): one new table marketing_metrics — a per-startup
time-series of (ts, channel?, campaign_id?, metric, value) points powering the analytics
aggregation. Additive; no lock on existing tables. New MarketingMetricName enum stored inline
(native_enum=False).
"""

from alembic import op
import sqlalchemy as sa

revision = "0038_marketing_metrics"
down_revision = "0037_seo_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_metrics",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("organic_social", "paid_social", "search", "email", "content_seo",
                    "partnerships", "events", "referral",
                    name="channelkey", native_enum=False, length=20),
            nullable=True,
        ),
        sa.Column("campaign_id", sa.UUID(), nullable=True),
        sa.Column(
            "metric",
            sa.Enum("visits", "impressions", "clicks", "conversions", "spend",
                    name="marketingmetricname", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_marketing_metrics_startup_id_startups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name=op.f("fk_marketing_metrics_campaign_id_campaigns"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_metrics")),
    )
    op.create_index(op.f("ix_marketing_metrics_startup_id"), "marketing_metrics", ["startup_id"], unique=False)
    op.create_index(op.f("ix_marketing_metrics_campaign_id"), "marketing_metrics", ["campaign_id"], unique=False)
    op.create_index("ix_marketing_metrics_startup_ts", "marketing_metrics", ["startup_id", "ts"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_marketing_metrics_startup_ts", table_name="marketing_metrics")
    op.drop_index(op.f("ix_marketing_metrics_campaign_id"), table_name="marketing_metrics")
    op.drop_index(op.f("ix_marketing_metrics_startup_id"), table_name="marketing_metrics")
    op.drop_table("marketing_metrics")
```

- [ ] **Step 6: Migration round-trip + single-head test (robust form)**

```python
# tests/test_marketing_metrics_migration.py
import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_migration_chain_single_head_includes_0038():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert "0038_marketing_metrics" in history.stdout


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0037_seo_tools")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr
```

- [ ] **Step 7: Run tests + drift check**

Run: `poetry run pytest tests/db/test_marketing_metric_model.py tests/test_marketing_metrics_migration.py -v && poetry run alembic upgrade head && poetry run alembic check`
Expected: PASS; `alembic check` = "No new upgrade operations detected."; single head `0038_marketing_metrics`.

- [ ] **Step 8: Commit**

```bash
git add app/db/models/enums.py app/db/models/marketing.py alembic/versions/0038_marketing_metrics.py tests/db/test_marketing_metric_model.py tests/test_marketing_metrics_migration.py
git commit -m "feat(marketing): marketing_metrics table + MarketingMetricName enum (migration 0038)"
```

---

## Task 2: Metrics ingestion (schemas + service + endpoint)

**Files:**
- Modify: `app/schemas/marketing.py` (`MetricPoint`, `MetricsIngest`)
- Create: `app/services/marketing/analytics.py` (`ingest_metrics`)
- Modify: `app/api/v1/endpoints/marketing.py` (`POST /marketing/metrics`)
- Test: `tests/api/test_marketing_analytics.py` (new)

**Interfaces (produced):** `MetricPoint`, `MetricsIngest`; `analytics.ingest_metrics(db, *, startup_id, data) -> int`; route `POST /marketing/metrics`.

- [ ] **Step 1: Write failing tests**

```python
# tests/api/test_marketing_analytics.py
from datetime import UTC, date, datetime

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


def test_ingest_metrics_bulk(client, db):
    _u, _s, h = _member(db)
    body = {"points": [
        {"ts": "2026-09-01", "channel": "email", "metric": "visits", "value": 1200},
        {"ts": "2026-09-01", "channel": "email", "metric": "conversions", "value": 30},
    ]}
    resp = client.post(f"{BASE}/metrics", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] == 2


def test_ingest_rejects_negative_value(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/metrics", json={"points": [
        {"ts": "2026-09-01", "metric": "visits", "value": -5}]}, headers=h)
    assert resp.status_code == 422, resp.text


def test_ingest_rejects_foreign_campaign(client, db):
    _u, _s, h = _member(db)
    _u2, s2, _h2 = _member(db)  # a different startup — build a campaign there
    from app.db.models.marketing import Campaign
    from app.db.models.enums import CampaignObjective, CampaignStatus
    other = Campaign(startup_id=s2.id, name="X", objective=CampaignObjective.leads, status=CampaignStatus.draft)
    db.add(other)
    db.flush()
    resp = client.post(f"{BASE}/metrics", json={"points": [
        {"ts": "2026-09-01", "campaign_id": str(other.id), "metric": "clicks", "value": 10}]}, headers=h)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_ingest_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    resp = client.post(f"{BASE}/metrics", json={"points": []}, headers=h)
    assert resp.status_code == 403, resp.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_analytics.py -v`
Expected: FAIL (route 404).

- [ ] **Step 3: Add schemas**

`app/schemas/marketing.py` (uses `BaseModel`, `Field`; add `date` from datetime if not imported, and `MarketingMetricName` to the enums import):

```python
class MetricPoint(BaseModel):
    ts: date
    channel: ChannelKey | None = None
    campaign_id: uuid.UUID | None = None
    metric: MarketingMetricName
    value: int = Field(ge=0)


class MetricsIngest(BaseModel):
    points: list[MetricPoint] = Field(default_factory=list)
```

- [ ] **Step 4: Create the service**

`app/services/marketing/analytics.py`:

```python
import uuid

from sqlalchemy.orm import Session

from app.db.models.marketing import Campaign, MarketingMetric
from app.schemas.marketing import MetricsIngest
from app.services.marketing.service import _validation


def ingest_metrics(db: Session, *, startup_id: uuid.UUID, data: MetricsIngest) -> int:
    # Validate every referenced campaign belongs to this startup before inserting anything.
    campaign_ids = {p.campaign_id for p in data.points if p.campaign_id is not None}
    if campaign_ids:
        owned = {
            row.id
            for row in db.query(Campaign.id).filter(
                Campaign.startup_id == startup_id, Campaign.id.in_(campaign_ids)
            ).all()
        }
        missing = campaign_ids - owned
        if missing:
            raise _validation("campaign_id", "campaign_id must reference a campaign in this workspace.")
    for p in data.points:
        db.add(MarketingMetric(
            startup_id=startup_id, ts=p.ts, channel=p.channel,
            campaign_id=p.campaign_id, metric=p.metric, value=p.value,
        ))
    db.flush()
    return len(data.points)
```

- [ ] **Step 5: Add the endpoint**

`app/api/v1/endpoints/marketing.py` — import `from app.services.marketing import analytics as analytics_svc` and `MetricsIngest`; add:

```python
@router.post("/metrics", response_model=dict[str, Any])
def ingest_metrics(
    payload: MetricsIngest,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    created = analytics_svc.ingest_metrics(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response({"created": created})
```

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_analytics.py -v`
Expected: PASS. No mutating client call inside an assert.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/analytics.py app/api/v1/endpoints/marketing.py tests/api/test_marketing_analytics.py
git commit -m "feat(marketing): metrics ingestion (POST /marketing/metrics, validated bulk insert)"
```

---

## Task 3: Analytics aggregation read

**Files:**
- Modify: `app/schemas/marketing.py` (`TrafficPoint`, `ChannelCac`, `Funnel`, `LeaderboardRow`, `AnalyticsResponse`)
- Modify: `app/services/marketing/analytics.py` (`get_analytics` + `_range_cutoff`)
- Modify: `app/api/v1/endpoints/marketing.py` (`GET /marketing/analytics`)
- Test: `tests/api/test_marketing_analytics.py` (extend)

**Interfaces (produced):** `analytics.get_analytics(db, *, startup_id, range_key) -> dict`; route `GET /marketing/analytics?range=`.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/api/test_marketing_analytics.py
def _seed(client, db, h, points):
    resp = client.post(f"{BASE}/metrics", json={"points": points}, headers=h)
    assert resp.status_code == 200, resp.text


def test_analytics_empty_is_zeroed(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/analytics", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["traffic_by_week"] == [] and data["cac_by_channel"] == [] and data["leaderboard"] == []
    assert data["funnel"] == {"impressions": 0, "clicks": 0, "conversions": 0,
                              "click_through_rate": 0.0, "conversion_rate": 0.0}


def test_analytics_cac_and_funnel(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    _seed(client, db, h, [
        {"ts": today, "channel": "paid_social", "metric": "spend", "value": 64000},
        {"ts": today, "channel": "paid_social", "metric": "conversions", "value": 20},
        {"ts": today, "metric": "impressions", "value": 1000},
        {"ts": today, "metric": "clicks", "value": 100},
        {"ts": today, "metric": "conversions", "value": 20},
    ])
    data = client.get(f"{BASE}/analytics?range=30d", headers=h).json()["data"]
    paid = next(c for c in data["cac_by_channel"] if c["channel"] == "paid_social")
    assert paid["spend"] == 64000 and paid["conversions"] == 20 and paid["cac"] == 3200
    assert data["funnel"]["clicks"] == 100 and data["funnel"]["click_through_rate"] == 10.0


def test_analytics_cac_null_when_no_conversions(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    _seed(client, db, h, [{"ts": today, "channel": "search", "metric": "spend", "value": 500}])
    data = client.get(f"{BASE}/analytics", headers=h).json()["data"]
    search = next(c for c in data["cac_by_channel"] if c["channel"] == "search")
    assert search["cac"] is None


def test_analytics_bad_range_422(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/analytics?range=nope", headers=h)
    assert resp.status_code == 422, resp.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_analytics.py -v -k analytics`
Expected: FAIL (route 404).

- [ ] **Step 3: Add schemas**

```python
class TrafficPoint(BaseModel):
    week_start: date
    visits: int


class ChannelCac(BaseModel):
    channel: ChannelKey
    spend: int
    conversions: int
    cac: int | None


class Funnel(BaseModel):
    impressions: int
    clicks: int
    conversions: int
    click_through_rate: float
    conversion_rate: float


class LeaderboardRow(BaseModel):
    campaign_id: uuid.UUID
    name: str
    clicks: int
    conversions: int
    spend: int
    cac: int | None


class AnalyticsResponse(BaseModel):
    range: str
    traffic_by_week: list[TrafficPoint]
    cac_by_channel: list[ChannelCac]
    funnel: Funnel
    leaderboard: list[LeaderboardRow]
```

- [ ] **Step 4: Implement `get_analytics`**

`app/services/marketing/analytics.py` — add imports `from datetime import date, timedelta`, `from sqlalchemy import func, case`, `from app.db.models.enums import MarketingMetricName`, `from app.core.errors` via `_validation` (already imported):

```python
_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _range_cutoff(range_key: str) -> date:
    if range_key not in _RANGE_DAYS:
        raise _validation("range", "range must be one of 7d, 30d, 90d.")
    return date.today() - timedelta(days=_RANGE_DAYS[range_key])


def _rate(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 1) if denominator else 0.0


def get_analytics(db: Session, *, startup_id: uuid.UUID, range_key: str) -> dict:
    cutoff = _range_cutoff(range_key)
    base = db.query(MarketingMetric).filter(
        MarketingMetric.startup_id == startup_id, MarketingMetric.ts >= cutoff
    )

    def _sum(metric: MarketingMetricName) -> int:
        return case((MarketingMetric.metric == metric, MarketingMetric.value), else_=0)

    # traffic by ISO week
    week = func.date_trunc("week", MarketingMetric.ts)
    traffic_rows = (
        base.filter(MarketingMetric.metric == MarketingMetricName.visits)
        .with_entities(week.label("wk"), func.coalesce(func.sum(MarketingMetric.value), 0))
        .group_by(week).order_by(week).all()
    )
    traffic_by_week = [{"week_start": wk.date(), "visits": int(v)} for wk, v in traffic_rows]

    # CAC by channel (channel-scoped rows only)
    cac_rows = (
        base.filter(MarketingMetric.channel.isnot(None))
        .with_entities(
            MarketingMetric.channel,
            func.sum(_sum(MarketingMetricName.spend)),
            func.sum(_sum(MarketingMetricName.conversions)),
        )
        .group_by(MarketingMetric.channel).all()
    )
    cac_by_channel = []
    for channel, spend, conversions in cac_rows:
        spend, conversions = int(spend or 0), int(conversions or 0)
        cac_by_channel.append({
            "channel": channel.value, "spend": spend, "conversions": conversions,
            "cac": round(spend / conversions) if conversions else None,
        })

    # funnel totals
    totals = base.with_entities(
        func.sum(_sum(MarketingMetricName.impressions)),
        func.sum(_sum(MarketingMetricName.clicks)),
        func.sum(_sum(MarketingMetricName.conversions)),
    ).one()
    impressions, clicks, conversions = (int(x or 0) for x in totals)
    funnel = {
        "impressions": impressions, "clicks": clicks, "conversions": conversions,
        "click_through_rate": _rate(clicks, impressions),
        "conversion_rate": _rate(conversions, clicks),
    }

    # leaderboard (campaign-scoped rows, joined to campaign name)
    lb_rows = (
        base.filter(MarketingMetric.campaign_id.isnot(None))
        .join(Campaign, Campaign.id == MarketingMetric.campaign_id)
        .with_entities(
            Campaign.id, Campaign.name,
            func.sum(_sum(MarketingMetricName.clicks)),
            func.sum(_sum(MarketingMetricName.conversions)),
            func.sum(_sum(MarketingMetricName.spend)),
        )
        .group_by(Campaign.id, Campaign.name)
        .order_by(func.sum(_sum(MarketingMetricName.conversions)).desc(), Campaign.name)
        .all()
    )
    leaderboard = []
    for cid, name, clk, cnv, spd in lb_rows:
        clk, cnv, spd = int(clk or 0), int(cnv or 0), int(spd or 0)
        leaderboard.append({
            "campaign_id": cid, "name": name, "clicks": clk, "conversions": cnv,
            "spend": spd, "cac": round(spd / cnv) if cnv else None,
        })

    return {
        "range": range_key, "traffic_by_week": traffic_by_week,
        "cac_by_channel": cac_by_channel, "funnel": funnel, "leaderboard": leaderboard,
    }
```

(The join to `Campaign` on `campaign_id` naturally excludes SET-NULL'd rows and, since `Campaign` is startup-owned, keeps the leaderboard tenant-scoped.)

- [ ] **Step 5: Add the endpoint**

```python
@router.get("/analytics", response_model=dict[str, Any])
def get_analytics(
    range: str = "30d",
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = analytics_svc.get_analytics(db, startup_id=membership.startup_id, range_key=range)
    return success_response(AnalyticsResponse(**data).model_dump(mode="json"))
```

(Import `AnalyticsResponse`. `range` is a plain query param; an unknown value raises `_validation` → 422.)

- [ ] **Step 6: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_analytics.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/analytics.py app/api/v1/endpoints/marketing.py tests/api/test_marketing_analytics.py
git commit -m "feat(marketing): GET /marketing/analytics — traffic/CAC/funnel/leaderboard aggregation"
```

---

## Task 4: Overview fills

**Files:**
- Modify: `app/services/marketing/analytics.py` (`top_channel_by_conversions`, `count_ai_content_ideas`)
- Modify: `app/services/marketing/service.py` (`overview()` fills)
- Test: `tests/api/test_marketing.py` (extend the overview test) or `tests/services/marketing/test_analytics.py`

**Interfaces (produced):** `analytics.top_channel_by_conversions(db, *, startup_id) -> str | None`; `analytics.count_ai_content_ideas(db, *, startup_id) -> int`.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/api/test_marketing_analytics.py
def test_overview_fills(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    _seed(client, db, h, [
        {"ts": today, "channel": "search", "metric": "conversions", "value": 40},
        {"ts": today, "channel": "email", "metric": "conversions", "value": 10},
    ])
    ov = client.get(f"{BASE}", headers=h).json()["data"]
    assert ov["top_channel_by_conversions"] == "search"
    assert ov["ai_content_ideas"] == 0          # no ready content_gap generations
    assert ov["active_campaigns"] == 0            # none active


def test_overview_top_channel_null_when_empty(client, db):
    _u, _s, h = _member(db)
    ov = client.get(f"{BASE}", headers=h).json()["data"]
    assert ov["top_channel_by_conversions"] is None
    assert ov["ai_content_ideas"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_analytics.py -v -k overview`
Expected: FAIL (top_channel_by_conversions is None even with metrics; active_campaigns None).

- [ ] **Step 3: Add the helpers**

`app/services/marketing/analytics.py` (add `from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus`, `from app.db.models.marketing import MarketingAiGeneration`):

```python
def top_channel_by_conversions(db: Session, *, startup_id: uuid.UUID) -> str | None:
    row = (
        db.query(MarketingMetric.channel, func.sum(MarketingMetric.value).label("c"))
        .filter(
            MarketingMetric.startup_id == startup_id,
            MarketingMetric.metric == MarketingMetricName.conversions,
            MarketingMetric.channel.isnot(None),
        )
        .group_by(MarketingMetric.channel)
        .order_by(func.sum(MarketingMetric.value).desc())
        .first()
    )
    return row[0].value if row is not None else None


def count_ai_content_ideas(db: Session, *, startup_id: uuid.UUID) -> int:
    rows = (
        db.query(MarketingAiGeneration.output)
        .filter(
            MarketingAiGeneration.startup_id == startup_id,
            MarketingAiGeneration.kind == MarketingGenerationKind.content_gap,
            MarketingAiGeneration.status == MarketingGenerationStatus.ready,
        )
        .all()
    )
    return sum(len((out or {}).get("gaps", []) or []) for (out,) in rows)
```

- [ ] **Step 4: Fill `overview()`**

`app/services/marketing/service.py` — import `from app.db.models.enums import CampaignStatus` and `from app.db.models.marketing import Campaign`, and `from app.services.marketing import analytics as analytics_svc` (import inside the function if a circular import arises, since analytics.py imports from service.py — prefer a local import in `overview()` to avoid the cycle). Replace the stubbed fields:

```python
    active_campaigns = (
        db.query(Campaign)
        .filter(Campaign.startup_id == startup_id, Campaign.status == CampaignStatus.active)
        .count()
    )
    from app.services.marketing import analytics as analytics_svc  # local import avoids cycle
    return {
        "scheduled_this_week": scheduled_this_week,
        "active_channels": active_channels,
        "active_campaigns": active_campaigns,
        "top_channel_by_conversions": analytics_svc.top_channel_by_conversions(db, startup_id=startup_id),
        "ai_content_ideas": analytics_svc.count_ai_content_ideas(db, startup_id=startup_id),
    }
```

NOTE: `analytics.py` imports `_validation` from `service.py`, so `service.py` importing `analytics` at module top would be a cycle — use the local (function-scoped) import shown above.

- [ ] **Step 5: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_analytics.py tests/api/test_marketing.py -v -k "overview or fills"`
Expected: PASS. Confirm no import cycle: `poetry run python -c "import app.main"`.

- [ ] **Step 6: Commit**

```bash
git add app/services/marketing/analytics.py app/services/marketing/service.py tests/api/test_marketing_analytics.py
git commit -m "feat(marketing): fill Overview top_channel_by_conversions + ai_content_ideas + active_campaigns"
```

---

## Task 5: E2E journey + captures

**Files:**
- Modify: `e2e/test_marketing.py`
- Produces: `e2e/_captures/marketing/` — metrics ingest, analytics, overview-with-fills captures.

**Interfaces (consumed):** the file's real request helpers + founder-client setup.

- [ ] **Step 1: Extend the live journey**

Add `test_marketing_analytics_journey` modeled on the existing marketing journeys (bind every mutating call to a var; keep mutating calls out of asserts): authenticate a founder, then:
- `POST /api/v1/marketing/metrics` with a handful of points across two channels + one campaign_id (create a campaign first via the existing campaign endpoint so the id is real) covering visits/impressions/clicks/conversions/spend → capture `metrics_ingested`.
- `GET /api/v1/marketing/analytics?range=30d` → capture `analytics`.
- `GET /api/v1/marketing` (overview) → capture `overview_with_fills` (shows top_channel_by_conversions + active_campaigns populated).

- [ ] **Step 2: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the analytics journey passes; captures written. Ensure Homebrew pg/redis stopped (Docker owns 5432/6379); if the Docker daemon is down, start Docker Desktop and wait for healthy containers. Commit ONLY the new marketing captures + `e2e/test_marketing.py` (restore other modules' churn with `git checkout -- e2e/_captures/<other>`).

- [ ] **Step 3: Commit**

```bash
git add e2e/test_marketing.py e2e/_captures/marketing/
git commit -m "test(e2e): marketing analytics journey (ingest + aggregate + overview fills) with captures"
```

---

## Task 6: Docs — FE guide + SOP + checklist (Module 10 COMPLETE)

**Files:**
- Create: `docs/fe-integration-guide-marketing-analytics.md`
- Create: `docs/sop/2026-09-29-marketing-slice5.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE guide** — `docs/fe-integration-guide-marketing-analytics.md`, payloads pasted **verbatim from the Task-5 captures**. Cover: `GET /marketing/analytics?range=7d|30d|90d` (the four widgets — `traffic_by_week`, `cac_by_channel` [spend/cac in **cents**], `funnel` [rates are percentages], `leaderboard`); the Overview fills (`top_channel_by_conversions`, `ai_content_ideas`, `active_campaigns`); auth (founder/team_member → 403); bad range → 422. **Flag `POST /marketing/metrics` ingestion as API-first / provisional — no FE ingestion screen yet; the FE consumes the analytics read.** Note the FE leaderboard should use this endpoint's `leaderboard` array (not `campaign.metrics`, which stays `{}`). Verification table (live vs provisional).

- [ ] **Step 2: SOP** — `docs/sop/2026-09-29-marketing-slice5.md`, matching the Slice 4 SOP style: what shipped (marketing_metrics store, ingestion, analytics aggregation, Overview fills; migration 0038), why (Module 10 Slice 5 = final slice, Performance Analytics per PRD 10.8, FE cross-checked — analytics read aligned, ingestion API-first), how (time-series store with optional channel/campaign dims; range-filtered SQL aggregation with date_trunc week + filtered sums; empty-safe rates; overview fills via analytics helpers with a local import to avoid the service↔analytics cycle), files/migration, verification (unit + e2e), follow-ups (export → Module 22; ad-platform auto-ingestion; FE ingestion screen; campaign.metrics JSONB cleanup). **State that this completes Module 10 (all 5 slices).**

- [ ] **Step 3: Checklist** — `docs/checklist/PROJECT_CHECKLIST.md`: mark **Slice 5 complete** and **Module 10 (Marketing Hub) COMPLETE (all 5 slices)**; move Module 10 from the 🟡 Open row to ✅ Fully complete and update the tally (**13 modules fully complete, 0 open** — or adjust to the file's current counts, keeping 09 Validation as junior/in-review and the not-started set otherwise unchanged). Follow the file's format; don't disturb unrelated sections.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(marketing): FE guide + SOP + checklist for Module 10 Slice 5 — Module 10 complete"
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

Expected: green; `alembic heads` = single head `0038_marketing_metrics`. (Any Resend-429 auth/onboarding failures are the known external-quota flake, green in CI.)

- [ ] **No AI attribution:** `git log develop..HEAD --format='%an <%ae>%n%b'` shows none.
- [ ] **CodeQL clean:** no mutating call inside an `assert`; no implicit string concat in a list literal, anywhere new.

---

## Self-Review

**Spec coverage:** metrics store → T1; ingestion → T2; analytics read (traffic/CAC/funnel/leaderboard) → T3; Overview fills → T4; e2e → T5; docs → T6. Decisions D1–D5 covered. Export/pixel-ingestion/campaign.metrics-revival correctly out of scope.

**Placeholder scan:** every code step carries real code; the only "model on the existing X" notes are T5's e2e journey (copy the file's real helpers, as prior slices did).

**Type consistency:** `analytics.ingest_metrics/get_analytics/top_channel_by_conversions/count_ai_content_ideas`, `MetricPoint`/`MetricsIngest`/`AnalyticsResponse` (+ `TrafficPoint`/`ChannelCac`/`Funnel`/`LeaderboardRow`), `MarketingMetricName`, and `MarketingMetric` columns are consistent across producing (T1–T4) and consuming (T5) tasks. The service↔analytics import cycle is explicitly avoided via a function-local import in `overview()` (T4).

**Review Focus:** ingestion validation (T2), empty-data/divide-by-zero + CAC-null + range + weekly bucketing (T3), leaderboard tenancy/FK-null (T3), overview fills (T4), RBAC (T2/T3) — each has an owning task's test.
