"""Tests for prediction-service's FastAPI serving layer (app/main.py).

Importing app.main triggers real model loading from data/models/, so
test_health doubles as a check that the trained models actually load
correctly -- if they don't, every test in this module fails at collection.
"""

from __future__ import annotations

import math

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_module
from common.schemas import ProcessedFeatures

client = TestClient(app)

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

VALID_VOYAGE = {
    "vessel_id": "V1",
    "vessel_type": "container",
    "route_id": "R1",
    "origin": "Mumbai",
    "destination": "Chennai",
    "distance_km": 1200.0,
    "cargo_tonnes": 40000.0,
    "cargo_utilization": 0.7,
    "speed_knots": 15.0,
    "fuel_type": "hfo",
    "wind_speed": 5.0,
    "wave_height": 1.5,
    "temperature": 28.0,
    "current_speed": 0.5,
}


def test_health_ok_and_models_loaded():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["models_loaded"] is True


def test_predict_valid_body_returns_prediction_response():
    response = client.post("/predict", json=VALID_FEATURES)
    assert response.status_code == 200
    body = response.json()
    for key in ("fuel_consumption", "operating_cost", "voyage_time"):
        assert key in body
        value = body[key]
        assert isinstance(value, (int, float))
        assert math.isfinite(value)
        assert value >= 0


def test_predict_missing_field_returns_422():
    incomplete = dict(VALID_FEATURES)
    del incomplete["speed_knots"]
    response = client.post("/predict", json=incomplete)
    assert response.status_code == 422


def test_predict_directionally_sane_for_varying_speed():
    low_speed = dict(VALID_FEATURES, speed_knots=10.0, speed_cubed=1000.0)
    high_speed = dict(VALID_FEATURES, speed_knots=20.0, speed_cubed=8000.0)

    low_response = client.post("/predict", json=low_speed)
    high_response = client.post("/predict", json=high_speed)

    assert low_response.status_code == 200
    assert high_response.status_code == 200

    low_body = low_response.json()
    high_body = high_response.json()
    for key in ("fuel_consumption", "operating_cost", "voyage_time"):
        assert math.isfinite(low_body[key])
        assert math.isfinite(high_body[key])
        assert low_body[key] >= 0
        assert high_body[key] >= 0


def test_predict_from_voyage_success(monkeypatch):
    async def fake_fetch_features(request):
        return ProcessedFeatures(**VALID_FEATURES)

    monkeypatch.setattr(main_module, "_fetch_features", fake_fetch_features)

    response = client.post("/predict/from-voyage", json=VALID_VOYAGE)
    assert response.status_code == 200
    body = response.json()
    for key in ("fuel_consumption", "operating_cost", "voyage_time"):
        assert key in body
        assert math.isfinite(body[key])
        assert body[key] >= 0


def test_predict_from_voyage_upstream_failure_returns_502(monkeypatch):
    async def fake_fetch_features(request):
        raise HTTPException(status_code=502, detail="data-service request failed: connection error")

    monkeypatch.setattr(main_module, "_fetch_features", fake_fetch_features)

    response = client.post("/predict/from-voyage", json=VALID_VOYAGE)
    assert response.status_code == 502


class TestFloorPrediction:
    """Tests for _floor_prediction, the visible-floor replacement for the
    old bare `max(pred, 0.0)` clamp (see the module-level comment above
    _MIN_PLAUSIBLE_FUEL_CONSUMPTION in app/main.py for the reasoning)."""

    def test_normal_in_distribution_prediction_is_unaffected(self):
        # A comfortably positive raw prediction, well above the epsilon
        # floor, should pass through unchanged.
        raw_value = 150.0
        result = main_module._floor_prediction(
            "fuel_consumption", raw_value, main_module._MIN_PLAUSIBLE_FUEL_CONSUMPTION, row=[0.0] * len(main_module._feature_columns)
        )
        assert result == raw_value

    def test_negative_raw_prediction_is_floored_to_epsilon_not_zero(self, caplog):
        # A genuinely negative raw prediction (the original bug's
        # symptom) must be floored to the small positive epsilon -- NOT
        # exactly 0.0 -- and must log a warning noting the raw value, so
        # the misprediction is discoverable rather than silently laundered
        # into an innocuous-looking "free voyage".
        row = [0.0] * len(main_module._feature_columns)
        with caplog.at_level("WARNING", logger=main_module.logger.name):
            result = main_module._floor_prediction(
                "fuel_consumption", -29.79, main_module._MIN_PLAUSIBLE_FUEL_CONSUMPTION, row
            )
        assert result == main_module._MIN_PLAUSIBLE_FUEL_CONSUMPTION
        assert result != 0.0
        assert any("negative" in rec.message for rec in caplog.records)
        assert any("-29.79" in rec.message for rec in caplog.records)

    def test_predict_endpoint_never_returns_a_bare_zero_for_a_negative_raw_prediction(self, monkeypatch):
        # End-to-end: force the underlying booster's raw output negative
        # (bypassing whatever the retrained model's own accuracy is) and
        # confirm /predict still returns a valid, floored, non-zero-looking
        # PredictionResponse instead of an exact 0.0.
        class _FakeBoosterPredictArray(list):
            def __getitem__(self, idx):
                return -42.0

        class _FakeBooster:
            def predict(self, dmatrix):
                return _FakeBoosterPredictArray([-42.0])

        monkeypatch.setitem(main_module._models, "fuel_consumption", _FakeBooster())

        response = client.post("/predict", json=VALID_FEATURES)
        assert response.status_code == 200
        body = response.json()
        assert body["fuel_consumption"] == main_module._MIN_PLAUSIBLE_FUEL_CONSUMPTION
        assert body["fuel_consumption"] != 0.0
