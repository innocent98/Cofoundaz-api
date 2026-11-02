"""D-DOC-1 per-person grants: the ONLY widening of the founder + team member rule on Documents.

A grant lets one person read one document (and comment on it, for the `comment` permission)
through the dedicated `/documents/shared-with-me` routes. It never opens a `_editor` route: a
mentor holding a grant still gets 403 on `GET /documents`.

Who a grant is for:
- an account (`grantee_user_id`): picked from the workspace's members, or
- an email (`grantee_email`, lower-cased) with no account yet. It binds to the account whose
  VERIFIED email matches, when that person verifies their address or next opens a Shared-with-me
  route. An unverified account never binds, so registering with someone else's address gets
  nothing.

Access always requires: an unrevoked grant bound to the caller's user id AND a document that is
not in the trash.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, Forbidden, GrantExists, NotFound
from app.db.models.document import Document, DocumentGrant
from app.db.models.enums import DOCUMENT_ROLES, ShareAccess
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.documents.common import display_name


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned or len(cleaned) > 255 or " " in cleaned:
        raise AppError(
            "VALIDATION_ERROR",
            "Enter a valid email address.",
            422,
            field_errors=[{"field": "email", "message": "Enter a valid email address."}],
        )
    return cleaned


def _user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(func.lower(User.email) == email).first()


def _member(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
    return (
        db.query(Membership)
        .filter(
            Membership.startup_id == startup_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )


_name = display_name


def create_grant(
    db: Session,
    doc: Document,
    *,
    granted_by_id: uuid.UUID,
    permission: ShareAccess,
    email: str | None,
    user_id: uuid.UUID | None,
) -> DocumentGrant:
    """Grant by workspace member (`user_id`) or by `email`. Exactly one must be given."""
    if (email is None) == (user_id is None):
        raise AppError("VALIDATION_ERROR", "Give either a member or an email address.", 422)
    grantee: User | None
    if user_id is not None:
        member = _member(db, doc.startup_id, user_id)
        grantee = db.get(User, user_id) if member else None
        if grantee is None:
            raise AppError(
                "VALIDATION_ERROR",
                "That person isn't in this workspace. Share by email instead.",
                422,
                field_errors=[{"field": "user_id", "message": "Not a member of this workspace."}],
            )
        address = normalize_email(grantee.email)
    else:
        assert email is not None  # nosec B101 - exactly one of the two is set (checked above)
        address = normalize_email(email)
        grantee = _user_by_email(db, address)
    if grantee is not None:
        member = _member(db, doc.startup_id, grantee.id)
        if member is not None and member.role in DOCUMENT_ROLES:
            raise AppError(
                "VALIDATION_ERROR",
                "That person is a founder or team member and already has full access.",
                422,
            )
    live = (
        db.query(DocumentGrant.id)
        .filter(
            DocumentGrant.document_id == doc.id,
            DocumentGrant.grantee_email == address,
            DocumentGrant.revoked_at.is_(None),
        )
        .first()
    )
    if live is not None:
        raise GrantExists()
    grant = DocumentGrant(
        startup_id=doc.startup_id,
        document_id=doc.id,
        grantee_user_id=grantee.id if grantee and grantee.email_verified_at else None,
        grantee_email=address,
        permission=permission,
        granted_by_id=granted_by_id,
    )
    try:
        with db.begin_nested():
            db.add(grant)
            db.flush()
    except IntegrityError:  # a concurrent grant for the same person won the partial unique index
        raise GrantExists() from None
    event_bus.publish(
        db,
        "document.grant.created",
        {
            "startup_id": str(doc.startup_id),
            "document_id": str(doc.id),
            "grant_id": str(grant.id),
            "title": doc.title,
            "permission": permission.value,
            "grantee_user_id": str(grant.grantee_user_id) if grant.grantee_user_id else None,
            "actor_id": str(granted_by_id),
            "sharer_name": _name(db, granted_by_id),
            "href": "/documents/shared-with-me",
        },
    )
    return grant


def set_permission(db: Session, grant: DocumentGrant, permission: ShareAccess) -> DocumentGrant:
    grant.permission = permission
    db.flush()
    return grant


def get_grant(db: Session, doc: Document, grant_id: Any) -> DocumentGrant:
    row = db.query(DocumentGrant).filter_by(id=grant_id, document_id=doc.id).first()
    if row is None:
        raise NotFound()
    return row


def revoke_grant(db: Session, grant: DocumentGrant, *, by: uuid.UUID) -> DocumentGrant:
    if grant.revoked_at is None:
        grant.revoked_at = dt.datetime.now(dt.UTC)
        grant.revoked_by_id = by
        db.flush()
        event_bus.publish(
            db,
            "document.grant.revoked",
            {
                "startup_id": str(grant.startup_id),
                "document_id": str(grant.document_id),
                "grant_id": str(grant.id),
            },
        )
    return grant


def list_grants(
    db: Session, doc: Document, *, include_revoked: bool = False
) -> list[DocumentGrant]:
    q = db.query(DocumentGrant).filter_by(document_id=doc.id)
    if not include_revoked:
        q = q.filter(DocumentGrant.revoked_at.is_(None))
    return q.order_by(DocumentGrant.created_at.desc()).all()


def list_workspace_grants(db: Session, startup: Startup) -> list[DocumentGrant]:
    return (
        db.query(DocumentGrant)
        .join(Document, Document.id == DocumentGrant.document_id)
        .filter(
            DocumentGrant.startup_id == startup.id,
            DocumentGrant.revoked_at.is_(None),
            Document.trashed_at.is_(None),
        )
        .order_by(DocumentGrant.created_at.desc())
        .all()
    )


def bind_pending_grants(db: Session, user: User) -> int:
    """Attach email-only grants to `user` once their email is verified. Idempotent and cheap; run
    whenever the account verifies and at the top of every Shared-with-me route."""
    if user.email_verified_at is None:
        return 0
    rows = (
        db.query(DocumentGrant)
        .filter(
            DocumentGrant.grantee_user_id.is_(None),
            DocumentGrant.revoked_at.is_(None),
            DocumentGrant.grantee_email == normalize_email(user.email),
        )
        .all()
    )
    for row in rows:
        row.grantee_user_id = user.id
    if rows:
        db.flush()
    return len(rows)


def on_user_verified(db: Session, payload: dict) -> None:
    user = db.get(User, uuid.UUID(str(payload["user_id"])))
    if user is not None:
        bind_pending_grants(db, user)


_bound_buses: set[int] = set()


def register(bus: Any = event_bus) -> None:
    """Bind a person's email grants the moment their address is verified (sign-up). Idempotent
    for the default bus; the Shared-with-me routes bind lazily too, which covers a later login."""
    if bus is event_bus and id(bus) in _bound_buses:
        return
    bus.subscribe("auth.user.verified", on_user_verified)
    if bus is event_bus:
        _bound_buses.add(id(bus))


def shared_with_me(db: Session, user: User) -> list[tuple[DocumentGrant, Document]]:
    """The caller's live grants on documents that are not in the trash, newest first."""
    bind_pending_grants(db, user)
    rows = (
        db.query(DocumentGrant, Document)
        .join(Document, Document.id == DocumentGrant.document_id)
        .filter(
            DocumentGrant.grantee_user_id == user.id,
            DocumentGrant.revoked_at.is_(None),
            Document.trashed_at.is_(None),
        )
        .order_by(DocumentGrant.created_at.desc())
        .all()
    )
    return [(g, d) for g, d in rows]


def granted_document(
    db: Session, user: User, document_id: Any, *, need: ShareAccess = ShareAccess.view
) -> tuple[DocumentGrant, Document]:
    """The grant + document for `user`, or 404: no grant, revoked, someone else's grant, or the
    document is trashed all look identical. `need=comment` additionally requires the comment
    permission (403 for a view-only grantee: they already know the document exists)."""
    bind_pending_grants(db, user)
    row = (
        db.query(DocumentGrant, Document)
        .join(Document, Document.id == DocumentGrant.document_id)
        .filter(
            DocumentGrant.document_id == document_id,
            DocumentGrant.grantee_user_id == user.id,
            DocumentGrant.revoked_at.is_(None),
            Document.trashed_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise NotFound()
    grant, doc = row
    if need == ShareAccess.comment and grant.permission != ShareAccess.comment:
        raise Forbidden()
    return grant, doc


def serialize_grant(db: Session, grant: DocumentGrant) -> dict[str, Any]:
    """Only a grantee who is a member of the workspace is identified (user id, name, "active").
    Anyone else, including an email that already matches a platform account, stays "pending"
    with just the email, so granting by email cannot be used to probe which addresses have
    accounts or what their names are."""
    member = grant.grantee_user_id is not None and (
        _member(db, grant.startup_id, grant.grantee_user_id) is not None
    )
    bound = grant.grantee_user_id if member else None
    return {
        "id": str(grant.id),
        "document_id": str(grant.document_id),
        "grantee": {
            "user_id": str(bound) if bound else None,
            "email": grant.grantee_email,
            "name": _name(db, bound),
        },
        "permission": grant.permission.value,
        "status": ("revoked" if grant.revoked_at else ("active" if bound else "pending")),
        "granted_by": str(grant.granted_by_id) if grant.granted_by_id else None,
        "created_at": grant.created_at.isoformat(),
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
    }


def serialize_shared_item(
    db: Session, grant: DocumentGrant, doc: Document, *, with_content: bool = False
) -> dict[str, Any]:
    """What a grantee sees. Deliberately not the owner's summary: no folder, tags, share counts
    or version history, and no workspace internals beyond a name to show 'from whom'."""
    startup = db.get(Startup, doc.startup_id)
    out: dict[str, Any] = {
        "id": str(doc.id),
        "title": doc.title,
        "kind": doc.kind.value,
        "status": doc.status.value,
        "version": doc.version,
        "updated_at": doc.updated_at.isoformat(),
        "permission": grant.permission.value,
        "workspace": {"id": str(doc.startup_id), "name": startup.name if startup else None},
        "shared_by": {
            "id": str(grant.granted_by_id) if grant.granted_by_id else None,
            "name": _name(db, grant.granted_by_id),
        },
        "shared_at": grant.created_at.isoformat(),
    }
    if with_content:
        out["sections"] = doc.sections
    return out
