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
