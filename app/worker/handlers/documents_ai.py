import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.document import Document
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.llm_budget import metered_complete, over_budget
from app.services.business.plan_context import build_plan_context
from app.services.documents.common import search_text_of
from app.services.documents.draft_prompt import build_draft_messages
from app.services.documents.generator import AI_DRAFT_JOB
from app.services.documents.service import validate_sections
from app.services.documents.template_defs import DOCUMENT_TEMPLATES, DocumentTemplate, SectionDef
from app.services.documents.versions import snapshot
from app.worker.runner import register_handler


def _result(
    status: str,
    *,
    drafted: list[str],
    skipped: list[str],
    failed: list[str],
    reason: str | None = None,
    version: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "drafted": drafted,
        "skipped": skipped,
        "failed": failed,
        "reason": reason,
        "document_version": version,
    }


def _body_of(doc: Document, heading: str) -> str | None:
    return next((s.get("body") or "" for s in doc.sections if s.get("heading") == heading), None)


def _write_drafts(
    db: Session, doc: Document, sections: dict[str, SectionDef], drafted: dict[str, str]
) -> tuple[list[str], list[str], int | None]:
    """Write drafts into sections that are STILL empty under the row lock. Returns
    `(written, skipped, version)`; `version` is None when nothing was written."""
    current = [dict(s) for s in doc.sections]
    written: list[str] = []
    skipped: list[str] = []
    for key, text in drafted.items():
        idx = next(
            (i for i, s in enumerate(current) if s.get("heading") == sections[key].heading), None
        )
        if idx is None or (current[idx].get("body") or "").strip():
            skipped.append(key)
            continue
        current[idx]["body"] = text
        written.append(key)
    if not written:
        return written, skipped, None
    doc.sections = validate_sections(current)
    doc.search_text = search_text_of(doc.sections)
    doc.ai_generated = True
    doc.version += 1
    db.flush()
    names = ", ".join(sections[k].heading for k in written)
    snapshot(db, doc, author_id=None, note=f"AI drafted: {names}")
    return written, skipped, doc.version


def _draft_all(
    db: Session,
    doc: Document,
    startup: Startup,
    tmpl: DocumentTemplate,
    todo: list[SectionDef],
    *,
    last_attempt: bool,
) -> tuple[dict[str, str], list[str], list[str], str | None]:
    """Ask the model for each still-empty section: `(drafted, skipped, failed, reason)`."""
    context = build_plan_context(db, startup)
    drafted: dict[str, str] = {}
    skipped: list[str] = []
    failed: list[str] = []
    reason: str | None = None
    for i, sec in enumerate(todo):
        current = _body_of(doc, sec.heading)
        if current is None or current.strip():
            skipped.append(sec.key)
            continue
        siblings = [(s.get("heading", ""), s.get("body", "")) for s in doc.sections]
        try:
            text = metered_complete(
                db,
                startup.id,
                build_draft_messages(
                    template_name=tmpl.name,
                    heading=sec.heading,
                    doc_title=doc.title,
                    context=context,
                    siblings=siblings,
                ),
                max_tokens=settings.LLM_MAX_TOKENS,
            )
        except Exception:
            if not last_attempt:
                raise
            log.exception("template draft for document {} failed at {}", doc.id, sec.key)
            failed.extend(s.key for s in todo[i:])
            return drafted, skipped, failed, "generation_failed"
        if text is None:  # the daily allowance ran out (checked before each call)
            failed.extend(s.key for s in todo[i:])
            return drafted, skipped, failed, "over_budget"
        if text.strip():
            drafted[sec.key] = text.strip()
        else:
            failed.append(sec.key)
            reason = reason or "empty_draft"
    return drafted, skipped, failed, reason


def handle_template_draft(db: Session, job: Job) -> None:
    """Fill the still-empty AI-draftable sections of a templated document. No commit.

    Never overwrites text: a section the founder typed into while the job waited is `skipped`,
    both before the model is called (saves tokens) and again under the row lock when writing.
    Outcome is always in `job.result` (`ready | partial | failed | over_budget | gone`) so the
    FE can show it. A provider error re-raises until the last attempt (the runner retries with
    backoff); on the last attempt whatever was drafted is saved and the rest is reported
    `failed`. The daily token budget is checked before every call and every call is debited.
    """
    payload = job.payload or {}
    keys: list[str] = list(payload.get("section_keys") or [])
    gone = _result("gone", drafted=[], skipped=[], failed=[])
    try:
        doc = db.get(Document, uuid.UUID(str(payload.get("document_id"))))
    except ValueError:
        doc = None
    tmpl = DOCUMENT_TEMPLATES.get(doc.template_key or "") if doc else None
    startup = db.get(Startup, doc.startup_id) if doc else None
    if doc is None or tmpl is None or startup is None or doc.trashed_at is not None:
        job.result = gone
        return
    sections = {s.key: s for s in tmpl.sections if s.ai and s.key in keys}
    if over_budget(db, startup.id):
        job.result = _result("over_budget", drafted=[], skipped=[], failed=[], reason="over_budget")
        return
    last_attempt = (job.attempts or 0) >= settings.WORKER_MAX_ATTEMPTS
    drafted, skipped, failed, reason = _draft_all(
        db, doc, startup, tmpl, list(sections.values()), last_attempt=last_attempt
    )
    written: list[str] = []
    version = None
    if drafted:
        db.refresh(doc, with_for_update=True)  # pick up edits made while the model was writing
        if doc.trashed_at is not None:
            job.result = gone
            return
        written, late_skips, version = _write_drafts(db, doc, sections, drafted)
        skipped += late_skips
    if failed:
        status = "partial" if written else ("over_budget" if reason == "over_budget" else "failed")
    else:
        status = "ready"
    job.result = _result(
        status, drafted=written, skipped=skipped, failed=failed, reason=reason, version=version
    )


register_handler(AI_DRAFT_JOB, handle_template_draft)
