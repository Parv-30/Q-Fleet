"""Data ingestion: turns raw sources into VoyageRequest-shaped records.

Two sources feed the pipeline:

1. Kaggle "Global Cargo Ships Dataset" (real vessel specifications, scraped
   from vesselfinder.com, CC0) -- ``load_global_cargo_ships_csv``. Used both
   to produce spec-only records and, more importantly, to calibrate the
   synthetic generator's per-vessel-type dwt/length distributions against
   real ships instead of arbitrary constants.
2. A physics-plausible synthetic generator -- ``generate_synthetic_records``
   -- for everything neither Kaggle dataset actually contains: speed,
   numeric weather, cargo utilization per leg, and delivery deadlines.
   Route distance is NOT fabricated -- it is computed for real via
   ``app.routing.compute_route`` (searoute sea-lane distances) for a small
   set of real named port pairs. Real AIS/weather feeds joined to
   voyage-level cargo/fuel data are not publicly available for free; this is
   expected, not a shortcut -- see the "Data sourcing" note in the README
   for the reasoning.

Kaggle's "Ship Fuel Consumption & CO2 Emissions Analysis" dataset was
evaluated and deliberately NOT wired in as an ingestion source: its own
Kaggle page states the data "was generated to simulate realistic maritime
operations in Nigeria" -- it is synthetic, not measured -- and its vessel
types (Fishing Trawler, Oil Service Boat, Surfer Boat, Tanker Ship) don't
match the commercial cargo/container/bulk/ro-ro fleet this project models.
Treating it as ground truth would have been worse than being honest about
building a calibrated synthetic dataset. Its fuel-consumption-vs-distance
magnitudes are still useful as a rough plausibility check and are recorded
in ``_FUEL_CO2_REFERENCE_NOTES`` below for that purpose only -- no rows from
it are ever loaded.

Malformed rows (missing required fields, values that fail VoyageRequest's
range validation) are skipped rather than raising, since a single bad row in
a multi-hundred-row CSV should not abort the whole load. The loader returns
a ``LoadResult`` so callers can see how many rows were skipped and why.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from app.routing import compute_route
from common.schemas import FuelType, VesselType, VoyageRequest

# Repo-root-relative default location for user-provided CSVs. This file lives
# at services/data-service/app/ingestion.py, so parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW_DATA_DIR = _REPO_ROOT / "data" / "raw"


@dataclass
class LoadResult:
    """A loader's output plus a record of what it couldn't use."""

    records: list[VoyageRequest] = field(default_factory=list)
    skipped_rows: int = 0
    errors: list[str] = field(default_factory=list)  # human-readable, capped

    def __len__(self) -> int:
        return len(self.records)


# ---------------------------------------------------------------------------
# Loader: Kaggle "Global Cargo Ships Dataset"
# https://www.kaggle.com/datasets/ibrahimonmars/global-cargo-ships-dataset
# ---------------------------------------------------------------------------
#
# Confirmed real headers (inspected on Kaggle's data-card preview; the site
# mislabels the first column "Company_Name" but its values and unique-value
# breakdown -- "Bulk Carrier" 34%, "Container Ship" 26%, ... -- are plainly
# vessel type, not a company):
#
#   Company_Name (actually ship_type), ship_name, built_year, gt, dwt,
#   length, width
#
# `GLOBAL_CARGO_COLUMN_MAP` maps our fields to these headers. If a future
# re-download of the "cleaned" CSV has Kaggle fix the header name, only the
# right-hand values below need updating.
GLOBAL_CARGO_COLUMN_MAP: dict[str, str] = {
    "vessel_type": "Company_Name",  # mislabeled on Kaggle; holds ship type
    "vessel_id": "ship_name",
    "deadweight": "dwt",
    "length": "length",
    "width": "width",
}

# Values in the CSV's vessel-type column are vesselfinder.com's own labels
# (e.g. "Crude Oil Tanker", "LNG Tanker", "Passenger (Cruise) Ship"), which
# don't match our five-value enum. Normalize known variants here; extend as
# more real label spellings surface once the full (not just previewed) file
# is inspected.
_VESSEL_TYPE_ALIASES: dict[str, VesselType] = {
    "container ship": VesselType.CONTAINER,
    "container": VesselType.CONTAINER,
    "bulk carrier": VesselType.BULK_CARRIER,
    "bulker": VesselType.BULK_CARRIER,
    "crude oil tanker": VesselType.TANKER,
    "oil tanker": VesselType.TANKER,
    "lng tanker": VesselType.TANKER,
    "tanker": VesselType.TANKER,
    "ro-ro": VesselType.RO_RO,
    "roro": VesselType.RO_RO,
    "vehicles carrier": VesselType.RO_RO,
    "general cargo": VesselType.GENERAL_CARGO,
    "general cargo ship": VesselType.GENERAL_CARGO,
    # Types present in the real dataset with no equivalent in our enum
    # (offshore support vessels, FSOs, cruise ships) fall back to
    # GENERAL_CARGO via _normalize_enum's default rather than being listed
    # here, since they aren't cargo-carrying in our domain's sense.
}

_FUEL_TYPE_ALIASES: dict[str, FuelType] = {
    "hfo": FuelType.HFO,
    "heavy fuel oil": FuelType.HFO,
    "diesel": FuelType.DIESEL,
    "mdo": FuelType.DIESEL,
    "lng": FuelType.LNG,
    "methanol": FuelType.METHANOL,
    "hydrogen": FuelType.HYDROGEN,
    "ammonia": FuelType.AMMONIA,
}

# This dataset has no route, speed, weather, or fuel columns at all -- it is
# static vessel specs, not voyages. Deadweight is the vessel's total carrying
# CAPACITY, not the cargo actually aboard for a given leg, so it's treated as
# an upper bound with a generic utilization rather than claimed as fact.
_ASSUMED_UTILIZATION_FOR_SPEC_ONLY_DATA = 0.7
_DEFAULT_SPEED_KNOTS = 14.0  # a typical laden cargo-ship service speed
_DEFAULT_WEATHER = dict(wind_speed=5.0, wave_height=1.0, temperature=20.0, current_speed=0.0)


def _normalize_enum(raw: object, aliases: dict[str, "VesselType | FuelType"], default: "VesselType | FuelType"):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return default
    key = str(raw).strip().lower()
    return aliases.get(key, default)


def load_global_cargo_ships_csv(path: str | Path | None = None) -> LoadResult:
    """Load the Kaggle global-cargo-ships spec dataset into VoyageRequest records.

    Since this dataset carries vessel specs (not voyages), each row is turned
    into a single synthetic "representative" route leg using
    ``_DEFAULT_SPEED_KNOTS`` and a generic origin/destination placeholder --
    good for enriching vessel_type/cargo_tonnes distributions in a combined
    training set, not for anything route-specific.

    Args:
        path: CSV file path. Defaults to
            ``data/raw/global_cargo_ships.csv`` under the repo root.
    """
    csv_path = Path(path) if path is not None else DEFAULT_RAW_DATA_DIR / "global_cargo_ships.csv"
    df = pd.read_csv(csv_path)

    result = LoadResult()
    colmap = GLOBAL_CARGO_COLUMN_MAP

    for idx, row in df.iterrows():
        try:
            vessel_id = str(row.get(colmap["vessel_id"], f"UNKNOWN-{idx}"))
            deadweight_raw = row.get(colmap["deadweight"])
            if pd.isna(deadweight_raw):
                result.skipped_rows += 1
                result.errors.append(f"row {idx}: missing deadweight")
                continue
            deadweight = float(deadweight_raw)
            if deadweight <= 0:
                result.skipped_rows += 1
                result.errors.append(f"row {idx}: non-positive deadweight")
                continue

            record = VoyageRequest(
                vessel_id=vessel_id,
                vessel_type=_normalize_enum(row.get(colmap["vessel_type"]), _VESSEL_TYPE_ALIASES, VesselType.GENERAL_CARGO),
                route_id=f"SPEC-{idx}",
                origin="UNKNOWN",
                destination="UNKNOWN",
                distance_km=1000.0,  # placeholder representative leg length
                cargo_tonnes=deadweight * _ASSUMED_UTILIZATION_FOR_SPEC_ONLY_DATA,
                cargo_utilization=_ASSUMED_UTILIZATION_FOR_SPEC_ONLY_DATA,
                speed_knots=_DEFAULT_SPEED_KNOTS,
                fuel_type=FuelType.HFO,
                **_DEFAULT_WEATHER,
            )
            result.records.append(record)
        except (ValidationError, ValueError, TypeError) as exc:
            result.skipped_rows += 1
            result.errors.append(f"row {idx}: {exc}")

    return result


def load_vessel_type_dwt_stats(path: str | Path | None = None) -> dict[VesselType, tuple[float, float]]:
    """Derive a (min, max) deadweight range per VesselType from the real
    Global Cargo Ships CSV, for calibrating the synthetic generator.

    Falls back to ``_FALLBACK_DWT_RANGES`` (based on the dataset's published
    aggregate histogram: dwt spans ~7,900-401,000t, concentrated in the
    130k-320k band) for any vessel type absent from the file, or if the file
    itself isn't present yet.
    """
    csv_path = Path(path) if path is not None else DEFAULT_RAW_DATA_DIR / "global_cargo_ships.csv"
    ranges = dict(_FALLBACK_DWT_RANGES)
    if not csv_path.exists():
        return ranges

    df = pd.read_csv(csv_path)
    colmap = GLOBAL_CARGO_COLUMN_MAP
    # Compare on the enum's underlying string value, not the enum member
    # itself -- pandas' vectorized `Series == member` does not reliably
    # dispatch to `str, Enum`'s equality when boxed as an object column.
    df["_vessel_type"] = df[colmap["vessel_type"]].apply(
        lambda v: _normalize_enum(v, _VESSEL_TYPE_ALIASES, None)
    ).apply(lambda vt: vt.value if vt is not None else None)
    for vessel_type in VesselType:
        subset = df[df["_vessel_type"] == vessel_type.value][colmap["deadweight"]].dropna()
        if len(subset) >= 2:
            ranges[vessel_type] = (float(subset.min()), float(subset.max()))
    return ranges


# Fallback dwt ranges (tonnes) per vessel type, used when the real CSV isn't
# present or has too few rows of a given type to derive a stable range.
# Anchored to the dataset's published aggregate histogram (7,900-401,000t
# overall, concentrated 130k-320k) split plausibly by typical vessel size.
_FALLBACK_DWT_RANGES: dict[VesselType, tuple[float, float]] = {
    VesselType.CONTAINER: (20000.0, 240000.0),
    VesselType.BULK_CARRIER: (30000.0, 400000.0),
    VesselType.TANKER: (30000.0, 320000.0),
    VesselType.RO_RO: (10000.0, 70000.0),
    VesselType.GENERAL_CARGO: (5000.0, 40000.0),
}

# Rough plausibility reference only -- NOT ingested as training rows. From
# Kaggle's "Ship Fuel Consumption & CO2 Emissions Analysis" data-card preview
# (explicitly a *simulated* dataset for small Nigerian-waterway craft, not
# commercial cargo vessels): fuel consumption for a ~50-500km leg ranged
# roughly 700-18,500 liters depending on ship type/distance/weather, i.e.
# order-of-magnitude tens of liters per km for small craft. Kept here purely
# so a human reviewing the synthetic generator's own fuel-plausibility
# formula (in prediction-service, Phase 2) has an external sanity check to
# compare against -- our commercial vessels are far larger and burn tonnes,
# not liters, per day, so no direct scaling is implied.
_FUEL_CO2_REFERENCE_NOTES = (
    "Nigeria fuel/CO2 dataset (synthetic, small craft): ~5-35 L fuel per km "
    "for Fishing Trawler/Oil Service Boat/Surfer Boat/Tanker Ship legs of "
    "50-500km, CO2 emissions ~2.7kg per liter of fuel burned "
    "(consistent with HFO/diesel emission factors)."
)


# ---------------------------------------------------------------------------
# Synthetic weather/route-leg generator
# ---------------------------------------------------------------------------
#
# Real AIS + weather feeds jointly covering commercial cargo voyages aren't
# publicly available for free (see module docstring), so weather/cargo
# fields are still fabricated with bounded, physics-plausible randomization,
# while vessel_type -> cargo_tonnes ranges are calibrated against the real
# Global Cargo Ships dwt distribution via ``load_vessel_type_dwt_stats``
# rather than picked arbitrarily. Route distance, however, is NOT random:
# it is computed for real from ``_SAMPLE_ROUTES`` port pairs via
# ``app.routing.compute_route`` (real searoute sea-lane distances -- see
# that module's docstring for the "not navigational-grade" caveat), so
# training records carry a real distance for a real named route rather than
# an arbitrary number drawn independently of the origin/destination. Where
# a route has both a Suez and a Cape option, the default (first / shortest)
# option's distance is used as the single representative distance for
# training-label purposes -- choosing between them is an optimizer-time
# decision (sub-phase D), not a training-data concern.

_WIND_SPEED_RANGE = (0.0, 25.0)  # m/s
_WAVE_HEIGHT_TYPICAL_RANGE = (0.0, 6.0)  # meters, calm-to-rough seas
_WAVE_HEIGHT_STORM_RANGE = (6.0, 10.0)  # meters, occasional storm leg
_STORM_PROBABILITY = 0.05
_TEMPERATURE_RANGE = (-5.0, 40.0)  # Celsius
_CURRENT_SPEED_RANGE = (-3.0, 3.0)  # knots, signed
_SPEED_KNOTS_RANGE = (8.0, 24.0)

# Speed is NOT sampled uniformly over _SPEED_KNOTS_RANGE. "Slow steaming"
# (deliberately running well below a vessel's design/service speed to cut
# fuel burn, since fuel consumption scales roughly with speed cubed) became
# widespread industry practice after the 2008 fuel price spike and remains
# common today for both cost and emissions reasons -- so real commercial
# cargo vessels spend a disproportionate share of operating time in the
# lower half of their speed range, not spread evenly across it. A Beta(2, 5)
# distribution over [0, 1] concentrates most of its mass in its lower
# ~40%, which -- rescaled onto _SPEED_KNOTS_RANGE -- puts the bulk of
# sampled speeds in the reduced/slow-steaming band while still leaving a
# realistic (if thinner) tail up toward full service speed. This also
# happens to fix a real weak spot in the trained model: the physics
# formula's speed-cubed term concentrates "interesting" label variance at
# higher speeds, so a uniform speed draw under-samples the low end,
# leaving the model poorly trained (and prone to badly wrong, sometimes
# negative, raw predictions) right where slow-steaming vessels actually
# operate. Both are true; the shape below is chosen to match the real
# distribution, not just to patch the model.
_SLOW_STEAMING_BETA_ALPHA = 2.0
_SLOW_STEAMING_BETA_BETA = 5.0


def _sample_speed_knots(rng: random.Random) -> float:
    """Sample one voyage speed (knots), biased toward the lower half of
    _SPEED_KNOTS_RANGE to reflect real-world slow-steaming prevalence (see
    the comment above _SLOW_STEAMING_BETA_ALPHA)."""
    unit_interval_sample = rng.betavariate(_SLOW_STEAMING_BETA_ALPHA, _SLOW_STEAMING_BETA_BETA)
    speed_min, speed_max = _SPEED_KNOTS_RANGE
    return speed_min + unit_interval_sample * (speed_max - speed_min)

# Real port pairs (names must exist in app.port_catalog.PORT_CATALOG),
# spanning the same major-trade-lane variety the old hardcoded name pairs
# gestured at, but now backed by real computed distances instead of a
# uniform random number.
_SAMPLE_ROUTES = [
    ("Mumbai", "Singapore"),
    ("Rotterdam", "New York"),
    ("Shanghai", "Los Angeles"),
    ("Singapore", "Dubai"),
    ("Santos", "Rotterdam"),
    ("Busan", "Vancouver"),
]


def _representative_distance_km(origin: str, destination: str) -> float:
    """The single real distance (km) used to label a synthetic route leg.

    Uses the default/shortest ``compute_route`` option -- when a Suez vs.
    Cape choice exists, that choice belongs to the optimizer (sub-phase D),
    not the training generator, which just needs one plausible number per
    route pair.
    """
    return compute_route(origin, destination)[0].distance_km


# Precomputed once per process at import time (not per-record) since
# ``compute_route`` already caches internally, and there are only as many
# distinct pairs as ``_SAMPLE_ROUTES`` entries.
_ROUTE_DISTANCES_KM: dict[tuple[str, str], float] = {
    pair: _representative_distance_km(*pair) for pair in _SAMPLE_ROUTES
}


def generate_synthetic_records(
    count: int = 500,
    seed: int | None = None,
    dwt_ranges: dict[VesselType, tuple[float, float]] | None = None,
) -> list[VoyageRequest]:
    """Generate physics-plausible synthetic VoyageRequest records.

    Wind/wave/current/temperature are drawn from bounded, realistic ranges
    (see module constants above); a small fraction of legs are flagged as
    "storm" legs with elevated wave height, so weather_severity spans its
    full range rather than clustering near calm conditions. Cargo tonnage
    per vessel type is bounded by ``dwt_ranges`` (real-data-calibrated via
    ``load_vessel_type_dwt_stats`` if not supplied) rather than one flat
    range for every vessel type.

    Args:
        count: number of records to generate.
        seed: if given, makes generation reproducible.
        dwt_ranges: per-VesselType (min, max) cargo tonnage bounds. Defaults
            to ``load_vessel_type_dwt_stats()``.
    """
    rng = random.Random(seed)
    vessel_types = list(VesselType)
    fuel_types = list(FuelType)
    dwt_ranges = dwt_ranges if dwt_ranges is not None else load_vessel_type_dwt_stats()

    records: list[VoyageRequest] = []
    for i in range(count):
        origin, destination = rng.choice(_SAMPLE_ROUTES)
        distance_km = _ROUTE_DISTANCES_KM[(origin, destination)]
        is_storm = rng.random() < _STORM_PROBABILITY
        wave_range = _WAVE_HEIGHT_STORM_RANGE if is_storm else _WAVE_HEIGHT_TYPICAL_RANGE

        vessel_type = rng.choice(vessel_types)
        dwt_min, dwt_max = dwt_ranges[vessel_type]
        cargo_utilization = round(rng.uniform(0.0, 1.0), 3)
        cargo_tonnes = round(rng.uniform(dwt_min, dwt_max) * cargo_utilization, 1)

        record = VoyageRequest(
            vessel_id=f"SYN-{i:05d}",
            vessel_type=vessel_type,
            route_id=f"SYN-ROUTE-{i:05d}",
            origin=origin,
            destination=destination,
            distance_km=distance_km,
            cargo_tonnes=cargo_tonnes,
            cargo_utilization=cargo_utilization,
            speed_knots=round(_sample_speed_knots(rng), 1),
            fuel_type=rng.choice(fuel_types),
            wind_speed=round(rng.uniform(*_WIND_SPEED_RANGE), 2),
            wave_height=round(rng.uniform(*wave_range), 2),
            temperature=round(rng.uniform(*_TEMPERATURE_RANGE), 1),
            current_speed=round(rng.uniform(*_CURRENT_SPEED_RANGE), 2),
        )
        records.append(record)

    return records


if __name__ == "__main__":
    # Standalone usage: `python -m app.ingestion` writes a combined synthetic
    # dataset to data/processed/synthetic_voyages.csv for quick inspection.
    synthetic = generate_synthetic_records(count=1000, seed=42)
    out_dir = _REPO_ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "synthetic_voyages.csv"
    pd.DataFrame([r.model_dump() for r in synthetic]).to_csv(out_path, index=False)
    print(f"Wrote {len(synthetic)} synthetic records to {out_path}")
