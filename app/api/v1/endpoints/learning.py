from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.learning import EnrollmentCreate, LessonProgressUpdate
from app.services.learning.catalog import ALL_ARTICLES, ALL_COURSES, ALL_PATHS, get_course
from app.services.learning.service import (
    article_view,
    complete_lesson,
    completed_lesson_ids,
    continue_watching,
    course_detail,
    course_summary,
    enrollments_by_course,
    get_or_create_enrollment,
    get_or_create_recommendation_reason,
    list_certificates,
    path_view,
    recommended_courses,
    serialize_certificate,
    serialize_enrollment,
)

router = APIRouter()
# Founders and team members only, reads included (spec D6, PRD RBAC matrix line 900).
_academy = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


@router.get("/recommendations")
def get_recommendations(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    enrollments = enrollments_by_course(db, startup.id, membership.user_id)
    completed = {cid for cid, e in enrollments.items() if e.completed_at is not None}
    shelf = recommended_courses(startup.stage, completed)
    watching = continue_watching(db, startup.id, membership.user_id)
    reason_row = get_or_create_recommendation_reason(db, startup.id, startup.stage)
    db.commit()  # lazy-create persists (mirrors GET /canvases/{type})
    return success_response(
        {
            "stage": startup.stage.value if startup.stage is not None else None,
            "recommendation_reason": reason_row.reason,
            "recommended": [course_summary(c, enrollments.get(c.id)) for c in shelf],
            "continue_watching": [course_summary(c, e) for c, e in watching],
        }
    )


@router.get("/courses")
def list_courses(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollments = enrollments_by_course(db, membership.startup_id, membership.user_id)
    return success_response(
        {"courses": [course_summary(c, enrollments.get(c.id)) for c in ALL_COURSES]}
    )


@router.get("/courses/{course_id}")
def get_course_detail(
    course_id: str,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    course = get_course(course_id)  # 404 if unknown
    enrollments = enrollments_by_course(db, membership.startup_id, membership.user_id)
    done = completed_lesson_ids(db, membership.startup_id, membership.user_id, course.id)
    return success_response(course_detail(course, enrollments.get(course.id), done))


@router.get("/paths")
def list_paths(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollments = enrollments_by_course(db, membership.startup_id, membership.user_id)
    progress = {course_id: e.progress for course_id, e in enrollments.items()}
    return success_response({"paths": [path_view(p, progress) for p in ALL_PATHS]})


@router.get("/articles")
def list_articles(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"articles": [article_view(a) for a in ALL_ARTICLES]})


@router.post("/enrollments", status_code=201)
def create_enrollment(
    body: EnrollmentCreate,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    enrollment, created = get_or_create_enrollment(
        db, membership.startup_id, membership.user_id, body.course_id
    )
    db.commit()
    return JSONResponse(
        status_code=(201 if created else 200),
        content=success_response(serialize_enrollment(enrollment)),
    )


@router.patch("/lessons/{lesson_id}/progress")
def update_lesson_progress(
    lesson_id: str,
    body: LessonProgressUpdate,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollment, certificate = complete_lesson(
        db, membership.startup_id, membership.user_id, lesson_id
    )
    db.commit()
    return success_response(
        {
            "lesson_id": lesson_id,
            **serialize_enrollment(enrollment),
            "certificate": serialize_certificate(certificate) if certificate else None,
        }
    )


@router.get("/certificates")
def list_my_certificates(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_certificates(db, membership.startup_id, membership.user_id)
    return success_response({"certificates": [serialize_certificate(c) for c in rows]})
