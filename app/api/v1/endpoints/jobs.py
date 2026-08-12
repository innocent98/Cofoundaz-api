import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.job import Job
from app.db.session import get_db

router = APIRouter()


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):  # noqa: B008
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
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
