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
