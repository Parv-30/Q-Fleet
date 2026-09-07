import pytest

from app.port_catalog import PORT_CATALOG, UnknownPortError, get_port, list_ports
from app.routing import RoutingError, _route_cache, compute_route


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
