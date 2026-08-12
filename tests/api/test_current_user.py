import uuid
from datetime import timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.models.user import User
from app.db.session import get_db
from tests.factories import create_user


def _mini_app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):  # noqa: B008
        return {"email": user.email}

    return app


def test_valid_token_resolves_user(db):
    user = create_user(db, email="who@ami.com")
    db.commit()
    app = _mini_app()
    app.dependency_overrides[get_db] = lambda: db
    token = create_access_token(str(user.id), expires_delta=timedelta(minutes=5))
    resp = TestClient(app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "who@ami.com"


def test_missing_user_401(db):
    app = _mini_app()
    app.dependency_overrides[get_db] = lambda: db
    token = create_access_token(str(uuid.uuid4()))
    resp = TestClient(app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
