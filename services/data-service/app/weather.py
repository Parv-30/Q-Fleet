"""Real live weather along a computed route, via Open-Meteo (free, no API
key required).

Replaces the previous "no live weather source at all" state (see
common/schemas.py's OptimizationConstraints docstring history) with two
real HTTP calls per sampled waypoint:

    1. Open-Meteo's standard forecast API (temperature + wind):
       https://api.open-meteo.com/v1/forecast
           ?latitude={lat}&longitude={lon}
           &current=temperature_2m,wind_speed_10m
           &daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max
           &forecast_days=5&timezone=auto&wind_speed_unit=ms
       (wind_speed_unit=ms requested explicitly -- Open-Meteo's default
       wind unit is km/h, but VoyageRequest.wind_speed is documented as
       m/s; verified against the live API during development that this
       parameter is honored, both for `current` and `daily` wind fields.)

    2. Open-Meteo's separate Marine Weather API (wave height) -- the
       standard forecast API has no ocean-wave data at all:
       https://marine-api.open-meteo.com/v1/marine
           ?latitude={lat}&longitude={lon}
           &current=wave_height,wave_direction,wave_period
           &daily=wave_height_max&forecast_days=5&timezone=auto

Both response shapes were fetched live (real Mumbai coordinates) during
development to confirm the actual JSON structure rather than guessing it;
see this module's tests (test_weather.py) for the captured fixture.

KNOWN GAP (documented, not fabricated): ocean current speed
(VoyageRequest.current_speed) has no free, no-API-key live source as
straightforward as Open-Meteo's weather/marine APIs. `_ASSUMED_CURRENT_SPEED`
below stays a plain constant placeholder for that one field -- every other
field in this module's output is real, live data.
"""

from __future__ import annotations

import asyncio

import httpx

from app.routing import compute_route, sample_waypoints
from app.weather_summary import summarize_weather_for_optimizer  # re-exported, see that module
from common.schemas import CurrentWeather, DailyForecast, WeatherSample

__all__ = [
    "fetch_weather_for_point",
    "fetch_weather_along_route",
    "summarize_weather_for_optimizer",
]

FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_API_URL = "https://marine-api.open-meteo.com/v1/marine"

DEFAULT_TIMEOUT_SECONDS = 10.0

# Ocean current speed: no free/no-key live API exists for this (see module
# docstring's KNOWN GAP). A mild, direction-agnostic "typical moderate
# current" placeholder, matching the spirit of optimizer.py's pre-existing
# _ASSUMED_WEATHER current_speed value.
_ASSUMED_CURRENT_SPEED_KNOTS = 0.5

# Fallback current/forecast values used ONLY when Open-Meteo is unreachable
# or returns an unexpected shape for a given point (see
# fetch_weather_for_point) -- keeps the pipeline from crashing on a
# transient network failure, at the documented cost of that one sample
# silently holding "typical moderate conditions" instead of a real reading.
# Every WeatherSample built this way sets `error` so callers can tell.
_FALLBACK_CURRENT = CurrentWeather(
    temperature=20.0, wind_speed=8.0, wave_height=1.5, current_speed=_ASSUMED_CURRENT_SPEED_KNOTS
)


def _fallback_forecast(days: int = 5) -> list[DailyForecast]:
    # No real dates available in a pure network-failure fallback -- use a
    # placeholder label rather than fabricating a plausible-looking date.
    return [
        DailyForecast(date="unknown", temp_max=20.0, temp_min=20.0, wind_speed_max=8.0, wave_height_max=1.5)
        for _ in range(days)
    ]


async def _fetch_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    response = await client.get(url, params=params, timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


async def _fetch_weather_for_point_async(client: httpx.AsyncClient, lat: float, lon: float) -> WeatherSample:
    """Async core of fetch_weather_for_point -- see that function for the
    public sync entry point and error-handling contract."""
    forecast_params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
        "forecast_days": 5,
        "timezone": "auto",
        "wind_speed_unit": "ms",
    }
    marine_params = {
        "latitude": lat,
        "longitude": lon,
        "current": "wave_height,wave_direction,wave_period",
        "daily": "wave_height_max",
        "forecast_days": 5,
        "timezone": "auto",
    }

    try:
        forecast_json, marine_json = await asyncio.gather(
            _fetch_json(client, FORECAST_API_URL, forecast_params),
            _fetch_json(client, MARINE_API_URL, marine_params),
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        # Transport failure, non-2xx status, or an unexpected response
        # shape -- don't crash the whole route's weather fetch over one
        # unreachable/misbehaving point; fall back with a clear error flag.
        return WeatherSample(
            lat=lat,
            lon=lon,
            current=_FALLBACK_CURRENT,
            forecast=_fallback_forecast(),
            error=f"Open-Meteo request failed for ({lat}, {lon}): {exc}",
        )

    try:
        current = CurrentWeather(
            temperature=float(forecast_json["current"]["temperature_2m"]),
            wind_speed=float(forecast_json["current"]["wind_speed_10m"]),
            wave_height=float(marine_json["current"]["wave_height"]),
            current_speed=_ASSUMED_CURRENT_SPEED_KNOTS,
        )

        daily_forecast = forecast_json["daily"]
        daily_marine = marine_json["daily"]
        dates = daily_forecast["time"]
        forecast = [
            DailyForecast(
                date=dates[i],
                temp_max=float(daily_forecast["temperature_2m_max"][i]),
                temp_min=float(daily_forecast["temperature_2m_min"][i]),
                wind_speed_max=float(daily_forecast["wind_speed_10m_max"][i]),
                wave_height_max=float(daily_marine["wave_height_max"][i]),
            )
            for i in range(len(dates))
        ]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return WeatherSample(
            lat=lat,
            lon=lon,
            current=_FALLBACK_CURRENT,
            forecast=_fallback_forecast(),
            error=f"Open-Meteo returned an unexpected response shape for ({lat}, {lon}): {exc}",
        )

    return WeatherSample(lat=lat, lon=lon, current=current, forecast=forecast)


async def fetch_weather_for_point_async(lat: float, lon: float) -> WeatherSample:
    """Async entry point -- see fetch_weather_for_point (the sync wrapper)
    for the full docstring/error-handling contract. This is the one
    FastAPI's async /weather endpoint should call directly (see
    app/main.py) -- calling the sync wrapper from inside a route handler
    would hit "asyncio.run() cannot be called from a running event loop",
    since uvicorn's request handling already runs its own loop.
    """
    async with httpx.AsyncClient() as client:
        return await _fetch_weather_for_point_async(client, lat, lon)


def fetch_weather_for_point(lat: float, lon: float) -> WeatherSample:
    """Real current conditions + 5-day forecast at one lat/lon, combining
    Open-Meteo's standard forecast API (temperature, wind) and Marine API
    (wave height).

    Never raises on a network/API failure -- returns a WeatherSample with
    `error` set and documented "typical moderate conditions" fallback
    values instead (see _FALLBACK_CURRENT), so one bad point can't take
    down a whole route's weather fetch or the optimizer pipeline that
    depends on it.

    SYNCHRONOUS wrapper (via asyncio.run) for callers with no running
    event loop of their own (e.g. plain scripts, tests, optimization-
    service's synchronous QPSO evaluation loop indirectly via
    weather_client.py's HTTP call -- though that one goes over HTTP, not
    this function directly). Do NOT call this from inside an async
    context (e.g. a FastAPI route handler) -- use
    fetch_weather_for_point_async there instead.
    """
    return asyncio.run(fetch_weather_for_point_async(lat, lon))


async def fetch_weather_along_route_async(
    origin: str, destination: str, spacing_km: float | None = None
) -> list[WeatherSample]:
    """Async entry point -- see fetch_weather_along_route (the sync
    wrapper) for the full docstring/error-handling contract. This is the
    one FastAPI's async /weather endpoint should call directly (see
    app/main.py), for the same "no nested event loop" reason as
    fetch_weather_for_point_async above.
    """
    routes = compute_route(origin, destination)
    primary_route = routes[0]
    waypoints = sample_waypoints(primary_route.path, spacing_km=spacing_km or _default_spacing())
    async with httpx.AsyncClient() as client:
        tasks = [_fetch_weather_for_point_async(client, lat, lon) for lat, lon in waypoints]
        return list(await asyncio.gather(*tasks))


def fetch_weather_along_route(
    origin: str, destination: str, spacing_km: float | None = None
) -> list[WeatherSample]:
    """Real route (via compute_route) -> real geometrically-sampled
    waypoints along its actual path (via sample_waypoints, every ~500-
    1000km) -> one WeatherSample per waypoint, fetched concurrently
    (asyncio.gather over httpx.AsyncClient) rather than serially, since
    each waypoint needs 2 HTTP round trips and a route can have 10+
    waypoints.

    Uses the FIRST route option compute_route() returns (the default/
    primary route) when more than one is available (e.g. Suez vs. Cape) --
    weather is sampled along the route a voyage would actually take, and
    picking one option to sample is simpler and clearer than blending
    weather across two geometrically very different paths. Raises
    whatever compute_route() raises (UnknownPortError, RoutingError) for
    invalid origin/destination input.

    SYNCHRONOUS wrapper (via asyncio.run) -- see fetch_weather_for_point's
    docstring for why this must not be called from inside an already-
    running event loop (use fetch_weather_along_route_async there
    instead, as app/main.py's /weather endpoint does).
    """
    return asyncio.run(fetch_weather_along_route_async(origin, destination, spacing_km))


def _default_spacing() -> float:
    from app.routing import DEFAULT_SAMPLE_SPACING_KM

    return DEFAULT_SAMPLE_SPACING_KM
