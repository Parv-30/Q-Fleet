"""HTTP client for data-service's real routing endpoint (sub-phase B),
used when a caller's OptimizationConstraints supply BOTH `origin` and
`destination` (see common/schemas.py's OptimizationConstraints docstring
and app/fleet_catalog.py's module docstring for the constrained-routing
design).

optimization-service had no existing client for data-service before this
sub-phase (its only existing cross-service calls are the RabbitMQ RPC
calls to prediction-service/emissions-service in app/rpc_client.py). Since
data-service's routing lookup is exposed as a plain REST endpoint (GET
/routes?origin=&destination=, see data-service/app/main.py), not an RPC
queue, a simple `httpx` call is the most consistent approach -- mirrors
gateway's/prediction-service's own httpx usage pattern (see
prediction-service/app/main.py's `_fetch_features`), except SYNCHRONOUS
(`httpx.Client`, not `httpx.AsyncClient`): app/optimizer.py's
run_optimization/`_evaluate_proposal` call path is plain synchronous
Python (QPSO's inner loop, no running event loop), matching how it already
calls the synchronous RabbitMQ RPC client.
"""

from __future__ import annotations

import os

import httpx

# Mirrors gateway's/prediction-service's DATA_SERVICE_URL env var pattern
# for local-dev-vs-Docker-Compose base URL resolution.
DATA_SERVICE_URL = os.environ.get("DATA_SERVICE_URL", "http://localhost:8000")

DEFAULT_TIMEOUT_SECONDS = 10.0


class RoutingClientError(Exception):
    """Raised when data-service's /routes call fails (connection error or
    non-200 response) -- covers both transport failures and data-service's
    own 404 (unknown port)/400 (e.g. origin == destination) responses."""


def fetch_route_options(
    origin: str,
    destination: str,
    base_url: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict]:
    """Call data-service's GET /routes?origin=&destination=, returning the
    raw list of route-option dicts (each with origin/destination/
    distance_km/via keys, matching data-service's RouteOption -- see
    data-service/app/routing.py) exactly as the endpoint returns them.

    Raises RoutingClientError on any connection failure or non-200
    response (folding data-service's 404/400 semantics into one exception
    type, since callers here just need "did this work or not").
    """
    url = f"{base_url or DATA_SERVICE_URL}/routes"
    try:
        with httpx.Client() as client:
            response = client.get(url, params={"origin": origin, "destination": destination}, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise RoutingClientError(f"data-service /routes request failed: {exc}") from exc

    if response.status_code != 200:
        raise RoutingClientError(
            f"data-service /routes returned {response.status_code}: {response.text}"
        )

    return response.json()
