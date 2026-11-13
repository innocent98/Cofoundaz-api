"""Term sheets: typed or extracted, confirmed by the founder, flagged by rules, compared.

Founder only (the routes enforce it). Every write takes the workspace lock and writes one
`term_sheet_audit` row with before and after. Extracted values are proposals until confirmed;
nothing unconfirmed is compared, flagged or announced.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import AiBudgetExhausted, AppError, FileNotClean, NotFound
from app.db.models.document import DocumentFile
from app.db.models.enums import JobStatus, ScanStatus
from app.db.models.funding import Investor
from app.db.models.job import Job
from app.db.models.term_sheet import TermSheet, TermSheetAudit
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.platform.llm_budget import over_budget
from app.platform.storage import get_storage
from app.schemas.term_sheet import (
    Terms,
    TermSheetConfirm,
    TermSheetCreate,
    TermSheetExtract,
    TermSheetUpdate,
)
from app.services.funding import rounds as rounds_svc
from app.services.funding import term_compare, term_rules
from app.services.funding.errors import _validation
from app.services.funding.locks import lock_workspace

EXTRACT_JOB = "ai.funding.term_sheet_extract"  # "ai." so the daily token budget gate applies
WORDING_JOB = "ai.funding.term_sheet_wording"
ADDED_EVENT = "funding.term_sheet.added"
MAX_SHEETS = 200
MAX_FAILED = 50  # failed extractions kept per workspace; the founder deletes them to retry more
MAX_PDF_BYTES = 15 * 1024 * 1024


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def currency(db: Session, startup_id: uuid.UUID) -> str:
    rnd = rounds_svc.latest_round(db, startup_id)
    return rnd.currency if rnd else "NGN"


def has_money(terms: Terms) -> bool:
    return terms.pre_money_minor is not None or terms.investment_minor is not None


def proposed_currency_mismatch(db: Session, sheet: TermSheet) -> dict[str, str] | None:
    """The currency the document states, when it differs from the round's. None when it states
    none, or the same one (an unstated currency is not a mismatch we can know about)."""
    doc = (sheet.proposed or {}).get("currency")
    rnd = rounds_svc.latest_round(db, sheet.startup_id)
    if rnd is None:  # no round yet: there is no currency to disagree with
        return None
    round_currency = rnd.currency
    if doc and doc != round_currency:
        return {"document": doc, "round": round_currency}
    return None


def terms_dict(terms: Terms) -> dict[str, Any]:
    """What is stored: only the stated terms (null and absent both mean "not stated")."""
    return terms.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------- serialisation and audit


def _investor_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {r.id: r.name for r in db.query(Investor.id, Investor.name).filter(Investor.id.in_(ids))}


def serialize(db: Session, rows: list[TermSheet], *, detail: bool = True) -> list[dict[str, Any]]:
    names = _investor_names(db, {r.investor_id for r in rows if r.investor_id})
    files = {
        f.id: f.filename
        for f in db.query(DocumentFile.id, DocumentFile.filename).filter(
            DocumentFile.id.in_({r.source_file_id for r in rows if r.source_file_id})
        )
    }
    out = []
    for r in rows:
        item = {
            "id": str(r.id),
            "label": r.label,
            "status": r.status,
            "error": r.error,
            "investor_id": str(r.investor_id) if r.investor_id else None,
            "investor_name": names.get(r.investor_id) if r.investor_id else None,
            "source_file_id": str(r.source_file_id) if r.source_file_id else None,
            "source_filename": files.get(r.source_file_id) if r.source_file_id else None,
            "job_id": str(r.job_id) if r.job_id else None,
            "extracted_by_ai": r.extracted_by_ai,
            "terms": r.terms,
            "flags": r.flags,
            "flag_count": len(r.flags),
            "confirmed_at": _iso(r.confirmed_at),
            "created_at": r.created_at.isoformat(),
        }
        if detail:
            item["proposed"] = r.proposed if r.status == "needs_confirmation" else None
            mismatch = (
                proposed_currency_mismatch(db, r) if r.status == "needs_confirmation" else None
            )
            item["currency_mismatch"] = mismatch is not None
            item["document_currency"] = mismatch["document"] if mismatch else None
            item["round_currency"] = currency(db, r.startup_id) if mismatch else None
        out.append(item)
    return out


def snapshot(r: TermSheet) -> dict[str, Any]:
    return {
        "label": r.label,
        "status": r.status,
        "investor_id": str(r.investor_id) if r.investor_id else None,
        "terms": r.terms,
        "flags": [f["rule"] for f in r.flags],
        "extracted_by_ai": r.extracted_by_ai,
    }


def audit_system(db: Session, sheet: TermSheet, before: dict[str, Any]) -> None:
    """Record a state change the worker made (extraction finished or failed): no actor."""
    after = snapshot(sheet)
    if after != before:
        _audit(db, sheet.startup_id, None, sheet.id, "update", before, after)


def _audit(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    sheet_id: uuid.UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    db.add(
        TermSheetAudit(
            created_at=datetime.now(UTC),
            startup_id=startup_id,
            term_sheet_id=sheet_id,
            actor_id=actor_id,
            action=action,
            before=before,
            after=after,
        )
    )


def audit_log(db: Session, startup_id: uuid.UUID, *, limit: int, offset: int) -> dict[str, Any]:
    q = db.query(TermSheetAudit).filter_by(startup_id=startup_id)
    rows = (
        q.order_by(TermSheetAudit.created_at.desc(), TermSheetAudit.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {
        "total": q.count(),
        "items": [
            {
                "id": str(r.id),
                "at": r.created_at.isoformat(),
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "term_sheet_id": str(r.term_sheet_id),
                "action": r.action,
                "before": r.before,
                "after": r.after,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------- reads


def get(
    db: Session, startup_id: uuid.UUID, sheet_id: uuid.UUID, *, lock: bool = False
) -> TermSheet:
    q = db.query(TermSheet).filter_by(id=sheet_id, startup_id=startup_id)
    row = (q.with_for_update() if lock else q).first()
    if row is None:
        raise NotFound()
    return row


def reconcile(db: Session, sheet: TermSheet) -> bool:
    """An `extracting` sheet whose job is gone or finished will never complete (the worker died, or
    the last attempt failed and its writes rolled back): mark it failed so it neither says
    "extracting" forever nor blocks a retry of the same file. Conditional on the status we saw, so
    a sheet that finished in the meantime is left alone."""
    if sheet.status != "extracting":
        return False
    job = db.get(Job, sheet.job_id) if sheet.job_id else None
    if job is not None and job.status in (JobStatus.queued, JobStatus.running):
        return False
    before = snapshot(sheet)
    changed = cast(
        CursorResult,
        db.execute(
            update(TermSheet)
            .where(TermSheet.id == sheet.id, TermSheet.status == "extracting")
            .values(status="failed", error="job_failed")
        ),
    ).rowcount
    db.refresh(sheet)
    if changed:
        audit_system(db, sheet, before)
    return bool(changed)


def list_sheets(
    db: Session, startup_id: uuid.UUID, *, investor_id: uuid.UUID | None
) -> list[TermSheet]:
    q = db.query(TermSheet).filter_by(startup_id=startup_id)
    if investor_id is not None:
        q = q.filter_by(investor_id=investor_id)
    rows = q.order_by(TermSheet.created_at.desc(), TermSheet.id).all()
    for r in rows:
        reconcile(db, r)
    return rows


def compare_sheets(db: Session, startup_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[str, Any]:
    unique = list(dict.fromkeys(ids))
    if not term_compare.MIN_SHEETS <= len(unique) <= term_compare.MAX_SHEETS:
        raise _validation(
            "ids",
            f"Pick between {term_compare.MIN_SHEETS} and {term_compare.MAX_SHEETS} term sheets.",
        )
    rows = (
        db.query(TermSheet)
        .filter(TermSheet.startup_id == startup_id, TermSheet.id.in_(unique))
        .all()
    )
    if len(rows) != len(unique):
        raise NotFound()
    unconfirmed = [r for r in rows if r.status != "confirmed"]
    if unconfirmed:
        raise AppError(
            "TERM_SHEET_NOT_CONFIRMED",
            "Confirm the values of every term sheet before comparing it.",
            409,
        )
    by_id = {r.id: r for r in rows}
    shaped = serialize(db, [by_id[i] for i in unique], detail=False)
    out = term_compare.compare(shaped)
    out["currency"] = currency(db, startup_id)
    out["disclaimer"] = term_rules.DISCLAIMER
    return out


# ---------------------------------------------------------------- writes


def _check_investor(db: Session, startup_id: uuid.UUID, investor_id: uuid.UUID | None) -> None:
    if investor_id is None:
        return
    if db.query(Investor.id).filter_by(id=investor_id, startup_id=startup_id).first() is None:
        raise _validation("investor_id", "That investor isn't in your pipeline.")


def _check_limit(db: Session, startup_id: uuid.UUID) -> None:
    live = db.query(TermSheet).filter(
        TermSheet.startup_id == startup_id, TermSheet.status != "failed"
    )
    if live.count() >= MAX_SHEETS:
        raise _validation("label", f"You can keep at most {MAX_SHEETS} term sheets.")


def _check_failed_cap(db: Session, startup_id: uuid.UUID) -> None:
    """Only extractions can fail, so only they are capped; typed creation never is."""
    failed = db.query(TermSheet.id).filter(
        TermSheet.startup_id == startup_id, TermSheet.status == "failed"
    )
    if failed.count() >= MAX_FAILED:
        raise _validation(
            "file_id", "Too many failed extractions. Delete some of them, then try again."
        )


def _announce(db: Session, sheet: TermSheet, actor_id: uuid.UUID) -> None:
    event_bus.publish(
        db,
        ADDED_EVENT,
        {
            "startup_id": str(sheet.startup_id),
            "term_sheet_id": str(sheet.id),
            "label": sheet.label,
            "actor_id": str(actor_id),
            "href": "/funding/term-sheets",
        },
    )


def _queue_wording(db: Session, sheet: TermSheet) -> None:
    """Ask the model to reword the flags in plain language. Best effort and optional: the fixed
    text is always there, and an exhausted budget just skips this."""
    if sheet.flags and not over_budget(db, sheet.startup_id):
        job_dispatcher.enqueue(db, WORDING_JOB, {"term_sheet_id": str(sheet.id)}, sheet.startup_id)


def _finish_confirmed(db: Session, sheet: TermSheet, terms: Terms, actor_id: uuid.UUID) -> None:
    sheet.terms = terms_dict(terms)
    sheet.flags = term_rules.evaluate(sheet.terms)
    sheet.status = "confirmed"
    sheet.error = None
    sheet.confirmed_at = datetime.now(UTC)
    sheet.confirmed_by_id = actor_id


def create_typed(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, data: TermSheetCreate
) -> TermSheet:
    lock_workspace(db, startup_id)
    _check_investor(db, startup_id, data.investor_id)
    _check_limit(db, startup_id)
    sheet = TermSheet(
        startup_id=startup_id,
        investor_id=data.investor_id,
        label=data.label,
        status="confirmed",
        extracted_by_ai=False,
        created_by_id=actor_id,
    )
    _finish_confirmed(db, sheet, data.terms, actor_id)
    db.add(sheet)
    db.flush()
    _audit(db, startup_id, actor_id, sheet.id, "create", None, snapshot(sheet))
    _announce(db, sheet, actor_id)
    _queue_wording(db, sheet)
    return sheet


def create_extraction(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, data: TermSheetExtract
) -> tuple[TermSheet, bool]:
    """Queue extraction from a Documents PDF. Returns `(sheet, created)`; the same bytes already
    extracted (or being extracted) return that sheet untouched and spend nothing."""
    # Read and hash the PDF BEFORE taking the workspace lock: storage I/O (up to 15 MB) must not
    # hold every other write of this workspace. Duplicates are re-checked under the lock.
    file = (
        db.query(DocumentFile)
        .filter_by(id=data.file_id, startup_id=startup_id)
        .filter(DocumentFile.trashed_at.is_(None))
        .first()
    )
    if file is None:
        raise NotFound()
    if file.content_type != "application/pdf":
        raise _validation("file_id", "Extraction needs a PDF. Enter the terms by hand otherwise.")
    if file.scan_status != ScanStatus.clean:
        raise FileNotClean()
    if file.size_bytes > MAX_PDF_BYTES:
        raise _validation("file_id", "That PDF is too large.")
    content = get_storage().read_private(file.storage_key)
    if content is None:
        raise NotFound()
    sha = hashlib.sha256(content).hexdigest()
    lock_workspace(db, startup_id)
    _check_investor(db, startup_id, data.investor_id)
    same = (
        db.query(TermSheet)
        .filter(
            TermSheet.startup_id == startup_id,
            TermSheet.content_sha256 == sha,
            TermSheet.status != "failed",
        )
        .order_by(TermSheet.created_at.desc())
        .all()
    )
    for existing in same:
        if not reconcile(db, existing):  # still alive (or done): it is the answer
            return existing, False
    _check_limit(db, startup_id)
    _check_failed_cap(db, startup_id)
    if over_budget(db, startup_id):
        raise AiBudgetExhausted()
    sheet = TermSheet(
        startup_id=startup_id,
        investor_id=data.investor_id,
        label=data.label or file.filename[:160],
        source_file_id=file.id,
        content_sha256=sha,
        status="extracting",
        extracted_by_ai=True,
        created_by_id=actor_id,
    )
    db.add(sheet)
    db.flush()
    job = job_dispatcher.enqueue(db, EXTRACT_JOB, {"term_sheet_id": str(sheet.id)}, startup_id)
    sheet.job_id = job.id
    _audit(db, startup_id, actor_id, sheet.id, "create", None, snapshot(sheet))
    db.flush()
    return sheet, True


def confirm(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID,
    sheet_id: uuid.UUID,
    data: TermSheetConfirm,
) -> TermSheet:
    """The founder's final values for an extracted sheet. Only a sheet awaiting confirmation can
    be confirmed (409 otherwise); the row is locked so a double click confirms once."""
    lock_workspace(db, startup_id)
    sheet = get(db, startup_id, sheet_id, lock=True)
    if sheet.status != "needs_confirmation":
        raise AppError(
            "NOT_AWAITING_CONFIRMATION",
            "This term sheet is not waiting for confirmation.",
            409,
        )
    mismatch = proposed_currency_mismatch(db, sheet)
    if mismatch and has_money(data.terms) and not data.acknowledge_currency_mismatch:
        raise AppError(
            "CURRENCY_MISMATCH",
            f"This document states amounts in {mismatch['document']}, but your round is in "
            f"{mismatch['round']}. Convert the amounts, or confirm that they are already in "
            f"{mismatch['round']}.",
            409,
        )
    before = snapshot(sheet)
    if data.label:
        sheet.label = data.label
    _finish_confirmed(db, sheet, data.terms, actor_id)
    db.flush()
    _audit(db, startup_id, actor_id, sheet.id, "confirm", before, snapshot(sheet))
    _announce(db, sheet, actor_id)
    _queue_wording(db, sheet)
    return sheet


def update_sheet(
    db: Session,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID,
    sheet_id: uuid.UUID,
    data: TermSheetUpdate,
) -> TermSheet:
    lock_workspace(db, startup_id)
    sheet = get(db, startup_id, sheet_id, lock=True)
    if sheet.status != "confirmed":
        raise AppError(
            "NOT_CONFIRMED", "Confirm the extracted values first; then you can edit them.", 409
        )
    given = data.model_fields_set
    before = snapshot(sheet)
    if "investor_id" in given:
        _check_investor(db, startup_id, data.investor_id)
        sheet.investor_id = data.investor_id
    if data.label is not None:
        sheet.label = data.label
    terms_changed = False
    if data.terms is not None and terms_dict(data.terms) != sheet.terms:
        sheet.terms = terms_dict(data.terms)
        sheet.flags = term_rules.evaluate(sheet.terms)
        terms_changed = True
    db.flush()
    after = snapshot(sheet)
    if after != before:  # a PATCH that changes nothing leaves no history
        _audit(db, startup_id, actor_id, sheet.id, "update", before, after)
    if terms_changed:
        _queue_wording(db, sheet)
    return sheet


def delete_sheet(
    db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID, sheet_id: uuid.UUID
) -> None:
    lock_workspace(db, startup_id)
    sheet = get(db, startup_id, sheet_id, lock=True)
    _audit(db, startup_id, actor_id, sheet.id, "delete", snapshot(sheet), None)
    db.delete(sheet)
    db.flush()
