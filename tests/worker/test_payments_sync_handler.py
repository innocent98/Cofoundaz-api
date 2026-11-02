import uuid

from app.db.models.job import Job
from app.worker import runner
from app.worker.handlers import finance_payments
from app.worker.scheduler import SCHED_PAYMENTS_SYNC
from tests.services.finance.payments_helpers import make_connection, make_workspace


def test_handler_registered():
    import importlib

    from app.worker.__main__ import register

    register()  # the worker entrypoint imports the handler module
    runner.JOB_HANDLERS.pop(SCHED_PAYMENTS_SYNC, None)
    importlib.reload(finance_payments)  # importing it registers the handler
    assert runner.JOB_HANDLERS[SCHED_PAYMENTS_SYNC] is finance_payments.handle_payments_sync


def test_handler_calls_sync_for_matching_workspace(db, monkeypatch):
    _u, s = make_workspace(db)
    conn = make_connection(db, s)
    seen = []
    monkeypatch.setattr(finance_payments, "sync_connection", lambda d, cid, **k: seen.append(cid))
    job = Job(
        type=SCHED_PAYMENTS_SYNC, payload={"connection_id": str(conn.id), "startup_id": str(s.id)}
    )
    finance_payments.handle_payments_sync(db, job)
    assert seen == [conn.id]


def test_handler_ignores_mismatched_workspace(db, monkeypatch):
    _u, s = make_workspace(db)
    conn = make_connection(db, s)
    seen = []
    monkeypatch.setattr(finance_payments, "sync_connection", lambda d, cid, **k: seen.append(cid))
    job = Job(
        type=SCHED_PAYMENTS_SYNC,
        payload={"connection_id": str(conn.id), "startup_id": str(uuid.uuid4())},
    )
    finance_payments.handle_payments_sync(db, job)
    assert seen == []
