import datetime as dt

from sqlalchemy.orm import Session

from app.services.documents.files import purge_expired_files, purge_object
from app.services.documents.service import purge_expired_documents

PURGE_BATCH = 500


def run_purge(db: Session, *, now: dt.datetime) -> dict[str, int]:
    """Permanently delete everything that has been in the trash for the retention period.

    Commits after each unit (documents, then files) so a failure on the second never undoes the
    first, and removes a file's stored object only after the row's deletion is committed (an
    orphaned object is the lesser harm). Intended to be run by a self-managed worker job."""
    documents = purge_expired_documents(db, now=now, limit=PURGE_BATCH)
    db.commit()
    targets = purge_expired_files(db, now=now, limit=PURGE_BATCH)
    db.commit()
    for target in targets:
        purge_object(target)
    return {"documents": documents, "files": len(targets)}
