"""Sales outreach jobs: the AI email drafts, the due-step send tick and the bounce poll.

The send tick and the poll are claimed once per time bucket by the scheduler (see
`_due_sales_outreach`), so any number of workers produce one job per bucket; the work itself is
also safe to repeat (row-locked, ledgered).
"""

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.enums import DraftKind, DraftStatus
from app.db.models.job import Job
from app.db.models.sales_sequences import EmailSequence, SalesDraft
from app.db.models.startup import Startup
from app.platform.llm import strip_dashes
from app.platform.llm_budget import metered_complete_json
from app.services.sales.bounces import poll_bounces
from app.services.sales.coach_context import deal_context, fence, load_deal
from app.services.sales.draft_prompts import (
    build_follow_up_messages,
    build_step_messages,
    draft_schema,
)
from app.services.sales.drafts import JOB as DRAFT_JOB
from app.services.sales.sending import run_send_tick
from app.services.sales.sequences import MERGE_TAGS
from app.worker.runner import register_handler
from app.worker.scheduler import SCHED_SALES_BOUNCES, SCHED_SALES_SEND

_TAG_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]*)\s*\}\}")


def _clean(text: Any, limit: int) -> str:
    out = strip_dashes(str(text or "")).strip()
    # An unknown tag would be rejected at save time, so never hand the FE one.
    out = _TAG_RE.sub(lambda m: m.group(0) if m.group(1) in MERGE_TAGS else "", out)
    return out[:limit].strip()


def normalize_draft(raw: dict[str, Any]) -> dict[str, str] | None:
    subject, body = _clean(raw.get("subject"), 200), _clean(raw.get("body"), 5000)
    return {"subject": subject, "body": body} if subject and body else None


def _step_context(db: Session, startup: Startup, draft: SalesDraft) -> dict[str, Any] | None:
    seq = db.get(EmailSequence, draft.sequence_id) if draft.sequence_id else None
    if seq is None or draft.step_index is None or draft.step_index >= len(seq.steps_json):
        return None
    step = seq.steps_json[draft.step_index]
    prior = (draft.output_json or {}).get("instructions") or None
    return {
        "seller": {
            "company": startup.name,
            "industry": startup.industry,
            "what_we_do": (startup.description or "")[:400] or None,
        },
        "sequence": seq.name,
        "step_number": draft.step_index + 1,
        "of_steps": len(seq.steps_json),
        "days_after_previous": step.get("wait_days"),
        "current_subject": step.get("subject"),
        "current_body": step.get("body"),
        "instructions_from_user": prior,
    }


def handle_draft(db: Session, job: Job) -> None:
    """Write a draft email. No commit. Over budget or a model error ends as `failed`."""
    draft = db.get(SalesDraft, uuid.UUID(job.payload["draft_id"]))
    if (
        draft is None
        or draft.status != DraftStatus.generating
        or str(draft.startup_id) != str(job.payload.get("startup_id"))
    ):
        return
    startup = db.get(Startup, draft.startup_id)
    if startup is None:
        return
    if draft.kind == DraftKind.sequence_step:
        ctx = _step_context(db, startup, draft)
        messages = build_step_messages(context=fence(ctx)) if ctx else None
    else:
        deal = load_deal(db, draft.startup_id, draft.deal_id) if draft.deal_id else None
        messages = (
            build_follow_up_messages(context=fence(deal_context(db, startup, deal)))
            if deal
            else None
        )
    if messages is None:
        draft.status, draft.error = DraftStatus.failed, "target_missing"
        db.flush()
        return
    draft.output_json = None
    try:
        raw = metered_complete_json(
            db,
            draft.startup_id,
            messages,
            schema=draft_schema(),
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - a model error must end the draft, not strand it
        log.warning(f"[sales-draft] generation failed: {type(exc).__name__}")
        draft.status, draft.error = DraftStatus.failed, "generation_failed"
    else:
        result = normalize_draft(raw) if raw is not None else None
        if raw is None:
            draft.status, draft.error = DraftStatus.failed, "over_budget"
        elif result is None:
            draft.status, draft.error = DraftStatus.failed, "bad_output"
        else:
            draft.output_json, draft.status = result, DraftStatus.ready
    db.flush()


def handle_send_tick(db: Session, job: Job) -> None:
    """Send every due sequence step. Commits per step itself (each is its own transaction)."""
    res = run_send_tick(db)
    if res.configured and (res.sent or res.rejected or res.failed):
        log.info(
            f"[outreach] tick sent={res.sent} rejected={res.rejected} failed={res.failed} "
            f"capped={res.hourly_capped}"
        )


def handle_bounce_poll(db: Session, job: Job) -> None:
    res = poll_bounces(db)
    if res.configured and res.seen:
        log.info(f"[outreach] bounce poll seen={res.seen} bounced={res.bounced}")


register_handler(DRAFT_JOB, handle_draft)
# Both commit per step / per message, so they manage their own transactions.
register_handler(SCHED_SALES_SEND, handle_send_tick, manages_transactions=True)
register_handler(SCHED_SALES_BOUNCES, handle_bounce_poll, manages_transactions=True)
