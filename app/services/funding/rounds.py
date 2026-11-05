"""The raise: one unfinished round per workspace (D-FUND-L1), currency fixed per round (D-FUND-7)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.enums import FundingRoundStatus
from app.db.models.funding import FundingRound, Investor
from app.schemas.funding import RoundUpdate
from app.services.funding.errors import _validation

DEFAULT_ROUND_NAME = "Funding round"


def latest_round(db: Session, startup_id: uuid.UUID) -> FundingRound | None:
    """The unfinished round if there is one, else the most recently closed (read-only history)."""
    return (
        db.query(FundingRound)
        .filter(FundingRound.startup_id == startup_id)
        .order_by(
            (FundingRound.status != FundingRoundStatus.closed.value).desc(),
            FundingRound.created_at.desc(),
            FundingRound.id.desc(),
        )
        .first()
    )


def active_round(db: Session, startup_id: uuid.UUID) -> FundingRound | None:
    return (
        db.query(FundingRound)
        .filter(
            FundingRound.startup_id == startup_id,
            FundingRound.status != FundingRoundStatus.closed.value,
        )
        .one_or_none()
    )


def round_for_new_investor(db: Session, startup_id: uuid.UUID) -> FundingRound:
    """Where a new investor lands. The first investor creates a planning round, so "Add your first
    investor" works with no setup; after a round is closed the founder starts the next one
    explicitly (PUT /round), never silently."""
    current = latest_round(db, startup_id)
    if current is None:
        return _create(db, startup_id, RoundUpdate()) or _lost_race(db, startup_id)
    if current.status == FundingRoundStatus.closed.value:
        raise AppError("ROUND_CLOSED", "Your last round is closed. Start a new round first.", 409)
    return current


def _lost_race(db: Session, startup_id: uuid.UUID) -> FundingRound:
    """Another request created the unfinished round between our read and our insert."""
    row = active_round(db, startup_id)
    if row is None:  # closed again in the meantime: ask the caller to retry
        raise AppError("ROUND_CONFLICT", "The round just changed. Try again.", 409)
    return row


def _create(db: Session, startup_id: uuid.UUID, data: RoundUpdate) -> FundingRound | None:
    """Insert in a savepoint. None means the one-unfinished-round index fired (a concurrent
    create won); the caller re-reads instead of surfacing a 500."""
    given = data.model_fields_set
    status = data.status.value if data.status else FundingRoundStatus.planning.value
    if status == FundingRoundStatus.closed.value:
        raise _validation("status", "A new round can't start closed.")
    row = FundingRound(
        startup_id=startup_id,
        name=data.name or DEFAULT_ROUND_NAME,
        status=status,
        currency=data.currency.value if data.currency else "NGN",
        target_minor=data.target_minor if "target_minor" in given else None,
        pre_money_minor=data.pre_money_minor if "pre_money_minor" in given else None,
        opened_at=datetime.now(UTC) if status == FundingRoundStatus.open.value else None,
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        return None
    return row


def upsert_round(db: Session, startup_id: uuid.UUID, data: RoundUpdate) -> FundingRound:
    row = active_round(db, startup_id)
    if row is None:
        row = _create(db, startup_id, data)
        if row is not None:
            return row
        row = _lost_race(db, startup_id)  # then apply this request on top of the winner
    given = data.model_fields_set
    if data.currency is not None and data.currency.value != row.currency:
        recorded = (
            db.query(func.count(Investor.id))
            .filter(Investor.round_id == row.id, Investor.recorded_transaction_id.isnot(None))
            .scalar()
        )
        if recorded:
            raise AppError(
                "ROUND_HAS_RECORDED_MONEY",
                "Money from this round is already recorded in Finance, so its currency is fixed.",
                409,
            )
        has_money = (
            db.query(func.count(Investor.id))
            .filter(
                Investor.round_id == row.id,
                (
                    Investor.check_min_minor.isnot(None)
                    | Investor.check_max_minor.isnot(None)
                    | Investor.committed_minor.isnot(None)
                ),
            )
            .scalar()
        )
        if has_money:
            raise _validation(
                "currency",
                "This round already has amounts in "
                f"{row.currency}. Clear them before changing the currency.",
            )
        from app.services.funding import cap_table as cap_svc  # noqa: PLC0415 - avoids a cycle

        if cap_svc.has_money(db, startup_id):
            raise AppError(
                "CAP_TABLE_HAS_AMOUNTS",
                "Your cap table already has amounts in "
                f"{row.currency}. Clear them before changing the currency.",
                409,
            )
        row.currency = data.currency.value
    if data.name is not None:
        row.name = data.name
    if "target_minor" in given:
        row.target_minor = data.target_minor
    if "pre_money_minor" in given:
        row.pre_money_minor = data.pre_money_minor
    if data.status is not None and data.status.value != row.status:
        row.status = data.status.value
        now = datetime.now(UTC)
        if row.status == FundingRoundStatus.open.value and row.opened_at is None:
            row.opened_at = now
        if row.status == FundingRoundStatus.closed.value:
            row.closed_at = now
    db.flush()
    return row


def serialize(row: FundingRound) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "status": row.status,
        "currency": row.currency,
        "target_minor": row.target_minor,
        "pre_money_minor": row.pre_money_minor,
        "opened_at": row.opened_at.isoformat() if row.opened_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
