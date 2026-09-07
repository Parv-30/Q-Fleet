"""Cleaning, validation, and feature engineering: VoyageRequest -> ProcessedFeatures.

This is the single place these transformations happen. prediction-service
calls data-service's /features endpoint (backed by this pipeline) instead of
reimplementing feature engineering, so training-time and inference-time
transformations can never drift apart.
"""

from __future__ import annotations

from common.schemas import FuelType, ProcessedFeatures, VesselType, VoyageRequest

# vessel_type and fuel_type are one-hot encoded (see ProcessedFeatures'
# docstring/comments in common/schemas.py) rather than mapped to a single
# ordinal int, since both are genuinely unordered categories and a fake
# numeric ordering was diagnosed as hurting the XGBoost models' ability to
# generalize across categories (most visibly for LNG/hydrogen fuel types at
# low speed). These maps go from an enum member to the exact
# ProcessedFeatures field name for that member's one-hot column.
_VESSEL_TYPE_FIELDS: dict[VesselType, str] = {
    VesselType.CONTAINER: "vessel_type_container",
    VesselType.BULK_CARRIER: "vessel_type_bulk_carrier",
    VesselType.TANKER: "vessel_type_tanker",
    VesselType.RO_RO: "vessel_type_ro_ro",
    VesselType.GENERAL_CARGO: "vessel_type_general_cargo",
}
_FUEL_TYPE_FIELDS: dict[FuelType, str] = {
    FuelType.HFO: "fuel_type_hfo",
    FuelType.DIESEL: "fuel_type_diesel",
    FuelType.LNG: "fuel_type_lng",
    FuelType.METHANOL: "fuel_type_methanol",
    FuelType.HYDROGEN: "fuel_type_hydrogen",
    FuelType.AMMONIA: "fuel_type_ammonia",
}

# Normalization ceilings for the weather severity index. Values beyond these
# are clamped to the max severity rather than allowed to blow up the index.
_MAX_WIND_SPEED = 40.0  # m/s, ~ Beaufort 12
_MAX_WAVE_HEIGHT = 14.0  # meters, phenomenal sea state
_MAX_CURRENT_SPEED = 5.0  # knots


class FeaturePipeline:
    """Reusable transform from a single VoyageRequest (or a batch) into the
    canonical ProcessedFeatures schema."""

    def transform(self, request: VoyageRequest) -> ProcessedFeatures:
        vessel_onehot = {field: 0 for field in _VESSEL_TYPE_FIELDS.values()}
        vessel_onehot[_VESSEL_TYPE_FIELDS[request.vessel_type]] = 1

        fuel_onehot = {field: 0 for field in _FUEL_TYPE_FIELDS.values()}
        fuel_onehot[_FUEL_TYPE_FIELDS[request.fuel_type]] = 1

        return ProcessedFeatures(
            distance_km=request.distance_km,
            speed_knots=request.speed_knots,
            speed_cubed=request.speed_knots**3,
            cargo_tonnes=request.cargo_tonnes,
            cargo_utilization=request.cargo_utilization,
            weather_severity=self._weather_severity(request),
            **vessel_onehot,
            **fuel_onehot,
        )

    def transform_batch(self, requests: list[VoyageRequest]) -> list[ProcessedFeatures]:
        return [self.transform(r) for r in requests]

    @staticmethod
    def _weather_severity(request: VoyageRequest) -> float:
        """Combine wind, wave, and current into a single 0-1 severity index
        via an unweighted mean of each component's normalized magnitude."""
        wind_component = min(abs(request.wind_speed) / _MAX_WIND_SPEED, 1.0)
        wave_component = min(abs(request.wave_height) / _MAX_WAVE_HEIGHT, 1.0)
        current_component = min(abs(request.current_speed) / _MAX_CURRENT_SPEED, 1.0)
        return (wind_component + wave_component + current_component) / 3.0
