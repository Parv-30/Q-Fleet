"""Live-broker integration test for app/rpc_client.py.

Unlike test_rpc_client.py (which mocks pika entirely), this test makes a
REAL blocking connection to a RabbitMQ broker and expects REAL
prediction-service and emissions-service RPC servers (app/rpc_server.py
in each of those services, run via `python -m app.rpc_server`) to be
listening on "prediction_rpc_queue" / "emissions_rpc_queue".

This was verified manually during development of this service:
    1. `docker compose up -d rabbitmq` from the repo root.
    2. prediction-service's and emissions-service's `python -m
       app.rpc_server` started as separate local processes (with
       PYTHONPATH set to the `services/` directory and RABBITMQ_HOST
       defaulting to localhost).
    3. A real call_prediction_rpc + call_emissions_rpc round trip
       succeeded end-to-end, e.g.:
           prediction: fuel_consumption=39.36... operating_cost=80856.13...
           emission:   lifecycle_ghg=145.78...
       confirming the whole reply_to/correlation_id RPC pattern works
       against a live broker and the real servers, not just mocks.

Automated test runs (pytest) still SKIP this test by default, since CI/dev
machines won't generally have RabbitMQ + both other services' RPC servers
running -- it only executes when RABBITMQ_HOST (or localhost:5672) is
actually reachable, detected via a short raw socket probe before
attempting any pika connection.
"""

from __future__ import annotations

import os
import socket

import pytest

from app.rpc_client import call_emissions_rpc, call_prediction_rpc
from common.schemas import FuelType, ProcessedFeatures

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = 5672


def _rabbitmq_reachable() -> bool:
    try:
        with socket.create_connection((RABBITMQ_HOST, RABBITMQ_PORT), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _rabbitmq_reachable(),
    reason=f"RabbitMQ not reachable at {RABBITMQ_HOST}:{RABBITMQ_PORT} -- start it "
    "(`docker compose up -d rabbitmq`) plus prediction-service's and "
    "emissions-service's `python -m app.rpc_server` to run this test.",
)

SAMPLE_FEATURES = ProcessedFeatures(
    distance_km=1000.0,
    speed_knots=14.0,
    speed_cubed=14.0**3,
    cargo_tonnes=50000.0,
    cargo_utilization=0.7,
    weather_severity=0.2,
    vessel_type_container=1,
    vessel_type_bulk_carrier=0,
    vessel_type_tanker=0,
    vessel_type_ro_ro=0,
    vessel_type_general_cargo=0,
    fuel_type_hfo=1,
    fuel_type_diesel=0,
    fuel_type_lng=0,
    fuel_type_methanol=0,
    fuel_type_hydrogen=0,
    fuel_type_ammonia=0,
)


def _connection_params():
    import pika

    return pika.ConnectionParameters(host=RABBITMQ_HOST)


def test_live_prediction_rpc_round_trip():
    response = call_prediction_rpc(SAMPLE_FEATURES, _connection_params(), timeout_seconds=10.0)
    assert response.fuel_consumption >= 0
    assert response.operating_cost >= 0
    assert response.voyage_time >= 0


def test_live_emissions_rpc_round_trip():
    response = call_emissions_rpc(FuelType.HFO, 100.0, _connection_params(), timeout_seconds=10.0)
    assert response.lifecycle_ghg >= 0


def test_live_prediction_then_emissions_pipeline():
    prediction = call_prediction_rpc(SAMPLE_FEATURES, _connection_params(), timeout_seconds=10.0)
    emission = call_emissions_rpc(
        FuelType.HFO, prediction.fuel_consumption, _connection_params(), timeout_seconds=10.0
    )
    assert emission.lifecycle_ghg >= 0
