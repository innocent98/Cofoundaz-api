import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.assessment import Assessment, AssessmentAnswer, AssessmentResult
from app.db.models.enums import AssessmentStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.activity import write_activity
from app.schemas.assessment import AnswerRequest
from app.services.assessment.bank import ASSESSMENT_BANK, question_by_key
from app.services.assessment.engine import next_question
from app.services.assessment.service import (
    answered_map,
    complete_assessment,
    serialize_question,
    start_or_resume,
    submit_answer,
)

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


def _assessment(db: Session, membership: Membership, assessment_id: uuid.UUID) -> Assessment:
    a = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.startup_id == membership.startup_id,
        )
        .first()
    )
    if a is None:
        raise NotFound()
    return a


def _actor_name(user: User) -> str:
    return (user.profile.full_name if user.profile else None) or "A teammate"


@router.post("", status_code=201)
def start_assessment(
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    a = start_or_resume(db, startup, user)
    nq = next_question(ASSESSMENT_BANK, answered_map(db, a), startup)
    db.commit()
    return success_response(
        {
            "assessment_id": str(a.id),
            "type": a.type.value,
            "status": a.status.value,
            "next_question": serialize_question(nq),
        }
    )


@router.get("/{assessment_id}/next-question")
def get_next_question(
    assessment_id: uuid.UUID,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    a = _assessment(db, membership, assessment_id)
    startup = _startup(db, membership)
    nq = next_question(ASSESSMENT_BANK, answered_map(db, a), startup)
    return success_response({"next_question": serialize_question(nq)})


@router.post("/{assessment_id}/answers")
def post_answer(
    assessment_id: uuid.UUID,
    payload: AnswerRequest,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    a = _assessment(db, membership, assessment_id)
    startup = _startup(db, membership)
    nq = submit_answer(db, a, startup, payload.question_key, payload.value)
    db.commit()
    return success_response({"next_question": serialize_question(nq)})


@router.post("/{assessment_id}/complete")
def post_complete_assessment(
    assessment_id: uuid.UUID,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    a = _assessment(db, membership, assessment_id)
    startup = _startup(db, membership)
    was_completed = a.status == AssessmentStatus.completed
    result = complete_assessment(db, a, startup)
    if not was_completed:
        write_activity(
            db,
            startup_id=startup.id,
            actor_user_id=user.id,
            action="assessment.completed",
            entity_type="assessment",
            entity_id=a.id,
            summary=f"{_actor_name(user)} completed the startup assessment",
        )
    db.commit()
    return success_response(result)


@router.get("")
def list_assessments(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = (
        db.query(Assessment)
        .filter(Assessment.startup_id == membership.startup_id)
        .order_by(Assessment.started_at.desc())
        .all()
    )
    results = {
        r.assessment_id: r
        for r in db.query(AssessmentResult)
        .filter(AssessmentResult.assessment_id.in_([a.id for a in rows] or [uuid.uuid4()]))
        .all()
    }
    return success_response(
        [
            {
                "id": str(a.id),
                "type": a.type.value,
                "status": a.status.value,
                "started_at": a.started_at.isoformat(),
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "overall_provisional": (
                    results[a.id].overall_provisional if a.id in results else None
                ),
            }
            for a in rows
        ]
    )


# Registered before GET /{assessment_id} -- "compare" would otherwise be parsed as an
# assessment_id path param and fail UUID conversion before this route ever runs.
@router.get("/compare")
def compare_assessments(
    ids: str = Query(...),
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    id_list = [x for x in ids.split(",") if x]
    if len(id_list) > 3:
        raise AppError("VALIDATION_ERROR", "Compare at most 3 assessments.", 422)
    out = []
    for raw in id_list:
        try:
            aid = uuid.UUID(raw)
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", "That isn't a valid assessment id.", 422) from exc
        # Joining through Assessment enforces tenancy; joining to AssessmentResult (only
        # written on completion) enforces "must be completed" for free -- no separate
        # status check needed, and no distinction leaked between "wrong workspace",
        # "unknown id", and "not completed yet".
        r = (
            db.query(AssessmentResult)
            .join(Assessment, Assessment.id == AssessmentResult.assessment_id)
            .filter(
                AssessmentResult.assessment_id == aid,
                Assessment.startup_id == membership.startup_id,
            )
            .first()
        )
        if r is None:
            raise NotFound()
        out.append({"id": raw, "dimension_scores": r.dimension_scores})
    return success_response(out)


@router.get("/{assessment_id}")
def get_assessment(
    assessment_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    a = _assessment(db, membership, assessment_id)
    answers = db.query(AssessmentAnswer).filter(AssessmentAnswer.assessment_id == a.id).all()
    answers_by_dimension: dict[str, list[dict[str, Any]]] = {}
    for ans in answers:
        q = question_by_key(ASSESSMENT_BANK, ans.question_key)
        bucket = q.dimension.value if q is not None else "unknown"
        answers_by_dimension.setdefault(bucket, []).append(
            {"question_key": ans.question_key, "value": ans.value_json}
        )
    result = db.query(AssessmentResult).filter_by(assessment_id=a.id).first()
    return success_response(
        {
            "id": str(a.id),
            "type": a.type.value,
            "status": a.status.value,
            "answers_by_dimension": answers_by_dimension,
            "result": (
                None
                if result is None
                else {
                    "dimension_scores": result.dimension_scores,
                    "overall_provisional": result.overall_provisional,
                    "narrative": result.narrative,
                }
            ),
        }
    )
