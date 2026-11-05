"""The investor pipeline: cards, the seven fixed stages, the interaction log, and the prompt (never
automatic, D-FUND-L3) to record arrived money in Finance."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import InvestorEventKind, InvestorStage, TransactionDirection
from app.db.models.funding import FundingRound, Investor, InvestorEvent
from app.platform.events import event_bus
from app.schemas.finance import TransactionCreate
from app.schemas.funding import (
    EventCreate,
    InvestorCreate,
    InvestorUpdate,
    RecordFunding,
    StageMove,
)
from app.services.finance import runway as runway_svc
from app.services.finance import service as finance_svc
from app.services.finance.categories import FUNDING_CATEGORY
from app.services.funding import rounds as rounds_svc
from app.services.funding.errors import _validation

STAGE_ORDER: tuple[str, ...] = tuple(s.value for s in InvestorStage)
_CLOSED_OUT = {InvestorStage.committed.value, InvestorStage.passed.value}
LEDGER_MAX_MINOR = 2_147_483_647  # Finance stores amount_minor as a 32-bit integer
COMMITTED_CHANGED = "funding.round.committed_changed"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v else None


# ---- reads ----
def get_investor(db: Session, startup_id: uuid.UUID, investor_id: uuid.UUID) -> Investor:
    row = db.query(Investor).filter_by(id=investor_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def serialize(row: Investor, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    overdue = bool(
        row.next_step_on and row.next_step_on < now.date() and row.stage not in _CLOSED_OUT
    )
    return {
        "id": str(row.id),
        "round_id": str(row.round_id),
        "name": row.name,
        "firm": row.firm,
        "type": row.type,
        "stage": row.stage,
        "warm": row.warm,
        "check_min_minor": row.check_min_minor,
        "check_max_minor": row.check_max_minor,
        "committed_minor": row.committed_minor,
        "next_step": row.next_step,
        "next_step_on": _iso(row.next_step_on),
        "next_step_overdue": overdue,
        "last_touch_at": _iso(row.last_touch_at),
        "thesis_notes": row.thesis_notes,
        "source": row.source,
        "passed_reason": row.passed_reason,
        "stage_entered_at": _iso(row.stage_entered_at),
        "days_in_stage": max(0, (now - row.stage_entered_at).days),
        "position": row.position,
        "recorded_in_finance": row.recorded_transaction_id is not None,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_investors(
    db: Session,
    startup_id: uuid.UUID,
    *,
    stage: InvestorStage | None,
    q: str | None,
    warm: bool | None,
    type_: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    rnd = rounds_svc.latest_round(db, startup_id)
    if rnd is None:
        return {"round_id": None, "items": [], "total": 0, "limit": limit, "offset": offset}
    query = db.query(Investor).filter(
        Investor.startup_id == startup_id, Investor.round_id == rnd.id
    )
    if stage is not None:
        query = query.filter(Investor.stage == stage.value)
    if warm is not None:
        query = query.filter(Investor.warm.is_(warm))
    if type_:
        query = query.filter(Investor.type == type_)
    if q and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        query = query.filter(or_(Investor.name.ilike(like), Investor.firm.ilike(like)))
    total = query.count()
    rows = query.all()
    rows.sort(key=lambda r: (STAGE_ORDER.index(r.stage), r.position, r.created_at))
    now = _now()
    page = rows[offset : offset + limit]
    return {
        "round_id": str(rnd.id),
        "items": [serialize(r, now=now) for r in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---- totals and the committed-changed event ----
def totals(db: Session, rnd: FundingRound) -> dict[str, Any]:
    """D-FUND-L2: soft = Term Sheet investors with an amount, committed = Committed with an amount.
    The percentages are not capped, a raise can be oversubscribed."""

    def _sum(stage: InvestorStage) -> int:
        return int(
            db.query(func.coalesce(func.sum(Investor.committed_minor), 0))
            .filter(
                Investor.round_id == rnd.id,
                Investor.stage == stage.value,
                Investor.committed_minor.isnot(None),
            )
            .scalar()
            or 0
        )

    soft, committed = _sum(InvestorStage.term_sheet), _sum(InvestorStage.committed)
    target = rnd.target_minor
    pct = (lambda v: round(v * 100 / target)) if target else (lambda _v: None)
    return {
        "currency": rnd.currency,
        "target_minor": target,
        "soft_minor": soft,
        "committed_minor": committed,
        "soft_pct": pct(soft),
        "committed_pct": pct(committed),
    }


def _publish_if_committed_changed(
    db: Session, rnd: FundingRound, before: int, actor_id: uuid.UUID | None
) -> None:
    after = totals(db, rnd)
    if after["committed_minor"] == before:
        return
    event_bus.publish(
        db,
        COMMITTED_CHANGED,
        {
            "startup_id": str(rnd.startup_id),
            "round_id": str(rnd.id),
            "committed_minor": after["committed_minor"],
            "previous_committed_minor": before,
            "target_minor": rnd.target_minor,
            "currency": rnd.currency,
            "actor_id": str(actor_id) if actor_id else None,
            "href": "/funding",
        },
    )


def _committed_now(db: Session, rnd: FundingRound) -> int:
    return int(totals(db, rnd)["committed_minor"])


# ---- placement ----
def _place(db: Session, inv: Investor, position: int | None) -> None:
    siblings = (
        db.query(Investor)
        .filter(
            Investor.round_id == inv.round_id,
            Investor.stage == inv.stage,
            Investor.id != inv.id,
        )
        .order_by(Investor.position, Investor.created_at)
        .all()
    )
    idx = len(siblings) if position is None else min(position, len(siblings))
    siblings.insert(idx, inv)
    for i, s in enumerate(siblings):
        s.position = i


def _compact(db: Session, round_id: uuid.UUID, stage: str) -> None:
    """Close the gap a card leaves behind when it moves out of a column."""
    rows = (
        db.query(Investor)
        .filter(Investor.round_id == round_id, Investor.stage == stage)
        .order_by(Investor.position, Investor.created_at)
        .all()
    )
    for i, r in enumerate(rows):
        r.position = i


def _check_range(lo: int | None, hi: int | None) -> None:
    if lo is not None and hi is not None and hi < lo:
        raise _validation("check_max_minor", "The maximum cheque can't be below the minimum.")


# ---- writes ----
def create_investor(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID | None, data: InvestorCreate
) -> Investor:
    rnd = rounds_svc.round_for_new_investor(db, startup_id)
    before = _committed_now(db, rnd)
    now = _now()
    row = Investor(
        startup_id=startup_id,
        round_id=rnd.id,
        name=data.name,
        firm=data.firm,
        type=data.type.value,
        stage=data.stage.value,
        warm=data.warm,
        check_min_minor=data.check_min_minor,
        check_max_minor=data.check_max_minor,
        committed_minor=data.committed_minor,
        next_step=data.next_step,
        next_step_on=data.next_step_on,
        thesis_notes=data.thesis_notes,
        source="manual",
        stage_entered_at=now,
        position=0,
    )
    db.add(row)
    db.flush()
    _place(db, row, None)
    db.add(
        InvestorEvent(
            startup_id=startup_id,
            investor_id=row.id,
            kind=InvestorEventKind.stage_change.value,
            body=None,
            from_stage=None,
            to_stage=row.stage,
            occurred_at=now,
            actor_id=actor_id,
        )
    )
    db.flush()
    _publish_if_committed_changed(db, rnd, before, actor_id)
    return row


def update_investor(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    investor_id: uuid.UUID,
    data: InvestorUpdate,
) -> Investor:
    row = get_investor(db, startup_id, investor_id)
    rnd = db.get(FundingRound, row.round_id)
    assert rnd is not None  # noqa: S101 - FK guarantees it
    before = _committed_now(db, rnd)
    given = data.model_fields_set
    # Validate the merged range before touching the row, so a refused edit leaves nothing dirty.
    lo = data.check_min_minor if "check_min_minor" in given else row.check_min_minor
    hi = data.check_max_minor if "check_max_minor" in given else row.check_max_minor
    _check_range(lo, hi)
    for field in ("check_min_minor", "check_max_minor", "committed_minor"):
        if field in given:
            setattr(row, field, getattr(data, field))
    if data.name is not None:
        row.name = data.name
    if data.type is not None:
        row.type = data.type.value
    if data.warm is not None:
        row.warm = data.warm
    for field in ("firm", "next_step", "next_step_on", "thesis_notes"):
        if field in given:
            setattr(row, field, getattr(data, field))
    db.flush()
    _publish_if_committed_changed(db, rnd, before, actor_id)
    return row


def delete_investor(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID | None, investor_id: uuid.UUID
) -> None:
    row = get_investor(db, startup_id, investor_id)
    rnd = db.get(FundingRound, row.round_id)
    assert rnd is not None  # noqa: S101
    before = _committed_now(db, rnd)
    db.delete(row)
    db.flush()
    _publish_if_committed_changed(db, rnd, before, actor_id)


def move_stage(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    investor_id: uuid.UUID,
    data: StageMove,
) -> tuple[Investor, dict[str, Any] | None]:
    row = get_investor(db, startup_id, investor_id)
    rnd = db.get(FundingRound, row.round_id)
    assert rnd is not None  # noqa: S101
    target = data.stage.value
    if target == InvestorStage.passed.value:
        if data.passed_reason is None:
            raise _validation("passed_reason", "Pick a reason for passing.")
    elif data.passed_reason is not None:
        raise _validation("passed_reason", "Only the Passed stage has a reason.")
    before = _committed_now(db, rnd)
    previous = row.stage
    if target == previous:
        if data.position is not None:
            _place(db, row, data.position)
        if target == InvestorStage.passed.value and data.passed_reason is not None:
            row.passed_reason = data.passed_reason.value
        db.flush()
        return row, None
    now = _now()
    row.stage = target
    row.stage_entered_at = now
    row.passed_reason = data.passed_reason.value if data.passed_reason else None
    db.flush()
    _place(db, row, data.position)
    _compact(db, row.round_id, previous)
    db.add(
        InvestorEvent(
            startup_id=startup_id,
            investor_id=row.id,
            kind=InvestorEventKind.stage_change.value,
            body=data.note,
            from_stage=previous,
            to_stage=target,
            occurred_at=now,
            actor_id=actor_id,
        )
    )
    db.flush()
    _publish_if_committed_changed(db, rnd, before, actor_id)
    return row, record_offer(row, rnd)


def record_offer(row: Investor, rnd: FundingRound) -> dict[str, Any] | None:
    """The prompt the FE shows after a move to Committed with an amount (D-FUND-L3). Never
    automatic: nothing is written to Finance until the founder confirms the money arrived."""
    if (
        row.stage != InvestorStage.committed.value
        or not row.committed_minor
        or row.recorded_transaction_id is not None
    ):
        return None
    ngn = rnd.currency == "NGN"
    return {
        "amount_minor": row.committed_minor if ngn else None,
        "currency": rnd.currency,
        "needs_ngn_amount": not ngn,
        "fits_ledger": (row.committed_minor <= LEDGER_MAX_MINOR) if ngn else True,
    }


# ---- interaction log ----
def add_event(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    investor_id: uuid.UUID,
    data: EventCreate,
) -> InvestorEvent:
    if data.kind == InvestorEventKind.stage_change:
        raise _validation("kind", "Stage changes are logged when you move the card.")
    row = get_investor(db, startup_id, investor_id)
    now = _now()
    when = data.occurred_at or now
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if when > now + timedelta(minutes=5):
        raise _validation("occurred_at", "That time is in the future.")
    ev = InvestorEvent(
        startup_id=startup_id,
        investor_id=row.id,
        kind=data.kind.value,
        body=data.body,
        occurred_at=when,
        actor_id=actor_id,
    )
    db.add(ev)
    if row.last_touch_at is None or when > row.last_touch_at:
        row.last_touch_at = when
    db.flush()
    return ev


def serialize_event(ev: InvestorEvent) -> dict[str, Any]:
    return {
        "id": str(ev.id),
        "investor_id": str(ev.investor_id),
        "kind": ev.kind,
        "body": ev.body,
        "from_stage": ev.from_stage,
        "to_stage": ev.to_stage,
        "occurred_at": _iso(ev.occurred_at),
        "actor_id": str(ev.actor_id) if ev.actor_id else None,
    }


def list_events(
    db: Session, startup_id: uuid.UUID, investor_id: uuid.UUID, *, limit: int, offset: int
) -> dict[str, Any]:
    get_investor(db, startup_id, investor_id)
    q = db.query(InvestorEvent).filter(
        InvestorEvent.startup_id == startup_id, InvestorEvent.investor_id == investor_id
    )
    total = q.count()
    rows = (
        q.order_by(InvestorEvent.occurred_at.desc(), InvestorEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [serialize_event(e) for e in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---- Finance hand-off ----
def record_funding(
    db: Session,
    startup_id: uuid.UUID,
    investor_id: uuid.UUID,
    data: RecordFunding,
) -> tuple[Investor, Any]:
    """Write one Funding inflow (cash, never revenue, D-FIN-11) through the Finance ledger service,
    only on the founder's explicit confirmation."""
    get_investor(db, startup_id, investor_id)  # 404 for another workspace's id
    # Row lock: the "already recorded?" read decides a ledger write, so two concurrent confirms
    # must queue here and the second one sees the first one's transaction id.
    row = (
        db.query(Investor)
        .filter_by(id=investor_id, startup_id=startup_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    rnd = db.get(FundingRound, row.round_id)
    assert rnd is not None  # noqa: S101
    if row.stage != InvestorStage.committed.value or not row.committed_minor:
        raise AppError("NOT_COMMITTED", "Move the investor to Committed with an amount first.", 409)
    if row.recorded_transaction_id is not None:
        raise AppError("ALREADY_RECORDED", "This money is already recorded in Finance.", 409)
    amount = data.amount_minor
    if amount is None:
        if rnd.currency != "NGN":
            raise _validation(
                "amount_minor",
                f"This round is in {rnd.currency} and Finance records NGN. "
                "Enter the naira amount you received.",
            )
        amount = row.committed_minor
    if amount > LEDGER_MAX_MINOR:
        raise _validation(
            "amount_minor",
            "Finance records up to ₦21,474,836.47 per entry. Enter a smaller amount, "
            "or add the rest in Finance.",
        )
    who = f"{row.name} ({row.firm})" if row.firm else row.name
    tx = finance_svc.create_transaction(
        db,
        startup_id=startup_id,
        data=TransactionCreate(
            date=data.received_on or _now().date(),
            description=f"Funding from {who}"[:300],
            category=FUNDING_CATEGORY,
            amount_minor=amount,
            currency="NGN",
            direction=TransactionDirection.inflow,
        ),
    )
    row.recorded_transaction_id = tx.id
    db.flush()
    runway_svc.evaluate_runway_alert(db, startup_id=startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=startup_id)
    return row, tx
