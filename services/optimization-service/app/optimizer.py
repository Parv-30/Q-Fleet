"""Top-level orchestration: ties QPSO (app/qpso.py) + the RPC client
(app/rpc_client.py) + objective evaluation (app/objectives.py) + NSGA-II
(app/nsga2.py) into one `run_optimization` entry point.

Per iteration, for each particle, QPSO proposes a (vessel, route, speed,
fuel) candidate; this module converts that into a VoyageRequest ->
ProcessedFeatures (reusing data-service's FeaturePipeline so feature
engineering can never drift from the rest of the pipeline), calls
prediction-service then emissions-service over RabbitMQ RPC, and scores
the result into ObjectiveValues via app/objectives.py. QPSO uses those
objectives (via its own internal scalarization -- see qpso.py's module
docstring) purely to steer the swarm; every evaluated candidate across
every iteration is accumulated into one population, which NSGA-II then
ranks at the end. Only the Pareto front (front 0) is returned.

This endpoint is expected to be SLOW: swarm_size * iterations RPC round
trips to prediction-service, plus the same number again to
emissions-service. That is an accepted, documented tradeoff for a
hackathon demo (see main.py) -- no background-job/async machinery is
built for this phase.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.fleet_catalog import CatalogRoute
from app.nsga2 import get_pareto_front
from app.objectives import evaluate_candidate
from app.qpso import CandidateProposal, SearchSpace, build_search_space, run_qpso
from app.routing_client import fetch_route_options
from app.rpc_client import call_emissions_rpc, call_prediction_rpc
from common.schemas import (
    ObjectiveValues,
    OptimizationCandidate,
    OptimizationConstraints,
    VoyageRequest,
)

# Cross-service import: data-service's FeaturePipeline is not (yet)
# packaged as an installable shared library, and both services name their
# package `app` -- a plain `from app.feature_pipeline import ...` would
# collide with THIS service's own `app` package (whichever is imported
# first wins the `app` name in sys.modules). Loaded via importlib under a
# distinct module name instead, mirroring prediction-service/train.py's
# exact same cross-service-import shim (see that file's "cross-service
# import shim" comment for the full rationale) -- this is the one place
# in the running service (not just an offline script) that needs it,
# since optimization-service is the one service that must build
# ProcessedFeatures itself rather than calling data-service over HTTP.
import importlib.util
import sys
import types
from pathlib import Path

# This file lives at services/optimization-service/app/optimizer.py locally
# (parents[2] from here is services/, so services/data-service/app is a
# sibling), but inside the Docker image (see Dockerfile) the layout is
# flatter -- WORKDIR /srv with app/, common/, and a copied data_service_app/
# all directly under /srv. Check both candidate locations rather than
# assuming one, mirroring prediction-service/app/main.py's model-directory
# resolution for the same local-vs-Docker layout difference.
_THIS_FILE_PARENTS = Path(__file__).resolve().parents
_LOCAL_DEV_DATA_SERVICE_APP_DIR = (
    _THIS_FILE_PARENTS[2] / "data-service" / "app" if len(_THIS_FILE_PARENTS) > 2 else None
)
_DOCKER_DATA_SERVICE_APP_DIR = Path(__file__).resolve().parent.parent / "data_service_app"

if _LOCAL_DEV_DATA_SERVICE_APP_DIR is not None and _LOCAL_DEV_DATA_SERVICE_APP_DIR.exists():
    _DATA_SERVICE_APP_DIR = _LOCAL_DEV_DATA_SERVICE_APP_DIR
elif _DOCKER_DATA_SERVICE_APP_DIR.exists():
    _DATA_SERVICE_APP_DIR = _DOCKER_DATA_SERVICE_APP_DIR
else:
    _DATA_SERVICE_APP_DIR = _LOCAL_DEV_DATA_SERVICE_APP_DIR or _DOCKER_DATA_SERVICE_APP_DIR


def _load_data_service_module(module_name: str, file_name: str) -> types.ModuleType:
    full_name = f"data_service_{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, _DATA_SERVICE_APP_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


_feature_pipeline_module = _load_data_service_module("feature_pipeline", "feature_pipeline.py")
FeaturePipeline = _feature_pipeline_module.FeaturePipeline

_feature_pipeline = FeaturePipeline()

# Default RabbitMQ connection parameters -- mirrors prediction-service's
# and emissions-service's RABBITMQ_HOST env var pattern.
import os

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")

# Fixed placeholder weather/voyage-context values used to build the
# VoyageRequest for each QPSO-proposed candidate, used as FALLBACKS ONLY
# when a caller's OptimizationConstraints doesn't specify a given field
# (see common/schemas.py's OptimizationConstraints docstring's HONESTY
# NOTE -- there is no live weather-data source anywhere in this codebase;
# this is a "typical moderate conditions" placeholder, not auto-fetched
# real weather). When no constraints are given at all, this reproduces
# this module's exact pre-constraints behavior: every candidate in the
# population is compared on a like-for-like weather/cargo basis.
_ASSUMED_WEATHER = dict(wind_speed=8.0, wave_height=1.5, temperature=20.0, current_speed=0.5)
_ASSUMED_CARGO_UTILIZATION = 0.75


def _connection_params():
    import pika

    return pika.ConnectionParameters(host=RABBITMQ_HOST)


def resolve_search_space(constraints: OptimizationConstraints | None) -> SearchSpace:
    """OptimizationConstraints -> the effective QPSO SearchSpace for this
    run (see app/qpso.py's build_search_space for the hard-filter rules
    applied per-dimension).

    When constraints.origin and constraints.destination are BOTH given,
    this is where the real cross-service call happens: data-service's
    /routes endpoint (sub-phase B's compute_route(), see
    app/routing_client.py) is queried for the actual 1-2 real route
    option(s) between those two ports, and the route dimension is
    narrowed (or pinned, if only one real option comes back) to exactly
    that real set -- never the old fake catalog, never the unrelated
    default route universe. Giving only one of origin/destination (not
    both) has no routing effect, matching OptimizationConstraints' own
    documented semantics.
    """
    route_catalog = None
    if constraints is not None and constraints.origin is not None and constraints.destination is not None:
        # Real cross-service call: data-service's /routes endpoint (sub-
        # phase B's compute_route() over the Marnet shipping-lane graph),
        # via the plain HTTP client in app/routing_client.py -- this
        # service has no other existing client for data-service, so this
        # is the one new cross-service call this sub-phase adds.
        options = fetch_route_options(constraints.origin, constraints.destination)
        route_catalog = [
            CatalogRoute(
                route_id=f"ROUTE-{constraints.origin[:3].upper()}-{constraints.destination[:3].upper()}"
                + ("" if len(options) == 1 else f"-{i}"),
                origin=opt["origin"],
                destination=opt["destination"],
                distance_km=opt["distance_km"],
                via=opt["via"],
            )
            for i, opt in enumerate(options)
        ]

    return build_search_space(constraints, route_catalog=route_catalog)


def _build_voyage_request(
    proposal: CandidateProposal,
    search_space: SearchSpace,
    constraints: OptimizationConstraints | None,
) -> VoyageRequest:
    vessel = search_space.vessel_catalog[proposal.vessel_index]
    route = search_space.route_catalog[proposal.route_index]

    if constraints is not None and constraints.cargo_tonnes is not None:
        cargo_tonnes = constraints.cargo_tonnes
        cargo_utilization = max(0.0, min(1.0, cargo_tonnes / vessel.capacity_tonnes)) if vessel.capacity_tonnes else 0.0
    else:
        cargo_utilization = _ASSUMED_CARGO_UTILIZATION
        cargo_tonnes = vessel.capacity_tonnes * cargo_utilization

    weather = dict(_ASSUMED_WEATHER)
    if constraints is not None:
        if constraints.wind_speed is not None:
            weather["wind_speed"] = constraints.wind_speed
        if constraints.wave_height is not None:
            weather["wave_height"] = constraints.wave_height
        if constraints.temperature is not None:
            weather["temperature"] = constraints.temperature
        if constraints.current_speed is not None:
            weather["current_speed"] = constraints.current_speed

    delivery_deadline = constraints.delivery_deadline if constraints is not None else None

    return VoyageRequest(
        vessel_id=vessel.vessel_id,
        vessel_type=vessel.vessel_type,
        route_id=route.route_id,
        origin=route.origin,
        destination=route.destination,
        distance_km=route.distance_km,
        cargo_tonnes=cargo_tonnes,
        cargo_utilization=cargo_utilization,
        speed_knots=proposal.speed,
        fuel_type=proposal.fuel,
        delivery_deadline=delivery_deadline,
        **weather,
    )


def _evaluate_proposal(
    proposal: CandidateProposal,
    search_space: SearchSpace,
    constraints: OptimizationConstraints | None,
) -> ObjectiveValues:
    """The evaluate_fn QPSO's swarm calls: decode a proposal into a full
    pipeline call (features -> prediction RPC -> emissions RPC ->
    objectives), using real constraint values where given (see
    _build_voyage_request) and feeding delivery_deadline through to
    objectives.py's deadline-aware reliability calculation."""
    voyage_request = _build_voyage_request(proposal, search_space, constraints)
    features = _feature_pipeline.transform(voyage_request)

    connection_params = _connection_params()
    prediction = call_prediction_rpc(features, connection_params)
    emission = call_emissions_rpc(proposal.fuel, prediction.fuel_consumption, connection_params)

    vessel = search_space.vessel_catalog[proposal.vessel_index]
    route = search_space.route_catalog[proposal.route_index]

    return evaluate_candidate(
        vessel=vessel.vessel_id,
        route=route.route_id,
        speed=proposal.speed,
        fuel=proposal.fuel,
        prediction=prediction,
        emission=emission,
        weather_severity=features.weather_severity,
        cargo_utilization=features.cargo_utilization,
        delivery_deadline=voyage_request.delivery_deadline,
        departure_time=datetime.now(timezone.utc),
    )


def _proposal_to_candidate(
    proposal: CandidateProposal, objectives: ObjectiveValues, search_space: SearchSpace
) -> OptimizationCandidate:
    vessel = search_space.vessel_catalog[proposal.vessel_index]
    route = search_space.route_catalog[proposal.route_index]
    return OptimizationCandidate(
        vessel=vessel.vessel_id,
        route=route.route_id,
        speed=proposal.speed,
        fuel=proposal.fuel,
        objectives=objectives,
    )


def run_optimization(
    iterations: int = 30,
    swarm_size: int = 20,
    seed: int | None = None,
    constraints: OptimizationConstraints | None = None,
) -> list[OptimizationCandidate]:
    """Run QPSO for `iterations` iterations with `swarm_size` particles,
    scoring every proposed candidate via the real prediction-service /
    emissions-service RPC pipeline, then return NSGA-II's Pareto front
    (front 0, sorted by crowding distance descending) over the full
    evaluated population.

    `constraints` (see common/schemas.py's OptimizationConstraints) lets a
    caller pin/narrow any of the 4 QPSO search dimensions (vessel, route,
    speed, fuel) and/or override the cargo/weather/deadline placeholders
    normally used to build each candidate's VoyageRequest -- see
    resolve_search_space and _build_voyage_request. Every field left
    unspecified stays exactly as open as this function's pre-constraints
    behavior (constraints=None reproduces that behavior exactly).

    Note the RPC calls make this function synchronous and slow --
    swarm_size * iterations round trips to each of prediction-service and
    emissions-service (plus, when origin+destination constraints are
    given, one extra HTTP call to data-service's /routes to resolve the
    real route option(s) up front). Fine for a hackathon demo (see
    app/main.py); not meant to back a low-latency endpoint.
    """
    search_space = resolve_search_space(constraints)

    def evaluate_fn(proposal: CandidateProposal) -> ObjectiveValues:
        return _evaluate_proposal(proposal, search_space, constraints)

    raw_population = run_qpso(
        swarm_size=swarm_size,
        iterations=iterations,
        evaluate_fn=evaluate_fn,
        seed=seed,
        search_space=search_space,
    )
    candidates = [
        _proposal_to_candidate(proposal, objectives, search_space) for proposal, objectives in raw_population
    ]
    return get_pareto_front(candidates)
