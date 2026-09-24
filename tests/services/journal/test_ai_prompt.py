from datetime import date

from app.db.models.enums import MissionStatus, RoadmapStatus, StartupStage, TaskEffort
from app.db.models.journal import JournalEntry, MoodLog
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.services.journal.ai_prompt import (
    build_journal_prompt_messages,
    gather_prompt_context,
    journal_prompt_schema,
)
from tests.factories import create_startup, create_user


def _shipped_milestone(db, startup, title):
    r = Roadmap(
        startup_id=startup.id, stage=StartupStage.build, template_key="default", template_version=1
    )
    db.add(r)
    db.flush()
    p = RoadmapPhase(roadmap_id=r.id, name="Phase 1", order=0)
    db.add(p)
    db.flush()
    m = RoadmapMilestone(phase_id=p.id, title=title, status=RoadmapStatus.done)
    db.add(m)
    db.flush()
    return m


def _mission_with_task(db, startup, task_title):
    mi = Mission(
        startup_id=startup.id,
        mission_date=date.today(),
        generated_by="system",
        status=MissionStatus.pending,
    )
    db.add(mi)
    db.flush()
    db.add(MissionTask(mission_id=mi.id, title=task_title, effort=TaskEffort.medium, order=0))
    db.flush()


def test_schema_shape():
    s = journal_prompt_schema()
    assert s["properties"]["prompt"]["type"] == "string"
    assert s["required"] == ["prompt"]


def test_gather_returns_operational_signals(db):
    s = create_startup(db, owner=create_user(db))
    _shipped_milestone(db, s, "Launched the beta")
    _mission_with_task(db, s, "Email 10 leads")
    milestone_title, mission_focus = gather_prompt_context(db, s.id)
    assert milestone_title == "Launched the beta"
    assert mission_focus == "Email 10 leads"


def test_gather_returns_none_without_signals(db):
    s = create_startup(db, owner=create_user(db))
    assert gather_prompt_context(db, s.id) == (None, None)


def test_privacy_journal_and_mood_never_surface(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s, "Launched the beta")
    # Seed a diary entry + mood with distinctive content that must NOT reach the prompt.
    db.add(
        JournalEntry(
            startup_id=s.id,
            founder_id=u.id,
            date=date.today(),
            content_encrypted="SECRET_DIARY_TEXT",
            mood=1,
            stress=9,
        )
    )
    db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=date.today(), mood=1, stress=9))
    db.flush()
    milestone_title, mission_focus = gather_prompt_context(db, s.id)
    msgs = build_journal_prompt_messages(
        milestone_title=milestone_title, mission_focus=mission_focus
    )
    blob = " ".join(m.content for m in msgs)
    assert "SECRET_DIARY_TEXT" not in blob
    assert "stress" not in blob.lower()
    assert "Launched the beta" in blob  # operational signal is present


def test_build_messages_shape():
    msgs = build_journal_prompt_messages(milestone_title="X", mission_focus=None)
    assert msgs[0].role == "system" and msgs[1].role == "user"
    assert "X" in msgs[1].content
