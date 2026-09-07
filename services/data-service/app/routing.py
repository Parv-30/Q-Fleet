"""Real maritime routing via ``searoute`` (searoute-py, offline, no API key).

Replaces the earlier hardcoded/fake route distances with real sea-lane
distance computation over the Marnet global shipping-lane graph (Oak Ridge
National Labs). This gives genuinely different distances depending on which
real chokepoints a route can use -- e.g. Mumbai-Rotterdam via Suez is
~11,800km, but forcing a Suez-avoiding route around the Cape of Good Hope
is ~20,000km -- rather than one arbitrary made-up number per route.

IMPORTANT caveat (also true of every distance this module returns): the
Marnet graph has a dated (~2000) base year, refreshed only in some regions.
It is suitable for realistic route-shape/distance estimation and fleet
planning, NOT certified navigational-grade routing. Do not present these
numbers as authoritative nautical-chart distances.

Only two route "shapes" are considered per port pair: the default
(shortest/unrestricted) route, and a Suez-avoiding alternative that also
avoids the Northwest Passage (``restrictions=["suez", "northwest"]``,
per the searoute API) as a proxy for "goes around the Cape of Good Hope
instead." When both calls land on essentially the same distance (e.g. two
ports on the same coast, where Suez was never going to be on the route
anyway), only one option is returned -- presenting a fake "Cape alternative"
for a Los Angeles-Seattle voyage would be actively misleading.
"""

from __future__ import annotations

from dataclasses import dataclass

import searoute as sr

from app.port_catalog import Port, get_port

# If the Suez-restricted route is within this fraction of the default
# route's distance, the two are treated as "the same route" (Suez was never
# relevant) and only one RouteOption is returned instead of two.
_MEANINGFUL_DIFFERENCE_THRESHOLD = 0.05

# Passage names (see searoute's `return_passages=True` output) mapped to a
# human-readable label for RouteOption.via.
_PASSAGE_LABELS: dict[str, str] = {
    "suez": "Suez Canal",
    "panama": "Panama Canal",
    "gibraltar": "Strait of Gibraltar",
    "bosporus": "Bosporus Strait",
    "ormuz": "Strait of Hormuz",
    "malacca": "Strait of Malacca",
    "babalmandab": "Bab-el-Mandeb Strait",
    "south_africa": "Cape of Good Hope",
    "bering": "Bering Strait",
    "northwest": "Northwest Passage",
}


class RoutingError(Exception):
    """Raised when a route cannot be computed for a valid-looking request."""


@dataclass(frozen=True)
class RouteOption:
    origin: str
    destination: str
    distance_km: float
    via: str  # e.g. "Suez Canal, Bab-el-Mandeb Strait" or "Cape of Good Hope"


def _describe_passages(passages: list[str]) -> str:
    if not passages:
        return "direct route (no major canal/strait chokepoint)"
    return ", ".join(_PASSAGE_LABELS.get(p, p) for p in passages)


def _run_searoute(origin: Port, destination: Port, restrictions: list[str] | None) -> tuple[float, list[str]]:
    origin_coord = [origin.lon, origin.lat]
    dest_coord = [destination.lon, destination.lat]
    try:
        kwargs = dict(return_passages=True)
        if restrictions:
            kwargs["restrictions"] = restrictions
        result = sr.searoute(origin_coord, dest_coord, **kwargs)
    except Exception as exc:  # searoute raises plain Exception/ValueError on unroutable pairs
        raise RoutingError(
            f"searoute failed to compute a route from {origin.name!r} to "
            f"{destination.name!r}: {exc}"
        ) from exc

    props = result["properties"]
    distance_km = float(props["length"])
    passages = list(props.get("traversed_passages", []))
    return distance_km, passages


# Manual in-memory cache keyed by (origin, destination) -- searoute
# computations are sub-100ms per the feasibility spike, but repeated lookups
# of the same well-known port pair (e.g. from the synthetic voyage
# generator) shouldn't redo the work within a running process.
_route_cache: dict[tuple[str, str], list[RouteOption]] = {}


def compute_route(origin_port: str, destination_port: str) -> list[RouteOption]:
    """Compute real route option(s) between two known ports.

    Returns one ``RouteOption`` when the Suez-restricted route isn't
    meaningfully different from the default route (same coast/region pairs),
    or two -- default (typically via Suez where relevant) and a Suez-avoiding
    Cape of Good Hope alternative -- when they genuinely diverge.

    Raises:
        UnknownPortError: if either port name isn't in the port catalog.
        RoutingError: if origin == destination, or the underlying searoute
            computation fails for a reason other than an unknown port.
    """
    if origin_port == destination_port:
        raise RoutingError(f"Origin and destination are both {origin_port!r}; cannot compute a route to itself.")

    cache_key = (origin_port, destination_port)
    if cache_key in _route_cache:
        return _route_cache[cache_key]

    origin = get_port(origin_port)
    destination = get_port(destination_port)

    default_km, default_passages = _run_searoute(origin, destination, restrictions=None)
    cape_km, cape_passages = _run_searoute(origin, destination, restrictions=["suez", "northwest"])

    default_option = RouteOption(
        origin=origin_port,
        destination=destination_port,
        distance_km=round(default_km, 1),
        via=_describe_passages(default_passages),
    )

    relative_diff = abs(cape_km - default_km) / default_km if default_km else 0.0
    if relative_diff < _MEANINGFUL_DIFFERENCE_THRESHOLD:
        options = [default_option]
    else:
        cape_option = RouteOption(
            origin=origin_port,
            destination=destination_port,
            distance_km=round(cape_km, 1),
            via=_describe_passages(cape_passages),
        )
        options = [default_option, cape_option]

    _route_cache[cache_key] = options
    return options
