"""QPSO (Quantum-behaved Particle Swarm Optimization) candidate generator.

Searches a MIXED discrete/continuous space -- vessel (discrete, indexes
into FLEET_CATALOG), route (discrete, indexes into a route catalog --
DEFAULT_ROUTE_CATALOG, or a real compute_route()-derived catalog for a
specific origin/destination -- see `build_search_space`), speed
(continuous, bounded [SPEED_MIN_KNOTS, SPEED_MAX_KNOTS] by default, or
narrowed/pinned by OptimizationConstraints), fuel (discrete, indexes into
FuelType) -- which is exactly why the project's architecture
specifies QPSO rather than vanilla PSO or a GA: QPSO's quantum-inspired
position update explores more broadly than classical PSO's
velocity-based update (no particle "velocity" to get stuck near zero, and
its delta-potential-well formulation lets a particle appear anywhere in
the search space with nonzero probability each iteration -- see Sun,
Fang & Xu et al.'s original QPSO papers), which suits a 4-dimensional
space where three of the four dimensions are actually discrete choices
being approximated by a continuous position value.

--- How this module combines QPSO with NSGA-II (Part D) -------------------

QPSO and NSGA-II have different native assumptions about objectives: QPSO
(like classical PSO) is fundamentally a SINGLE-objective algorithm --
every particle's personal best and the swarm's global best are defined by
comparing one scalar fitness value. NSGA-II is multi-objective by
construction, using Pareto dominance instead of any single fitness value.
Naively bolting them together (e.g. only feeding QPSO's "best" particles
into NSGA-II) would throw away most of the swarm's diversity and would let
QPSO's arbitrary scalarization choice quietly override NSGA-II's real
Pareto ranking -- defeating the point of doing multi-objective ranking at
all.

This module's design, used consistently by app/optimizer.py:

    1. QPSO explores the search space for `iterations` iterations. Each
       particle's own personal-best / the swarm's global-best -- used
       ONLY to steer the swarm's quantum position update toward promising
       regions -- are picked via a simple, fixed-weight SCALARIZED
       fitness (a weighted sum of normalized objectives; see
       `_scalarize`). This scalarization exists purely to give QPSO a
       search DIRECTION; it is never used to decide what counts as
       "good" for the final result.
    2. EVERY particle's evaluated candidate, from EVERY iteration (not
       just the scalarized "best" ones), is accumulated into one flat
       population list and returned by `run_qpso`.
    3. The caller (app/optimizer.py) hands that full population to
       NSGA-II's `fast_non_dominated_sort` / `get_pareto_front`, which is
       what actually performs real multi-objective selection using Pareto
       dominance across all six ObjectiveValues fields -- NSGA-II's
       ranking is authoritative; QPSO's scalarized fitness never
       determines the final Pareto front.

This hybrid is a legitimate, explainable, commonly-used pattern for
combining a swarm/evolutionary explorer with a dedicated multi-objective
selector: the swarm's job is broad, guided EXPLORATION of the search
space; NSGA-II's job is the actual multi-objective SELECTION. It is
documented here prominently because the combination is this project's own
specific interpretation of "QPSO + NSGA-II", not a named algorithm from
the literature.

--- The QPSO position update itself ---------------------------------------

Implements the standard QPSO formula (Sun, Fang, Xu, 2004 and follow-on
work) per particle per dimension, each iteration:

    mbest = mean of all particles' personal-best positions (the "mean
        best position" -- QPSO's substitute for classical PSO's implicit
        assumption of a single attractor; here the whole swarm's
        collective personal-best position anchors the quantum potential
        well).
    For each particle i, each dimension d:
        phi ~ U(0, 1)
        p_id = phi * pbest_id + (1 - phi) * gbest_d
            (a random point between this particle's personal best and the
            swarm's global best -- the local attractor for this update)
        u ~ U(0, 1)
        beta = beta_max - (beta_max - beta_min) * (iteration / max_iterations)
            (the contraction-expansion coefficient, decreasing LINEARLY
            across iterations -- large beta early for broad exploration,
            small beta late for fine-grained convergence; see
            `beta_schedule` / the `test_qpso.py` coverage of this)
        L = beta * abs(mbest_d - position_id)
        position_id = p_id + or - L * ln(1 / u), sign chosen with
            probability 0.5 each (the delta-potential-well-inspired
            stochastic jump)

This is the real published formula (not an approximation of it) -- each
term above corresponds directly to a term in the standard QPSO
literature's position-update equation.

Continuous position values for the three "discrete" dimensions (vessel,
route, fuel) are discretized into valid catalog indices via
floor()+modulo (see `_discretize_index`), which always yields an index in
[0, len(catalog)) regardless of how far outside a "natural" range the raw
continuous value has drifted -- QPSO's positions are unbounded in
principle (there's no velocity clamp forcing them to stay in a box), so
wrapping via modulo rather than clamping keeps the whole real line usable
and keeps every catalog entry reachable from any position.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.fleet_catalog import DEFAULT_ROUTE_CATALOG, FLEET_CATALOG, SPEED_MAX_KNOTS, SPEED_MIN_KNOTS
from common.schemas import FuelType

N_DIMENSIONS = 4  # vessel index, route index, speed, fuel index
DIM_VESSEL, DIM_ROUTE, DIM_SPEED, DIM_FUEL = range(N_DIMENSIONS)

# Contraction-expansion coefficient bounds, standard QPSO literature
# defaults: broad exploration early (beta_max), fine-grained convergence
# late (beta_min), decreasing linearly across iterations.
BETA_MAX = 1.0
BETA_MIN = 0.4

_FUEL_TYPES: list[FuelType] = list(FuelType)


def beta_schedule(iteration: int, max_iterations: int) -> float:
    """Linearly-decreasing contraction-expansion coefficient beta, per the
    standard QPSO formulation: BETA_MAX at iteration 0, BETA_MIN at the
    final iteration (max_iterations - 1), evenly spaced in between."""
    if max_iterations <= 1:
        return BETA_MIN
    progress = iteration / (max_iterations - 1)
    return BETA_MAX - (BETA_MAX - BETA_MIN) * progress


def _discretize_index(position_value: float, catalog_size: int) -> int:
    """Map an unbounded continuous QPSO position value to a valid index
    in [0, catalog_size) via floor + modulo, per the module docstring --
    always in-bounds regardless of how far position_value has drifted."""
    return int(math.floor(position_value)) % catalog_size


def _clamp_speed(position_value: float, speed_min: float, speed_max: float) -> float:
    """Speed is genuinely continuous (not an index into a catalog), so it
    is bounded via clamping rather than the modulo-wrap used for the
    discrete dimensions -- wrapping a physical speed value at the bounds
    would be physically nonsensical (24.1 knots is not "8.1 knots")."""
    return max(speed_min, min(speed_max, position_value))


@dataclass
class DimensionSpace:
    """The effective search space for ONE QPSO dimension, after applying
    any user-supplied OptimizationConstraints (see
    common/schemas.py's OptimizationConstraints and this module's
    docstring for the hard-filter semantics).

    `indices` is the list of catalog indices this dimension may resolve
    to -- for an unconstrained dimension this is every index in the full
    catalog (today's behavior, unchanged); for a narrowed dimension it is
    the constrained subset only; for a PINNED dimension it is a
    single-element list, and `pinned` is True so the swarm machinery knows
    to hold this dimension constant rather than randomizing/perturbing it.
    """

    indices: list[int]
    pinned: bool = False

    @property
    def pinned_index(self) -> int:
        return self.indices[0]


@dataclass
class SpeedSpace:
    """The effective continuous search space for the speed dimension: a
    [min, max] range (possibly collapsed to a single point, i.e. min ==
    max, which counts as pinned)."""

    speed_min: float
    speed_max: float

    @property
    def pinned(self) -> bool:
        return self.speed_min == self.speed_max

    @property
    def pinned_value(self) -> float:
        return self.speed_min


@dataclass
class SearchSpace:
    """The full effective 4-dimensional search space for one optimization
    run: per-dimension catalogs/bounds after applying
    OptimizationConstraints, defaulting to today's full-catalog/full-range
    behavior when no constraints (or no constraints touching a given
    dimension) are given. Constructed once per run by
    optimizer.py/`build_search_space` and passed into QPSOSwarm.
    """

    vessel: DimensionSpace
    route: DimensionSpace
    speed: SpeedSpace
    fuel: DimensionSpace
    # The concrete catalogs each dimension's indices index into -- carried
    # alongside the index lists so decode_position doesn't need a second
    # source of truth for "what does index 2 mean".
    vessel_catalog: list
    route_catalog: list
    fuel_catalog: list[FuelType]


def build_search_space(
    constraints,
    route_catalog: list | None = None,
) -> SearchSpace:
    """Apply an OptimizationConstraints (see common/schemas.py) on top of
    the fully-open default search space, producing the effective per-
    dimension pin/narrow/open state QPSO will search -- this is the single
    place the "hard filter" rule from the module docstring is implemented:
    a field the user specified PINS or NARROWS its dimension; a field left
    None leaves that dimension exactly as open as `default_search_space()`.

    `route_catalog`, when given, REPLACES the default route universe --
    this is how optimizer.py wires in a real compute_route() result for a
    specific origin+destination (see fleet_catalog.resolve_constrained_
    routes): every entry in `route_catalog` is a candidate the route
    dimension may resolve to, narrowed (or pinned, if there is only one)
    to exactly that set rather than the default curated universe. When
    `route_catalog` is None, the route dimension is built against
    DEFAULT_ROUTE_CATALOG instead (still further narrowed by
    constraints.route_id if given).

    `constraints` may be None, in which case this returns exactly
    `default_search_space()`.
    """
    space = default_search_space()
    if route_catalog is not None:
        # A single real compute_route() result for a specific pair (or any
        # single-entry override) is a de facto pin -- nothing else to
        # search among -- regardless of whether `constraints` is given at
        # all, so this is applied unconditionally here rather than inside
        # the `constraints is None` early-return below.
        space = SearchSpace(
            vessel=space.vessel,
            route=DimensionSpace(indices=list(range(len(route_catalog))), pinned=len(route_catalog) == 1),
            speed=space.speed,
            fuel=space.fuel,
            vessel_catalog=space.vessel_catalog,
            route_catalog=route_catalog,
            fuel_catalog=space.fuel_catalog,
        )

    if constraints is None:
        return space

    # --- Vessel: vessel_id (pin) takes priority over vessel_type (narrow) ---
    if constraints.vessel_id is not None:
        matches = [i for i, v in enumerate(space.vessel_catalog) if v.vessel_id == constraints.vessel_id]
        if matches:
            space.vessel = DimensionSpace(indices=[matches[0]], pinned=True)
    elif constraints.vessel_type is not None:
        matches = [i for i, v in enumerate(space.vessel_catalog) if v.vessel_type == constraints.vessel_type]
        if matches:
            space.vessel = DimensionSpace(indices=matches, pinned=len(matches) == 1)

    # --- Route: route_id (direct pin) takes priority over the route_catalog
    # narrowing/pinning already applied above; origin+destination
    # narrowing is handled by the caller supplying `route_catalog` above,
    # since it requires a real compute_route() call this module has no
    # business making itself.
    if constraints.route_id is not None:
        matches = [i for i, r in enumerate(space.route_catalog) if r.route_id == constraints.route_id]
        if matches:
            space.route = DimensionSpace(indices=[matches[0]], pinned=True)

    # --- Speed: an exact speed_knots pin takes priority over a min/max
    # narrow. Falling back to the default bounds for whichever of
    # min/max isn't given, per "narrows continuous bounds" semantics.
    if constraints.speed_knots is not None:
        space.speed = SpeedSpace(speed_min=constraints.speed_knots, speed_max=constraints.speed_knots)
    elif constraints.speed_min_knots is not None or constraints.speed_max_knots is not None:
        new_min = constraints.speed_min_knots if constraints.speed_min_knots is not None else space.speed.speed_min
        new_max = constraints.speed_max_knots if constraints.speed_max_knots is not None else space.speed.speed_max
        space.speed = SpeedSpace(speed_min=new_min, speed_max=new_max)

    # --- Fuel: allowed_fuels narrows (or pins, if exactly one entry). ---
    if constraints.allowed_fuels is not None:
        matches = [i for i, f in enumerate(space.fuel_catalog) if f in constraints.allowed_fuels]
        if matches:
            space.fuel = DimensionSpace(indices=matches, pinned=len(matches) == 1)

    return space


def default_search_space() -> SearchSpace:
    """The fully-open search space: every dimension unconstrained, exactly
    matching this module's pre-constraints behavior (full FLEET_CATALOG,
    full DEFAULT_ROUTE_CATALOG, full [SPEED_MIN_KNOTS, SPEED_MAX_KNOTS]
    range, every FuelType) -- used when no OptimizationConstraints (or
    None) is supplied, and as the base every dimension narrows/pins from.
    """
    return SearchSpace(
        vessel=DimensionSpace(indices=list(range(len(FLEET_CATALOG)))),
        route=DimensionSpace(indices=list(range(len(DEFAULT_ROUTE_CATALOG)))),
        speed=SpeedSpace(speed_min=SPEED_MIN_KNOTS, speed_max=SPEED_MAX_KNOTS),
        fuel=DimensionSpace(indices=list(range(len(_FUEL_TYPES)))),
        vessel_catalog=FLEET_CATALOG,
        route_catalog=DEFAULT_ROUTE_CATALOG,
        fuel_catalog=_FUEL_TYPES,
    )


@dataclass
class CandidateProposal:
    """One QPSO particle's decoded position for one iteration: the
    (vessel, route, speed, fuel) tuple ready to be scored by
    prediction-service/emissions-service and evaluated into
    ObjectiveValues by app/objectives.py."""

    vessel_index: int
    route_index: int
    speed: float
    fuel: FuelType


def decode_position(position: list[float], search_space: SearchSpace | None = None) -> CandidateProposal:
    """Continuous QPSO position vector -> a concrete, in-bounds candidate
    proposal, applying each dimension's own discretization/clamping rule
    (see module docstring) WITHIN the given search_space -- a pinned
    dimension always decodes to its pinned value regardless of the raw
    position value (the position component for a pinned dimension is
    never actually used to choose among alternatives); a narrowed
    dimension discretizes/clamps into the narrowed index list / range
    instead of the full catalog/range. Defaults to `default_search_space()`
    (today's fully-open behavior) when no search_space is given, so
    existing callers/tests are unaffected.
    """
    space = search_space or default_search_space()

    if space.vessel.pinned:
        vessel_index = space.vessel.pinned_index
    else:
        local_index = _discretize_index(position[DIM_VESSEL], len(space.vessel.indices))
        vessel_index = space.vessel.indices[local_index]

    if space.route.pinned:
        route_index = space.route.pinned_index
    else:
        local_index = _discretize_index(position[DIM_ROUTE], len(space.route.indices))
        route_index = space.route.indices[local_index]

    if space.speed.pinned:
        speed = space.speed.pinned_value
    else:
        speed = _clamp_speed(position[DIM_SPEED], space.speed.speed_min, space.speed.speed_max)

    if space.fuel.pinned:
        fuel_index = space.fuel.pinned_index
    else:
        local_index = _discretize_index(position[DIM_FUEL], len(space.fuel.indices))
        fuel_index = space.fuel.indices[local_index]
    fuel = space.fuel_catalog[fuel_index]

    return CandidateProposal(
        vessel_index=vessel_index,
        route_index=route_index,
        speed=speed,
        fuel=fuel,
    )


def _scalarize(objectives, weights: dict[str, float]) -> float:
    """Fixed-weight scalarized fitness used ONLY to steer QPSO's swarm
    (see module docstring) -- lower is better, matching the "minimize"
    convention used for the pbest/gbest comparisons below. Minimized
    objectives are added as-is; maximized objectives are subtracted
    (turning "higher is better" into "lower [more negative] is better" in
    the same additive scalar).
    """
    return (
        weights["fuel_consumption"] * objectives.fuel_consumption
        + weights["operating_cost"] * objectives.operating_cost
        + weights["lifecycle_ghg"] * objectives.lifecycle_ghg
        - weights["reliability"] * objectives.reliability
        - weights["cargo_satisfaction"] * objectives.cargo_satisfaction
        - weights["fleet_utilization"] * objectives.fleet_utilization
    )


# Default scalarization weights: normalize each objective's rough
# operating scale (fuel/cost/ghg are large-magnitude physical quantities;
# reliability/cargo_satisfaction/fleet_utilization are already in [0, 1])
# so no single objective mechanically dominates the sum just because of
# its units. These are only a steering heuristic (see module docstring),
# not a claim about real-world relative importance.
DEFAULT_SCALARIZATION_WEIGHTS: dict[str, float] = {
    "fuel_consumption": 1.0 / 100.0,  # tonnes, O(10-500)
    "operating_cost": 1.0 / 50000.0,  # currency units, O(1e4-1e5)
    "lifecycle_ghg": 1.0 / 500.0,  # tonnes CO2e, O(100-2000)
    "reliability": 1.0,
    "cargo_satisfaction": 1.0,
    "fleet_utilization": 1.0,
}


@dataclass
class Particle:
    position: list[float]
    personal_best_position: list[float]
    personal_best_fitness: float = field(default=math.inf)


class QPSOSwarm:
    """A QPSO swarm over the 4-dimensional mixed candidate space.

    Usage: construct once, then call `.step(evaluate_fn)` once per
    iteration, where `evaluate_fn(CandidateProposal) -> ObjectiveValues`
    does the actual (expensive, RPC-calling) scoring. `run_qpso` below is
    the convenience entry point that drives the whole loop and collects
    the full evaluated population -- see its docstring and the module
    docstring for why the full population (not just personal/global
    bests) is what matters downstream.
    """

    def __init__(
        self,
        swarm_size: int,
        max_iterations: int,
        seed: int | None = None,
        scalarization_weights: dict[str, float] | None = None,
        search_space: SearchSpace | None = None,
    ):
        self.swarm_size = swarm_size
        self.max_iterations = max_iterations
        self.rng = random.Random(seed)
        self.weights = scalarization_weights or DEFAULT_SCALARIZATION_WEIGHTS
        self.search_space = search_space or default_search_space()

        self.particles: list[Particle] = []
        for _ in range(swarm_size):
            position = self._random_position()
            self.particles.append(
                Particle(position=position, personal_best_position=list(position))
            )

        self.global_best_position: list[float] = list(self.particles[0].position)
        self.global_best_fitness: float = math.inf

    def _random_position(self) -> list[float]:
        """A random starting position within the (possibly narrowed/
        pinned) search space. For a pinned dimension, the position value
        is fixed at that pinned target rather than randomized at all --
        decode_position would ignore it anyway, but keeping the position
        AT the pinned value too (rather than some random distractor value)
        keeps the quantum update's mbest/attractor math meaningful, since
        the position vector is otherwise used verbatim as `particle.position`.
        For a narrowed dimension, randomizes only within the narrowed
        local-index range / speed range, not the full catalog/range."""
        space = self.search_space

        if space.vessel.pinned:
            vessel_pos = float(space.vessel.pinned_index)
        else:
            vessel_pos = self.rng.uniform(0, len(space.vessel.indices))

        if space.route.pinned:
            route_pos = float(space.route.pinned_index)
        else:
            route_pos = self.rng.uniform(0, len(space.route.indices))

        if space.speed.pinned:
            speed_pos = space.speed.pinned_value
        else:
            speed_pos = self.rng.uniform(space.speed.speed_min, space.speed.speed_max)

        if space.fuel.pinned:
            fuel_pos = float(space.fuel.pinned_index)
        else:
            fuel_pos = self.rng.uniform(0, len(space.fuel.indices))

        return [vessel_pos, route_pos, speed_pos, fuel_pos]

    def _mbest(self) -> list[float]:
        """Mean best position: the componentwise mean of every particle's
        personal-best position, per the standard QPSO formulation."""
        mbest = [0.0] * N_DIMENSIONS
        for particle in self.particles:
            for d in range(N_DIMENSIONS):
                mbest[d] += particle.personal_best_position[d]
        return [v / self.swarm_size for v in mbest]

    def step(self, iteration: int, evaluate_fn) -> list[tuple[CandidateProposal, object]]:
        """Run one QPSO iteration: decode + evaluate every particle's
        CURRENT position, update personal/global bests from the
        scalarized fitness, then move every particle via the quantum
        position update. Returns this iteration's (proposal, objectives)
        pairs for every particle -- the caller accumulates these across
        iterations into the full population handed to NSGA-II.
        """
        beta = beta_schedule(iteration, self.max_iterations)
        mbest = self._mbest()

        # Per-dimension pinned flags, computed once per step -- a pinned
        # dimension's position is held exactly constant across the whole
        # run: never randomized in _random_position (see above), never
        # perturbed by the quantum update below, and decode_position
        # always resolves it to the single pinned catalog value/speed
        # regardless of whatever value happens to sit in that slot of the
        # position vector.
        pinned_dims = [
            self.search_space.vessel.pinned,
            self.search_space.route.pinned,
            self.search_space.speed.pinned,
            self.search_space.fuel.pinned,
        ]

        iteration_results: list[tuple[CandidateProposal, object]] = []

        # Evaluate current positions, update personal/global bests.
        for particle in self.particles:
            proposal = decode_position(particle.position, self.search_space)
            objectives = evaluate_fn(proposal)
            iteration_results.append((proposal, objectives))

            fitness = _scalarize(objectives, self.weights)
            if fitness < particle.personal_best_fitness:
                particle.personal_best_fitness = fitness
                particle.personal_best_position = list(particle.position)
            if fitness < self.global_best_fitness:
                self.global_best_fitness = fitness
                self.global_best_position = list(particle.position)

        # Quantum position update, per the module docstring's formula --
        # skipped entirely for any pinned dimension, which simply keeps
        # its current (constant) position value untouched.
        for particle in self.particles:
            new_position = list(particle.position)
            for d in range(N_DIMENSIONS):
                if pinned_dims[d]:
                    continue
                phi = self.rng.uniform(0.0, 1.0)
                p_d = phi * particle.personal_best_position[d] + (1 - phi) * self.global_best_position[d]

                u = self.rng.uniform(1e-9, 1.0)  # avoid ln(1/0) = inf
                L = beta * abs(mbest[d] - particle.position[d])
                sign = 1.0 if self.rng.random() < 0.5 else -1.0
                new_position[d] = p_d + sign * L * math.log(1.0 / u)
            particle.position = new_position

        return iteration_results


def run_qpso(
    swarm_size: int,
    iterations: int,
    evaluate_fn,
    seed: int | None = None,
    search_space: SearchSpace | None = None,
) -> list[tuple[CandidateProposal, object]]:
    """Drive a full QPSO run and return the FULL evaluated population
    (every particle's proposal + objectives, from every iteration) -- see
    the module docstring for why the full population, not just the
    scalarized "best" particles, is what gets returned here: NSGA-II
    (Part D) does the real multi-objective selection over this whole set.

    `evaluate_fn(CandidateProposal) -> ObjectiveValues` is injected by the
    caller (app/optimizer.py wires it up to the RPC client + objectives
    module); kept as a plain callable here so this module has no
    dependency on prediction-service/emissions-service/RPC at all and can
    be tested with a cheap synthetic evaluate_fn.

    `search_space` carries any user-supplied OptimizationConstraints'
    effect on the 4 dimensions (see `SearchSpace`/`build_search_space`);
    defaults to `default_search_space()` (today's fully-open behavior)
    when omitted, so existing callers/tests are unaffected.
    """
    swarm = QPSOSwarm(
        swarm_size=swarm_size, max_iterations=iterations, seed=seed, search_space=search_space
    )
    population: list[tuple[CandidateProposal, object]] = []
    for iteration in range(iterations):
        population.extend(swarm.step(iteration, evaluate_fn))
    return population
