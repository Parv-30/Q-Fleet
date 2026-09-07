"""NSGA-II: non-dominated sorting, crowding distance, and Pareto front
extraction over a population of scored OptimizationCandidate objects.

This is the "real multi-objective ranking" half of the QPSO + NSGA-II
design documented in qpso.py's module docstring: QPSO explores the
mixed discrete/continuous search space and produces a full population of
evaluated candidates; this module is what actually decides which of those
candidates are Pareto-optimal, using the standard NSGA-II algorithm (Deb,
Pratap, Agarwal & Meyarivan, 2002, "A Fast and Elitist Multiobjective
Genetic Algorithm: NSGA-II").

Objective directions (see common.schemas.ObjectiveValues):
    MINIMIZE: fuel_consumption, operating_cost, lifecycle_ghg
    MAXIMIZE: reliability, cargo_satisfaction, fleet_utilization

`dominates` internally flips the sign of the MAXIMIZE objectives so every
comparison can be done in "lower is better" terms without duplicating
comparison logic per objective.

Implements the real O(M*N^2) fast-non-dominated-sort algorithm (M =
number of objectives, N = population size) via domination counts and
dominated-solution sets, not an approximation -- see
`fast_non_dominated_sort` for the algorithm structure, which mirrors the
NSGA-II paper's Figure 2 pseudocode directly.
"""

from __future__ import annotations

from common.schemas import ObjectiveValues, OptimizationCandidate

# Objectives to minimize vs. maximize -- see module docstring.
_MINIMIZE_FIELDS = ("fuel_consumption", "operating_cost", "lifecycle_ghg")
_MAXIMIZE_FIELDS = ("reliability", "cargo_satisfaction", "fleet_utilization")
ALL_OBJECTIVE_FIELDS = _MINIMIZE_FIELDS + _MAXIMIZE_FIELDS


def _signed(objectives: ObjectiveValues) -> tuple[float, ...]:
    """Return all six objective values in a common "lower is better" sign
    convention: MINIMIZE fields as-is, MAXIMIZE fields negated. Comparing
    these tuples element-wise with <= / < is then a correct dominance
    check regardless of each objective's native direction."""
    minimize_values = tuple(getattr(objectives, f) for f in _MINIMIZE_FIELDS)
    maximize_values = tuple(-getattr(objectives, f) for f in _MAXIMIZE_FIELDS)
    return minimize_values + maximize_values


def dominates(a: ObjectiveValues, b: ObjectiveValues) -> bool:
    """Standard Pareto dominance: a dominates b iff a is at least as good
    as b on every objective, and strictly better on at least one --
    correctly accounting for each objective's minimize/maximize direction
    (see module docstring)."""
    a_signed = _signed(a)
    b_signed = _signed(b)

    at_least_as_good_everywhere = all(av <= bv for av, bv in zip(a_signed, b_signed))
    strictly_better_somewhere = any(av < bv for av, bv in zip(a_signed, b_signed))
    return at_least_as_good_everywhere and strictly_better_somewhere


def fast_non_dominated_sort(
    candidates: list[OptimizationCandidate],
) -> list[list[OptimizationCandidate]]:
    """Partition `candidates` into ranked Pareto fronts (front 0 = the
    non-dominated Pareto-optimal set, front 1 = the next tier once front 0
    is removed, etc.), using the standard NSGA-II fast-non-dominated-sort
    algorithm (domination counts + dominated-solution sets).

    Every candidate must have `.objectives` populated (not None).
    """
    n = len(candidates)
    if n == 0:
        return []

    for c in candidates:
        if c.objectives is None:
            raise ValueError("fast_non_dominated_sort requires every candidate to have .objectives populated")

    # domination_count[i]: number of candidates that dominate candidates[i]
    domination_count = [0] * n
    # dominated_indices[i]: indices of candidates that candidates[i] dominates
    dominated_indices: list[list[int]] = [[] for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if dominates(candidates[i].objectives, candidates[j].objectives):
                dominated_indices[i].append(j)
            elif dominates(candidates[j].objectives, candidates[i].objectives):
                domination_count[i] += 1

    fronts: list[list[int]] = []
    current_front = [i for i in range(n) if domination_count[i] == 0]

    while current_front:
        fronts.append(current_front)
        next_front: list[int] = []
        for i in current_front:
            for j in dominated_indices[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    next_front.append(j)
        current_front = next_front

    return [[candidates[i] for i in front] for front in fronts]


def crowding_distance(front: list[OptimizationCandidate]) -> dict[int, float]:
    """Standard NSGA-II crowding distance for one front, keyed by
    `id(candidate)` (OptimizationCandidate is a pydantic model, not
    hashable/orderable by value, so object identity is used as the key --
    callers should look up distances immediately after computing them,
    while the same candidate objects are still in scope).

    Boundary solutions (min/max on any objective) get infinite distance so
    they are always preferred, per the standard algorithm. If all
    candidates share the same value for an objective (zero range), that
    objective contributes 0 to every candidate's distance for that
    objective, avoiding a division by zero.
    """
    n = len(front)
    distances: dict[int, float] = {id(c): 0.0 for c in front}
    if n == 0:
        return distances
    if n <= 2:
        for c in front:
            distances[id(c)] = float("inf")
        return distances

    for field in ALL_OBJECTIVE_FIELDS:
        sorted_front = sorted(front, key=lambda c: getattr(c.objectives, field))
        min_value = getattr(sorted_front[0].objectives, field)
        max_value = getattr(sorted_front[-1].objectives, field)
        value_range = max_value - min_value

        distances[id(sorted_front[0])] = float("inf")
        distances[id(sorted_front[-1])] = float("inf")

        if value_range == 0:
            continue

        for k in range(1, n - 1):
            prev_value = getattr(sorted_front[k - 1].objectives, field)
            next_value = getattr(sorted_front[k + 1].objectives, field)
            contribution = (next_value - prev_value) / value_range
            if distances[id(sorted_front[k])] != float("inf"):
                distances[id(sorted_front[k])] += contribution

    return distances


_DEDUPE_EPSILON = 1e-9


def _dedupe_key(candidate: OptimizationCandidate) -> tuple:
    """Identity key used to collapse literal/near-identical duplicate
    candidates in a Pareto front (see `deduplicate_front`).

    Two candidates are considered "the same solution" if they share the
    same (vessel, route, speed, fuel) configuration -- since that tuple is
    what QPSO actually searches over, and identical configurations
    necessarily produce identical objective values (fuel_consumption,
    operating_cost, etc. are deterministic functions of it). Speed is
    rounded to guard against float noise (e.g. 8.000000001 vs 8.0) that
    would otherwise be treated as a distinct particle position even though
    it is effectively the same optimum.
    """
    return (
        candidate.vessel,
        candidate.route,
        round(candidate.speed, 6),
        candidate.fuel,
    )


def deduplicate_front(front: list[OptimizationCandidate]) -> list[OptimizationCandidate]:
    """Collapse literal/near-identical duplicate candidates out of a Pareto
    front before it is presented to a user.

    Background: QPSO's swarm can (and commonly does) converge multiple
    particles onto the same or a near-identical optimum. When that
    happens, `fast_non_dominated_sort` is *correct* to place all of those
    identical twins into front 0 -- none of them dominates another,
    because they're the same point. But correctness for ranking purposes
    is not the same as usefulness for decision support: showing an
    operator the same (vessel, route, speed, fuel) configuration three
    times in a row is noise, not additional optimal information.

    This is a standard Pareto-set post-processing step (deduplication of
    the returned front), kept separate from `fast_non_dominated_sort`
    itself so the sort's dominance semantics stay untouched -- dedup only
    affects what is ultimately presented.

    A candidate is treated as a duplicate of an already-kept
    representative if EITHER:
      - they share the same (vessel, route, speed, fuel) configuration
        (`_dedupe_key`) -- the dimensions QPSO actually searches over, so
        an identical configuration necessarily reproduces identical
        objectives; or
      - their objective values are equal within a tiny floating-point
        epsilon (`_objectives_close`) -- catching the (rarer) case of two
        different configurations that just happen to score identically,
        which is equally uninteresting to show twice.

    The first representative encountered is kept and input order is
    preserved for survivors; this is O(n^2) in front size, which is fine
    since Pareto fronts presented to users are small (tens of candidates,
    not thousands).
    """
    kept: list[OptimizationCandidate] = []

    for candidate in front:
        candidate_key = _dedupe_key(candidate)
        is_duplicate = any(
            candidate_key == _dedupe_key(existing) or _objectives_close(existing.objectives, candidate.objectives)
            for existing in kept
        )
        if not is_duplicate:
            kept.append(candidate)

    return kept


def _objectives_close(a: ObjectiveValues | None, b: ObjectiveValues | None) -> bool:
    """True if two ObjectiveValues are identical within a tiny
    floating-point epsilon on every field (or both None)."""
    if a is None or b is None:
        return a is b
    return all(
        abs(getattr(a, field) - getattr(b, field)) <= _DEDUPE_EPSILON
        for field in ALL_OBJECTIVE_FIELDS
    )


def get_pareto_front(candidates: list[OptimizationCandidate]) -> list[OptimizationCandidate]:
    """Convenience function: run the full NSGA-II sort and return just
    front 0 (the Pareto-optimal set), deduplicated (see
    `deduplicate_front`) and sorted by crowding distance descending (most
    diverse/spread-out solutions first) for presentation purposes -- e.g.
    showing a demo user a handful of maximally distinct tradeoffs rather
    than a cluster of near-identical (or literally identical) ones.
    """
    fronts = fast_non_dominated_sort(candidates)
    if not fronts:
        return []

    front_0 = deduplicate_front(fronts[0])
    distances = crowding_distance(front_0)
    return sorted(front_0, key=lambda c: distances[id(c)], reverse=True)
