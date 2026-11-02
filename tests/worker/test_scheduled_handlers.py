import uuid
from datetime import UTC, datetime, timedelta

from app.db.models.enums import ComplianceStatus, IpAssetStatus, IpAssetType, RoadmapStatus
from app.db.models.job import (  # JobStatus from app.db.models.enums if not re-exported
    Job,
    JobStatus,
)
from app.db.models.legal import ComplianceItem, IpAsset
from app.db.models.mission import MissionSettings
from app.platform import events as events_mod
from app.worker.handlers import scheduled
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def _job(job_type, payload):
    return Job(type=job_type, payload=payload, status=JobStatus.running)


def test_mission_generate_publishes_when_a_mission_results(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    m = create_milestone(db, phase=p)
    create_task(db, milestone=m, title="Call three customers")
    db.add(MissionSettings(startup_id=s.id, weekend_missions=True))  # date-proof: any weekday
    db.flush()
    scheduled.handle_mission_generate(
        db, _job("scheduled.mission.generate", {"startup_id": str(s.id)})
    )
    ready = [p for e, p in published if e == "mission.ready"]
    assert ready and ready[0]["first_task_title"] == "Call three customers"


def test_mission_generate_does_not_announce_an_empty_mission(db, monkeypatch):
    """A weekends-off rest day (or nothing left to do) produces an empty mission: no ping."""
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    r = create_roadmap(db, startup=s)
    create_milestone(db, phase=create_phase(db, roadmap=r))  # no tasks at all
    scheduled.handle_mission_generate(
        db, _job("scheduled.mission.generate", {"startup_id": str(s.id)})
    )
    assert not any(e == "mission.ready" for e, _ in published)


def test_overdue_publishes_only_if_still_overdue(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    m = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.todo)
    scheduled.handle_roadmap_overdue(
        db, _job("scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(m.id)})
    )
    assert (
        "roadmap.milestone.overdue",
        {"startup_id": str(s.id), "milestone_id": str(m.id)},
    ) in published


def test_overdue_suppresses_publish_when_milestone_already_done(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    m = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.done)
    scheduled.handle_roadmap_overdue(
        db, _job("scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(m.id)})
    )
    assert not any(e == "roadmap.milestone.overdue" for e, _ in published)


def test_overdue_suppresses_publish_when_milestone_missing(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    missing_id = uuid.uuid4()
    scheduled.handle_roadmap_overdue(
        db,
        _job(
            "scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(missing_id)}
        ),
    )
    assert published == []


def test_quarterly_publishes(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    scheduled.handle_assessment_quarterly(
        db, _job("scheduled.assessment.quarterly", {"startup_id": str(s.id)})
    )
    assert (
        "assessment.quarterly.due",
        {"startup_id": str(s.id), "href": "/assessment"},
    ) in published


def test_handlers_registered():
    # The autouse `_fresh_handler_imports` fixture evicts this module from
    # sys.modules before each test, and sibling tests clear JOB_HANDLERS, so
    # re-import here to re-trigger the register_handler(...) import-time
    # side-effect this assertion depends on (independent of collection order).
    import app.worker.handlers.scheduled  # noqa: F401
    from app.worker import runner

    assert "scheduled.mission.generate" in runner.JOB_HANDLERS
    assert "scheduled.roadmap.overdue" in runner.JOB_HANDLERS
    assert "scheduled.assessment.quarterly" in runner.JOB_HANDLERS


# --- legal reminders ---------------------------------------------------------------------------


def _today():
    from datetime import datetime as _dt

    return _dt.now(UTC).date()


def _ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    db.flush()
    return u, s


def _comp(db, s, *, days_out, **kw):
    kw.setdefault("title", "VAT filing")
    due = _today() + timedelta(days=days_out)
    row = ComplianceItem(startup_id=s.id, due_on=due, anchor_day=due.day, **kw)
    db.add(row)
    db.flush()
    return row


def _payload(s, item, period_key):
    return {
        "startup_id": str(s.id),
        "item_id": str(item.id),
        "title": item.title,
        "due_on": item.due_on.isoformat(),
        "period_key": period_key,
    }


def _asset(db, s, *, days_out, status=IpAssetStatus.registered):
    row = IpAsset(
        startup_id=s.id,
        type=IpAssetType.trademark,
        name="Kolo (wordmark)",
        jurisdiction="Nigeria",
        status=status,
        renewal_on=_today() + timedelta(days=days_out),
    )
    db.add(row)
    db.flush()
    return row


def test_compliance_due_publishes_with_a_computed_days_out(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    item = _comp(db, s, days_out=3)
    # Claimed under due14 (windows, spec D17) while actually three days out. `days_out` must be
    # computed from the date, NOT read off the threshold name (spec D22).
    scheduled.handle_legal_compliance_due(
        db, _job("scheduled.legal.compliance.due", _payload(s, item, "due14"))
    )
    assert len(published) == 1
    event, payload = published[0]
    assert event == "legal.compliance.due_soon"
    assert payload["days_out"] == 3
    assert payload["title"] == "VAT filing"
    assert payload["item_id"] == str(item.id)


def test_an_overdue_claim_publishes_the_overdue_event(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    item = _comp(db, s, days_out=-5)
    scheduled.handle_legal_compliance_due(
        db, _job("scheduled.legal.compliance.due", _payload(s, item, "overdue"))
    )
    assert [e for e, _ in published] == ["legal.compliance.overdue"]
    assert published[0][1]["days_out"] == -5


def test_compliance_due_is_silent_when_the_item_is_now_done(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    item = _comp(db, s, days_out=3, status=ComplianceStatus.done)
    scheduled.handle_legal_compliance_due(
        db, _job("scheduled.legal.compliance.due", _payload(s, item, "due7"))
    )
    # The state re-check is the only gate besides the claim (brief gotcha).
    assert published == []


def test_compliance_due_is_silent_when_the_item_is_gone(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    scheduled.handle_legal_compliance_due(
        db,
        _job(
            "scheduled.legal.compliance.due",
            {
                "startup_id": str(s.id),
                "item_id": str(uuid.uuid4()),
                "title": "Deleted filing",
                "due_on": _today().isoformat(),
                "period_key": "due7",
            },
        ),
    )
    assert published == []


def test_ip_renewal_publishes_with_a_computed_days_out(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    asset = _asset(db, s, days_out=6)
    scheduled.handle_legal_ip_renewal_due(
        db,
        _job(
            "scheduled.legal.ip.renewal",
            {
                "startup_id": str(s.id),
                "asset_id": str(asset.id),
                "name": asset.name,
                "renewal_on": asset.renewal_on.isoformat(),
                "period_key": "due14",
            },
        ),
    )
    assert [e for e, _ in published] == ["legal.ip.renewal_due_soon"]
    assert published[0][1]["days_out"] == 6
    assert published[0][1]["name"] == "Kolo (wordmark)"


def test_ip_renewal_is_silent_when_the_asset_went_back_to_drafting(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    asset = _asset(db, s, days_out=6, status=IpAssetStatus.drafting)
    scheduled.handle_legal_ip_renewal_due(
        db,
        _job(
            "scheduled.legal.ip.renewal",
            {
                "startup_id": str(s.id),
                "asset_id": str(asset.id),
                "name": asset.name,
                "renewal_on": asset.renewal_on.isoformat(),
                "period_key": "due7",
            },
        ),
    )
    assert published == []


def test_ip_renewal_is_silent_when_the_renewal_date_was_cleared(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    asset = _asset(db, s, days_out=6)
    renewal = asset.renewal_on.isoformat()
    asset.renewal_on = None
    db.flush()
    scheduled.handle_legal_ip_renewal_due(
        db,
        _job(
            "scheduled.legal.ip.renewal",
            {
                "startup_id": str(s.id),
                "asset_id": str(asset.id),
                "name": asset.name,
                "renewal_on": renewal,
                "period_key": "due7",
            },
        ),
    )
    assert published == []


def test_the_due_phrase_reads_from_days_out():
    from app.services.notifications.registry import _due_phrase

    assert _due_phrase({"days_out": 0}) == "due today"
    assert _due_phrase({"days_out": 1}) == "due tomorrow"
    assert _due_phrase({"days_out": 3}) == "due in 3 days"
    assert _due_phrase({"days_out": 14}) == "due in 14 days"
    # Overdue reads as overdue rather than "due in -5 days".
    assert _due_phrase({"days_out": -5}) == "overdue"
    assert isinstance(_due_phrase({}), str)


def test_legal_handlers_registered():
    # Re-imported for the same reason as `test_handlers_registered` above: the autouse fixture
    # evicts this module, so the register_handler import-time side effect must be re-triggered.
    import app.worker.handlers.scheduled  # noqa: F401
    from app.worker import runner

    assert "scheduled.legal.compliance.due" in runner.JOB_HANDLERS
    assert "scheduled.legal.ip.renewal" in runner.JOB_HANDLERS
