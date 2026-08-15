import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Header
from sqlalchemy.orm import Query, Session

from app.api.deps import get_current_user
from app.core.errors import Forbidden
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.user import User
from app.db.session import get_db


def resolve_workspace(db: Session, user: User, workspace_id: uuid.UUID) -> Membership:
    m = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.startup_id == workspace_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )
    if m is None:
        raise Forbidden()
    return m


def require_workspace(
    x_workspace_id: uuid.UUID = Header(..., alias="X-Workspace-Id"),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    return resolve_workspace(db, user, x_workspace_id)


def require_role(*roles: MembershipRole) -> Callable[..., Membership]:
    def _dep(membership: Membership = Depends(require_workspace)) -> Membership:  # noqa: B008
        if roles and membership.role not in roles:
            raise Forbidden()
        return membership

    return _dep


def tenant_scope(query: Query, startup_id: uuid.UUID, model: type[Any]) -> Query:
    return query.filter(model.startup_id == startup_id)
