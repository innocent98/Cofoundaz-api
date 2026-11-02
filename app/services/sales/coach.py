import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import ChatMessageRole, ChatMessageStatus, CoachSessionStatus
from app.db.models.sales_coach import SalesCoachMessage, SalesCoachSession
from app.db.models.startup import Startup
from app.platform.jobs import job_dispatcher
from app.schemas.sales_coach import CoachSessionCreate
from app.services.sales.coach_context import deal_context, load_deal, scenario_context
from app.services.sales.errors import _validation

REPLY_JOB = "ai.sales.coach_reply"
SCORECARD_JOB = "ai.sales.coach_scorecard"


def get_session(
    db: Session,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    lock: bool = False,
) -> SalesCoachSession:
    """Practice sessions are private to the member who started them (like the co-founder chat)."""
    stmt = select(SalesCoachSession).where(
        SalesCoachSession.id == session_id,
        SalesCoachSession.startup_id == startup_id,
        SalesCoachSession.created_by == user_id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound()
    return row


def list_sessions(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, *, limit: int, offset: int
) -> list[SalesCoachSession]:
    return list(
        db.scalars(
            select(SalesCoachSession)
            .where(
                SalesCoachSession.startup_id == startup_id,
                SalesCoachSession.created_by == user_id,
            )
            .order_by(SalesCoachSession.created_at.desc(), SalesCoachSession.id)
            .limit(limit)
            .offset(offset)
        )
    )


def messages_for(db: Session, session: SalesCoachSession) -> list[SalesCoachMessage]:
    return list(
        db.scalars(
            select(SalesCoachMessage)
            .where(SalesCoachMessage.session_id == session.id)
            .order_by(SalesCoachMessage.created_at.asc(), SalesCoachMessage.id.asc())
        )
    )


def create_session(
    db: Session, startup: Startup, user_id: uuid.UUID, data: CoachSessionCreate
) -> SalesCoachSession:
    if data.deal_id is None and data.scenario is None:
        raise _validation("deal_id", "Pick a deal or describe a scenario.")
    if data.deal_id is not None:
        deal = load_deal(db, startup.id, data.deal_id)
        if deal is None:
            raise _validation("deal_id", "Deal not found.")
        context = deal_context(db, startup, deal)
    else:
        context = scenario_context(startup, data.scenario or "")
    row = SalesCoachSession(
        startup_id=startup.id,
        created_by=user_id,
        mode="roleplay",
        deal_id=data.deal_id,
        scenario=data.scenario,
        persona=data.persona,
        difficulty=data.difficulty,
        context_json=context,
        status=CoachSessionStatus.active,
    )
    db.add(row)
    db.flush()
    if data.message:
        post_message(db, row, data.message)
    return row


def _in_flight(db: Session, session_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(SalesCoachMessage.id).where(
                SalesCoachMessage.session_id == session_id,
                SalesCoachMessage.role == ChatMessageRole.assistant,
                SalesCoachMessage.status == ChatMessageStatus.generating,
            )
        )
        is not None
    )


def _reply_in_progress() -> AppError:
    return AppError(
        "REPLY_IN_PROGRESS",
        "The buyer is still replying. Please wait.",
        409,
    )


def post_message(db: Session, session: SalesCoachSession, content: str) -> SalesCoachMessage:
    """Store the seller's turn plus an empty `generating` buyer turn and enqueue the reply job.
    Returns the buyer turn to poll for."""
    if session.status != CoachSessionStatus.active:
        raise AppError("SESSION_ENDED", "This practice session has ended.", 409)
    if _in_flight(db, session.id):
        raise _reply_in_progress()
    # Same 1ms gap as the co-founder chat: both rows share one transaction, so their
    # created_at would otherwise tie and the turn order would be nondeterministic.
    now = datetime.now(UTC)
    db.add(
        SalesCoachMessage(
            session_id=session.id,
            role=ChatMessageRole.user,
            status=ChatMessageStatus.ready,
            content=content,
            created_at=now,
        )
    )
    buyer = SalesCoachMessage(
        session_id=session.id,
        role=ChatMessageRole.assistant,
        status=ChatMessageStatus.generating,
        content="",
        created_at=now + timedelta(milliseconds=1),
    )
    db.add(buyer)
    session.updated_at = now + timedelta(milliseconds=1)
    db.flush()
    job_dispatcher.enqueue(
        db,
        REPLY_JOB,
        {"message_id": str(buyer.id), "startup_id": str(session.startup_id)},
        session.startup_id,
    )
    return buyer


def _seller_turns(db: Session, session_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(SalesCoachMessage)
            .where(
                SalesCoachMessage.session_id == session_id,
                SalesCoachMessage.role == ChatMessageRole.user,
            )
        )
        or 0
    )


def end_session(db: Session, session: SalesCoachSession) -> SalesCoachSession:
    """Close the call and queue the scorecard. Ending a session whose scorecard failed retries
    it; ending one that already has a scorecard changes nothing."""
    if session.status == CoachSessionStatus.scoring:
        raise AppError("SCORING_IN_PROGRESS", "The scorecard is already being written.", 409)
    if session.status == CoachSessionStatus.ended and session.scorecard_json is not None:
        return session
    if _in_flight(db, session.id):
        raise _reply_in_progress()
    if _seller_turns(db, session.id) == 0:
        raise AppError(
            "NOTHING_TO_SCORE", "Say something to the buyer before ending the session.", 422
        )
    session.status = CoachSessionStatus.scoring
    session.scorecard_error = None
    session.ended_at = session.ended_at or datetime.now(UTC)
    db.flush()
    job_dispatcher.enqueue(
        db,
        SCORECARD_JOB,
        {"session_id": str(session.id), "startup_id": str(session.startup_id)},
        session.startup_id,
    )
    return session


def serialize_message(m: SalesCoachMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "role": m.role.value,
        "status": m.status.value,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def serialize_session(
    s: SalesCoachSession, messages: list[SalesCoachMessage] | None = None
) -> dict[str, Any]:
    deal_ctx = (s.context_json or {}).get("deal") or {}
    out: dict[str, Any] = {
        "id": str(s.id),
        "mode": s.mode,
        "deal_id": str(s.deal_id) if s.deal_id else None,
        "deal_name": deal_ctx.get("name"),
        "scenario": s.scenario,
        "persona": s.persona,
        "difficulty": s.difficulty.value,
        "status": s.status.value,
        "scorecard": s.scorecard_json,
        "scorecard_error": s.scorecard_error,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
    }
    if messages is not None:
        out["messages"] = [serialize_message(m) for m in messages]
    return out
