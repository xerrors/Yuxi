import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from yuxi.api.main import build_cors_options, parse_cors_origins


def _client_for_origins(origins: list[str]) -> TestClient:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **build_cors_options(origins))

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return TestClient(app)


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,last-event-id",
        },
    )


def test_default_development_cors_origins(monkeypatch):
    monkeypatch.delenv("YUXI_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("YUXI_ENV", "development")

    assert parse_cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_production_does_not_default_to_wildcard(monkeypatch):
    monkeypatch.delenv("YUXI_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("YUXI_ENV", "production")

    assert parse_cors_origins() == []


def test_cors_allows_configured_origin_with_credentials():
    client = _client_for_origins(["http://localhost:5173"])

    response = _preflight(client, "http://localhost:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_unconfigured_origin():
    client = _client_for_origins(["http://localhost:5173"])

    response = _preflight(client, "https://evil.example")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origins", [["http://localhost:5173"], ["*"]])
def test_cors_allows_conditional_get_headers(origins):
    """配置允许的浏览器来源可读取 ETag 并发送条件请求。"""
    origin = "http://localhost:5173"
    client = _client_for_origins(origins)
    response = client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,if-none-match",
        },
    )
    assert response.status_code == 200
    headers = client.get("/ping", headers={"Origin": origin}).headers
    assert "etag" in {name.strip().lower() for name in headers["access-control-expose-headers"].split(",")}


def test_wildcard_cors_disables_credentials():
    client = _client_for_origins(["*"])

    response = _preflight(client, "https://any.example")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
