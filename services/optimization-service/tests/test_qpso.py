"""Tests for app/qpso.py.

Uses a cheap synthetic `evaluate_fn` (no RPC calls) so these tests run
fast and require no broker/services -- app/qpso.py itself has no
dependency on prediction-service/emissions-service/RPC at all, by design
(see its module docstring), which is exactly what makes this possible.
"""

from __future__ import annotations

import math

from app.fleet_catalog import DEFAULT_ROUTE_CATALOG, FLEET_CATALOG, SPEED_MAX_KNOTS, SPEED_MIN_KNOTS
from app.qpso import (
    BETA_MAX,
    BETA_MIN,
    N_DIMENSIONS,
    QPSOSwarm,
    _discretize_index,
    beta_schedule,
    build_search_space,
    decode_position,
    default_search_space,
    run_qpso,
)
from common.schemas import FuelType, ObjectiveValues, OptimizationConstraints


def fake_evaluate(proposal) -> ObjectiveValues:
    """Cheap synthetic objective function standing in for the real
    RPC-backed pipeline: deterministic given the proposal, no I/O."""
    return ObjectiveValues(
        fuel_consumption=50.0 + proposal.speed * 2.0,
        operating_cost=1000.0 + proposal.speed * 100.0,
        lifecycle_ghg=200.0 + proposal.speed * 5.0,
        reliability=max(0.0, 1.0 - proposal.speed / 100.0),
        cargo_satisfaction=0.5,
        fleet_utilization=0.5,
    )


# --- discretization ---------------------------------------------------


def test_discretize_index_always_in_bounds_for_wide_range_of_inputs():
    catalog_size = len(FLEET_CATALOG)
    for raw in [-1000.5, -1.0, -0.001, 0.0, 0.5, 3.99, 4.0, 1e6, -1e6]:
        index = _discretize_index(raw, catalog_size)
        assert 0 <= index < catalog_size


def test_decode_position_vessel_route_fuel_always_valid_indices():
    test_positions = [
        [0.0, 0.0, 14.0, 0.0],
        [-5.5, 100.2, 30.0, -3.3],
        [1e9, -1e9, -50.0, 1e9],
        [3.999, 5.999, 8.0, 5.999],
    ]
    for position in test_positions:
        proposal = decode_position(position)
        assert 0 <= proposal.vessel_index < len(FLEET_CATALOG)
        assert 0 <= proposal.route_index < len(DEFAULT_ROUTE_CATALOG)
        assert isinstance(proposal.fuel, FuelType)


def test_decode_position_speed_always_within_bounds():
    test_speeds = [-1000.0, -0.001, 0.0, SPEED_MIN_KNOTS, 15.0, SPEED_MAX_KNOTS, 1000.0]
    for speed in test_speeds:
        proposal = decode_position([0.0, 0.0, speed, 0.0])
        assert SPEED_MIN_KNOTS <= proposal.speed <= SPEED_MAX_KNOTS


# --- beta schedule -------------------------------------------------------


def test_beta_schedule_starts_at_max_ends_at_min():
    max_iterations = 30
    assert beta_schedule(0, max_iterations) == BETA_MAX
    assert beta_schedule(max_iterations - 1, max_iterations) == BETA_MIN


def test_beta_schedule_decreases_monotonically():
    max_iterations = 10
    betas = [beta_schedule(i, max_iterations) for i in range(max_iterations)]
    for earlier, later in zip(betas, betas[1:]):
        assert later <= earlier
    assert betas[0] > betas[-1]


def test_beta_schedule_single_iteration_returns_min():
    assert beta_schedule(0, 1) == BETA_MIN


# --- swarm dynamics --------------------------------------------------------


def test_particles_move_across_iterations():
    swarm = QPSOSwarm(swarm_size=5, max_iterations=5, seed=42)
    positions_before = [list(p.position) for p in swarm.particles]
    swarm.step(0, fake_evaluate)
    positions_after = [list(p.position) for p in swarm.particles]

    # At least some particle must have actually moved -- the update is
    # stochastic but "no particle moves at all" would indicate a bug.
    assert any(before != after for before, after in zip(positions_before, positions_after))


def test_positions_keep_changing_across_many_iterations():
    swarm = QPSOSwarm(swarm_size=5, max_iterations=10, seed=7)
    snapshots = []
    for i in range(10):
        swarm.step(i, fake_evaluate)
        snapshots.append([list(p.position) for p in swarm.particles])

    # Consecutive snapshots should generally differ (swarm keeps exploring).
    distinct_transitions = sum(1 for a, b in zip(snapshots, snapshots[1:]) if a != b)
    assert distinct_transitions > 0


def test_run_qpso_returns_full_population_not_just_bests():
    swarm_size, iterations = 6, 4
    population = run_qpso(swarm_size, iterations, fake_evaluate, seed=1)
    # Every particle's evaluation from every iteration is retained.
    assert len(population) == swarm_size * iterations
    for proposal, objectives in population:
        assert isinstance(objectives, ObjectiveValues)


def test_run_qpso_all_positions_decode_to_valid_bounds():
    population = run_qpso(swarm_size=8, iterations=5, evaluate_fn=fake_evaluate, seed=3)
    for proposal, _ in population:
        assert 0 <= proposal.vessel_index < len(FLEET_CATALOG)
        assert 0 <= proposal.route_index < len(DEFAULT_ROUTE_CATALOG)
        assert SPEED_MIN_KNOTS <= proposal.speed <= SPEED_MAX_KNOTS
        assert isinstance(proposal.fuel, FuelType)


# --- reproducibility -------------------------------------------------------


def test_run_qpso_reproducible_with_fixed_seed():
    pop_a = run_qpso(swarm_size=5, iterations=5, evaluate_fn=fake_evaluate, seed=123)
    pop_b = run_qpso(swarm_size=5, iterations=5, evaluate_fn=fake_evaluate, seed=123)

    assert len(pop_a) == len(pop_b)
    for (proposal_a, obj_a), (proposal_b, obj_b) in zip(pop_a, pop_b):
        assert proposal_a.vessel_index == proposal_b.vessel_index
        assert proposal_a.route_index == proposal_b.route_index
        assert proposal_a.speed == proposal_b.speed
        assert proposal_a.fuel == proposal_b.fuel
        assert obj_a == obj_b


def test_run_qpso_different_seeds_produce_different_populations():
    pop_a = run_qpso(swarm_size=5, iterations=5, evaluate_fn=fake_evaluate, seed=1)
    pop_b = run_qpso(swarm_size=5, iterations=5, evaluate_fn=fake_evaluate, seed=2)

    speeds_a = [p.speed for p, _ in pop_a]
    speeds_b = [p.speed for p, _ in pop_b]
    assert speeds_a != speeds_b


# --- mbest sanity ------------------------------------------------------


def test_mbest_is_mean_of_personal_bests():
    swarm = QPSOSwarm(swarm_size=4, max_iterations=3, seed=9)
    mbest = swarm._mbest()
    assert len(mbest) == N_DIMENSIONS
    for d in range(N_DIMENSIONS):
        expected = sum(p.personal_best_position[d] for p in swarm.particles) / swarm.swarm_size
        assert math.isclose(mbest[d], expected)


# --- hard-filtering (OptimizationConstraints -> SearchSpace) --------------


def test_unconstrained_search_space_matches_default():
    """None constraints must reproduce today's fully-open behavior exactly
    -- regression test for backward compatibility."""
    space = build_search_space(None)
    default = default_search_space()
    assert space.vessel.indices == default.vessel.indices
    assert space.route.indices == default.route.indices
    assert space.fuel.indices == default.fuel.indices
    assert space.speed.speed_min == default.speed.speed_min
    assert space.speed.speed_max == default.speed.speed_max
    assert not space.vessel.pinned
    assert not space.route.pinned
    assert not space.fuel.pinned
    assert not space.speed.pinned


def test_unconstrained_full_run_explores_multiple_vessels_and_fuels():
    """Regression test: a full run with no constraints still searches the
    whole catalog, exactly as before OptimizationConstraints existed."""
    population = run_qpso(swarm_size=10, iterations=8, evaluate_fn=fake_evaluate, seed=11)
    vessel_indices = {p.vessel_index for p, _ in population}
    fuels = {p.fuel for p, _ in population}
    # A reasonably large, unconstrained run should visit more than just one
    # vessel/fuel -- if this ever collapses to a single value, something
    # broke the "fully open by default" guarantee.
    assert len(vessel_indices) > 1
    assert len(fuels) > 1


def test_pinned_vessel_id_used_by_every_candidate_across_full_run():
    constraints = OptimizationConstraints(vessel_id=FLEET_CATALOG[3].vessel_id)
    space = build_search_space(constraints)
    assert space.vessel.pinned
    assert space.vessel.indices == [3]

    population = run_qpso(swarm_size=8, iterations=6, evaluate_fn=fake_evaluate, seed=5, search_space=space)
    for proposal, _ in population:
        assert proposal.vessel_index == 3


def test_pinned_vessel_type_narrows_to_matching_vessels_only():
    from common.schemas import VesselType

    constraints = OptimizationConstraints(vessel_type=VesselType.CONTAINER)
    space = build_search_space(constraints)
    expected_indices = [i for i, v in enumerate(FLEET_CATALOG) if v.vessel_type == VesselType.CONTAINER]
    assert space.vessel.indices == expected_indices

    population = run_qpso(swarm_size=8, iterations=6, evaluate_fn=fake_evaluate, seed=6, search_space=space)
    for proposal, _ in population:
        assert FLEET_CATALOG[proposal.vessel_index].vessel_type == VesselType.CONTAINER


def test_allowed_fuels_subset_confines_every_candidate():
    constraints = OptimizationConstraints(allowed_fuels=[FuelType.LNG, FuelType.METHANOL])
    space = build_search_space(constraints)
    assert not space.fuel.pinned  # 2 options -- narrowed, not pinned

    population = run_qpso(swarm_size=10, iterations=8, evaluate_fn=fake_evaluate, seed=7, search_space=space)
    for proposal, _ in population:
        assert proposal.fuel in (FuelType.LNG, FuelType.METHANOL)


def test_single_allowed_fuel_pins_the_fuel_dimension():
    constraints = OptimizationConstraints(allowed_fuels=[FuelType.HYDROGEN])
    space = build_search_space(constraints)
    assert space.fuel.pinned

    population = run_qpso(swarm_size=6, iterations=5, evaluate_fn=fake_evaluate, seed=8, search_space=space)
    for proposal, _ in population:
        assert proposal.fuel == FuelType.HYDROGEN


def test_exact_speed_knots_pins_speed_for_every_candidate():
    constraints = OptimizationConstraints(speed_knots=16.5)
    space = build_search_space(constraints)
    assert space.speed.pinned
    assert space.speed.pinned_value == 16.5

    population = run_qpso(swarm_size=8, iterations=6, evaluate_fn=fake_evaluate, seed=9, search_space=space)
    for proposal, _ in population:
        assert proposal.speed == 16.5


def test_speed_range_narrows_without_pinning():
    constraints = OptimizationConstraints(speed_min_knots=10.0, speed_max_knots=12.0)
    space = build_search_space(constraints)
    assert not space.speed.pinned
    assert space.speed.speed_min == 10.0
    assert space.speed.speed_max == 12.0

    population = run_qpso(swarm_size=8, iterations=6, evaluate_fn=fake_evaluate, seed=10, search_space=space)
    for proposal, _ in population:
        assert 10.0 <= proposal.speed <= 12.0


def test_route_catalog_override_narrows_route_dimension():
    from app.fleet_catalog import CatalogRoute

    custom_routes = [
        CatalogRoute("R-A", "Mumbai", "Amsterdam", 11800.0, via="Suez Canal"),
        CatalogRoute("R-B", "Mumbai", "Amsterdam", 20500.0, via="Cape of Good Hope"),
    ]
    space = build_search_space(None, route_catalog=custom_routes)
    assert space.route_catalog == custom_routes
    assert not space.route.pinned  # 2 options

    population = run_qpso(swarm_size=8, iterations=6, evaluate_fn=fake_evaluate, seed=12, search_space=space)
    for proposal, _ in population:
        assert 0 <= proposal.route_index < 2


def test_single_route_option_pins_route_dimension():
    from app.fleet_catalog import CatalogRoute

    custom_routes = [CatalogRoute("R-A", "Los Angeles", "Seattle", 1600.0, via="direct route")]
    space = build_search_space(None, route_catalog=custom_routes)
    assert space.route.pinned

    population = run_qpso(swarm_size=6, iterations=5, evaluate_fn=fake_evaluate, seed=13, search_space=space)
    for proposal, _ in population:
        assert proposal.route_index == 0


def test_fully_pinned_search_space_every_candidate_identical():
    constraints = OptimizationConstraints(
        vessel_id=FLEET_CATALOG[0].vessel_id,
        speed_knots=14.0,
        allowed_fuels=[FuelType.DIESEL],
    )
    from app.fleet_catalog import CatalogRoute

    custom_routes = [CatalogRoute("R-A", "Mumbai", "Singapore", 3900.0)]
    space = build_search_space(constraints, route_catalog=custom_routes)
    assert space.vessel.pinned and space.route.pinned and space.speed.pinned and space.fuel.pinned

    population = run_qpso(swarm_size=5, iterations=4, evaluate_fn=fake_evaluate, seed=14, search_space=space)
    for proposal, _ in population:
        assert proposal.vessel_index == 0
        assert proposal.route_index == 0
        assert proposal.speed == 14.0
        assert proposal.fuel == FuelType.DIESEL
