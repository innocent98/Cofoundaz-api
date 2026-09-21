def test_register_wires_email_handler():
    from app.worker import runner
    from app.worker.__main__ import register

    runner.JOB_HANDLERS.clear()
    register()
    assert "email.notification" in runner.JOB_HANDLERS


def test_main_loop_runs_until_stop(db, monkeypatch):
    import app.worker.__main__ as entry

    calls = {"n": 0}
    monkeypatch.setattr(entry, "run_once", lambda d: calls.__setitem__("n", calls["n"] + 1) or 0)
    monkeypatch.setattr(entry, "SessionLocal", lambda: db)
    monkeypatch.setattr(entry.time, "sleep", lambda _s: None)
    stops = iter([False, False, True])
    entry.main_loop(stop=lambda: next(stops))
    assert calls["n"] == 2


def test_register_wires_scheduled_handlers():
    from app.worker import runner
    from app.worker.__main__ import register

    runner.JOB_HANDLERS.clear()
    register()
    assert "scheduled.mission.generate" in runner.JOB_HANDLERS


def test_main_loop_throttles_scheduler(db, monkeypatch):
    import app.worker.__main__ as entry

    ticks = {"n": 0}
    monkeypatch.setattr(entry, "run_once", lambda d: 0)
    monkeypatch.setattr(
        entry, "scheduler_tick", lambda d, now: ticks.__setitem__("n", ticks["n"] + 1)
    )
    monkeypatch.setattr(entry, "SessionLocal", lambda: db)
    monkeypatch.setattr(entry.time, "sleep", lambda _s: None)
    # SCHEDULER_INTERVAL default 60s; three quick iterations should tick the scheduler exactly once
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCHEDULER_INTERVAL", 60)
    stops = iter([False, False, False, True])
    entry.main_loop(stop=lambda: next(stops))
    assert ticks["n"] == 1
