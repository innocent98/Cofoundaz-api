"""The 30-day trash purge: service, daily scheduling and the self-managed worker handler."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.db.models.comment import Comment
from app.db.models.document import (
    Document,
    DocumentFile,
    DocumentGrant,
    DocumentShare,
    DocumentVersion,
    SignatureRequest,
)
from app.db.models.enums import DocumentKind, MembershipRole, ShareAccess
from app.db.models.job import Job, JobStatus
from app.platform.events import event_bus
from app.services.documents.files import upload_file
from app.services.documents.service import (
    TRASH_RETENTION_DAYS,
    create_document,
    purge_expired_documents,
    trash_document,
)
from app.services.documents.shares import create_share
from app.services.documents.trash import run_purge
from app.worker import scheduler
from app.worker.handlers import scheduled
from app.worker.runner import SELF_MANAGED
from app.worker.scheduler import SCHED_DOCUMENT_TRASH_PURGE, scheduler_tick
from tests.documents_helpers import team

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def ws(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    startup, users = team(db)
    return startup, users[MembershipRole.founder]


def _doc(db, s, u, title="D"):
    return create_document(
        db,
        s,
        created_by_id=u.id,
        kind=DocumentKind.custom,
        title=title,
        sections=[],
        folder=None,
        template_key=None,
    )


def _file(db, s, u, name="a.pdf"):
    return upload_file(
        db,
        s,
        uploaded_by_id=u.id,
        filename=name,
        content_type="application/pdf",
        size_bytes=13,
        content=b"%PDF-1.4 body",
        folder=None,
    )


def _age(row, days):
    row.trashed_at = NOW - timedelta(days=days)


def test_retention_is_thirty_days():
    assert TRASH_RETENTION_DAYS == 30


def test_only_documents_past_retention_are_purged_with_their_dependents(db, ws):
    s, u = ws
    old, fresh, live = _doc(db, s, u, "old"), _doc(db, s, u, "fresh"), _doc(db, s, u, "live")
    create_share(
        db,
        old,
        shared_by_id=u.id,
        email="a@example.com",
        access_level=ShareAccess.view,
        expires_in_days=7,
    )
    db.add(
        DocumentGrant(
            startup_id=s.id,
            document_id=old.id,
            grantee_email="g@example.com",
            permission=ShareAccess.view,
        )
    )
    db.flush()
    _age(old, 31)
    _age(fresh, 29)
    _age(live, 0)
    live.trashed_at = None
    db.flush()
    assert purge_expired_documents(db, now=NOW) == 1
    db.expire_all()
    assert {d.title for d in db.query(Document).all()} == {"fresh", "live"}
    assert db.query(DocumentVersion).filter_by(document_id=old.id).count() == 0
    assert db.query(DocumentShare).filter_by(document_id=old.id).count() == 0
    assert db.query(DocumentGrant).filter_by(document_id=old.id).count() == 0
    assert db.query(DocumentVersion).filter_by(document_id=fresh.id).count() == 1
    assert db.query(Comment).count() == 0 or all(c.entity_id != old.id for c in db.query(Comment))


def test_boundary_exactly_thirty_days_is_purged(db, ws):
    s, u = ws
    d = _doc(db, s, u)
    _age(d, 30)
    db.flush()
    assert purge_expired_documents(db, now=NOW) == 1


def test_documents_in_the_trash_stay_until_then(db, ws):
    s, u = ws
    d = _doc(db, s, u)
    trash_document(db, d, actor_id=u.id)
    assert purge_expired_documents(db, now=datetime.now(UTC)) == 0


def test_purge_emits_an_event_per_document(db, ws):
    s, u = ws
    d = _doc(db, s, u)
    _age(d, 40)
    db.flush()
    before = len(event_bus.published)
    purge_expired_documents(db, now=NOW)
    assert [
        (e, p["document_id"]) for e, p in event_bus.published[before:] if e == "document.purged"
    ] == [("document.purged", str(d.id))]


def test_run_purge_removes_expired_files_and_their_objects_after_commit(db, ws, tmp_path):
    s, u = ws
    old, fresh = _file(db, s, u, "old.pdf"), _file(db, s, u, "fresh.pdf")
    old_key, fresh_key = old.storage_key, fresh.storage_key
    _age(old, 45)
    _age(fresh, 2)
    db.flush()
    assert len(list(tmp_path.rglob("*.pdf"))) == 2
    assert run_purge(db, now=NOW) == {"documents": 0, "files": 1}
    assert db.query(DocumentFile).filter_by(storage_key=old_key).count() == 0
    assert db.query(DocumentFile).filter_by(storage_key=fresh_key).count() == 1
    assert [p.name for p in tmp_path.rglob("*.pdf")] == [fresh_key.rsplit("/", 1)[1]]


def test_a_file_with_a_signature_request_is_never_purged(db, ws, tmp_path):
    s, u = ws
    f = _file(db, s, u)
    db.add(SignatureRequest(startup_id=s.id, file_id=f.id, title="NDA"))
    _age(f, 90)
    db.flush()
    assert run_purge(db, now=NOW)["files"] == 0
    assert db.query(DocumentFile).count() == 1 and len(list(tmp_path.rglob("*.pdf"))) == 1


def test_a_missing_object_does_not_fail_the_purge(db, ws, tmp_path):
    s, u = ws
    f = _file(db, s, u)
    for p in tmp_path.rglob("*.pdf"):
        p.unlink()
    _age(f, 40)
    db.flush()
    assert run_purge(db, now=NOW)["files"] == 1


# ---- scheduling ---------------------------------------------------------------------------


def test_due_item_is_one_global_job_per_local_day():
    [item] = scheduler._due_trash_purge(None, NOW)
    assert item.task_key == "documents.trash_purge"
    assert item.scope_key == "all" and item.period_key == "2026-10-25"
    assert item.job_type == SCHED_DOCUMENT_TRASH_PURGE == "scheduled.documents.trash_purge"
    assert item.startup_id is None and item.payload == {}


def test_two_ticks_in_a_day_enqueue_once_and_the_next_day_again(db):
    scheduler_tick(db, now=NOW)
    scheduler_tick(db, now=NOW + timedelta(hours=3))
    assert db.query(Job).filter_by(type=SCHED_DOCUMENT_TRASH_PURGE).count() == 1
    scheduler_tick(db, now=NOW + timedelta(days=1))
    assert db.query(Job).filter_by(type=SCHED_DOCUMENT_TRASH_PURGE).count() == 2


def test_the_handler_is_self_managed_and_purges(db, ws, monkeypatch):
    assert SCHED_DOCUMENT_TRASH_PURGE in SELF_MANAGED
    s, u = ws
    d = _doc(db, s, u)
    _age(d, 31)
    db.flush()
    monkeypatch.setattr(
        scheduled, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)})
    )
    job = Job(type=SCHED_DOCUMENT_TRASH_PURGE, payload={}, status=JobStatus.running)
    scheduled.handle_documents_trash_purge(db, job)
    assert db.query(Document).filter_by(id=d.id).count() == 0
