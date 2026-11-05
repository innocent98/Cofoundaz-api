"""The founder side of reporting: report one AI Co-Founder reply or one comment, list my live
reports. A founder reaches only their own workspace's items (an AI reply only inside their own
conversation); anything else is a 404, so an id from elsewhere tells them nothing.
"""

import uuid
from typing import Any

from sqlalchemy import exc
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.ai_chat import AiConversation, AiMessage
from app.db.models.comment import Comment
from app.db.models.content_report import ContentReport
from app.db.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    ReportItemType,
    ReportReason,
    ReportStatus,
)
from app.db.models.marketplace import MarketplaceProvider
from app.db.models.membership import Membership
from app.db.models.user import User
from app.platform.audit import write_audit
from app.services.marketplace import directory
from app.services.reports import core

LIVE = (ReportStatus.open, ReportStatus.escalated)


def _public(r: ContentReport) -> dict[str, Any]:
    # A founder sees only "received": what staff decided stays internal.
    return {
        "id": str(r.id),
        "item_type": r.item_type.value,
        "item_id": str(r.item_id),
        "reason": r.reason.value,
        "status": "received",
        "created_at": core.iso(r.created_at),
    }


def _target(
    db: Session, user: User, m: Membership, item_type: ReportItemType, item_id: uuid.UUID
) -> tuple[str, uuid.UUID | None]:
    """The text to copy and who wrote it, for an item this person may report."""
    if item_type in (ReportItemType.listing, ReportItemType.provider):
        return _marketplace_target(db, user, item_type, item_id)
    if item_type == ReportItemType.ai_message:
        msg = (
            db.query(AiMessage)
            .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
            .filter(
                AiMessage.id == item_id,
                AiConversation.startup_id == m.startup_id,
                AiConversation.created_by == user.id,
                AiMessage.role == ChatMessageRole.assistant,
                AiMessage.status == ChatMessageStatus.ready,
                AiMessage.removed_by_moderation_at.is_(None),
            )
            .with_for_update(of=AiMessage)  # a removal in flight finishes first
            .first()
        )
        if msg is None:
            raise NotFound()
        return msg.content, None
    comment = (
        db.query(Comment)
        .filter(
            Comment.id == item_id,
            Comment.startup_id == m.startup_id,
            # Document comments belong to Documents (private by D-ADM-9's spirit).
            Comment.entity_type != "document",
            Comment.removed_by_moderation_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if comment is None:
        raise NotFound()
    if comment.author_id == user.id:
        raise core.err("OWN_COMMENT", "You can't report your own comment.", 422)
    return comment.body, comment.author_id


def _marketplace_target(
    db: Session, user: User, item_type: ReportItemType, item_id: uuid.UUID
) -> tuple[str, uuid.UUID | None]:
    """A listing or expert the reporter can actually see in the directory (anything else is a 404)."""
    if item_type == ReportItemType.listing:
        listing, owner = directory.get_listing(db, item_id)
        text = f"{listing.title}\n{listing.summary}"
    else:
        profile = directory.get_provider(
            db, item_id
        )  # raises NotFound unless live in the directory
        found = db.get(MarketplaceProvider, item_id)
        if found is None:  # unreachable: get_provider just found it
            raise NotFound()
        owner = found
        text = f"{profile['display_name']}\n{profile['headline'] or ''}\n{profile['bio'] or ''}"
    if owner.user_id == user.id:
        raise core.err("OWN_ITEM", "You can't report your own listing or profile.", 422)
    return text, owner.user_id


def create_report(
    db: Session,
    user: User,
    m: Membership,
    *,
    item_type: ReportItemType,
    item_id: uuid.UUID,
    reason: ReportReason,
    note: str | None,
) -> dict[str, Any]:
    text, author_id = _target(db, user, m, item_type, item_id)
    # Spent only once the item is valid: a 404 or your own comment doesn't use a slot.
    core.claim(
        f"report_rl:{user.id}",
        core.REPORTS_PER_HOUR,
        3600,
        "You've sent several reports recently. Please wait a little before sending another.",
    )
    report = ContentReport(
        startup_id=m.startup_id,
        reporter_id=user.id,
        item_type=item_type,
        item_id=item_id,
        item_author_id=author_id,
        reason=reason,
        note=note,
        snippet=text[: core.SNIPPET_MAX],
        status=ReportStatus.open,
    )
    try:
        with db.begin_nested():  # the partial unique index refuses a second live report
            db.add(report)
            db.flush()
    except exc.IntegrityError as e:
        raise core.err(
            "ALREADY_REPORTED", "You've already reported this. We'll take a look.", 409
        ) from e
    write_audit(
        db,
        "report.filed",
        actor_user_id=user.id,
        startup_id=m.startup_id,
        entity_type="content_report",
        entity_id=report.id,
        details={"item_type": item_type.value, "item_id": str(item_id), "reason": reason.value},
    )
    out = _public(report)
    db.commit()
    return out


def my_reports(db: Session, user: User, m: Membership) -> list[dict[str, Any]]:
    """Reports of mine that are still being looked at, so the UI can show "Reported"."""
    rows = (
        db.query(ContentReport)
        .filter(
            ContentReport.startup_id == m.startup_id,
            ContentReport.reporter_id == user.id,
            ContentReport.status.in_(LIVE),
        )
        .order_by(ContentReport.created_at.desc(), ContentReport.id.desc())
        .limit(200)
        .all()
    )
    return [_public(r) for r in rows]
