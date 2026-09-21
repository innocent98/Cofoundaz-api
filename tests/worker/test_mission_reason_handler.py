import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import MissionStatus, TaskEffort
from app.db.models.job import Job, JobStatus
from app.db.models.mission import Mission, MissionTask
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_mission_reason
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *a, **k):  # pragma: no cover - not used here
        raise AssertionError("complete should not be called")

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _mission_with_tasks(db, startup, reasons):
    m = Mission(
        startup_id=startup.id,
        mission_date=__import__("datetime").date.today(),
        generated_by="system",
        status=MissionStatus.pending,
    )
    db.add(m)
    db.flush()
    for i, r in enumerate(reasons):
        db.add(
            MissionTask(
                mission_id=m.id, title=f"Task {i}", reason=r, effort=TaskEffort.medium, order=i
            )
        )
    db.flush()
    return m


def _job(mission_id):
    return Job(
        type="ai.mission.reason", payload={"mission_id": str(mission_id)}, status=JobStatus.running
    )


def test_rewrites_every_task_reason(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["From your 'A' milestone.", "From your 'B' milestone."])
    fake = _FakeLLM(
        {"reasons": [{"order": 0, "reason": "AI zero"}, {"order": 1, "reason": "AI one"}]}
    )
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_mission_reason(db, _job(m.id))
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    assert [t.reason for t in tasks] == ["AI zero", "AI one"]
    assert fake.calls == 1


def test_missing_order_keeps_templated_reason(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["templated-0", "templated-1"])
    fake = _FakeLLM({"reasons": [{"order": 0, "reason": "AI zero"}]})  # no order 1
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_mission_reason(db, _job(m.id))
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    assert tasks[0].reason == "AI zero"
    assert tasks[1].reason == "templated-1"  # fallback preserved


def test_noop_when_mission_missing(db):
    handle_mission_reason(db, _job(uuid.uuid4()))  # no raise


def test_noop_when_no_tasks(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, [])
    called = {"n": 0}
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError()))
    handle_mission_reason(db, _job(m.id))  # returns before any LLM client construction
    assert called["n"] == 0


def test_stub_marks_first_task(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["t0", "t1"])
    handle_mission_reason(db, _job(m.id))
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    assert "[stub-llm]" in tasks[0].reason  # order 0 upgraded
    assert tasks[1].reason == "t1"  # stub returns one item only -> fallback


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["t0"])
    with pytest.raises(RuntimeError):
        handle_mission_reason(db, _job(m.id))
