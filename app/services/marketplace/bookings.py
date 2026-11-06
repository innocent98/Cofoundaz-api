"""Founder bookings, checkout and the expert's accept / decline (design §3.4, §5, §6, D-MP-L7).

Creating a booking is the money-sensitive moment, so the order is fixed: idempotency replay,
visibility and ownership checks, consent, THEN row locks (expert, then listing), slot check
against the live schedule and every other booking, insert booking + order, Paystack initialise,
commit. A booking holds its slot for 15 minutes while it waits for payment; payment confirmed by
Paystack (webhook or the founder's verify call) moves it to `awaiting_provider`, where the expert
has 48 hours (`ACCEPT_WINDOW`) before it expires and the card is refunded.

Only a founder books, pays or cancels (D-MP-L1); founder and team member read."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext
from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.billing import BillingPaymentMethod
from app.db.models.enums import (
    BookingStatus,
    ListingStatus,
    ListingType,
    MembershipStatus,
    OrderStatus,
    ProviderStatus,
)
from app.db.models.marketplace import (
    MarketplaceBooking,
    MarketplaceConsent,
    MarketplaceListing,
    MarketplaceOrder,
    MarketplaceProvider,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.email import EmailMessage
from app.platform.events import event_bus
from app.services.billing import paystack
from app.services.marketplace import availability, emails, orders
from app.services.marketplace.constants import (
    CONSENT_KIND_TERMS,
    MARKETPLACE_TERMS_VERSION,
    SLOT_HOLD,
)
from app.services.marketplace.directory import get_listing
from app.services.marketplace.listings import commission_bps_for, err, split_price

TABS: dict[str, tuple[BookingStatus, ...]] = {
    "upcoming": (BookingStatus.awaiting_provider, BookingStatus.confirmed),
    "in_progress": (BookingStatus.in_progress,),
    "delivered": (BookingStatus.delivered, BookingStatus.disputed),
    "closed": (
        BookingStatus.completed,
        BookingStatus.refunded,
        BookingStatus.partially_refunded,
        BookingStatus.cancelled,
        BookingStatus.declined,
        BookingStatus.expired,
    ),
}
REVEAL_MEETING = (BookingStatus.confirmed, BookingStatus.in_progress, BookingStatus.delivered)


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ views


def _order_for(db: Session, booking_id: uuid.UUID) -> MarketplaceOrder:
    row = db.query(MarketplaceOrder).filter(MarketplaceOrder.booking_id == booking_id).one()
    return row


def order_view(order: MarketplaceOrder, *, booking_status: BookingStatus) -> dict[str, Any]:
    """The founder's view of the money: exactly what they were charged and what came back. No
    commission or expert net (the founder pays the listed price, nothing on top)."""
    return {
        "id": str(order.id),
        "status": order.status.value,
        "amount_minor": order.amount_minor,
        "currency": order.currency,
        "reference": order.paystack_reference,
        "checkout_url": (
            order.checkout_url
            if order.status == OrderStatus.pending
            and booking_status == BookingStatus.pending_payment
            else None
        ),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "receipt_number": order.receipt_number,
        "refunded_minor": order.refunded_minor,
        "refund_pending_minor": order.refund_due_minor,
    }


def founder_view(db: Session, b: MarketplaceBooking) -> dict[str, Any]:
    provider = db.get(MarketplaceProvider, b.provider_id)
    order = _order_for(db, b.id)
    return {
        "id": str(b.id),
        "status": b.status.value,
        "type": b.type,
        "listing_id": str(b.listing_id),
        "title": b.title_snap,
        "category": b.category_snap,
        "deliverables": list(b.deliverables_snap),
        "duration_min": b.duration_min_snap,
        "delivery_days": b.delivery_days_snap,
        "revisions_included": b.revisions_included_snap,
        "price_minor": b.price_minor,
        "currency": b.currency,
        "brief": b.brief,
        "starts_at": b.starts_at.isoformat() if b.starts_at else None,
        "ends_at": b.ends_at.isoformat() if b.ends_at else None,
        "timezone": provider.timezone if provider else None,
        "slot_hold_expires_at": (
            b.slot_hold_expires_at.isoformat() if b.slot_hold_expires_at else None
        ),
        "accept_by": b.accept_by.isoformat() if b.accept_by else None,
        "due_at": b.due_at.isoformat() if b.due_at else None,
        "meeting_url": b.meeting_url_snap if b.status in REVEAL_MEETING else None,
        "closed_reason": b.closed_reason,
        "provider": {
            "id": str(provider.id) if provider else None,
            "display_name": provider.display_name if provider else None,
            "avatar_url": (
                f"/api/v1/marketplace/providers/{provider.id}/avatar"
                if provider and provider.avatar_file_id
                else None
            ),
        },
        "order": order_view(order, booking_status=b.status),
        "created_at": b.created_at.isoformat(),
    }


def provider_view(db: Session, b: MarketplaceBooking) -> dict[str, Any]:
    """The expert's view: the brief and the money they would earn, and nothing about the founder's
    workspace (design §2). The client is identified by name only."""
    order = _order_for(db, b.id)
    split = split_price(b.price_minor, b.commission_bps_snap)
    client = db.get(User, b.booked_by)
    return {
        "id": str(b.id),
        "status": b.status.value,
        "type": b.type,
        "title": b.title_snap,
        "category": b.category_snap,
        "deliverables": list(b.deliverables_snap),
        "duration_min": b.duration_min_snap,
        "delivery_days": b.delivery_days_snap,
        "revisions_included": b.revisions_included_snap,
        "brief": b.brief,
        "client_name": (client.profile.full_name if client and client.profile else None),
        "starts_at": b.starts_at.isoformat() if b.starts_at else None,
        "ends_at": b.ends_at.isoformat() if b.ends_at else None,
        "accept_by": b.accept_by.isoformat() if b.accept_by else None,
        "due_at": b.due_at.isoformat() if b.due_at else None,
        "meeting_url": b.meeting_url_snap if b.status in REVEAL_MEETING else None,
        "closed_reason": b.closed_reason,
        "earnings": {
            "price_minor": b.price_minor,
            "commission_bps": split["commission_bps"],
            "commission_minor": split["commission_minor"],
            "net_minor": split["net_minor"],
            "currency": b.currency,
            "paid": order.status
            in (OrderStatus.paid, OrderStatus.held, OrderStatus.partially_refunded),
        },
        "created_at": b.created_at.isoformat(),
    }


# ------------------------------------------------------------------ consent


def terms_accepted(db: Session, user_id: uuid.UUID) -> bool:
    return (
        db.query(MarketplaceConsent.id)
        .filter(
            MarketplaceConsent.user_id == user_id,
            MarketplaceConsent.kind == CONSENT_KIND_TERMS,
            MarketplaceConsent.version == MARKETPLACE_TERMS_VERSION,
        )
        .first()
        is not None
    )


def saved_card(db: Session, startup_id: uuid.UUID) -> BillingPaymentMethod | None:
    return (
        db.query(BillingPaymentMethod)
        .filter(
            BillingPaymentMethod.startup_id == startup_id, BillingPaymentMethod.removed_at.is_(None)
        )
        .order_by(BillingPaymentMethod.is_default.desc(), BillingPaymentMethod.created_at.desc())
        .first()
    )


def checkout_status(db: Session, user: User, m: Membership) -> dict[str, Any]:
    card = saved_card(db, m.startup_id)
    return {
        "payments_enabled": bool(settings.MARKETPLACE_PAYMENTS_ENABLED),
        "terms_version": MARKETPLACE_TERMS_VERSION,
        "terms_accepted": terms_accepted(db, user.id),
        "can_book": m.role.value == "founder",
        "saved_card": {"brand": card.brand, "last4": card.last4} if card else None,
    }


# ------------------------------------------------------------------ creating a booking


def _own_listing(db: Session, provider: MarketplaceProvider, startup_id: uuid.UUID) -> bool:
    """D-MP-L16: an expert can't book themselves: same user, or the same email as any member."""
    rows = (
        db.query(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .filter(Membership.startup_id == startup_id, Membership.status == MembershipStatus.active)
        .all()
    )
    emails_ = {e.lower() for _, e in rows}
    return (provider.user_id in {uid for uid, _ in rows}) or provider.email.lower() in emails_


def _same_request(
    b: MarketplaceBooking, listing_id: uuid.UUID, starts_at: datetime | None, brief: str
) -> bool:
    return b.listing_id == listing_id and b.starts_at == starts_at and b.brief == brief


def find_replay(
    db: Session,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    key: str,
    listing_id: uuid.UUID,
    starts_at: datetime | None,
    brief: str,
) -> MarketplaceBooking | None:
    row = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.booked_by == user_id, MarketplaceBooking.idempotency_key == key)
        .first()
    )
    if row is None:
        return None
    if row.startup_id != startup_id or not _same_request(row, listing_id, starts_at, brief):
        raise err(
            "IDEMPOTENCY_CONFLICT",
            "That Idempotency-Key was already used for a different booking.",
            409,
        )
    return row


def _ensure_terms(db: Session, user: User, accept_terms: bool) -> None:
    """First booking (or a new terms version): the founder must tick the box, and we record it."""
    if terms_accepted(db, user.id):
        return
    if not accept_terms:
        raise AppError(
            code="TERMS_REQUIRED",
            message="Accept the Marketplace terms to book.",
            http_status=422,
            field_errors=[{"field": "accept_terms", "message": "You need to accept the terms."}],
        )
    db.add(
        MarketplaceConsent(
            user_id=user.id, kind=CONSENT_KIND_TERMS, version=MARKETPLACE_TERMS_VERSION
        )
    )


def _resolve_slot(
    db: Session,
    listing: MarketplaceListing,
    provider: MarketplaceProvider,
    starts_at: datetime | None,
    t: datetime,
) -> tuple[datetime, datetime]:
    """The requested start must be one of the slots the generator offers RIGHT NOW (schedule,
    exceptions, notice, buffer and every other live booking), under the expert's row lock."""
    if starts_at is None or listing.duration_min is None:
        raise err("SLOT_REQUIRED", "Choose a time for this consultation.", 422)
    day = starts_at.astimezone(ZoneInfo(provider.timezone)).date()
    open_slots = availability.slots_for(db, listing, provider, day, day, now=t)
    wanted = starts_at.astimezone(UTC)
    match = next((s for s in open_slots if s.starts_at == wanted), None)
    if match is None:
        raise err("SLOT_UNAVAILABLE", "That time isn't available any more. Pick another.", 409)
    return match.starts_at, match.ends_at


def create_booking(
    db: Session,
    user: User,
    m: Membership,
    *,
    listing_id: uuid.UUID,
    starts_at: datetime | None,
    brief: str,
    idempotency_key: str,
    accept_terms: bool,
    pay_with: str,
) -> tuple[MarketplaceBooking, MarketplaceOrder, bool]:
    """Returns (booking, order, replayed). Raises before touching Paystack on every bad input."""
    if not settings.MARKETPLACE_PAYMENTS_ENABLED:
        raise err("PAYMENTS_DISABLED", "Booking isn't open yet. Payments are switched off.", 403)
    replay = find_replay(db, user.id, m.startup_id, idempotency_key, listing_id, starts_at, brief)
    if replay is not None:
        return replay, _order_for(db, replay.id), True
    listing, provider = get_listing(db, listing_id)  # 404 unless approved expert + active listing
    if _own_listing(db, provider, m.startup_id):
        raise err("CANNOT_BOOK_OWN", "You can't book your own service.", 422)
    consult = listing.type == ListingType.consultation
    if consult and starts_at is None:
        raise err("SLOT_REQUIRED", "Choose a time for this consultation.", 422)
    if not consult and starts_at is not None:
        raise err("SLOT_NOT_APPLICABLE", "Packages don't have a time slot.", 422)
    if pay_with == "saved_card" and saved_card(db, m.startup_id) is None:
        raise err("NO_SAVED_CARD", "There's no saved card on this workspace.", 422)
    _ensure_terms(db, user, accept_terms)

    # Serialise bookings per expert: lock the expert row, then the listing, and re-read both.
    locked_provider = (
        db.query(MarketplaceProvider)
        .filter(MarketplaceProvider.id == provider.id)
        .with_for_update()
        .one()
    )
    locked_listing = (
        db.query(MarketplaceListing)
        .filter(MarketplaceListing.id == listing.id)
        .with_for_update()
        .one()
    )
    if (
        locked_provider.status != ProviderStatus.approved
        or locked_listing.status != ListingStatus.active
    ):
        raise NotFound()
    t = now()
    ends_at: datetime | None = None
    if consult:
        starts_at, ends_at = _resolve_slot(db, locked_listing, locked_provider, starts_at, t)
    bps = commission_bps_for(locked_provider)
    split = split_price(locked_listing.price_minor, bps)
    booking = MarketplaceBooking(
        startup_id=m.startup_id,
        listing_id=locked_listing.id,
        provider_id=locked_provider.id,
        booked_by=user.id,
        status=BookingStatus.pending_payment,
        type=locked_listing.type.value,
        title_snap=locked_listing.title,
        category_snap=locked_listing.category,
        price_minor=locked_listing.price_minor,
        currency="NGN",
        commission_bps_snap=bps,
        deliverables_snap=list(locked_listing.deliverables_json),
        duration_min_snap=locked_listing.duration_min,
        delivery_days_snap=locked_listing.delivery_days,
        revisions_included_snap=locked_listing.revisions_included,
        meeting_url_snap=locked_listing.meeting_url,
        brief=brief,
        starts_at=starts_at,
        ends_at=ends_at,
        slot_hold_expires_at=t + SLOT_HOLD,
        idempotency_key=idempotency_key,
    )
    try:
        with db.begin_nested():
            db.add(booking)
            db.flush()
    except IntegrityError:
        # a concurrent request with the same key won the insert: answer with its booking
        replay = find_replay(
            db, user.id, m.startup_id, idempotency_key, listing_id, starts_at, brief
        )
        if replay is None:
            raise err(
                "SLOT_UNAVAILABLE", "That time isn't available any more. Pick another.", 409
            ) from None
        return replay, _order_for(db, replay.id), True
    order = MarketplaceOrder(
        booking_id=booking.id,
        startup_id=m.startup_id,
        amount_minor=booking.price_minor,
        commission_minor=split["commission_minor"],
        provider_net_minor=split["net_minor"],
        currency="NGN",
        status=OrderStatus.pending,
        settlement_mode="platform",
        paystack_reference=orders.reference_for(booking.id),
        idempotency_key=idempotency_key,
    )
    db.add(order)
    db.flush()
    if pay_with == "card":
        base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
        init = paystack.initialize(
            email=user.email,
            amount_minor=order.amount_minor,
            reference=order.paystack_reference,
            callback_url=f"{base}/marketplace/bookings/{booking.id}",
            metadata={
                "booking_id": str(booking.id),
                "order_id": str(order.id),
                "kind": "marketplace",
            },
        )
        url = init.get("authorization_url")
        order.checkout_url = url if isinstance(url, str) else None
    return booking, order, False


def charge_saved_card(
    db: Session, user: User, m: Membership, booking: MarketplaceBooking, order: MarketplaceOrder
) -> str:
    """One-click rebooking on the workspace's saved card. Runs AFTER the pending booking is
    committed (so a crash can't charge a card with nothing on file). Returns the outcome of
    applying the confirmed payment; a declined or unverifiable charge cancels the booking."""
    card = saved_card(db, m.startup_id)
    if card is None:
        return _fail_checkout(db, booking, order, "no_saved_card")
    try:
        data = paystack.charge_authorization(
            authorization_code=card.authorization_code,
            email=user.email,
            amount_minor=order.amount_minor,
            reference=order.paystack_reference,
            metadata={"booking_id": str(booking.id), "kind": "marketplace"},
        )
    except AppError:
        # We don't know whether the card was charged (a timeout can follow a real charge). Leave
        # the hold pending: the webhook confirms it, or the timer expires it.
        return "card_error"
    if data.get("status") != "success":
        return _fail_checkout(db, booking, order, "card_declined")
    return verify_and_apply(db, order.paystack_reference)


def _fail_checkout(
    db: Session, booking: MarketplaceBooking, order: MarketplaceOrder, reason: str
) -> str:
    b = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.id == booking.id)
        .with_for_update()
        .one()
    )
    row = orders.lock_order(db, order.id)  # booking first, then order, as everywhere
    if row.status == OrderStatus.pending and b.status == BookingStatus.pending_payment:
        row.status = OrderStatus.failed
        b.status = BookingStatus.cancelled
        b.closed_reason = reason
    return reason


def verify_and_apply(db: Session, reference: str) -> str:
    """Ask Paystack what happened to `reference` and apply a confirmed payment. The webhook, the
    founder's verify button and the saved-card path all end here, so there is one code path and
    the amount is always checked against what Paystack says it took."""
    try:
        tx = paystack.verify(reference)
    except paystack.PaystackRejected:
        return "not_found_at_paystack"
    return orders.apply_payment(db, reference, tx)


# ------------------------------------------------------------------ founder actions


def lock_for_founder(db: Session, m: Membership, booking_id: uuid.UUID) -> MarketplaceBooking:
    row = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.id == booking_id, MarketplaceBooking.startup_id == m.startup_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def get_for_founder(db: Session, m: Membership, booking_id: uuid.UUID) -> MarketplaceBooking:
    row = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.id == booking_id, MarketplaceBooking.startup_id == m.startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def list_for_founder(
    db: Session,
    m: Membership,
    *,
    tab: str | None,
    status: BookingStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[MarketplaceBooking], int]:
    q = db.query(MarketplaceBooking).filter(
        MarketplaceBooking.startup_id == m.startup_id,
        MarketplaceBooking.status
        != BookingStatus.pending_payment,  # a checkout hold isn't a booking yet
    )
    if tab:
        q = q.filter(MarketplaceBooking.status.in_(TABS[tab]))
    if status:
        q = q.filter(MarketplaceBooking.status == status)
    total = q.count()
    rows = (
        q.order_by(MarketplaceBooking.created_at.desc(), MarketplaceBooking.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


def cancel_by_founder(
    db: Session, user: User, m: Membership, booking_id: uuid.UUID
) -> tuple[MarketplaceBooking, list[EmailMessage]]:
    """Cancel before the expert accepts: no charge if unpaid, full refund if paid (D-MP-4)."""
    b = lock_for_founder(db, m, booking_id)
    order = orders.lock_order(db, _order_for(db, b.id).id)
    mail: list[EmailMessage] = []
    if b.status == BookingStatus.pending_payment:
        b.status = BookingStatus.cancelled
        b.closed_reason = "founder_cancelled"
        b.slot_hold_expires_at = None
        if order.status == OrderStatus.pending:
            order.status = OrderStatus.failed
    elif b.status == BookingStatus.awaiting_provider:
        b.status = BookingStatus.cancelled
        b.closed_reason = "founder_cancelled"
        orders.request_refund(
            db,
            order,
            order.amount_minor - order.refunded_minor - order.refund_due_minor,
            "founder_cancelled",
            actor_id=user.id,
        )
        provider = db.get(MarketplaceProvider, b.provider_id)
        mail.append(
            emails.booking_closed_email(
                user.email,
                provider=provider.display_name if provider else None,
                title=b.title_snap,
                reason="founder_cancelled",
            )
        )
    else:
        raise err(
            "CANCEL_NOT_ALLOWED",
            "This booking can't be cancelled now. Use 'Report a problem' once it's under way.",
            409,
        )
    write_audit(
        db,
        "marketplace.booking.cancelled",
        actor_user_id=user.id,
        startup_id=b.startup_id,
        entity_type="marketplace_booking",
        entity_id=b.id,
    )
    event_bus.publish(db, "marketplace.booking.cancelled", {"booking_id": str(b.id)})
    return b, mail


# ------------------------------------------------------------------ the expert's side


def get_for_provider(
    db: Session, ctx: ProviderContext, booking_id: uuid.UUID
) -> MarketplaceBooking:
    row = (
        db.query(MarketplaceBooking)
        .filter(
            MarketplaceBooking.id == booking_id,
            MarketplaceBooking.provider_id == ctx.provider.id,
            MarketplaceBooking.status != BookingStatus.pending_payment,
        )
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def list_for_provider(
    db: Session,
    ctx: ProviderContext,
    *,
    tab: str | None,
    status: BookingStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[MarketplaceBooking], int]:
    q = db.query(MarketplaceBooking).filter(
        MarketplaceBooking.provider_id == ctx.provider.id,
        MarketplaceBooking.status != BookingStatus.pending_payment,
    )
    if tab:
        q = q.filter(MarketplaceBooking.status.in_(TABS[tab]))
    if status:
        q = q.filter(MarketplaceBooking.status == status)
    total = q.count()
    rows = (
        q.order_by(MarketplaceBooking.created_at.desc(), MarketplaceBooking.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


def _lock_for_provider(
    db: Session, ctx: ProviderContext, booking_id: uuid.UUID
) -> MarketplaceBooking:
    row = (
        db.query(MarketplaceBooking)
        .filter(
            MarketplaceBooking.id == booking_id, MarketplaceBooking.provider_id == ctx.provider.id
        )
        .with_for_update()
        .first()
    )
    if row is None or row.status == BookingStatus.pending_payment:
        raise NotFound()
    return row


def accept(db: Session, ctx: ProviderContext, booking_id: uuid.UUID) -> MarketplaceBooking:
    b = _lock_for_provider(db, ctx, booking_id)
    if b.status != BookingStatus.awaiting_provider:
        raise err("INVALID_STATE", f"Can't accept a booking that is '{b.status.value}'.")
    t = now()
    if (b.accept_by and b.accept_by <= t) or (b.starts_at and b.starts_at <= t):
        raise err("ACCEPT_WINDOW_CLOSED", "The time to accept this booking has passed.", 409)
    b.accepted_at = t
    if b.type == ListingType.consultation.value:
        b.status = BookingStatus.confirmed
    else:
        b.status = BookingStatus.in_progress
        b.due_at = t + timedelta(days=b.delivery_days_snap or 0)
    write_audit(
        db,
        "marketplace.booking.accepted",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_booking",
        entity_id=b.id,
        details={"provider_id": str(b.provider_id)},
    )
    event_bus.publish(db, "marketplace.booking.confirmed", {"booking_id": str(b.id)})
    return b


def decline(
    db: Session, ctx: ProviderContext, booking_id: uuid.UUID
) -> tuple[MarketplaceBooking, uuid.UUID, list[EmailMessage]]:
    b = _lock_for_provider(db, ctx, booking_id)
    if b.status != BookingStatus.awaiting_provider:
        raise err("INVALID_STATE", f"Can't decline a booking that is '{b.status.value}'.")
    order = orders.lock_order(db, _order_for(db, b.id).id)
    b.status = BookingStatus.declined
    b.closed_reason = "provider_declined"
    orders.request_refund(
        db,
        order,
        order.amount_minor - order.refunded_minor - order.refund_due_minor,
        "provider_declined",
        actor_id=ctx.user.id,
    )
    write_audit(
        db,
        "marketplace.booking.declined",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_booking",
        entity_id=b.id,
        details={"provider_id": str(b.provider_id)},
    )
    event_bus.publish(db, "marketplace.booking.declined", {"booking_id": str(b.id)})
    booker = db.get(User, b.booked_by)
    mail = (
        [
            emails.booking_closed_email(
                booker.email,
                provider=ctx.provider.display_name,
                title=b.title_snap,
                reason="provider_declined",
            )
        ]
        if booker
        else []
    )
    return b, order.id, mail
