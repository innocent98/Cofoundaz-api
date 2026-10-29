import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.core.rate_limit import limiter
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.email import get_email_sender
from app.schemas.team import (
    CommentCreate,
    CommentEdit,
    CommentEntity,
    CommentResolve,
    MemberRoleChange,
    TeamInvitesRequest,
)
from app.services.onboarding.invites import create_invitations, deliver_invitations
from app.services.team import members as members_svc
from app.services.team import service as team_svc

router = APIRouter()


@router.post("/invitations")
@limiter.limit("10/minute")
def invite_members(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    payload: TeamInvitesRequest,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Invite people to the active workspace at any time (PRD 23 `POST /team/invitations`;
    the top bar's "+ Invite", founder only). Same rules as onboarding invites: an address
    that's already a member or already has a pending invite is `skipped`, not an error."""
    startup = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if startup is None:
        raise NotFound()
    items = [{"email": str(i.email), "role": i.role.value} for i in payload.invites]
    result = create_invitations(db, startup, user, items)
    db.commit()  # commit BEFORE sending: never email for an invitation that rolled back
    return success_response(result.response(deliver_invitations(result)))


def _ws(db: Session, membership: Membership) -> Startup:
    startup = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if startup is None:
        raise NotFound()
    return startup


@router.get("/members")
def get_members(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Active workspace members (assignee/owner pickers, directory)."""
    return success_response(
        members_svc.list_members_detailed(
            db, _ws(db, membership), include_last_active=membership.role == MembershipRole.founder
        )
    )


@router.get("/comments")
def get_comments(
    entity_type: CommentEntity = Query(...),  # noqa: B008
    entity_id: uuid.UUID = Query(...),  # noqa: B008
    anchor: str | None = Query(None, max_length=60),  # noqa: B008
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 23.2 thread for one entity, oldest first (optionally one section via `anchor`)."""
    return success_response(
        team_svc.list_comments(db, _ws(db, membership), user, entity_type, entity_id, anchor)
    )


@router.post("/comments", status_code=201)
@limiter.limit("30/minute")
def post_comment(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    payload: CommentCreate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = team_svc.create_comment(
        db,
        _ws(db, membership),
        user,
        membership.role,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        body=payload.body,
        mention_user_ids=payload.mention_user_ids,
        anchor=payload.anchor,
    )
    db.commit()
    return success_response(data)


@router.patch("/comments/{comment_id}")
def patch_comment(
    comment_id: uuid.UUID,
    payload: CommentEdit,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = team_svc.edit_comment(db, _ws(db, membership), user, comment_id, payload.body)
    db.commit()
    return success_response(data)


@router.delete("/comments/{comment_id}", status_code=204)
def remove_comment(
    comment_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    team_svc.delete_comment(db, _ws(db, membership), user, comment_id)
    db.commit()


@router.post("/comments/{comment_id}/resolve")
def resolve_comment(
    comment_id: uuid.UUID,
    payload: CommentResolve,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 23.2 "Resolve thread" toggle (founder/TM, or the comment's author)."""
    data = team_svc.set_resolved(
        db, _ws(db, membership), user, membership.role, comment_id, payload.resolved
    )
    db.commit()
    return success_response(data)


# ----------------------------------------------------------------------
# Members: change role, remove, leave (founder-only except leave)
# ----------------------------------------------------------------------

_FOUNDER = require_role(MembershipRole.founder)


@router.patch("/members/{user_id}")
def change_member_role(
    user_id: uuid.UUID,
    payload: MemberRoleChange,
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    m = members_svc.change_role(
        db,
        startup=_ws(db, membership),
        actor=user,
        target_user_id=user_id,
        role=MembershipRole(payload.role.value),
    )
    db.commit()
    return success_response({"user_id": str(m.user_id), "role": m.role.value})


@router.delete("/members/{user_id}")
def remove_member(
    user_id: uuid.UUID,
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = members_svc.remove_member(
        db, startup=_ws(db, membership), actor=user, target_user_id=user_id
    )
    db.commit()
    return success_response({"user_id": str(user_id), "removed": True, **out})


@router.post("/leave")
def leave_workspace(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = members_svc.leave(db, startup=_ws(db, membership), user=user)
    db.commit()
    return success_response({"left": True, **out})


# ----------------------------------------------------------------------
# Invitations: list, resend, revoke (founder only)
# ----------------------------------------------------------------------


@router.get("/invitations")
def list_invitations(
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(members_svc.list_invitations(db, _ws(db, membership)))


@router.post("/invitations/{invitation_id}/resend")
@limiter.limit("10/minute")
def resend_invitation(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    invitation_id: uuid.UUID,
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    msg = members_svc.resend_invitation(
        db, startup=_ws(db, membership), actor=user, invitation_id=invitation_id
    )
    db.commit()  # commit BEFORE sending: never email for a change that rolled back
    delivered = True
    try:
        get_email_sender().send(msg)
    except Exception:  # noqa: BLE001 - the new link exists; the founder can resend again
        delivered = False
    return success_response({"id": str(invitation_id), "resent": True, "delivered": delivered})


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(
    invitation_id: uuid.UUID,
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    members_svc.revoke_invitation(
        db, startup=_ws(db, membership), actor=user, invitation_id=invitation_id
    )
    db.commit()
    return success_response({"id": str(invitation_id), "revoked": True})


# ----------------------------------------------------------------------
# Audit tab (founder only) and the comments index
# ----------------------------------------------------------------------


@router.get("/audit")
def get_audit(
    limit: int = Query(30, ge=1, le=100),  # noqa: B008
    cursor: str | None = Query(None),  # noqa: B008
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, next_cursor = members_svc.audit_log(db, _ws(db, membership), limit=limit, cursor=cursor)
    return success_response({"entries": rows, "next_cursor": next_cursor})


@router.get("/audit/export")
def export_audit(
    membership: Membership = Depends(_FOUNDER),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    return Response(
        content=members_svc.audit_csv(db, _ws(db, membership)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="team-audit-log.csv"'},
    )


@router.get("/comments/threads")
def comment_threads(
    filter: str = Query("all", pattern="^(all|mentions|unresolved)$"),  # noqa: A002, B008
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        members_svc.comment_threads(db, _ws(db, membership), user, filter_=filter)
    )
