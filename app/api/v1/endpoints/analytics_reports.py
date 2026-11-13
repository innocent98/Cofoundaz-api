"""Analytics & Reports slice B: saved reports, preview, frozen runs and shares.

Same prefix as the Overview (`/api/v1/analytics`). Founder and team member write; accountant,
mentor and business consultant only read reports the Founder shared with them. Every number is
authorised per metric (see services/analytics/reports.py and runs.py)."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import Forbidden
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.events import event_bus
from app.services.analytics import catalog as cat
from app.services.analytics import reports as rep
from app.services.analytics import runs
from app.services.analytics import service as svc
from app.services.billing.gate import assert_feature

router = APIRouter()
_role = require_role(*cat.REPORT_ROLES)


def _access(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    assert_feature(db, membership.startup_id, "analytics")
    return membership


def _writer(membership: Membership = Depends(_access)) -> Membership:  # noqa: B008
    if membership.role not in cat.REPORT_WRITE_ROLES:
        raise Forbidden()
    return membership


def _founder(membership: Membership = Depends(_access)) -> Membership:  # noqa: B008
    if membership.role != MembershipRole.founder:
        raise Forbidden()
    return membership


class BlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = Field(default=None, max_length=40)
    type: Literal["metric", "text"]
    metric: str | None = Field(default=None, max_length=60)
    viz: str | None = Field(default=None, max_length=10)
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=5000)
    range: str | None = Field(default=None, max_length=20)
    from_: str | None = Field(default=None, alias="from", max_length=10)
    to: str | None = Field(default=None, max_length=10)
    compare: Literal["none", "previous", "year"] | None = None

    def raw(self) -> dict[str, Any]:
        d = self.model_dump(exclude_none=True)
        if self.from_:
            d["from"] = d.pop("from_")
        return d


class RangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    range: str = Field(default="30d", max_length=20)
    from_: str | None = Field(default=None, alias="from", max_length=10)
    to: str | None = Field(default=None, max_length=10)
    compare: Literal["none", "previous", "year"] = "previous"

    def raw(self) -> dict[str, Any]:
        return {"range": self.range, "from": self.from_, "to": self.to, "compare": self.compare}


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    blocks: list[BlockIn] | None = Field(default=None, max_length=rep.MAX_BLOCKS)
    default_range: RangeIn | None = None
    template_key: str | None = Field(default=None, max_length=40)


class ReportPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    blocks: list[BlockIn] | None = Field(default=None, max_length=rep.MAX_BLOCKS)
    default_range: RangeIn | None = None


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[BlockIn] = Field(max_length=rep.MAX_BLOCKS)
    default_range: RangeIn | None = None


class SharesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[uuid.UUID] = Field(max_length=rep.MAX_SHARES * 2)


def _raw_blocks(b: list[BlockIn] | None) -> list[dict[str, Any]] | None:
    return None if b is None else [x.raw() for x in b]


# ---- templates, preview (declared before /{id}) ----
@router.get("/reports/templates", response_model=dict[str, Any])
def templates(
    membership: Membership = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    metrics = svc.all_metrics(db, membership.startup_id)
    return success_response({"templates": rep.templates_for(membership.role, metrics)})


@router.post("/reports/preview", response_model=dict[str, Any])
def preview(
    payload: PreviewBody,
    membership: Membership = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rng = payload.default_range.raw() if payload.default_range else None
    return success_response(runs.preview(db, membership, _raw_blocks(payload.blocks) or [], rng))


# ---- CRUD ----
@router.get("/reports", response_model=dict[str, Any])
def list_reports(
    q: str | None = Query(default=None, max_length=80),
    sort: Literal["updated", "name", "created"] = "updated",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(rep.list_reports(db, membership, q, sort, limit, offset))


@router.post("/reports", status_code=201, response_model=dict[str, Any])
def create_report(
    payload: ReportCreate,
    membership: Membership = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.create(
        db,
        membership,
        name=payload.name,
        description=payload.description,
        blocks=_raw_blocks(payload.blocks),
        default_range=payload.default_range.raw() if payload.default_range else None,
        template_key=payload.template_key,
    )
    db.commit()
    return success_response(rep.detail(db, membership, row))


@router.get("/reports/{report_id}", response_model=dict[str, Any])
def get_report(
    report_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(rep.detail(db, membership, rep.get_visible(db, membership, report_id)))


@router.patch("/reports/{report_id}", response_model=dict[str, Any])
def patch_report(
    report_id: uuid.UUID,
    payload: ReportPatch,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    rep.require_edit(membership, row)
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "blocks" in changes:
        changes["blocks"] = _raw_blocks(payload.blocks)
    if "default_range" in changes:
        changes["default_range"] = payload.default_range.raw() if payload.default_range else None
    rep.update(db, membership, row, payload.version, changes)
    db.commit()
    return success_response(rep.detail(db, membership, row))


@router.delete("/reports/{report_id}", response_model=dict[str, Any])
def delete_report(
    report_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    rep.require_edit(membership, row)
    rep.soft_delete(db, row)
    db.commit()
    return success_response({"deleted": True})


@router.post("/reports/{report_id}/duplicate", status_code=201, response_model=dict[str, Any])
def duplicate_report(
    report_id: uuid.UUID,
    membership: Membership = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    src = rep.get_visible(db, membership, report_id)
    row, dropped = rep.duplicate(db, membership, src)
    db.commit()
    return success_response({**rep.detail(db, membership, row), "dropped_blocks": dropped})


# ---- runs ----
@router.post(
    "/reports/{report_id}/run", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any]
)
def run_report(
    report_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    run, job_id = runs.start_run(db, membership, row)
    db.commit()
    return success_response({"run_id": str(run.id), "job_id": job_id, "status": run.status})


def _run_names(db: Session, items: list[Any]) -> dict[uuid.UUID, str]:
    return rep.names(db, {r.run_by for r in items if r.run_by})


@router.get("/reports/{report_id}/runs", response_model=dict[str, Any])
def list_runs(
    report_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    items = runs.list_runs(db, row)
    metrics = svc.all_metrics(db, membership.startup_id)
    names = _run_names(db, items)
    return success_response(
        {
            "runs": [
                runs.serialize_run(r, membership.role, metrics, names, with_snapshot=False)
                for r in items
            ]
        }
    )


@router.get("/reports/{report_id}/runs/{run_id}", response_model=dict[str, Any])
def get_run(
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    run = runs.get_run(db, row, run_id)
    metrics = svc.all_metrics(db, membership.startup_id)
    return success_response(
        runs.serialize_run(run, membership.role, metrics, _run_names(db, [run]), with_snapshot=True)
    )


# ---- shares ----
@router.get("/reports/{report_id}/shares", response_model=dict[str, Any])
def get_shares(
    report_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    if membership.role != MembershipRole.founder:
        raise Forbidden()
    return success_response(rep.shares_view(db, row))


@router.put("/reports/{report_id}/shares", response_model=dict[str, Any])
def put_shares(
    report_id: uuid.UUID,
    payload: SharesBody,
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rep.get_visible(db, membership, report_id)
    added = rep.set_shares(db, membership, row, payload.user_ids)
    if added:
        event_bus.publish(
            db,
            "analytics.report.shared",
            {
                "startup_id": str(row.startup_id),
                "report_id": str(row.id),
                "report_name": row.name,
                "user_ids": [str(u) for u in added],
                "actor_id": str(user.id),
            },
        )
    db.commit()
    return success_response(rep.shares_view(db, row))
