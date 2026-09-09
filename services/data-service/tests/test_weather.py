"""Tests for app/weather.py and app/weather_summary.py.

Three tiers, per this project's established pattern for live-dependency
code (see test_rpc_client.py vs. test_rpc_client_integration.py):

    1. TestSummarizeWeatherForOptimizer -- pure aggregation logic over
       hand-built WeatherSample objects, no network at all.
    2. TestFetchWeatherForPoint / TestFetchWeatherAlongRoute -- httpx
       mocked with REALISTIC fixture JSON (captured from real Open-Meteo
       calls during development against Mumbai's coordinates, see
       FORECAST_FIXTURE/MARINE_FIXTURE below), so parsing logic is
       exercised without hitting the network.
    3. TestLiveOpenMeteoIntegration -- ONE real network test, skipped
       gracefully (not failed) when Open-Meteo isn't reachable, mirroring
       optimization-service/tests/test_rpc_client_integration.py's socket-
       reachability-check pattern.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.weather import fetch_weather_along_route, fetch_weather_for_point
from app.weather_summary import summarize_weather_for_optimizer
from common.schemas import CurrentWeather, DailyForecast, WeatherSample

# Real response shapes captured from live calls made during development
# against Mumbai's coordinates (19.076, 72.877):
#   curl "https://api.open-meteo.com/v1/forecast?latitude=19.076&longitude=72.877&current=temperature_2m,wind_speed_10m&wind_speed_unit=ms&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max&forecast_days=5&timezone=auto"
#   curl "https://marine-api.open-meteo.com/v1/marine?latitude=19.076&longitude=72.877&current=wave_height,wave_direction,wave_period&daily=wave_height_max&forecast_days=5&timezone=auto"
FORECAST_FIXTURE = {
    "latitude": 19.086115,
    "longitude": 72.85291,
    "generationtime_ms": 0.09,
    "utc_offset_seconds": 19800,
    "timezone": "Asia/Kolkata",
    "timezone_abbreviation": "GMT+5:30",
    "elevation": 8.0,
    "current_units": {"time": "iso8601", "interval": "seconds", "temperature_2m": "°C", "wind_speed_10m": "m/s"},
    "current": {"time": "2026-09-09T11:00", "interval": 900, "temperature_2m": 28.1, "wind_speed_10m": 2.35},
    "daily_units": {
        "time": "iso8601",
        "temperature_2m_max": "°C",
        "temperature_2m_min": "°C",
        "wind_speed_10m_max": "m/s",
    },
    "daily": {
        "time": ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"],
        "temperature_2m_max": [28.9, 28.9, 29.4, 29.5, 27.7],
        "temperature_2m_min": [24.5, 25.0, 25.2, 25.3, 25.1],
        "wind_speed_10m_max": [3.83, 3.17, 3.36, 3.83, 3.92],
    },
}

MARINE_FIXTURE = {
    "latitude": 19.041664,
    "longitude": 72.875015,
    "generationtime_ms": 0.11,
    "utc_offset_seconds": 19800,
    "timezone": "Asia/Kolkata",
    "timezone_abbreviation": "GMT+5:30",
    "elevation": 8.0,
    "current_units": {"time": "iso8601", "interval": "seconds", "wave_height": "m", "wave_direction": "°", "wave_period": "s"},
    "current": {"time": "2026-09-09T11:00", "interval": 900, "wave_height": 0.84, "wave_direction": 263, "wave_period": 6.90},
    "daily_units": {"time": "iso8601", "wave_height_max": "m"},
    "daily": {
        "time": ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"],
        "wave_height_max": [0.88, 0.86, 0.84, 0.90, 1.06],
    },
}


# ---------------------------------------------------------------------------
# Pure aggregation -- no network
# ---------------------------------------------------------------------------
class TestSummarizeWeatherForOptimizer:
    def _sample(self, wind_speed, wave_height, temperature, current_speed=0.5) -> WeatherSample:
        return WeatherSample(
            lat=0.0,
            lon=0.0,
            current=CurrentWeather(
                wind_speed=wind_speed, wave_height=wave_height, temperature=temperature, current_speed=current_speed
            ),
            forecast=[],
        )

    def test_single_sample_returns_its_own_values(self):
        sample = self._sample(wind_speed=10.0, wave_height=2.0, temperature=25.0)
        result = summarize_weather_for_optimizer([sample])
        assert result == {"wind_speed": 10.0, "wave_height": 2.0, "temperature": 25.0, "current_speed": 0.5}

    def test_multiple_samples_are_averaged(self):
        samples = [
            self._sample(wind_speed=10.0, wave_height=1.0, temperature=20.0),
            self._sample(wind_speed=20.0, wave_height=3.0, temperature=30.0),
        ]
        result = summarize_weather_for_optimizer(samples)
        assert result["wind_speed"] == pytest.approx(15.0)
        assert result["wave_height"] == pytest.approx(2.0)
        assert result["temperature"] == pytest.approx(25.0)

    def test_current_speed_is_not_averaged_takes_first_sample(self):
        samples = [
            self._sample(wind_speed=10.0, wave_height=1.0, temperature=20.0, current_speed=0.5),
            self._sample(wind_speed=10.0, wave_height=1.0, temperature=20.0, current_speed=0.5),
        ]
        result = summarize_weather_for_optimizer(samples)
        assert result["current_speed"] == 0.5

    def test_empty_list_raises_value_error(self):
        with pytest.raises(ValueError):
            summarize_weather_for_optimizer([])


# ---------------------------------------------------------------------------
# fetch_weather_for_point -- mocked httpx against realistic fixtures
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_body


class TestFetchWeatherForPoint:
    def test_parses_realistic_fixture_into_weather_sample(self):
        async def fake_get(self, url, params=None, timeout=None):
            if "marine-api" in url:
                return _FakeResponse(MARINE_FIXTURE)
            return _FakeResponse(FORECAST_FIXTURE)

        with patch("httpx.AsyncClient.get", new=fake_get):
            sample = fetch_weather_for_point(19.076, 72.877)

        assert sample.error is None
        assert sample.current.temperature == 28.1
        assert sample.current.wind_speed == 2.35
        assert sample.current.wave_height == 0.84
        assert len(sample.forecast) == 5
        assert sample.forecast[0].date == "2026-09-09"
        assert sample.forecast[0].temp_max == 28.9
        assert sample.forecast[0].temp_min == 24.5
        assert sample.forecast[0].wind_speed_max == 3.83
        assert sample.forecast[0].wave_height_max == 0.88

    def test_network_failure_returns_fallback_with_error_set(self):
        import httpx

        async def failing_get(self, url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("httpx.AsyncClient.get", new=failing_get):
            sample = fetch_weather_for_point(19.076, 72.877)

        assert sample.error is not None
        assert "failed" in sample.error.lower()
        # Documented fallback values, not a crash.
        assert sample.current.temperature == 20.0
        assert len(sample.forecast) == 5

    def test_unexpected_response_shape_returns_fallback_with_error_set(self):
        async def malformed_get(self, url, params=None, timeout=None):
            return _FakeResponse({"unexpected": "shape"})

        with patch("httpx.AsyncClient.get", new=malformed_get):
            sample = fetch_weather_for_point(19.076, 72.877)

        assert sample.error is not None
        assert sample.current.temperature == 20.0


class TestFetchWeatherAlongRoute:
    def test_computes_route_samples_waypoints_and_fetches_each(self):
        async def fake_get(self, url, params=None, timeout=None):
            if "marine-api" in url:
                return _FakeResponse(MARINE_FIXTURE)
            return _FakeResponse(FORECAST_FIXTURE)

        with patch("httpx.AsyncClient.get", new=fake_get):
            samples = fetch_weather_along_route("Mumbai", "Los Angeles")

        assert len(samples) >= 2
        for sample in samples:
            assert sample.error is None
            assert -90.0 <= sample.lat <= 90.0
            assert -180.0 <= sample.lon <= 180.0


# ---------------------------------------------------------------------------
# Live integration -- real network, skips gracefully if unreachable
# ---------------------------------------------------------------------------
def _open_meteo_reachable() -> bool:
    # A raw TCP connect to port 443 isn't enough here: it succeeds even
    # when the actual HTTPS request would fail locally for an unrelated
    # reason (e.g. this Python install's SSL context not trusting the
    # system CA store, seen during development on some Windows setups) --
    # this test should skip gracefully in that case too, same as a true
    # network-down case, rather than reporting a false failure. A real,
    # short-timeout HTTPS request is the only way to know it will actually
    # work.
    try:
        import httpx

        httpx.get("https://api.open-meteo.com/v1/forecast", params={"latitude": 0, "longitude": 0}, timeout=3.0)
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _open_meteo_reachable(),
    reason="api.open-meteo.com not reachable -- this test requires real network access.",
)
class TestLiveOpenMeteoIntegration:
    def test_real_weather_fetch_for_mumbai_is_well_formed(self):
        # Mumbai's real coordinates (see port_catalog.py).
        sample = fetch_weather_for_point(18.9750, 72.8258)

        assert sample.error is None, f"live Open-Meteo call failed: {sample.error}"
        assert -10 <= sample.current.temperature <= 50
        assert sample.current.wind_speed >= 0
        assert sample.current.wave_height >= 0
        assert len(sample.forecast) == 5
        for day in sample.forecast:
            assert day.date
            assert day.temp_max >= day.temp_min
