"""What has to happen when a booking stops being live, in one place so every path that ends one
(cancel, decline, expiry, completion, and slice E's disputes) calls the same thing."""

from sqlalchemy.orm import Session

from app.db.models.marketplace import MarketplaceBooking
from app.services.marketplace import booking_documents, calendar_link


def on_closed(db: Session, b: MarketplaceBooking, *, drop_calendar: bool) -> None:
    """Revoke the expert's document links. `drop_calendar` is true for a booking that will not
    happen (cancelled, declined, expired, refunded) and false for one that DID (completed): its
    meeting stays on the founder's Calendar as the record."""
    booking_documents.revoke_all(db, b)
    if drop_calendar:
        calendar_link.cancel(db, b)
