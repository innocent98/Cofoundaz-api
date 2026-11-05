"""Availability storage and the glue between it and the pure slot generator (`slots.py`)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.enums import AvailabilityKind, ListingType
from app.db.models.marketplace import (
    MarketplaceAvailabilityException,
    MarketplaceAvailabilityRule,
    MarketplaceListing,
    MarketplaceProvider,
)
from app.platform.audit import write_audit
from app.services.marketplace.constants import MAX_SLOT_RANGE_DAYS
from app.services.marketplace.slots import ExceptionIn, RuleIn, Slot, generate_slots

HORIZON_DAYS = 60


def _err(code: str, message: str, status: int = 422) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def _rules(db: Session, provider_id: uuid.UUID) -> list[MarketplaceAvailabilityRule]:
    return (
        db.query(MarketplaceAvailabilityRule)
        .filter(MarketplaceAvailabilityRule.provider_id == provider_id)
        .order_by(
            MarketplaceAvailabilityRule.weekday,
            MarketplaceAvailabilityRule.start_time,
        )
        .all()
    )


def _exceptions(db: Session, provider_id: uuid.UUID) -> list[MarketplaceAvailabilityException]:
    return (
        db.query(MarketplaceAvailabilityException)
        .filter(MarketplaceAvailabilityException.provider_id == provider_id)
        .order_by(
            MarketplaceAvailabilityException.on_date, MarketplaceAvailabilityException.start_time
        )
        .all()
    )


def get_availability(db: Session, provider: MarketplaceProvider) -> dict[str, Any]:
    return {
        "timezone": provider.timezone,
        "min_notice_h": provider.min_notice_h,
        "buffer_min": provider.buffer_min,
        "rules": [
            {
                "weekday": r.weekday,
                "start": r.start_time.strftime("%H:%M"),
                "end": r.end_time.strftime("%H:%M"),
                "slot_min": r.slot_min,
            }
            for r in _rules(db, provider.id)
        ],
        "exceptions": [
            {
                "on_date": e.on_date.isoformat(),
                "kind": e.kind.value,
                "start": e.start_time.strftime("%H:%M") if e.start_time else None,
                "end": e.end_time.strftime("%H:%M") if e.end_time else None,
            }
            for e in _exceptions(db, provider.id)
        ],
    }


def _validate(payload: dict[str, Any]) -> None:
    for r in payload["rules"]:
        if r["end"] <= r["start"]:
            raise _err("VALIDATION_ERROR", "Each weekly window must end after it starts.")
    today = date.today()
    for e in payload["exceptions"]:
        if e["on_date"] < today - timedelta(days=1):
            raise _err("VALIDATION_ERROR", "Exceptions can't be in the past.")
        has_start, has_end = e["start"] is not None, e["end"] is not None
        if has_start != has_end:
            raise _err("VALIDATION_ERROR", "Give both a start and an end time, or neither.")
        if e["kind"] == "extra" and not has_start:
            raise _err("VALIDATION_ERROR", "Extra hours need a start and an end time.")
        if has_start and e["end"] <= e["start"]:
            raise _err("VALIDATION_ERROR", "An exception must end after it starts.")


def put_availability(
    db: Session, provider: MarketplaceProvider, actor: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Replace the whole schedule. The settings and the rows change together or not at all."""
    _validate(payload)
    db.query(MarketplaceAvailabilityRule).filter(
        MarketplaceAvailabilityRule.provider_id == provider.id
    ).delete(synchronize_session=False)
    db.query(MarketplaceAvailabilityException).filter(
        MarketplaceAvailabilityException.provider_id == provider.id
    ).delete(synchronize_session=False)
    provider.min_notice_h = payload["min_notice_h"]
    provider.buffer_min = payload["buffer_min"]
    for r in payload["rules"]:
        db.add(
            MarketplaceAvailabilityRule(
                provider_id=provider.id,
                weekday=r["weekday"],
                start_time=r["start"],
                end_time=r["end"],
                slot_min=r["slot_min"],
            )
        )
    for e in payload["exceptions"]:
        db.add(
            MarketplaceAvailabilityException(
                provider_id=provider.id,
                on_date=e["on_date"],
                kind=AvailabilityKind(e["kind"]),
                start_time=e["start"],
                end_time=e["end"],
            )
        )
    db.flush()
    write_audit(
        db,
        "marketplace.availability.updated",
        actor_user_id=actor,
        entity_type="marketplace_provider",
        entity_id=provider.id,
        details={"rules": len(payload["rules"]), "exceptions": len(payload["exceptions"])},
    )
    return get_availability(db, provider)


def busy_intervals(
    db: Session, provider_id: uuid.UUID, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """UTC intervals already taken. Bookings arrive in slice C; until then nothing is taken."""
    return []


def slots_for(
    db: Session,
    listing: MarketplaceListing,
    provider: MarketplaceProvider,
    from_date: date,
    to_date: date,
    *,
    now: datetime | None = None,
) -> list[Slot]:
    if listing.type != ListingType.consultation or not listing.duration_min:
        return []
    tz = ZoneInfo(provider.timezone)
    start = datetime.combine(from_date, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(
        UTC
    )
    return generate_slots(
        tz=tz,
        rules=[
            RuleIn(r.weekday, r.start_time, r.end_time, r.slot_min) for r in _rules(db, provider.id)
        ],
        exceptions=[
            ExceptionIn(e.on_date, e.kind.value, e.start_time, e.end_time)
            for e in _exceptions(db, provider.id)
        ],
        duration_min=listing.duration_min,
        from_date=from_date,
        to_date=to_date,
        now=now or datetime.now(UTC),
        min_notice_h=provider.min_notice_h,
        buffer_min=provider.buffer_min,
        busy=busy_intervals(db, provider.id, start, end),
    )


def parse_range(
    from_: date | None, to: date | None, tz: ZoneInfo, now: datetime
) -> tuple[date, date]:
    today = now.astimezone(tz).date()
    start = from_ or today
    end = to or start + timedelta(days=6)
    if end < start:
        raise _err("VALIDATION_ERROR", "`to` must not be before `from`.")
    if (end - start).days + 1 > MAX_SLOT_RANGE_DAYS:
        raise _err("RANGE_TOO_LONG", f"Ask for at most {MAX_SLOT_RANGE_DAYS} days at a time.")
    if start < today - timedelta(days=1) or end > today + timedelta(days=HORIZON_DAYS):
        raise _err("RANGE_OUT_OF_BOUNDS", f"Slots are offered up to {HORIZON_DAYS} days ahead.")
    return start, end
