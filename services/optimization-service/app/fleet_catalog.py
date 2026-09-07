"""Fixed catalogs QPSO's discrete search dimensions index into: a small
representative fleet of vessels, and a route universe QPSO searches when
the caller hasn't pinned/narrowed a specific origin+destination.

--- Vessels ----------------------------------------------------------------

FLEET_CATALOG is intentionally small, fixed, and hand-curated rather than
pulled from data-service's ingestion pipeline at runtime -- QPSO needs a
STABLE set of discrete choices to search over within a single optimization
run (the catalog defines the meaning of "vessel index 3", and that meaning
must not shift mid-run), and a hackathon-scale demo optimization doesn't
need thousands of candidate vessels to produce a meaningful Pareto front.
Vessel cargo capacities are anchored to data-service's real,
Kaggle-calibrated per-VesselType dwt ranges (see
data-service/app/ingestion.py's `_FALLBACK_DWT_RANGES` /
`load_vessel_type_dwt_stats`) so they are plausible rather than arbitrary.

Sourcing a "real global vessel fleet dataset" (e.g. real IMO-registered
hulls with real capacities) is out of scope for this sub-phase -- flagged
here as a documented future improvement, not attempted now.

--- Routes -------------------------------------------------------------

The old ROUTE_CATALOG (6 hardcoded fake distances, disconnected from
sub-phase B's real `data-service/app/routing.py`) has been REMOVED. Routes
now come from one of two real sources, matching the user's explicit
"origin+destination is a REQUEST for real routing options, not a pin to
one fake row" requirement:

    1. Constrained mode (a specific origin+destination given): call
       `resolve_constrained_routes()`, a thin wrapper around
       data-service's real `compute_route()` (sub-phase B, Marnet
       shipping-lane graph) -- returns the actual 1-2 real RouteOptions for
       exactly that port pair (e.g. Suez vs. Cape of Good Hope for
       Mumbai-Amsterdam).
    2. Unconstrained mode (no origin/destination given at all): QPSO still
       needs SOME route universe to search across. `DEFAULT_ROUTE_CATALOG`
       below is that universe -- a curated set of REAL major-trade-lane
       port pairs (drawn from data-service's real `PORT_CATALOG`), with
       REAL computed distances via `compute_route()` (not fabricated
       numbers, unlike the old ROUTE_CATALOG). This is strictly better than
       the old fake catalog while still being small/stable enough for QPSO
       to index into for a single run.

Both paths return `CatalogRoute` entries so the rest of this module/QPSO
doesn't need to know which source produced them.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.schemas import VesselType

# Cross-service import: data-service's routing/port_catalog modules aren't
# (yet) packaged as an installable shared library. Reuses the exact same
# local-vs-Docker path resolution + importlib shim as optimizer.py's
# FeaturePipeline import (see that file's "cross-service import shim"
# comment for the full rationale) so both places resolve the sibling
# data-service/app directory identically.
import importlib.util
import sys
import types
from pathlib import Path

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


_port_catalog_module = _load_data_service_module("port_catalog", "port_catalog.py")

# data-service/app/routing.py does `from app.port_catalog import Port,
# get_port` -- an ABSOLUTE import of ITS OWN `app` package. optimization-
# service already has a real `app` package loaded under `sys.modules["app"]`
# (this very package), so routing.py's import would silently resolve
# against the WRONG `app` package (this service's, which has no
# `port_catalog` submodule) rather than data-service's, unlike
# feature_pipeline.py (loaded the same way elsewhere in this codebase),
# which has no such `app.*` import to collide on. Worked around the same
# way Python's own import system would resolve it inside data-service
# itself: temporarily register the already-loaded `data_service_port_catalog`
# module as `sys.modules["app.port_catalog"]` (and a bare placeholder
# `sys.modules["app"]`'s attribute) just long enough to exec routing.py,
# then restore this service's real `app` package attribute state --
# avoids ever replacing `sys.modules["app"]` itself, which would break any
# other module still holding a reference to this service's real `app`
# package during the same import.
import app as _this_services_app_package  # this service's own `app` package

_had_port_catalog_attr = hasattr(_this_services_app_package, "port_catalog")
_previous_port_catalog_attr = getattr(_this_services_app_package, "port_catalog", None)
_this_services_app_package.port_catalog = _port_catalog_module
sys.modules["app.port_catalog"] = _port_catalog_module
try:
    _routing_module = _load_data_service_module("routing", "routing.py")
finally:
    del sys.modules["app.port_catalog"]
    if _had_port_catalog_attr:
        _this_services_app_package.port_catalog = _previous_port_catalog_attr
    else:
        delattr(_this_services_app_package, "port_catalog")

compute_route = _routing_module.compute_route
RoutingError = _routing_module.RoutingError
UnknownPortError = _port_catalog_module.UnknownPortError


@dataclass(frozen=True)
class FleetVessel:
    """One catalog entry: a named, representative vessel of a given type
    and cargo capacity (tonnes, i.e. its deadweight)."""

    vessel_id: str
    vessel_type: VesselType
    capacity_tonnes: float


@dataclass(frozen=True)
class CatalogRoute:
    """One catalog entry: a named origin/destination leg with a fixed
    distance -- either a curated default-universe entry or the real
    result of a compute_route() lookup for a specific pair."""

    route_id: str
    origin: str
    destination: str
    distance_km: float
    via: str = "unspecified"


# Capacities are drawn from within data-service's ingestion.py
# `_FALLBACK_DWT_RANGES` per VesselType (itself calibrated against the
# Kaggle Global Cargo Ships dataset), picking a small ("compact") and a
# large ("standard/mega") representative for each of the five vessel
# types -- 10 entries total, covering the full VesselType enum at varying
# capacities per the task's guidance.
FLEET_CATALOG: list[FleetVessel] = [
    FleetVessel("CNT-COMPACT-01", VesselType.CONTAINER, 40000.0),
    FleetVessel("CNT-MEGA-01", VesselType.CONTAINER, 200000.0),
    FleetVessel("BULK-COMPACT-01", VesselType.BULK_CARRIER, 60000.0),
    FleetVessel("BULK-MEGA-01", VesselType.BULK_CARRIER, 350000.0),
    FleetVessel("TANK-COMPACT-01", VesselType.TANKER, 60000.0),
    FleetVessel("TANK-MEGA-01", VesselType.TANKER, 280000.0),
    FleetVessel("RORO-COMPACT-01", VesselType.RO_RO, 15000.0),
    FleetVessel("RORO-STANDARD-01", VesselType.RO_RO, 55000.0),
    FleetVessel("GC-COMPACT-01", VesselType.GENERAL_CARGO, 8000.0),
    FleetVessel("GC-STANDARD-01", VesselType.GENERAL_CARGO, 30000.0),
]

# Continuous speed search bounds (knots) -- matches data-service's
# ingestion.py `_SPEED_KNOTS_RANGE`, a realistic commercial cargo-vessel
# operating envelope.
SPEED_MIN_KNOTS = 8.0
SPEED_MAX_KNOTS = 24.0

# Real major-trade-lane port PAIRS (real port_catalog.py names) used to
# build the default "explore everything" route universe when no
# origin/destination is given at all. A small, deliberately curated
# handful covering different regions -- not an attempt at an exhaustive
# global route set, just real distances instead of fabricated ones.
_DEFAULT_ROUTE_PAIRS: list[tuple[str, str]] = [
    ("Mumbai", "Rotterdam"),
    ("Shanghai", "Los Angeles"),
    ("Singapore", "Dubai"),
    ("Santos", "Rotterdam"),
    ("Busan", "Vancouver"),
    ("Shanghai", "Rotterdam"),
    ("New York", "Felixstowe"),
    ("Jebel Ali", "Singapore"),
]


def _catalog_routes_from_options(route_id_prefix: str, options: list) -> list[CatalogRoute]:
    """RouteOption(s) from data-service's compute_route() -> CatalogRoute
    entries, tagging each with a stable, human-readable route_id (origin,
    destination and -- when there are 2 options -- an index suffix so the
    Suez and Cape alternatives get distinct ids)."""
    catalog_routes = []
    for i, option in enumerate(options):
        suffix = "" if len(options) == 1 else f"-{i}"
        catalog_routes.append(
            CatalogRoute(
                route_id=f"{route_id_prefix}{suffix}",
                origin=option.origin,
                destination=option.destination,
                distance_km=option.distance_km,
                via=option.via,
            )
        )
    return catalog_routes


def _build_default_route_catalog() -> list[CatalogRoute]:
    """Build the curated real-distance default route universe eagerly at
    import time (mirrors the old ROUTE_CATALOG's module-level constant
    shape) via real compute_route() lookups against real port pairs. If a
    real lookup fails for some reason (e.g. searoute can't route an
    unusual pair, or a port name typo), that pair is skipped rather than
    crashing import -- this is a curated convenience default, not a
    correctness-critical path.
    """
    catalog: list[CatalogRoute] = []
    for origin, destination in _DEFAULT_ROUTE_PAIRS:
        try:
            options = compute_route(origin, destination)
        except (RoutingError, UnknownPortError):
            continue
        prefix = f"ROUTE-{origin[:3].upper()}-{destination[:3].upper()}"
        catalog.extend(_catalog_routes_from_options(prefix, options))
    return catalog


DEFAULT_ROUTE_CATALOG: list[CatalogRoute] = _build_default_route_catalog()


def resolve_constrained_routes(origin: str, destination: str) -> list[CatalogRoute]:
    """Real routing lookup for a SPECIFIC origin+destination pair (the
    "constrained mode" described in the module docstring): returns the
    actual 1-2 real RouteOptions data-service's compute_route() computes
    for that pair, as CatalogRoute entries QPSO's route dimension can be
    narrowed to.

    Raises whatever compute_route() raises (RoutingError, UnknownPortError)
    -- callers (optimizer.py) decide how to surface that as an HTTP error.
    """
    options = compute_route(origin, destination)
    prefix = f"ROUTE-{origin[:3].upper()}-{destination[:3].upper()}"
    return _catalog_routes_from_options(prefix, options)
