"""Two recomputes of one workspace at once must write one history row and one notification."""

import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.document import Document
from app.db.models.enums import DocumentKind, FinancialModelStatus, MembershipRole
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.db.models.readiness import ReadinessScoreHistory
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.notifications.registry import register
from app.services.readiness import service
from tests.factories import create_membership, create_startup, create_user


def test_concurrent_recomputes_serialise(engine, monkeypatch):
    register()
    with Session(engine) as setup:
        u = create_user(setup, email_verified_at=datetime.now(UTC))
        s = create_startup(setup, owner=u)
        create_membership(setup, u, s, role=MembershipRole.founder)
        setup.commit()
        sid, uid = s.id, u.id
    try:
        # A first score exists, so the next change counts as an improvement.
        with Session(engine) as first:
            service.recompute_readiness(first, first.get(Startup, sid), trigger="seed")
            first.commit()
        with Session(engine) as work:
            work.add(Document(startup_id=sid, title="Deck", kind=DocumentKind.pitch_deck))
            work.add(Document(startup_id=sid, title="One", kind=DocumentKind.one_pager))
            work.add(Document(startup_id=sid, title="FA", kind=DocumentKind.founder_agreement))
            work.add(
                FinancialModel(
                    startup_id=sid,
                    status=FinancialModelStatus.complete,
                    overrides_rev=0,
                    calculated_rev=0,
                )
            )
            work.commit()

        # Without the lock both threads meet here, having both read the old row. With it the
        # second thread is blocked before it computes, so the first times out and carries on.
        barrier = threading.Barrier(2)
        real_compute = service.compute

        def meeting(db, startup):
            try:
                barrier.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                pass
            return real_compute(db, startup)

        monkeypatch.setattr(service, "compute", meeting)
        errors: list[Exception] = []

        def run() -> None:
            try:
                with Session(engine) as db:
                    service.recompute_readiness(db, db.get(Startup, sid), trigger="race")
                    db.commit()
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors

        with Session(engine) as check:
            rows = check.query(ReadinessScoreHistory).filter_by(startup_id=sid).count()
            told = (
                check.query(Notification).filter_by(user_id=uid, type="readiness.improved").count()
            )
        assert rows == 2  # the seed point and exactly one for the change
        assert told == 1
    finally:
        with Session(engine) as cleanup:
            # Committed rows outlive the test transaction: remove everything this test caused,
            # including the email jobs the notification queued, or other tests see them.
            cleanup.query(Job).filter(Job.startup_id == sid).delete()
            cleanup.query(Job).filter(Job.payload["user_id"].astext == str(uid)).delete(
                synchronize_session=False
            )
            for model, obj_id in ((Startup, sid), (User, uid)):
                obj = cleanup.get(model, obj_id)
                if obj is not None:
                    cleanup.delete(obj)
            cleanup.commit()
