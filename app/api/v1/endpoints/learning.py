from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.learning import EnrollmentCreate, LessonNoteUpdate, LessonProgressUpdate
from app.services.learning import certificates
from app.services.learning.catalog import (
    get_article,
    get_course,
    list_articles,
    list_courses,
    list_paths,
)
from app.services.learning.service import (
    article_detail,
    article_view,
    build_recommendations,
    certificate_view,
    certificate_views,
    course_detail,
    course_summary,
    get_note,
    get_or_create_enrollment,
    load_state,
    path_view,
    put_note,
    serialize_enrollment,
    update_lesson_progress,
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
    state = load_state(db, startup.id, membership.user_id)
    payload = build_recommendations(db, startup, membership.user_id, state)
    db.commit()  # lazy-create persists (mirrors GET /canvases/{type})
    return success_response(payload)


@router.get("/courses")
def list_all_courses(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    state = load_state(db, membership.startup_id, membership.user_id)
    return success_response({"courses": [course_summary(c, state) for c in list_courses(db)]})


@router.get("/courses/{course_id}")
def get_course_detail(
    course_id: str,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    course = get_course(db, course_id)  # 404 if unknown
    state = load_state(db, membership.startup_id, membership.user_id)
    return success_response(course_detail(course, state))


@router.get("/paths")
def list_all_paths(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    state = load_state(db, membership.startup_id, membership.user_id)
    courses = {c.id: c for c in list_courses(db)}
    return success_response({"paths": [path_view(p, courses, state) for p in list_paths(db)]})


@router.get("/articles")
def list_all_articles(
    q: str | None = Query(default=None, max_length=100),
    tag: str | None = Query(default=None, max_length=40),
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"articles": [article_view(a) for a in list_articles(db, q=q, tag=tag)]}
    )


@router.get("/articles/{article_id}")
def get_article_detail(
    article_id: str,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(article_detail(db, get_article(db, article_id)))


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
def update_progress(
    lesson_id: str,
    body: LessonProgressUpdate,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollment, certificate, position_s = update_lesson_progress(
        db,
        membership.startup_id,
        membership.user_id,
        lesson_id,
        completed=body.completed is True,
        position_s=body.position_s,
    )
    certificate_payload = certificate_view(db, certificate) if certificate else None
    db.commit()
    return success_response(
        {
            "lesson_id": lesson_id,
            **serialize_enrollment(enrollment),
            "position_s": position_s,
            "certificate": certificate_payload,
        }
    )


@router.get("/lessons/{lesson_id}/notes")
def read_note(
    lesson_id: str,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(get_note(db, membership.startup_id, membership.user_id, lesson_id))


@router.put("/lessons/{lesson_id}/notes")
def write_note(
    lesson_id: str,
    body: LessonNoteUpdate,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    note = put_note(db, membership.startup_id, membership.user_id, lesson_id, body.body)
    db.commit()
    return success_response(note)


@router.get("/certificates")
def list_my_certificates(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"certificates": certificate_views(db, membership.startup_id, membership.user_id)}
    )


@router.get("/certificates/{certificate_id}/pdf")
def download_certificate_pdf(
    certificate_id: str,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """The owner's certificate as a PDF. Scoped by workspace and user: any other id is a 404."""
    cert, pdf = certificates.owned_pdf(
        db, membership.startup_id, membership.user_id, certificate_id
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="cofoundaz-certificate-{cert.verify_code}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )
