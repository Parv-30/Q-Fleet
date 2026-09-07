"""Objective evaluation: (vessel, route, speed, fuel, prediction, emission)
-> ObjectiveValues, the six-objective vector NSGA-II ranks a population on.

fuel_consumption, operating_cost, and lifecycle_ghg are read straight off
the upstream services' responses -- prediction-service and
emissions-service already own those calculations (see
prediction-service/app/physics_model.py and
emissions-service/app/emission_factors.py), so this module does not
recompute or second-guess them.

reliability, cargo_satisfaction, and fleet_utilization have no upstream
service or "ground truth" anywhere in this codebase -- optimization-service
is the only place they are ever computed, so this module defines them from
scratch. Each is a deliberately simple, DOCUMENTED proxy formula (in the
same spirit as physics_model.py's and emission_factors.py's documented
assumptions), not a certified model. All three are always in [0, 1].

--- reliability's optional deadline-aware schedule-risk term -------------
When a caller supplies BOTH `delivery_deadline` and `departure_time` (see
evaluate_candidate's optional parameters -- fed from
OptimizationConstraints.delivery_deadline, an OPTIONAL "operational
constraint" per explicit user instruction), reliability also folds in a
schedule-risk signal: predicted arrival = departure_time +
prediction.voyage_time (hours). If that predicted arrival is AFTER the
deadline, reliability is reduced proportionally to how many hours late the
candidate would be (see `_LATE_PENALTY_PER_HOUR`) -- a real "will I make my
delivery commitment" measure, not a token gesture. If the predicted
arrival is comfortably before the deadline, no penalty is applied (a
small, deliberately simple design choice: rewarding "more margin is
better" would just double-count what a lower voyage_time already achieves
via speed/route choice, so margin itself gives no bonus, only lateness
gives a penalty). When either `delivery_deadline` or `departure_time` is
omitted (the default), reliability's calculation is BYTE-FOR-BYTE
identical to before this term existed -- this is a strict backward-
compatible addition, not a behavior change for the common case.

--- reliability -------------------------------------------------------
Voyage risk goes up with weather severity (a rougher sea state is more
likely to cause delays, mechanical stress, or safety incidents) and with
running a vessel close to or above its practical maximum safe speed in bad
weather (overstressing the engine/hull in a seaway is a well-known real
cause of unplanned repairs and schedule risk). Modeled as a 1.0 baseline
reduced by:
  - a weather penalty proportional to weather_severity (a calmer voyage is
    inherently more reliable/predictable than a stormy one), and
  - a speed-margin penalty that only kicks in once requested speed exceeds
    a weather-adjusted safe-speed ceiling (running well below the ceiling
    costs nothing; pushing past it costs reliability, and pushing further
    past it costs more).
The weather-adjusted ceiling itself drops as weather worsens (a vessel's
"safe" speed in a storm is lower than in calm seas), so the same
speed_knots value can be fine in calm weather and risky in a storm --
this couples the two penalty terms the way real seakeeping practice does,
rather than treating speed risk as a flat, weather-independent constant.

--- cargo_satisfaction --------------------------------------------------
How well the chosen vessel's load matches the voyage's actual cargo
demand, i.e. is cargo_utilization in a sensible operating band -- not too
empty (running a giant near-empty ship on a small load wastes capacity
that could have gone to a smaller/other vessel and is commercially poor
use of the asset for this voyage) and not pinned at/near 1.0 (running at
the very edge of nameplate capacity leaves no margin for last-minute
cargo changes, stability trim adjustments, or measurement error, and in
practice ships are rarely loaded to their literal 100% limit). Modeled as
a peak at a "sweet spot" target utilization band, falling off linearly
toward 0 at 0% utilization and toward a lower-but-nonzero floor at 100%
utilization.

--- fleet_utilization ---------------------------------------------------
Distinct from cargo_satisfaction, which asks "is THIS voyage's cargo load
sensible for the chosen vessel". fleet_utilization instead asks "is the
FLEET's asset capacity being used efficiently across the fleet" -- from a
fleet-operator's viewpoint, a vessel sitting mostly empty is idle
capital/capacity, full stop, regardless of whether the requested cargo
happened to be a good "fit" in the cargo_satisfaction sense. It is
therefore modeled as directly, monotonically increasing in
cargo_utilization (more utilization = more efficient use of the asset,
with no fall-off penalty at the high end the way cargo_satisfaction has),
capturing the classic fleet-management tradeoff against
cargo_satisfaction: NSGA-II can genuinely trade one off against the other
because they reward different utilization levels rather than being a
disguised duplicate of each other.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from common.schemas import EmissionResponse, FuelType, ObjectiveValues, PredictionResponse

# --- reliability tuning constants -----------------------------------------
# Weather penalty: at maximum weather_severity (1.0), reliability loses up
# to this much just from sea state, before any speed penalty.
_WEATHER_PENALTY_WEIGHT = 0.5

# Safe-speed ceiling in calm weather (weather_severity=0), knots -- a
# generic representative "full sea speed" for commercial cargo vessels.
_CALM_SAFE_SPEED_KNOTS = 22.0
# In the worst modeled weather (weather_severity=1.0), the safe-speed
# ceiling drops to this floor -- ships slow down substantially in heavy
# seas both for safety and to avoid slamming/structural loads.
_STORM_SAFE_SPEED_KNOTS = 12.0
# Reliability lost per knot requested above the weather-adjusted ceiling.
_OVERSPEED_PENALTY_PER_KNOT = 0.05

# Reliability lost per hour a candidate's predicted arrival would be past
# the delivery_deadline (only applied when both delivery_deadline and
# departure_time are given -- see module docstring). Calibrated so a full
# day late (24h) costs 0.24, a substantial-but-not-instantly-zeroing
# penalty -- meaningfully worse than any weather/overspeed penalty alone,
# without letting a single very-late candidate go negative before the
# final clamp.
_LATE_PENALTY_PER_HOUR = 0.01

# --- cargo_satisfaction tuning constants ----------------------------------
# "Sweet spot" utilization band: satisfaction peaks at 1.0 across this
# range (neither wastefully empty nor pinned at the capacity edge).
_TARGET_UTILIZATION_LOW = 0.6
_TARGET_UTILIZATION_HIGH = 0.85
# Satisfaction floor at 100% utilization (not 0 -- a full ship is still a
# usable, revenue-generating outcome, just without operating margin).
_FULL_UTILIZATION_SATISFACTION_FLOOR = 0.6


def _schedule_risk_penalty(
    voyage_time_hours: float,
    delivery_deadline: datetime | None,
    departure_time: datetime | None,
) -> float:
    """Hours-late-scaled reliability penalty, or 0.0 when deadline-aware
    scoring isn't in play (either optional parameter missing) -- see
    module docstring. Both must be given for this to do anything, since a
    deadline with no departure reference (or vice versa) can't be compared
    against a predicted arrival time at all.
    """
    if delivery_deadline is None or departure_time is None:
        return 0.0

    predicted_arrival = departure_time + timedelta(hours=voyage_time_hours)
    hours_late = (predicted_arrival - delivery_deadline).total_seconds() / 3600.0
    if hours_late <= 0.0:
        return 0.0
    return hours_late * _LATE_PENALTY_PER_HOUR


def _reliability(
    weather_severity: float,
    speed_knots: float,
    *,
    voyage_time_hours: float | None = None,
    delivery_deadline: datetime | None = None,
    departure_time: datetime | None = None,
) -> float:
    weather_penalty = _WEATHER_PENALTY_WEIGHT * weather_severity

    safe_speed_ceiling = _CALM_SAFE_SPEED_KNOTS - weather_severity * (
        _CALM_SAFE_SPEED_KNOTS - _STORM_SAFE_SPEED_KNOTS
    )
    overspeed = max(speed_knots - safe_speed_ceiling, 0.0)
    overspeed_penalty = overspeed * _OVERSPEED_PENALTY_PER_KNOT

    schedule_penalty = 0.0
    if voyage_time_hours is not None:
        schedule_penalty = _schedule_risk_penalty(voyage_time_hours, delivery_deadline, departure_time)

    reliability = 1.0 - weather_penalty - overspeed_penalty - schedule_penalty
    return max(0.0, min(1.0, reliability))


def _cargo_satisfaction(cargo_utilization: float) -> float:
    u = max(0.0, min(1.0, cargo_utilization))

    if u < _TARGET_UTILIZATION_LOW:
        # Linear ramp from 0 (empty) up to 1.0 at the band's low edge.
        return u / _TARGET_UTILIZATION_LOW
    if u <= _TARGET_UTILIZATION_HIGH:
        # Inside the sweet spot: full satisfaction.
        return 1.0
    # Linear fall-off from 1.0 at the band's high edge down to the floor
    # at u=1.0.
    span = 1.0 - _TARGET_UTILIZATION_HIGH
    fraction_past_band = (u - _TARGET_UTILIZATION_HIGH) / span
    return 1.0 - fraction_past_band * (1.0 - _FULL_UTILIZATION_SATISFACTION_FLOOR)


def _fleet_utilization(cargo_utilization: float) -> float:
    # Directly monotonic in utilization -- see module docstring for why
    # this is intentionally distinct from cargo_satisfaction's peaked
    # shape.
    return max(0.0, min(1.0, cargo_utilization))


def evaluate_candidate(
    vessel: str,
    route: str,
    speed: float,
    fuel: FuelType,
    prediction: PredictionResponse,
    emission: EmissionResponse,
    *,
    weather_severity: float,
    cargo_utilization: float,
    delivery_deadline: datetime | None = None,
    departure_time: datetime | None = None,
) -> ObjectiveValues:
    """Compute the six-objective ObjectiveValues vector for one candidate.

    `weather_severity` and `cargo_utilization` are the same
    ProcessedFeatures-derived inputs used to produce `prediction`/
    `emission` in the first place -- callers already have these on hand
    from building the candidate's ProcessedFeatures, so they are passed
    through rather than re-derived here.

    `delivery_deadline`/`departure_time` are OPTIONAL (see module
    docstring's deadline-aware reliability section) -- when both are
    given, reliability incorporates a real schedule-risk penalty derived
    from comparing `departure_time + prediction.voyage_time` against
    `delivery_deadline`. When either is omitted (the default), reliability
    is computed exactly as it was before this parameter pair existed.
    """
    return ObjectiveValues(
        fuel_consumption=prediction.fuel_consumption,
        operating_cost=prediction.operating_cost,
        lifecycle_ghg=emission.lifecycle_ghg,
        reliability=_reliability(
            weather_severity,
            speed,
            voyage_time_hours=prediction.voyage_time,
            delivery_deadline=delivery_deadline,
            departure_time=departure_time,
        ),
        cargo_satisfaction=_cargo_satisfaction(cargo_utilization),
        fleet_utilization=_fleet_utilization(cargo_utilization),
    )
