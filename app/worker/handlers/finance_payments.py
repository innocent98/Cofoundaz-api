import uuid

from sqlalchemy.orm import Session

from app.db.models.finance_connection import FinanceConnection
from app.db.models.job import Job
from app.services.finance.payments.sync import sync_connection
from app.worker.runner import register_handler
from app.worker.scheduler import SCHED_PAYMENTS_SYNC


def handle_payments_sync(db: Session, job: Job) -> None:
    connection_id = uuid.UUID(str(job.payload["connection_id"]))
    # The payload is only a hint: the connection must still belong to the workspace it was
    # scanned under before anything is imported.
    conn = db.get(FinanceConnection, connection_id)
    if conn is None or str(conn.startup_id) != str(job.payload.get("startup_id")):
        return
    sync_connection(db, connection_id)


register_handler(SCHED_PAYMENTS_SYNC, handle_payments_sync)
