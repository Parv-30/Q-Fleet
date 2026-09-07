"""Offline training pipeline: synthetic voyages -> three XGBoost regressors.

Trains three independent XGBRegressor models (fuel_consumption,
operating_cost, voyage_time) on data-service's calibrated synthetic voyage
generator, using physics_model.compute_physics_labels as ground truth and
FeaturePipeline.transform as the feature engineering step -- the same
transform the running service will use at inference time, so training-time
and serving-time features cannot drift apart.

Three separate single-output regressors are used instead of one multi-output
model because boosted trees don't natively share structure across outputs
the way a neural net can; three independently-tuned XGBRegressors are simpler
to reason about, debug, and (if ever needed) re-tune per target than a
multi-output wrapper, at the cost of some redundant computation that is
irrelevant for a hackathon-scale offline training run.

This script is standalone (run via `python train.py` from
services/prediction-service) and is NOT part of the running FastAPI service;
the serving code calls data-service over HTTP and loads the saved model
files instead of re-running any of this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

# Allow `from common.schemas import ...` and `from app... import ...` to
# resolve when this script is run directly (`python train.py`), mirroring
# conftest.py's setup for pytest. Needed here too since a standalone script
# invocation doesn't go through conftest.py at all.
_SERVICES_DIR = Path(__file__).resolve().parent.parent
_THIS_SERVICE_DIR = Path(__file__).resolve().parent
for _path in (_SERVICES_DIR, _THIS_SERVICE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.physics_model import compute_physics_labels

# --- cross-service import shim -------------------------------------------
# This training script needs data-service's synthetic voyage generator and
# feature pipeline, but the two services are sibling directories that are
# not (yet) packaged as installable libraries -- and both services happen to
# name their package `app`, so a plain sys.path insertion would just resolve
# `app` to whichever service's package Python imported first (prediction-
# service's own `app.physics_model`, already imported above). To reach
# data-service's `app` package under its own name without that collision, we
# load it directly from file via importlib and register it under a distinct
# sys.modules key. This is specific to this offline, one-off training
# script -- the actual running prediction-service calls data-service over
# HTTP instead, so this shim is never loaded by any deployed service code.
import importlib.util
import types

_DATA_SERVICE_APP_DIR = Path(__file__).resolve().parents[1] / "data-service" / "app"


def _load_data_service_module(module_name: str, file_name: str) -> types.ModuleType:
    full_name = f"data_service_{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, _DATA_SERVICE_APP_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


# ingestion.py does `from app.routing import compute_route`, and routing.py
# does `from app.port_catalog import ...` -- both resolve against whatever
# `app` package is already in sys.modules, which by this point is
# prediction-service's own `app` (imported above for physics_model), not
# data-service's. Load data-service's port_catalog/routing modules the same
# file-based way and register them under `app.port_catalog`/`app.routing`
# *before* loading ingestion.py, so ingestion's plain `from app.X import Y`
# statements resolve to data-service's real modules instead of either
# colliding with prediction-service's `app` package or raising
# ModuleNotFoundError.
import app as _prediction_service_app_package

_port_catalog_module = _load_data_service_module("port_catalog", "port_catalog.py")
sys.modules["app.port_catalog"] = _port_catalog_module
setattr(_prediction_service_app_package, "port_catalog", _port_catalog_module)

# routing.py's own `from app.port_catalog import ...` must resolve *after*
# the registration above, hence loading it only now.
_routing_module = _load_data_service_module("routing", "routing.py")
sys.modules["app.routing"] = _routing_module
setattr(_prediction_service_app_package, "routing", _routing_module)

_feature_pipeline_module = _load_data_service_module("feature_pipeline", "feature_pipeline.py")
_ingestion_module = _load_data_service_module("ingestion", "ingestion.py")

FeaturePipeline = _feature_pipeline_module.FeaturePipeline
generate_synthetic_records = _ingestion_module.generate_synthetic_records

# Column order used to build the feature matrix X. XGBoost's native
# .save_model()/Booster format does not persist input feature names for a
# plain numpy-array-trained model, so this exact order must be replayed
# verbatim by any serving code reconstructing a feature row -- hence it is
# also written out to feature_columns.json alongside the model files.
FEATURE_COLUMNS: list[str] = [
    "distance_km",
    "speed_knots",
    "speed_cubed",
    "cargo_tonnes",
    "cargo_utilization",
    "weather_severity",
    # One-hot encoded vessel_type / fuel_type columns (see ProcessedFeatures
    # in common/schemas.py for why these replaced a single ordinal int per
    # category) -- listed in the same order the fields are declared on
    # ProcessedFeatures so this list and that model can never silently
    # drift apart.
    "vessel_type_container",
    "vessel_type_bulk_carrier",
    "vessel_type_tanker",
    "vessel_type_ro_ro",
    "vessel_type_general_cargo",
    "fuel_type_hfo",
    "fuel_type_diesel",
    "fuel_type_lng",
    "fuel_type_methanol",
    "fuel_type_hydrogen",
    "fuel_type_ammonia",
]

# Targets trained/evaluated, and the filename each model is saved under.
_TARGET_MODEL_FILENAMES: dict[str, str] = {
    "fuel_consumption": "fuel_consumption_model.json",
    "operating_cost": "operating_cost_model.json",
    "voyage_time": "voyage_time_model.json",
}

TEST_SIZE = 0.2
RANDOM_STATE = 42

# Reasonable, un-tuned defaults -- adequate for a hackathon deliverable, not
# the result of a hyperparameter search.
XGB_PARAMS = dict(
    n_estimators=200,
    max_depth=6,
    learning_rate=0.1,
    random_state=RANDOM_STATE,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data" / "models"


def processed_features_to_row(pf) -> list[float]:
    """ProcessedFeatures -> one feature-matrix row, in FEATURE_COLUMNS order.

    Pulled out as its own function so the row-building logic (the one part
    of this script most likely to silently drift from FEATURE_COLUMNS) is
    unit-testable without running any training.
    """
    return [
        pf.distance_km,
        pf.speed_knots,
        pf.speed_cubed,
        pf.cargo_tonnes,
        pf.cargo_utilization,
        pf.weather_severity,
        float(pf.vessel_type_container),
        float(pf.vessel_type_bulk_carrier),
        float(pf.vessel_type_tanker),
        float(pf.vessel_type_ro_ro),
        float(pf.vessel_type_general_cargo),
        float(pf.fuel_type_hfo),
        float(pf.fuel_type_diesel),
        float(pf.fuel_type_lng),
        float(pf.fuel_type_methanol),
        float(pf.fuel_type_hydrogen),
        float(pf.fuel_type_ammonia),
    ]


def train_and_save(count: int, seed: int, output_dir: Path) -> dict:
    """Generate synthetic data, train the three regressors, save models +
    feature column order, and return a metrics/summary dict.

    Kept separate from the __main__ block so tests can run this at a small
    `count` against a tmp_path output_dir instead of re-running the full
    5000-record training (or not testing this flow at all).
    """
    records = generate_synthetic_records(count=count, seed=seed)

    pipeline = FeaturePipeline()
    features = pipeline.transform_batch(records)
    labels = [compute_physics_labels(r) for r in records]

    X = np.array([processed_features_to_row(pf) for pf in features], dtype=float)
    y_fuel = np.array([label.fuel_consumption for label in labels], dtype=float)
    y_cost = np.array([label.operating_cost for label in labels], dtype=float)
    y_time = np.array([label.voyage_time for label in labels], dtype=float)

    indices = np.arange(len(records))
    train_idx, test_idx = train_test_split(indices, test_size=TEST_SIZE, random_state=RANDOM_STATE)

    X_train, X_test = X[train_idx], X[test_idx]
    targets = {"fuel_consumption": y_fuel, "operating_cost": y_cost, "voyage_time": y_time}

    output_dir.mkdir(parents=True, exist_ok=True)

    models: dict[str, XGBRegressor] = {}
    metrics: dict[str, dict[str, float]] = {}

    for name, y in targets.items():
        y_train, y_test = y[train_idx], y[test_idx]

        model = XGBRegressor(**XGB_PARAMS)
        model.fit(X_train, y_train)

        predictions = model.predict(X_test)
        metrics[name] = {
            "r2": float(r2_score(y_test, predictions)),
            "mae": float(mean_absolute_error(y_test, predictions)),
        }

        model.save_model(str(output_dir / _TARGET_MODEL_FILENAMES[name]))
        models[name] = model

    with open(output_dir / "feature_columns.json", "w") as f:
        json.dump(FEATURE_COLUMNS, f, indent=2)

    return {
        "num_records": len(records),
        "train_size": len(train_idx),
        "test_size": len(test_idx),
        "metrics": metrics,
        "models": models,
    }


def _print_summary(results: dict) -> None:
    print(f"Generated {results['num_records']} synthetic voyage records")
    print(f"Train/test split: {results['train_size']} train / {results['test_size']} test\n")
    print(f"{'Target':<20}{'R2':>10}{'MAE':>14}")
    for name, m in results["metrics"].items():
        print(f"{name:<20}{m['r2']:>10.4f}{m['mae']:>14.4f}")


if __name__ == "__main__":
    results = train_and_save(count=5000, seed=42, output_dir=DEFAULT_OUTPUT_DIR)
    _print_summary(results)
