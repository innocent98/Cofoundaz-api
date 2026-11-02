from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.schemas.reports import ReportCreateRequest
from app.services.reports import founder

router = APIRouter()

_Member = Annotated[Membership, Depends(require_workspace)]
_User = Annotated[User, Depends(get_verified_user)]
_Db = Annotated[Session, Depends(get_db)]


@router.post("", status_code=201)
def report_item(payload: ReportCreateRequest, m: _Member, user: _User, db: _Db) -> dict[str, Any]:
    """Report one AI Co-Founder reply or one comment. Any workspace member; rate-limited."""
    return success_response(
        founder.create_report(
            db,
            user,
            m,
            item_type=payload.item_type,
            item_id=payload.item_id,
            reason=payload.reason,
            note=payload.note,
        )
    )


@router.get("/mine")
def my_reports(m: _Member, user: _User, db: _Db) -> dict[str, Any]:
    """My reports that are still with staff, so the UI can mark those items "Reported"."""
    return success_response({"reports": founder.my_reports(db, user, m)})
