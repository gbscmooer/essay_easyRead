from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware

from backend.main import DEFAULT_RATE_LIMIT, app, register_http_error_handlers


class Payload(BaseModel):
    count: int = Field(ge=1)


def _isolated_app(*, limit: str = "100/minute") -> FastAPI:
    isolated = FastAPI()
    isolated.state.limiter = Limiter(
        key_func=lambda request: "isolated-client",
        default_limits=[limit],
        storage_uri="memory://",
        headers_enabled=True,
    )
    register_http_error_handlers(isolated)
    isolated.add_middleware(SlowAPIMiddleware)

    @isolated.get("/nested")
    async def nested_error():
        raise HTTPException(
            status_code=400,
            detail={"detail": "参数错误", "code": "CUSTOM_BAD_REQUEST"},
        )

    @isolated.get("/plain")
    async def plain_error():
        raise HTTPException(status_code=404, detail="资源不存在")

    @isolated.post("/validated")
    async def validated(payload: Payload):
        return payload

    @isolated.get("/limited")
    async def limited():
        return {"ok": True}

    return isolated


def test_production_app_installs_default_rate_limit_middleware():
    assert any(middleware.cls is SlowAPIMiddleware for middleware in app.user_middleware)
    assert DEFAULT_RATE_LIMIT == "120/minute"
    assert app.state.limiter._headers_enabled is True


def test_http_exception_dict_is_unwrapped_to_top_level_contract():
    with TestClient(_isolated_app()) as client:
        response = client.get("/nested")

    assert response.status_code == 400
    assert response.json() == {"detail": "参数错误", "code": "CUSTOM_BAD_REQUEST"}


def test_http_exception_string_uses_status_code_contract():
    with TestClient(_isolated_app()) as client:
        response = client.get("/plain")

    assert response.status_code == 404
    assert response.json() == {"detail": "资源不存在", "code": "NOT_FOUND"}


def test_request_validation_error_uses_top_level_contract():
    with TestClient(_isolated_app()) as client:
        response = client.post("/validated", json={"count": 0})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["detail"].startswith("body.count:")


def test_rate_limit_returns_top_level_contract_without_global_state():
    with TestClient(_isolated_app(limit="2/minute")) as client:
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
        response = client.get("/limited")

    assert response.status_code == 429
    assert response.json() == {
        "detail": "请求过于频繁，请稍后重试",
        "code": "RATE_LIMITED",
    }
    assert response.headers["retry-after"]
