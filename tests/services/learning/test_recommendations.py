import pytest

from app.db.models.enums import EnrichmentStatus, ExperimentType, StartupStage
from app.db.models.job import Job
from app.db.models.learning import LearningRecommendation
from app.db.models.validation import Experiment
from app.platform.events import event_bus
from app.services.learning import recommendations as rec
from app.services.learning import service as svc
from app.services.learning.catalog import list_courses
from tests.learning_helpers import add_assessment, add_interview_and_experiment, founder_and_startup


@pytest.fixture(autouse=True)
def _content(academy):
    return academy


def _shelf(db, stage, gaps=None, done=frozenset()):
    gaps = gaps or rec.Gaps(())
    return [c.id for c in rec.rank_courses(list_courses(db), stage, set(done), gaps)]


def test_gaps_come_from_the_weakest_assessment_dimensions_and_validation_signals(db):
    user, startup = founder_and_startup(db)
    assert rec.gather_gaps(db, startup.id).tags == ("customers", "demand")  # nothing logged yet
    add_assessment(db, startup, {"product": 80, "market": 70, "money": 40, "legal": 55, "team": 90})
    gaps = rec.gather_gaps(db, startup.id)
    # money (40) then legal (55); money also means pricing; the validation signals follow
    assert gaps.tags == ("money", "pricing", "legal", "customers", "demand")
    add_interview_and_experiment(db, startup)
    assert rec.gather_gaps(db, startup.id).tags == ("money", "pricing", "legal")


def test_every_stage_has_a_shelf(db):
    for stage in StartupStage:
        assert _shelf(db, stage), stage


def test_the_shelf_follows_stage_then_gaps_then_level(db):
    base = _shelf(db, StartupStage.validation)
    assert base == [  # no gaps: beginner first, catalog order
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "validation-price-your-product",
        "build-scope-the-mvp",
    ]
    pricing = rec.Gaps(("pricing", "money"))
    assert _shelf(db, StartupStage.validation, pricing)[0] == "validation-price-your-product"
    assert _shelf(db, StartupStage.validation, done={"idea-shape-the-problem"})[0] == (
        "validation-talk-to-customers"
    )
    assert _shelf(db, None)[0] == "idea-shape-the-problem"  # no stage: beginner courses only
    assert all(
        c.level.value == "beginner"
        for c in rec.rank_courses(list_courses(db), None, set(), rec.Gaps(()))
    )


def test_reason_text_names_the_gap_or_falls_back_to_the_stage(db):
    course = next(c for c in list_courses(db) if c.id == "validation-price-your-product")
    assert rec.templated_reason(StartupStage.validation, course, ["pricing"]) == (
        "Your assessment flagged pricing as a gap, and Price your product covers it."
    )
    assert rec.templated_reason(StartupStage.validation, course, ["customers"]).startswith(
        "You have not logged a customer interview yet"
    )
    assert rec.templated_reason(StartupStage.idea, course, []) == "Recommended for your idea stage."
    assert rec.templated_reason(None, None, []) == "Recommended to help you get started."
    assert len(rec.templated_reason(None, course, ["pricing"])) <= 300


def test_one_reason_per_hero_course_regenerated_when_the_context_changes(db):
    user, startup = founder_and_startup(db)
    state = svc.load_state(db, startup.id, user.id)
    first = svc.build_recommendations(db, startup, user.id, state)
    hero_id = first["hero"]["course_id"]
    assert first["recommendation_reason"] == first["hero"]["reason"]
    assert first["hero"]["reason_source"] == "template"
    assert db.query(Job).filter_by(type="ai.learning.recommendations").count() == 1

    # same context again: nothing new queued
    svc.build_recommendations(db, startup, user.id, state)
    assert db.query(Job).filter_by(type="ai.learning.recommendations").count() == 1

    # the AI wrote a line; then a gap closes -> the reason is rewritten and queued again
    row = db.query(LearningRecommendation).filter_by(startup_id=startup.id, course_id=hero_id).one()
    row.reason, row.status = "AI line", EnrichmentStatus.ready
    db.flush()
    again = svc.build_recommendations(db, startup, user.id, state)
    assert again["hero"]["reason"] == "AI line" and again["hero"]["reason_source"] == "ai"
    db.add(Experiment(startup_id=startup.id, name="Smoke", type=ExperimentType.smoke_test))
    db.flush()  # the demand gap closes; the hero is unchanged, its reason is stale
    changed = svc.build_recommendations(db, startup, user.id, state)
    assert changed["hero"]["course_id"] == hero_id
    assert changed["hero"]["reason_source"] == "template"
    assert changed["hero"]["reason"] != "AI line"
    jobs = (
        db.query(Job).filter_by(type="ai.learning.recommendations").order_by(Job.created_at).all()
    )
    assert len(jobs) == 2
    assert jobs[-1].payload["course_id"] == hero_id and jobs[-1].payload["context_key"]


def test_a_new_hero_after_a_stage_change_notifies_once_but_the_first_row_does_not(db):
    user, startup = founder_and_startup(db, stage=StartupStage.idea)
    state = svc.load_state(db, startup.id, user.id)
    svc.build_recommendations(db, startup, user.id, state)
    assert not [
        e
        for e in event_bus.published
        if e[0] == "learning.course.recommended" and e[1]["startup_id"] == str(startup.id)
    ]

    startup.stage = StartupStage.launch
    db.flush()
    result = svc.build_recommendations(db, startup, user.id, state)
    events = [
        e
        for e in event_bus.published
        if e[0] == "learning.course.recommended" and e[1]["startup_id"] == str(startup.id)
    ]
    assert len(events) == 1
    assert events[0][1]["course_id"] == result["hero"]["course_id"]
    assert events[0][1]["user_id"] == str(user.id) and events[0][1]["stage"] == "launch"
    svc.build_recommendations(db, startup, user.id, state)  # a repeat read is not news
    assert (
        len(
            [
                e
                for e in event_bus.published
                if e[0] == "learning.course.recommended" and e[1]["startup_id"] == str(startup.id)
            ]
        )
        == 1
    )


def test_a_finished_stage_shelf_has_no_hero(db):
    user, startup = founder_and_startup(db, stage=StartupStage.build)
    state = svc.load_state(db, startup.id, user.id)
    for lesson in ("build-scope-the-mvp-1", "build-scope-the-mvp-2", "build-scope-the-mvp-3"):
        svc.update_lesson_progress(db, startup.id, user.id, lesson, completed=True, position_s=None)
    state = svc.load_state(db, startup.id, user.id)
    data = svc.build_recommendations(db, startup, user.id, state)
    assert data["hero"] is None and data["recommended"] == []
    assert data["recommendation_reason"] == "Recommended for your build stage."
