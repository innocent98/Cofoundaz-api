"""Export a Business Builder artifact as a Module 18 Document (D-BB-2).

The Document is a normal editable draft in Documents (versioned, shareable, downloadable there);
the editor's "Export" never produces a client-side file. Each canvas block or record becomes one
section, so the structure the founder built survives the export.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.document import Document
from app.db.models.enums import CanvasType, DocumentKind, RecordKind
from app.db.models.startup import Startup
from app.services.business.canvas_defs import CANVAS_BLOCKS
from app.services.business.records import list_records
from app.services.business.service import get_or_create_canvas
from app.services.documents.service import create_document

CANVAS_TITLES: dict[CanvasType, str] = {
    CanvasType.business_model: "Business Model Canvas",
    CanvasType.lean: "Lean Canvas",
    CanvasType.value_prop: "Value Proposition Canvas",
    CanvasType.mission_vision: "Mission & Vision",
    CanvasType.swot: "SWOT",
}

RECORD_TITLES: dict[RecordKind, str] = {
    RecordKind.persona: "Customer Personas",
    RecordKind.pricing: "Pricing Strategy",
    RecordKind.revenue_stream: "Revenue Model",
    RecordKind.competitor: "Competitive Analysis",
}

_FIELD_LABELS = {
    "est_monthly": "Est. monthly",
    "pricing_basis": "Pricing basis",
    "watering_holes": "Watering holes",
    "threat_level": "Threat level",
    "model_type": "Model",
}
_SKIP_FIELDS = {"name", "map_x", "map_y"}


class NothingToExport(AppError):  # noqa: N818
    code, http_status = "NOTHING_TO_EXPORT", 422
    message = "There's nothing to export yet. Add something first."


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {_text(v)}" for v in value if v not in (None, "", [], {}))
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", []))
    return str(value)


def _record_body(data: dict) -> str:
    lines = []
    for key, value in data.items():
        if key in _SKIP_FIELDS or value in (None, "", [], {}):
            continue
        label = _FIELD_LABELS.get(key, key.replace("_", " ").capitalize())
        rendered = _text(value)
        lines.append(f"{label}:\n{rendered}" if "\n" in rendered else f"{label}: {rendered}")
    return "\n\n".join(lines)


def _title(startup: Startup, artifact: str) -> str:
    return f"{startup.name or 'Business'}: {artifact}"


def export_canvas(
    db: Session, startup: Startup, canvas_type: CanvasType, user_id: uuid.UUID
) -> Document:
    canvas = get_or_create_canvas(db, startup, canvas_type)
    sections = [
        {"heading": b.label, "body": _text(canvas.blocks.get(b.key) or "")}
        for b in CANVAS_BLOCKS[canvas_type]
    ]
    if not any(s["body"] for s in sections):
        raise NothingToExport()
    return create_document(
        db,
        startup,
        created_by_id=user_id,
        kind=DocumentKind.custom,
        title=_title(startup, CANVAS_TITLES[canvas_type]),
        sections=sections,
        folder=None,
        template_key=None,
    )


def export_records(db: Session, startup: Startup, kind: RecordKind, user_id: uuid.UUID) -> Document:
    records = list_records(db, startup, kind)
    if not records:
        raise NothingToExport()
    title = RECORD_TITLES[kind]
    sections = [
        {
            "heading": str(r.data.get("name") or r.data.get("model_type") or f"{title} {i + 1}"),
            "body": _record_body(r.data),
        }
        for i, r in enumerate(records)
    ]
    return create_document(
        db,
        startup,
        created_by_id=user_id,
        kind=DocumentKind.custom,
        title=_title(startup, title),
        sections=sections,
        folder=None,
        template_key=None,
    )
