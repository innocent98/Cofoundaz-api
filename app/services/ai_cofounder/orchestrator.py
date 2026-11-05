"""The AI Co-Founder reply job: route, gather context, call the agent, store the result.

Money and retries (the rules the Readiness slices learned the hard way):
  * Every model call goes through `llm_budget` metering. A failed call is billed on its own
    session by the metering helpers, so a retry is never free.
  * The routing decision is committed (with the router's debit) BEFORE the agent call, so a worker
    retry or a reaped job reuses it instead of paying for routing again.
  * After the agent call returns, nothing can raise before the result and the debit commit
    together: the writes are one conditional UPDATE and pure serialisation.
  * The final write is conditional on the turn still being `generating`, so a Cancel that landed
    while the model was running wins; the tokens spent stay billed.
The handler is registered as self-managed (it commits its own units of work)."""

import uuid
from typing import cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.ai_chat import AiAgentRoute, AiConversation, AiMessage
from app.db.models.enums import ChatMessageRole, ChatMessageStatus
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.llm import EmptyCompletionError
from app.platform.llm_budget import headroom, metered_complete
from app.services.ai_cofounder.agents import AGENTS
from app.services.ai_cofounder.context import build_context
from app.services.ai_cofounder.prompts import build_agent_messages
from app.services.ai_cofounder.router import RouteDecision, route_message
from app.services.ai_cofounder.service import BUDGET_FAILED_TEXT, REPLY_FAILED_TEXT, fail_message
from app.services.ai_preferences import get_prefs

HISTORY_TURNS = 20  # cap so one long conversation can't amplify the input cost of every reply
HISTORY_CHARS = 24_000  # and by size: oldest turns drop first (a turn may be 8000 chars)
CHARS_PER_TOKEN = 3  # deliberately pessimistic, so the pre-flight check errs toward refusing


def _history(db: Session, conversation: AiConversation, msg: AiMessage) -> list[AiMessage]:
    return list(
        reversed(
            db.query(AiMessage)
            .filter(
                AiMessage.conversation_id == conversation.id,
                AiMessage.id != msg.id,
                AiMessage.status == ChatMessageStatus.ready,
                AiMessage.removed_by_moderation_at.is_(None),  # removed by staff: never re-fed
            )
            .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
            .limit(HISTORY_TURNS)
            .all()
        )
    )


def _trim_history(history: list[AiMessage]) -> list[AiMessage]:
    """Newest turns that fit in HISTORY_CHARS; the oldest are dropped first."""
    kept: list[AiMessage] = []
    total = 0
    for m in reversed(history):
        total += len(m.content or "")
        if kept and total > HISTORY_CHARS:
            break
        kept.append(m)
    return list(reversed(kept))


def _input_tokens_estimate(messages: list) -> int:
    return sum(len(m.content) for m in messages) // CHARS_PER_TOKEN + 1


def _stored_route(db: Session, msg: AiMessage) -> AiAgentRoute | None:
    return db.query(AiAgentRoute).filter_by(message_id=msg.id).first()


def _turn_still_exists(db: Session, msg: AiMessage) -> bool:
    """Re-read the assistant row under a row lock: a concurrent DELETE of the conversation then
    waits for us, or has already removed the row (see the delete guard in `service`)."""
    return (
        db.query(AiMessage.id).filter(AiMessage.id == msg.id).with_for_update().first() is not None
    )


def _decide_and_store(
    db: Session, conversation: AiConversation, msg: AiMessage, prior: list[AiMessage]
) -> AiAgentRoute | None:
    """Route, store the decision and commit. None when the conversation was deleted while the
    router ran: the router's debit is committed (the call was paid for) and nothing else is
    written, because a route row for a gone message would violate its foreign key, raise, and
    roll the debit back with it."""
    # the founder's message that this turn answers is the user row just before it
    asked = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.role == ChatMessageRole.user,
            AiMessage.created_at < msg.created_at,
        )
        .order_by(AiMessage.created_at.desc())
        .first()
    )
    previous_agent = next(
        (
            m.agent_key
            for m in reversed(prior)
            if m.role == ChatMessageRole.assistant and m.agent_key
        ),
        None,
    )
    # plain ids up front: after the commit below an expired row that was deleted cannot be read
    conv_id, startup_id, msg_id = conversation.id, conversation.startup_id, msg.id
    d: RouteDecision = route_message(
        db, startup_id, asked.content if asked else "", previous_agent=previous_agent
    )
    # Make the router's debit durable before anything else is written or locked: a Cancel or a
    # DELETE landing now can no longer take the router tokens with it (review of #228).
    db.commit()
    agent = AGENTS[d.agent_key]
    route = AiAgentRoute(
        message_id=msg_id,
        conversation_id=conv_id,
        startup_id=startup_id,
        agent_key=agent.key,
        method=d.method,
        confidence=d.confidence,
        prompt_version=agent.version,
    )
    # Lock order matches DELETE (conversation, then its messages), so the two cannot deadlock.
    locked = (
        db.query(AiConversation.id).filter(AiConversation.id == conv_id).with_for_update().first()
    )
    current = (
        db.query(AiMessage.status).filter(AiMessage.id == msg_id).with_for_update().scalar()
        if locked
        else None
    )
    if current != ChatMessageStatus.generating:  # deleted or cancelled meanwhile
        db.commit()
        return None
    db.add(route)
    # Tag the conversation at its first routed reply (History / list topic filter).
    db.execute(
        update(AiConversation)
        .where(AiConversation.id == conv_id, AiConversation.topic.is_(None))
        .values(topic=agent.topic)
    )
    db.execute(
        update(AiMessage)
        .where(AiMessage.id == msg_id, AiMessage.status == ChatMessageStatus.generating)
        .values(agent_key=agent.key)
    )
    db.commit()  # the decision and the router's debit are now durable
    db.refresh(msg)
    return route


def reasoning_summary(route: AiAgentRoute, labels: list[str], reason: str) -> str:
    agent = AGENTS[route.agent_key]
    used = ", ".join(labels) if labels else "no workspace data (none available yet)"
    return f"Answered by the {agent.name} because {reason}. I used: {used}."


_REASON = {
    "rules": "your message is about {topic}",
    "carryover": "this continues your last topic",
    "llm": "the orchestrator classified your message as {topic}",
    "fallback": "there was no clear match, so the Strategist took it",
}


def _end_failed(db: Session, msg: AiMessage, text: str) -> None:
    """Fail the turn (unless it was deleted or already ended) and commit."""
    if _turn_still_exists(db, msg):
        fail_message(db, msg, text)
    db.commit()


def _store_reply(
    db: Session,
    msg: AiMessage,
    route: AiAgentRoute,
    text: str,
    used: list[dict[str, str]],
    conversation: AiConversation,
) -> None:
    """From the lock to the commit nothing can fail: the model call is already paid for."""
    agent = AGENTS[route.agent_key]
    reason = _REASON.get(route.method, "").format(topic=agent.topic)
    changed = cast(
        CursorResult,
        db.execute(
            update(AiMessage)
            .where(AiMessage.id == msg.id, AiMessage.status == ChatMessageStatus.generating)
            .values(
                content=text.strip(),
                status=ChatMessageStatus.ready,
                agent_key=agent.key,
                reasoning_summary=reasoning_summary(route, [u["label"] for u in used], reason),
                disclaimer=agent.disclaimer,
                sources=used,
            )
        ),
    ).rowcount
    if changed:
        event_bus.publish(
            db,
            "ai.cofounder.reply.completed",
            {
                "message_id": str(msg.id),
                "conversation_id": str(conversation.id),
                "startup_id": str(conversation.startup_id),
                "agent_key": agent.key,
                "method": route.method,
            },
        )
    db.commit()  # the reply (or the discard after a Cancel) and the debit land together


def run_reply(db: Session, job: Job) -> None:
    try:
        msg = db.get(AiMessage, uuid.UUID(str(job.payload.get("message_id"))))
    except ValueError:
        msg = None
    if (
        msg is None
        or msg.role != ChatMessageRole.assistant
        or msg.status != ChatMessageStatus.generating  # done, failed, or cancelled: nothing to do
    ):
        return
    conversation = db.get(AiConversation, msg.conversation_id)
    startup = db.get(Startup, conversation.startup_id) if conversation else None
    if conversation is None or startup is None:
        return

    prior = _trim_history(_history(db, conversation, msg))
    route = _stored_route(db, msg)
    if route is None:
        route = _decide_and_store(db, conversation, msg, prior)
        if route is None or msg.status != ChatMessageStatus.generating:
            return  # deleted or cancelled while routing
    agent = AGENTS[route.agent_key]

    context, used = build_context(db, startup, conversation.created_by, agent.sections)
    tone = (
        get_prefs(db, user_id=conversation.created_by, startup_id=startup.id).tone
        if conversation.created_by
        else None
    )
    messages = build_agent_messages(
        agent=agent,
        context=context,
        history=[(m.role.value, m.content) for m in prior if m.content],
        tone=tone,
    )
    # Pre-flight: refuse when what is left of today's allowance cannot cover this call, instead of
    # overshooting the cap by one big turn. Nothing is called or spent.
    left = headroom(db, startup.id)
    if left is not None and left < _input_tokens_estimate(messages) + settings.LLM_CHAT_MAX_TOKENS:
        _end_failed(db, msg, BUDGET_FAILED_TEXT)
        return
    try:
        text = metered_complete(db, startup.id, messages, max_tokens=settings.LLM_CHAT_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 - any provider failure must end the turn eventually
        # The failed call was already billed on its own session. An empty completion repeats for
        # the same prompt, so end the turn now instead of spending the budget on retries.
        if (
            not isinstance(exc, EmptyCompletionError)
            and job.attempts < settings.WORKER_MAX_ATTEMPTS
        ):
            raise  # the runner rolls back and retries with backoff; the route is already stored
        log.error(f"[ai] cofounder reply {msg.id} failed on attempt {job.attempts}: {exc}")
        _end_failed(db, msg, REPLY_FAILED_TEXT)
        return
    if text is None:  # over budget: no templated fallback for a chat turn
        _end_failed(db, msg, BUDGET_FAILED_TEXT)
        return
    if not _turn_still_exists(db, msg):
        db.commit()  # deleted while the model ran: the call is paid for, so bill it
        return
    _store_reply(db, msg, route, text, used, conversation)
