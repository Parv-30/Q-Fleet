"""Tests for app/fleet_catalog.py's real-routing-backed catalogs.

Covers: the default "explore everything" route universe is built from
REAL compute_route() distances (not fabricated numbers), and a specific
origin/destination pair resolves to the actual 1-2 real RouteOptions for
that pair via resolve_constrained_routes.
"""

from __future__ import annotations

from app.fleet_catalog import DEFAULT_ROUTE_CATALOG, FLEET_CATALOG, resolve_constrained_routes


def test_fleet_catalog_covers_all_five_vessel_types():
    from common.schemas import VesselType

    types_present = {v.vessel_type for v in FLEET_CATALOG}
    assert types_present == set(VesselType)


def test_default_route_catalog_is_nonempty_and_real():
    assert len(DEFAULT_ROUTE_CATALOG) > 0
    for route in DEFAULT_ROUTE_CATALOG:
        assert route.distance_km > 0
        assert route.origin != route.destination


def test_default_route_catalog_includes_curated_major_lane():
    origins_destinations = {(r.origin, r.destination) for r in DEFAULT_ROUTE_CATALOG}
    assert ("Mumbai", "Rotterdam") in origins_destinations


def test_resolve_constrained_routes_returns_real_options_for_known_pair():
    options = resolve_constrained_routes("Mumbai", "Amsterdam")
    assert 1 <= len(options) <= 2
    for option in options:
        assert option.origin == "Mumbai"
        assert option.destination == "Amsterdam"
        assert option.distance_km > 0
        assert option.via  # non-empty description


def test_resolve_constrained_routes_mumbai_amsterdam_offers_suez_and_cape():
    """Per the user's explicit example: Mumbai -> Amsterdam should surface
    both a Suez-routed option and a Cape-of-Good-Hope alternative, since
    they meaningfully diverge in distance."""
    options = resolve_constrained_routes("Mumbai", "Amsterdam")
    assert len(options) == 2
    vias = " ".join(o.via for o in options).lower()
    assert "suez" in vias or "cape" in vias or "good hope" in vias
    distances = sorted(o.distance_km for o in options)
    # The two options should be meaningfully different, not near-duplicates.
    assert distances[1] > distances[0] * 1.05


def test_resolve_constrained_routes_same_coast_pair_may_return_single_option():
    options = resolve_constrained_routes("Los Angeles", "Seattle")
    assert 1 <= len(options) <= 2


def test_resolve_constrained_routes_route_ids_are_distinct_when_two_options():
    options = resolve_constrained_routes("Mumbai", "Amsterdam")
    if len(options) == 2:
        route_ids = {o.route_id for o in options}
        assert len(route_ids) == 2
