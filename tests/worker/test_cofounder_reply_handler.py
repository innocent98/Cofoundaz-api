import uuid

from app.core.config import settings
from app.db.models.ai_chat import AiMessage
from app.db.models.enums import ChatMessageRole, ChatMessageStatus, MembershipRole, StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.llm_usage import LlmUsageDaily
from app.platform import llm_budget
from app.platform.llm_budget import today_utc
from app.services.ai_cofounder.service import create_conversation
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
    assert assistant.content == ""


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
