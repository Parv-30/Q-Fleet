"""Tests for gateway's FastAPI proxy/aggregator layer (app/main.py).

Mirrors prediction-service's tests/test_main.py convention: monkeypatch the
internal helper functions (_check_service_health, _proxy_post) rather than
requiring live downstream services, since the gateway itself has no real
logic beyond forwarding HTTP calls.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_module

client = TestClient(app)


def test_health_reports_all_services_ok(monkeypatch):
    async def fake_check(url):
        return "ok"

    monkeypatch.setattr(main_module, "_check_service_health", fake_check)

    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["services"] == {
        "data": "ok",
        "prediction": "ok",
        "emissions": "ok",
        "optimization": "ok",
    }


def test_health_reports_unreachable_service_without_crashing(monkeypatch):
    async def fake_check(url):
        if "8003" in url or "optimization" in url:
            return "unreachable"
        return "ok"

    monkeypatch.setattr(main_module, "_check_service_health", fake_check)

    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["services"]["optimization"] == "unreachable"
    assert body["services"]["data"] == "ok"


def test_check_service_health_returns_unreachable_on_connection_error(monkeypatch):
    """Exercise the real _check_service_health (not monkeypatched) against a
    mocked httpx.AsyncClient to confirm it swallows transport errors rather
    than raising."""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, timeout):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeClient())

    import asyncio

    result = asyncio.run(main_module._check_service_health("http://localhost:9999/health"))
    assert result == "unreachable"


@pytest.mark.parametrize(
    "endpoint,target_key",
    [
        ("/api/optimize", "optimize"),
        ("/api/features", "features"),
        ("/api/predict", "predict"),
        ("/api/emissions", "emissions"),
    ],
)
def test_proxy_forwards_body_and_returns_downstream_response_unchanged(monkeypatch, endpoint, target_key):
    captured = {}

    async def fake_proxy_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        return Response(content=b'{"result": "ok"}', status_code=200, media_type="application/json")

    monkeypatch.setattr(main_module, "_proxy_post", fake_proxy_post)

    payload = {"some": "field", "n": 1}
    response = client.post(endpoint, json=payload)

    assert response.status_code == 200
    assert response.json() == {"result": "ok"}
    assert captured["url"] == main_module._PROXY_TARGETS[target_key]
    import json as _json

    assert _json.loads(captured["body"]) == payload


@pytest.mark.parametrize(
    "endpoint",
    ["/api/optimize", "/api/features", "/api/predict", "/api/emissions"],
)
def test_proxy_downstream_failure_returns_502(monkeypatch, endpoint):
    async def fake_proxy_post(url, body, headers):
        raise HTTPException(status_code=502, detail=f"upstream request to {url} failed: connection error")

    monkeypatch.setattr(main_module, "_proxy_post", fake_proxy_post)

    response = client.post(endpoint, json={"a": 1})
    assert response.status_code == 502


def test_proxy_post_raises_502_on_real_connection_error(monkeypatch):
    """Exercise the real _proxy_post (not monkeypatched) against a mocked
    httpx.AsyncClient to confirm connection failures become HTTPException(502)
    rather than an unhandled exception."""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, content, headers, timeout):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda: FakeClient())

    import asyncio

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module._proxy_post("http://localhost:9999/optimize", b"{}", {}))

    assert exc_info.value.status_code == 502


def test_api_ports_proxies_to_data_service_with_no_params(monkeypatch):
    captured = {}

    async def fake_proxy_get(url, params):
        captured["url"] = url
        captured["params"] = params
        return Response(content=b'[{"name": "Mumbai"}]', status_code=200, media_type="application/json")

    monkeypatch.setattr(main_module, "_proxy_get", fake_proxy_get)

    response = client.get("/api/ports")

    assert response.status_code == 200
    assert response.json() == [{"name": "Mumbai"}]
    assert captured["url"] == main_module._PROXY_TARGETS["ports"]
    assert captured["params"] == {}


def test_api_routes_proxies_origin_and_destination_query_params(monkeypatch):
    captured = {}

    async def fake_proxy_get(url, params):
        captured["url"] = url
        captured["params"] = params
        return Response(
            content=b'[{"origin": "Mumbai", "destination": "Amsterdam", "distance_km": 11995.8, "via": "Suez Canal"}]',
            status_code=200,
            media_type="application/json",
        )

    monkeypatch.setattr(main_module, "_proxy_get", fake_proxy_get)

    response = client.get("/api/routes", params={"origin": "Mumbai", "destination": "Amsterdam"})

    assert response.status_code == 200
    assert response.json() == [
        {"origin": "Mumbai", "destination": "Amsterdam", "distance_km": 11995.8, "via": "Suez Canal"}
    ]
    assert captured["url"] == main_module._PROXY_TARGETS["routes"]
    assert captured["params"] == {"origin": "Mumbai", "destination": "Amsterdam"}


def test_cors_headers_present_on_response(monkeypatch):
    async def fake_check(url):
        return "ok"

    monkeypatch.setattr(main_module, "_check_service_health", fake_check)

    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_allows_configured_dev_origins():
    response = client.options(
        "/api/optimize",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
