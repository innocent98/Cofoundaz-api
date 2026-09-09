import uuid
from dataclasses import dataclass
from typing import Any

from app.core.errors import NotFound
from app.db.models.enums import DocumentKind


@dataclass(frozen=True)
class DocumentTemplate:
    key: str
    name: str
    description: str
    kind: DocumentKind
    sections: tuple[str, ...]


def _t(
    key: str, name: str, description: str, kind: DocumentKind, *sections: str
) -> DocumentTemplate:
    return DocumentTemplate(
        key=key, name=name, description=description, kind=kind, sections=sections
    )


DOCUMENT_TEMPLATES: dict[str, DocumentTemplate] = {
    t.key: t
    for t in (
        _t(
            "business_plan",
            "Business Plan",
            "A full business plan.",
            DocumentKind.business_plan,
            "Executive Summary",
            "Problem",
            "Solution",
            "Market & Customer",
            "Business Model",
            "Go-to-Market",
            "Team",
            "Financials",
            "The Ask",
        ),
        _t(
            "pitch_deck",
            "Pitch Deck",
            "An investor pitch narrative.",
            DocumentKind.pitch_deck,
            "Hook",
            "Problem",
            "Solution",
            "Why Now",
            "Market",
            "Product",
            "Team",
            "The Ask",
        ),
        _t(
            "financial_model",
            "Financial Model",
            "A lightweight financial narrative.",
            DocumentKind.financial_model,
            "Assumptions",
            "Revenue",
            "Costs",
            "Runway",
            "Projections",
        ),
        _t(
            "meeting_notes",
            "Meeting Notes",
            "Structured meeting notes.",
            DocumentKind.meeting_notes,
            "Attendees",
            "Agenda",
            "Discussion",
            "Decisions",
            "Action Items",
        ),
        _t(
            "one_pager",
            "One-Pager",
            "A concise one-page overview.",
            DocumentKind.one_pager,
            "Overview",
            "Problem",
            "Solution",
            "Traction",
            "The Ask",
        ),
    )
}


def _get(template_key: str) -> DocumentTemplate:
    tmpl = DOCUMENT_TEMPLATES.get(template_key)
    if tmpl is None:
        raise NotFound()
    return tmpl


def instantiate(template_key: str) -> tuple[DocumentKind, str, list[dict[str, Any]]]:
    tmpl = _get(template_key)
    sections = [{"id": str(uuid.uuid4()), "heading": h, "body": ""} for h in tmpl.sections]
    return tmpl.kind, tmpl.name, sections


def _view(tmpl: DocumentTemplate) -> dict[str, Any]:
    return {
        "key": tmpl.key,
        "name": tmpl.name,
        "description": tmpl.description,
        "kind": tmpl.kind.value,
        "sections": list(tmpl.sections),
    }


def catalog() -> list[dict[str, Any]]:
    return [_view(t) for t in DOCUMENT_TEMPLATES.values()]


def template_view(template_key: str) -> dict[str, Any]:
    return _view(_get(template_key))
