import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.business import BusinessPlan
from app.db.models.enums import (
    BusinessPlanStatus,
    CanvasType,
    MembershipRole,
    RecordKind,
    SuggestionStatus,
)
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.jobs import job_dispatcher
from app.schemas.business import CanvasSave, PositioningMapSave, RecordCreate, SuggestionCreate
from app.services.business.positioning import (
    assemble_map,
    get_or_create_map,
    serialize_map,
    update_axes,
)
from app.services.business.record_defs import fields
from app.services.business.records import (
    _record,
    create_record,
    delete_record,
    list_records,
    serialize_record,
    update_record,
)
from app.services.business.service import (
    get_or_create_canvas,
    overview,
    save_canvas,
    serialize_canvas,
)
from app.services.business.suggestions import (
    approve_suggestion,
    create_suggestion,
    list_suggestions,
    reject_suggestion,
    serialize_suggestion,
)

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


def _parse_type(type: str) -> CanvasType:
    try:
        return CanvasType(type)
    except ValueError:
        raise NotFound() from None


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


@router.post("/canvases/{type}/ai-fill", status_code=status.HTTP_202_ACCEPTED)
def ai_fill_canvas(
    type: str,
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


@router.post("/suggestions", status_code=status.HTTP_201_CREATED)
def create_suggestion_endpoint(
    body: SuggestionCreate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = create_suggestion(db, membership, body.op, body.target, body.payload, body.note)
    db.commit()
    return success_response(serialize_suggestion(db, s))


@router.get("/suggestions")
def list_suggestions_endpoint(
    status: str | None = None,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    try:
        parsed = SuggestionStatus(status) if status else None
    except ValueError:
        raise NotFound() from None  # unknown status filter -> 404 (uniform "no such view")
    rows = list_suggestions(db, _startup(db, membership), parsed)
    return success_response({"suggestions": [serialize_suggestion(db, r) for r in rows]})


@router.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = approve_suggestion(db, membership, suggestion_id)
    db.commit()
    return success_response(serialize_suggestion(db, s))


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = reject_suggestion(db, membership, suggestion_id)
    db.commit()
    return success_response(serialize_suggestion(db, s))


@router.get("/positioning-map")
def get_positioning_map(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    data = assemble_map(db, startup)  # lazily creates the axes row
    db.commit()
    return success_response(data)


@router.put("/positioning-map")
def put_positioning_map(
    body: PositioningMapSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    row = get_or_create_map(db, startup)
    update_axes(db, row, body.axes)
    db.commit()
    return success_response(serialize_map(row))


@router.post("/plan/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_plan(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    plan = BusinessPlan(
        startup_id=startup.id, status=BusinessPlanStatus.generating, created_by_id=user.id
    )
    db.add(plan)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "business.plan.generate",
        {"startup_id": str(startup.id), "plan_id": str(plan.id)},
        startup.id,
    )
    db.commit()
    return success_response({"plan_id": str(plan.id), "status": plan.status.value})


@router.get("/plan")
def get_plan(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    plan = (
        db.query(BusinessPlan)
        .filter(BusinessPlan.startup_id == membership.startup_id)
        .order_by(BusinessPlan.created_at.desc())
        .first()
    )
    if plan is None:
        raise NotFound()
    return success_response(
        {
            "id": str(plan.id),
            "status": plan.status.value,
            "document_id": (str(plan.document_id) if plan.document_id else None),
            "created_at": plan.created_at.isoformat(),
        }
    )


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
