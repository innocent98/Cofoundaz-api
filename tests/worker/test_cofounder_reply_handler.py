import uuid

from app.core.config import settings
from app.db.models.ai_chat import AiMessage
from app.db.models.enums import ChatMessageRole, ChatMessageStatus, MembershipRole, StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.llm_usage import LlmUsageDaily
from app.platform import llm_budget
from app.platform.llm_budget import today_utc
from app.services.ai_cofounder.service import (
    BUDGET_FAILED_TEXT,
    REPLY_FAILED_TEXT,
    create_conversation,
)
from app.worker import runner
from app.worker.handlers.ai import handle_cofounder_reply
from tests.factories import create_membership, create_startup, create_user


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.last_usage_tokens = 7
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _conv_with_pending(db):
    u = create_user(db)
    s = create_startup(
        db, owner=u, industry="Fintech", stage=StartupStage.idea, description="Savings app"
    )
    s.profile.goals = ["Get first customers"]
    create_membership(db, u, s, role=MembershipRole.founder)
    conv = create_conversation(db, s, u, "What should I focus on?")
    db.flush()
    assistant = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == conv.id,
            AiMessage.role == ChatMessageRole.assistant,
        )
        .one()
    )
    return s, assistant


def _job(message_id, startup_id):
    return Job(
        type="ai.cofounder.reply",
        payload={"message_id": str(message_id), "startup_id": str(startup_id)},
        status=JobStatus.running,
    )


def test_fills_assistant_turn(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s, assistant = _conv_with_pending(db)
    fake = _FakeLLM("Focus on your MVP and first customers.")
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)

    handle_cofounder_reply(db, _job(assistant.id, s.id))

    db.refresh(assistant)
    assert assistant.status == ChatMessageStatus.ready
    assert assistant.content == "Focus on your MVP and first customers."
    assert fake.calls == 1


def test_stub_fills_turn(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, assistant = _conv_with_pending(db)

    handle_cofounder_reply(db, _job(assistant.id, s.id))

    db.refresh(assistant)
    assert assistant.status == ChatMessageStatus.ready
    assert "[stub-llm]" in assistant.content


def test_over_budget_marks_failed(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    s, assistant = _conv_with_pending(db)
    db.add(LlmUsageDaily(startup_id=s.id, usage_date=today_utc(), tokens_used=100))
    db.flush()

    handle_cofounder_reply(db, _job(assistant.id, s.id))

    db.refresh(assistant)
    assert assistant.status == ChatMessageStatus.failed
    assert assistant.content == BUDGET_FAILED_TEXT


def test_noop_when_message_missing(db):
    handle_cofounder_reply(db, _job(uuid.uuid4(), uuid.uuid4()))  # benign no-op, no raise


def test_noop_when_not_generating(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, assistant = _conv_with_pending(db)
    assistant.status = ChatMessageStatus.ready
    assistant.content = "already answered"
    db.flush()

    handle_cofounder_reply(db, _job(assistant.id, s.id))

    db.refresh(assistant)
    assert assistant.content == "already answered"  # untouched


class _EmptyLLM:
    last_usage_tokens = 0

    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        raise RuntimeError("LLM API returned an empty completion (finish_reason=length).")


def _drain_ready(db, job_type="ai.cofounder.reply"):
    """Make every queued job of this type due now so run_once claims it."""
    from datetime import UTC, datetime

    for j in db.query(Job).filter(Job.type == job_type):
        j.run_after = datetime.now(UTC)
    db.flush()


def test_empty_completion_retries_then_fails_the_message_via_run_once(db, monkeypatch):
    """Finance D9(a): after the last retry the turn is `failed` with a visible message,
    never left `generating`."""
    from app.platform.events import event_bus
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers the handler)

    monkeypatch.setattr(settings, "WORKER_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 0)
    s, assistant = _conv_with_pending(db)
    fake = _EmptyLLM()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    job = db.query(Job).filter(Job.type == "ai.cofounder.reply").one()
    seen = len(event_bus.published)

    for attempt in (1, 2):
        _drain_ready(db)
        assert runner.run_once(db) == 1
        db.refresh(assistant)
        db.refresh(job)
        assert assistant.status == ChatMessageStatus.generating  # still retrying
        assert job.status == JobStatus.queued and job.attempts == attempt

    _drain_ready(db)
    assert runner.run_once(db) == 1
    db.refresh(assistant)
    db.refresh(job)
    assert fake.calls == 3
    assert assistant.status == ChatMessageStatus.failed
    assert assistant.content == REPLY_FAILED_TEXT
    assert job.status == JobStatus.succeeded  # the turn ended; nothing left to retry
    new_events = [e for e, _p in event_bus.published[seen:]]
    assert "ai.cofounder.reply.failed" in new_events


def test_stale_generating_message_is_failed(db):
    from datetime import UTC, datetime, timedelta

    from app.services.ai_cofounder.service import fail_stale_generating

    s, assistant = _conv_with_pending(db)
    conv = assistant.conversation_id
    from app.db.models.ai_chat import AiConversation

    conversation = db.get(AiConversation, conv)
    assert fail_stale_generating(db, conversation) == 0  # fresh turn left alone
    assistant.created_at = datetime.now(UTC) - timedelta(
        seconds=settings.AI_REPLY_STALE_SECONDS + 5
    )
    db.flush()

    assert fail_stale_generating(db, conversation) == 1
    db.refresh(assistant)
    assert assistant.status == ChatMessageStatus.failed
    assert assistant.content == REPLY_FAILED_TEXT


def test_failed_call_tokens_are_still_debited(monkeypatch):
    """A call that burned tokens then failed is billed on its own session."""
    from app.platform import llm_budget as lb

    billed = []
    monkeypatch.setattr(lb, "_debit_independent", lambda sid, n: billed.append(n))
    monkeypatch.setattr(lb, "over_budget", lambda db, sid: False)

    class _Burn:
        last_usage_tokens = 1700

        def complete(self, messages, *, max_tokens, temperature=0.7):
            raise RuntimeError("empty")

    monkeypatch.setattr(lb, "get_llm_client", lambda: _Burn())
    import pytest

    with pytest.raises(RuntimeError):
        lb.metered_complete(None, uuid.uuid4(), [], max_tokens=10)
    assert billed == [1700]


def test_debit_independent_commits_on_its_own_session(monkeypatch):
    from app.db import session as session_mod
    from app.platform import llm_budget as lb

    events = []

    class _Own:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            events.append("commit")

    monkeypatch.setattr(session_mod, "SessionLocal", lambda: _Own())
    monkeypatch.setattr(lb, "debit", lambda own, sid, n: events.append(("debit", n)))
    lb._debit_independent(uuid.uuid4(), 0)
    assert events == []  # nothing to bill
    lb._debit_independent(uuid.uuid4(), 42)
    assert events == [("debit", 42), "commit"]


def test_empty_completion_ends_the_turn_on_the_first_attempt(db, monkeypatch):
    """A repeatable empty completion must not be retried: it would only burn the daily budget."""
    from app.platform.events import event_bus
    from app.platform.llm import EmptyCompletionError
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers the handler)

    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 0)
    s, assistant = _conv_with_pending(db)

    class _Empty:
        last_usage_tokens = 0
        calls = 0

        def complete(self, messages, *, max_tokens, temperature=0.7):
            _Empty.calls += 1
            raise EmptyCompletionError("empty (finish_reason=length)")

    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _Empty())
    job = db.query(Job).filter(Job.type == "ai.cofounder.reply").one()
    seen = len(event_bus.published)

    assert runner.run_once(db) == 1
    db.refresh(assistant)
    db.refresh(job)
    assert _Empty.calls == 1
    assert assistant.status == ChatMessageStatus.failed
    assert assistant.content == REPLY_FAILED_TEXT
    assert job.status == JobStatus.succeeded and job.attempts == 1
    assert "ai.cofounder.reply.failed" in [e for e, _p in event_bus.published[seen:]]


def test_headroom_reports_remaining_tokens(db, monkeypatch):
    s, _assistant = _conv_with_pending(db)
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 0)
    assert llm_budget.headroom(db, s.id) is None
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1000)
    assert llm_budget.headroom(db, s.id) == 1000
    db.add(LlmUsageDaily(startup_id=s.id, usage_date=today_utc(), tokens_used=1500))
    db.flush()
    assert llm_budget.headroom(db, s.id) == 0
