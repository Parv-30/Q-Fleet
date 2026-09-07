import pytest

from app.ingestion import (
    LoadResult,
    generate_synthetic_records,
    load_global_cargo_ships_csv,
    load_vessel_type_dwt_stats,
)
from common.schemas import VesselType, VoyageRequest


# ---------------------------------------------------------------------------
# Global cargo ships loader
#
# Real headers confirmed from Kaggle's data-card preview: Kaggle mislabels
# the vessel-type column "Company_Name", but its values/breakdown ("Bulk
# Carrier" 34%, "Container Ship" 26%, ...) are plainly ship type.
# ---------------------------------------------------------------------------
class TestLoadGlobalCargoShipsCsv:
    def test_loads_well_formed_rows(self, tmp_path):
        csv_path = tmp_path / "global_cargo_ships.csv"
        csv_path.write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Container Ship,MSC LORETO,2023,236184,240000,399,60\n"
            "Crude Oil Tanker,ATHERINA,2011,169919,319471,340,60\n"
        )
        result = load_global_cargo_ships_csv(csv_path)
        assert isinstance(result, LoadResult)
        assert len(result.records) == 2
        assert result.skipped_rows == 0
        first = result.records[0]
        assert first.vessel_id == "MSC LORETO"
        assert first.vessel_type == VesselType.CONTAINER
        assert first.cargo_tonnes == pytest.approx(240000 * 0.7)
        assert 0.0 <= first.cargo_utilization <= 1.0

    def test_skips_rows_with_missing_or_nonpositive_deadweight(self, tmp_path):
        csv_path = tmp_path / "global_cargo_ships.csv"
        csv_path.write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Container Ship,G1,2020,80000,,300,50\n"
            "Crude Oil Tanker,G2,2020,80000,-500,300,50\n"
            "Bulk Carrier,G3,2020,80000,50000,300,50\n"
        )
        result = load_global_cargo_ships_csv(csv_path)
        assert len(result.records) == 1
        assert result.skipped_rows == 2

    def test_produces_valid_voyage_requests(self, tmp_path):
        csv_path = tmp_path / "global_cargo_ships.csv"
        csv_path.write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Bulk Carrier,G1,2020,40000,30000,250,40\n"
        )
        result = load_global_cargo_ships_csv(csv_path)
        assert isinstance(result.records[0], VoyageRequest)
        assert result.records[0].speed_knots > 0
        assert result.records[0].distance_km > 0

    def test_unrecognized_vessel_type_falls_back_to_general_cargo(self, tmp_path):
        csv_path = tmp_path / "global_cargo_ships.csv"
        csv_path.write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Offshore Support Vessel,PRELUDE,2017,499167,394330,489,74\n"
        )
        result = load_global_cargo_ships_csv(csv_path)
        assert result.records[0].vessel_type == VesselType.GENERAL_CARGO

    def test_defaults_to_repo_raw_data_path_when_no_path_given(self, monkeypatch, tmp_path):
        import app.ingestion as ingestion_module

        fake_dir = tmp_path / "data" / "raw"
        fake_dir.mkdir(parents=True)
        (fake_dir / "global_cargo_ships.csv").write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Bulk Carrier,G1,2020,40000,30000,250,40\n"
        )
        monkeypatch.setattr(ingestion_module, "DEFAULT_RAW_DATA_DIR", fake_dir)
        result = load_global_cargo_ships_csv()
        assert len(result.records) == 1


# ---------------------------------------------------------------------------
# Real-data-calibrated dwt stats
# ---------------------------------------------------------------------------
class TestLoadVesselTypeDwtStats:
    def test_falls_back_to_defaults_when_file_missing(self, tmp_path):
        missing_path = tmp_path / "does_not_exist.csv"
        stats = load_vessel_type_dwt_stats(missing_path)
        assert set(stats.keys()) == set(VesselType)
        for low, high in stats.values():
            assert low < high

    def test_derives_range_from_real_rows_when_present(self, tmp_path):
        csv_path = tmp_path / "global_cargo_ships.csv"
        csv_path.write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Container Ship,A,2020,1,50000,1,1\n"
            "Container Ship,B,2020,1,150000,1,1\n"
        )
        stats = load_vessel_type_dwt_stats(csv_path)
        low, high = stats[VesselType.CONTAINER]
        assert low == pytest.approx(50000.0)
        assert high == pytest.approx(150000.0)

    def test_uses_fallback_for_type_with_too_few_real_rows(self, tmp_path):
        csv_path = tmp_path / "global_cargo_ships.csv"
        csv_path.write_text(
            "Company_Name,ship_name,built_year,gt,dwt,length,width\n"
            "Container Ship,A,2020,1,50000,1,1\n"
        )
        defaults = load_vessel_type_dwt_stats(tmp_path / "missing.csv")
        stats = load_vessel_type_dwt_stats(csv_path)
        # Only one Container row present (< 2), so it should fall back
        # rather than report a degenerate (x, x) range.
        assert stats[VesselType.CONTAINER] == defaults[VesselType.CONTAINER]


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------
class TestGenerateSyntheticRecords:
    def test_generates_requested_count(self):
        records = generate_synthetic_records(count=50, seed=1)
        assert len(records) == 50
        assert all(isinstance(r, VoyageRequest) for r in records)

    def test_default_count_is_in_usable_range(self):
        records = generate_synthetic_records(seed=1)
        assert 500 <= len(records) <= 1000

    def test_seed_reproducibility(self):
        a = generate_synthetic_records(count=100, seed=42)
        b = generate_synthetic_records(count=100, seed=42)
        assert [r.model_dump() for r in a] == [r.model_dump() for r in b]

    def test_different_seeds_produce_different_output(self):
        a = generate_synthetic_records(count=100, seed=1)
        b = generate_synthetic_records(count=100, seed=2)
        assert [r.model_dump() for r in a] != [r.model_dump() for r in b]

    def test_weather_and_route_fields_stay_within_bounds(self):
        records = generate_synthetic_records(count=1000, seed=7)
        for r in records:
            assert 0.0 <= r.wind_speed <= 25.0
            assert 0.0 <= r.wave_height <= 10.0
            assert -5.0 <= r.temperature <= 40.0
            assert -3.0 <= r.current_speed <= 3.0
            assert r.distance_km > 0
            assert r.speed_knots > 0
            assert 0.0 <= r.cargo_utilization <= 1.0

    def test_storm_legs_occur_but_remain_a_minority(self):
        records = generate_synthetic_records(count=1000, seed=123)
        storm_legs = [r for r in records if r.wave_height > 6.0]
        # occasional storms, not the majority of legs
        assert 0 < len(storm_legs) < len(records) * 0.5

    def test_cargo_tonnes_respects_supplied_dwt_ranges_per_vessel_type(self):
        narrow_ranges = {vt: (1000.0, 2000.0) for vt in VesselType}
        records = generate_synthetic_records(count=200, seed=3, dwt_ranges=narrow_ranges)
        for r in records:
            assert 0.0 <= r.cargo_tonnes <= 2000.0

    def test_distance_km_comes_from_real_route_computation(self):
        # Real distances now come from app.routing.compute_route for the
        # real _SAMPLE_ROUTES port pairs, not an independent random draw --
        # every generated distance must match one of those real, precomputed
        # values exactly rather than falling somewhere in a continuous range.
        from app.ingestion import _ROUTE_DISTANCES_KM

        real_distances = set(_ROUTE_DISTANCES_KM.values())
        records = generate_synthetic_records(count=300, seed=11)
        for r in records:
            assert r.distance_km in real_distances

    def test_distance_km_matches_origin_destination_pair(self):
        from app.ingestion import _ROUTE_DISTANCES_KM

        records = generate_synthetic_records(count=300, seed=11)
        for r in records:
            assert r.distance_km == _ROUTE_DISTANCES_KM[(r.origin, r.destination)]
