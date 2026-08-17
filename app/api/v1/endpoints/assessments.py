from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.engine import next_question
from app.services.assessment.service import answered_map, serialize_question, start_or_resume

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


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
