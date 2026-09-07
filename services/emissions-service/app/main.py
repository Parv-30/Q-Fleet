"""FastAPI REST layer for emissions-service.

Pure Python/Pydantic logic -- no trained model to load, unlike
prediction-service, so there is no model-loading step at import time.
Mirrors data-service's and prediction-service's app/main.py conventions.
Exposes:

    GET  /health                    -- readiness check
    POST /emissions                 -- (fuel_type, fuel_consumption) -> EmissionResponse
    POST /emissions/from-prediction -- (fuel_type, PredictionResponse) -> EmissionResponse

/emissions/from-prediction is the endpoint optimization-service (Phase 4)
will actually call: it receives prediction-service's PredictionResponse
directly in its own request body and pipes fuel_consumption straight into
the emissions calculator, with no HTTP round-trip needed (unlike
prediction-service's /predict/from-voyage, which does need one, since it
must call data-service to get ProcessedFeatures from raw voyage input).
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.emission_factors import compute_lifecycle_emissions
from common.schemas import EmissionResponse, FuelType, PredictionResponse

app = FastAPI(title="Q-Fleet AI Emissions Service")


class EmissionRequest(BaseModel):
    """Request body for POST /emissions: the minimal inputs the lifecycle
    GHG calculator needs -- fuel type and fuel mass burned. Mirrors
    ProcessedFeatures/VoyageRequest's convention of a small, explicit
    Pydantic request model per endpoint."""

    fuel_type: FuelType
    fuel_consumption: float = Field(ge=0, description="tonnes")


class EmissionFromPredictionRequest(BaseModel):
    """Request body for POST /emissions/from-prediction: the fuel type
    (not carried by PredictionResponse itself) plus the upstream
    prediction-service output it should be combined with."""

    fuel_type: FuelType
    prediction: PredictionResponse


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok"}


@app.post("/emissions", response_model=EmissionResponse)
async def emissions(request: EmissionRequest) -> EmissionResponse:
    return compute_lifecycle_emissions(request.fuel_type, request.fuel_consumption)


@app.post("/emissions/from-prediction", response_model=EmissionResponse)
async def emissions_from_prediction(request: EmissionFromPredictionRequest) -> EmissionResponse:
    return compute_lifecycle_emissions(request.fuel_type, request.prediction.fuel_consumption)
