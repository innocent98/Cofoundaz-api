import pytest

from app.services.health_score.config import DIMENSION_WEIGHTS, RECOMMENDATION_CATALOG
from app.services.health_score.scoring import band_for, weighted_overall


def test_weights_sum_to_one():
    assert round(sum(DIMENSION_WEIGHTS.values()), 6) == 1.0
    assert set(DIMENSION_WEIGHTS) == {"product", "market", "money", "legal", "team"}


def test_weighted_overall_equal_weights():
    assert weighted_overall({"product": 80, "market": 60, "money": 40, "legal": 100, "team": 20}) == 60


def test_weighted_overall_clamps_and_rounds():
    assert weighted_overall({"product": 71, "market": 71, "money": 71, "legal": 71, "team": 72}) == 71


@pytest.mark.parametrize("score,band", [
    (0, "at_risk"), (39, "at_risk"), (40, "needs_work"), (59, "needs_work"),
    (60, "healthy"), (79, "healthy"), (80, "thriving"), (100, "thriving"),
])
def test_band_boundaries(score, band):
    assert band_for(score) == band


def test_catalog_covers_all_dimensions():
    assert set(RECOMMENDATION_CATALOG) == {"product", "market", "money", "legal", "team"}
    for entries in RECOMMENDATION_CATALOG.values():
        for e in entries:
            assert {"key", "title", "body", "estimated_lift", "effort", "triggers_below"} <= set(e)
