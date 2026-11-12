"""Who is who across the two sides of the Marketplace."""

import uuid

from sqlalchemy.orm import Session

from app.db.models.enums import MembershipStatus
from app.db.models.marketplace import MarketplaceProvider
from app.db.models.membership import Membership
from app.db.models.user import User


def is_own_provider(db: Session, provider: MarketplaceProvider, startup_id: uuid.UUID) -> bool:
    """D-MP-L16: an expert can't deal with their own workspace: same user, or the same email as
    any active member."""
    rows = (
        db.query(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .filter(Membership.startup_id == startup_id, Membership.status == MembershipStatus.active)
        .all()
    )
    emails_ = {e.lower() for _, e in rows}
    return (provider.user_id in {uid for uid, _ in rows}) or provider.email.lower() in emails_
