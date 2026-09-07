import pytest

from app.feature_pipeline import FeaturePipeline
from common.schemas import FuelType, ProcessedFeatures, VesselType, VoyageRequest


def make_request(**overrides) -> VoyageRequest:
    defaults = dict(
        vessel_id="V1",
        vessel_type=VesselType.CONTAINER,
        route_id="R1",
        origin="Mumbai",
        destination="Singapore",
        distance_km=3800.0,
        cargo_tonnes=50000.0,
        cargo_utilization=0.8,
        speed_knots=18.0,
        fuel_type=FuelType.HFO,
        wind_speed=5.0,
        wave_height=1.2,
        temperature=28.0,
        current_speed=0.5,
    )
    defaults.update(overrides)
    return VoyageRequest(**defaults)


class TestFeaturePipeline:
    def test_transform_returns_processed_features(self):
        pipeline = FeaturePipeline()
        result = pipeline.transform(make_request())
        assert isinstance(result, ProcessedFeatures)

    def test_passthrough_fields_preserved(self):
        pipeline = FeaturePipeline()
        req = make_request(distance_km=1234.5, cargo_tonnes=9000.0, cargo_utilization=0.55, speed_knots=14.0)
        result = pipeline.transform(req)
        assert result.distance_km == 1234.5
        assert result.cargo_tonnes == 9000.0
        assert result.cargo_utilization == 0.55
        assert result.speed_knots == 14.0

    def test_speed_cubed_is_derived_correctly(self):
        pipeline = FeaturePipeline()
        req = make_request(speed_knots=10.0)
        result = pipeline.transform(req)
        assert result.speed_cubed == pytest.approx(1000.0)

    def test_weather_severity_is_normalized_between_zero_and_one(self):
        pipeline = FeaturePipeline()
        calm = pipeline.transform(make_request(wind_speed=0.0, wave_height=0.0, current_speed=0.0))
        rough = pipeline.transform(make_request(wind_speed=30.0, wave_height=10.0, current_speed=5.0))
        assert 0.0 <= calm.weather_severity <= 1.0
        assert 0.0 <= rough.weather_severity <= 1.0
        assert rough.weather_severity > calm.weather_severity

    def test_weather_severity_clamped_at_one_for_extreme_values(self):
        pipeline = FeaturePipeline()
        extreme = pipeline.transform(make_request(wind_speed=500.0, wave_height=500.0, current_speed=500.0))
        assert extreme.weather_severity == 1.0

    _VESSEL_TYPE_FIELD_NAMES = [
        "vessel_type_container",
        "vessel_type_bulk_carrier",
        "vessel_type_tanker",
        "vessel_type_ro_ro",
        "vessel_type_general_cargo",
    ]
    _FUEL_TYPE_FIELD_NAMES = [
        "fuel_type_hfo",
        "fuel_type_diesel",
        "fuel_type_lng",
        "fuel_type_methanol",
        "fuel_type_hydrogen",
        "fuel_type_ammonia",
    ]

    @pytest.mark.parametrize(
        "vessel_type",
        list(VesselType),
    )
    def test_vessel_type_encoding_is_one_hot_and_deterministic(self, vessel_type):
        pipeline = FeaturePipeline()
        req = make_request(vessel_type=vessel_type)
        result = pipeline.transform(req)

        onehot = {name: getattr(result, name) for name in self._VESSEL_TYPE_FIELD_NAMES}
        # Exactly one field is 1, all others are 0.
        assert sum(onehot.values()) == 1
        assert all(v in (0, 1) for v in onehot.values())

        # Same input always maps to the same one-hot pattern.
        result2 = pipeline.transform(req)
        onehot2 = {name: getattr(result2, name) for name in self._VESSEL_TYPE_FIELD_NAMES}
        assert onehot == onehot2

    def test_different_vessel_types_map_to_different_onehot_patterns(self):
        pipeline = FeaturePipeline()
        a = pipeline.transform(make_request(vessel_type=VesselType.CONTAINER))
        b = pipeline.transform(make_request(vessel_type=VesselType.TANKER))
        a_onehot = tuple(getattr(a, name) for name in self._VESSEL_TYPE_FIELD_NAMES)
        b_onehot = tuple(getattr(b, name) for name in self._VESSEL_TYPE_FIELD_NAMES)
        assert a_onehot != b_onehot
        assert a.vessel_type_container == 1
        assert a.vessel_type_tanker == 0
        assert b.vessel_type_tanker == 1
        assert b.vessel_type_container == 0

    @pytest.mark.parametrize(
        "fuel_type",
        list(FuelType),
    )
    def test_fuel_type_encoding_is_one_hot_and_deterministic(self, fuel_type):
        pipeline = FeaturePipeline()
        req = make_request(fuel_type=fuel_type)
        result = pipeline.transform(req)

        onehot = {name: getattr(result, name) for name in self._FUEL_TYPE_FIELD_NAMES}
        assert sum(onehot.values()) == 1
        assert all(v in (0, 1) for v in onehot.values())

    def test_transform_batch_processes_multiple_requests(self):
        pipeline = FeaturePipeline()
        reqs = [make_request(speed_knots=s) for s in (10.0, 15.0, 20.0)]
        results = pipeline.transform_batch(reqs)
        assert len(results) == 3
        assert all(isinstance(r, ProcessedFeatures) for r in results)
        assert [r.speed_knots for r in results] == [10.0, 15.0, 20.0]

    def test_rejects_negative_distance_at_request_construction(self):
        with pytest.raises(Exception):
            make_request(distance_km=-1.0)

    def test_rejects_cargo_utilization_out_of_range(self):
        with pytest.raises(Exception):
            make_request(cargo_utilization=1.5)
