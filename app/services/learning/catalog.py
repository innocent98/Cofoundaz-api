"""Learning Academy catalog (Module 17) -- read-only, versioned, in-code.

ALL CONTENT IN THIS FILE IS PLACEHOLDER. Every course, lesson, path and article title starts with
``[Placeholder] ``. Replacing it with real content is a required pre-go-live task (spec D8).

The shape is normalized so it maps one-to-one onto tables when the Module 25.4 CMS moves the
catalog into the database: courses own their ordered lessons, and paths reference courses by id
in order rather than copying them (spec D1).

Ids are stable. Enrolment, lesson-progress and certificate rows store them, so never rename an id
that anything references. Lesson ids are unique across the whole catalog.
"""

from dataclasses import dataclass

from app.core.errors import NotFound
from app.db.models.enums import CourseLevel, StartupStage

LEARNING_CATALOG_VERSION = 1
PLACEHOLDER_PREFIX = "[Placeholder] "


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    order: int
    video_ref: str
    duration_min: int
    transcript: str


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    level: CourseLevel
    stage_tags: tuple[StartupStage, ...]
    lessons: tuple[Lesson, ...]


@dataclass(frozen=True)
class LearningPath:
    id: str
    title: str
    stage: StartupStage
    course_ids: tuple[str, ...]


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    tags: tuple[str, ...]
    body: str


def _course(
    course_id: str,
    title: str,
    level: CourseLevel,
    stage_tags: tuple[StartupStage, ...],
    lessons: tuple[tuple[str, int], ...],
) -> Course:
    """Build a placeholder course; lesson ids are ``<course_id>-<n>``, unique catalog-wide."""
    return Course(
        id=course_id,
        title=PLACEHOLDER_PREFIX + title,
        level=level,
        stage_tags=stage_tags,
        lessons=tuple(
            Lesson(
                id=f"{course_id}-{n}",
                title=PLACEHOLDER_PREFIX + lesson_title,
                order=n,
                video_ref=f"placeholder://{course_id}/{n}",
                duration_min=duration_min,
                transcript="Placeholder transcript.",
            )
            for n, (lesson_title, duration_min) in enumerate(lessons, start=1)
        ),
    )


ALL_COURSES: tuple[Course, ...] = (
    _course(
        "idea-shape-the-problem",
        "Shape the problem",
        CourseLevel.beginner,
        (StartupStage.idea, StartupStage.validation),
        (
            ("Write a problem statement", 8),
            ("Find who has the problem", 10),
            ("Size the pain", 12),
        ),
    ),
    _course(
        "validation-talk-to-customers",
        "Talk to customers",
        CourseLevel.beginner,
        (StartupStage.validation,),
        (
            ("Recruit interviewees", 9),
            ("Run the interview", 14),
            ("Synthesize what you heard", 11),
        ),
    ),
    _course(
        "build-scope-the-mvp",
        "Scope the MVP",
        CourseLevel.intermediate,
        (StartupStage.validation, StartupStage.build),
        (
            ("Pick the one core flow", 12),
            ("Cut everything else", 10),
        ),
    ),
    _course(
        "launch-plan-go-to-market",
        "Plan your go-to-market",
        CourseLevel.intermediate,
        (StartupStage.launch,),
        (
            ("Choose launch channels", 11),
            ("Write launch messaging", 9),
        ),
    ),
    _course(
        "growth-find-a-channel",
        "Find a repeatable channel",
        CourseLevel.advanced,
        (StartupStage.growth,),
        (
            ("Test three channels", 13),
            ("Double down on the winner", 10),
        ),
    ),
    _course(
        "scale-build-the-org",
        "Build the org",
        CourseLevel.advanced,
        (StartupStage.scale,),
        (
            ("Document core processes", 12),
            ("Hire against the plan", 14),
        ),
    ),
)

ALL_PATHS: tuple[LearningPath, ...] = (
    LearningPath(
        id="path-validation-foundations",
        title=PLACEHOLDER_PREFIX + "Validation foundations",
        stage=StartupStage.validation,
        course_ids=(
            "idea-shape-the-problem",
            "validation-talk-to-customers",
            "build-scope-the-mvp",
        ),
    ),
    LearningPath(
        id="path-launch-to-growth",
        title=PLACEHOLDER_PREFIX + "From launch to growth",
        stage=StartupStage.launch,
        course_ids=("launch-plan-go-to-market", "growth-find-a-channel"),
    ),
)

ALL_ARTICLES: tuple[Article, ...] = (
    Article(
        id="article-interview-questions",
        title=PLACEHOLDER_PREFIX + "Questions that get honest answers",
        tags=("validation", "customers"),
        body="Placeholder article body.",
    ),
    Article(
        id="article-pricing-basics",
        title=PLACEHOLDER_PREFIX + "Pricing basics",
        tags=("pricing", "validation"),
        body="Placeholder article body.",
    ),
)

COURSES: dict[str, Course] = {course.id: course for course in ALL_COURSES}
PATHS: dict[str, LearningPath] = {path.id: path for path in ALL_PATHS}
ARTICLES: dict[str, Article] = {article.id: article for article in ALL_ARTICLES}
LESSONS: dict[str, tuple[Course, Lesson]] = {
    lesson.id: (course, lesson) for course in ALL_COURSES for lesson in course.lessons
}


def get_course(course_id: str) -> Course:
    course = COURSES.get(course_id)
    if course is None:
        raise NotFound()
    return course


def get_lesson(lesson_id: str) -> tuple[Course, Lesson]:
    found = LESSONS.get(lesson_id)
    if found is None:
        raise NotFound()
    return found


def course_duration_min(course: Course) -> int:
    return sum(lesson.duration_min for lesson in course.lessons)


def path_duration_min(path: LearningPath) -> int:
    return sum(course_duration_min(COURSES[course_id]) for course_id in path.course_ids)
