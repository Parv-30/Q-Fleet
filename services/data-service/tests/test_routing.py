import pytest

from app.port_catalog import PORT_CATALOG, UnknownPortError, get_port, list_ports
from app.routing import RoutingError, _haversine_km, _route_cache, compute_route, sample_waypoints


# ---------------------------------------------------------------------------
# Port catalog
# ---------------------------------------------------------------------------
class TestPortCatalog:
    def test_has_at_least_20_real_ports(self):
        assert len(PORT_CATALOG) >= 20

    def test_all_coordinates_are_in_plausible_ranges(self):
        for port in list_ports():
            assert -90.0 <= port.lat <= 90.0
            assert -180.0 <= port.lon <= 180.0

    def test_mumbai_coordinates_are_approximately_correct(self):
        mumbai = get_port("Mumbai")
        assert mumbai.lat == pytest.approx(19.0, abs=0.5)
        assert mumbai.lon == pytest.approx(73.0, abs=0.5)

    def test_rotterdam_coordinates_are_approximately_correct(self):
        rotterdam = get_port("Rotterdam")
        assert rotterdam.lat == pytest.approx(52.0, abs=0.5)
        assert rotterdam.lon == pytest.approx(4.0, abs=0.5)

    def test_unknown_port_raises_clear_error(self):
        with pytest.raises(UnknownPortError):
            get_port("Atlantis")


# ---------------------------------------------------------------------------
# compute_route
# ---------------------------------------------------------------------------
class TestComputeRoute:
    def setup_method(self):
        # Each test should compute routes fresh rather than reusing another
        # test's cached entry.
        _route_cache.clear()

    def test_asia_europe_pair_returns_two_meaningfully_different_options(self):
        options = compute_route("Mumbai", "Rotterdam")
        assert len(options) == 2

        distances = sorted(o.distance_km for o in options)
        suez_km, cape_km = distances
        # Real sane ranges anchored to the feasibility spike's confirmed
        # numbers for this pair (Suez ~11,816km, Cape ~20,090km).
        assert 10_000 <= suez_km <= 13_000
        assert 18_000 <= cape_km <= 21_000
        assert cape_km > suez_km

    def test_same_coast_pair_returns_single_option(self):
        # Los Angeles and Seattle are both US West Coast -- Suez is never a
        # candidate route for either, so the two searoute calls should
        # produce the same (or near-identical) distance and only one
        # RouteOption, not two fabricated ones.
        options = compute_route("Los Angeles", "Seattle")
        assert len(options) == 1

    def test_unknown_port_raises_clear_error(self):
        with pytest.raises(UnknownPortError):
            compute_route("Atlantis", "Rotterdam")
        with pytest.raises(UnknownPortError):
            compute_route("Mumbai", "Atlantis")

    def test_same_origin_and_destination_raises_clear_error(self):
        with pytest.raises(RoutingError):
            compute_route("Mumbai", "Mumbai")

    def test_caching_avoids_recomputation(self, monkeypatch):
        import app.routing as routing_module

        call_count = 0
        real_searoute = routing_module.sr.searoute

        def counting_searoute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return real_searoute(*args, **kwargs)

        monkeypatch.setattr(routing_module.sr, "searoute", counting_searoute)

        compute_route("Mumbai", "Rotterdam")
        calls_after_first = call_count
        assert calls_after_first > 0

        compute_route("Mumbai", "Rotterdam")
        assert call_count == calls_after_first  # no new underlying calls

    def test_cache_dict_is_populated_after_a_call(self):
        compute_route("Mumbai", "Rotterdam")
        assert ("Mumbai", "Rotterdam") in _route_cache

    def test_route_option_carries_a_real_path(self):
        options = compute_route("Mumbai", "Rotterdam")
        for option in options:
            assert len(option.path) >= 2
            # Endpoints should be close to the real port coordinates
            # (searoute snaps to the nearest sea-lane node, not the exact
            # harbor point, hence an approximate check).
            first_lat, first_lon = option.path[0]
            last_lat, last_lon = option.path[-1]
            assert first_lat == pytest.approx(18.9750, abs=1.0)
            assert first_lon == pytest.approx(72.8258, abs=1.0)
            assert last_lat == pytest.approx(51.9244, abs=1.0)
            assert last_lon == pytest.approx(4.4777, abs=1.0)


# ---------------------------------------------------------------------------
# Geometric waypoint sampling
# ---------------------------------------------------------------------------
class TestHaversineKm:
    def test_same_point_is_zero_distance(self):
        assert _haversine_km((18.0, 72.0), (18.0, 72.0)) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_mumbai_to_rotterdam_straight_line(self):
        # Real great-circle distance Mumbai-Rotterdam is ~7200km (straight
        # line, not the sea route) -- sanity-checks the haversine formula
        # itself against a known real-world value.
        mumbai = (18.9750, 72.8258)
        rotterdam = (51.9244, 4.4777)
        distance = _haversine_km(mumbai, rotterdam)
        assert 6800 <= distance <= 7600


class TestSampleWaypoints:
    def test_synthetic_straight_line_evenly_spaced_by_distance(self):
        # A synthetic path along the equator, unevenly spaced by ARRAY
        # INDEX (points bunched near the start) but a straight line, so
        # real-distance spacing is easy to verify by hand.
        path = [(0.0, lon) for lon in [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]]
        waypoints = sample_waypoints(path, spacing_km=100.0)

        assert waypoints[0] == pytest.approx(path[0])
        assert waypoints[-1] == pytest.approx(path[-1])

        # Consecutive waypoints should be roughly `spacing_km` apart in
        # real distance (allowing slack for the last, shorter segment).
        for i in range(len(waypoints) - 2):
            gap = _haversine_km(waypoints[i], waypoints[i + 1])
            assert gap == pytest.approx(100.0, rel=0.05)

    def test_path_shorter_than_spacing_returns_endpoints_only(self):
        path = [(0.0, 0.0), (0.0, 0.05)]  # a few km
        waypoints = sample_waypoints(path, spacing_km=1000.0)
        assert waypoints[0] == pytest.approx(path[0])
        assert waypoints[-1] == pytest.approx(path[-1])
        assert len(waypoints) == 2

    def test_real_mumbai_rotterdam_route_sampling_stays_within_spacing_bounds(self):
        options = compute_route("Mumbai", "Rotterdam")
        route = options[0]
        waypoints = sample_waypoints(route.path, spacing_km=750.0)

        assert len(waypoints) >= 2
        assert waypoints[0] == pytest.approx(route.path[0])
        assert waypoints[-1] == pytest.approx(route.path[-1])
        for i in range(len(waypoints) - 1):
            gap = _haversine_km(waypoints[i], waypoints[i + 1])
            # Real waypoint spacing along a great-circle-ish sea route
            # won't be exact due to interpolation over an uneven path, but
            # should stay in the same ballpark as the requested spacing.
            assert gap <= 900.0

    def test_empty_path_raises_value_error(self):
        with pytest.raises(ValueError):
            sample_waypoints([])

    def test_single_point_path_raises_value_error(self):
        with pytest.raises(ValueError):
            sample_waypoints([(0.0, 0.0)])

    def test_non_positive_spacing_raises_value_error(self):
        with pytest.raises(ValueError):
            sample_waypoints([(0.0, 0.0), (1.0, 1.0)], spacing_km=0)
