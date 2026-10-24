import importlib
import uuid
from datetime import UTC, datetime

import pytest

from app.db.models.job import Job, JobStatus
from app.db.models.learning import Certificate, CertificateFile
from app.services.learning import service as svc
from app.worker import runner
from app.worker.handlers import learning as handlers
from tests.learning_helpers import founder_and_startup


@pytest.fixture(autouse=True)
def _content(academy):
    return academy


@pytest.fixture(autouse=True)
def _registered():
    """Sibling tests clear the handler table; register ours the way importing the module does."""
    runner.JOB_HANDLERS.pop("learning.certificate.generate", None)
    importlib.reload(handlers)


def _certificate(db):
    user, startup = founder_and_startup(db)
    cert = None
    for n in (1, 2, 3):
        _e, got, _p = svc.update_lesson_progress(
            db, startup.id, user.id, f"build-scope-the-mvp-{n}", completed=True, position_s=None
        )
        cert = got or cert
    assert cert is not None
    return cert


def test_the_handler_is_registered_so_jobs_no_longer_fail_with_no_handler():
    assert (
        runner.JOB_HANDLERS["learning.certificate.generate"] is handlers.handle_certificate_generate
    )


def test_the_job_enqueued_on_completion_renders_the_pdf(db):
    cert = _certificate(db)
    job = db.query(Job).filter_by(type="learning.certificate.generate").one()
    handlers.handle_certificate_generate(db, job)
    assert db.get(CertificateFile, cert.id).content.startswith(b"%PDF")
    handlers.handle_certificate_generate(db, job)  # idempotent
    assert db.query(CertificateFile).count() == 1


def test_the_worker_loop_drains_the_job_to_succeeded(db):
    cert = _certificate(db)
    runner.run_once(db)
    job = db.query(Job).filter_by(type="learning.certificate.generate").one()
    assert job.status == JobStatus.succeeded, job.error
    assert db.get(CertificateFile, cert.id) is not None


def test_missing_or_revoked_certificates_are_skipped(db):
    cert = _certificate(db)
    ghost = Job(type="learning.certificate.generate", payload={"certificate_id": str(uuid.uuid4())})
    handlers.handle_certificate_generate(db, ghost)
    cert.revoked_at = datetime.now(UTC)
    db.flush()
    handlers.handle_certificate_generate(
        db, Job(type="learning.certificate.generate", payload={"certificate_id": str(cert.id)})
    )
    assert db.query(CertificateFile).count() == 0
    assert db.query(Certificate).count() == 1
