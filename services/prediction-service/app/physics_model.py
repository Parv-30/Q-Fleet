"""Physically-grounded label generator: VoyageRequest -> PredictionResponse.

Produces training targets (fuel_consumption, operating_cost, voyage_time) for
synthetic voyages so an XGBoost model (built elsewhere) has something
physically credible to learn from, rather than arbitrary numbers. It is
deliberately NOT a certified naval-architecture tool -- every constant below
is a documented, order-of-magnitude engineering approximation, not a value
pulled from a specific towing-tank test or class-society formula. The goal is
internal consistency (higher speed -> more fuel, roughly cubically; rougher
seas -> more fuel; heavier fuels burn more mass for the same energy; a
following current shortens a voyage) so a model trained on these labels
learns the right *shape* of the problem.

Overall approach, tying together the naval-architecture concepts used:

1. Admiralty coefficient law: propulsion power P is approximated as
   proportional to displacement^(2/3) * speed^3. This is the classical
   "Admiralty coefficient" relationship used for early-stage powering
   estimates -- resistance grows with wetted-surface-ish area (~displacement
   ^(2/3)) and required power grows with the cube of speed for a given hull.
2. Effective displacement for resistance purposes is interpolated between a
   ballast ("light") and full-load displacement based on cargo_utilization,
   because an empty ship still has real hull resistance -- it does not
   linearly track cargo_tonnes down to zero.
3. Added resistance in waves: rough seas increase the power a ship must
   develop to hold speed. This follows the well-documented ITTC-style
   "added resistance in waves" concept -- we do not reproduce an ITTC
   seakeeping calculation, only its qualitative/quantitative order of
   magnitude (roughly a 10-30% power penalty in severe conditions).
4. Fuel-type-specific specific fuel consumption (SFC) and relative energy
   density convert required energy (kWh) into burned fuel mass (tonnes),
   since a gram of ammonia and a gram of HFO do not deliver the same energy.
5. Voyage time uses distance over an effective speed that nets out the
   along-route current component -- a following current shortens transit,
   a head current lengthens it, exactly like real navigation.
6. Operating cost combines the fuel bill (fuel mass x fuel price/tonne) with
   a flat hourly charter/crew rate x voyage time -- a simplification of a
   real opex model, documented as such.

Limitations: no real hull-form data, no actual resistance curves, no
propeller/engine efficiency curves, no route-specific currents/traffic, and
the fuel-type economics are relative orderings rather than live bunker
prices. This is sufficient for generating a large, physically-plausible,
internally-consistent synthetic label set -- not for real voyage planning.
"""

from __future__ import annotations

from common.schemas import FuelType, PredictionResponse, VesselType, VoyageRequest

# ---------------------------------------------------------------------------
# Displacement model
# ---------------------------------------------------------------------------

# Cargo utilization can theoretically be 0 (empty ship). Below this floor,
# cargo_tonnes / utilization stops being a usable DWT estimate: cargo_tonnes
# is also ~0 in that case, so the ratio collapses towards 0 and understates
# an empty-but-real ship's actual hull size, rather than blowing up. Below
# the floor we instead fall back to a representative per-vessel-type DWT
# capacity (_REPRESENTATIVE_DWT), since a real ship's size does not shrink to
# nothing just because it is sailing empty on this particular leg.
_UTILIZATION_FLOOR_FOR_DWT_BACKOUT = 0.05

# Representative DWT capacity (tonnes) per vessel type, used only as the
# fallback above. Anchored to the same rough real-world size bands used
# elsewhere in this project's data pipeline (data-service's dwt calibration)
# -- mid-range figures for a "typical" ship of each type, not a precise fleet
# average.
_REPRESENTATIVE_DWT: dict[VesselType, float] = {
    VesselType.CONTAINER: 80000.0,
    VesselType.BULK_CARRIER: 120000.0,
    VesselType.TANKER: 110000.0,
    VesselType.RO_RO: 25000.0,
    VesselType.GENERAL_CARGO: 15000.0,
}

# lightship_fraction: displacement -> DWT ratio approximation
# (DWT / full-load displacement), i.e. displacement = dwt / lightship_fraction.
# A ship's DWT is typically ~60-75% of its full-load displacement; the
# remainder is the lightship weight (steel, machinery, outfit) that doesn't
# scale with cargo. Container ships are hull-and-machinery-heavy relative to
# their cargo capacity (faster hulls, more powerful engines, reefer plant,
# lashing gear) so they run a leaner DWT/displacement fraction than bulk
# carriers or tankers, which are comparatively simple steel boxes optimized
# to maximize deadweight per ton of steel. These are documented, defensible
# approximations for this synthetic-label generator, not precise naval
# architecture figures for any specific class of ship.
_DWT_TO_DISPLACEMENT_FRACTION: dict[VesselType, float] = {
    VesselType.CONTAINER: 0.62,
    VesselType.BULK_CARRIER: 0.75,
    VesselType.TANKER: 0.73,
    VesselType.RO_RO: 0.60,
    VesselType.GENERAL_CARGO: 0.68,
}

# Effective displacement for hull-resistance purposes is interpolated between
# a "light" (near-ballast) condition and the full-load displacement, because
# an unladen ship still displaces water and generates real wetted-hull drag.
# Modeling this as a fraction of full-load displacement (rather than as a
# function of cargo_tonnes, which would go to exactly zero) is a standard,
# defensible linear simplification for a synthetic-data generator.
_LIGHT_DISPLACEMENT_FRACTION = 0.55  # ballast displacement as a fraction of full load


def _full_load_displacement(request: VoyageRequest) -> float:
    """Back out an approximate full-load displacement (tonnes) from cargo.

    Below ``_UTILIZATION_FLOOR_FOR_DWT_BACKOUT``, cargo_tonnes / utilization
    is no longer a reliable size estimate (an empty ship reports
    cargo_tonnes=0 regardless of how big it actually is), so DWT falls back
    to a representative per-vessel-type capacity instead.
    """
    if request.cargo_utilization < _UTILIZATION_FLOOR_FOR_DWT_BACKOUT:
        dwt = _REPRESENTATIVE_DWT[request.vessel_type]
    else:
        dwt = request.cargo_tonnes / request.cargo_utilization
    fraction = _DWT_TO_DISPLACEMENT_FRACTION[request.vessel_type]
    return dwt / fraction


def _effective_displacement(request: VoyageRequest) -> float:
    """Linear interpolation between light and full-load displacement, driven
    by cargo_utilization, so an empty ship still carries hull resistance."""
    full_load = _full_load_displacement(request)
    light = full_load * _LIGHT_DISPLACEMENT_FRACTION
    return light + (full_load - light) * request.cargo_utilization


# ---------------------------------------------------------------------------
# Admiralty coefficient power law
# ---------------------------------------------------------------------------

# Admiralty coefficient: P = displacement^(2/3) * speed^3 / ADMIRALTY_COEFFICIENT.
#
# The Admiralty coefficient is fundamentally a hull-form-and-speed-regime
# dependent figure, not a universal constant -- a single flat value across
# all vessel types was a simplification that materially over-predicted power
# (and hence fuel) for slow, full-bodied hulls. Since power is INVERSELY
# proportional to this coefficient, a HIGHER coefficient means LOWER power
# for the same displacement/speed, and vice versa:
#
# - CONTAINER ships are fast, fine-hulled (high length/beam, slender
#   waterlines) vessels designed to punch through resistance at 18-24 knot
#   service speeds -- they need relatively MORE power per unit of
#   displacement^(2/3)*speed^3, so they carry a LOWER coefficient. Real-world
#   validation confirmed the flat 450 already landed large container ships
#   correctly in the real 150-300 t/day range, so this value is kept.
# - BULK_CARRIER and TANKER hulls are slow (12-15 knot service speed), full-
#   bodied "steel boxes" optimized to maximize deadweight per ton of steel
#   rather than for speed -- classical Admiralty-coefficient tables put
#   slow full-hulled ships well above fast fine-hulled ones. Real-world
#   validation showed bulk carriers over-predicted by roughly 2.5-3x (~97
#   t/day predicted vs. 30-45 t/day real-world for Capesize ships), so the
#   coefficient is raised to roughly triple the container figure. Tankers
#   showed a smaller (1.8-3.6x) overshoot, so they sit between bulk carriers
#   and container ships.
# - RO_RO ships are faster and finer-hulled than bulk carriers (they carry
#   wheeled cargo and are built more like fast cargo/passenger hulls), so
#   they sit close to the container figure.
# - GENERAL_CARGO ships are slower and fuller than container ships but not
#   as extreme as bulk carriers/tankers, so they sit mid-range.
ADMIRALTY_COEFFICIENT: dict[VesselType, float] = {
    VesselType.CONTAINER: 450.0,
    VesselType.BULK_CARRIER: 1180.0,
    VesselType.TANKER: 850.0,
    VesselType.RO_RO: 480.0,
    VesselType.GENERAL_CARGO: 600.0,
}

# Added-resistance-in-waves coefficient: real ships see roughly a 10-30%
# increase in required power in rough seas, a well-documented effect in
# naval architecture (see e.g. ITTC's added-resistance-in-waves guidelines).
# We approximate the whole effect with one scalar applied to weather_severity
# (already a 0-1 index combining wind/wave/current -- see data-service's
# FeaturePipeline) rather than a full seakeeping calculation.
ADDED_RESISTANCE_COEFFICIENT = 0.28


def _weather_severity(request: VoyageRequest) -> float:
    """0-1 severity index, mirroring FeaturePipeline._weather_severity so the
    label generator's notion of "rough weather" matches the model's own
    training features. Recomputed locally (not imported) to keep this module
    self-contained and independent of data-service's normalization ceilings
    changing out from under it silently -- if they diverge, tests here will
    catch it.
    """
    max_wind_speed = 40.0  # m/s, ~ Beaufort 12
    max_wave_height = 14.0  # meters, phenomenal sea state
    max_current_speed = 5.0  # knots
    wind_component = min(abs(request.wind_speed) / max_wind_speed, 1.0)
    wave_component = min(abs(request.wave_height) / max_wave_height, 1.0)
    current_component = min(abs(request.current_speed) / max_current_speed, 1.0)
    return (wind_component + wave_component + current_component) / 3.0


def _required_power_kw(request: VoyageRequest) -> float:
    """Admiralty-law propulsion power, inflated for added resistance in waves."""
    displacement = _effective_displacement(request)
    coefficient = ADMIRALTY_COEFFICIENT[request.vessel_type]
    base_power = (displacement ** (2.0 / 3.0)) * (request.speed_knots ** 3) / coefficient
    weather_multiplier = 1.0 + _weather_severity(request) * ADDED_RESISTANCE_COEFFICIENT
    return base_power * weather_multiplier


# ---------------------------------------------------------------------------
# Fuel-type specific fuel consumption (SFC) and relative energy density
# ---------------------------------------------------------------------------

# (SFC_g_per_kWh, relative_energy_density) per fuel type.
#
# SFC (g/kWh) figures are standard, validated marine-engineering figures
# (HFO ~185 g/kWh and diesel ~180 g/kWh are textbook figures for large
# 2-stroke slow-speed marine diesels, confirmed correct in a prior
# validation pass and left unchanged here).
#
# relative_energy_density is now derived directly from real net calorific
# values (NCV, MJ/kg) published in ISO 8217 (marine fuel quality standard)
# and DNV/IMO technical references, divided by HFO's NCV so HFO = 1.0 by
# definition. Real NCV figures used (MJ/kg):
#   HFO: 40.2 | MDO/MGO (diesel): 42.7 | LNG: 49.2 | Methanol: 19.9 |
#   Ammonia (LHV): 18.6 | Hydrogen (LHV): 120.0
# These are the SAME real calorific-value figures used in emissions-
# service's emission_factors.py, so both services agree on the physical
# reality of each fuel's energy content rather than carrying two
# independently-guessed numbers:
#
# - HFO: baseline, relative_energy_density = 40.2 / 40.2 = 1.00.
# - Diesel (MDO/MGO): a lighter distillate than HFO, genuinely higher NCV
#   (42.7 MJ/kg) -- relative_energy_density = 42.7 / 40.2 = 1.06.
# - LNG: mostly methane, genuinely one of the highest gravimetric calorific
#   values among marine fuels (49.2 MJ/kg) -- relative_energy_density =
#   49.2 / 40.2 = 1.22, meaning LNG needs LESS mass for the same energy
#   (volumetric energy density is much lower than HFO's, relevant for tank
#   sizing, not modeled here).
# - Methanol: genuinely roughly HALF of HFO's calorific value (19.9 MJ/kg),
#   a very well-documented figure in marine-fuel-transition literature --
#   relative_energy_density = 19.9 / 40.2 = 0.495, so an engine burning
#   methanol needs roughly double the mass for equivalent energy.
# - Ammonia: genuinely low calorific value (18.6 MJ/kg LHV), similar order
#   to methanol -- relative_energy_density = 18.6 / 40.2 = 0.463. It also
#   burns less efficiently in current dual-fuel engine designs, reflected
#   in a higher SFC.
# - Hydrogen: genuinely the highest gravimetric calorific value of any
#   chemical fuel (120 MJ/kg LHV, roughly 3x HFO's -- real physics, not an
#   approximation) -- relative_energy_density = 120.0 / 40.2 = 2.99. Despite
#   a high SFC in g/kWh (fuel-cell/combustion hydrogen systems need more
#   grams of throughput per kWh at the engine than a diesel), the actual
#   fuel MASS burned is low once this energy-density adjustment is applied
#   (very low volumetric density is why hydrogen ships need large tanks --
#   not modeled here).
_FUEL_PROPERTIES: dict[FuelType, tuple[float, float]] = {
    # fuel_type: (SFC_g_per_kWh, relative_energy_density vs HFO = 1.0)
    FuelType.HFO: (185.0, 1.00),
    FuelType.DIESEL: (180.0, 1.06),
    FuelType.LNG: (150.0, 1.22),
    FuelType.METHANOL: (350.0, 0.495),
    FuelType.AMMONIA: (410.0, 0.463),
    FuelType.HYDROGEN: (450.0, 2.99),
}


def _fuel_mass_tonnes(request: VoyageRequest, power_kw: float, voyage_time_hours: float) -> float:
    """Energy (kWh) x SFC (g/kWh) / relative_energy_density -> fuel mass (t).

    Dividing by relative_energy_density expresses "a denser fuel needs less
    mass to deliver the same energy" -- e.g. hydrogen's very high energy
    density per unit mass sharply cuts the mass burned despite its high
    nominal SFC figure, while methanol's low energy density roughly doubles
    the mass burned relative to its SFC alone.
    """
    energy_kwh = power_kw * voyage_time_hours
    sfc_g_per_kwh, relative_energy_density = _FUEL_PROPERTIES[request.fuel_type]
    fuel_grams = energy_kwh * sfc_g_per_kwh / relative_energy_density
    return fuel_grams / 1_000_000.0  # g -> tonnes


# ---------------------------------------------------------------------------
# Voyage time (distance over current-adjusted effective speed)
# ---------------------------------------------------------------------------

_KNOTS_TO_KMH = 1.852  # 1 knot = 1.852 km/h, exact by definition


def _voyage_time_hours(request: VoyageRequest) -> float:
    """distance_km / effective_speed_kmh, where effective speed nets out the
    along-route current: a following current (positive current_speed, per
    VoyageRequest's sign convention) shortens transit, a head current
    lengthens it."""
    ship_speed_kmh = request.speed_knots * _KNOTS_TO_KMH
    current_kmh = request.current_speed * _KNOTS_TO_KMH
    effective_speed_kmh = ship_speed_kmh + current_kmh

    # Guard against a current so strongly adverse it would reverse or stall
    # progress outright (unrealistic but not excluded by VoyageRequest's
    # validation) -- floor effective speed well above zero rather than
    # letting voyage_time blow up to infinity or go negative.
    min_effective_speed_kmh = 0.1 * ship_speed_kmh
    effective_speed_kmh = max(effective_speed_kmh, min_effective_speed_kmh)

    return request.distance_km / effective_speed_kmh


# ---------------------------------------------------------------------------
# Operating cost
# ---------------------------------------------------------------------------

# Bunker fuel prices genuinely fluctuate too much (day to day, port to port)
# for either of us to cite a specific current USD/tonne figure reliably --
# unlike the physical/chemical constants above (calorific value, carbon
# content), price is NOT a stable, citable constant, and we are not going to
# fabricate false precision here. These numbers are ILLUSTRATIVE, RELATIVE-
# ORDERING figures only, not live market quotes:
# HFO has historically been the cheapest bunker fuel; diesel/MGO commands a
# premium for lower sulfur content; LNG, methanol, ammonia, and hydrogen are
# alternative/low-carbon fuels that generally cost more per tonne today (and
# especially more per unit of energy delivered, since several of them are
# also less energy-dense) due to production scale, infrastructure, and
# demand -- consistent with the DIRECTION of current maritime-fuel-transition
# market commentary, but the specific numbers below should not be read as
# real current bunker quotes. Anyone needing real pricing should consult a
# live bunker price index (e.g. Ship & Bunker, Platts) at time of use.
_FUEL_PRICE_PER_TONNE: dict[FuelType, float] = {
    FuelType.HFO: 500.0,
    FuelType.DIESEL: 650.0,
    FuelType.LNG: 700.0,
    FuelType.METHANOL: 800.0,
    FuelType.AMMONIA: 900.0,
    FuelType.HYDROGEN: 1400.0,
}

# Flat hourly rate standing in for crew wages, charter hire, insurance, and
# port/agency overhead accrued per hour underway. A real opex model would
# vary this by vessel size/type and charter market conditions; a single flat
# constant is a documented simplification appropriate for a synthetic-label
# generator whose job is to give the trained model a directionally-correct
# "time also costs money" signal.
HOURLY_OPERATING_RATE = 800.0


def compute_physics_labels(request: VoyageRequest) -> PredictionResponse:
    """Compute (fuel_consumption, operating_cost, voyage_time) for one voyage
    from first-principles-flavored naval-architecture approximations.

    See the module docstring for the overall approach and its limitations.
    """
    voyage_time_hours = _voyage_time_hours(request)
    power_kw = _required_power_kw(request)
    fuel_consumption_tonnes = _fuel_mass_tonnes(request, power_kw, voyage_time_hours)

    fuel_price = _FUEL_PRICE_PER_TONNE[request.fuel_type]
    operating_cost = fuel_consumption_tonnes * fuel_price + voyage_time_hours * HOURLY_OPERATING_RATE

    return PredictionResponse(
        fuel_consumption=max(fuel_consumption_tonnes, 0.0),
        operating_cost=max(operating_cost, 0.0),
        voyage_time=max(voyage_time_hours, 0.0),
    )
