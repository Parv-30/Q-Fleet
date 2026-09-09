"""FastAPI reverse-proxy/aggregator for the Q-Fleet AI dashboard (Phase 5).

The React frontend talks ONLY to this gateway (port 8080) rather than to the
four backend services directly -- this hides individual service hosts/ports
from the frontend and avoids the frontend having to deal with CORS across 4
different origins. Mirrors data-service's/prediction-service's/
emissions-service's/optimization-service's app/main.py conventions (thin
FastAPI layer, env-var-configurable downstream URLs, httpx.AsyncClient for
outbound calls, clear HTTPException on downstream failure rather than
crashing).

Exposes:

    GET  /health         -- gateway liveness + per-service downstream health
    POST /api/optimize   -- proxies to optimization-service's POST /optimize
    POST /api/features   -- proxies to data-service's POST /features
    POST /api/predict    -- proxies to prediction-service's POST /predict
    POST /api/emissions  -- proxies to emissions-service's POST /emissions
    GET  /api/ports      -- proxies to data-service's GET /ports
    GET  /api/routes     -- proxies to data-service's GET /routes?origin=&destination=
    GET  /api/weather    -- proxies to data-service's GET /weather?origin=&destination=

/api/optimize is the primary endpoint: the dashboard's Pareto-front
comparison, scenario trade-off, and deployment-recommendation views are all
driven by its response (list[OptimizationCandidate], see
services/common/schemas.py).
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

# Downstream service base URLs. Docker Compose overrides these to use
# compose service names (e.g. http://data-service:8000) for
# container-to-container networking; local dev outside Docker falls back to
# localhost, mirroring the DATA_SERVICE_URL pattern already established in
# prediction-service/app/main.py.
DATA_SERVICE_URL = os.environ.get("DATA_SERVICE_URL", "http://localhost:8000")
PREDICTION_SERVICE_URL = os.environ.get("PREDICTION_SERVICE_URL", "http://localhost:8001")
EMISSIONS_SERVICE_URL = os.environ.get("EMISSIONS_SERVICE_URL", "http://localhost:8002")
OPTIMIZATION_SERVICE_URL = os.environ.get("OPTIMIZATION_SERVICE_URL", "http://localhost:8003")

# Timeouts: proxy calls get a generous timeout since /optimize in particular
# is expected to be slow (see optimization-service/app/main.py's docstring --
# a full QPSO + NSGA-II run). Health checks get a short timeout so one dead
# downstream service can't hang the gateway's own /health response.
_PROXY_TIMEOUT = 120.0
_HEALTH_CHECK_TIMEOUT = 2.0

app = FastAPI(title="Q-Fleet AI Gateway")

# --- CORS ------------------------------------------------------------------
# This is a hackathon-scale local demo: the dashboard is a single trusted
# frontend running on a developer's machine (or one Docker Compose network),
# not a public multi-tenant API. Allowing "*" would be unacceptable for a
# real production gateway (it would let ANY origin read responses, including
# ones containing operational data), but here the tradeoff is deliberately
# accepted for convenience -- we don't know in advance whether the frontend
# agent's dev server is Vite (5173) or CRA (3000), and a misconfigured/absent
# FRONTEND_ORIGIN env var should never silently break the demo. If this were
# heading to production, this should be tightened to an explicit allow-list
# driven entirely by FRONTEND_ORIGIN (no wildcard, no hardcoded localhost
# ports).
_FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN")
_DEFAULT_DEV_ORIGINS = [
    "http://localhost:5173",  # Vite default
    "http://localhost:3000",  # CRA default
]
_ALLOWED_ORIGINS = list(_DEFAULT_DEV_ORIGINS)
if _FRONTEND_ORIGIN and _FRONTEND_ORIGIN not in _ALLOWED_ORIGINS:
    _ALLOWED_ORIGINS.append(_FRONTEND_ORIGIN)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DOWNSTREAM_HEALTH_URLS: dict[str, str] = {
    "data": f"{DATA_SERVICE_URL}/health",
    "prediction": f"{PREDICTION_SERVICE_URL}/health",
    "emissions": f"{EMISSIONS_SERVICE_URL}/health",
    "optimization": f"{OPTIMIZATION_SERVICE_URL}/health",
}

_PROXY_TARGETS: dict[str, str] = {
    "features": f"{DATA_SERVICE_URL}/features",
    "predict": f"{PREDICTION_SERVICE_URL}/predict",
    "emissions": f"{EMISSIONS_SERVICE_URL}/emissions",
    "optimize": f"{OPTIMIZATION_SERVICE_URL}/optimize",
    "ports": f"{DATA_SERVICE_URL}/ports",
    "routes": f"{DATA_SERVICE_URL}/routes",
    "weather": f"{DATA_SERVICE_URL}/weather",
}


async def _check_service_health(url: str) -> str:
    """Ping one downstream service's /health with a short timeout. Returns
    "ok", "degraded" (reachable but non-200), or "unreachable" (timeout,
    connection error, or any other transport failure) -- never raises, so a
    single dead service can't crash the gateway's own /health response."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=_HEALTH_CHECK_TIMEOUT)
    except httpx.HTTPError:
        return "unreachable"

    return "ok" if response.status_code == 200 else "degraded"


async def _proxy_post(url: str, body: bytes, headers: dict[str, str]) -> Response:
    """POST `body` to `url` and return the downstream response unchanged
    (status code, JSON body, content-type). Raises HTTPException(502) on
    connection failure -- no retry logic, a clear error is enough for a
    hackathon demo, mirroring prediction-service's _fetch_features."""
    try:
        async with httpx.AsyncClient() as client:
            downstream_response = await client.post(
                url, content=body, headers=headers, timeout=_PROXY_TIMEOUT
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream request to {url} failed: {exc}") from exc

    return Response(
        content=downstream_response.content,
        status_code=downstream_response.status_code,
        media_type=downstream_response.headers.get("content-type"),
    )


async def _proxy_get(url: str, params: dict[str, str]) -> Response:
    """GET `url` (with query `params`) and return the downstream response
    unchanged (status code, JSON body, content-type). Raises
    HTTPException(502) on connection failure, mirroring _proxy_post -- the
    only difference from that function is GET-with-query-params instead of
    POST-with-body, since /ports and /routes are read-only lookups with no
    request body."""
    try:
        async with httpx.AsyncClient() as client:
            downstream_response = await client.get(url, params=params, timeout=_PROXY_TIMEOUT)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream request to {url} failed: {exc}") from exc

    return Response(
        content=downstream_response.content,
        status_code=downstream_response.status_code,
        media_type=downstream_response.headers.get("content-type"),
    )


def _forward_headers(request: Request) -> dict[str, str]:
    """Only forward content-type -- other incoming headers (host, content-
    length, connection, etc.) either don't make sense to replay against a
    different host or are recomputed by httpx itself."""
    content_type = request.headers.get("content-type")
    return {"content-type": content_type} if content_type else {}


@app.get("/health")
async def health() -> dict[str, object]:
    services = {
        name: await _check_service_health(url) for name, url in _DOWNSTREAM_HEALTH_URLS.items()
    }
    return {"status": "ok", "services": services}


@app.post("/api/optimize")
async def api_optimize(request: Request) -> Response:
    body = await request.body()
    return await _proxy_post(_PROXY_TARGETS["optimize"], body, _forward_headers(request))


@app.post("/api/features")
async def api_features(request: Request) -> Response:
    body = await request.body()
    return await _proxy_post(_PROXY_TARGETS["features"], body, _forward_headers(request))


@app.post("/api/predict")
async def api_predict(request: Request) -> Response:
    body = await request.body()
    return await _proxy_post(_PROXY_TARGETS["predict"], body, _forward_headers(request))


@app.post("/api/emissions")
async def api_emissions(request: Request) -> Response:
    body = await request.body()
    return await _proxy_post(_PROXY_TARGETS["emissions"], body, _forward_headers(request))


@app.get("/api/ports")
async def api_ports() -> Response:
    return await _proxy_get(_PROXY_TARGETS["ports"], {})


@app.get("/api/routes")
async def api_routes(origin: str, destination: str) -> Response:
    return await _proxy_get(_PROXY_TARGETS["routes"], {"origin": origin, "destination": destination})


@app.get("/api/weather")
async def api_weather(origin: str, destination: str) -> Response:
    # /weather can be slower than /routes (multiple real Open-Meteo HTTP
    # calls per sampled waypoint, done concurrently server-side but still
    # real network round trips) -- reuses the same generous _PROXY_TIMEOUT
    # as /api/optimize rather than risking a premature gateway timeout.
    return await _proxy_get(_PROXY_TARGETS["weather"], {"origin": origin, "destination": destination})
