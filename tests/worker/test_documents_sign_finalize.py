"""The `documents.signature_finalize` job: executed PDF + one email per party, resumable."""

import importlib
import uuid

import pytest
from pypdf import PdfReader

from app.core.config import settings
from app.db.models.document import DocumentFile, SignatureRequest, SignatureSigner
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.job import Job
from app.platform.email import ConsoleEmailSender
from app.services.documents.files import upload_file
from app.services.documents.signatures import (
    FINALIZE_JOB,
    create_request,
    record_signature,
    signer_token,
)
from app.worker import runner
from tests.documents_helpers import team
from tests.pdf_helpers import make_pdf


@pytest.fixture
def world(db, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    startup, users = team(db)
    founder = users[MembershipRole.founder]
    founder.email = "founder@example.com"
    db.flush()
    mod = importlib.import_module("app.worker.handlers.documents_sign")
    importlib.reload(mod)  # registers the handler against this test's runner state
    mail = ConsoleEmailSender()
    monkeypatch.setattr(mod, "get_email_sender", lambda: mail)
    return startup, founder, mod, mail


def _completed(db, startup, founder):
    f = upload_file(
        db,
        startup,
        uploaded_by_id=founder.id,
        filename="nda.pdf",
        content_type="application/pdf",
        size_bytes=1,
        content=make_pdf(),
        folder=None,
    )
    req, pairs = create_request(
        db,
        f,
        created_by_id=founder.id,
        title="NDA",
        signers=[{"email": "a@x.com", "name": "A"}, {"email": "b@x.com", "name": "B"}],
        expires_in_days=14,
    )
    for signer, _raw in pairs:
        record_signature(
            db, signer, typed_name=f"Name {signer.email}", ip="203.0.113.9", user_agent="UA"
        )
    db.flush()
    return f, req


def _job(db, req) -> Job:
    return next(
        j
        for j in db.query(Job).filter_by(type=FINALIZE_JOB)
        if j.payload["request_id"] == str(req.id)
    )


def test_the_job_is_self_managed_and_runs_through_run_once(db, world):
    startup, founder, _mod, mail = world
    f, req = _completed(db, startup, founder)
    assert FINALIZE_JOB in runner.SELF_MANAGED
    assert runner.run_once(db) >= 1
    assert _job(db, req).status == JobStatus.succeeded
    db.refresh(req)
    assert req.executed_file_id and req.executed_hash and req.owner_emailed_at
    executed = db.get(DocumentFile, req.executed_file_id)
    assert executed.filename == "nda - signed.pdf" and executed.is_private and executed.url is None
    assert sorted(m.to for m in mail.sent) == ["a@x.com", "b@x.com", "founder@example.com"]
    assert all(
        m.subject == "Signed: NDA" and m.attachments[0].filename == "nda - signed.pdf"
        for m in mail.sent
    )
    assert {s.executed_emailed_at is not None for s in db.query(SignatureSigner)} == {True}


def test_rerunning_the_job_neither_duplicates_the_file_nor_the_emails(db, world):
    startup, founder, _mod, mail = world
    _f, req = _completed(db, startup, founder)
    runner.run_once(db)
    first_file, sent = req.executed_file_id, len(mail.sent)
    db.add(Job(type=FINALIZE_JOB, payload={"request_id": str(req.id)}, status=JobStatus.queued))
    db.flush()
    runner.run_once(db)
    db.refresh(req)
    assert req.executed_file_id == first_file and len(mail.sent) == sent
    assert db.query(DocumentFile).filter(DocumentFile.filename.like("% - signed.pdf")).count() == 1


def test_a_failed_email_is_retried_for_that_party_only(db, world, monkeypatch):
    startup, founder, mod, mail = world
    _f, req = _completed(db, startup, founder)

    class Flaky(ConsoleEmailSender):
        fail_for = {"b@x.com"}

        def send(self, msg):
            if msg.to in self.fail_for:
                raise ConnectionError("smtp down")
            super().send(msg)

    flaky = Flaky()
    monkeypatch.setattr(mod, "get_email_sender", lambda: flaky)
    runner.run_once(db)
    job = _job(db, req)
    assert job.status == JobStatus.queued and "failed" in job.error  # will retry
    assert sorted(m.to for m in flaky.sent) == ["a@x.com", "founder@example.com"]
    db.refresh(req)
    assert req.executed_file_id is not None  # the file survived the failed sends

    flaky.fail_for = set()
    job.run_after = None
    db.flush()
    runner.run_once(db)
    assert _job(db, req).status == JobStatus.succeeded
    assert sorted(m.to for m in flaky.sent) == ["a@x.com", "b@x.com", "founder@example.com"]


def test_a_missing_request_or_source_file_is_handled(db, world):
    startup, founder, _mod, _mail = world
    db.add(
        Job(type=FINALIZE_JOB, payload={"request_id": str(uuid.uuid4())}, status=JobStatus.queued)
    )
    db.flush()
    runner.run_once(db)  # nothing to do: succeeds quietly
    assert db.query(Job).filter_by(type=FINALIZE_JOB).one().status == JobStatus.succeeded
    db.query(Job).delete()
    db.flush()

    f, req = _completed(db, startup, founder)
    from app.platform.storage import get_storage

    get_storage().delete_private(f.storage_key)
    runner.run_once(db)
    job = _job(db, req)
    assert job.status == JobStatus.queued and "content is missing" in job.error
    db.refresh(req)
    assert req.executed_file_id is None


def test_the_stamped_pdf_keeps_the_original_pages_first(db, world):
    startup, founder, _mod, _mail = world
    f, req = _completed(db, startup, founder)
    runner.run_once(db)
    db.refresh(req)
    from app.platform.storage import get_storage

    out = PdfReader(
        __import__("io").BytesIO(
            get_storage().read_private(db.get(DocumentFile, req.executed_file_id).storage_key)
        )
    )
    assert out.pages[0].extract_text().strip() == "Test agreement"
    assert "Audit certificate" in out.pages[-1].extract_text()
    assert db.get(SignatureRequest, req.id).document_hash in out.pages[-1].extract_text()
    assert signer_token  # imported for the sibling tests' parity


def test_a_party_claimed_by_another_job_is_not_emailed_again(db, world):
    from app.worker.handlers.documents_sign import _claim, _unclaim

    startup, founder, _mod, mail = world
    _f, req = _completed(db, startup, founder)
    a = db.query(SignatureSigner).filter_by(email="a@x.com").one()
    assert _claim(db, SignatureSigner, a.id, "executed_emailed_at") is True
    assert _claim(db, SignatureSigner, a.id, "executed_emailed_at") is False  # the second job loses
    runner.run_once(db)
    assert "a@x.com" not in [m.to for m in mail.sent]  # the claim holder is responsible for it
    _unclaim(db, SignatureSigner, a.id, "executed_emailed_at")
    assert _claim(db, SignatureSigner, a.id, "executed_emailed_at") is True
