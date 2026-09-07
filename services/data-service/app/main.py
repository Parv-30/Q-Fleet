"""FastAPI REST layer for data-service.

Exposes FeaturePipeline over HTTP so prediction-service (Phase 2) can request
on-demand feature engineering instead of reimplementing it, and exposes a
/records read endpoint over whatever processed dataset ingestion eventually
produces on disk. /ports and /routes expose the real port catalog and real
searoute-based route distances (see app/port_catalog.py, app/routing.py) so
other services/UIs can look up real distances instead of hardcoding them.
"""

from __future__ import annotations

from pathlib import Path

from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query

from app.feature_pipeline import FeaturePipeline
from app.port_catalog import UnknownPortError, list_ports
from app.routing import RoutingError, compute_route
from common.schemas import ProcessedFeatures, VoyageRequest

# Expected location of the processed/engineered dataset once the ingestion
# pipeline lands. One-line fix here (path + read call) once that file format
# is settled — until then /records degrades gracefully to an empty list.
PROCESSED_RECORDS_PATH = Path("D:/sih/data/processed/voyages.parquet")

# FeaturePipeline is stateless and cheap to construct, but there is no reason
# to rebuild it on every request — a single module-level instance, created at
# import time, is the simplest correct pattern here (no startup I/O to defer,
# so a lifespan hook would only add ceremony).
_pipeline = FeaturePipeline()

app = FastAPI(title="Q-Fleet AI Data Service")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/features", response_model=ProcessedFeatures)
async def compute_features(request: VoyageRequest) -> ProcessedFeatures:
    return _pipeline.transform(request)


@app.post("/features/batch", response_model=list[ProcessedFeatures])
async def compute_features_batch(requests: list[VoyageRequest]) -> list[ProcessedFeatures]:
    return _pipeline.transform_batch(requests)


@app.get("/records")
async def get_records(
    vessel_type: str | None = Query(default=None),
    route_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
) -> list[dict]:
    """Query cleaned/engineered records from the processed dataset.

    Returns [] until ingestion has produced PROCESSED_RECORDS_PATH — this is
    deliberate rather than an error, since Phase 1 callers should be able to
    integrate against this endpoint before that dataset exists.
    """
    if not PROCESSED_RECORDS_PATH.exists():
        return []

    import pandas as pd

    df = pd.read_parquet(PROCESSED_RECORDS_PATH)

    if vessel_type is not None and "vessel_type" in df.columns:
        df = df[df["vessel_type"] == vessel_type]
    if route_id is not None and "route_id" in df.columns:
        df = df[df["route_id"] == route_id]
    if start_date is not None and "delivery_deadline" in df.columns:
        df = df[df["delivery_deadline"] >= start_date]
    if end_date is not None and "delivery_deadline" in df.columns:
        df = df[df["delivery_deadline"] <= end_date]

    return df.to_dict(orient="records")


@app.get("/ports")
async def get_ports() -> list[dict]:
    """The full known-port catalog, for populating an origin/destination dropdown."""
    return [asdict(port) for port in list_ports()]


@app.get("/routes")
async def get_routes(
    origin: str = Query(...),
    destination: str = Query(...),
) -> list[dict]:
    """Real sea-route distance option(s) between two known ports.

    Uses the `searoute` package (Marnet global shipping-lane graph) -- see
    app/routing.py's module docstring for the "not navigational-grade"
    caveat. Returns 1 option when a Suez-avoiding alternative wouldn't be
    meaningfully different, 2 when it is (e.g. Asia-Europe routes).
    """
    try:
        options = compute_route(origin, destination)
    except UnknownPortError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [asdict(option) for option in options]
