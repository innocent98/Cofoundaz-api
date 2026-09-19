import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import PricingModelType, RecordKind, ThreatLevel


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields -> ValidationError


class PersonaData(_Base):
    name: str
    demographics: str = ""
    goals: list[str] = []
    frustrations: list[str] = []
    watering_holes: list[str] = []
    quote: str = ""


class RevenueStreamData(_Base):
    name: str
    pricing_basis: str = ""
    est_monthly: float = 0
    assumptions: str = ""


class CompetitorData(_Base):
    name: str
    positioning: str = ""
    price: str = ""
    strengths: list[str] = []
    weaknesses: list[str] = []
    threat_level: ThreatLevel = ThreatLevel.medium
    map_x: float | None = Field(default=None, ge=0, le=1)
    map_y: float | None = Field(default=None, ge=0, le=1)


class PricingTier(_Base):
    name: str
    price: str = ""
    features: list[str] = []


class PricingData(_Base):
    model_type: PricingModelType
    tiers: list[PricingTier] = []


RECORD_SCHEMAS: dict[RecordKind, type[BaseModel]] = {
    RecordKind.persona: PersonaData,
    RecordKind.revenue_stream: RevenueStreamData,
    RecordKind.competitor: CompetitorData,
    RecordKind.pricing: PricingData,
}


def _record_item_schema(kind: RecordKind) -> dict[str, Any]:
    _str = {"type": "string"}
    _strs = {"type": "array", "items": {"type": "string"}}
    if kind == RecordKind.persona:
        props = {"name": _str, "demographics": _str, "goals": _strs,
                 "frustrations": _strs, "watering_holes": _strs, "quote": _str}
    elif kind == RecordKind.revenue_stream:
        props = {"name": _str, "pricing_basis": _str, "est_monthly": {"type": "number"}, "assumptions": _str}
    elif kind == RecordKind.competitor:
        props = {"name": _str, "positioning": _str, "price": _str, "strengths": _strs, "weaknesses": _strs,
                 "threat_level": {"type": "string", "enum": [m.value for m in ThreatLevel]}}
        # map_x/map_y (UI positioning coords) intentionally omitted — the AI shouldn't set them.
    elif kind == RecordKind.pricing:
        props = {
            "model_type": {"type": "string", "enum": [m.value for m in PricingModelType]},
            "tiers": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": _str, "price": _str, "features": _strs},
                "required": ["name", "price", "features"], "additionalProperties": False,
            }},
        }
    else:  # pragma: no cover - exhaustive over RecordKind
        raise ValueError(kind)
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def record_json_schema(kind: RecordKind) -> dict[str, Any]:
    """Strict OpenAI JSON schema: an object with a `records` array (<=3) of the kind's shape."""
    return {
        "type": "object",
        "properties": {"records": {"type": "array", "maxItems": 3, "items": _record_item_schema(kind)}},
        "required": ["records"],
        "additionalProperties": False,
    }


def fields(kind: RecordKind) -> list[dict[str, Any]]:
    """FE field descriptors for a kind: each dict has `key`, `required`, `type`,
    and `choices` (list of enum member values when the field's annotation is a
    Python `enum.Enum` subclass, else `None`)."""
    out: list[dict[str, Any]] = []
    for key, info in RECORD_SCHEMAS[kind].model_fields.items():
        annotation = info.annotation
        choices: list[Any] | None = None
        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            choices = [member.value for member in annotation]
        out.append(
            {
                "key": key,
                "required": info.is_required(),
                "type": str(annotation),
                "choices": choices,
            }
        )
    return out
