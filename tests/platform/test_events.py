from app.platform.events import LogEventBus


def test_logbus_records_events():
    bus = LogEventBus()
    bus.publish("auth.user.registered", {"user_id": "u1"})
    assert bus.published[-1] == ("auth.user.registered", {"user_id": "u1"})
