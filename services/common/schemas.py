"""Canonical data contract shared by every Q-Fleet AI microservice.

Each model below marks one hand-off point in the pipeline:

    VoyageRequest -> [data-service]      -> ProcessedFeatures
    ProcessedFeatures -> [prediction-service] -> PredictionResponse
    PredictionResponse -> [emissions-service] -> EmissionResponse
    (vessel, route, speed, fuel) -> [optimization-service] -> OptimizationCandidate

Every service imports these models rather than redefining its own copy, so
the shape of data flowing between services can only change in one place.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class VesselType(str, Enum):
    CONTAINER = "container"
    BULK_CARRIER = "bulk_carrier"
    TANKER = "tanker"
    RO_RO = "ro_ro"
    GENERAL_CARGO = "general_cargo"


class FuelType(str, Enum):
    HFO = "hfo"
    DIESEL = "diesel"
    LNG = "lng"
    METHANOL = "methanol"
    HYDROGEN = "hydrogen"
    AMMONIA = "ammonia"


class VoyageRequest(BaseModel):
    """User-controlled + automatically-obtained parameters: the raw input
    to the pipeline, for one vessel on one route leg."""

    vessel_id: str
    vessel_type: VesselType

    route_id: str
    origin: str
    destination: str
    distance_km: float = Field(gt=0)

    cargo_tonnes: float = Field(ge=0)
    cargo_utilization: float = Field(ge=0, le=1)

    speed_knots: float = Field(gt=0)

    fuel_type: FuelType

    wind_speed: float = Field(ge=0, description="m/s")
    wave_height: float = Field(ge=0, description="meters")
    temperature: float = Field(description="degrees Celsius")
    current_speed: float = Field(description="knots, signed (+/- along route heading)")

    delivery_deadline: datetime | None = None


class OptimizationConstraints(BaseModel):
    """Optional real-voyage-context filters a user can supply to
    optimization-service's POST /optimize, matching the project's
    architecture diagram's input categories: Vessel, Route, Cargo, Speed,
    Fuel, Weather, Operational constraints.

    EVERY field defaults to None (fully unconstrained) -- per explicit user
    instruction, nothing here is required, including delivery_deadline
    ("operational constraints" in the architecture diagram). QPSO's hard-
    filter semantics (see optimization-service/app/qpso.py) are:

        - a field left as None stays FULLY OPEN: QPSO searches the whole
          catalog/range for that dimension, exactly as it did before this
          model existed.
        - a field given a single/exact value PINS that dimension: every
          candidate QPSO proposes for the whole run uses exactly that
          value, no drift, no cross-over into alternatives.
        - a field given a subset/range NARROWS that dimension: QPSO
          searches only within the given subset/range, never outside it.

    --- Vessel filtering: vessel_id vs. vessel_type -------------------------
    Both are supported as independent, combinable optional filters because
    they answer different user questions: "use exactly THIS vessel" vs.
    "use any vessel of THIS class, QPSO can still pick among them". If a
    user gives vessel_id, that hard-pins to one exact FLEET_CATALOG entry
    (vessel_type is then redundant but harmless if also given, as long as
    it's consistent). If only vessel_type is given, the vessel dimension
    narrows to every FLEET_CATALOG entry of that type (still an open
    sub-search, not a pin) -- this matches "if user specifies a vessel,
    QPSO only considers that vessel" while still being useful for a user
    who only cares about the class of ship, not one specific hull.

    --- Route filtering: origin/destination vs. route_id --------------------
    origin+destination (both given) is the PRIMARY user-facing way to
    request routing, per explicit user example ("mum to amsterdam ... suez
    canal or around africa ... qpso will still search every possible
    config"): this triggers a real data-service compute_route() lookup and
    narrows QPSO's route dimension to ONLY the 1-2 real RouteOptions
    returned for that exact port pair (e.g. Suez vs. Cape) -- QPSO still
    freely chooses between them, this is a narrow, not a pin, unless
    compute_route() happens to return only one option. route_id is a more
    direct/advanced pin (exact single route, e.g. from a previous lookup's
    result) for callers who already know which specific route they want.
    Giving only one of origin/destination (not both) has no routing effect
    since compute_route() needs both ends -- the route dimension stays
    fully open.

    --- cargo_tonnes ---------------------------------------------------------
    A single hard value (not a range), matching "cargo" being one input in
    the architecture diagram -- this is not a QPSO search dimension at all
    (QPSO doesn't search over cargo), it directly overrides the
    _ASSUMED_CARGO_UTILIZATION-derived cargo_tonnes used to build each
    candidate's VoyageRequest (see optimizer.py).

    --- Speed: speed_knots vs. speed_min/max_knots --------------------------
    speed_knots is "I want exactly this speed" (pins the continuous speed
    dimension to a constant). speed_min_knots/speed_max_knots is "somewhere
    in this range" (narrows QPSO's continuous search bounds). If
    speed_knots is given, it is treated as speed_min_knots ==
    speed_max_knots == speed_knots internally (see qpso.py) -- giving both
    speed_knots and an inconsistent min/max is not validated here (last
    word goes to whichever optimizer.py/qpso.py reads first); callers
    should give one or the other, not conflicting values in both.

    --- allowed_fuels ---------------------------------------------------------
    A subset filter over FuelType. None means every fuel in the catalog is
    open to search, exactly as today. A single-element list is a de facto
    pin (only one choice), which the hard-filter machinery treats
    identically to a genuine pin -- no special-casing needed.

    --- Weather fields --------------------------------------------------------
    wind_speed / wave_height / temperature / current_speed. UPDATED HONESTY
    NOTE (live weather sub-phase): when these are omitted AND origin+
    destination ARE both given, optimizer.py now fetches REAL live weather
    (Open-Meteo forecast + marine APIs, sampled at real waypoints along the
    actual computed route -- see data-service/app/weather.py) and
    summarizes it into these four fields (see
    summarize_weather_for_optimizer). Only current_speed (ocean current)
    stays a documented placeholder constant, since no free/no-key live
    source for it exists. When origin/destination are NOT both given
    (fully unconstrained route search), there is no real route to sample
    weather along, so optimizer.py falls back to the pre-existing
    hardcoded `_ASSUMED_WEATHER` "typical moderate conditions" placeholder
    for ALL four fields, exactly as before this sub-phase. Providing these
    fields directly always overrides whatever source (live or placeholder)
    would otherwise be used, for callers who already have their own
    numbers.

    --- delivery_deadline -----------------------------------------------------
    Optional (per explicit user instruction: "i want operational constraint
    as optional"). When given, optimizer.py compares each candidate's
    predicted arrival (departure "now" + predicted voyage_time) against
    this deadline and objectives.py's reliability calculation applies a
    real schedule-risk penalty when a candidate would miss it. When absent,
    reliability's calculation is completely unchanged from before this
    model existed (see objectives.py's docstring).
    """

    # --- Vessel ---
    vessel_id: str | None = None
    vessel_type: VesselType | None = None

    # --- Route ---
    origin: str | None = None
    destination: str | None = None
    route_id: str | None = None

    # --- Cargo ---
    cargo_tonnes: float | None = Field(default=None, ge=0)

    # --- Speed ---
    speed_knots: float | None = Field(default=None, gt=0)
    speed_min_knots: float | None = Field(default=None, gt=0)
    speed_max_knots: float | None = Field(default=None, gt=0)

    # --- Fuel ---
    allowed_fuels: list[FuelType] | None = None

    # --- Weather (placeholder overrides -- see docstring's HONESTY NOTE) ---
    wind_speed: float | None = Field(default=None, ge=0, description="m/s")
    wave_height: float | None = Field(default=None, ge=0, description="meters")
    temperature: float | None = Field(default=None, description="degrees Celsius")
    current_speed: float | None = Field(default=None, description="knots, signed")

    # --- Operational constraints (optional) ---
    delivery_deadline: datetime | None = None


class CurrentWeather(BaseModel):
    """Live "right now" conditions at one lat/lon, as returned by Open-Meteo's
    standard forecast API (temperature/wind) + Marine API (wave height) --
    see data-service/app/weather.py's module docstring for the exact
    endpoints and units used. `current_speed` (ocean current, not wind) has
    no free/no-key live source available -- it is a documented constant
    placeholder here too, kept consistent with the single-value shape
    VoyageRequest/OptimizationConstraints already use for it.
    """

    temperature: float = Field(description="degrees Celsius")
    wind_speed: float = Field(ge=0, description="m/s")
    wave_height: float = Field(ge=0, description="meters")
    current_speed: float = Field(description="knots, signed -- placeholder, no live source (see module docstring)")


class DailyForecast(BaseModel):
    """One day of Open-Meteo's 5-day daily forecast for one lat/lon."""

    date: str = Field(description="ISO 8601 date, e.g. 2026-09-09")
    temp_max: float = Field(description="degrees Celsius")
    temp_min: float = Field(description="degrees Celsius")
    wind_speed_max: float = Field(ge=0, description="m/s")
    wave_height_max: float = Field(ge=0, description="meters")


class WeatherSample(BaseModel):
    """Live current conditions + 5-day forecast at one waypoint along a
    computed route (see data-service/app/routing.py's sample_waypoints and
    app/weather.py's fetch_weather_along_route). One of these is produced
    per sampled point along the route, roughly every 500-1000km, so the
    full `list[WeatherSample]` for a voyage shows how weather varies along
    its real path -- both for feeding optimization-service's optimizer
    (see summarize_weather_for_optimizer) and for a future frontend map
    visualization (GET /weather on data-service exposes this list as-is).
    """

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    current: CurrentWeather
    forecast: list[DailyForecast]
    error: str | None = Field(
        default=None,
        description="Set (current/forecast then hold documented fallback values) when "
        "Open-Meteo was unreachable or returned an unexpected response for this point -- "
        "see fetch_weather_for_point's docstring.",
    )


class ProcessedFeatures(BaseModel):
    """Data-processing output: cleaned, normalized, engineered features.

    This is data-service's primary deliverable and prediction-service's
    model input — the one and only place these fields are computed.
    """

    distance_km: float
    speed_knots: float
    speed_cubed: float
    cargo_tonnes: float
    cargo_utilization: float
    weather_severity: float = Field(ge=0, le=1)

    # One-hot encoded categorical features, one flat 0/1 field per enum
    # member (exactly one member of each group is 1 per record). Both
    # vessel_type and fuel_type are genuinely UNORDERED categories with no
    # physical ranking between members -- a single ordinal int (the
    # previous `vessel_type_encoded`/`fuel_type_encoded: int` design)
    # imposes a fake numeric ordering (e.g. HFO < DIESEL < LNG < METHANOL <
    # HYDROGEN < AMMONIA) that has no physical meaning. XGBoost's trees CAN
    # still split on an ordinal-encoded category, but a false ordering
    # makes generalization/extrapolation between categories worse than it
    # should be -- this was diagnosed as a contributing factor to bad
    # low-speed LNG/hydrogen fuel_consumption predictions. A flat set of
    # named boolean fields (rather than e.g. a nested `dict[str, int]`) is
    # used because it is more XGBoost-friendly (each field is its own
    # feature-matrix column with a stable name) and easier to reason about
    # column-by-column than a nested structure.
    vessel_type_container: int = Field(ge=0, le=1)
    vessel_type_bulk_carrier: int = Field(ge=0, le=1)
    vessel_type_tanker: int = Field(ge=0, le=1)
    vessel_type_ro_ro: int = Field(ge=0, le=1)
    vessel_type_general_cargo: int = Field(ge=0, le=1)

    fuel_type_hfo: int = Field(ge=0, le=1)
    fuel_type_diesel: int = Field(ge=0, le=1)
    fuel_type_lng: int = Field(ge=0, le=1)
    fuel_type_methanol: int = Field(ge=0, le=1)
    fuel_type_hydrogen: int = Field(ge=0, le=1)
    fuel_type_ammonia: int = Field(ge=0, le=1)


class PredictionResponse(BaseModel):
    """ML-generated outputs (prediction-service)."""

    fuel_consumption: float = Field(ge=0, description="tonnes")
    operating_cost: float = Field(ge=0, description="currency units")
    voyage_time: float = Field(ge=0, description="hours")


class EmissionResponse(BaseModel):
    """Emission-model output (emissions-service)."""

    lifecycle_ghg: float = Field(ge=0, description="tonnes CO2-equivalent")


class ObjectiveValues(BaseModel):
    """The six objectives NSGA-II ranks a population on."""

    fuel_consumption: float
    operating_cost: float
    lifecycle_ghg: float
    reliability: float
    cargo_satisfaction: float
    fleet_utilization: float


class OptimizationCandidate(BaseModel):
    """One member of optimization-service's QPSO population.

    Populated progressively: QPSO proposes (vessel, route, speed, fuel);
    optimization-service then calls prediction-service and
    emissions-service to fill in the response fields before NSGA-II
    ranks the scored population into a Pareto front.
    """

    vessel: str
    route: str
    speed: float
    fuel: FuelType

    prediction: PredictionResponse | None = None
    emission: EmissionResponse | None = None
    objectives: ObjectiveValues | None = None
