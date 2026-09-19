from dataclasses import dataclass
from typing import Any

from app.db.models.enums import CanvasType


@dataclass(frozen=True)
class BlockDef:
    key: str
    label: str
    kind: str  # "text" | "list"


def _list(*pairs: tuple[str, str]) -> tuple[BlockDef, ...]:
    return tuple(BlockDef(key=k, label=lbl, kind="list") for k, lbl in pairs)


CANVAS_BLOCKS: dict[CanvasType, tuple[BlockDef, ...]] = {
    CanvasType.business_model: _list(
        ("key_partners", "Key Partners"),
        ("key_activities", "Key Activities"),
        ("key_resources", "Key Resources"),
        ("value_propositions", "Value Propositions"),
        ("customer_relationships", "Customer Relationships"),
        ("channels", "Channels"),
        ("customer_segments", "Customer Segments"),
        ("cost_structure", "Cost Structure"),
        ("revenue_streams", "Revenue Streams"),
    ),
    CanvasType.lean: _list(
        ("problem", "Problem"),
        ("solution", "Solution"),
        ("key_metrics", "Key Metrics"),
        ("unique_value_proposition", "Unique Value Proposition"),
        ("unfair_advantage", "Unfair Advantage"),
        ("channels", "Channels"),
        ("customer_segments", "Customer Segments"),
        ("cost_structure", "Cost Structure"),
        ("revenue_streams", "Revenue Streams"),
    ),
    CanvasType.value_prop: _list(
        ("jobs", "Customer Jobs"),
        ("pains", "Pains"),
        ("gains", "Gains"),
        ("products_services", "Products & Services"),
        ("pain_relievers", "Pain Relievers"),
        ("gain_creators", "Gain Creators"),
    ),
    CanvasType.swot: _list(
        ("strengths", "Strengths"),
        ("weaknesses", "Weaknesses"),
        ("opportunities", "Opportunities"),
        ("threats", "Threats"),
    ),
    CanvasType.mission_vision: (
        BlockDef(key="mission", label="Mission", kind="text"),
        BlockDef(key="vision", label="Vision", kind="text"),
    ),
}


def empty_blocks(canvas_type: CanvasType) -> dict[str, Any]:
    return {b.key: ("" if b.kind == "text" else []) for b in CANVAS_BLOCKS[canvas_type]}


def canvas_json_schema(canvas_type: CanvasType) -> dict[str, Any]:
    """A strict JSON Schema for one canvas type: text blocks -> string, list blocks -> array
    of strings; every block required, no extra keys (OpenAI strict json_schema mode)."""
    props: dict[str, Any] = {}
    for b in CANVAS_BLOCKS[canvas_type]:
        props[b.key] = (
            {"type": "array", "items": {"type": "string"}} if b.kind == "list" else {"type": "string"}
        )
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }
