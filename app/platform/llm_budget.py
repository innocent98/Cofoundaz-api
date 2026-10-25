import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.llm_usage import LlmUsageDaily
from app.platform.llm import LLMMessage, get_llm_client


def today_utc() -> date:
    return datetime.now(UTC).date()


def _used_today(db: Session, startup_id: uuid.UUID) -> int:
    used = db.execute(
        select(LlmUsageDaily.tokens_used).where(
            LlmUsageDaily.startup_id == startup_id, LlmUsageDaily.usage_date == today_utc()
        )
    ).scalar_one_or_none()
    return used or 0


def over_budget(db: Session, startup_id: uuid.UUID) -> bool:
    budget = settings.LLM_DAILY_TOKEN_BUDGET
    if budget <= 0:
        return False  # unlimited
    return _used_today(db, startup_id) >= budget


def headroom(db: Session, startup_id: uuid.UUID) -> int | None:
    """Tokens left today; None when the budget is unlimited."""
    budget = settings.LLM_DAILY_TOKEN_BUDGET
    if budget <= 0:
        return None
    return max(0, budget - _used_today(db, startup_id))


def debit(db: Session, startup_id: uuid.UUID, tokens: int) -> None:
    if tokens <= 0:
        return
    stmt = (
        pg_insert(LlmUsageDaily)
        .values(startup_id=startup_id, usage_date=today_utc(), tokens_used=tokens)
        .on_conflict_do_update(
            index_elements=["startup_id", "usage_date"],
            set_={"tokens_used": LlmUsageDaily.tokens_used + tokens},
        )
    )
    db.execute(stmt)
    db.flush()


def _debit_independent(startup_id: uuid.UUID, tokens: int) -> None:
    from app.db.session import SessionLocal  # local: avoids an import cycle at module load

    if tokens <= 0:
        return
    with SessionLocal() as own:
        debit(own, startup_id, tokens)
        own.commit()


def metered_complete(
    db: Session, startup_id: uuid.UUID, messages: list[LLMMessage], *, max_tokens: int
) -> str | None:
    if over_budget(db, startup_id):
        return None
    client = get_llm_client()
    if hasattr(client, "headroom"):
        client.headroom = headroom(db, startup_id)
    try:
        text = client.complete(messages, max_tokens=max_tokens)
    except Exception:
        # A failed call (e.g. an empty reasoning-budget completion, retried larger) still cost
        # tokens, and the job's savepoint rolls back this session's writes, so bill them on
        # their own session or retries would be free.
        _debit_independent(startup_id, getattr(client, "last_usage_tokens", 0))
        raise
    debit(db, startup_id, getattr(client, "last_usage_tokens", 0))
    return text


def metered_complete_json(
    db: Session, startup_id: uuid.UUID, messages: list[LLMMessage], *, schema: dict, max_tokens: int
) -> dict | None:
    if over_budget(db, startup_id):
        return None
    client = get_llm_client()
    result = client.complete_json(messages, schema=schema, max_tokens=max_tokens)
    debit(db, startup_id, getattr(client, "last_usage_tokens", 0))
    return result


def is_ai_enrichment_job(job_type: str) -> bool:
    return (
        job_type.startswith("ai.")
        or job_type.endswith(".ai_fill")
        or job_type == "business.plan.generate"
    )
