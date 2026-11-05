"""Taking a listing down and putting it back (staff moderation and the reports flow share this).

The listing remembers what it was before (`active` or `paused`), so a relist restores exactly that:
an expert's own pause survives a takedown. `unlisted_via_report_id` is set only by the reports
flow, so restoring a report undoes a takedown that report made and never one a moderator made."""

import uuid

from app.db.models.enums import ListingStatus
from app.db.models.marketplace import MarketplaceListing

TAKEABLE = (ListingStatus.active, ListingStatus.paused)


def take_down(
    row: MarketplaceListing,
    *,
    actor_id: uuid.UUID | None,
    reason: str,
    via_report_id: uuid.UUID | None = None,
) -> bool:
    """Unlist a live listing. False (and no change) if it isn't live."""
    if row.status not in TAKEABLE:
        return False
    row.unlisted_prior_status = row.status.value
    row.status = ListingStatus.unlisted
    row.unlisted_by, row.unlisted_reason = actor_id, reason
    row.unlisted_via_report_id = via_report_id
    return True


def put_back(row: MarketplaceListing) -> ListingStatus:
    """Relist: back to the status it had before the takedown (active when unknown)."""
    prior = ListingStatus(row.unlisted_prior_status or ListingStatus.active.value)
    row.status = prior if prior in TAKEABLE else ListingStatus.active
    row.unlisted_by = row.unlisted_reason = row.unlisted_prior_status = None
    row.unlisted_via_report_id = None
    return row.status
