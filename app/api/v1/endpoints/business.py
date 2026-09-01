from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import CanvasType, MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.schemas.business import CanvasSave
from app.services.business.service import (
    get_or_create_canvas,
    overview,
    save_canvas,
    serialize_canvas,
)

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


def _parse_type(type: str) -> CanvasType:
    try:
        return CanvasType(type)
    except ValueError:
        raise NotFound() from None


@router.get("/overview")
def get_overview(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(overview(db, _startup(db, membership)))


@router.get("/canvases/{type}")
def get_canvas(
    type: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    canvas = get_or_create_canvas(db, _startup(db, membership), canvas_type)
    db.commit()  # lazy-create persists
    return success_response(serialize_canvas(canvas))


@router.put("/canvases/{type}")
def put_canvas(
    type: str,
    body: CanvasSave,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    canvas_type = _parse_type(type)
    canvas = get_or_create_canvas(db, _startup(db, membership), canvas_type)
    saved = save_canvas(db, canvas, body.blocks, body.version)
    db.commit()
    return success_response(serialize_canvas(saved))
