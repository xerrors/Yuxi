from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from server.main import _build_cors_options, _parse_cors_origins


def _client_for_origins(origins: list[str]) -> TestClient:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **_build_cors_options(origins))

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

    assert _parse_cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_production_does_not_default_to_wildcard(monkeypatch):
    monkeypatch.delenv("YUXI_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("YUXI_ENV", "production")

    assert _parse_cors_origins() == []


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


def test_wildcard_cors_disables_credentials():
    client = _client_for_origins(["*"])

    response = _preflight(client, "https://any.example")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
