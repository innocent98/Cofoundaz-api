import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, DependencyCycle, NotFound
from app.db.models.enums import (
    JobStatus,
    MembershipRole,
    MembershipStatus,
    RoadmapStatus,
    TaskEffort,
)
from app.db.models.membership import Membership
from app.db.models.roadmap import (
    Roadmap,
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapTask,
    RoadmapTaskDependency,
)
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.schemas.roadmap import (
    DependencyCreate,
    MilestoneCreate,
    MilestoneUpdate,
    PhaseCreate,
    PhaseUpdate,
    TaskCreate,
    TaskUpdate,
)
from app.services.roadmap.dependencies import add_dependency, would_create_cycle
from app.services.roadmap.service import (
    generate_roadmap,
    milestone_overdue,
    person_ref,
    recompute_milestone_progress,
    serialize_tree,
    task_overdue,
)

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


def _validate_member(
    db: Session, membership: Membership, user_id: uuid.UUID | None, field: str
) -> None:
    if user_id is None:
        return
    ok = (
        db.query(Membership)
        .filter(
            Membership.startup_id == membership.startup_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )
    if ok is None:
        raise AppError(
            "VALIDATION_ERROR",
            "That user is not a member of this workspace.",
            422,
            field_errors=[{"field": field, "message": "Not an active member."}],
        )


def _milestone(db: Session, membership: Membership, milestone_id: uuid.UUID) -> RoadmapMilestone:
    roadmap = _require_roadmap(db, membership)
    m = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapMilestone.id == milestone_id, RoadmapPhase.roadmap_id == roadmap.id)
        .first()
    )
    if m is None:
        raise NotFound()
    return m


def _milestone_out(db: Session, m: RoadmapMilestone) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "phase_id": str(m.phase_id),
        "title": m.title,
        "description": m.description,
        "due_on": m.due_on.isoformat() if m.due_on else None,
        "owner": person_ref(db, m.owner_id),
        "status": m.status.value,
        "progress": m.progress,
        "overdue": milestone_overdue(m),
        "order": m.order,
    }


def _task(db: Session, membership: Membership, task_id: uuid.UUID) -> RoadmapTask:
    roadmap = _require_roadmap(db, membership)
    t = (
        db.query(RoadmapTask)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapTask.id == task_id, RoadmapPhase.roadmap_id == roadmap.id)
        .first()
    )
    if t is None:
        raise NotFound()
    return t


def _task_out(db: Session, t: RoadmapTask) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "milestone_id": str(t.milestone_id),
        "title": t.title,
        "description": t.description,
        "effort": t.effort.value,
        "status": t.status.value,
        "assignee": person_ref(db, t.assignee_id),
        "due_on": t.due_on.isoformat() if t.due_on else None,
        "overdue": task_overdue(t),
        "order": t.order,
        "depends_on": [],
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


@router.post("/milestones", status_code=201)
def create_milestone_ep(
    body: MilestoneCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    phase = _phase(db, membership, body.phase_id)  # 404 if foreign/unknown
    _validate_member(db, membership, body.owner_id, "owner_id")
    order = (
        body.order
        if body.order is not None
        else _next_order(db, RoadmapMilestone, phase_id=phase.id)
    )
    m = RoadmapMilestone(
        phase_id=phase.id,
        title=body.title,
        description=body.description,
        due_on=body.due_on,
        owner_id=body.owner_id,
        status=body.status or RoadmapStatus.todo,
        progress=0,
        order=order,
    )
    db.add(m)
    db.flush()
    if m.status == RoadmapStatus.done:
        m.progress = 100
    db.commit()
    return success_response(_milestone_out(db, m))


@router.patch("/milestones/{milestone_id}")
def update_milestone_ep(
    milestone_id: uuid.UUID,
    body: MilestoneUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    m = _milestone(db, membership, milestone_id)
    was_done = m.status == RoadmapStatus.done
    fields = body.model_dump(exclude_unset=True)
    if "owner_id" in fields:
        _validate_member(db, membership, fields["owner_id"], "owner_id")
    for field, value in fields.items():
        setattr(m, field, value)
    db.flush()
    # keep progress honest if the milestone itself was flipped and has no tasks
    recompute_milestone_progress(db, m)
    now_done = m.status == RoadmapStatus.done
    if now_done and not was_done:
        roadmap = _require_roadmap(db, membership)
        event_bus.publish(
            "roadmap.milestone.completed",
            {
                "startup_id": str(membership.startup_id),
                "roadmap_id": str(roadmap.id),
                "milestone_id": str(m.id),
                "title": m.title,
            },
        )
    db.commit()
    return success_response(_milestone_out(db, m))


@router.delete("/milestones/{milestone_id}")
def delete_milestone_ep(
    milestone_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    m = _milestone(db, membership, milestone_id)
    db.delete(m)
    db.commit()
    return success_response({"deleted": True})


@router.post("/tasks", status_code=201)
def create_task_ep(
    body: TaskCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    milestone = _milestone(db, membership, body.milestone_id)  # 404 if foreign
    _validate_member(db, membership, body.assignee_id, "assignee_id")
    order = (
        body.order
        if body.order is not None
        else _next_order(db, RoadmapTask, milestone_id=milestone.id)
    )
    t = RoadmapTask(
        milestone_id=milestone.id,
        title=body.title,
        description=body.description,
        effort=body.effort or TaskEffort.medium,
        status=body.status or RoadmapStatus.todo,
        assignee_id=body.assignee_id,
        due_on=body.due_on,
        order=order,
    )
    db.add(t)
    db.flush()
    recompute_milestone_progress(db, milestone)
    db.commit()
    return success_response(_task_out(db, t))


@router.patch("/tasks/{task_id}")
def update_task_ep(
    task_id: uuid.UUID,
    body: TaskUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    t = _task(db, membership, task_id)
    fields = body.model_dump(exclude_unset=True)
    if "assignee_id" in fields:
        _validate_member(db, membership, fields["assignee_id"], "assignee_id")
    for field, value in fields.items():
        setattr(t, field, value)
    db.flush()
    milestone = db.query(RoadmapMilestone).filter_by(id=t.milestone_id).one()
    recompute_milestone_progress(db, milestone)
    db.commit()
    return success_response(_task_out(db, t))


@router.delete("/tasks/{task_id}")
def delete_task_ep(
    task_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    t = _task(db, membership, task_id)
    milestone_id = t.milestone_id
    db.delete(t)
    db.flush()
    milestone = db.query(RoadmapMilestone).filter_by(id=milestone_id).one()
    recompute_milestone_progress(db, milestone)
    db.commit()
    return success_response({"deleted": True})


@router.post("/tasks/{task_id}/dependencies", status_code=201)
def create_dependency_ep(
    task_id: uuid.UUID,
    body: DependencyCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    dependent = _task(db, membership, task_id)  # 404 if foreign
    if body.depends_on_task_id == dependent.id:
        raise AppError(
            "VALIDATION_ERROR",
            "A task cannot depend on itself.",
            422,
            field_errors=[
                {"field": "depends_on_task_id", "message": "A task cannot depend on itself."}
            ],
        )
    dependency = _task(db, membership, body.depends_on_task_id)  # 404 if foreign
    roadmap = _require_roadmap(db, membership)
    if would_create_cycle(db, roadmap.id, dependent.id, dependency.id):
        raise DependencyCycle(
            message=(
                f"That would create a loop — {dependency.title} already depends on "
                f"{dependent.title}."
            )
        )
    _row, created = add_dependency(db, dependent.id, dependency.id)
    db.commit()
    payload = {"task_id": str(dependent.id), "depends_on_task_id": str(dependency.id)}
    return JSONResponse(status_code=(201 if created else 200), content=success_response(payload))


@router.delete("/tasks/{task_id}/dependencies/{depends_on_task_id}")
def delete_dependency_ep(
    task_id: uuid.UUID,
    depends_on_task_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    dependent = _task(db, membership, task_id)  # 404 if foreign
    edge = (
        db.query(RoadmapTaskDependency)
        .filter_by(task_id=dependent.id, depends_on_task_id=depends_on_task_id)
        .first()
    )
    if edge is None:
        raise NotFound()
    db.delete(edge)
    db.commit()
    return success_response({"deleted": True})
