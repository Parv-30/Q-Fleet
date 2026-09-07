# Q-Fleet AI

Smart India Hackathon 2026 — Problem Statement 26138, "Quantum-Inspired Fuel
Consumption Prediction and Green Fleet Optimization" (theme: Smart Vehicles).

Q-Fleet AI is a decision-support platform for green fleet optimization in
maritime shipping: it predicts a vessel's fuel consumption, operating cost,
and voyage time with an ensemble ML model, converts that into lifecycle GHG
emissions, then searches the space of fleet configurations (vessel, route,
speed, fuel type) with a quantum-inspired optimizer (QPSO) whose candidates
are scored by calling back into the prediction and emissions logic, before
ranking the scored population with NSGA-II into a Pareto front that a
dashboard presents to an operator for a deployment decision.

## Parameter classification

| Category | Contents |
|---|---|
| User-controlled parameters | vessel selection, route/route constraints, cargo requirement, speed limits/preferences, fuel preferences, delivery deadline, operational constraints |
| Automatically obtained parameters | weather, AIS information, historical voyage information, vessel database information, fuel/emission data |
| Data-processing outputs | cleaned data, normalized data, engineered features |
| ML-generated outputs | fuel consumption, operating cost, voyage time |
| Emission-model outputs | lifecycle GHG emissions |
| Optimization-generated values | optimal vessel, optimal route, optimal speed, optimal fuel, Pareto-optimal combinations |
| Final dashboard outputs | scenario comparison, trade-offs, recommended configurations, deployment recommendation |

## Architecture

Five microservices, one added per phase, communicating over RabbitMQ (RPC
request/reply for calls needing a synchronous answer, plain queues for
fire-and-forget jobs), orchestrated locally with Docker Compose.

| Phase | Service | Responsibility | Status |
|---|---|---|---|
| 1 | `data-service` | Ingest, clean, validate, transform, and feature-engineer vessel/route/weather/AIS/fuel data; expose a query API for downstream services | **done** |
| 2 | `prediction-service` | Ensemble ML model (XGBoost) — input: processed features; output: fuel consumption, operating cost, voyage time | **done** |
| 3 | `emissions-service` | Lifecycle GHG calculator — input: fuel + fuel type + emission factors; output: lifecycle GHG emissions | **done** |
| 4 | `optimization-service` | QPSO generates candidate fleet configs → RPC calls to prediction-service and emissions-service per candidate → objective evaluation (minimize fuel/cost/GHG; maximize reliability/cargo satisfaction/fleet utilization) → NSGA-II non-dominated sorting → Pareto front | **done** |
| 5 | `gateway` + `frontend` (React) | API gateway aggregating all services; dashboard for Pareto-front comparison, scenario trade-offs, and deployment recommendation | **done** |

The optimization loop (Phase 4) is not a one-way pipeline: every QPSO
candidate must round-trip through `prediction-service` and
`emissions-service` before NSGA-II can rank the population.
`optimization-service` is the only service that calls both.

## Canonical data contract

Every hand-off point in the pipeline is a versioned Pydantic model in
[`services/common/schemas.py`](services/common/schemas.py), imported by every
service rather than redefined per-service:

- **`VoyageRequest`** — user-controlled + automatically-obtained parameters,
  the raw input to the pipeline.
- **`ProcessedFeatures`** — data-processing output (`data-service`'s
  deliverable; `prediction-service`'s model input).
- **`PredictionResponse`** — ML-generated outputs (`prediction-service`).
- **`EmissionResponse`** — emission-model output (`emissions-service`).
- **`OptimizationCandidate`** — a QPSO population member, populated
  progressively with its `PredictionResponse`, `EmissionResponse`, and
  `ObjectiveValues` as `optimization-service` scores it.

## ML model choice

**XGBoost** for `prediction-service` (Phase 2): fuel consumption is driven by
nonlinear interactions (speed³ drag, cargo load × weather severity,
vessel-type-specific efficiency curves) that gradient-boosted trees capture
natively, it handles the mixed numeric + encoded-categorical feature set in
`ProcessedFeatures` directly, and it's the standard strong baseline the
pitch's own benchmarking narrative (vs. plain regression/GA/PSO) needs. See
the Phase 1 plan for the full rationale.

**Version pinning matters for `train.py`.** XGBoost's native JSON model
format is not guaranteed compatible across major versions, so `train.py`
must be run with the exact `xgboost` (and `scikit-learn`) versions pinned in
`services/prediction-service/requirements.txt`. A mismatch between the
training environment and the serving environment doesn't error — it silently
produces wrong predictions from an apparently healthy model, which is a much
harder bug to catch. Always train inside an environment (or the Docker image
itself) that matches `requirements.txt` exactly.

## Data sourcing

Real AIS feeds jointly covering route, speed, cargo, weather, and fuel for
commercial cargo vessels aren't available for free anywhere. `data-service`
uses a **calibrated synthetic** approach instead: real vessel specifications
(the Kaggle Global Cargo Ships dataset — real ships, CC0) anchor cargo-
tonnage ranges per vessel type, and a physics-plausible generator fabricates
everything no free dataset provides jointly (route distance, speed, numeric
weather, per-leg cargo utilization). A second Kaggle dataset ("Ship Fuel
Consumption & CO2 Emissions Analysis") was evaluated and deliberately not
used as a data source, since its own page states it's simulated data for
small Nigerian-waterway craft, not measured telemetry for the commercial
fleet this project models — see
[`services/data-service/README.md`](services/data-service/README.md) for
the full reasoning, real column mappings, and how to drop in the real CSV.

## Running locally

The prediction model isn't checked into the repo (trained artifacts aren't
committed — see below), so train it once before bringing the stack up:

```bash
cd services/prediction-service
pip install -r requirements.txt
python train.py
```

This writes `fuel_consumption_model.json`, `operating_cost_model.json`,
`voyage_time_model.json`, and `feature_columns.json` to `data/models/`,
which `docker compose` will pick up on the next step.

Then bring up the full stack:

```bash
docker compose up --build
```

Brings up RabbitMQ (management UI at `localhost:15672`, guest/guest),
`data-service` (`localhost:8000`), `prediction-service` (`localhost:8001`),
`emissions-service` (`localhost:8002`), `optimization-service`
(`localhost:8003`), and `gateway` (`localhost:8090`) — each service's
interactive API docs are at `/docs` on its own port.

For the dashboard, run the frontend separately:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Set `VITE_API_BASE_URL` in `frontend/.env.local`
(see `frontend/.env.example`) if the gateway isn't on the default port.

To run a service's tests directly (no Docker required):

```bash
cd services/data-service
python -m pytest tests/ -v
```

**Note:** `data-service/requirements.txt` pins `pyarrow==18.1.0`, which has
prebuilt wheels for Python 3.12 (used inside the Docker image) but not for
newer interpreters like 3.14 — if running tests natively on a host with
Python 3.14+, pyarrow may fail to install/build; this doesn't affect the
Docker path. `prediction-service` similarly needs `xgboost`/`scikit-learn`
versions matching `requirements.txt` exactly — a mismatch between training
and serving environments can silently produce wrong predictions rather than
an error, so train and serve with the same pinned versions.

## Repository layout

```
services/
  common/                shared Pydantic schemas (the data contract)
  data-service/          ingestion, real routing, feature pipeline, REST API
  prediction-service/    XGBoost fuel/cost/time model, training script
  emissions-service/     lifecycle GHG calculator
  optimization-service/  QPSO + NSGA-II fleet optimizer
  gateway/                API gateway aggregating the services above
frontend/                React dashboard (Vite + TypeScript + Tailwind)
docker-compose.yml       local orchestration (RabbitMQ + services)
```
