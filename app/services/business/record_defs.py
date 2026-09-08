import enum
from typing import Any

from pydantic import BaseModel, ConfigDict

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
