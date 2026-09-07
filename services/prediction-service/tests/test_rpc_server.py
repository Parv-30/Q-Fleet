"""Tests for prediction-service's RabbitMQ RPC handler (app/rpc_server.py).

These test the pure `handle_prediction_request` function only -- no real
RabbitMQ broker/connection is involved. Importing app.rpc_server (via
app.main) triggers the same real model loading as test_main.py, so these
tests exercise the actual trained XGBoost models, not mocks.
"""

from __future__ import annotations

import json
import math

from app.main import _predict_from_row, processed_features_to_row
from app.rpc_server import QUEUE_NAME, handle_prediction_request
from common.schemas import PredictionResponse, ProcessedFeatures

VALID_FEATURES = {
    "distance_km": 1200.0,
    "speed_knots": 15.0,
    "speed_cubed": 3375.0,
    "cargo_tonnes": 40000.0,
    "cargo_utilization": 0.7,
    "weather_severity": 0.2,
    "vessel_type_container": 1,
    "vessel_type_bulk_carrier": 0,
    "vessel_type_tanker": 0,
    "vessel_type_ro_ro": 0,
    "vessel_type_general_cargo": 0,
    "fuel_type_hfo": 1,
    "fuel_type_diesel": 0,
    "fuel_type_lng": 0,
    "fuel_type_methanol": 0,
    "fuel_type_hydrogen": 0,
    "fuel_type_ammonia": 0,
}


def test_queue_name_is_well_known():
    assert QUEUE_NAME == "prediction_rpc_queue"


def test_handle_prediction_request_valid_body_matches_direct_call():
    body = json.dumps(VALID_FEATURES).encode("utf-8")
    response_bytes = handle_prediction_request(body)
    response = PredictionResponse.model_validate_json(response_bytes)

    # Cross-check against calling the real prediction logic directly, the
    # same real loaded models test_main.py's HTTP tests exercise.
    features = ProcessedFeatures(**VALID_FEATURES)
    row = processed_features_to_row(features)
    expected = _predict_from_row(row)

    assert response.fuel_consumption == expected.fuel_consumption
    assert response.operating_cost == expected.operating_cost
    assert response.voyage_time == expected.voyage_time


def test_handle_prediction_request_returns_finite_nonnegative_values():
    body = json.dumps(VALID_FEATURES).encode("utf-8")
    response = PredictionResponse.model_validate_json(handle_prediction_request(body))

    assert math.isfinite(response.fuel_consumption)
    assert math.isfinite(response.operating_cost)
    assert math.isfinite(response.voyage_time)
    assert response.fuel_consumption >= 0
    assert response.operating_cost >= 0
    assert response.voyage_time >= 0


def test_handle_prediction_request_malformed_json_returns_error_body():
    body = b"not valid json{{{"
    result = handle_prediction_request(body)
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_prediction_request_missing_fields_returns_error_body():
    incomplete = dict(VALID_FEATURES)
    del incomplete["distance_km"]
    body = json.dumps(incomplete).encode("utf-8")
    result = handle_prediction_request(body)
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_prediction_request_wrong_types_returns_error_body():
    invalid = dict(VALID_FEATURES, distance_km="not-a-number")
    body = json.dumps(invalid).encode("utf-8")
    result = handle_prediction_request(body)
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_prediction_request_does_not_raise_on_bad_input():
    # The whole point of the error-body contract: never let a bad message
    # crash the consumer loop.
    try:
        handle_prediction_request(b"{}")
        handle_prediction_request(b"")
        handle_prediction_request(b"[1,2,3]")
    except Exception as exc:  # pragma: no cover - explicit failure message
        raise AssertionError(f"handle_prediction_request raised unexpectedly: {exc}")
