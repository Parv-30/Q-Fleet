"""RabbitMQ RPC server for prediction-service.

Exposes the same prediction logic as POST /predict in app/main.py, but over
a RabbitMQ RPC queue instead of HTTP. This is what optimization-service's
QPSO inner loop calls: per the project's architecture, calls that happen
many times per optimization run (once per candidate per iteration) go over
RabbitMQ RPC rather than plain HTTP, to avoid hammering the REST layer and
to keep the inner loop on the same request/reply pattern used elsewhere in
the system for synchronous inter-service calls.

Message contract (textbook RabbitMQ RPC pattern -- see
https://www.rabbitmq.com/tutorials/tutorial-six-python.html):
    - Clients publish a request to queue "prediction_rpc_queue" with the
      `reply_to` property set to their own callback queue and a unique
      `correlation_id`.
    - Request body: JSON-serialized ProcessedFeatures
      (ProcessedFeatures.model_dump_json()).
    - This server consumes the message, computes the prediction, and
      publishes the response to the `reply_to` queue, echoing back the same
      `correlation_id`.
    - Response body: JSON-serialized PredictionResponse
      (PredictionResponse.model_dump_json()), or a JSON error object of the
      form {"error": "<message>"} if the request body could not be parsed.

The module is split into:
    - `handle_prediction_request(body: bytes) -> bytes`: pure function, no
      pika/connection dependencies. Parses the request, calls the existing
      `processed_features_to_row` / `_predict_from_row` logic from
      app.main (no duplicated prediction logic), and returns the response
      bytes. This is the unit-testable core -- see tests/test_rpc_server.py.
    - `start_rpc_server()`: wires the pure function up to a real pika
      connection/channel and blocks forever consuming messages. Not
      exercised by unit tests (requires a running broker).

TODO: this needs to run as a second process alongside uvicorn in
production -- e.g. via a supervisor process or a separate container/
deployment; not wired into the Dockerfile CMD for this phase, run manually
or via a test harness for now (e.g. `python -m app.rpc_server`).
"""

from __future__ import annotations

import json
import os

from pydantic import ValidationError

from app.main import _predict_from_row, processed_features_to_row
from common.schemas import ProcessedFeatures

# Docker Compose sets this to "rabbitmq" for container networking (the
# service is named "rabbitmq" in docker-compose.yml); local dev outside
# Docker falls back to localhost -- mirrors prediction-service's
# DATA_SERVICE_URL env var pattern in app/main.py.
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")

QUEUE_NAME = "prediction_rpc_queue"


class InvalidRequestError(Exception):
    """Raised internally when an RPC request body cannot be parsed into a
    ProcessedFeatures instance. Not raised out of handle_prediction_request
    itself -- that function catches this and returns a JSON error body
    instead, so a single bad message can never crash the consumer."""


def handle_prediction_request(body: bytes) -> bytes:
    """Pure request/response handler: JSON bytes in, JSON bytes out.

    No pika or RabbitMQ dependency -- this is the unit-testable core of the
    RPC server. Given a JSON-serialized ProcessedFeatures body, returns a
    JSON-serialized PredictionResponse body computed via the exact same
    `processed_features_to_row` + `_predict_from_row` path that POST
    /predict uses in app/main.py.

    On malformed/invalid input (bad JSON, missing/invalid fields), returns
    a JSON object of the form {"error": "<description>"} instead of
    raising -- callers (the pika consume callback) should always get bytes
    back to publish, never an exception that could take down the consumer
    loop.
    """
    try:
        features = ProcessedFeatures.model_validate_json(body)
    except (ValidationError, ValueError) as exc:
        return json.dumps({"error": f"invalid request body: {exc}"}).encode("utf-8")

    row = processed_features_to_row(features)
    response = _predict_from_row(row)
    return response.model_dump_json().encode("utf-8")


def start_rpc_server() -> None:
    """Connect to RabbitMQ, declare the RPC queue, and consume forever.

    Blocking call -- intended to run as its own process (see module
    docstring TODO), not inside the FastAPI/uvicorn event loop.
    """
    import pika  # imported lazily so the pure handler above never requires pika at import time

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME)
    channel.basic_qos(prefetch_count=1)

    def on_request(ch, method, props, body):
        response = handle_prediction_request(body)
        ch.basic_publish(
            exchange="",
            routing_key=props.reply_to,
            properties=pika.BasicProperties(correlation_id=props.correlation_id),
            body=response,
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_request)

    print(f" [x] Awaiting prediction RPC requests on '{QUEUE_NAME}' (host={RABBITMQ_HOST})")
    channel.start_consuming()


if __name__ == "__main__":
    start_rpc_server()
