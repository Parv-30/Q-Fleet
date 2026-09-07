"""FastAPI REST layer for prediction-service.

Loads the three XGBoost regressors trained by train.py (fuel_consumption,
operating_cost, voyage_time) once at import time and serves predictions over
HTTP, mirroring data-service's app/main.py conventions. Exposes:

    GET  /health               -- readiness check (models loaded or not)
    POST /predict               -- ProcessedFeatures -> PredictionResponse
    POST /predict/from-voyage   -- VoyageRequest -> (calls data-service) -> PredictionResponse

/predict/from-voyage is the endpoint optimization-service (Phase 4) will
actually call: it hides the data-service round trip so downstream callers
never need to compute ProcessedFeatures themselves.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
import xgboost as xgb
from fastapi import FastAPI, HTTPException

from common.schemas import PredictionResponse, ProcessedFeatures, VoyageRequest

# --- model directory resolution -------------------------------------------
# This file lives at services/prediction-service/app/main.py both locally and
# inside the Docker image, but the *repo layout around it* differs between
# the two:
#   - Local dev: repo root is 3 parents up from this file
#     (app -> prediction-service -> services -> repo root), and models live
#     at <repo root>/data/models (train.py's DEFAULT_OUTPUT_DIR).
#   - Docker: the Dockerfile's WORKDIR is /srv, and it does
#     `COPY data/models ./data/models` alongside `COPY .../app ./app`, so
#     inside the image the layout is flatter: /srv/app/main.py and
#     /srv/data/models are siblings (only 1 parent up from this file, not 3).
# Rather than branch on an environment variable, just check which candidate
# path actually exists on disk -- this works correctly in both environments
# without needing to know which one we're in, and defaults to the repo-root
# candidate if neither exists yet (so the resulting FileNotFoundError below
# points at the expected local-dev location).
_THIS_FILE_PARENTS = Path(__file__).resolve().parents
# Docker's flatter layout (/srv/app/main.py) only has 2 parents available
# (/srv/app, /srv) -- indexing parents[3] unconditionally raises IndexError
# there, so guard the repo-root candidate's existence check itself.
_REPO_ROOT_MODELS_DIR = (
    _THIS_FILE_PARENTS[3] / "data" / "models" if len(_THIS_FILE_PARENTS) > 3 else None
)
_DOCKER_SIBLING_MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models"

if _REPO_ROOT_MODELS_DIR is not None and _REPO_ROOT_MODELS_DIR.exists():
    MODELS_DIR = _REPO_ROOT_MODELS_DIR
elif _DOCKER_SIBLING_MODELS_DIR.exists():
    MODELS_DIR = _DOCKER_SIBLING_MODELS_DIR
else:
    MODELS_DIR = _REPO_ROOT_MODELS_DIR or _DOCKER_SIBLING_MODELS_DIR

# data-service's base URL. Docker Compose sets this to
# http://data-service:8000 for container-to-container networking; local dev
# outside Docker falls back to localhost.
DATA_SERVICE_URL = os.environ.get("DATA_SERVICE_URL", "http://localhost:8000")

logger = logging.getLogger(__name__)

# A raw XGBoost regressor has no built-in non-negativity constraint, so a
# genuinely bad misprediction (diagnosed for LNG/hydrogen fuel types near
# the training range's low-speed end) can come out negative. Silently
# clamping that straight to 0.0 (`max(pred, 0.0)`) launders a real model
# failure into an innocuous-looking "free voyage" -- which the QPSO
# optimizer then happily treats as a dominant, zero-cost candidate,
# flooding the Pareto front with physically nonsensical results. Instead:
#   1. a negative raw prediction is logged as a warning (with the input row
#      and the raw pre-clamp value) so it is discoverable in service logs
#      instead of invisible, and
#   2. the clamp floor is a small positive epsilon, not exactly 0.0, since
#      a real ship burns SOME fuel (and incurs SOME operating cost) for any
#      nonzero-distance voyage -- a floored value is visually
#      distinguishable from "the model confidently predicted zero" by any
#      downstream consumer (e.g. the optimizer) that checks for it.
_MIN_PLAUSIBLE_FUEL_CONSUMPTION = 0.01  # tonnes -- effectively "negligible but nonzero"
_MIN_PLAUSIBLE_OPERATING_COST = 0.01  # currency units

_MODEL_FILENAMES: dict[str, str] = {
    "fuel_consumption": "fuel_consumption_model.json",
    "operating_cost": "operating_cost_model.json",
    "voyage_time": "voyage_time_model.json",
}


def _load_models() -> dict[str, xgb.Booster]:
    """Load all three trained boosters once, at import time.

    xgb.Booster (rather than XGBRegressor) is used at serving time since it
    is the lighter-weight inference-only API and loading via
    `Booster.load_model()` is a direct match for train.py's
    `XGBRegressor.save_model()` output -- XGBoost's native JSON format is
    interchangeable between the two APIs.
    """
    models: dict[str, xgb.Booster] = {}
    for target, filename in _MODEL_FILENAMES.items():
        booster = xgb.Booster()
        booster.load_model(str(MODELS_DIR / filename))
        models[target] = booster
    return models


def _load_feature_columns() -> list[str]:
    with open(MODELS_DIR / "feature_columns.json") as f:
        return json.load(f)


_models = _load_models()
_feature_columns = _load_feature_columns()

app = FastAPI(title="Q-Fleet AI Prediction Service")


def processed_features_to_row(pf: ProcessedFeatures) -> list[float]:
    """ProcessedFeatures -> one feature-matrix row, in feature_columns.json
    order. Mirrors train.py's processed_features_to_row so serving-time
    column ordering can never silently drift from training-time ordering --
    replicated locally rather than imported since train.py is a standalone
    training script, not meant to be imported by the running service.
    """
    values = {
        "distance_km": pf.distance_km,
        "speed_knots": pf.speed_knots,
        "speed_cubed": pf.speed_cubed,
        "cargo_tonnes": pf.cargo_tonnes,
        "cargo_utilization": pf.cargo_utilization,
        "weather_severity": pf.weather_severity,
        # One-hot vessel_type / fuel_type columns -- see ProcessedFeatures
        # in common/schemas.py and train.py's FEATURE_COLUMNS for why these
        # replaced a single ordinal int per category. Keyed by column name
        # (not positional) and looked up via _feature_columns (loaded from
        # feature_columns.json, written by train.py), so this dict does not
        # need to be listed in any particular order itself -- correctness
        # only requires every name train.py could have written to also be
        # present here, which it is (mirrors train.py's FEATURE_COLUMNS
        # field-for-field).
        "vessel_type_container": float(pf.vessel_type_container),
        "vessel_type_bulk_carrier": float(pf.vessel_type_bulk_carrier),
        "vessel_type_tanker": float(pf.vessel_type_tanker),
        "vessel_type_ro_ro": float(pf.vessel_type_ro_ro),
        "vessel_type_general_cargo": float(pf.vessel_type_general_cargo),
        "fuel_type_hfo": float(pf.fuel_type_hfo),
        "fuel_type_diesel": float(pf.fuel_type_diesel),
        "fuel_type_lng": float(pf.fuel_type_lng),
        "fuel_type_methanol": float(pf.fuel_type_methanol),
        "fuel_type_hydrogen": float(pf.fuel_type_hydrogen),
        "fuel_type_ammonia": float(pf.fuel_type_ammonia),
    }
    return [values[column] for column in _feature_columns]


def _floor_prediction(target: str, raw_value: float, floor: float, row: list[float]) -> float:
    """Clamp a raw model prediction to a non-negative, non-zero-looking
    floor, logging a warning when the raw value was actually negative (a
    genuine model misprediction, not a normal rounding-to-zero case).

    See the module-level comment above `_MIN_PLAUSIBLE_FUEL_CONSUMPTION`
    for why this floors at a small epsilon rather than exactly 0.0, and why
    the negative case is logged rather than clamped silently.
    """
    if raw_value < 0:
        logger.warning(
            "prediction-service: raw %s prediction was negative (%.6f); "
            "flooring to %.6f. This indicates a model misprediction, not a "
            "genuine near-zero voyage cost -- input feature row: %s",
            target,
            raw_value,
            floor,
            dict(zip(_feature_columns, row)),
        )
        return floor
    return max(raw_value, floor) if raw_value < floor else raw_value


def _predict_from_row(row: list[float]) -> PredictionResponse:
    dmatrix = xgb.DMatrix([row], feature_names=_feature_columns)
    predictions = {target: float(model.predict(dmatrix)[0]) for target, model in _models.items()}
    return PredictionResponse(
        fuel_consumption=_floor_prediction(
            "fuel_consumption", predictions["fuel_consumption"], _MIN_PLAUSIBLE_FUEL_CONSUMPTION, row
        ),
        operating_cost=_floor_prediction(
            "operating_cost", predictions["operating_cost"], _MIN_PLAUSIBLE_OPERATING_COST, row
        ),
        # voyage_time has no analogous "genuinely bad misprediction" finding
        # from the diagnosis (the bug was specific to fuel_consumption /
        # operating_cost going negative for LNG/hydrogen at low speed) and
        # a floor of exactly 0.0 hours is physically sensible (a
        # zero-distance leg legitimately takes ~0 time), so it keeps the
        # simple non-negativity clamp rather than an epsilon floor.
        voyage_time=max(predictions["voyage_time"], 0.0),
    )


async def _fetch_features(request: VoyageRequest) -> ProcessedFeatures:
    """Call data-service's /features endpoint over HTTP. Raises
    HTTPException(502) on connection failure or a non-200 response -- no
    retry logic, a clear error is enough for a hackathon demo."""
    url = f"{DATA_SERVICE_URL}/features"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=request.model_dump(mode="json"), timeout=10.0)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"data-service request failed: {exc}") from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"data-service returned {response.status_code}: {response.text}",
        )

    return ProcessedFeatures(**response.json())


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "models_loaded": len(_models) == len(_MODEL_FILENAMES)}


@app.post("/predict", response_model=PredictionResponse)
async def predict(features: ProcessedFeatures) -> PredictionResponse:
    row = processed_features_to_row(features)
    return _predict_from_row(row)


@app.post("/predict/from-voyage", response_model=PredictionResponse)
async def predict_from_voyage(request: VoyageRequest) -> PredictionResponse:
    features = await _fetch_features(request)
    row = processed_features_to_row(features)
    return _predict_from_row(row)
