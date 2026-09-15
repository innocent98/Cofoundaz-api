import uuid
from datetime import UTC, datetime

from app.db.models.job import Job
from app.db.models.enums import JobStatus
from app.db.models.notification_preference import NotificationPreference
from tests.factories import create_startup, create_user


def test_preference_row_roundtrips(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    pref = NotificationPreference(
        user_id=u.id, startup_id=s.id, master_email=True, categories={"documents": False}
    )
    db.add(pref)
    db.flush()
    got = db.query(NotificationPreference).filter_by(user_id=u.id, startup_id=s.id).one()
    assert got.master_email is True and got.categories == {"documents": False}


def test_job_has_worker_columns(db):
    job = Job(type="email.notification", payload={"notification_id": str(uuid.uuid4())},
              status=JobStatus.queued)
    db.add(job)
    db.flush()
    assert job.attempts == 0 and job.run_after is None
