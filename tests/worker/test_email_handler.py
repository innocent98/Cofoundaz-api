from datetime import UTC, datetime

from app.db.models.enums import JobStatus
from app.db.models.job import Job
from app.platform import email as email_mod
from app.services.notifications.service import create_notifications
from app.worker.handlers import email as email_handler
from tests.factories import create_membership, create_startup, create_user


def test_handle_sends_one_email(db, monkeypatch):
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(email_handler, "get_email_sender", lambda: sender)
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    (n,) = create_notifications(
        db,
        user_ids=[u.id],
        startup_id=s.id,
        type="document.shared",
        title="A document was shared",
        body="",
        data={},
    )
    job = Job(
        type="email.notification", payload={"notification_id": str(n.id)}, status=JobStatus.running
    )
    email_handler.handle_email_notification(db, job)
    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg.to == u.email and msg.subject == "A document was shared"
    assert "/documents" in msg.html


def test_render_email_escapes_html(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    (n,) = create_notifications(
        db,
        user_ids=[u.id],
        startup_id=s.id,
        type="document.shared",
        title='<script>x</script> & "q"',
        body="<b>hi</b>",
        data={},
    )
    rendered = email_handler.render_email(n)
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered
    assert "&lt;b&gt;" in rendered
    assert "<script>" not in rendered
    assert "<b>hi</b>" not in rendered


def test_handle_missing_notification_is_noop(db, monkeypatch):
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(email_handler, "get_email_sender", lambda: sender)
    import uuid

    job = Job(
        type="email.notification",
        payload={"notification_id": str(uuid.uuid4())},
        status=JobStatus.running,
    )
    email_handler.handle_email_notification(db, job)
    assert sender.sent == []
