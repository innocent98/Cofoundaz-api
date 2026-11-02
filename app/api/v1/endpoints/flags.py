from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.db.models.membership import Membership
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.services import flags

router = APIRouter()


@router.get("")
def my_flags(
    membership: Annotated[Membership, Depends(require_workspace)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Every flag's on/off for the current workspace, keyed by flag key."""
    return success_response({"flags": flags.flags_for_workspace(db, membership.startup_id)})
