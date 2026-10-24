"""Learning Academy catalog reads (Module 17).

The catalog lives in the database. ``app.services.learning.content`` upserts it by slug from the
markdown under ``content/academy/``; everything here is a read. Ids are the slugs. Enrolment,
lesson-progress and certificate rows store them as plain text, so never rename a slug that
learners already reference.
"""

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.learning import AcademyArticle, AcademyCourse, AcademyLesson, AcademyPath


def list_courses(db: Session) -> list[AcademyCourse]:
    return db.query(AcademyCourse).order_by(AcademyCourse.position, AcademyCourse.id).all()


def get_course(db: Session, course_id: str) -> AcademyCourse:
    course = db.get(AcademyCourse, course_id)
    if course is None:
        raise NotFound()
    return course


def get_lesson(db: Session, lesson_id: str) -> tuple[AcademyCourse, AcademyLesson]:
    lesson = db.get(AcademyLesson, lesson_id)
    if lesson is None:
        raise NotFound()
    return get_course(db, lesson.course_id), lesson


def list_paths(db: Session) -> list[AcademyPath]:
    return db.query(AcademyPath).order_by(AcademyPath.position, AcademyPath.id).all()


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_articles(
    db: Session, *, q: str | None = None, tag: str | None = None
) -> list[AcademyArticle]:
    """Articles in editorial order, optionally filtered by a search term and/or a tag."""
    query = db.query(AcademyArticle)
    term = (q or "").strip()
    if term:
        pattern = _like(term)
        query = query.filter(
            AcademyArticle.title.ilike(pattern, escape="\\")
            | AcademyArticle.summary.ilike(pattern, escape="\\")
            | AcademyArticle.body.ilike(pattern, escape="\\")
        )
    wanted = (tag or "").strip().lower()
    if wanted:
        query = query.filter(AcademyArticle.tags.contains([wanted]))
    return query.order_by(AcademyArticle.position, AcademyArticle.id).all()


def get_article(db: Session, article_id: str) -> AcademyArticle:
    article = db.get(AcademyArticle, article_id)
    if article is None:
        raise NotFound()
    return article


def course_duration_min(course: AcademyCourse) -> int:
    return sum(lesson.duration_min for lesson in course.lessons)
