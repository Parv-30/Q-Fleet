import pytest
from fastapi.testclient import TestClient

from app.main import app
from common.schemas import FuelType, VesselType

client = TestClient(app)


def make_request(**overrides) -> dict:
    defaults = dict(
        vessel_id="V1",
        vessel_type=VesselType.CONTAINER.value,
        route_id="R1",
        origin="Mumbai",
        destination="Singapore",
        distance_km=3800.0,
        cargo_tonnes=50000.0,
        cargo_utilization=0.8,
        speed_knots=18.0,
        fuel_type=FuelType.HFO.value,
        wind_speed=5.0,
        wave_height=1.2,
        temperature=28.0,
        current_speed=0.5,
    )
    defaults.update(overrides)
    return defaults


class TestHealth:
    def test_health_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestFeatures:
    def test_valid_request_returns_processed_features(self):
        response = client.post("/features", json=make_request(speed_knots=10.0))
        assert response.status_code == 200
        body = response.json()
        assert body["speed_knots"] == 10.0
        assert body["speed_cubed"] == pytest.approx(1000.0)
        assert body["distance_km"] == 3800.0
        assert body["cargo_tonnes"] == 50000.0
        assert body["cargo_utilization"] == 0.8
        assert 0.0 <= body["weather_severity"] <= 1.0
        vessel_onehot_fields = [
            "vessel_type_container",
            "vessel_type_bulk_carrier",
            "vessel_type_tanker",
            "vessel_type_ro_ro",
            "vessel_type_general_cargo",
        ]
        fuel_onehot_fields = [
            "fuel_type_hfo",
            "fuel_type_diesel",
            "fuel_type_lng",
            "fuel_type_methanol",
            "fuel_type_hydrogen",
            "fuel_type_ammonia",
        ]
        for field in vessel_onehot_fields + fuel_onehot_fields:
            assert isinstance(body[field], int)
        assert sum(body[f] for f in vessel_onehot_fields) == 1
        assert sum(body[f] for f in fuel_onehot_fields) == 1
        # request used vessel_type=container, fuel_type=hfo
        assert body["vessel_type_container"] == 1
        assert body["fuel_type_hfo"] == 1

    def test_negative_distance_returns_422(self):
        response = client.post("/features", json=make_request(distance_km=-1.0))
        assert response.status_code == 422

    def test_out_of_range_cargo_utilization_returns_422(self):
        response = client.post("/features", json=make_request(cargo_utilization=1.5))
        assert response.status_code == 422


class TestFeaturesBatch:
    def test_batch_returns_same_length_list(self):
        requests = [make_request(speed_knots=s) for s in (10.0, 15.0, 20.0)]
        response = client.post("/features/batch", json=requests)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 3
        assert [r["speed_knots"] for r in body] == [10.0, 15.0, 20.0]

    def test_batch_with_invalid_item_returns_422(self):
        requests = [make_request(), make_request(distance_km=-5.0)]
        response = client.post("/features/batch", json=requests)
        assert response.status_code == 422


class TestRecords:
    def test_records_returns_empty_list_when_no_data(self):
        response = client.get("/records")
        assert response.status_code == 200
        assert response.json() == []

    def test_records_with_filters_returns_empty_list_when_no_data(self):
        response = client.get("/records", params={"vessel_type": "container", "route_id": "R1"})
        assert response.status_code == 200
        assert response.json() == []


class TestPorts:
    def test_returns_full_port_catalog(self):
        response = client.get("/ports")
        assert response.status_code == 200
        body = response.json()
        assert len(body) >= 20
        assert all({"name", "country", "lat", "lon"} <= set(p.keys()) for p in body)
        names = {p["name"] for p in body}
        assert "Mumbai" in names
        assert "Rotterdam" in names


class TestRoutes:
    def test_valid_query_returns_route_options(self):
        response = client.get("/routes", params={"origin": "Mumbai", "destination": "Rotterdam"})
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        for option in body:
            assert option["origin"] == "Mumbai"
            assert option["destination"] == "Rotterdam"
            assert option["distance_km"] > 0
            assert isinstance(option["via"], str) and option["via"]

    def test_same_coast_pair_returns_single_option(self):
        response = client.get("/routes", params={"origin": "Los Angeles", "destination": "Seattle"})
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_unknown_origin_returns_404_with_helpful_message(self):
        response = client.get("/routes", params={"origin": "Atlantis", "destination": "Rotterdam"})
        assert response.status_code == 404
        assert "Atlantis" in response.json()["detail"]

    def test_same_origin_and_destination_returns_400(self):
        response = client.get("/routes", params={"origin": "Mumbai", "destination": "Mumbai"})
        assert response.status_code == 400
