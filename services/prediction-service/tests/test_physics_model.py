import pytest

from app.physics_model import ADMIRALTY_COEFFICIENT, compute_physics_labels
from common.schemas import FuelType, PredictionResponse, VesselType, VoyageRequest


def make_request(**overrides) -> VoyageRequest:
    defaults = dict(
        vessel_id="V1",
        vessel_type=VesselType.CONTAINER,
        route_id="R1",
        origin="Mumbai",
        destination="Singapore",
        distance_km=3800.0,
        cargo_tonnes=50000.0,
        cargo_utilization=0.8,
        speed_knots=18.0,
        fuel_type=FuelType.HFO,
        wind_speed=5.0,
        wave_height=1.2,
        temperature=28.0,
        current_speed=0.5,
    )
    defaults.update(overrides)
    return VoyageRequest(**defaults)


class TestComputePhysicsLabels:
    def test_returns_prediction_response(self):
        result = compute_physics_labels(make_request())
        assert isinstance(result, PredictionResponse)

    def test_outputs_are_non_negative(self):
        result = compute_physics_labels(make_request())
        assert result.fuel_consumption >= 0
        assert result.operating_cost >= 0
        assert result.voyage_time >= 0

    # --- Speed -> fuel consumption (roughly cubic) ---------------------

    def test_higher_speed_increases_fuel_consumption(self):
        slow = compute_physics_labels(make_request(speed_knots=12.0))
        fast = compute_physics_labels(make_request(speed_knots=18.0))
        assert fast.fuel_consumption > slow.fuel_consumption

    def test_doubling_speed_increases_fuel_much_more_than_double(self):
        # Admiralty power ~ speed^3, but voyage_time ~ 1/speed, so energy
        # (and hence fuel mass) ~ speed^2 -- still strongly superlinear.
        # Doubling speed should increase fuel by well over 2x, consistent
        # with a cubic-ish power relationship, without pinning the exact
        # exponent (weather/current terms also interact).
        base = compute_physics_labels(make_request(speed_knots=10.0, current_speed=0.0))
        doubled = compute_physics_labels(make_request(speed_knots=20.0, current_speed=0.0))
        ratio = doubled.fuel_consumption / base.fuel_consumption
        assert ratio > 2.5

    # --- Cargo utilization -> fuel consumption --------------------------

    def test_higher_cargo_utilization_increases_fuel_consumption(self):
        light = compute_physics_labels(make_request(cargo_utilization=0.2, cargo_tonnes=10000.0))
        heavy = compute_physics_labels(make_request(cargo_utilization=0.9, cargo_tonnes=45000.0))
        assert heavy.fuel_consumption > light.fuel_consumption

    # --- Weather severity -> fuel consumption ---------------------------

    def test_higher_wind_increases_fuel_consumption(self):
        calm = compute_physics_labels(make_request(wind_speed=2.0, wave_height=0.5, current_speed=0.0))
        windy = compute_physics_labels(make_request(wind_speed=35.0, wave_height=0.5, current_speed=0.0))
        assert windy.fuel_consumption > calm.fuel_consumption

    def test_higher_waves_increase_fuel_consumption(self):
        calm = compute_physics_labels(make_request(wind_speed=2.0, wave_height=0.2, current_speed=0.0))
        rough = compute_physics_labels(make_request(wind_speed=2.0, wave_height=12.0, current_speed=0.0))
        assert rough.fuel_consumption > calm.fuel_consumption

    def test_stronger_adverse_current_increases_fuel_consumption(self):
        # An adverse (negative) current both raises weather-severity-driven
        # required power AND lengthens voyage time (lower effective speed),
        # so both effects push fuel consumption up together -- a clean,
        # unconfounded test of the weather severity contribution. (A
        # favorable current is a poor choice here because it simultaneously
        # raises severity but *shortens* voyage time, so the two effects can
        # partially cancel in total fuel mass -- see the voyage_time test
        # for that interaction instead.)
        mild = compute_physics_labels(make_request(current_speed=-0.1))
        strong = compute_physics_labels(make_request(current_speed=-4.5))
        assert strong.fuel_consumption > mild.fuel_consumption

    # --- Distance -> voyage_time and fuel_consumption -------------------

    def test_longer_distance_increases_voyage_time(self):
        short = compute_physics_labels(make_request(distance_km=1000.0))
        long_ = compute_physics_labels(make_request(distance_km=5000.0))
        assert long_.voyage_time > short.voyage_time

    def test_longer_distance_increases_fuel_consumption(self):
        short = compute_physics_labels(make_request(distance_km=1000.0))
        long_ = compute_physics_labels(make_request(distance_km=5000.0))
        assert long_.fuel_consumption > short.fuel_consumption

    def test_voyage_time_scales_roughly_linearly_with_distance(self):
        base = compute_physics_labels(make_request(distance_km=1000.0, current_speed=0.0))
        tripled = compute_physics_labels(make_request(distance_km=3000.0, current_speed=0.0))
        ratio = tripled.voyage_time / base.voyage_time
        assert ratio == pytest.approx(3.0, rel=0.05)

    def test_fuel_consumption_scales_roughly_linearly_with_distance(self):
        # At fixed speed/weather, power (kW) is constant, so fuel mass
        # (power x time) should track distance closely (time ~ distance).
        base = compute_physics_labels(make_request(distance_km=1000.0, current_speed=0.0))
        tripled = compute_physics_labels(make_request(distance_km=3000.0, current_speed=0.0))
        ratio = tripled.fuel_consumption / base.fuel_consumption
        assert ratio == pytest.approx(3.0, rel=0.05)

    # --- Current direction -> voyage_time --------------------------------

    def test_favorable_current_shortens_voyage_time(self):
        favorable = compute_physics_labels(make_request(current_speed=2.0))
        adverse = compute_physics_labels(make_request(current_speed=-2.0))
        assert favorable.voyage_time < adverse.voyage_time

    # --- Fuel type matters ------------------------------------------------

    def test_different_fuel_types_produce_different_fuel_consumption(self):
        hfo = compute_physics_labels(make_request(fuel_type=FuelType.HFO))
        methanol = compute_physics_labels(make_request(fuel_type=FuelType.METHANOL))
        hydrogen = compute_physics_labels(make_request(fuel_type=FuelType.HYDROGEN))
        ammonia = compute_physics_labels(make_request(fuel_type=FuelType.AMMONIA))
        lng = compute_physics_labels(make_request(fuel_type=FuelType.LNG))
        values = {
            hfo.fuel_consumption,
            methanol.fuel_consumption,
            hydrogen.fuel_consumption,
            ammonia.fuel_consumption,
            lng.fuel_consumption,
        }
        assert len(values) == 5  # all distinct

    def test_different_fuel_types_produce_different_operating_cost(self):
        hfo = compute_physics_labels(make_request(fuel_type=FuelType.HFO))
        ammonia = compute_physics_labels(make_request(fuel_type=FuelType.AMMONIA))
        assert hfo.operating_cost != ammonia.operating_cost

    def test_all_fuel_types_produce_valid_non_negative_outputs(self):
        for fuel_type in FuelType:
            result = compute_physics_labels(make_request(fuel_type=fuel_type))
            assert result.fuel_consumption >= 0
            assert result.operating_cost >= 0
            assert result.voyage_time >= 0

    # --- Zero-cargo edge case ---------------------------------------------

    def test_zero_cargo_utilization_does_not_crash(self):
        result = compute_physics_labels(make_request(cargo_utilization=0.0, cargo_tonnes=0.0))
        assert isinstance(result, PredictionResponse)
        assert result.fuel_consumption >= 0

    def test_zero_cargo_still_has_hull_resistance_fuel_burn(self):
        # An empty ship should still burn a meaningful amount of fuel to
        # move its own light-ship displacement -- not zero.
        empty = compute_physics_labels(make_request(cargo_utilization=0.0, cargo_tonnes=0.0))
        assert empty.fuel_consumption > 0.0

    # --- All vessel types produce valid outputs ---------------------------

    @pytest.mark.parametrize("vessel_type", list(VesselType))
    def test_all_vessel_types_produce_valid_outputs(self, vessel_type):
        result = compute_physics_labels(make_request(vessel_type=vessel_type))
        assert result.fuel_consumption >= 0
        assert result.operating_cost >= 0
        assert result.voyage_time >= 0

    # --- Per-vessel-type Admiralty coefficient calibration ----------------
    #
    # Regression guard for the flat-coefficient bug: a single ADMIRALTY_
    # COEFFICIENT for every hull form over-predicted fuel for slow, full-
    # bodied hulls (bulk carriers/tankers) by roughly 2-3x relative to
    # real-world figures, while it happened to already land correctly for
    # fast, fine-hulled container ships. These tests lock in the required
    # *ordering* (slow full hulls need a materially higher coefficient, i.e.
    # LESS power per unit of displacement^(2/3)*speed^3, than fast fine
    # hulls) so the calibration can't silently regress back to a flat value.

    def test_admiralty_coefficient_is_per_vessel_type(self):
        assert set(ADMIRALTY_COEFFICIENT.keys()) == set(VesselType)

    def test_bulk_carrier_and_tanker_coefficients_exceed_container(self):
        # Bulk carriers/tankers are slow, full-bodied hulls that need much
        # less power per unit of displacement/speed than a fast, slender
        # container ship -- since power is inversely proportional to the
        # coefficient, this means a HIGHER coefficient for these types.
        container = ADMIRALTY_COEFFICIENT[VesselType.CONTAINER]
        assert ADMIRALTY_COEFFICIENT[VesselType.BULK_CARRIER] > container
        assert ADMIRALTY_COEFFICIENT[VesselType.TANKER] > container

    def test_bulk_carrier_coefficient_exceeds_tanker(self):
        # Validation showed bulk carriers overshot real-world fuel figures
        # more severely (~2.5-3x) than tankers (~1.8-3.6x on a smaller
        # vessel), so bulk carriers need the largest upward correction.
        assert (
            ADMIRALTY_COEFFICIENT[VesselType.BULK_CARRIER]
            > ADMIRALTY_COEFFICIENT[VesselType.TANKER]
        )

    def test_ro_ro_coefficient_close_to_container(self):
        # Ro-ros are faster/finer-hulled than bulk carriers, closer in
        # character to container ships.
        container = ADMIRALTY_COEFFICIENT[VesselType.CONTAINER]
        ro_ro = ADMIRALTY_COEFFICIENT[VesselType.RO_RO]
        assert ro_ro < ADMIRALTY_COEFFICIENT[VesselType.TANKER]
        assert ro_ro == pytest.approx(container, rel=0.2)

    def test_general_cargo_coefficient_between_container_and_bulk_carrier(self):
        container = ADMIRALTY_COEFFICIENT[VesselType.CONTAINER]
        bulk_carrier = ADMIRALTY_COEFFICIENT[VesselType.BULK_CARRIER]
        general_cargo = ADMIRALTY_COEFFICIENT[VesselType.GENERAL_CARGO]
        assert container < general_cargo < bulk_carrier

    def test_bulk_carrier_burns_less_fuel_per_day_than_flat_coefficient_would(self):
        # Direct end-to-end check on realistic Capesize-like inputs: a slow
        # (13 knot) bulk carrier should land in a plausible t/day range, well
        # below what the old flat coefficient (450 for every vessel type)
        # would have produced for the same inputs.
        bulk = make_request(
            vessel_type=VesselType.BULK_CARRIER,
            speed_knots=13.0,
            distance_km=19000.0,
            cargo_tonnes=160000.0,
            cargo_utilization=0.9,
        )
        result = compute_physics_labels(bulk)
        tonnes_per_day = result.fuel_consumption / (result.voyage_time / 24.0)

        # Real-world Capesize bulk carriers run roughly 30-45 t/day at
        # service speed; allow some headroom either side since this is a
        # simplified synthetic-label generator, not a certified naval-
        # architecture tool, but it must be far below the ~97 t/day the flat
        # coefficient produced.
        assert 20.0 < tonnes_per_day < 60.0

    def test_container_ship_still_in_previously_correct_range(self):
        # The bulk-carrier fix must not disturb container ships, which
        # already validated correctly against real-world figures under the
        # old flat coefficient (container's coefficient is unchanged).
        container = make_request(
            vessel_type=VesselType.CONTAINER,
            speed_knots=20.0,
            distance_km=10500.0,
            cargo_tonnes=100000.0 * 0.85,
            cargo_utilization=0.85,
        )
        result = compute_physics_labels(container)
        tonnes_per_day = result.fuel_consumption / (result.voyage_time / 24.0)
        assert 150.0 < tonnes_per_day < 300.0
