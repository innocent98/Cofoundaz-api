"""CSV lead import: preview into Redis (30 min), then commit with a column mapping.

Error messages here are fixed strings plus a row number. CSV cell text is untrusted and is
never echoed back (it would land in responses and activity rows).
"""

import csv
import io
import json
import uuid
from typing import Any

import redis as redis_lib
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import LeadSource, LeadStatus
from app.db.models.sales import SalesContact, SalesLead
from app.platform.activity import write_activity
from app.schemas.sales import ImportMapping, ImportResult, PreviewOut
from app.services.sales.errors import _validation

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
TTL_SECONDS = 1800
MAX_INVALID_LISTED = 50
_SAMPLE_ROWS = 5
_KEY = "sales:csv:{}"

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "name": ("name", "full name"),
    "company": ("company", "organisation", "organization"),
    "email": ("email", "e-mail"),
    "source": ("source",),
}


def _unreadable() -> AppError:
    return AppError("VALIDATION_ERROR", "We could not read that CSV file.", 422)


def _parse(raw: bytes) -> tuple[list[str], list[list[str]]]:
    if len(raw) > MAX_BYTES:
        raise AppError("PAYLOAD_TOO_LARGE", "CSV must be 2 MB or smaller.", 413)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _unreadable() from exc
    if "\x00" in text:  # Postgres text cannot hold NUL
        raise _unreadable()
    headers: list[str] | None = None
    rows: list[list[str]] = []
    try:
        for rec in csv.reader(io.StringIO(text, newline="")):
            if not any(c.strip() for c in rec):
                continue
            if headers is None:
                headers = [c.strip() for c in rec]
                continue
            rows.append(rec)
            if len(rows) > MAX_ROWS:
                raise AppError("VALIDATION_ERROR", "CSV can have at most 5,000 rows.", 422)
    except csv.Error as exc:
        raise _unreadable() from exc
    if headers is None or not any(headers):
        raise _unreadable()
    return headers, rows


def _suggest(headers: list[str]) -> dict[str, str | None]:
    lowered = {h.lower(): h for h in reversed(headers)}  # first duplicate wins
    return {
        field: next((lowered[s] for s in names if s in lowered), None)
        for field, names in _SYNONYMS.items()
    }


def preview_csv(
    r: redis_lib.Redis, startup_id: uuid.UUID, user_id: uuid.UUID, raw: bytes
) -> PreviewOut:
    headers, rows = _parse(raw)
    upload_id = uuid.uuid4().hex
    payload = {
        "startup_id": str(startup_id),
        "user_id": str(user_id),
        "headers": headers,
        "rows": rows,
    }
    r.set(_KEY.format(upload_id), json.dumps(payload), ex=TTL_SECONDS)
    return PreviewOut(
        upload_id=upload_id,
        headers=headers,
        sample_rows=rows[:_SAMPLE_ROWS],
        suggested_mapping=_suggest(headers),
        row_count=len(rows),
    )


def _load(r: redis_lib.Redis, startup_id: uuid.UUID, upload_id: str) -> dict[str, Any]:
    raw = r.get(_KEY.format(upload_id))
    if raw is None:
        raise NotFound()
    data: dict[str, Any] = json.loads(str(raw))
    if data.get("startup_id") != str(startup_id):
        raise NotFound()
    return data


def _col(headers: list[str], name: str | None, field: str) -> int | None:
    if name is None:
        return None
    if name not in headers:
        raise _validation(f"mapping.{field}", "That column is not in the file.")
    return headers.index(name)


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _norm_email(value: str) -> str | None:
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError:
        return None


def commit_csv(
    db: Session,
    r: redis_lib.Redis,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    upload_id: str,
    mapping: ImportMapping,
) -> ImportResult:
    data = _load(r, startup_id, upload_id)
    headers: list[str] = data["headers"]
    if not (mapping.name or mapping.company):
        raise _validation("mapping.name", "Map a name or a company column.")
    i_name = _col(headers, mapping.name, "name")
    i_company = _col(headers, mapping.company, "company")
    i_email = _col(headers, mapping.email, "email")
    _col(headers, mapping.source, "source")
    # claim the upload atomically so two concurrent commits cannot both import it
    if r.getdel(_KEY.format(upload_id)) is None:
        raise NotFound()

    seen: set[str] = set(
        db.scalars(
            select(func.lower(SalesLead.email)).where(
                SalesLead.startup_id == startup_id, SalesLead.email.is_not(None)
            )
        )
    )
    seen.update(
        db.scalars(
            select(func.lower(SalesContact.email)).where(
                SalesContact.startup_id == startup_id, SalesContact.email.is_not(None)
            )
        )
    )

    added = 0
    duplicates = 0
    invalid: list[dict[str, Any]] = []
    invalid_total = 0
    new_rows: list[SalesLead] = []
    for n, rec in enumerate(data["rows"], start=1):
        name, company, email_raw = _cell(rec, i_name), _cell(rec, i_company), _cell(rec, i_email)
        error: str | None = None
        email: str | None = None
        if not (name or company):
            error = "A name or company is required."
        elif len(name) > 200 or len(company) > 200:
            error = "A value is too long."
        elif email_raw:
            email = _norm_email(email_raw)
            if email is None or len(email) > 320:
                error = "Email is not valid."
        if error is not None:
            invalid_total += 1
            if len(invalid) < MAX_INVALID_LISTED:
                invalid.append({"row": n, "error": error})
            continue
        if email is not None:
            key = email.lower()
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
        new_rows.append(
            SalesLead(
                startup_id=startup_id,
                name=name or None,
                company=company or None,
                email=email,
                source=LeadSource.csv,
                status=LeadStatus.new,
            )
        )
        added += 1
    db.add_all(new_rows)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.lead.imported",
        summary=f"Imported {added} leads from CSV",
        actor_user_id=user_id,
        meta={
            "added": added,
            "duplicates_skipped": duplicates,
            "invalid_total": invalid_total,
        },
    )
    return ImportResult(
        added=added,
        duplicates_skipped=duplicates,
        invalid_rows=invalid,
        invalid_total=invalid_total,
    )
