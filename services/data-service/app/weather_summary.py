"""Pure aggregation logic: list[WeatherSample] -> the single-value weather
shape VoyageRequest/OptimizationConstraints need.

Deliberately split out of app/weather.py (which does the real Open-Meteo
HTTP calls, via `httpx`, and imports `app.routing`) so this one function
has NO dependency on anything but `common.schemas` -- optimization-service
loads exactly this module via its cross-service-import shim (see
app/optimizer.py's module docstring on why a plain `from app.weather
import ...` isn't used there: `app.weather`'s own `from app.routing
import ...` would resolve against optimization-service's OWN `app`
package when loaded that way, not data-service's). `app/weather.py`
re-exports `summarize_weather_for_optimizer` from here for data-service's
own callers, so there is exactly one implementation either way.
"""

from __future__ import annotations

from common.schemas import WeatherSample


def summarize_weather_for_optimizer(samples: list[WeatherSample]) -> dict[str, float]:
    """Route-wide list[WeatherSample] -> the single-value
    {wind_speed, wave_height, temperature, current_speed} shape
    VoyageRequest/OptimizationConstraints' weather fields need.

    AGGREGATION CHOICE: plain mean of each sample point's CURRENT
    conditions across the route. This is a simple, honest default -- it
    does NOT weight points by when the vessel would actually reach them
    (e.g. a slower vessel reaching a later waypoint days from now should
    arguably use that waypoint's FORECAST for that day, not its current
    reading). That refinement needs ETA-aware routing (departure time +
    cumulative distance/speed per waypoint) which doesn't exist yet in
    this codebase; a plain mean-of-current-conditions is deliberately kept
    simple for now and documented here as a known future improvement,
    rather than silently baking in an unstated assumption.

    current_speed is NOT averaged from samples (every sample already holds
    the same placeholder value -- see app/weather.py's module docstring's
    KNOWN GAP) -- returned as-is for clarity.

    Raises ValueError for an empty samples list (nothing to summarize).
    """
    if not samples:
        raise ValueError("summarize_weather_for_optimizer requires at least one WeatherSample")

    n = len(samples)
    return {
        "wind_speed": sum(s.current.wind_speed for s in samples) / n,
        "wave_height": sum(s.current.wave_height for s in samples) / n,
        "temperature": sum(s.current.temperature for s in samples) / n,
        "current_speed": samples[0].current.current_speed,
    }
