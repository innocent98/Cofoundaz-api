"""D-ASM-4: POST /complete also returns the new Health Score and the per-dimension change."""

import uuid

from app.db.models.assessment import Assessment
from app.db.models.enums import Dimension
from app.db.models.health_score import HealthScore
from tests.api.assessment.helpers import BASE, founder, walk


def _complete(client, h, *, pick):
    aid = client.post(BASE, headers=h).json()["data"]["assessment_id"]
    walk(client, h, aid, pick=pick)
    r = client.post(f"{BASE}/{aid}/complete", headers=h)
    assert r.status_code == 200, r.text
    return aid, r.json()["data"]


def test_first_completion_has_health_with_null_previous(client, db):
    _u, s, h = founder(db)
    db.commit()
    _aid, data = _complete(client, h, pick=0)
    health = data["health"]
    stored = db.query(HealthScore).filter_by(startup_id=s.id).one()
    assert health["score"] == stored.score
    assert health["previous_score"] is None
    assert health["delta"] is None
    assert [d["key"] for d in health["dimensions"]] == [d.value for d in Dimension]
    for d in health["dimensions"]:
        assert d["score"] == stored.dimension_scores[d["key"]]
        assert d["previous"] is None and d["delta"] is None


def test_existing_response_fields_are_untouched(client, db):
    _u, _s, h = founder(db)
    db.commit()
    _aid, data = _complete(client, h, pick=0)
    assert {
        "assessment_id",
        "status",
        "dimension_scores",
        "overall_provisional",
        "narrative",
    } <= set(data)
    assert data["status"] == "completed"


def test_reassessment_reports_previous_and_deltas(client, db):
    _u, s, h = founder(db)
    db.commit()
    _a1, first = _complete(client, h, pick=0)
    _a2, second = _complete(client, h, pick=-1)
    h1, h2 = first["health"], second["health"]
    assert h2["previous_score"] == h1["score"]
    assert h2["delta"] == h2["score"] - h1["score"]
    by_key_1 = {d["key"]: d for d in h1["dimensions"]}
    assert len(h2["dimensions"]) == 5
    for d in h2["dimensions"]:
        assert d["previous"] == by_key_1[d["key"]]["score"]
        assert d["delta"] == d["score"] - d["previous"]
    # the two assessments really did score differently, so the test is not vacuous
    assert any(d["delta"] != 0 for d in h2["dimensions"])
    stored = db.query(HealthScore).filter_by(startup_id=s.id).one()
    assert h2["score"] == stored.score


def test_replayed_complete_does_not_recompute_and_returns_null_health(client, db):
    _u, _s, h = founder(db)
    db.commit()
    aid, first = _complete(client, h, pick=0)
    assert first["health"] is not None
    again = client.post(f"{BASE}/{aid}/complete", headers=h)
    assert again.status_code == 200
    assert again.json()["data"]["health"] is None
    assert db.get(Assessment, uuid.UUID(aid)).status.value == "completed"
