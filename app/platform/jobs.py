import uuid

from sqlalchemy.orm import Session

from app.db.models.enums import JobStatus
from app.db.models.job import Job


class JobDispatcher:
    """v1 stub: persists a queued row. A real worker drains it in Modules 05/06."""

    def enqueue(
        self,
        db: Session,
        type: str,
        payload: dict,
        startup_id: uuid.UUID | None = None,
    ) -> Job:
        job = Job(type=type, payload=payload, startup_id=startup_id, status=JobStatus.queued)
        db.add(job)
        db.flush()
        return job


job_dispatcher = JobDispatcher()
