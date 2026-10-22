import uuid

from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.db.models.learning import Certificate
from app.services.learning.certificates import generate_pdf_for
from app.worker.runner import register_handler


def handle_certificate_generate(db: Session, job: Job) -> None:
    """Render the certificate PDF once. Idempotent: a repeat job finds the file and stops."""
    cert = db.get(Certificate, uuid.UUID(str(job.payload["certificate_id"])))
    if cert is None or cert.revoked_at is not None:
        return
    generate_pdf_for(db, cert)


register_handler("learning.certificate.generate", handle_certificate_generate)
