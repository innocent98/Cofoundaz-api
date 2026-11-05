from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError
from app.db.models.business import BusinessRecord
from app.db.models.document import Document
from app.db.models.enums import (
    AssumptionStatus,
    CanvasType,
    DocumentKind,
    FinancialModelStatus,
    InvoiceStatus,
    MembershipRole,
    RecordKind,
    RiskLevel,
    StageKind,
    StartupStage,
)
from app.db.models.financial_model import FinancialModel
from app.db.models.invoice import Invoice
from app.db.models.readiness import ReadinessScore, ReadinessScoreHistory
from app.db.models.validation import Assumption
from app.platform.events import event_bus
from app.services.notifications.registry import register
from app.services.readiness import service
from app.services.readiness.signals import SIGNALS, must_haves, read_signals
from app.services.sales.stages import ensure_stages
from tests.factories import (
    create_business_canvas,
    create_membership,
    create_startup,
    create_user,
)


@pytest.fixture()
def ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    s.stage = StartupStage.idea
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s


def _by_key(res):
    return {sig["key"]: sig for d in res["dimensions"].values() for sig in d["signals"]}


def test_empty_workspace_scores_low_with_every_dimension_present(db, ws):
    _u, s = ws
    res = service.compute(db, s)
    assert 0 <= res["score"] < 40
    assert res["band"] == "not_yet"
    assert not res["capped"]
    assert list(res["dimensions"]) == [
        "team",
        "traction",
        "market",
        "product",
        "financials",
        "story",
    ]
    assert all(not m["met"] for m in res["must_haves"])
    assert res["gaps"] and all(g["href"].startswith("/") for g in res["gaps"])
    # No money data: runway does not apply and is left out, not counted as 0.
    assert "financials.runway" not in _by_key(res)


def test_every_signal_reads_without_error_and_has_a_stage_target():
    assert len({s.key for s in SIGNALS}) == len(SIGNALS)
    for sig in SIGNALS:
        assert set(sig.targets) == set(StartupStage)
        assert sig.href.startswith("/")


def test_artefacts_move_their_signals(db, ws):
    _u, s = ws
    db.add(Document(startup_id=s.id, title="Deck", kind=DocumentKind.pitch_deck))
    db.add(Document(startup_id=s.id, title="One", kind=DocumentKind.one_pager))
    db.add(Document(startup_id=s.id, title="FA", kind=DocumentKind.founder_agreement))
    db.add(
        FinancialModel(
            startup_id=s.id,
            status=FinancialModelStatus.complete,
            overrides_rev=2,
            calculated_rev=2,
        )
    )
    create_business_canvas(
        db,
        startup=s,
        type=CanvasType.lean,
        blocks={"customer_segments": ["SMEs"], "solution": ["An app"]},
    )
    db.add(
        BusinessRecord(startup_id=s.id, kind=RecordKind.competitor, data={"name": "X"}, position=0)
    )
    db.flush()
    sigs = _by_key(service.compute(db, s))
    assert sigs["story.pitch_deck"]["score"] == 100
    assert sigs["story.one_pager"]["score"] == 100
    assert sigs["team.founder_agreement"]["score"] == 50  # drafted, nothing signed
    assert sigs["financials.model"]["score"] == 100
    assert sigs["market.customer_segments"]["score"] == 100
    assert sigs["product.solution"]["score"] == 100
    assert sigs["market.competitors"]["score"] == 50
    assert sigs["story.pitch_deck"]["note"] is None  # no gap note once at target


def test_stale_model_scores_half_and_has_a_note(db, ws):
    _u, s = ws
    db.add(
        FinancialModel(
            startup_id=s.id,
            status=FinancialModelStatus.complete,
            overrides_rev=3,
            calculated_rev=1,
        )
    )
    db.flush()
    sig = _by_key(service.compute(db, s))["financials.model"]
    assert sig["score"] == 50
    assert "Recalculate" in sig["note"]


def test_traction_signals_from_sales_and_invoices(db, ws):
    from app.db.models.sales import Deal

    _u, s = ws
    stages = {st.kind: st for st in ensure_stages(db, s.id)}
    won = next(st for st in ensure_stages(db, s.id) if st.kind == StageKind.won)
    open_stage = next(st for st in stages.values() if st.kind == StageKind.open)
    db.add(Deal(startup_id=s.id, name="A", stage_id=won.id, last_activity_at=datetime.now(UTC)))
    db.add(
        Deal(startup_id=s.id, name="B", stage_id=open_stage.id, last_activity_at=datetime.now(UTC))
    )
    for i, email in enumerate(["a@x.test", "A@x.test", "b@x.test"]):
        db.add(
            Invoice(
                startup_id=s.id,
                number=f"INV-{i}",
                client_name="C",
                client_email=email,
                status=InvoiceStatus.paid,
            )
        )
    db.flush()
    sigs = _by_key(service.compute(db, s))
    assert sigs["traction.customers"]["value"] == 2  # distinct paid clients beat 1 won deal
    assert sigs["traction.pipeline"]["value"] == 1
    assert any(m["key"] == "traction_proof" and m["met"] for m in must_haves(db, s))


def test_validation_proof_point_needs_demand_not_just_one_assumption(db, ws):
    _u, s = ws
    db.add(
        Assumption(
            startup_id=s.id,
            statement="x",
            risk=RiskLevel.high,
            status=AssumptionStatus.validated,
        )
    )
    db.flush()
    proof = next(m for m in must_haves(db, s) if m["key"] == "traction_proof")
    assert proof["met"] is False


def test_market_size_only_counts_with_a_source(db, ws):
    u, s = ws
    service.put_inputs(
        db, s, u.id, {"tam_minor": 100, "sam_minor": 50, "som_minor": 10, "currency": "NGN"}
    )
    assert _by_key(service.compute(db, s))["market.size"]["score"] == 0
    service.put_inputs(db, s, u.id, {"source_url": "https://example.com/report"})
    assert _by_key(service.compute(db, s))["market.size"]["score"] == 100
    assert service.get_inputs(db, s.id)["source_url"] == "https://example.com/report"


def test_inputs_default_when_never_set(db, ws):
    _u, s = ws
    assert service.get_inputs(db, s.id)["tam_minor"] is None


def test_cap_holds_a_perfect_score_at_69(db, ws, monkeypatch):
    _u, s = ws
    from app.services.readiness import service as svc

    monkeypatch.setattr(svc, "raw_overall", lambda _d: 95)
    res = svc.compute(db, s)
    assert res["raw_score"] == 95 and res["score"] == 69 and res["capped"] is True
    assert res["band"] == "getting_close"


def test_recompute_stores_history_only_on_change_and_throttles(db, ws):
    _u, s = ws
    first = service.refresh_readiness(db, s, trigger="a")
    assert first.trigger == "a"
    again = service.refresh_readiness(db, s, trigger="b")
    assert again.trigger == "a"  # inside 15 minutes: no recompute
    service.refresh_readiness(db, s, trigger="c", force=True)  # unchanged inputs
    n = db.query(ReadinessScoreHistory).filter_by(startup_id=s.id).count()
    assert n == 1
    db.add(Document(startup_id=s.id, title="Deck", kind=DocumentKind.pitch_deck))
    db.flush()
    service.refresh_readiness(db, s, trigger="d", force=True)
    assert db.query(ReadinessScoreHistory).filter_by(startup_id=s.id).count() == 2


def test_stale_row_recomputes_on_read(db, ws):
    _u, s = ws
    service.refresh_readiness(db, s, trigger="a")
    row = db.query(ReadinessScore).filter_by(startup_id=s.id).one()
    row.updated_at = datetime.now(UTC) - timedelta(minutes=16)
    db.flush()
    assert service.refresh_readiness(db, s, trigger="b").trigger == "b"


def test_updated_event_and_improved_notification_rules(db, ws):
    u, s = ws
    register()
    seen = []
    event_bus.subscribe("readiness.updated", lambda _db, p: seen.append(p))
    service.refresh_readiness(db, s, trigger="first")
    assert seen and seen[-1]["previous_score"] is None
    # A big jump: deck + one-pager + model + canvas.
    db.add(Document(startup_id=s.id, title="Deck", kind=DocumentKind.pitch_deck))
    db.add(Document(startup_id=s.id, title="One", kind=DocumentKind.one_pager))
    db.add(
        FinancialModel(
            startup_id=s.id,
            status=FinancialModelStatus.complete,
            overrides_rev=0,
            calculated_rev=0,
        )
    )
    db.flush()
    row = service.refresh_readiness(db, s, trigger="jump", force=True)
    assert seen[-1]["previous_score"] is not None and seen[-1]["ready"] is False
    from app.db.models.notification import Notification

    got = db.query(Notification).filter_by(user_id=u.id, type="readiness.improved").all()
    assert len(got) == 1 and "improved to" in got[0].title
    assert row.notified_score == row.score
    # Another rise the same day: no second notification.
    db.add(Document(startup_id=s.id, title="FA", kind=DocumentKind.founder_agreement))
    create_business_canvas(
        db,
        startup=s,
        type=CanvasType.lean,
        blocks={"customer_segments": ["a"], "solution": ["b"]},
    )
    db.flush()
    service.refresh_readiness(db, s, trigger="more", force=True)
    assert db.query(Notification).filter_by(user_id=u.id, type="readiness.improved").count() == 1


def test_small_rise_does_not_notify(db, ws):
    u, s = ws
    register()
    service.refresh_readiness(db, s, trigger="first")
    db.add(Document(startup_id=s.id, title="One", kind=DocumentKind.one_pager))
    db.flush()
    service.refresh_readiness(db, s, trigger="tiny", force=True)
    from app.db.models.notification import Notification

    assert db.query(Notification).filter_by(user_id=u.id, type="readiness.improved").count() == 0


def test_manual_recompute_is_rate_limited(db, ws):
    _u, s = ws
    service.force_recompute(db, s)
    with pytest.raises(AppError) as e:
        service.force_recompute(db, s)
    assert e.value.http_status == 429


def test_history_ranges_markers_and_validation(db, ws):
    _u, s = ws
    now = datetime.now(UTC)
    for days, score, band, capped in [(5, 30, "not_yet", False), (3, 45, "getting_close", True)]:
        db.add(
            ReadinessScoreHistory(
                startup_id=s.id,
                score=score,
                band=band,
                capped=capped,
                dimension_scores={"team": 1},
                trigger="t",
                config_version=1,
                created_at=now - timedelta(days=days),
            )
        )
    db.add(
        ReadinessScoreHistory(
            startup_id=s.id,
            score=72,
            band="ready",
            capped=False,
            dimension_scores={"team": 1},
            trigger="t",
            config_version=1,
            created_at=now - timedelta(days=1),
        )
    )
    db.add(
        ReadinessScoreHistory(
            startup_id=s.id,
            score=10,
            band="not_yet",
            capped=False,
            dimension_scores={"team": 1},
            trigger="t",
            config_version=1,
            created_at=now - timedelta(days=200),
        )
    )
    db.flush()
    out = service.history(db, s, 30)
    assert [p["score"] for p in out["points"]] == [30, 45, 72]
    assert [m["label"] for m in out["markers"]] == [
        "Moved to getting_close",
        "Moved to ready",
        "Cap lifted",
    ]
    assert len(service.history(db, s, 365)["points"]) == 4
    with pytest.raises(AppError):
        service.history(db, s, 7)


def test_snapshot_is_share_safe(db, ws):
    _u, s = ws
    snap = service.snapshot(db, s)
    assert set(snap) == {"score", "band", "ready", "dimensions", "computed_at"}
    assert [d["key"] for d in snap["dimensions"]][0] == "team"
    assert all(set(d) == {"key", "label", "score"} for d in snap["dimensions"])


def test_read_signals_marks_dropped_when_not_applicable(db, ws):
    _u, s = ws
    keys = {r.key for r in read_signals(db, s)}
    assert "financials.runway" not in keys and "team.assessment" not in keys
