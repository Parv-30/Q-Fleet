"""Hand-verified correctness tests for NSGA-II (app/nsga2.py).

Each test constructs ObjectiveValues with KNOWN dominance relationships
(worked out by hand, not derived from the implementation) and asserts the
implementation reproduces exactly the expected dominance / fronts /
crowding ordering. This is the most important test file for this service:
NSGA-II bugs (e.g. flipping a minimize/maximize direction, an off-by-one
in domination counts) are easy to introduce and easy to miss without
exact, independently-worked-out expected results.

Recall objective directions (see common.schemas.ObjectiveValues and
app/nsga2.py's module docstring):
    MINIMIZE: fuel_consumption, operating_cost, lifecycle_ghg
    MAXIMIZE: reliability, cargo_satisfaction, fleet_utilization
"""

from __future__ import annotations

from app.nsga2 import (
    crowding_distance,
    deduplicate_front,
    dominates,
    fast_non_dominated_sort,
    get_pareto_front,
)
from common.schemas import FuelType, ObjectiveValues, OptimizationCandidate


def make_candidate(name: str, **objective_overrides) -> OptimizationCandidate:
    """Build an OptimizationCandidate with default objective values,
    overridden per-test. Defaults are a "neutral" point where every
    objective is a mid-range value -- tests override exactly the fields
    relevant to the dominance relationship being tested."""
    defaults = dict(
        fuel_consumption=100.0,
        operating_cost=1000.0,
        lifecycle_ghg=500.0,
        reliability=0.5,
        cargo_satisfaction=0.5,
        fleet_utilization=0.5,
    )
    defaults.update(objective_overrides)
    return OptimizationCandidate(
        vessel=name,
        route="ROUTE-A",
        speed=14.0,
        fuel=FuelType.HFO,
        objectives=ObjectiveValues(**defaults),
    )


# ---------------------------------------------------------------------------
# dominates()
# ---------------------------------------------------------------------------


def test_dominates_strictly_better_on_all_minimize_objectives():
    # A beats B on every minimized objective -> A dominates B.
    a = ObjectiveValues(
        fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.5, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    b = ObjectiveValues(
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.5, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    assert dominates(a, b) is True
    assert dominates(b, a) is False


def test_dominates_strictly_better_on_all_maximize_objectives():
    # A beats B on every maximized objective -> A dominates B.
    a = ObjectiveValues(
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.9, cargo_satisfaction=0.9, fleet_utilization=0.9,
    )
    b = ObjectiveValues(
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.3, cargo_satisfaction=0.3, fleet_utilization=0.3,
    )
    assert dominates(a, b) is True
    assert dominates(b, a) is False


def test_dominates_equal_on_all_objectives_is_not_dominance():
    a = ObjectiveValues(
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.5, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    b = a.model_copy()
    assert dominates(a, b) is False
    assert dominates(b, a) is False


def test_dominates_mutually_non_dominated_tradeoff():
    # A is better on fuel_consumption, B is better on reliability -- a
    # genuine tradeoff, neither dominates the other.
    a = ObjectiveValues(
        fuel_consumption=50, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.3, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    b = ObjectiveValues(
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.9, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    assert dominates(a, b) is False
    assert dominates(b, a) is False


def test_dominates_at_least_as_good_and_strictly_better_on_one():
    # A ties B everywhere except is strictly better on one minimized
    # objective -> A dominates B (the "at least as good everywhere, and
    # strictly better somewhere" clause).
    a = ObjectiveValues(
        fuel_consumption=90, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.5, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    b = ObjectiveValues(
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.5, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    assert dominates(a, b) is True
    assert dominates(b, a) is False


# ---------------------------------------------------------------------------
# fast_non_dominated_sort() / get_pareto_front()
# ---------------------------------------------------------------------------


def test_sort_a_dominates_b_on_every_objective():
    # A dominates B outright (better minimize, better maximize) -> two
    # fronts: [A], [B].
    a = make_candidate(
        "A",
        fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.9, cargo_satisfaction=0.9, fleet_utilization=0.9,
    )
    b = make_candidate(
        "B",
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.3, cargo_satisfaction=0.3, fleet_utilization=0.3,
    )
    fronts = fast_non_dominated_sort([a, b])
    assert len(fronts) == 2
    assert [c.vessel for c in fronts[0]] == ["A"]
    assert [c.vessel for c in fronts[1]] == ["B"]

    pareto = get_pareto_front([a, b])
    assert [c.vessel for c in pareto] == ["A"]


def test_sort_c_and_d_mutually_non_dominated_tradeoff():
    # C is cheap/fuel-efficient but less reliable; D is reliable but
    # costlier -- a genuine tradeoff. Both belong in front 0.
    c = make_candidate(
        "C",
        fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.3, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    d = make_candidate(
        "D",
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.9, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    fronts = fast_non_dominated_sort([c, d])
    assert len(fronts) == 1
    assert {x.vessel for x in fronts[0]} == {"C", "D"}

    pareto = get_pareto_front([c, d])
    assert {x.vessel for x in pareto} == {"C", "D"}


def test_sort_combined_known_fronts():
    # Combine the two scenarios above into one population of four and
    # verify the expected three-front structure by hand:
    #   A vs B: A better on fuel/cost/ghg AND reliability -> A dominates B.
    #   A vs C: tied on fuel/cost/ghg/cargo/fleet, A's reliability 0.9 >
    #     C's 0.3 -> A dominates C.
    #   A vs D: tied on cost/ghg/cargo/fleet/reliability, A's
    #     fuel_consumption 50 < D's 100 -> A dominates D.
    #   C vs D: C is better on fuel/cost/ghg, D is better on reliability --
    #     a genuine tradeoff, mutually non-dominated.
    #   C vs B: tied on reliability/cargo/fleet, C better on fuel/cost/ghg
    #     -> C dominates B.
    #   D vs B: D better on fuel/cost/ghg AND reliability -> D dominates B.
    # So: A is dominated by nothing -> front 0 = {A}.
    #     Removing A: C and D are now dominated by nothing (their only
    #     dominator was A) -> front 1 = {C, D}.
    #     Removing C and D: B is now dominated by nothing left -> front 2 = {B}.
    a = make_candidate(
        "A",
        fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.9, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    b = make_candidate(
        "B",
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.3, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    c = make_candidate(
        "C",
        fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.3, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    d = make_candidate(
        "D",
        fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.9, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )

    # Sanity-check the hand-derived pairwise relationships directly first.
    assert dominates(a.objectives, b.objectives) is True
    assert dominates(a.objectives, c.objectives) is True
    assert dominates(a.objectives, d.objectives) is True
    assert dominates(c.objectives, b.objectives) is True
    assert dominates(d.objectives, b.objectives) is True
    assert dominates(c.objectives, d.objectives) is False
    assert dominates(d.objectives, c.objectives) is False
    assert dominates(b.objectives, a.objectives) is False
    assert dominates(b.objectives, c.objectives) is False
    assert dominates(b.objectives, d.objectives) is False

    fronts = fast_non_dominated_sort([a, b, c, d])
    assert len(fronts) == 3
    assert {x.vessel for x in fronts[0]} == {"A"}
    assert {x.vessel for x in fronts[1]} == {"C", "D"}
    assert {x.vessel for x in fronts[2]} == {"B"}

    pareto = get_pareto_front([a, b, c, d])
    assert {x.vessel for x in pareto} == {"A"}


def test_sort_all_mutually_non_dominated_single_front():
    # Three candidates, each best on a different objective and worst on
    # another -- classic three-way tradeoff, all in front 0.
    x = make_candidate("X", fuel_consumption=10, operating_cost=1000, lifecycle_ghg=1000)
    y = make_candidate("Y", fuel_consumption=1000, operating_cost=10, lifecycle_ghg=1000)
    z = make_candidate("Z", fuel_consumption=1000, operating_cost=1000, lifecycle_ghg=10)

    fronts = fast_non_dominated_sort([x, y, z])
    assert len(fronts) == 1
    assert {c.vessel for c in fronts[0]} == {"X", "Y", "Z"}


def test_sort_empty_population():
    assert fast_non_dominated_sort([]) == []
    assert get_pareto_front([]) == []


def test_sort_raises_if_objectives_missing():
    candidate = OptimizationCandidate(vessel="A", route="R", speed=14.0, fuel=FuelType.HFO)
    try:
        fast_non_dominated_sort([candidate])
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# crowding_distance()
# ---------------------------------------------------------------------------


def test_crowding_distance_boundary_points_are_infinite():
    low = make_candidate("low", fuel_consumption=10)
    mid = make_candidate("mid", fuel_consumption=50)
    high = make_candidate("high", fuel_consumption=90)
    # Keep every other objective identical so fuel_consumption is the only
    # objective contributing a finite/non-trivial distance for "mid".
    front = [low, mid, high]
    distances = crowding_distance(front)
    assert distances[id(low)] == float("inf")
    assert distances[id(high)] == float("inf")
    assert distances[id(mid)] < float("inf")
    assert distances[id(mid)] > 0


def test_crowding_distance_two_or_fewer_candidates_all_infinite():
    a = make_candidate("A")
    b = make_candidate("B")
    distances = crowding_distance([a, b])
    assert distances[id(a)] == float("inf")
    assert distances[id(b)] == float("inf")

    single = crowding_distance([a])
    assert single[id(a)] == float("inf")


def test_crowding_distance_empty_front():
    assert crowding_distance([]) == {}


def test_crowding_distance_zero_range_objective_does_not_crash():
    # All three candidates share identical objective values -> every
    # objective's range is 0; must not divide by zero, and mid should get
    # a finite (0) contribution from that objective rather than inf/nan.
    a = make_candidate("A")
    b = make_candidate("B")
    c = make_candidate("C")
    distances = crowding_distance([a, b, c])
    for value in distances.values():
        assert value == value  # not NaN



# ---------------------------------------------------------------------------
# deduplicate_front() / get_pareto_front() duplicate handling
#
# QPSO's swarm can converge multiple particles onto the same (or a
# near-identical) optimum. Because identical candidates cannot dominate
# each other, fast_non_dominated_sort correctly places all of them in
# front 0 -- but presenting the same (vessel, route, speed, fuel)
# configuration multiple times to a decision-maker is noise, not
# additional optimal information. These tests pin down that
# get_pareto_front/deduplicate_front collapse such duplicates down to one
# representative.
# ---------------------------------------------------------------------------


def test_deduplicate_front_collapses_identical_objectives():
    a = make_candidate("A")
    b = make_candidate("B")  # different vessel name, but same everything else
    front = [a, b]
    deduped = deduplicate_front(front)
    assert len(deduped) == 1
    assert deduped[0] is a  # first representative encountered is kept


def test_deduplicate_front_keeps_genuinely_distinct_candidates():
    low = make_candidate("low", fuel_consumption=10, reliability=0.1)
    high = make_candidate("high", fuel_consumption=90, reliability=0.9)
    deduped = deduplicate_front([low, high])
    assert len(deduped) == 2
    assert {c.vessel for c in deduped} == {"low", "high"}


def test_deduplicate_front_merges_within_floating_point_epsilon():
    a = make_candidate("A", fuel_consumption=100.0)
    b = make_candidate("B", fuel_consumption=100.0 + 1e-12)  # noise-level float diff
    deduped = deduplicate_front([a, b])
    assert len(deduped) == 1


def test_deduplicate_front_empty():
    assert deduplicate_front([]) == []


def test_get_pareto_front_deduplicates_three_identical_qpso_particles():
    # Reproduces the reported bug: three QPSO particles converge to the
    # exact same (vessel, route, speed, fuel) configuration, and therefore
    # score identical objectives. None dominates another (they're
    # identical), so naive front-0 extraction would return all three --
    # get_pareto_front must collapse them to a single representative.
    shared_objectives = dict(
        fuel_consumption=54.53, operating_cost=592188.94, lifecycle_ghg=209.77,
        reliability=0.93, cargo_satisfaction=0.52, fleet_utilization=0.31,
    )
    particles = [
        OptimizationCandidate(
            vessel="GC-COMPACT-01",
            route="ROUTE-MUM-BUE",
            speed=8.0,
            fuel=FuelType.DIESEL,
            objectives=ObjectiveValues(**shared_objectives),
        )
        for _ in range(3)
    ]
    pareto = get_pareto_front(particles)
    assert len(pareto) == 1
    assert pareto[0].vessel == "GC-COMPACT-01"


def test_get_pareto_front_deduplicates_but_keeps_real_tradeoffs():
    # Two identical duplicates of "C" plus a genuinely distinct "D" that
    # forms a real tradeoff -- dedup should drop the extra "C" copy but
    # keep exactly one "C" and the distinct "D".
    c1 = make_candidate(
        "C", fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.3, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    c2 = make_candidate(
        "C-twin", fuel_consumption=50, operating_cost=500, lifecycle_ghg=200,
        reliability=0.3, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    # give c2 the same (vessel, route, speed, fuel) key as c1 so dedupe key matches
    c2 = c2.model_copy(update={"vessel": "C"})
    d = make_candidate(
        "D", fuel_consumption=100, operating_cost=1000, lifecycle_ghg=500,
        reliability=0.9, cargo_satisfaction=0.5, fleet_utilization=0.5,
    )
    pareto = get_pareto_front([c1, c2, d])
    assert len(pareto) == 2
    assert {c.vessel for c in pareto} == {"C", "D"}


def test_get_pareto_front_sorted_by_crowding_distance_descending():
    # Three mutually non-dominated points forming a genuine tradeoff along
    # fuel_consumption vs. reliability (one improves as the other
    # worsens, so no candidate dominates another): the two extremes get
    # infinite crowding distance and should sort ahead of the interior
    # ("mid") point, which gets a finite distance.
    low = make_candidate("low", fuel_consumption=10, reliability=0.1)
    mid = make_candidate("mid", fuel_consumption=50, reliability=0.5)
    high = make_candidate("high", fuel_consumption=90, reliability=0.9)
    fronts = fast_non_dominated_sort([low, mid, high])
    assert len(fronts) == 1  # confirm this is genuinely one tradeoff front

    pareto = get_pareto_front([low, mid, high])
    names = [c.vessel for c in pareto]
    assert names[-1] == "mid"  # least diverse (finite distance) sorts last
    assert set(names[:2]) == {"low", "high"}
