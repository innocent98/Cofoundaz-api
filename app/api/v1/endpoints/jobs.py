import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import MembershipStatus
from app.db.models.job import Job
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter()


@router.get("/{job_id}")
def get_job(
    job_id: uuid.UUID,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    job = db.query(Job).filter(Job.id == job_id).first()
    # Tenancy: a job is readable only by an active member of its workspace.
    # Unknown job, an orphan job with no startup_id, and a cross-workspace job all
    # return a uniform 404 so job existence can't be probed by an unrelated caller.
    # No X-Workspace-Id header is required — the FE polls jobs (e.g. right after
    # onboarding-complete) before it has a workspace header, so scope on the job's
    # own startup_id instead.
    if job is None or job.startup_id is None:
        raise NotFound()
    member = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.startup_id == job.startup_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )
    if member is None:
        raise NotFound()
    return success_response(
        {
            "id": str(job.id),
            "type": job.type,
            "status": job.status.value,
            "result": job.result,
            "error": job.error,
        }
    )
