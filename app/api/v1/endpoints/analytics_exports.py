"""Analytics & Reports slice C: exports (report PDF / XLSX, data tables XLSX / CSV).

Same prefix as the rest of Analytics (`/api/v1/analytics`). Founder, team member and accountant
only (spec section 3, Exports column); mentor, business consultant and every other role are 403.
The download is the only way to the file: authorised per request, audited, never a public URL.
See services/analytics/exports.py for the rules."""

import uuid
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.rate_limit import limiter
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.services.analytics import catalog as cat
from app.services.analytics import exports as exp
from app.services.analytics import render as rnd
from app.services.analytics import reports as rep
from app.services.billing.gate import assert_feature

router = APIRouter()
_role = require_role(*cat.EXPORT_ROLES)


def _access(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    assert_feature(db, membership.startup_id, "analytics")
    return membership


class ReportExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["pdf", "xlsx"]
    run_id: uuid.UUID | None = None


class TableRangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    range: str = Field(default="30d", max_length=20)
    from_: str | None = Field(default=None, alias="from", max_length=10)
    to: str | None = Field(default=None, max_length=10)
    compare: Literal["none", "previous", "year"] = "previous"


class DataTablesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(min_length=1, max_length=exp.MAX_TABLE_METRICS)
    range: TableRangeIn | None = None
    format: Literal["xlsx", "csv"]
    include_metadata: bool = True


def _accepted(row: Any, db: Session) -> dict[str, Any]:
    return success_response(exp.detail(db, row))


@router.post(
    "/reports/{report_id}/export",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict[str, Any],
)
def export_report(
    report_id: uuid.UUID,
    payload: ReportExportIn,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    report = rep.get_visible(db, membership, report_id)
    row = exp.export_report(db, membership, report, payload.format, payload.run_id)
    db.commit()
    return _accepted(row, db)


@router.post(
    "/exports/data-tables", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any]
)
def export_data_tables(
    payload: DataTablesIn,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rng = None
    if payload.range is not None:
        r = payload.range
        rng = {"range": r.range, "from": r.from_, "to": r.to, "compare": r.compare}
    row = exp.export_tables(
        db, membership, payload.metrics, rng, payload.format, payload.include_metadata
    )
    db.commit()
    return _accepted(row, db)


@router.get("/exports", response_model=dict[str, Any])
def list_exports(
    artifact_type: Literal["report", "data_tables"] | None = None,
    limit: int = Query(default=50, ge=1, le=exp.LIST_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(exp.list_exports(db, membership, artifact_type, limit, offset))


@router.get("/exports/{export_id}", response_model=dict[str, Any])
def get_export(
    export_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(exp.detail(db, exp.get_visible(db, membership, export_id)))


@router.get("/exports/{export_id}/download")
@limiter.shared_limit("60/minute", scope="analytics-export-download")
def download_export(
    request: Request,
    export_id: uuid.UUID,
    membership: Membership = Depends(_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """The file itself (not the JSON envelope). 404 / 410 / 409 / 403 per `open_for_download`."""
    row, content = exp.open_for_download(db, membership, export_id)
    exp.record_download(db, row, user.id, request.client.host if request.client else None)
    db.commit()
    name = (row.filename or "export").replace('"', "").replace("\r", "").replace("\n", "")
    ascii_name = name.encode("ascii", "ignore").decode().strip() or "export"
    return Response(
        content=content,
        media_type=rnd.CONTENT_TYPES[row.format],
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
