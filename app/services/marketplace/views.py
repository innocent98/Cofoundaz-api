"""The booking detail each side sees: the booking itself plus its deliveries and shared documents.
Kept out of `bookings` so that module does not depend on deliveries (which depends on it)."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.marketplace import MarketplaceBooking
from app.services.marketplace import booking_documents, bookings, deliveries


def founder_detail(db: Session, b: MarketplaceBooking) -> dict[str, Any]:
    return {
        **bookings.founder_view(db, b),
        "deliveries": deliveries.list_view(db, b.id, files_base=deliveries.FOUNDER_FILES),
        "documents": booking_documents.list_for_booking(db, b, live_only=False),
    }


def provider_detail(db: Session, b: MarketplaceBooking) -> dict[str, Any]:
    docs = booking_documents.list_for_booking(db, b, live_only=True)
    for d in docs:
        d["url"] = f"/api/v1/provider/bookings/{b.id}/documents/{d['document_id']}"
    return {
        **bookings.provider_view(db, b),
        "deliveries": deliveries.list_view(db, b.id, files_base=deliveries.PROVIDER_FILES),
        "documents": docs,
    }
