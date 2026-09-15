import pytest

from app.core.errors import NotFound
from app.db.models.enums import CourseLevel, StartupStage
from app.services.learning.catalog import (
    ALL_ARTICLES,
    ALL_COURSES,
    ALL_PATHS,
    ARTICLES,
    COURSES,
    LEARNING_CATALOG_VERSION,
    LESSONS,
    PATHS,
    PLACEHOLDER_PREFIX,
    course_duration_min,
    get_course,
    get_lesson,
    path_duration_min,
)


def test_catalog_is_versioned():
    assert LEARNING_CATALOG_VERSION == 1


def test_ids_are_unique_and_lesson_ids_are_unique_catalog_wide():
    assert len(COURSES) == len(ALL_COURSES)
    assert len(PATHS) == len(ALL_PATHS)
    assert len(ARTICLES) == len(ALL_ARTICLES)
    assert len(LESSONS) == sum(len(course.lessons) for course in ALL_COURSES)


def test_every_course_has_ordered_lessons_and_every_path_has_known_courses():
    for course in ALL_COURSES:
        assert course.lessons
        assert [lesson.order for lesson in course.lessons] == list(
            range(1, len(course.lessons) + 1)
        )
    for path in ALL_PATHS:
        assert path.course_ids
        assert all(course_id in COURSES for course_id in path.course_ids)


def test_content_is_labelled_placeholder_and_realistic_in_shape():
    titles = (
        [course.title for course in ALL_COURSES]
        + [lesson.title for course in ALL_COURSES for lesson in course.lessons]
        + [path.title for path in ALL_PATHS]
        + [article.title for article in ALL_ARTICLES]
    )
    assert all(title.startswith(PLACEHOLDER_PREFIX) for title in titles)
    assert {stage for course in ALL_COURSES for stage in course.stage_tags} == set(StartupStage)
    assert len({course.level for course in ALL_COURSES}) > 1
    assert any(len(path.course_ids) >= 2 for path in ALL_PATHS)
    assert len(ALL_ARTICLES) >= 2


def test_lookups_and_unknown_ids_404():
    assert get_course("idea-shape-the-problem").level == CourseLevel.beginner
    course, lesson = get_lesson("idea-shape-the-problem-1")
    assert course.id == "idea-shape-the-problem" and lesson.order == 1
    with pytest.raises(NotFound):
        get_course("nope")
    with pytest.raises(NotFound):
        get_lesson("nope")


def test_durations_are_derived():
    course = get_course("idea-shape-the-problem")
    assert course_duration_min(course) == sum(lesson.duration_min for lesson in course.lessons)
    path = PATHS["path-validation-foundations"]
    assert path_duration_min(path) == sum(
        course_duration_min(COURSES[course_id]) for course_id in path.course_ids
    )
