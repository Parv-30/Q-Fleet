"""FastAPI REST layer for optimization-service (Phase 4).

Mirrors data-service's/prediction-service's/emissions-service's app/main.py
conventions. Exposes:

    GET  /health     -- readiness check
    POST /optimize   -- {iterations?, swarm_size?, seed?, constraints?} -> {"pareto_front": [...]}

POST /optimize is expected to be SLOW: it runs a full QPSO + NSGA-II
optimization run, which means swarm_size * iterations RabbitMQ RPC round
trips to prediction-service, then the same number again to
emissions-service (see app/optimizer.py's module docstring). This is an
accepted tradeoff for a hackathon demo -- no background-job/polling
machinery is built for this phase, the request just blocks until the
whole run (and its Pareto-front extraction) completes.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.optimizer import run_optimization
from common.schemas import OptimizationCandidate, OptimizationConstraints

app = FastAPI(title="Q-Fleet AI Optimization Service")


class OptimizeRequest(BaseModel):
    """Request body for POST /optimize. All fields optional with sensible
    defaults -- see app/optimizer.py's run_optimization for what they
    control. `constraints` (see common/schemas.py's OptimizationConstraints)
    is the real-voyage-input pipeline: every field on it is itself
    optional, defaulting to fully-open/unconstrained QPSO search exactly
    as before this field existed."""

    iterations: int = Field(default=30, gt=0, description="QPSO iterations")
    swarm_size: int = Field(default=20, gt=0, description="QPSO particle count")
    seed: int | None = Field(default=None, description="RNG seed for reproducibility")
    constraints: OptimizationConstraints | None = Field(
        default=None, description="Optional real-voyage input filters (vessel/route/cargo/speed/fuel/weather/deadline)"
    )


class OptimizeResponse(BaseModel):
    pareto_front: list[OptimizationCandidate]


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok"}


@app.post("/optimize", response_model=OptimizeResponse)
async def optimize(request: OptimizeRequest) -> OptimizeResponse:
    pareto_front = run_optimization(
        iterations=request.iterations,
        swarm_size=request.swarm_size,
        seed=request.seed,
        constraints=request.constraints,
    )
    return OptimizeResponse(pareto_front=pareto_front)
