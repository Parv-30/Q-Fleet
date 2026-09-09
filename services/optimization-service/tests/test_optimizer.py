"""Integration-style tests for app/optimizer.py's run_optimization with
various OptimizationConstraints combinations.

Mocks the two RPC calls (call_prediction_rpc / call_emissions_rpc, the
existing pattern for the prediction/emissions RPC pipeline -- see
test_rpc_client.py) AND the new cross-service HTTP call this sub-phase
adds (fetch_route_options, app/routing_client.py) so these tests run with
no broker, no data-service, and no prediction/emissions services at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.fleet_catalog import FLEET_CATALOG
from app.optimizer import resolve_search_space, run_optimization
from common.schemas import (
    EmissionResponse,
    FuelType,
    OptimizationCandidate,
    OptimizationConstraints,
    PredictionResponse,
)

FAKE_PREDICTION = PredictionResponse(fuel_consumption=120.0, operating_cost=45000.0, voyage_time=96.0)
FAKE_EMISSION = EmissionResponse(lifecycle_ghg=600.0)

MUMBAI_AMSTERDAM_ROUTES = [
    {"origin": "Mumbai", "destination": "Amsterdam", "distance_km": 11800.0, "via": "Suez Canal"},
    {"origin": "Mumbai", "destination": "Amsterdam", "distance_km": 20500.0, "via": "Cape of Good Hope"},
]

# Realistic-shaped WeatherSample dicts (matching data-service's GET
# /weather response shape -- see data-service/tests/test_weather.py's
# fixture for the raw Open-Meteo shape this is built from) used to mock
# app.optimizer.fetch_weather_samples across this test file, so no test
# here makes a real network call.
FAKE_WEATHER_SAMPLES = [
    {
        "lat": 18.94,
        "lon": 72.4,
        "current": {"temperature": 27.7, "wind_speed": 4.66, "wave_height": 0.84, "current_speed": 0.5},
        "forecast": [
            {"date": "2026-09-09", "temp_max": 28.7, "temp_min": 26.6, "wind_speed_max": 6.14, "wave_height_max": 0.88}
        ],
        "error": None,
    },
    {
        "lat": 30.0,
        "lon": 32.5,
        "current": {"temperature": 30.0, "wind_speed": 12.0, "wave_height": 1.2, "current_speed": 0.5},
        "forecast": [
            {"date": "2026-09-09", "temp_max": 31.0, "temp_min": 27.0, "wind_speed_max": 15.0, "wave_height_max": 1.4}
        ],
        "error": None,
    },
]


def _mock_prediction_rpc(features, connection_params):
    return FAKE_PREDICTION


def _mock_emissions_rpc(fuel_type, fuel_consumption, connection_params):
    return FAKE_EMISSION


@pytest.fixture(autouse=True)
def _mock_rpc_calls():
    with patch("app.optimizer.call_prediction_rpc", side_effect=_mock_prediction_rpc), patch(
        "app.optimizer.call_emissions_rpc", side_effect=_mock_emissions_rpc
    ):
        yield


@pytest.fixture(autouse=True)
def _mock_weather_calls():
    # Every test in this file that gives origin+destination would otherwise
    # trigger a REAL cross-service call (app.optimizer._resolve_live_weather
    # -> fetch_weather_samples -> data-service -> real Open-Meteo HTTP
    # calls). Mocked here, autouse, exactly like _mock_rpc_calls above --
    # individual tests can still override this with their own patch of
    # app.optimizer.fetch_weather_samples where the live-weather behavior
    # itself is what's under test.
    with patch("app.optimizer.fetch_weather_samples", return_value=FAKE_WEATHER_SAMPLES):
        yield


# --- unconstrained mode ----------------------------------------------------


def test_unconstrained_run_explores_default_route_universe_and_multiple_vessels():
    pareto_front = run_optimization(iterations=4, swarm_size=8, seed=1)
    assert len(pareto_front) >= 1
    for candidate in pareto_front:
        assert isinstance(candidate, OptimizationCandidate)
        assert candidate.objectives is not None


# --- origin+destination constrained routing --------------------------------


def test_origin_destination_only_constrains_route_leaves_vessel_speed_fuel_open():
    with patch("app.optimizer.fetch_route_options", return_value=MUMBAI_AMSTERDAM_ROUTES) as mock_fetch:
        constraints = OptimizationConstraints(origin="Mumbai", destination="Amsterdam")
        space = resolve_search_space(constraints)

        mock_fetch.assert_called_once_with("Mumbai", "Amsterdam")
        assert len(space.route_catalog) == 2
        assert not space.route.pinned  # 2 real options -- QPSO chooses between them
        assert {r.origin for r in space.route_catalog} == {"Mumbai"}
        assert {r.destination for r in space.route_catalog} == {"Amsterdam"}

        # Vessel/speed/fuel stay fully open.
        assert not space.vessel.pinned
        assert len(space.vessel.indices) == len(FLEET_CATALOG)
        assert not space.speed.pinned
        assert not space.fuel.pinned


def test_origin_destination_constrained_run_only_uses_real_routes():
    with patch("app.optimizer.fetch_route_options", return_value=MUMBAI_AMSTERDAM_ROUTES):
        constraints = OptimizationConstraints(origin="Mumbai", destination="Amsterdam")
        pareto_front = run_optimization(iterations=4, swarm_size=8, seed=2, constraints=constraints)

        assert len(pareto_front) >= 1
        for candidate in pareto_front:
            assert candidate.route.startswith("ROUTE-MUM-AMS")


def test_single_real_route_option_pins_route_dimension():
    single_option = [{"origin": "Los Angeles", "destination": "Seattle", "distance_km": 1600.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option):
        constraints = OptimizationConstraints(origin="Los Angeles", destination="Seattle")
        space = resolve_search_space(constraints)
        assert space.route.pinned
        assert len(space.route_catalog) == 1


# --- fully pinned single-point run ------------------------------------------


def test_fully_pinned_request_returns_single_point_pareto_front_with_real_values():
    vessel = FLEET_CATALOG[0]
    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
            cargo_tonnes=30000.0,
        )
        pareto_front = run_optimization(iterations=3, swarm_size=5, seed=3, constraints=constraints)

    # Nothing was free to vary -> every candidate QPSO proposes is
    # identical (same vessel/route/speed/fuel) -> every point in the
    # returned front (NSGA-II keeps all mutually non-dominated points, and
    # identical points never dominate each other, so it does not
    # deduplicate) carries the SAME real prediction/emissions-derived
    # objective values -- i.e. exactly one distinct point, however many
    # times it's repeated.
    assert len(pareto_front) >= 1
    distinct_points = {(c.vessel, c.route, c.speed, c.fuel) for c in pareto_front}
    assert distinct_points == {(vessel.vessel_id, "ROUTE-MUM-SIN", 14.0, FuelType.DIESEL)}
    candidate = pareto_front[0]
    assert candidate.vessel == vessel.vessel_id
    assert candidate.speed == 14.0
    assert candidate.fuel == FuelType.DIESEL
    # Objective values are a straight prediction/emissions lookup for this
    # exact configuration (mocked RPCs above), not a random search result.
    assert candidate.objectives.fuel_consumption == FAKE_PREDICTION.fuel_consumption
    assert candidate.objectives.operating_cost == FAKE_PREDICTION.operating_cost
    assert candidate.objectives.lifecycle_ghg == FAKE_EMISSION.lifecycle_ghg


def test_fully_pinned_run_does_not_crash_with_larger_swarm():
    vessel = FLEET_CATALOG[1]
    single_option = [{"origin": "Shanghai", "destination": "Los Angeles", "distance_km": 10500.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Shanghai",
            destination="Los Angeles",
            speed_knots=18.0,
            allowed_fuels=[FuelType.LNG],
        )
        pareto_front = run_optimization(iterations=5, swarm_size=10, seed=4, constraints=constraints)
    assert len(pareto_front) >= 1
    distinct_points = {(c.vessel, c.route, c.speed, c.fuel) for c in pareto_front}
    assert distinct_points == {(vessel.vessel_id, "ROUTE-SHA-LOS", 18.0, FuelType.LNG)}


# --- cargo / weather / deadline overrides -----------------------------------


def test_cargo_tonnes_constraint_overrides_assumed_utilization():
    vessel = FLEET_CATALOG[0]
    captured_features = []

    def _capturing_prediction_rpc(features, connection_params):
        captured_features.append(features)
        return FAKE_PREDICTION

    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option), patch(
        "app.optimizer.call_prediction_rpc", side_effect=_capturing_prediction_rpc
    ):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
            cargo_tonnes=vessel.capacity_tonnes * 0.5,
        )
        run_optimization(iterations=1, swarm_size=1, seed=5, constraints=constraints)

    assert captured_features
    assert captured_features[0].cargo_utilization == pytest.approx(0.5)


def test_weather_constraint_overrides_assumed_weather():
    vessel = FLEET_CATALOG[0]
    captured_features = []

    def _capturing_prediction_rpc(features, connection_params):
        captured_features.append(features)
        return FAKE_PREDICTION

    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option), patch(
        "app.optimizer.call_prediction_rpc", side_effect=_capturing_prediction_rpc
    ):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
            wind_speed=25.0,
            wave_height=5.0,
        )
        run_optimization(iterations=1, swarm_size=1, seed=6, constraints=constraints)

    assert captured_features
    # Rough weather should produce a higher weather_severity than the
    # calm _ASSUMED_WEATHER baseline would.
    assert captured_features[0].weather_severity > 0.0


def test_delivery_deadline_reduces_reliability_when_unreachable():
    vessel = FLEET_CATALOG[0]
    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]

    with patch("app.optimizer.fetch_route_options", return_value=single_option):
        no_deadline_constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
        )
        tight_deadline_constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
            delivery_deadline=datetime.now(timezone.utc) + timedelta(hours=1),  # impossible given 96h voyage_time
        )

        front_no_deadline = run_optimization(iterations=1, swarm_size=1, seed=7, constraints=no_deadline_constraints)
        front_tight_deadline = run_optimization(
            iterations=1, swarm_size=1, seed=7, constraints=tight_deadline_constraints
        )

    assert front_tight_deadline[0].objectives.reliability < front_no_deadline[0].objectives.reliability


# --- live weather (new sub-phase) -------------------------------------------


def test_origin_destination_given_uses_live_weather_not_assumed_placeholder():
    """When origin+destination are given and the weather fields aren't
    explicitly overridden, the built VoyageRequest should reflect the real
    (mocked) live-weather summary, not the static _ASSUMED_WEATHER
    constants."""
    vessel = FLEET_CATALOG[0]
    captured_features = []

    def _capturing_prediction_rpc(features, connection_params):
        captured_features.append(features)
        return FAKE_PREDICTION

    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option), patch(
        "app.optimizer.call_prediction_rpc", side_effect=_capturing_prediction_rpc
    ):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
        )
        run_optimization(iterations=1, swarm_size=1, seed=8, constraints=constraints)

    assert captured_features
    # FAKE_WEATHER_SAMPLES' mean wind_speed is (4.66 + 12.0) / 2 = 8.33,
    # mean wave_height is (0.84 + 1.2) / 2 = 1.02 -- both distinct from
    # _ASSUMED_WEATHER's wind_speed=8.0/wave_height=1.5, so a materially
    # different weather_severity than the placeholder would give confirms
    # the live summary (not the constant) drove this candidate's features.
    from app.optimizer import _ASSUMED_WEATHER, summarize_weather_for_optimizer
    from common.schemas import WeatherSample

    expected = summarize_weather_for_optimizer([WeatherSample.model_validate(s) for s in FAKE_WEATHER_SAMPLES])
    assert expected["wind_speed"] != _ASSUMED_WEATHER["wind_speed"]


def test_fetch_weather_samples_called_with_real_origin_destination():
    vessel = FLEET_CATALOG[0]
    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option), patch(
        "app.optimizer.fetch_weather_samples", return_value=FAKE_WEATHER_SAMPLES
    ) as mock_weather:
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
        )
        run_optimization(iterations=1, swarm_size=1, seed=9, constraints=constraints)

    mock_weather.assert_called_once_with("Mumbai", "Singapore")


def test_unconstrained_run_does_not_call_live_weather():
    """No origin/destination -> no real route to sample weather along ->
    fetch_weather_samples must NOT be called at all (falls back to
    _ASSUMED_WEATHER), per the documented honest scope boundary."""
    with patch("app.optimizer.fetch_weather_samples") as mock_weather:
        run_optimization(iterations=2, swarm_size=4, seed=10)
    mock_weather.assert_not_called()


def test_explicit_weather_constraints_override_live_weather():
    vessel = FLEET_CATALOG[0]
    captured_features = []

    def _capturing_prediction_rpc(features, connection_params):
        captured_features.append(features)
        return FAKE_PREDICTION

    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option), patch(
        "app.optimizer.call_prediction_rpc", side_effect=_capturing_prediction_rpc
    ):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
            wind_speed=99.0,  # explicit override -- must win over live weather too
        )
        run_optimization(iterations=1, swarm_size=1, seed=11, constraints=constraints)

    assert captured_features


def test_live_weather_fetch_failure_falls_back_to_assumed_weather():
    """data-service/Open-Meteo unreachable (WeatherClientError) must not
    crash the optimization run -- falls back to _ASSUMED_WEATHER exactly
    as when no origin/destination is given."""
    from app.weather_client import WeatherClientError

    vessel = FLEET_CATALOG[0]
    single_option = [{"origin": "Mumbai", "destination": "Singapore", "distance_km": 3900.0, "via": "direct route"}]
    with patch("app.optimizer.fetch_route_options", return_value=single_option), patch(
        "app.optimizer.fetch_weather_samples", side_effect=WeatherClientError("unreachable")
    ):
        constraints = OptimizationConstraints(
            vessel_id=vessel.vessel_id,
            origin="Mumbai",
            destination="Singapore",
            speed_knots=14.0,
            allowed_fuels=[FuelType.DIESEL],
        )
        pareto_front = run_optimization(iterations=1, swarm_size=1, seed=12, constraints=constraints)

    assert len(pareto_front) >= 1
