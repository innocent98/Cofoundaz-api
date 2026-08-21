from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.jobs import job_dispatcher
from app.services.roadmap.service import generate_roadmap, serialize_tree

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


def _roadmap(db: Session, membership: Membership) -> Roadmap | None:
    return db.query(Roadmap).filter(Roadmap.startup_id == membership.startup_id).first()


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
