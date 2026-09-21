from app.platform.llm import StubLLMClient
from app.services.health_score.ai_recommendations import (
    build_health_recommendation_messages,
    catalog_bodies,
    health_recommendation_schema,
)
from app.services.health_score.config import RECOMMENDATION_CATALOG


def test_catalog_bodies_covers_every_key():
    cb = catalog_bodies()
    expected = {e["key"] for entries in RECOMMENDATION_CATALOG.values() for e in entries}
    assert set(cb) == expected
    assert cb["product.define_mvp"] == RECOMMENDATION_CATALOG["product"][0]["body"]


def test_schema_shape_and_key_enum():
    s = health_recommendation_schema(["product.define_mvp", "market.icp_definition"])
    item = s["properties"]["recommendations"]["items"]
    assert item["required"] == ["key", "body"]
    assert item["additionalProperties"] is False
    assert item["properties"]["key"]["enum"] == ["product.define_mvp", "market.icp_definition"]
    assert s["additionalProperties"] is False


def test_schema_is_stub_fillable_with_real_key():
    keys = ["product.define_mvp", "market.icp_definition"]
    out = StubLLMClient().complete_json(
        [], schema=health_recommendation_schema(keys), max_tokens=10
    )
    first = out["recommendations"][0]
    assert first["key"] == "product.define_mvp"  # stub returns enum[0] -> a real key
    assert "[stub-llm]" in first["body"]


def test_builder_is_pii_free_and_lists_recs():
    msgs = build_health_recommendation_messages(
        [("product.define_mvp", "product", "Define your MVP scope")],
        name="Cofoundaz",
        industry="Fintech",
        stage="idea",
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "product.define_mvp" in body and "Define your MVP scope" in body
    assert "Product" in body  # dimension label
    assert "Cofoundaz" in body and "Fintech" in body
