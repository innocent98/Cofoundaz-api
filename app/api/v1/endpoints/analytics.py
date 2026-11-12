"""Analytics & Reports (PRD Module 22), slice A: the metric catalog, the Overview, By hub, batch
resolve and founder-entered custom metrics. Founder, team member and accountant may open it
(D-ANA-4); every number is authorised per metric in the service, so an accountant gets finance
only and a locked metric's value is never computed. Behind the `analytics` plan module (Growth)."""

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import Forbidden, NotFound
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.services.analytics import catalog as cat
from app.services.analytics import custom as custom_svc
from app.services.analytics import overview as overview_svc
from app.services.analytics import periods as per
from app.services.analytics import service as svc
from app.services.billing.gate import assert_feature

router = APIRouter()
_role = require_role(*cat.ANALYTICS_ROLES)


def _analytics(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    """Role first (403), then the Growth plan module (402). Dark until PLAN_ENFORCEMENT is on."""
    assert_feature(db, membership.startup_id, "analytics")
    return membership


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.get(Startup, membership.startup_id)
    if s is None:
        raise NotFound()
    return s


def _period(range_: str, from_: date | None, to: date | None) -> per.Period:
    return per.resolve(range_, from_, to)


# ---- catalog ----
@router.get("/metrics-catalog", response_model=dict[str, Any])
def metrics_catalog(
    membership: Membership = Depends(_analytics),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(svc.catalog_for(db, _startup(db, membership), membership.role))


# ---- live reads ----
@router.get("/overview", response_model=dict[str, Any])
def overview(
    range: str = "30d",  # noqa: A002
    from_: date | None = Query(default=None, alias="from"),  # noqa: B008
    to: date | None = Query(default=None),  # noqa: B008
    compare: str = "previous",
    membership: Membership = Depends(_analytics),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.check_rate(membership.startup_id)
    p = _period(range, from_, to)
    data = overview_svc.overview(db, _startup(db, membership), membership.role, p, compare)
    return success_response(data)


@router.get("/by-hub", response_model=dict[str, Any])
def by_hub(
    hub: str | None = None,
    range: str = "30d",  # noqa: A002
    from_: date | None = Query(default=None, alias="from"),  # noqa: B008
    to: date | None = Query(default=None),  # noqa: B008
    compare: str = "previous",
    membership: Membership = Depends(_analytics),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.check_rate(membership.startup_id)
    p = _period(range, from_, to)
    data = overview_svc.by_hub(db, _startup(db, membership), membership.role, p, compare, hub)
    return success_response(data)


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=60)
    range: str = "30d"
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    compare: Literal["none", "previous", "year"] = "none"
    series: bool = False


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[MetricSpec] = Field(min_length=1, max_length=svc.MAX_BATCH)


@router.post("/metrics/resolve", response_model=dict[str, Any])
def resolve_metrics(
    payload: ResolveBody,
    membership: Membership = Depends(_analytics),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.check_rate(membership.startup_id)
    specs = [s.model_dump(by_alias=False) for s in payload.metrics]
    results = svc.resolve_many(db, _startup(db, membership), membership.role, specs)
    return success_response({"metrics": results})


# ---- custom metrics (D-ANA-2 b) ----
class CustomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    unit: Literal["count", "money", "percent"]


class PointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = Field(min_length=7, max_length=10)
    value: float | int | str


class PointsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[PointIn] = Field(min_length=1, max_length=custom_svc.MAX_POINTS_PER_CALL)


def _writer(membership: Membership = Depends(_analytics)) -> Membership:  # noqa: B008
    if membership.role not in cat.CUSTOM_WRITE_ROLES:
        raise Forbidden()
    return membership


@router.get("/custom-metrics", response_model=dict[str, Any])
def list_custom(
    membership: Membership = Depends(_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"metrics": custom_svc.list_all(db, membership.startup_id)})


@router.post("/custom-metrics", status_code=201, response_model=dict[str, Any])
def create_custom(
    payload: CustomCreate,
    membership: Membership = Depends(_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = custom_svc.create(db, membership.startup_id, user.id, payload.label, payload.unit)
    db.commit()
    return success_response(row)


@router.put("/custom-metrics/{metric_id}/points", response_model=dict[str, Any])
def put_points(
    metric_id: uuid.UUID,
    payload: PointsBody,
    membership: Membership = Depends(_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = custom_svc.put_points(
        db, membership.startup_id, user.id, metric_id, [p.model_dump() for p in payload.points]
    )
    db.commit()
    return success_response(row)


@router.delete("/custom-metrics/{metric_id}", response_model=dict[str, Any])
def delete_custom(
    metric_id: uuid.UUID,
    membership: Membership = Depends(_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    custom_svc.delete(db, membership.startup_id, metric_id)
    db.commit()
    return success_response({"deleted": True})
