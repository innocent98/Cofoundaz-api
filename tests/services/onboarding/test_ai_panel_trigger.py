from app.db.models.enums import StartupStage
from app.db.models.job import Job
from app.services.onboarding.steps import _maybe_generate_ai_panel
from app.services.onboarding.workspace import serialize_state
from tests.factories import create_startup, create_user


def _jobs(db):
    return db.query(Job).filter(Job.type == "ai.onboarding.panel").count()


def _ready_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get first customers"]
    db.flush()
    return u, s


def test_generates_and_enqueues_once_when_signals_complete(db):
    u, s = _ready_startup(db)
    _maybe_generate_ai_panel(db, s)
    assert s.profile.ai_panel is not None  # templated instant value
    assert _jobs(db) == 1
    _maybe_generate_ai_panel(db, s)  # already set -> no re-enqueue
    assert _jobs(db) == 1


def test_no_enqueue_when_signals_incomplete(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)  # no goals
    _maybe_generate_ai_panel(db, s)
    assert s.profile.ai_panel is None
    assert _jobs(db) == 0


def test_serialize_state_includes_ai_panel(db):
    u, s = _ready_startup(db)
    body = serialize_state(db, s, u)
    assert "ai_panel" in body
    assert body["ai_panel"] is None  # not generated yet
    _maybe_generate_ai_panel(db, s)
    body = serialize_state(db, s, u)
    assert body["ai_panel"] == s.profile.ai_panel
