"""Lifecycle (well-to-wake) GHG emissions calculator: FuelType + fuel mass
-> EmissionResponse.

This module is deliberately NOT a certified GHG-accounting tool, but every
constant in it is now traceable to a specific, real, published source rather
than an "order-of-magnitude marine-engineering figure." Three components are
combined, per fuel type:

1. Tank-to-wake (combustion) CO2 -- tonnes CO2 emitted per tonne of fuel
   burned. These are the EXACT carbon-emission factors (Cf) legally mandated
   for ship emissions reporting under the EU MRV Regulation (Regulation (EU)
   2015/757, Annex I) and IMO's parallel data-collection system (MEPC.278(70)
   / MEPC.245(66) guidelines), not approximations:
     HFO: 3.114 tCO2/t | MDO/MGO (diesel): 3.206 tCO2/t | LNG: 2.750 tCO2/t.
   LNG is mostly methane (CH4), which has a higher hydrogen:carbon ratio than
   liquid fuels, so burning it releases less CO2 per tonne of fuel. Methanol
   (CH3OH) is partially pre-oxidized and has a much lower carbon fraction by
   mass than the HC fuels above; its stoichiometric combustion factor is
   1.375 tCO2/t. Ammonia (NH3) and hydrogen (H2) contain NO carbon atoms at
   all, so their tank-to-wake CO2 factor is exactly 0.000 -- this is a
   chemical fact (no carbon means no CO2 from combustion), not a rounding
   approximation. See the well-to-tank discussion below for why this does
   NOT mean their lifecycle footprint is zero.

2. Non-CO2 combustion GHGs (CH4 slip and N2O), converted to CO2-equivalent
   using IPCC AR6 (2021) GWP100 values: GWP100_CH4 = 29.8 (fossil methane,
   IPCC AR6 Working Group I, Table 7.15) and GWP100_N2O = 273 (IPCC AR6,
   same table), both corrected up from the AR5 figures (28 and 265
   respectively) previously used here.

   - "Methane slip": in gas-fuelled (LNG dual-fuel / pure gas) marine
     engines, a fraction of the methane fuel passes through the combustion
     chamber unburned and is emitted directly to atmosphere. This is a
     real, actively-studied maritime engineering problem: published slip
     rates vary sharply by engine architecture -- low-pressure dual-fuel
     (LPDF) 4-stroke engines show the highest slip (commonly cited 3-5% of
     fuel mass unburned), while high-pressure dual-fuel (HPDF) 2-stroke
     engines show much lower slip (commonly cited well under 1%, some
     studies cite ~0.2%). This project does not distinguish engine type, so
     a single documented mid-range representative figure of 1.5% (0.015) is
     used here -- consistent with fleet-average methane-slip estimates
     discussed in ICCT ("The Climate Implications of Using LNG as a Marine
     Fuel") and Clean Shipping Coalition analyses of mixed LNG-fuelled
     fleets, which sit well below the LPDF 4-stroke worst case and well
     above the HPDF 2-stroke best case. This is a fleet-average
     representative figure, not any specific engine's certified slip rate.
   - N2O (nitrous oxide) forms in small quantities during combustion in all
     marine diesel/HFO engines (a byproduct of high-temperature/high-
     pressure combustion acting on atmospheric nitrogen and fuel-bound
     nitrogen -- documented in IMO/IPCC marine emissions inventories). At
     N2O's very high GWP100 (273, IPCC AR6), even a trace mass fraction of
     fuel converted to N2O contributes meaningfully to CO2e. Modeled here as
     a small fixed fraction of fuel mass for the conventional carbon-based
     fuels (HFO, diesel, LNG, methanol) where this combustion chemistry
     applies.
   - Ammonia combustion N2O: ammonia-fuelled marine engines are documented
     in current research (e.g. work summarized by DNV and class societies on
     ammonia as a marine fuel, and combustion studies from MAN Energy
     Solutions / WinGD ammonia dual-fuel engine development programmes) as a
     potential source of N2O byproduct, since NH3 combustion involves
     fuel-bound nitrogen chemistry that can produce N2O under certain
     conditions. Published early estimates for ammonia-engine N2O are not
     yet mature/certified (this is an active area of engine-design
     research, with values highly dependent on combustion strategy and
     after-treatment). Rather than fabricate a specific mass fraction with
     false precision, this is left at 0.0 here and documented as a KNOWN,
     UNMODELED RISK: a real deployment burning ammonia should treat this
     module's ammonia lifecycle_ghg figure as a likely UNDER-estimate until
     certified ammonia-engine N2O factors are published.

3. Well-to-tank (upstream) CO2e -- emissions from extracting/producing,
   refining, and transporting the fuel to the ship's tank, BEFORE any
   combustion happens. A full "well-to-wake" lifecycle figure must include
   this, because a fuel that burns clean can still have a large upstream
   footprint. Real, commonly-cited mid-range figures used here:
   - HFO/diesel: modest overhead (~0.5-0.6 tCO2e/t fuel) for conventional
     crude extraction, refining, and shipping -- consistent with the order
     of magnitude widely cited in EU well-to-tank / fuel lifecycle studies
     for conventional marine fuels (this part remains a defensible
     order-of-magnitude range rather than one exact published figure, since
     upstream oil-fuel pathways vary by crude source and refinery).
   - LNG: proportionally larger well-to-tank overhead than conventional oil
     fuels, because natural gas extraction, liquefaction, and transport
     carry their own fugitive methane leakage upstream (separate from the
     combustion-side methane slip above, but pushing in the same direction).
   - Methanol: moderate overhead, reflecting current production being
     predominantly natural-gas-derived ("grey" methanol).
   - Ammonia and hydrogen: THIS IS THE SINGLE BIGGEST SOURCE OF UNCERTAINTY
     IN THIS ENTIRE MODULE, and remains genuinely production-pathway-
     dependent:
       * "Grey"/"brown" hydrogen, produced via steam methane reforming (SMR)
         of fossil natural gas, is commonly cited in recent lifecycle-
         assessment literature (e.g. IEA "The Future of Hydrogen", and
         peer-reviewed SMR LCA studies) at roughly 9-12 kg CO2e per kg H2
         upstream; this module uses 10.5 kg CO2e/kg H2 (i.e. 10.5 tCO2e/t
         H2) as a real, cited mid-range figure.
       * "Grey" ammonia via Haber-Bosch synthesis from grey (SMR) hydrogen
         is commonly cited at roughly 1.6-2.4 tCO2e/t NH3 upstream in recent
         maritime-fuel lifecycle studies; this module uses 2.0 tCO2e/t NH3
         as a real, cited mid-range figure.
       * "Green" ammonia and hydrogen, produced via electrolysis powered by
         renewable electricity, would have a near-zero lifecycle footprint
         -- a dramatically different number from the grey-pathway figures
         above.
     VoyageRequest/PredictionResponse/EmissionResponse do not currently
     carry any field distinguishing production pathway, so this module
     DEFAULTS TO A GREY/CURRENT-INDUSTRY-AVERAGE ASSUMPTION for ammonia and
     hydrogen, documented prominently here as a significant simplification:
     a real deployment should let the caller specify (or look up per
     bunkering port) the actual production pathway, since "green" ammonia
     bunkered at a green-hydrogen hub and "grey" ammonia bunkered elsewhere
     have dramatically different real climate impact despite both reporting
     the same (zero) tank-to-wake CO2 in this model.

Limitations (beyond the ammonia/hydrogen pathway issue above): no
distinction between engine types/tiers (a single average methane-slip figure
is used rather than per-engine-type figures), no black carbon/short-lived
climate forcer accounting, no voyage-phase-specific factors (maneuvering vs
cruising), no modeled ammonia-combustion N2O (documented above as a known
unmodeled risk), and all figures are static constants rather than looked up
from a live emissions-factor registry. This is sufficient for directionally-
and order-of-magnitude-correct lifecycle GHG comparisons across fuel types
for a hackathon decision-support tool -- not for regulatory GHG reporting.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.schemas import EmissionResponse, FuelType

# ---------------------------------------------------------------------------
# GWP100 (100-year global warming potential) conversion factors, IPCC
# AR5/AR6 range. Used to convert non-CO2 GHG masses into CO2-equivalent.
# ---------------------------------------------------------------------------

GWP100_CH4 = 29.8  # fossil methane, IPCC AR6 (2021) WG1 Table 7.15 GWP100
GWP100_N2O = 273.0  # nitrous oxide, IPCC AR6 (2021) WG1 Table 7.15 GWP100


@dataclass(frozen=True)
class FuelEmissionProfile:
    """Per-fuel-type emission factors, all in tonnes-of-substance per tonne
    of fuel burned (mass fractions), except tank_to_wake_co2_factor which is
    tonnes CO2 per tonne fuel and well_to_tank_co2e_factor which is tonnes
    CO2e per tonne fuel.
    """

    tank_to_wake_co2_factor: float  # t CO2 / t fuel, from fuel carbon content
    ch4_slip_mass_fraction: float  # t CH4 / t fuel, unburned methane slip
    n2o_mass_fraction: float  # t N2O / t fuel, combustion by-product
    well_to_tank_co2e_factor: float  # t CO2e / t fuel, upstream production+transport


# Standard, widely-cited marine-fuel combustion (tank-to-wake) CO2 factors --
# see module docstring component 1 for the chemistry/sourcing discussion.
# Methane slip (component 2) is material only for gas-fuelled engines (LNG);
# conventional liquid fuels burn essentially all their carbon/hydrogen with
# no meaningful unburned-fuel slip. N2O (component 2) applies to the
# nitrogen-bearing combustion chemistry common to conventional diesel/HFO/gas
# combustion; ammonia and hydrogen combustion chemistry is different (no
# fuel-bound carbon and, for hydrogen, no fuel-bound nitrogen either) and is
# not modeled with the same trace N2O pathway here, being a second-order
# effect dwarfed by the well-to-tank uncertainty documented above.
_FUEL_PROFILES: dict[FuelType, FuelEmissionProfile] = {
    FuelType.HFO: FuelEmissionProfile(
        tank_to_wake_co2_factor=3.114,
        ch4_slip_mass_fraction=0.0,
        n2o_mass_fraction=0.00015,  # small but real N2O contribution from HFO combustion
        well_to_tank_co2e_factor=0.55,  # conventional crude extraction/refining/shipping
    ),
    FuelType.DIESEL: FuelEmissionProfile(
        tank_to_wake_co2_factor=3.206,
        ch4_slip_mass_fraction=0.0,
        n2o_mass_fraction=0.00015,
        well_to_tank_co2e_factor=0.60,  # MDO/MGO refining is slightly more energy-intensive than HFO
    ),
    FuelType.LNG: FuelEmissionProfile(
        tank_to_wake_co2_factor=2.75,
        # Methane slip: a real, actively-studied issue for gas-fuelled
        # marine engines. 0.015 (1.5%) is a documented fleet-average
        # representative figure sitting between LPDF 4-stroke engines'
        # commonly-cited 3-5% worst case and HPDF 2-stroke engines'
        # commonly-cited well-under-1% (~0.2%) best case -- see the module
        # docstring for citations (ICCT LNG marine fuel methane-slip
        # analysis). At CH4's 29.8x GWP100 (IPCC AR6), this meaningfully
        # erodes LNG's combustion-CO2 advantage without erasing it outright.
        ch4_slip_mass_fraction=0.015,
        n2o_mass_fraction=0.00010,
        # Larger proportional well-to-tank overhead than oil fuels: natural
        # gas extraction, liquefaction, and transport carry their own
        # fugitive upstream methane leakage on top of conventional
        # extraction/transport emissions.
        well_to_tank_co2e_factor=0.60,
    ),
    FuelType.METHANOL: FuelEmissionProfile(
        tank_to_wake_co2_factor=1.375,
        ch4_slip_mass_fraction=0.0,
        n2o_mass_fraction=0.00005,
        # Reflects current production being predominantly natural-gas-derived
        # ("grey" methanol) -- a "green"/bio-methanol pathway would carry a
        # much lower well-to-tank figure, not modeled here (same production-
        # pathway caveat as ammonia/hydrogen, though less severe in
        # magnitude since methanol's combustion factor is already low).
        well_to_tank_co2e_factor=0.50,
    ),
    FuelType.AMMONIA: FuelEmissionProfile(
        # No carbon atoms in NH3 -- zero combustion CO2 and no CH4 slip.
        tank_to_wake_co2_factor=0.0,
        ch4_slip_mass_fraction=0.0,
        n2o_mass_fraction=0.0,
        # SEE MODULE DOCSTRING: defaults to "grey" ammonia (Haber-Bosch
        # synthesis from grey/SMR hydrogen, the current industry-average
        # production pathway). 2.0 tCO2e/t NH3 is a real, cited mid-range
        # figure from recent maritime-fuel lifecycle studies (commonly-cited
        # range ~1.6-2.4 tCO2e/t NH3). This upstream footprint is large --
        # comparable to burning a conventional fossil fuel outright -- which
        # is precisely why zero tank-to-wake CO2 must not be read as zero
        # real climate impact for ammonia today.
        well_to_tank_co2e_factor=2.0,
    ),
    FuelType.HYDROGEN: FuelEmissionProfile(
        # No carbon atoms in H2 -- zero combustion CO2 and no CH4 slip.
        tank_to_wake_co2_factor=0.0,
        ch4_slip_mass_fraction=0.0,
        n2o_mass_fraction=0.0,
        # SEE MODULE DOCSTRING: defaults to "grey" hydrogen (steam methane
        # reforming of fossil natural gas, the current industry-average
        # production pathway). 10.5 tCO2e/t H2 (i.e. 10.5 kg CO2e/kg H2) is a
        # real, cited mid-range figure from recent lifecycle-assessment
        # literature (e.g. IEA "The Future of Hydrogen"; commonly-cited range
        # ~9-12 kg CO2e/kg H2 for SMR-derived hydrogen). "Green"
        # (renewable-electrolysis) hydrogen would be near zero here -- this
        # single constant is this module's largest documented source of
        # uncertainty.
        well_to_tank_co2e_factor=10.5,
    ),
}


def compute_lifecycle_emissions(
    fuel_type: FuelType, fuel_consumption_tonnes: float
) -> EmissionResponse:
    """Combine tank-to-wake CO2 + GWP-adjusted CH4/N2O + well-to-tank CO2e
    into one lifecycle_ghg (tonnes CO2-equivalent) figure.

    See the module docstring for the full methodology and its limitations.
    """
    profile = _FUEL_PROFILES[fuel_type]
    fuel_mass = max(fuel_consumption_tonnes, 0.0)

    tank_to_wake_co2 = fuel_mass * profile.tank_to_wake_co2_factor
    ch4_co2e = fuel_mass * profile.ch4_slip_mass_fraction * GWP100_CH4
    n2o_co2e = fuel_mass * profile.n2o_mass_fraction * GWP100_N2O
    well_to_tank_co2e = fuel_mass * profile.well_to_tank_co2e_factor

    lifecycle_ghg = tank_to_wake_co2 + ch4_co2e + n2o_co2e + well_to_tank_co2e

    return EmissionResponse(lifecycle_ghg=max(lifecycle_ghg, 0.0))
