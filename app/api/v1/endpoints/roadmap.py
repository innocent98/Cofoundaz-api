import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapPhase
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.jobs import job_dispatcher
from app.schemas.roadmap import PhaseCreate, PhaseUpdate
from app.services.roadmap.service import generate_roadmap, serialize_tree

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


def _roadmap(db: Session, membership: Membership) -> Roadmap | None:
    return db.query(Roadmap).filter(Roadmap.startup_id == membership.startup_id).first()


def _require_roadmap(db: Session, membership: Membership) -> Roadmap:
    r = _roadmap(db, membership)
    if r is None:
        raise NotFound()
    return r


def _phase(db: Session, membership: Membership, phase_id: uuid.UUID) -> RoadmapPhase:
    roadmap = _require_roadmap(db, membership)
    p = (
        db.query(RoadmapPhase)
        .filter(RoadmapPhase.id == phase_id, RoadmapPhase.roadmap_id == roadmap.id)
        .first()
    )
    if p is None:
        raise NotFound()
    return p


def _next_order(db: Session, model: type[Any], **filters: Any) -> int:
    val = db.query(func.max(model.order)).filter_by(**filters).scalar()
    return 0 if val is None else val + 1


def _phase_out(p: RoadmapPhase) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "order": p.order,
        "starts_on": p.starts_on.isoformat() if p.starts_on else None,
        "ends_on": p.ends_on.isoformat() if p.ends_on else None,
    }


@router.get("")
def get_roadmap(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    roadmap = _roadmap(db, membership)
    if roadmap is None:
        roadmap = generate_roadmap(db, startup, actor=user)
    db.commit()
    return success_response(serialize_tree(db, roadmap, startup))


@router.post("/generate", status_code=202)
def post_generate(
    membership: Membership = Depends(  # noqa: B008
        require_role(MembershipRole.founder, MembershipRole.team_member)  # noqa: B008
    ),
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    generate_roadmap(db, startup, actor=user)
    job = job_dispatcher.enqueue(
        db, "roadmap.generate", {"startup_id": str(startup.id)}, startup.id
    )
    job.status = JobStatus.succeeded
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.post("/phases", status_code=201)
def create_phase(
    body: PhaseCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    roadmap = _require_roadmap(db, membership)
    order = (
        body.order
        if body.order is not None
        else _next_order(db, RoadmapPhase, roadmap_id=roadmap.id)
    )
    p = RoadmapPhase(
        roadmap_id=roadmap.id,
        name=body.name,
        order=order,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
    )
    db.add(p)
    db.commit()
    return success_response(_phase_out(p))


@router.patch("/phases/{phase_id}")
def update_phase(
    phase_id: uuid.UUID,
    body: PhaseUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    p = _phase(db, membership, phase_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    db.commit()
    return success_response(_phase_out(p))


@router.delete("/phases/{phase_id}")
def delete_phase(
    phase_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    p = _phase(db, membership, phase_id)
    db.delete(p)
    db.commit()
    return success_response({"deleted": True})
