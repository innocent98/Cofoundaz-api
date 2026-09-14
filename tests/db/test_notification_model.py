from app.db.models.notification import Notification
from tests.factories import create_startup, create_user


def test_notification_round_trip(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    n = Notification(
        user_id=u.id,
        startup_id=s.id,
        type="document.shared",
        title="A document was shared with your workspace",
        body="",
        data={"document_id": "x"},
    )
    db.add(n)
    db.flush()
    db.refresh(n)
    assert n.read_at is None and n.type == "document.shared"
    assert n.data == {"document_id": "x"}
