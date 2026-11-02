import uuid
from datetime import date

from app.core.config import settings
from app.db.models.enums import EnrichmentStatus, RoadmapStatus, StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.journal import JournalEntry, JournalPrompt, MoodLog
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.platform import llm_budget
from app.worker.handlers.ai import handle_journal_prompt
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_usage_tokens = 5
        self.seen = ""

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        self.seen = " ".join(m.content for m in messages)
        return self.payload


def _shipped_milestone(db, startup, title="Shipped v1"):
    r = Roadmap(
        startup_id=startup.id,
        stage=StartupStage.build,
        template_key="default",
        template_version=1,
    )
    db.add(r)
    db.flush()
    p = RoadmapPhase(roadmap_id=r.id, name="P", order=0)
    db.add(p)
    db.flush()
    db.add(RoadmapMilestone(phase_id=p.id, title=title, status=RoadmapStatus.done))
    db.flush()


def _prompt_row(db, startup_id, founder_id, status=EnrichmentStatus.generating, prompt="static Q"):
    row = JournalPrompt(
        startup_id=startup_id,
        founder_id=founder_id,
        date=date.today(),
        prompt=prompt,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def _job(startup_id, founder_id):
    return Job(
        type="ai.journal.prompt",
        payload={
            "startup_id": str(startup_id),
            "founder_id": str(founder_id),
            "date": date.today().isoformat(),
        },
        status=JobStatus.running,
    )


def test_writes_prompt_and_marks_ready(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id)
    fake = _FakeLLM({"prompt": "How did shipping v1 change your week?"})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_journal_prompt(db, _job(s.id, u.id))
    row = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.prompt == "How did shipping v1 change your week?"
    assert fake.calls == 1


def test_no_signal_marks_ready_no_llm(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)  # no milestone, no mission
    _prompt_row(db, s.id, u.id, prompt="static Q")
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_journal_prompt(db, _job(s.id, u.id))
    row = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.prompt == "static Q"


def test_over_budget_keeps_generating(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id, prompt="static Q")
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_journal_prompt(db, _job(s.id, u.id))
    row = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert row.status == EnrichmentStatus.generating
    assert row.prompt == "static Q"


def test_idempotent_when_ready(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id, status=EnrichmentStatus.ready, prompt="done")
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_journal_prompt(db, _job(s.id, u.id))  # no raise, no LLM


def test_truncates_prompt_to_300(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"prompt": "y" * 500}))
    handle_journal_prompt(db, _job(s.id, u.id))
    assert (
        len(db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one().prompt) == 300
    )


def test_privacy_diary_and_mood_never_sent(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s, title="Closed first customer")
    db.add(
        JournalEntry(
            startup_id=s.id,
            founder_id=u.id,
            date=date.today(),
            content_encrypted="SECRET_DIARY",
            mood=1,
            stress=8,
        )
    )
    db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=date.today(), mood=1, stress=8))
    db.flush()
    _prompt_row(db, s.id, u.id)
    fake = _FakeLLM({"prompt": "What did closing your first customer teach you?"})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_journal_prompt(db, _job(s.id, u.id))
    assert "SECRET_DIARY" not in fake.seen
    assert "Closed first customer" in fake.seen


def test_noop_when_startup_missing(db):
    handle_journal_prompt(db, _job(uuid.uuid4(), uuid.uuid4()))  # no raise
