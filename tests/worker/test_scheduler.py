from app.db.models.scheduled_run import ScheduledRun
from app.worker.scheduler import _claim


def test_claim_is_once_per_period(db):
    assert _claim(db, "mission.generate", "ws1", "2026-09-18") is True
    # same triple → already claimed
    assert _claim(db, "mission.generate", "ws1", "2026-09-18") is False
    # different period → claimable
    assert _claim(db, "mission.generate", "ws1", "2026-09-19") is True
    assert db.query(ScheduledRun).filter_by(task_key="mission.generate", scope_key="ws1").count() == 2
