import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

from app.core.config import settings
from app.core.logger import log
from app.core.redis import get_redis

if TYPE_CHECKING:
    from redis.asyncio.client import PubSub


def channel_for(startup_id: str, user_id: str) -> str:
    return f"notif:{startup_id}:{user_id}"


def publish_notification(startup_id: str, user_id: str, payload: dict) -> None:
    """Best-effort live publish. Runs in an after_commit listener, so it must never raise:
    the DB row + email job are the durable path; SSE is a live optimization."""
    try:
        get_redis().publish(channel_for(startup_id, user_id), json.dumps(payload))
    except Exception as exc:  # noqa: BLE001 - live delivery is best-effort
        log.warning(f"[realtime] publish failed (startup={startup_id} user={user_id}): {exc}")


def mint_stream_ticket(user_id: str, startup_id: str) -> str:
    tok = secrets.token_urlsafe(32)
    get_redis().set(
        f"sse_ticket:{tok}", f"{user_id}:{startup_id}", ex=settings.SSE_TICKET_TTL, nx=True
    )
    return tok


def consume_stream_ticket(tok: str) -> tuple[str, str] | None:
    """One-time consume via GETDEL (Redis >= 6.2). Returns (user_id, startup_id) or None."""
    raw = cast(str | None, get_redis().getdel(f"sse_ticket:{tok}"))
    if not raw or ":" not in raw:
        return None
    user_id, _, startup_id = raw.partition(":")
    return user_id, startup_id


@asynccontextmanager
async def subscription(channel: str) -> AsyncIterator["PubSub"]:
    """Async context manager yielding a subscribed redis.asyncio pubsub; cleans up on exit."""
    import redis.asyncio as aioredis  # lazy: keep asyncio client out of module import

    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        yield pubsub
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()
