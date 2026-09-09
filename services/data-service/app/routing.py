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

import math
from dataclasses import dataclass, field

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
    # Real sea-lane path this route follows, as (lat, lon) points in travel
    # order -- extracted from searoute's GeoJSON LineString geometry (see
    # _run_searoute). NOT evenly spaced in real distance: searoute emits
    # more points where the Marnet graph has denser edges (e.g. near
    # chokepoints), fewer over open ocean. Added so weather-sampling (see
    # sample_waypoints below and app/weather.py) has real waypoints to work
    # with instead of just a start/end pair. Defaults to () so any existing
    # code constructing a RouteOption positionally/without this field
    # (e.g. hand-built test fixtures) keeps working unchanged.
    path: tuple[tuple[float, float], ...] = field(default=())


def _describe_passages(passages: list[str]) -> str:
    if not passages:
        return "direct route (no major canal/strait chokepoint)"
    return ", ".join(_PASSAGE_LABELS.get(p, p) for p in passages)


def _run_searoute(
    origin: Port, destination: Port, restrictions: list[str] | None
) -> tuple[float, list[str], tuple[tuple[float, float], ...]]:
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
    # searoute's GeoJSON LineString geometry.coordinates is a list of
    # [lon, lat] pairs in travel order -- flip to (lat, lon) to match this
    # codebase's convention everywhere else (see port_catalog.py's Port).
    # For trans-Pacific-style routes searoute can emit longitudes outside
    # the standard [-180, 180] range (e.g. 241.9 instead of -118.1) when
    # the route crosses the antimeridian -- normalized back into range so
    # every consumer (waypoint sampling, WeatherSample's lon validator,
    # any future map rendering) gets a conventional longitude.
    raw_coords = result["geometry"]["coordinates"]
    path = tuple((float(lat), _normalize_lon(float(lon))) for lon, lat in raw_coords)
    return distance_km, passages, path


def _normalize_lon(lon: float) -> float:
    """Wrap a longitude into the conventional [-180, 180] range."""
    return ((lon + 180.0) % 360.0) - 180.0


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

    default_km, default_passages, default_path = _run_searoute(origin, destination, restrictions=None)
    cape_km, cape_passages, cape_path = _run_searoute(origin, destination, restrictions=["suez", "northwest"])

    default_option = RouteOption(
        origin=origin_port,
        destination=destination_port,
        distance_km=round(default_km, 1),
        via=_describe_passages(default_passages),
        path=default_path,
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
            path=cape_path,
        )
        options = [default_option, cape_option]

    _route_cache[cache_key] = options
    return options


# --- Geometric waypoint sampling --------------------------------------------
#
# searoute's path points are NOT evenly spaced in real distance (see
# RouteOption.path's docstring), so picking every Nth array index would
# oversample dense chokepoint regions and undersample open ocean. Instead:
# walk the path accumulating real haversine distance between consecutive
# points, and linearly interpolate a new point exactly at each target
# cumulative-distance step.

_EARTH_RADIUS_KM = 6371.0088

# Default target spacing between sampled weather waypoints, per the user's
# "every 500-1000km along the route" requirement -- the midpoint of that
# range is used as the nominal step.
DEFAULT_SAMPLE_SPACING_KM = 750.0


def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _interpolate(p1: tuple[float, float], p2: tuple[float, float], fraction: float) -> tuple[float, float]:
    """Point `fraction` of the way from p1 to p2, linear in lat/lon.

    A true great-circle (slerp) interpolation would be more precise for
    very long segments, but consecutive searoute path points are already
    close together (that's what makes the path a usable polyline at all),
    so plain linear interpolation is accurate enough for weather-sampling
    purposes and much simpler.

    Antimeridian-safe: longitudes are normalized to [-180, 180] (see
    _normalize_lon), so two consecutive path points straddling the seam
    (e.g. 179.5 and -179.5, a true 1-degree gap) would otherwise
    interpolate the "long way around" (a near-360-degree lon delta). Takes
    the shorter of the two directions around the circle instead.
    """
    lat = p1[0] + (p2[0] - p1[0]) * fraction
    dlon = p2[1] - p1[1]
    if dlon > 180.0:
        dlon -= 360.0
    elif dlon < -180.0:
        dlon += 360.0
    lon = _normalize_lon(p1[1] + dlon * fraction)
    return (lat, lon)


def sample_waypoints(
    path: tuple[tuple[float, float], ...] | list[tuple[float, float]],
    spacing_km: float = DEFAULT_SAMPLE_SPACING_KM,
) -> list[tuple[float, float]]:
    """Evenly-spaced-by-REAL-DISTANCE points along `path`, roughly every
    `spacing_km` kilometers (measured by cumulative haversine distance
    along consecutive path points, not by array index).

    Always includes the first and last path point. For a path shorter than
    `spacing_km`, returns just those two endpoints. Raises ValueError for
    an empty or single-point path (can't sample a route with no path).
    """
    if len(path) < 2:
        raise ValueError("sample_waypoints requires a path with at least 2 points")
    if spacing_km <= 0:
        raise ValueError("spacing_km must be positive")

    path = list(path)

    # Cumulative distance at each path point.
    cumulative = [0.0]
    for i in range(1, len(path)):
        cumulative.append(cumulative[-1] + _haversine_km(path[i - 1], path[i]))
    total_km = cumulative[-1]

    if total_km == 0:
        return [path[0]]

    num_steps = max(1, round(total_km / spacing_km))
    targets = [total_km * i / num_steps for i in range(num_steps + 1)]

    waypoints: list[tuple[float, float]] = []
    seg_idx = 0
    for target in targets:
        # Advance to the segment [cumulative[seg_idx], cumulative[seg_idx+1]]
        # containing `target`.
        while seg_idx < len(path) - 2 and cumulative[seg_idx + 1] < target:
            seg_idx += 1
        seg_start_km, seg_end_km = cumulative[seg_idx], cumulative[seg_idx + 1]
        seg_len = seg_end_km - seg_start_km
        fraction = 0.0 if seg_len == 0 else (target - seg_start_km) / seg_len
        waypoints.append(_interpolate(path[seg_idx], path[seg_idx + 1], fraction))

    return waypoints
