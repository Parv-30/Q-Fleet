# data-service

Phase 1 of Q-Fleet AI: cleans, validates, and feature-engineers voyage data
(`VoyageRequest` -> `ProcessedFeatures`) for prediction-service to consume.

## Data sourcing decision

We evaluated two public Kaggle datasets before writing any ingestion code —
see below for what each one actually contains. Neither is real per-voyage
telemetry jointly covering route, speed, cargo, weather, and fuel, which
turns out to be unavailable for free anywhere (it's normally proprietary or
paid AIS/noon-report data). Given that, `app/ingestion.py` uses:

1. **Real vessel specifications** from the Global Cargo Ships dataset (real
   ships, scraped from vesselfinder.com) to calibrate cargo-tonnage ranges
   per vessel type.
2. **A synthetic generator** for everything no free dataset provides jointly
   — route distance, speed, numeric weather, cargo utilization per leg — but
   built physics-plausibly and bounded by those real per-type cargo ranges,
   rather than picked from arbitrary constants.

This is a "hybrid" approach in spirit, but since even the fuel/CO2 dataset
that looked most promising turned out to be synthetic itself (see below), be
precise when describing it: it's a **calibrated synthetic dataset**, not a
mix of real and synthetic per-voyage records.

### Kaggle "Ship Fuel Consumption & CO2 Emissions Analysis" — evaluated, not used

Inspected directly on Kaggle (columns: `ship_id, ship_type, route_id, month,
distance, fuel_type, fuel_consumption, CO2_emissions, weather_conditions,
engine_efficiency`). **Not wired into ingestion** for two reasons stated on
its own Kaggle page: (1) it says the data "was generated to simulate
realistic maritime operations in Nigeria" — it's synthetic, not measured;
(2) its four vessel types (Fishing Trawler, Oil Service Boat, Surfer Boat,
Tanker Ship) are small craft, not the container/bulk/tanker/ro-ro/general-
cargo fleet this project models. Loading it as if it were real telemetry
would have been actively misleading. Its rough fuel-per-km magnitudes are
kept as a plausibility reference in `_FUEL_CO2_REFERENCE_NOTES` in
`app/ingestion.py` — never ingested as rows.

### Kaggle "Global Cargo Ships Dataset" — real vessel specs, used

Drop the CSV at **`data/raw/global_cargo_ships.csv`** (relative to the repo
root, i.e. `D:\sih\data\raw\global_cargo_ships.csv`; the "cleaned" file from
the dataset's three CSVs). Load with:

```python
from app.ingestion import load_global_cargo_ships_csv
result = load_global_cargo_ships_csv()  # or pass an explicit path
print(len(result.records), "loaded,", result.skipped_rows, "skipped")
```

Confirmed real headers (inspected on Kaggle's data-card preview — CC0,
scraped from vesselfinder.com):

| VoyageRequest field | real CSV header | note |
|---|---|---|
| vessel_id | `ship_name` | |
| vessel_type | `Company_Name` | Kaggle mislabels this column; its actual values/breakdown ("Bulk Carrier" 34%, "Container Ship" 26%, ...) are vessel type, not a company |
| cargo_tonnes (derived) | `dwt` | deadweight tonnage: total carrying capacity, not cargo actually aboard for a given leg |

Also present but unused by ingestion: `built_year`, `gt` (gross tonnage),
`length`, `width`. This dataset is vessel specs, not voyages — no route,
speed, weather, or fuel columns. Each row becomes one synthetic
"representative" leg (`cargo_tonnes = dwt * 0.7`, a fixed 14-knot speed,
placeholder origin/destination). Vessel-type strings are normalized via
`_VESSEL_TYPE_ALIASES` (e.g. `"Crude Oil Tanker"` -> `VesselType.TANKER`);
types with no equivalent in our enum (offshore support vessels, FSOs, cruise
ships) fall back to `GENERAL_CARGO`. Rows missing/with non-positive
deadweight are skipped.

`load_vessel_type_dwt_stats()` derives a real (min, max) cargo-tonnage range
per `VesselType` from this same file (falling back to
`_FALLBACK_DWT_RANGES`, anchored to the dataset's own published aggregate
histogram, for any type with fewer than 2 real rows or if the file isn't
present) — this is what calibrates the synthetic generator below.

### Synthetic weather/route/cargo generator

Route distance, speed, numeric weather, and per-leg cargo utilization aren't
jointly available in any free dataset, so `generate_synthetic_records`
fabricates them: wind speed 0-25 m/s, wave height 0-6m typically with a 5%
chance of a "storm" leg up to 10m, temperature -5 to 40°C, current speed -3
to 3 knots (signed) — while cargo tonnage per vessel type is bounded by the
real dwt ranges above rather than one flat range for every type.

Run standalone to write a CSV for quick inspection:

```bash
python -m app.ingestion
```

This writes 1000 seeded (`seed=42`) synthetic records to
`data/processed/synthetic_voyages.csv`. Or call it directly for a training
set:

```python
from app.ingestion import generate_synthetic_records
records = generate_synthetic_records(count=800, seed=42)  # reproducible, calibrated against real dwt data
```

## Running tests

```bash
python -m pytest tests/ -v
```
