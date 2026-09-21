from app.platform.events import DispatchingEventBus


def test_publish_records_and_dispatches(db):
    bus = DispatchingEventBus()
    seen = []
    bus.subscribe("x.happened", lambda d, p: seen.append(p))
    bus.publish(db, "x.happened", {"k": 1})
    assert bus.published[-1] == ("x.happened", {"k": 1})
    assert seen == [{"k": 1}]


def test_unregistered_event_no_handler(db):
    bus = DispatchingEventBus()
    bus.publish(db, "nobody.listening", {"k": 1})  # must not raise
    assert bus.published[-1] == ("nobody.listening", {"k": 1})


def test_failing_handler_is_isolated_and_does_not_raise(db):
    from app.db.models.user import User

    bus = DispatchingEventBus()

    def boom(d, p):
        d.add(User(email=None))  # NOT NULL violation on flush inside the savepoint
        d.flush()

    ok = []
    bus.subscribe("x", boom)
    bus.subscribe("x", lambda d, p: ok.append(1))
    bus.publish(db, "x", {})  # must NOT raise
    assert ok == [1]  # the good handler still ran
    # the outer session is still usable after the savepoint rollback
    assert db.query(User).filter(User.email.is_(None)).count() == 0
