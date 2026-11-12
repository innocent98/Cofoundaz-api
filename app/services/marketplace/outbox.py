"""Emails raised deep inside a money path (a webhook, a timer) are queued on the session and sent
only AFTER the surrounding transaction commits, so a rolled-back payment never emails anyone and a
slow mail server never holds a row lock."""

from sqlalchemy.orm import Session

from app.db.models.marketplace import MarketplaceBooking, MarketplaceProvider
from app.platform.email import EmailMessage
from app.services.marketplace import emails

_KEY = "marketplace_outbox"


def queue(db: Session, msg: EmailMessage | None) -> None:
    if msg is not None:
        db.info.setdefault(_KEY, []).append(msg)


def flush(db: Session) -> list[str]:
    """Send everything queued. Call once, after commit. Returns the addresses that failed."""
    pending: list[EmailMessage] = db.info.pop(_KEY, [])
    return emails.deliver(pending) if pending else []


def discard(db: Session) -> None:
    db.info.pop(_KEY, None)


def new_request_email(db: Session, b: MarketplaceBooking) -> EmailMessage | None:
    from zoneinfo import ZoneInfo

    provider = db.get(MarketplaceProvider, b.provider_id)
    if provider is None or b.accept_by is None:
        return None
    tz = ZoneInfo(provider.timezone)
    when = (
        b.starts_at.astimezone(tz).strftime("%a %d %b %Y, %H:%M ") + provider.timezone
        if b.starts_at
        else None
    )
    deadline = b.accept_by.astimezone(tz).strftime("%a %d %b, %H:%M")
    return emails.booking_request_email(
        provider.email,
        name=provider.display_name,
        title=b.title_snap,
        when=when,
        accept_by=deadline,
    )
