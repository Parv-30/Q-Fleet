"""HTTP client for data-service's real live-weather endpoint (live-weather
sub-phase), used when a caller's OptimizationConstraints supply BOTH
`origin` and `destination` AND doesn't already override all four weather
fields directly -- see common/schemas.py's OptimizationConstraints
docstring's Weather fields section and app/optimizer.py's
_resolve_weather.

Mirrors app/routing_client.py exactly (same env var, same synchronous
httpx.Client choice for the same reason -- optimizer.py's evaluate path is
plain synchronous Python, no running event loop -- same error-folding
strategy), just against data-service's GET /weather instead of GET
/routes.
"""

from __future__ import annotations

import os

import httpx

# Mirrors routing_client.py's DATA_SERVICE_URL env var pattern.
DATA_SERVICE_URL = os.environ.get("DATA_SERVICE_URL", "http://localhost:8000")

# Real Open-Meteo calls (2 per sampled waypoint, several waypoints per
# route) can be slower than data-service's /routes lookup -- a more
# generous timeout than routing_client's 10s avoids a premature failure
# on a real multi-waypoint route.
DEFAULT_TIMEOUT_SECONDS = 30.0


class WeatherClientError(Exception):
    """Raised when data-service's /weather call fails (connection error or
    non-200 response) -- covers both transport failures and data-service's
    own 404 (unknown port)/400 (e.g. origin == destination) responses."""


def fetch_weather_samples(
    origin: str,
    destination: str,
    base_url: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict]:
    """Call data-service's GET /weather?origin=&destination=, returning the
    raw list of WeatherSample dicts exactly as the endpoint returns them.

    Raises WeatherClientError on any connection failure or non-200
    response, mirroring routing_client.fetch_route_options.
    """
    url = f"{base_url or DATA_SERVICE_URL}/weather"
    try:
        with httpx.Client() as client:
            response = client.get(url, params={"origin": origin, "destination": destination}, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise WeatherClientError(f"data-service /weather request failed: {exc}") from exc

    if response.status_code != 200:
        raise WeatherClientError(
            f"data-service /weather returned {response.status_code}: {response.text}"
        )

    return response.json()
