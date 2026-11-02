"""Template generator (gap-list row 13): pre-fill a template from the startup profile and the
Business Builder, report which fields are filled / empty / AI-draftable, create the document and,
on request, queue an AI draft for the sections that are still empty."""

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AiBudgetExhausted, AppError
from app.db.models.document import Document
from app.db.models.enums import JobStatus
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.jobs import job_dispatcher
from app.platform.llm_budget import over_budget
from app.services.business.plan_context import build_plan_context
from app.services.documents.common import _invalid, display_name
from app.services.documents.service import create_document
from app.services.documents.template_defs import (
    DocumentTemplate,
    FieldDef,
    SectionDef,
    get_template,
    render_body,
)

AI_DRAFT_JOB = "ai.documents.template_draft"
MAX_VALUE_CHARS = {"text": 500, "date": 500, "longtext": 5000}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROFILE_KEYS = ("name", "industry", "stage", "description", "country", "website", "business_model")
_SKIP_RECORD_KEYS = {"name", "map_x", "map_y"}


@dataclass
class Context:
    profile: dict[str, Any]
    canvases: dict[str, dict[str, Any]]
    records: dict[str, list[dict[str, Any]]]
    user_name: str | None
    today: str


def load_context(db: Session, startup: Startup, user_id: uuid.UUID | None) -> Context:
    built = build_plan_context(db, startup)
    profile = {k: built["profile"].get(k) for k in _PROFILE_KEYS if k in built["profile"]}
    profile["description"] = startup.description
    profile["country"] = startup.country
    profile["website"] = startup.website
    return Context(
        profile=profile,
        canvases=built["canvases"],
        records=built["records"],
        user_name=display_name(db, user_id),
        today=dt.datetime.now(dt.UTC).date().isoformat(),
    )


def _lines(items: Any) -> str:
    if isinstance(items, str):
        return items.strip()
    if isinstance(items, list):
        return "\n".join(f"- {str(i).strip()}" for i in items if str(i).strip())
    return ""


def _record_line(data: dict[str, Any]) -> str:
    title = str(data.get("name") or data.get("model_type") or "").strip()
    bits = []
    for key, val in data.items():
        if key in _SKIP_RECORD_KEYS or key == "model_type":
            continue
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val if isinstance(v, str | int | float))
        if isinstance(val, str | int | float) and not isinstance(val, bool) and str(val).strip():
            if val == 0:
                continue
            bits.append(f"{key.replace('_', ' ')}: {str(val).strip()}")
    if not title and not bits:
        return ""
    return (
        f"- {title}" + (f" ({'; '.join(bits)})" if bits else "")
        if title
        else f"- {'; '.join(bits)}"
    )


def resolve_spec(spec: str, ctx: Context) -> tuple[str, str] | None:
    """`(text, source)` for an auto-fill spec, or None when the workspace has nothing for it."""
    kind, _, rest = spec.partition(".")
    if spec == "today":
        return ctx.today, "system"
    if spec == "user.full_name":
        return (ctx.user_name, "account") if ctx.user_name else None
    if kind == "profile":
        val = ctx.profile.get(rest)
        return (str(val).strip(), "profile") if val and str(val).strip() else None
    if kind == "canvas":
        canvas, _, block = rest.partition(".")
        text = _lines(ctx.canvases.get(canvas, {}).get(block))
        return (text, "business_builder") if text else None
    if kind == "record":
        text = "\n".join(filter(None, (_record_line(r) for r in ctx.records.get(rest, []))))
        return (text, "business_builder") if text else None
    return None


def clean_values(tmpl: DocumentTemplate, values: dict[str, Any]) -> dict[str, str]:
    """Caller-supplied field values: known keys only, text only, control characters removed."""
    by_key = {f.key: f for f in tmpl.fields}
    out: dict[str, str] = {}
    for key, raw in values.items():
        field = by_key.get(key)
        if field is None:
            raise _invalid(f"values.{key}", "That isn't a field on this template.")
        if not isinstance(raw, str):
            raise _invalid(f"values.{key}", "Expected text.")
        text = _CONTROL.sub("", raw).strip()
        if len(text) > MAX_VALUE_CHARS[field.type]:
            raise _invalid(f"values.{key}", f"At most {MAX_VALUE_CHARS[field.type]} characters.")
        if text:
            out[key] = text
    return out


def _field_state(f: FieldDef, ctx: Context, values: dict[str, str]) -> dict[str, Any]:
    value, source = "", None
    if f.key in values:
        value, source = values[f.key], "you"
    elif f.source and (hit := resolve_spec(f.source, ctx)):
        value, source = hit
    elif f.default:
        value, source = f.default, "template"
    return {
        "key": f.key,
        "label": f.label,
        "type": f.type,
        "required": f.required,
        "value": value,
        "state": "filled" if value else "empty",
        "source": source,
        "ai_draftable": False,
    }


def _section_state(
    tmpl: DocumentTemplate, s: SectionDef, ctx: Context, field_values: dict[str, str]
) -> dict[str, Any]:
    value, source = "", None
    if s.body:
        value, source = render_body(tmpl, s.body, field_values), "template"
    else:
        parts = [hit for spec in s.autofill if (hit := resolve_spec(spec, ctx))]
        if parts:
            value = "\n\n".join(p[0] for p in parts)
            source = (
                "business_builder"
                if any(p[1] == "business_builder" for p in parts)
                else parts[0][1]
            )
    return {
        "key": s.key,
        "heading": s.heading,
        "value": value,
        "state": "filled" if value else "empty",
        "source": source,
        "ai_draftable": s.ai,
    }


def build_form(ctx: Context, tmpl: DocumentTemplate, values: dict[str, str]) -> dict[str, Any]:
    fields = [_field_state(f, ctx, values) for f in tmpl.fields]
    resolved = {f["key"]: f["value"] for f in fields if f["value"]}
    sections = [_section_state(tmpl, s, ctx, resolved) for s in tmpl.sections]
    return {
        "template_key": tmpl.key,
        "fields": fields,
        "sections": sections,
        "unfilled_required": [f["key"] for f in fields if f["required"] and not f["value"]],
        "disclaimer": tmpl.disclaimer,
        "ai_available": any(s.ai for s in tmpl.sections),
    }


def form_for(
    db: Session, startup: Startup, user_id: uuid.UUID | None, template_key: str
) -> dict[str, Any]:
    tmpl = get_template(template_key)
    return build_form(load_context(db, startup, user_id), tmpl, {})


def ai_state(
    status: str, job_id: uuid.UUID | None = None, sections: list[str] | None = None
) -> dict[str, Any]:
    return {"status": status, "job_id": str(job_id) if job_id else None, "sections": sections or []}


def _draftable_keys(tmpl: DocumentTemplate) -> list[str]:
    return [s.key for s in tmpl.sections if s.ai]


def _pick_sections(
    tmpl: DocumentTemplate, requested: list[str] | None, empty_keys: set[str]
) -> list[str]:
    draftable = _draftable_keys(tmpl)
    if requested is not None:
        bad = [k for k in requested if k not in draftable]
        if bad:
            raise _invalid("ai_sections", "AI can't draft: " + ", ".join(bad) + ".")
        return [k for k in draftable if k in requested and k in empty_keys]
    return [k for k in draftable if k in empty_keys]


def _enqueue(db: Session, startup: Startup, doc: Document, keys: list[str]) -> uuid.UUID:
    job = job_dispatcher.enqueue(
        db,
        AI_DRAFT_JOB,
        {"startup_id": str(startup.id), "document_id": str(doc.id), "section_keys": keys},
        startup.id,
    )
    return job.id


def create_from_template(
    db: Session,
    startup: Startup,
    *,
    user_id: uuid.UUID,
    template_key: str,
    values: dict[str, Any],
    title: str | None,
    folder: str | None,
    want_ai: bool,
    ai_sections: list[str] | None,
) -> tuple[Document, dict[str, Any], dict[str, Any]]:
    """Create the document from `template_key`. Returns `(document, form, ai)`.

    Validation (unknown field, AI asked of a template that has nothing to draft) happens before
    anything is written. Over the daily AI budget the document is still created and `ai` says
    `over_budget`: the founder keeps the auto-filled draft and sees why no AI text is coming."""
    tmpl = get_template(template_key)
    clean = clean_values(tmpl, values)
    ctx = load_context(db, startup, user_id)
    form = build_form(ctx, tmpl, clean)
    picked: list[str] = []
    if want_ai:
        if not _draftable_keys(tmpl):
            raise AppError("VALIDATION_ERROR", "AI can't draft sections of this template.", 422)
        empty = {s["key"] for s in form["sections"] if s["state"] == "empty"}
        picked = _pick_sections(tmpl, ai_sections, empty)
    sections = [
        {"id": str(uuid.uuid4()), "heading": s["heading"], "body": s["value"]}
        for s in form["sections"]
    ]
    doc = create_document(
        db,
        startup,
        created_by_id=user_id,
        kind=tmpl.kind,
        title=(title or "").strip() or tmpl.name,
        sections=sections,
        folder=folder,
        template_key=tmpl.key,
    )
    if not want_ai:
        return doc, form, ai_state("not_requested")
    if not picked:
        return doc, form, ai_state("nothing_to_draft")
    if over_budget(db, startup.id):
        return doc, form, ai_state("over_budget")
    return doc, form, ai_state("queued", _enqueue(db, startup, doc, picked), picked)


def queue_redraft(
    db: Session, startup: Startup, doc: Document, requested: list[str] | None
) -> dict[str, Any]:
    """Queue AI for the still-empty draftable sections of an existing templated document (the
    retry after a failed or skipped draft). 422 when there is nothing to draft, 429 over budget."""
    tmpl = get_template(doc.template_key) if doc.template_key else None
    if tmpl is None or not _draftable_keys(tmpl):
        raise AppError(
            "VALIDATION_ERROR", "AI can only draft documents made from a business template.", 422
        )
    bodies = {s.get("heading"): (s.get("body") or "").strip() for s in doc.sections}
    empty = {s.key for s in tmpl.sections if s.heading in bodies and not bodies[s.heading]}
    picked = _pick_sections(tmpl, requested, empty)
    if not picked:
        raise AppError("VALIDATION_ERROR", "There are no empty sections for AI to draft.", 422)
    if over_budget(db, startup.id):
        raise AiBudgetExhausted()
    in_flight = (
        db.query(Job.id)
        .filter(
            Job.type == AI_DRAFT_JOB,
            Job.startup_id == startup.id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.payload["document_id"].astext == str(doc.id),
        )
        .first()
    )
    if in_flight is not None:
        raise AppError(
            "AI_DRAFT_IN_PROGRESS", "An AI draft is already running for this document.", 409
        )
    return ai_state("queued", _enqueue(db, startup, doc, picked), picked)
