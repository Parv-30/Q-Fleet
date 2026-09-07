"""RabbitMQ RPC CLIENT for calling prediction-service and
emissions-service from optimization-service's QPSO inner loop.

Mirrors the servers' own module structure (see
prediction-service/app/rpc_server.py and
emissions-service/app/rpc_server.py): the pure "build request bytes" /
"parse response bytes" logic is factored out from the blocking pika I/O,
so the request/response contract can be unit-tested with no broker at
all, while the actual `call_prediction_rpc` / `call_emissions_rpc`
functions do the real blocking RPC round-trip (standard RabbitMQ RPC
client pattern -- see
https://www.rabbitmq.com/tutorials/tutorial-six-python.html):

    1. Declare an exclusive, auto-generated "callback" reply queue.
    2. Publish the request to the well-known server queue
       ("prediction_rpc_queue" / "emissions_rpc_queue") with `reply_to`
       set to that callback queue and a fresh `correlation_id`.
    3. Consume from the callback queue until a message with the matching
       `correlation_id` arrives; that message's body is the response.

Each call opens its own BlockingConnection and closes it when done --
simple and correct for a hackathon demo's optimization run (many calls in
a loop, but no concurrent callers within one process), at the cost of
per-call connection overhead. A production version would keep one
long-lived connection/channel across calls; not needed here.
"""

from __future__ import annotations

import json
import uuid

from pydantic import ValidationError

from common.schemas import EmissionResponse, FuelType, PredictionResponse, ProcessedFeatures

PREDICTION_QUEUE_NAME = "prediction_rpc_queue"
EMISSIONS_QUEUE_NAME = "emissions_rpc_queue"

# Give up waiting for a reply after this many seconds -- protects the QPSO
# loop from hanging forever if a downstream RPC server is down.
DEFAULT_TIMEOUT_SECONDS = 10.0


class RpcTimeoutError(Exception):
    """Raised when no correlated response arrives within the timeout."""


class RpcErrorResponse(Exception):
    """Raised when the RPC server returned a JSON {"error": ...} body
    (see prediction-service's/emissions-service's handle_*_request
    functions) instead of a valid response."""


# ---------------------------------------------------------------------------
# Pure request/response (de)serialization -- unit-testable without pika.
# ---------------------------------------------------------------------------


def build_prediction_request(features: ProcessedFeatures) -> bytes:
    """ProcessedFeatures -> JSON bytes, matching prediction-service's
    rpc_server.handle_prediction_request's expected request body exactly."""
    return features.model_dump_json().encode("utf-8")


def parse_prediction_response(body: bytes) -> PredictionResponse:
    """JSON bytes -> PredictionResponse, or raises RpcErrorResponse if the
    server returned an {"error": ...} body instead."""
    _raise_if_error_body(body)
    return PredictionResponse.model_validate_json(body)


def build_emissions_request(fuel_type: FuelType, fuel_consumption: float) -> bytes:
    """(fuel_type, fuel_consumption) -> JSON bytes, matching
    emissions-service's rpc_server.handle_emissions_request's expected
    request body exactly (its EmissionRpcRequest / EmissionRequest shape:
    {"fuel_type": ..., "fuel_consumption": ...})."""
    return json.dumps({"fuel_type": fuel_type.value, "fuel_consumption": fuel_consumption}).encode("utf-8")


def parse_emissions_response(body: bytes) -> EmissionResponse:
    """JSON bytes -> EmissionResponse, or raises RpcErrorResponse if the
    server returned an {"error": ...} body instead."""
    _raise_if_error_body(body)
    return EmissionResponse.model_validate_json(body)


def _raise_if_error_body(body: bytes) -> None:
    """Both RPC servers return a JSON {"error": "..."} body (never raise)
    on a malformed request -- detect that shape before attempting to
    parse the body as the expected success-response model, so callers get
    a clear RpcErrorResponse instead of an opaque pydantic ValidationError."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return  # not JSON at all; let the caller's model_validate_json raise naturally
    if isinstance(parsed, dict) and "error" in parsed:
        raise RpcErrorResponse(parsed["error"])


# ---------------------------------------------------------------------------
# Blocking pika I/O -- the actual RPC round-trip.
# ---------------------------------------------------------------------------


def _call_rpc(
    queue_name: str,
    request_body: bytes,
    connection_params,
    timeout_seconds: float,
) -> bytes:
    """Generic blocking RPC client round-trip, shared by both
    call_prediction_rpc and call_emissions_rpc: declare a callback queue,
    publish the request with reply_to + correlation_id, and consume until
    the matching correlation_id arrives (or timeout).

    `connection_params` is a pika.ConnectionParameters instance (or
    anything else pika.BlockingConnection accepts) -- passed through
    as-is so callers/tests control host/credentials/etc.
    """
    import pika  # imported lazily, mirrors the RPC servers' own lazy pika import

    connection = pika.BlockingConnection(connection_params)
    try:
        channel = connection.channel()
        callback_queue = channel.queue_declare(queue="", exclusive=True).method.queue

        correlation_id = str(uuid.uuid4())
        response_holder: dict[str, bytes] = {}

        def on_response(ch, method, props, body):
            if props.correlation_id == correlation_id:
                response_holder["body"] = body

        channel.basic_consume(queue=callback_queue, on_message_callback=on_response, auto_ack=True)

        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            properties=pika.BasicProperties(reply_to=callback_queue, correlation_id=correlation_id),
            body=request_body,
        )

        connection.process_data_events(time_limit=timeout_seconds)
        # process_data_events with time_limit processes what's available up
        # to that many seconds; loop until we actually have a body or run
        # out of total budget, since a single call may return before the
        # message has arrived.
        elapsed = timeout_seconds
        remaining = timeout_seconds
        while "body" not in response_holder and remaining > 0:
            step = min(0.5, remaining)
            connection.process_data_events(time_limit=step)
            remaining -= step

        if "body" not in response_holder:
            raise RpcTimeoutError(
                f"no response from '{queue_name}' within {timeout_seconds}s (correlation_id={correlation_id})"
            )
        return response_holder["body"]
    finally:
        connection.close()


def call_prediction_rpc(
    features: ProcessedFeatures,
    connection_params,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> PredictionResponse:
    """Blocking RPC call to prediction-service's "prediction_rpc_queue".
    Raises RpcTimeoutError on timeout, RpcErrorResponse if the server
    reports a parse/validation error, or pydantic's ValidationError if the
    response body doesn't match PredictionResponse."""
    request_body = build_prediction_request(features)
    response_body = _call_rpc(PREDICTION_QUEUE_NAME, request_body, connection_params, timeout_seconds)
    return parse_prediction_response(response_body)


def call_emissions_rpc(
    fuel_type: FuelType,
    fuel_consumption: float,
    connection_params,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> EmissionResponse:
    """Blocking RPC call to emissions-service's "emissions_rpc_queue".
    Raises RpcTimeoutError on timeout, RpcErrorResponse if the server
    reports a parse/validation error, or pydantic's ValidationError if the
    response body doesn't match EmissionResponse."""
    request_body = build_emissions_request(fuel_type, fuel_consumption)
    response_body = _call_rpc(EMISSIONS_QUEUE_NAME, request_body, connection_params, timeout_seconds)
    return parse_emissions_response(response_body)
