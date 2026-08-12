from app.main import app


def test_limiter_registered():
    assert getattr(app.state, "limiter", None) is not None
