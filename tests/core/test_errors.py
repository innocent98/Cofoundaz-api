from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import AppError, EmailTaken, register_exception_handlers


def _app():
    app = FastAPI()
    register_exception_handlers(app)
    r = APIRouter()

    class Body(BaseModel):
        n: int

    @r.get("/boom")
    def boom():
        raise EmailTaken()

    @r.get("/generic")
    def generic():
        raise AppError("CUSTOM", "nope", http_status=418)

    @r.post("/validate")
    def validate(body: Body):
        return {"ok": body.n}

    app.include_router(r)
    return app


def test_apperror_renders_envelope():
    c = TestClient(_app())
    resp = c.get("/boom")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_TAKEN"


def test_generic_apperror_status():
    resp = TestClient(_app()).get("/generic")
    assert resp.status_code == 418
    assert resp.json()["error"]["code"] == "CUSTOM"


def test_validation_error_remapped_to_field_errors():
    resp = TestClient(_app()).post("/validate", json={"n": "notint"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["field_errors"][0]["field"] == "n"
