"""Sales coach jobs: the role-play buyer's reply, the end-of-session scorecard and call prep.

Same shape as `marketing_ai.py` / the co-founder reply: each handler no-ops unless its row is
still `generating`/`scoring` (idempotent), meters every call against the daily budget, and
never commits (the runner owns the transaction). Unlike the co-founder chat, a model error is
caught and recorded on the row, so the FE sees `failed` instead of polling a row that never
finishes.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.enums import (
    CallPrepStatus,
    ChatMessageRole,
    ChatMessageStatus,
    CoachSessionStatus,
)
from app.db.models.job import Job
from app.db.models.sales_coach import SalesCallPrep, SalesCoachMessage, SalesCoachSession
from app.db.models.startup import Startup
from app.platform.llm import strip_dashes
from app.platform.llm_budget import metered_complete, metered_complete_json
from app.services.sales.call_prep import JOB as CALL_PREP_JOB
from app.services.sales.coach import REPLY_JOB, SCORECARD_JOB
from app.services.sales.coach_context import deal_context, fence, load_deal
from app.services.sales.coach_prompts import (
    build_call_prep_messages,
    build_roleplay_messages,
    build_scorecard_messages,
    call_prep_schema,
    scorecard_schema,
)
from app.worker.runner import register_handler

_HISTORY_LIMIT = 30
_SCORES = ("discovery", "objection_handling", "next_step_close")


def _ready_turns(db: Session, session_id: uuid.UUID, *, limit: int | None = None) -> list[Any]:
    stmt = (
        select(SalesCoachMessage)
        .where(
            SalesCoachMessage.session_id == session_id,
            SalesCoachMessage.status == ChatMessageStatus.ready,
        )
        .order_by(SalesCoachMessage.created_at.desc(), SalesCoachMessage.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(reversed(list(db.scalars(stmt))))


def handle_coach_reply(db: Session, job: Job) -> None:
    """Fill the buyer's turn of a role-play. No commit. Over budget or a model error -> `failed`."""
    msg = db.get(SalesCoachMessage, uuid.UUID(job.payload["message_id"]))
    if (
        msg is None
        or msg.role != ChatMessageRole.assistant
        or msg.status != ChatMessageStatus.generating
    ):
        return
    session = db.get(SalesCoachSession, msg.session_id)
    if session is None or str(session.startup_id) != str(job.payload.get("startup_id")):
        return
    prior = [m for m in _ready_turns(db, session.id, limit=_HISTORY_LIMIT) if m.content]
    messages = build_roleplay_messages(
        persona=session.persona,
        difficulty=session.difficulty,
        context=fence(session.context_json),
        history=[(m.role.value, m.content) for m in prior],
    )
    try:
        text = metered_complete(
            db, session.startup_id, messages, max_tokens=settings.LLM_MAX_TOKENS
        )
    except Exception as exc:  # noqa: BLE001 - a model error must end the turn, not strand it
        log.warning(f"[sales-coach] reply failed: {exc}")
        text = None
    reply = strip_dashes(text).strip() if text else ""
    msg.status = ChatMessageStatus.ready if reply else ChatMessageStatus.failed
    msg.content = reply
    db.flush()


register_handler(REPLY_JOB, handle_coach_reply)


def _score(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(10, round(value)))


def normalize_scorecard(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Clamp each score to a whole 0..10, derive the overall from them, clean the feedback.
    None when any score is missing or not a number."""
    scores = {k: _score(raw.get(k)) for k in _SCORES}
    if any(v is None for v in scores.values()):
        return None
    feedback = [
        strip_dashes(str(line)).strip() for line in (raw.get("feedback") or []) if str(line).strip()
    ][:8]
    values = [int(v) for v in scores.values() if v is not None]
    return {
        "scores": scores,
        "overall": round(sum(values) / len(values), 1),
        "feedback": feedback,
    }


def _finish_scoring(session: SalesCoachSession, card: dict[str, Any] | None, error: str) -> None:
    session.status = CoachSessionStatus.ended
    session.scorecard_json = card
    session.scorecard_error = None if card else error


def handle_coach_scorecard(db: Session, job: Job) -> None:
    """Score the seller's side of an ended session. No commit. On failure the session is `ended`
    with `scorecard_error` set; ending it again retries."""
    session = db.get(SalesCoachSession, uuid.UUID(job.payload["session_id"]))
    if (
        session is None
        or session.status != CoachSessionStatus.scoring
        or str(session.startup_id) != str(job.payload.get("startup_id"))
    ):
        return
    transcript = [(m.role.value, m.content) for m in _ready_turns(db, session.id) if m.content]
    try:
        raw = metered_complete_json(
            db,
            session.startup_id,
            build_scorecard_messages(
                persona=session.persona, difficulty=session.difficulty, transcript=transcript
            ),
            schema=scorecard_schema(),
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[sales-coach] scorecard failed: {exc}")
        _finish_scoring(session, None, "generation_failed")
    else:
        if raw is None:
            _finish_scoring(session, None, "over_budget")
        else:
            card = normalize_scorecard(raw)
            _finish_scoring(session, card, "bad_output")
    db.flush()


register_handler(SCORECARD_JOB, handle_coach_scorecard)


def _clean_list(items: Any, limit: int) -> list[str]:
    out = [strip_dashes(str(i)).strip() for i in (items if isinstance(items, list) else [])]
    return [i for i in out if i][:limit]


def normalize_call_prep(raw: dict[str, Any]) -> dict[str, Any]:
    objections = []
    for o in raw.get("objections") or []:
        if not isinstance(o, dict):
            continue
        objection = strip_dashes(str(o.get("objection", ""))).strip()
        response = strip_dashes(str(o.get("response", ""))).strip()
        if objection and response:
            objections.append({"objection": objection, "response": response})
    return {
        "context": strip_dashes(str(raw.get("context", ""))).strip(),
        "objections": objections[:5],
        "call_goal": strip_dashes(str(raw.get("call_goal", ""))).strip(),
        "discovery_questions": _clean_list(raw.get("discovery_questions"), 3),
    }


def _fail_prep(prep: SalesCallPrep, error: str) -> None:
    prep.status = CallPrepStatus.failed
    prep.error = error


def handle_call_prep(db: Session, job: Job) -> None:
    """Write a call-prep sheet for a deal from its live context. No commit."""
    prep = db.get(SalesCallPrep, uuid.UUID(job.payload["prep_id"]))
    if (
        prep is None
        or prep.status != CallPrepStatus.generating
        or str(prep.startup_id) != str(job.payload.get("startup_id"))
    ):
        return
    startup = db.get(Startup, prep.startup_id)
    deal = load_deal(db, prep.startup_id, prep.deal_id)
    if startup is None or deal is None:
        _fail_prep(prep, "deal_missing")
        db.flush()
        return
    try:
        raw = metered_complete_json(
            db,
            prep.startup_id,
            build_call_prep_messages(context=fence(deal_context(db, startup, deal))),
            schema=call_prep_schema(),
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[sales-coach] call prep failed: {exc}")
        _fail_prep(prep, "generation_failed")
    else:
        if raw is None:
            _fail_prep(prep, "over_budget")
        else:
            prep.output_json = normalize_call_prep(raw)
            prep.status = CallPrepStatus.ready
    db.flush()


register_handler(CALL_PREP_JOB, handle_call_prep)
