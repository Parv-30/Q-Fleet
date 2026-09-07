"""Tests for emissions-service's RabbitMQ RPC handler (app/rpc_server.py).

These test the pure `handle_emissions_request` function only -- no real
RabbitMQ broker/connection is involved. They exercise the real
compute_lifecycle_emissions calculation (no mocks), matching test_main.py's
approach for the REST /emissions endpoint.
"""

from __future__ import annotations

import json
import math

from app.emission_factors import compute_lifecycle_emissions
from app.rpc_server import QUEUE_NAME, handle_emissions_request
from common.schemas import EmissionResponse, FuelType

VALID_REQUEST = {
    "fuel_type": "hfo",
    "fuel_consumption": 500.0,
}


def test_queue_name_is_well_known():
    assert QUEUE_NAME == "emissions_rpc_queue"


def test_handle_emissions_request_valid_body_matches_direct_call():
    body = json.dumps(VALID_REQUEST).encode("utf-8")
    response = EmissionResponse.model_validate_json(handle_emissions_request(body))

    expected = compute_lifecycle_emissions(FuelType.HFO, 500.0)
    assert response.lifecycle_ghg == expected.lifecycle_ghg


def test_handle_emissions_request_returns_finite_positive_value():
    body = json.dumps(VALID_REQUEST).encode("utf-8")
    response = EmissionResponse.model_validate_json(handle_emissions_request(body))
    assert math.isfinite(response.lifecycle_ghg)
    assert response.lifecycle_ghg > 0


def test_handle_emissions_request_all_fuel_types_match_direct_call():
    for fuel_type in FuelType:
        body = json.dumps({"fuel_type": fuel_type.value, "fuel_consumption": 250.0}).encode("utf-8")
        response = EmissionResponse.model_validate_json(handle_emissions_request(body))
        expected = compute_lifecycle_emissions(fuel_type, 250.0)
        assert response.lifecycle_ghg == expected.lifecycle_ghg


def test_handle_emissions_request_malformed_json_returns_error_body():
    result = handle_emissions_request(b"not valid json{{{")
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_emissions_request_missing_fields_returns_error_body():
    incomplete = {"fuel_type": "hfo"}
    result = handle_emissions_request(json.dumps(incomplete).encode("utf-8"))
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_emissions_request_invalid_fuel_type_returns_error_body():
    invalid = {"fuel_type": "coal", "fuel_consumption": 100.0}
    result = handle_emissions_request(json.dumps(invalid).encode("utf-8"))
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_emissions_request_negative_fuel_consumption_returns_error_body():
    invalid = {"fuel_type": "hfo", "fuel_consumption": -10.0}
    result = handle_emissions_request(json.dumps(invalid).encode("utf-8"))
    parsed = json.loads(result)
    assert "error" in parsed


def test_handle_emissions_request_does_not_raise_on_bad_input():
    try:
        handle_emissions_request(b"{}")
        handle_emissions_request(b"")
        handle_emissions_request(b"[1,2,3]")
    except Exception as exc:  # pragma: no cover - explicit failure message
        raise AssertionError(f"handle_emissions_request raised unexpectedly: {exc}")
