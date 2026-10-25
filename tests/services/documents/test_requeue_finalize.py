"""The one-off re-queue of stuck executed-PDF jobs, and the `failed` executed state."""

import importlib

import pytest

from app.core.config import settings
from app.db.models.document import SignatureRequest, SignatureSigner
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.job import Job
from app.platform.email import ConsoleEmailSender
from app.services.documents import requeue_finalize
from app.services.documents.files import upload_file
from app.services.documents.signatures import (
    FINALIZE_JOB,
    create_request,
    executed_info,
    record_signature,
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
    importlib.reload(mod)
    mail = ConsoleEmailSender()
    monkeypatch.setattr(mod, "get_email_sender", lambda: mail)
    return startup, founder, mail


def _completed(db, startup, founder, emails=("a@x.com",)):
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
        signers=[{"email": e} for e in emails],
        expires_in_days=14,
    )
    for signer, _raw in pairs:
        record_signature(db, signer, typed_name="N", ip=None, user_agent=None)
    db.flush()
    return req


def _jobs(db, req):
    return [
        j
        for j in db.query(Job).filter_by(type=FINALIZE_JOB)
        if j.payload["request_id"] == str(req.id)
    ]


def _strand(db, req, status=JobStatus.failed):
    """What a request looks like after its job died."""
    for j in _jobs(db, req):
        j.status = status
        j.attempts = settings.WORKER_MAX_ATTEMPTS
    db.flush()


def test_a_stranded_request_is_requeued_once_and_finishes_through_run_once(db, world):
    startup, founder, mail = world
    req = _completed(db, startup, founder)
    _strand(db, req)
    assert requeue_finalize.requeue(db, dry_run=True) == {
        "stuck": 1,
        "skipped_active_job": 0,
        "enqueued": 0,
        "would_enqueue": 1,
    }
    assert len(_jobs(db, req)) == 1  # a dry run enqueues nothing
    assert requeue_finalize.requeue(db)["enqueued"] == 1
    again = requeue_finalize.requeue(db)  # the queued job is live: skipped, not duplicated
    assert (again["stuck"], again["skipped_active_job"], again["enqueued"]) == (1, 1, 0)
    assert len(_jobs(db, req)) == 2

    runner.run_once(db)
    db.refresh(req)
    assert req.executed_file_id and req.owner_emailed_at
    assert sorted(m.to for m in mail.sent) == ["a@x.com", "founder@example.com"]
    done = requeue_finalize.requeue(db)  # fully done: nothing left to do
    assert (done["stuck"], done["enqueued"]) == (0, 0)


def test_a_built_file_with_unsent_emails_is_requeued_and_only_the_unsent_get_mail(db, world):
    startup, founder, mail = world
    _completed(db, startup, founder, emails=("a@x.com", "b@x.com"))
    runner.run_once(db)
    sent_before = len(mail.sent)
    db.query(SignatureSigner).filter_by(email="b@x.com").update({"executed_emailed_at": None})
    db.flush()
    assert requeue_finalize.requeue(db)["enqueued"] == 1
    runner.run_once(db)
    assert [m.to for m in mail.sent[sent_before:]] == ["b@x.com"]  # nobody was emailed twice


def test_legacy_and_incomplete_requests_are_not_touched(db, world):
    startup, founder, _mail = world
    legacy = _completed(db, startup, founder)
    legacy.document_hash = None  # pre-v2
    open_req = create_request(
        db,
        upload_file(
            db,
            startup,
            uploaded_by_id=founder.id,
            filename="b.pdf",
            content_type="application/pdf",
            size_bytes=1,
            content=make_pdf(),
            folder=None,
        ),
        created_by_id=founder.id,
        title="B",
        signers=[{"email": "z@x.com"}],
        expires_in_days=14,
    )[0]
    db.query(Job).delete()
    db.flush()
    assert requeue_finalize.requeue(db)["stuck"] == 0
    assert open_req.status.value == "awaiting"


def test_executed_state_is_failed_only_when_no_job_is_alive_and_one_died(db, world):
    startup, founder, _mail = world
    req = _completed(db, startup, founder)
    assert executed_info(db, req)["state"] == "pending"  # job queued
    _strand(db, req, JobStatus.running)
    assert executed_info(db, req)["state"] == "pending"  # still being worked on
    _strand(db, req)
    assert executed_info(db, req) == {
        "state": "failed",
        "file_id": None,
        "filename": None,
        "size_bytes": None,
    }
    requeue_finalize.requeue(db)  # a fresh job: pending again, until it finishes
    assert executed_info(db, req)["state"] == "pending"
    runner.run_once(db)
    db.refresh(req)
    assert executed_info(db, req)["state"] == "ready"


def test_the_cli_prints_the_counts(db, world, capsys, monkeypatch):
    startup, founder, _mail = world
    req = _completed(db, startup, founder)
    _strand(db, req)
    db.commit = db.flush  # keep the test transaction
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    requeue_finalize.main(["--dry-run"])
    assert '"would_enqueue": 1' in capsys.readouterr().out
    assert db.query(SignatureRequest).count() == 1
