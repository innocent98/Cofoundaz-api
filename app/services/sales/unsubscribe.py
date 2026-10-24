import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import SuppressionReason
from app.db.models.sales_sequences import SalesSuppression, SequenceSend
from app.services.sales.sequences import startup_name
from app.services.sales.suppression import suppress
from app.services.sales.tokens import verify_token


def _send_for(db: Session, token: str) -> SequenceSend:
    """404 for anything the MAC does not vouch for, and for a send that no longer exists. The two
    cases look identical from outside, so a probe learns nothing about which ids are real."""
    send_id: uuid.UUID | None = verify_token(token)
    send = db.get(SequenceSend, send_id) if send_id is not None else None
    if send is None:
        raise NotFound()
    return send


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def unsubscribe_status(db: Session, token: str) -> dict[str, object]:
    send = _send_for(db, token)
    already = (
        db.scalar(
            select(SalesSuppression.id).where(
                SalesSuppression.startup_id == send.startup_id,
                SalesSuppression.email == send.recipient_email,
            )
        )
        is not None
    )
    return {
        "email": mask_email(send.recipient_email),
        "sender": startup_name(db, send.startup_id),
        "unsubscribed": already,
    }


def unsubscribe(db: Session, token: str) -> dict[str, object]:
    """One-click, idempotent. Suppresses the address for this workspace only and stops every
    live enrollment for it there."""
    send = _send_for(db, token)
    suppress(db, send.startup_id, send.recipient_email, SuppressionReason.unsubscribed)
    return {
        "email": mask_email(send.recipient_email),
        "sender": startup_name(db, send.startup_id),
        "unsubscribed": True,
    }
