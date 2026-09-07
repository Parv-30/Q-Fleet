"""Tests for app/rpc_client.py.

Two layers, matching the module's own split:
  1. Pure request/response (de)serialization -- build_*_request /
     parse_*_response -- tested directly, no pika/broker involved.
  2. The blocking call_*_rpc functions' pika I/O -- tested here with pika
     mocked out (a real BlockingConnection is never constructed), so these
     tests always run in any environment without a broker.

A real, live-broker integration test additionally exists in
test_rpc_client_integration.py, guarded to skip automatically if RabbitMQ
plus the prediction-service/emissions-service RPC servers aren't actually
reachable -- see that file for what was verified end-to-end.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.rpc_client import (
    EMISSIONS_QUEUE_NAME,
    PREDICTION_QUEUE_NAME,
    RpcErrorResponse,
    RpcTimeoutError,
    build_emissions_request,
    build_prediction_request,
    call_emissions_rpc,
    call_prediction_rpc,
    parse_emissions_response,
    parse_prediction_response,
)
from common.schemas import EmissionResponse, FuelType, PredictionResponse, ProcessedFeatures

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


# --- pure (de)serialization ------------------------------------------------


def test_build_prediction_request_round_trips_processed_features():
    body = build_prediction_request(SAMPLE_FEATURES)
    assert ProcessedFeatures.model_validate_json(body) == SAMPLE_FEATURES


def test_parse_prediction_response_valid_body():
    expected = PredictionResponse(fuel_consumption=100.0, operating_cost=5000.0, voyage_time=48.0)
    body = expected.model_dump_json().encode("utf-8")
    assert parse_prediction_response(body) == expected


def test_parse_prediction_response_error_body_raises():
    body = json.dumps({"error": "invalid request body: boom"}).encode("utf-8")
    with pytest.raises(RpcErrorResponse):
        parse_prediction_response(body)


def test_build_emissions_request_matches_server_expected_shape():
    body = build_emissions_request(FuelType.LNG, 250.5)
    parsed = json.loads(body)
    assert parsed == {"fuel_type": "lng", "fuel_consumption": 250.5}


def test_parse_emissions_response_valid_body():
    expected = EmissionResponse(lifecycle_ghg=800.0)
    body = expected.model_dump_json().encode("utf-8")
    assert parse_emissions_response(body) == expected


def test_parse_emissions_response_error_body_raises():
    body = json.dumps({"error": "invalid request body: boom"}).encode("utf-8")
    with pytest.raises(RpcErrorResponse):
        parse_emissions_response(body)


def test_queue_names_are_well_known():
    assert PREDICTION_QUEUE_NAME == "prediction_rpc_queue"
    assert EMISSIONS_QUEUE_NAME == "emissions_rpc_queue"


# --- blocking call_*_rpc with pika mocked ----------------------------------


def _make_mock_pika_module(response_body: bytes):
    """Build a MagicMock standing in for the `pika` module, wired so that
    `_call_rpc`'s usage pattern (declare queue, basic_consume, publish,
    process_data_events) ends with the on_response callback having been
    invoked with `response_body` under the correlation_id the code itself
    generated -- exercised by making queue_declare return a fake queue
    name and basic_consume capture the callback, then process_data_events
    immediately invoke it with a matching correlation_id captured from the
    publish call.
    """
    mock_pika = MagicMock()
    mock_connection = MagicMock()
    mock_channel = MagicMock()

    mock_pika.BlockingConnection.return_value = mock_connection
    mock_connection.channel.return_value = mock_channel

    mock_channel.queue_declare.return_value.method.queue = "amq.gen-callback-queue"

    captured = {}

    def fake_basic_consume(queue, on_message_callback, auto_ack):
        captured["callback"] = on_message_callback

    def fake_basic_publish(exchange, routing_key, properties, body):
        captured["correlation_id"] = properties.correlation_id
        captured["routing_key"] = routing_key
        captured["request_body"] = body

    def fake_process_data_events(time_limit=None):
        # Simulate the reply arriving on the very first poll.
        if "callback" in captured and "response_delivered" not in captured:
            captured["response_delivered"] = True
            fake_method = MagicMock()
            fake_props = MagicMock()
            fake_props.correlation_id = captured["correlation_id"]
            captured["callback"](mock_channel, fake_method, fake_props, response_body)

    mock_channel.basic_consume.side_effect = fake_basic_consume
    mock_channel.basic_publish.side_effect = fake_basic_publish
    mock_connection.process_data_events.side_effect = fake_process_data_events

    # BasicProperties needs to be a real-ish object carrying reply_to/correlation_id
    def fake_basic_properties(reply_to=None, correlation_id=None):
        props = MagicMock()
        props.reply_to = reply_to
        props.correlation_id = correlation_id
        return props

    mock_pika.BasicProperties.side_effect = fake_basic_properties

    return mock_pika, mock_connection, mock_channel, captured


def test_call_prediction_rpc_happy_path_with_mocked_pika():
    expected = PredictionResponse(fuel_consumption=88.0, operating_cost=3000.0, voyage_time=40.0)
    mock_pika, mock_connection, mock_channel, captured = _make_mock_pika_module(
        expected.model_dump_json().encode("utf-8")
    )

    with patch.dict("sys.modules", {"pika": mock_pika}):
        result = call_prediction_rpc(SAMPLE_FEATURES, connection_params=object())

    assert result == expected
    assert captured["routing_key"] == PREDICTION_QUEUE_NAME
    mock_connection.close.assert_called_once()


def test_call_emissions_rpc_happy_path_with_mocked_pika():
    expected = EmissionResponse(lifecycle_ghg=444.0)
    mock_pika, mock_connection, mock_channel, captured = _make_mock_pika_module(
        expected.model_dump_json().encode("utf-8")
    )

    with patch.dict("sys.modules", {"pika": mock_pika}):
        result = call_emissions_rpc(FuelType.HFO, 150.0, connection_params=object())

    assert result == expected
    assert captured["routing_key"] == EMISSIONS_QUEUE_NAME
    assert json.loads(captured["request_body"]) == {"fuel_type": "hfo", "fuel_consumption": 150.0}
    mock_connection.close.assert_called_once()


def test_call_prediction_rpc_closes_connection_even_on_error():
    error_body = json.dumps({"error": "bad request"}).encode("utf-8")
    mock_pika, mock_connection, mock_channel, captured = _make_mock_pika_module(error_body)

    with patch.dict("sys.modules", {"pika": mock_pika}):
        with pytest.raises(RpcErrorResponse):
            call_prediction_rpc(SAMPLE_FEATURES, connection_params=object())

    mock_connection.close.assert_called_once()


def test_call_prediction_rpc_times_out_when_no_response_arrives():
    mock_pika = MagicMock()
    mock_connection = MagicMock()
    mock_channel = MagicMock()
    mock_pika.BlockingConnection.return_value = mock_connection
    mock_connection.channel.return_value = mock_channel
    mock_channel.queue_declare.return_value.method.queue = "amq.gen-callback-queue"
    mock_pika.BasicProperties.side_effect = lambda reply_to=None, correlation_id=None: MagicMock(
        reply_to=reply_to, correlation_id=correlation_id
    )
    # process_data_events never delivers anything -- response_holder stays empty.
    mock_connection.process_data_events.return_value = None

    with patch.dict("sys.modules", {"pika": mock_pika}):
        with pytest.raises(RpcTimeoutError):
            call_prediction_rpc(SAMPLE_FEATURES, connection_params=object(), timeout_seconds=0.1)

    mock_connection.close.assert_called_once()
