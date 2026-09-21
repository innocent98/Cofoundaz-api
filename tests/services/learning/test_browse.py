from datetime import UTC, datetime, timedelta

from app.db.models.enums import MembershipRole, StartupStage
from app.services.learning.catalog import ARTICLES, COURSES, PATHS, LearningPath
from app.services.learning.service import (
    article_view,
    complete_lesson,
    completed_lesson_ids,
    continue_watching,
    course_detail,
    course_summary,
    enrollments_by_course,
    get_or_create_enrollment,
    path_progress,
    path_view,
    recommended_courses,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s)
    db.flush()
    return u, s


def _ids(courses):
    return [course.id for course in courses]


def test_recommendations_match_stage_beginner_first_then_catalog_order():
    assert _ids(recommended_courses(StartupStage.validation, set())) == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "build-scope-the-mvp",
    ]


def test_recommendations_exclude_completed_courses():
    completed = {"idea-shape-the-problem"}
    assert _ids(recommended_courses(StartupStage.validation, completed)) == [
        "validation-talk-to-customers",
        "build-scope-the-mvp",
    ]


def test_recommendations_for_a_single_stage():
    assert _ids(recommended_courses(StartupStage.launch, set())) == ["launch-plan-go-to-market"]


def test_recommendations_without_a_stage_fall_back_to_beginner_courses():
    assert _ids(recommended_courses(None, set())) == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
    ]


def test_path_percentage_rounds_exact_halves_to_even():
    path = LearningPath(id="t", title="t", stage=StartupStage.idea, course_ids=("a", "b", "c", "d"))
    assert path_progress(path, {"a": 100, "b": 50, "c": 50, "d": 50}) == 62  # 62.5 -> 62


def test_unenrolled_courses_count_as_zero_in_a_path():
    path = LearningPath(id="t", title="t", stage=StartupStage.idea, course_ids=("a", "b"))
    assert path_progress(path, {"a": 100}) == 50


def test_path_view_uses_the_average_of_course_progress():
    path = PATHS["path-validation-foundations"]
    view = path_view(path, {"idea-shape-the-problem": 100})
    assert view["progress"] == 33  # (100 + 0 + 0) / 3
    assert view["course_ids"] == list(path.course_ids)
    assert view["course_count"] == 3


def test_continue_watching_lists_enrolled_not_completed_most_recent_first(db):
    u, s = _ctx(db)
    older, _created = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    newer, _created = get_or_create_enrollment(db, s.id, u.id, "validation-talk-to-customers")
    older.updated_at = datetime.now(UTC) - timedelta(hours=1)
    newer.updated_at = datetime.now(UTC)
    for lesson in COURSES["build-scope-the-mvp"].lessons:  # completed -> excluded
        complete_lesson(db, s.id, u.id, lesson.id)
    db.flush()
    assert [course.id for course, _e in continue_watching(db, s.id, u.id)] == [
        "validation-talk-to-customers",
        "idea-shape-the-problem",
    ]


def test_continue_watching_never_shows_another_members_enrolments(db):
    u, s = _ctx(db)
    teammate = create_user(db)
    create_membership(db, teammate, s, role=MembershipRole.team_member)
    db.flush()
    get_or_create_enrollment(db, s.id, teammate.id, "idea-shape-the-problem")
    assert continue_watching(db, s.id, u.id) == []


def test_course_summary_and_detail_reflect_the_callers_progress(db):
    u, s = _ctx(db)
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    course = COURSES["idea-shape-the-problem"]
    enrollment = enrollments_by_course(db, s.id, u.id)["idea-shape-the-problem"]

    summary = course_summary(course, enrollment)
    assert summary["enrolled"] is True and summary["completed"] is False
    assert summary["progress"] == 33 and summary["lesson_count"] == 3

    done = completed_lesson_ids(db, s.id, u.id, course.id)
    detail = course_detail(course, enrollment, done)
    assert [lesson["completed"] for lesson in detail["lessons"]] == [True, False, False]

    untouched = course_summary(COURSES["scale-build-the-org"], None)
    assert untouched["enrolled"] is False and untouched["progress"] == 0


def test_article_view():
    view = article_view(ARTICLES["article-pricing-basics"])
    assert view["id"] == "article-pricing-basics"
    assert view["tags"] == ["pricing", "validation"]
