"""Live-broker end-to-end test for app/optimizer.py's run_optimization.

Like test_rpc_client_integration.py, this only runs when RabbitMQ (plus
prediction-service's and emissions-service's RPC servers) is actually
reachable -- see that file for the exact manual verification steps used
during development. Uses a deliberately tiny swarm_size/iterations so the
test runs quickly (a handful of RPC round trips, not the default
swarm_size=20/iterations=30 which would be slow -- see app/main.py's
module docstring on why /optimize itself is expected to be slow at
production-scale settings).
"""

from __future__ import annotations

import os
import socket

import pytest

from app.optimizer import run_optimization
from common.schemas import OptimizationCandidate

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


def test_run_optimization_small_scale_returns_pareto_front():
    pareto_front = run_optimization(iterations=3, swarm_size=5, seed=42)

    assert len(pareto_front) >= 1
    for candidate in pareto_front:
        assert isinstance(candidate, OptimizationCandidate)
        assert candidate.objectives is not None
        assert candidate.objectives.fuel_consumption >= 0
        assert candidate.objectives.lifecycle_ghg >= 0
        assert 0.0 <= candidate.objectives.reliability <= 1.0


def test_run_optimization_reproducible_with_seed():
    front_a = run_optimization(iterations=3, swarm_size=5, seed=99)
    front_b = run_optimization(iterations=3, swarm_size=5, seed=99)

    assert len(front_a) == len(front_b)
    assert {c.vessel for c in front_a} == {c.vessel for c in front_b}
    assert {round(c.speed, 6) for c in front_a} == {round(c.speed, 6) for c in front_b}
