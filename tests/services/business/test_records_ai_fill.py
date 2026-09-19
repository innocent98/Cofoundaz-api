from app.db.models.enums import PricingModelType, RecordKind, ThreatLevel
from app.services.business.ai_fill import build_record_fill_messages
from app.services.business.record_defs import record_json_schema


def test_record_json_schema_is_strict_records_array():
    for kind in RecordKind:
        s = record_json_schema(kind)
        assert s["type"] == "object" and s["required"] == ["records"] and s["additionalProperties"] is False
        arr = s["properties"]["records"]
        assert arr["type"] == "array" and arr["maxItems"] == 3
        item = arr["items"]
        assert item["type"] == "object" and item["additionalProperties"] is False
        assert item["required"] == list(item["properties"])  # strict: all required


def test_record_json_schema_enums():
    comp = record_json_schema(RecordKind.competitor)["properties"]["records"]["items"]["properties"]
    assert comp["threat_level"]["enum"] == [m.value for m in ThreatLevel]
    assert "map_x" not in comp and "map_y" not in comp  # UI coords omitted from ai-fill
    pricing = record_json_schema(RecordKind.pricing)["properties"]["records"]["items"]["properties"]
    assert pricing["model_type"]["enum"] == [m.value for m in PricingModelType]
    assert pricing["tiers"]["items"]["type"] == "object"


def test_build_record_fill_messages_pii_free():
    msgs = build_record_fill_messages(RecordKind.persona, name="Acme", industry="Fintech", stage="validation")
    assert [m.role for m in msgs] == ["system", "user"]
    blob = " ".join(m.content for m in msgs)
    assert "Acme" in blob and "persona" in blob.lower() and "@" not in blob
