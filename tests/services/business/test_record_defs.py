import pytest
from pydantic import ValidationError

from app.db.models.enums import PricingModelType, RecordKind, ThreatLevel
from app.services.business.record_defs import RECORD_SCHEMAS, fields


def test_every_kind_has_a_schema():
    assert set(RECORD_SCHEMAS) == set(RecordKind)


def test_persona_requires_name_defaults_the_rest():
    m = RECORD_SCHEMAS[RecordKind.persona](name="Busy Founder")
    assert m.name == "Busy Founder"
    assert m.goals == [] and m.quote == ""
    with pytest.raises(ValidationError):
        RECORD_SCHEMAS[RecordKind.persona]()  # name required


def test_competitor_threat_level_enum():
    m = RECORD_SCHEMAS[RecordKind.competitor](name="Acme", threat_level="high")
    assert m.threat_level == ThreatLevel.high
    with pytest.raises(ValidationError):
        RECORD_SCHEMAS[RecordKind.competitor](name="Acme", threat_level="apocalyptic")


def test_pricing_nested_tiers_validate():
    m = RECORD_SCHEMAS[RecordKind.pricing](
        model_type="tiered",
        tiers=[{"name": "Pro", "price": "$29", "features": ["A", "B"]}],
    )
    assert m.model_type == PricingModelType.tiered
    assert m.tiers[0].name == "Pro" and m.tiers[0].features == ["A", "B"]
    with pytest.raises(ValidationError):
        RECORD_SCHEMAS[RecordKind.pricing](model_type="not-a-model")


def test_fields_descriptor_lists_keys():
    keys = {f["key"] for f in fields(RecordKind.persona)}
    assert {"name", "goals", "quote"} <= keys
