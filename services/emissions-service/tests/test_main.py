"""Tests for emissions-service's FastAPI serving layer (app/main.py)."""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_EMISSION_REQUEST = {
    "fuel_type": "hfo",
    "fuel_consumption": 500.0,
}

VALID_PREDICTION = {
    "fuel_consumption": 500.0,
    "operating_cost": 250000.0,
    "voyage_time": 120.0,
}

VALID_FROM_PREDICTION_REQUEST = {
    "fuel_type": "hfo",
    "prediction": VALID_PREDICTION,
}


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_emissions_valid_body_returns_emission_response():
    response = client.post("/emissions", json=VALID_EMISSION_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert "lifecycle_ghg" in body
    assert math.isfinite(body["lifecycle_ghg"])
    assert body["lifecycle_ghg"] > 0


def test_emissions_missing_field_returns_422():
    incomplete = dict(VALID_EMISSION_REQUEST)
    del incomplete["fuel_consumption"]
    response = client.post("/emissions", json=incomplete)
    assert response.status_code == 422


def test_emissions_invalid_fuel_type_returns_422():
    invalid = dict(VALID_EMISSION_REQUEST, fuel_type="coal")
    response = client.post("/emissions", json=invalid)
    assert response.status_code == 422


def test_emissions_negative_fuel_consumption_returns_422():
    invalid = dict(VALID_EMISSION_REQUEST, fuel_consumption=-10.0)
    response = client.post("/emissions", json=invalid)
    assert response.status_code == 422


def test_emissions_from_prediction_valid_body_returns_emission_response():
    response = client.post("/emissions/from-prediction", json=VALID_FROM_PREDICTION_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert "lifecycle_ghg" in body
    assert math.isfinite(body["lifecycle_ghg"])
    assert body["lifecycle_ghg"] > 0


def test_emissions_from_prediction_missing_fuel_type_returns_422():
    incomplete = {"prediction": VALID_PREDICTION}
    response = client.post("/emissions/from-prediction", json=incomplete)
    assert response.status_code == 422


def test_emissions_from_prediction_missing_prediction_returns_422():
    incomplete = {"fuel_type": "hfo"}
    response = client.post("/emissions/from-prediction", json=incomplete)
    assert response.status_code == 422


def test_emissions_and_from_prediction_agree_for_same_fuel_mass():
    direct = client.post("/emissions", json=VALID_EMISSION_REQUEST).json()
    from_prediction = client.post("/emissions/from-prediction", json=VALID_FROM_PREDICTION_REQUEST).json()
    assert direct["lifecycle_ghg"] == from_prediction["lifecycle_ghg"]
