import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
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
from app.schemas.business import (
    CanvasAiFill,
    CanvasRestore,
    CanvasSave,
    FitLinksSave,
    MissionVisionOptions,
    PlanGenerate,
    PositioningMapSave,
    PricingAdvisorRequest,
    RecordCreate,
    SuggestionCreate,
)
from app.services.business.ai_assist import (
    competitor_prices,
    has_validation_data,
    validation_context,
)
from app.services.business.canvas_defs import CANVAS_BLOCKS
from app.services.business.export import export_canvas, export_records
from app.services.business.plan_defs import (
    DEFAULT_OPTIONS,
    PLAN_SECTIONS,
    SECTIONS_BY_KEY,
    STAGE_CAPTIONS,
)
from app.services.business.plan_tools import (
    default_growth_pct,
    projection,
    readiness,
    send_to_finance,
    set_fit_links,
)
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
    get_version,
    list_versions,
    overview,
    restore_version,
    save_canvas,
    serialize_canvas,
    serialize_version,
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
# D-BB-3 / PRD 08 access line: only a business consultant works in suggest mode.
_suggester = require_role(MembershipRole.business_consultant)


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
    saved = save_canvas(db, canvas, body.blocks, body.version, user_id=user.id)
    db.commit()
    return success_response(serialize_canvas(saved))


@router.get("/canvases/{type}/versions")
def get_canvas_versions(
    type: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas = get_or_create_canvas(db, _startup(db, membership), _parse_type(type))
    db.commit()
    return success_response({"versions": [serialize_version(v) for v in list_versions(db, canvas)]})


@router.get("/canvases/{type}/versions/{version}")
def get_canvas_version(
    type: str,
    version: int,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas = get_or_create_canvas(db, _startup(db, membership), _parse_type(type))
    db.commit()
    return success_response(serialize_version(get_version(db, canvas, version), with_blocks=True))


@router.post("/canvases/{type}/versions/{version}/restore")
def restore_canvas_version(
    type: str,
    version: int,
    body: CanvasRestore,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas = get_or_create_canvas(db, _startup(db, membership), _parse_type(type))
    saved = restore_version(db, canvas, version, body.version, user.id)
    db.commit()
    return success_response(serialize_canvas(saved))


@router.post("/canvases/{type}/export", status_code=status.HTTP_201_CREATED)
def export_canvas_endpoint(
    type: str,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    doc = export_canvas(db, _startup(db, membership), canvas_type, user.id)
    db.commit()
    return success_response({"document_id": str(doc.id), "title": doc.title})


@router.post("/canvases/{type}/ai-fill", status_code=status.HTTP_202_ACCEPTED)
def ai_fill_canvas(
    type: str,
    body: CanvasAiFill | None = None,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    startup = _startup(db, membership)
    payload: dict[str, Any] = {"startup_id": str(startup.id), "canvas_type": canvas_type.value}
    if body is not None and body.block is not None:
        if body.block not in {b.key for b in CANVAS_BLOCKS[canvas_type]}:
            raise AppError(
                "VALIDATION_ERROR",
                "That section doesn't exist on this canvas.",
                422,
                field_errors=[{"field": "block", "message": "Not a block on this canvas."}],
            )
        payload["block"] = body.block  # per-section assist, append-only (D-BB-4)
    job = job_dispatcher.enqueue(
        db,
        type="business.canvas.ai_fill",
        payload=payload,
        startup_id=startup.id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.post("/canvases/mission_vision/options", status_code=status.HTTP_202_ACCEPTED)
def mission_vision_options(
    body: MissionVisionOptions,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.4 "Give me 3 options": poll `GET /jobs/{id}`; `result.options` holds the variants."""
    startup = _startup(db, membership)
    job = job_dispatcher.enqueue(
        db,
        type="ai.business.mission_vision_options",
        payload={"startup_id": str(startup.id), "field": body.field},
        startup_id=startup.id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.post("/personas/from-validation", status_code=status.HTTP_202_ACCEPTED)
def personas_from_validation(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.6 "Generate personas from my validation notes" (Module 09 interviews/assumptions)."""
    startup = _startup(db, membership)
    if not has_validation_data(validation_context(db, startup)):
        raise AppError(
            "NO_VALIDATION_DATA",
            "Log a customer interview or an assumption in Validation first.",
            422,
        )
    job = job_dispatcher.enqueue(
        db,
        type="ai.business.personas_from_validation",
        payload={"startup_id": str(startup.id)},
        startup_id=startup.id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.post("/pricing/advisor", status_code=status.HTTP_202_ACCEPTED)
def pricing_advisor(
    body: PricingAdvisorRequest,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.7 AI advisor panel. Competitor prices = the competitor records' prices plus any
    typed in; advice comes back in `result.advice` and is never written automatically."""
    startup = _startup(db, membership)
    prices = competitor_prices(db, startup)
    known = {p["name"].casefold() for p in prices}
    prices += [p.model_dump() for p in body.competitor_prices if p.name.casefold() not in known]
    job = job_dispatcher.enqueue(
        db,
        type="ai.business.pricing_advisor",
        payload={
            "startup_id": str(startup.id),
            "competitor_prices": prices[:20],
            "willingness_to_pay": body.willingness_to_pay,
        },
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
    membership: Membership = Depends(_suggester),  # noqa: B008
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
    body: PlanGenerate | None = None,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    options = (body or PlanGenerate()).model_dump()
    plan = BusinessPlan(
        startup_id=startup.id,
        status=BusinessPlanStatus.generating,
        created_by_id=user.id,
        options=options,
        progress={
            "done": 0,
            "total": len(PLAN_SECTIONS),
            "stage": STAGE_CAPTIONS[PLAN_SECTIONS[0].key],
        },
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


def _latest_plan(db: Session, membership: Membership) -> BusinessPlan | None:
    return (
        db.query(BusinessPlan)
        .filter(BusinessPlan.startup_id == membership.startup_id)
        .order_by(BusinessPlan.created_at.desc())
        .first()
    )


@router.get("/plan")
def get_plan(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    plan = _latest_plan(db, membership)
    if plan is None:
        raise NotFound()
    return success_response(
        {
            "id": str(plan.id),
            "status": plan.status.value,
            "document_id": (str(plan.document_id) if plan.document_id else None),
            "options": plan.options or dict(DEFAULT_OPTIONS),
            "progress": plan.progress,
            "error": plan.error,
            "created_at": plan.created_at.isoformat(),
        }
    )


@router.get("/plan/readiness")
def get_plan_readiness(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.11 readiness checklist: every artifact the plan draws on."""
    return success_response(readiness(db, _startup(db, membership)))


@router.post("/plan/draft-missing", status_code=status.HTTP_202_ACCEPTED)
def draft_missing(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.11 "Draft missing sections with AI": one AI fill per artifact that isn't ready.
    Canvas fills only write EMPTY blocks; record fills only run for kinds with no records."""
    startup = _startup(db, membership)
    jobs = []
    for item in readiness(db, startup)["items"]:
        if item["status"] == "ready":
            continue
        is_canvas = item["type"] in {t.value for t in CanvasType}
        job = job_dispatcher.enqueue(
            db,
            type=("business.canvas.ai_fill" if is_canvas else f"business.{item['type']}.ai_fill"),
            payload=(
                {"startup_id": str(startup.id), "canvas_type": item["type"]}
                if is_canvas
                else {"startup_id": str(startup.id), "kind": item["type"]}
            ),
            startup_id=startup.id,
        )
        jobs.append({"type": item["type"], "job_id": str(job.id)})
    db.commit()
    return success_response({"jobs": jobs})


@router.post("/plan/sections/{key}/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate_plan_section(
    key: str,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.11 "Regenerate a section" of the latest finished plan's Document."""
    plan = _latest_plan(db, membership)
    if plan is None or plan.document_id is None or key not in SECTIONS_BY_KEY:
        raise NotFound()
    job = job_dispatcher.enqueue(
        db,
        type="ai.business.plan_section",
        payload={"startup_id": str(plan.startup_id), "plan_id": str(plan.id), "section": key},
        startup_id=plan.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.get("/revenue-streams/projection")
def revenue_projection(
    months: int = Query(12, ge=1, le=60),  # noqa: B008
    growth_pct: float | None = Query(None, ge=-50, le=100),  # noqa: B008
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.8 projected chart. Growth defaults to Finance's runway MoM growth assumption."""
    startup = _startup(db, membership)
    g = growth_pct if growth_pct is not None else default_growth_pct(db, startup)
    return success_response(projection(db, startup, months, g))


@router.post("/revenue-streams/send-to-finance")
def revenue_send_to_finance(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.8 "Send to financial model" (revenue drivers for PRD 12.6)."""
    drivers = send_to_finance(db, _startup(db, membership), user.id)
    db.commit()
    return success_response(drivers)


@router.put("/canvases/value_prop/fit")
def put_fit_links(
    body: FitLinksSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 08.5 fit-lines: replace the Value Proposition's links (reliever↔pain, creator↔gain)."""
    canvas = get_or_create_canvas(db, _startup(db, membership), CanvasType.value_prop)
    set_fit_links(db, canvas, [link.model_dump() for link in body.links], user.id)
    db.commit()
    return success_response(serialize_canvas(canvas))


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
    record = create_record(db, startup, rk, payload.data, user_id=user.id)
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


@router.post("/{kind}/export", status_code=status.HTTP_201_CREATED)
def export_kind(
    kind: str,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rk = _parse_kind(kind)
    doc = export_records(db, _startup(db, membership), rk, user.id)
    db.commit()
    return success_response({"document_id": str(doc.id), "title": doc.title})


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
    update_record(db, record, payload.data, user_id=user.id)
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
