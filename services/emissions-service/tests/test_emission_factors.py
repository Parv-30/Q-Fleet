"""Tests for emissions-service's lifecycle GHG calculator (app/emission_factors.py).

Verify real GHG-accounting relationships, not just "returns a number":
linearity in fuel mass, relative ordering across fuel types, the real
finding that LNG's methane slip (at real, cited slip/GWP figures) now
outweighs its combustion-CO2 advantage over HFO on a full lifecycle basis,
and the "no carbon in fuel does not mean zero lifecycle emissions" effect
for grey ammonia/hydrogen.
"""

from __future__ import annotations

import pytest

from app.emission_factors import compute_lifecycle_emissions, _FUEL_PROFILES
from common.schemas import EmissionResponse, FuelType


class TestBasicShape:
    def test_returns_emission_response(self):
        result = compute_lifecycle_emissions(FuelType.HFO, 100.0)
        assert isinstance(result, EmissionResponse)

    def test_output_non_negative_for_all_fuel_types(self):
        for fuel_type in FuelType:
            result = compute_lifecycle_emissions(fuel_type, 250.0)
            assert result.lifecycle_ghg >= 0

    def test_zero_fuel_consumption_gives_zero_lifecycle_ghg(self):
        for fuel_type in FuelType:
            result = compute_lifecycle_emissions(fuel_type, 0.0)
            assert result.lifecycle_ghg == pytest.approx(0.0)

    def test_negative_fuel_consumption_clamped_to_zero(self):
        # Defensive: the calculator itself should never emit a negative
        # figure even if called with a bad negative input directly.
        result = compute_lifecycle_emissions(FuelType.HFO, -50.0)
        assert result.lifecycle_ghg == pytest.approx(0.0)


class TestLinearityInFuelMass:
    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_lifecycle_ghg_scales_linearly_with_fuel_mass(self, fuel_type):
        base = compute_lifecycle_emissions(fuel_type, 100.0)
        tripled = compute_lifecycle_emissions(fuel_type, 300.0)
        if base.lifecycle_ghg == 0:
            assert tripled.lifecycle_ghg == pytest.approx(0.0)
        else:
            ratio = tripled.lifecycle_ghg / base.lifecycle_ghg
            assert ratio == pytest.approx(3.0, rel=1e-9)

    def test_more_fuel_consumed_increases_lifecycle_ghg(self):
        for fuel_type in FuelType:
            low = compute_lifecycle_emissions(fuel_type, 100.0)
            high = compute_lifecycle_emissions(fuel_type, 500.0)
            assert high.lifecycle_ghg >= low.lifecycle_ghg
            # Only strictly greater when the fuel has any nonzero factor
            # (true for every fuel here once well-to-tank is included).
            assert high.lifecycle_ghg > low.lifecycle_ghg


class TestHfoVsDiesel:
    def test_hfo_and_diesel_are_similar_order_of_magnitude(self):
        hfo = compute_lifecycle_emissions(FuelType.HFO, 100.0)
        diesel = compute_lifecycle_emissions(FuelType.DIESEL, 100.0)
        ratio = diesel.lifecycle_ghg / hfo.lifecycle_ghg
        assert 0.9 < ratio < 1.3

    def test_diesel_lifecycle_ghg_higher_than_hfo(self):
        # Diesel's higher combustion factor (3.206 vs 3.114) and slightly
        # higher well-to-tank overhead should make it come out higher.
        hfo = compute_lifecycle_emissions(FuelType.HFO, 100.0)
        diesel = compute_lifecycle_emissions(FuelType.DIESEL, 100.0)
        assert diesel.lifecycle_ghg > hfo.lifecycle_ghg


class TestLngMethaneSlipNarrowing:
    def test_lng_tank_to_wake_co2_factor_lower_than_hfo_and_diesel(self):
        hfo_factor = _FUEL_PROFILES[FuelType.HFO].tank_to_wake_co2_factor
        diesel_factor = _FUEL_PROFILES[FuelType.DIESEL].tank_to_wake_co2_factor
        lng_factor = _FUEL_PROFILES[FuelType.LNG].tank_to_wake_co2_factor
        assert lng_factor < hfo_factor
        assert lng_factor < diesel_factor

    def test_lng_full_lifecycle_ghg_exceeds_hfo_once_real_methane_slip_is_modeled(self):
        """IMPORTANT REAL FINDING (not a bug): with real, cited constants --
        EU MRV tank-to-wake Cf factors, a documented 1.5% fleet-average
        methane-slip mass fraction (ICCT-style mid-range estimate), and IPCC
        AR6 GWP100 = 29.8 for fossil methane -- LNG's full well-to-wake
        lifecycle GHG per tonne of fuel burned is actually HIGHER than
        HFO's, not lower.

        LNG's combustion-CO2 advantage over HFO is only 3.114 - 2.750 =
        0.364 tCO2/t. But at a real 1.5% methane-slip mass fraction and
        GWP100 29.8, the slipped methane alone contributes
        0.015 * 29.8 = 0.447 tCO2e/t -- already larger than the entire
        combustion-CO2 advantage LNG was supposed to provide, before LNG's
        larger well-to-tank (upstream fugitive methane leakage) overhead is
        even added. This overturns the previous ("old-constants") qualitative
        conclusion that LNG retains a narrowed-but-real lifecycle advantage
        over HFO -- with the previous, non-cited 0.6% slip fraction and
        AR5-style GWP100 28, LNG's slip penalty was smaller than its
        combustion advantage; at the real, cited 1.5% fleet-average slip
        fraction, it no longer is. This is a genuine, real-data-driven result
        that should inform fuel-choice guidance, not be tuned away.
        """
        hfo = compute_lifecycle_emissions(FuelType.HFO, 100.0)
        lng = compute_lifecycle_emissions(FuelType.LNG, 100.0)
        assert lng.lifecycle_ghg > hfo.lifecycle_ghg

    def test_lng_combustion_only_co2_still_lower_than_hfo_despite_worse_full_lifecycle(self):
        """LNG's headline tank-to-wake (combustion-only) CO2 figure is still
        genuinely lower than HFO's -- the reversal above happens only once
        methane slip and well-to-tank upstream leakage are included. Both
        facts are real and both matter: a naive combustion-only comparison
        would wrongly favor LNG, which is exactly the "isn't as green as it
        first looks" finding this module exists to surface.
        """
        fuel_mass = 100.0
        hfo_profile = _FUEL_PROFILES[FuelType.HFO]
        lng_profile = _FUEL_PROFILES[FuelType.LNG]

        naive_hfo_co2 = fuel_mass * hfo_profile.tank_to_wake_co2_factor
        naive_lng_co2 = fuel_mass * lng_profile.tank_to_wake_co2_factor
        assert naive_lng_co2 < naive_hfo_co2

        hfo_full = compute_lifecycle_emissions(FuelType.HFO, fuel_mass)
        lng_full = compute_lifecycle_emissions(FuelType.LNG, fuel_mass)
        assert lng_full.lifecycle_ghg > hfo_full.lifecycle_ghg


class TestZeroCarbonFuelsStillHaveLifecycleFootprint:
    @pytest.mark.parametrize("fuel_type", [FuelType.AMMONIA, FuelType.HYDROGEN])
    def test_tank_to_wake_co2_factor_is_zero(self, fuel_type):
        assert _FUEL_PROFILES[fuel_type].tank_to_wake_co2_factor == 0.0

    @pytest.mark.parametrize("fuel_type", [FuelType.AMMONIA, FuelType.HYDROGEN])
    def test_lifecycle_ghg_is_not_zero_despite_no_carbon_in_fuel(self, fuel_type):
        # The important, easy-to-get-wrong detail: no carbon in the fuel
        # molecule does NOT mean zero real-world climate impact, because
        # grey ammonia/hydrogen production carries a large upstream
        # (well-to-tank) CO2e footprint.
        result = compute_lifecycle_emissions(fuel_type, 100.0)
        assert result.lifecycle_ghg > 0.0

    def test_ammonia_and_hydrogen_lifecycle_ghg_driven_entirely_by_well_to_tank(self):
        for fuel_type in (FuelType.AMMONIA, FuelType.HYDROGEN):
            result = compute_lifecycle_emissions(fuel_type, 100.0)
            profile = _FUEL_PROFILES[fuel_type]
            expected = 100.0 * profile.well_to_tank_co2e_factor
            assert result.lifecycle_ghg == pytest.approx(expected)


class TestAllFuelTypesValid:
    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_all_fuel_types_produce_valid_emission_response(self, fuel_type):
        result = compute_lifecycle_emissions(fuel_type, 321.0)
        assert isinstance(result, EmissionResponse)
        assert result.lifecycle_ghg >= 0

    def test_all_fuel_types_produce_distinct_lifecycle_ghg_values(self):
        values = {
            fuel_type: compute_lifecycle_emissions(fuel_type, 100.0).lifecycle_ghg
            for fuel_type in FuelType
        }
        assert len(set(values.values())) == len(FuelType)
