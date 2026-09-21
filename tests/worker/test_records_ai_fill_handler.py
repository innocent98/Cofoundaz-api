import pytest

from app.core.config import settings
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.job import Job, JobStatus
from app.services.business.records import create_record
from app.worker.handlers.ai import handle_record_ai_fill
from tests.factories import create_startup, create_user


def _job(startup_id, kind):
    return Job(
        type=f"business.{kind.value}.ai_fill",
        payload={"startup_id": str(startup_id), "kind": kind.value},
        status=JobStatus.running,
    )


def _count(db, s, kind):
    return db.query(BusinessRecord).filter_by(startup_id=s.id, kind=kind).count()


def test_ai_fill_populates_empty_kind(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    handle_record_ai_fill(db, _job(s.id, RecordKind.persona))
    n = _count(db, s, RecordKind.persona)
    assert 1 <= n <= 3
    rec = db.query(BusinessRecord).filter_by(startup_id=s.id, kind=RecordKind.persona).first()
    assert "[stub-llm]" in rec.data["name"]  # pydantic-valid record created


def test_ai_fill_noop_when_kind_not_empty(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_record(db, s, RecordKind.persona, {"name": "Mine"})
    handle_record_ai_fill(db, _job(s.id, RecordKind.persona))
    assert _count(db, s, RecordKind.persona) == 1  # unchanged


def test_ai_fill_noop_when_startup_missing(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    import uuid

    handle_record_ai_fill(db, _job(uuid.uuid4(), RecordKind.persona))  # no raise


def test_ai_fill_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    with pytest.raises(RuntimeError):
        handle_record_ai_fill(db, _job(s.id, RecordKind.competitor))
