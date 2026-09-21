import pytest

from app.core.errors import NotFound
from app.db.models.enums import DocumentKind
from app.services.documents.template_defs import (
    DOCUMENT_TEMPLATES,
    catalog,
    instantiate,
    template_view,
)


def test_registry_has_the_five_templates():
    assert set(DOCUMENT_TEMPLATES) == {
        "business_plan",
        "pitch_deck",
        "financial_model",
        "meeting_notes",
        "one_pager",
    }


def test_instantiate_seeds_one_section_per_heading_with_ids():
    kind, title, sections = instantiate("business_plan")
    assert kind == DocumentKind.business_plan
    assert title == "Business Plan"
    headings = [s["heading"] for s in sections]
    assert headings[0] == "Executive Summary" and "The Ask" in headings
    assert all(s["body"] == "" and s["id"] for s in sections)
    assert len({s["id"] for s in sections}) == len(sections)  # unique ids


def test_instantiate_unknown_key_404():
    with pytest.raises(NotFound):
        instantiate("nope")


def test_catalog_shape_and_template_view():
    cat = {t["key"]: t for t in catalog()}
    assert cat["pitch_deck"]["name"] == "Pitch Deck"
    assert isinstance(cat["pitch_deck"]["sections"], list)  # headings only
    assert template_view("one_pager")["kind"] == "one_pager"
    with pytest.raises(NotFound):
        template_view("nope")
