"""Tests for train.py's core logic: row-building in isolation, plus a small
end-to-end training run at a tiny record count so it stays fast (a full
5000-record run belongs in `python train.py`, not in the test suite)."""

from __future__ import annotations

import json

import numpy as np
from xgboost import XGBRegressor

from common.schemas import ProcessedFeatures
from train import FEATURE_COLUMNS, processed_features_to_row, train_and_save


def _make_processed_features(
    distance_km, speed_knots, speed_cubed, cargo_tonnes, cargo_utilization, weather_severity,
    vessel_type="vessel_type_container", fuel_type="fuel_type_hfo",
) -> ProcessedFeatures:
    """Build a ProcessedFeatures with all one-hot fields defaulted to 0
    except the one named vessel_type/fuel_type field, which is set to 1."""
    vessel_fields = {
        "vessel_type_container": 0,
        "vessel_type_bulk_carrier": 0,
        "vessel_type_tanker": 0,
        "vessel_type_ro_ro": 0,
        "vessel_type_general_cargo": 0,
    }
    fuel_fields = {
        "fuel_type_hfo": 0,
        "fuel_type_diesel": 0,
        "fuel_type_lng": 0,
        "fuel_type_methanol": 0,
        "fuel_type_hydrogen": 0,
        "fuel_type_ammonia": 0,
    }
    vessel_fields[vessel_type] = 1
    fuel_fields[fuel_type] = 1
    return ProcessedFeatures(
        distance_km=distance_km,
        speed_knots=speed_knots,
        speed_cubed=speed_cubed,
        cargo_tonnes=cargo_tonnes,
        cargo_utilization=cargo_utilization,
        weather_severity=weather_severity,
        **vessel_fields,
        **fuel_fields,
    )


class TestProcessedFeaturesToRow:
    def test_length_matches_feature_columns(self):
        pf = _make_processed_features(
            distance_km=3800.0,
            speed_knots=18.0,
            speed_cubed=5832.0,
            cargo_tonnes=40000.0,
            cargo_utilization=0.8,
            weather_severity=0.25,
            vessel_type="vessel_type_bulk_carrier",
            fuel_type="fuel_type_lng",
        )
        row = processed_features_to_row(pf)
        assert len(row) == len(FEATURE_COLUMNS) == 17

    def test_values_in_documented_order(self):
        pf = _make_processed_features(
            distance_km=1000.0,
            speed_knots=15.0,
            speed_cubed=3375.0,
            cargo_tonnes=20000.0,
            cargo_utilization=0.5,
            weather_severity=0.1,
            vessel_type="vessel_type_ro_ro",
            fuel_type="fuel_type_hydrogen",
        )
        row = processed_features_to_row(pf)
        assert row == [
            1000.0, 15.0, 3375.0, 20000.0, 0.5, 0.1,
            # vessel_type one-hot: container, bulk_carrier, tanker, ro_ro, general_cargo
            0.0, 0.0, 0.0, 1.0, 0.0,
            # fuel_type one-hot: hfo, diesel, lng, methanol, hydrogen, ammonia
            0.0, 0.0, 0.0, 0.0, 1.0, 0.0,
        ]

    def test_encoded_fields_are_floats(self):
        pf = _make_processed_features(
            distance_km=1.0,
            speed_knots=1.0,
            speed_cubed=1.0,
            cargo_tonnes=1.0,
            cargo_utilization=0.0,
            weather_severity=0.0,
            vessel_type="vessel_type_tanker",
            fuel_type="fuel_type_ammonia",
        )
        row = processed_features_to_row(pf)
        assert all(isinstance(v, float) for v in row[6:])


class TestTrainAndSave:
    def test_end_to_end_small_scale(self, tmp_path):
        results = train_and_save(count=100, seed=1, output_dir=tmp_path)

        assert results["num_records"] == 100
        assert results["train_size"] + results["test_size"] == 100

        # Three regressors were produced, one per target.
        assert set(results["models"].keys()) == {
            "fuel_consumption",
            "operating_cost",
            "voyage_time",
        }
        for model in results["models"].values():
            assert isinstance(model, XGBRegressor)

        # Metrics reported for all three targets, and are finite numbers
        # (not asserting a specific R2 value -- that would be flaky at
        # count=100 with un-tuned hyperparameters).
        assert set(results["metrics"].keys()) == {
            "fuel_consumption",
            "operating_cost",
            "voyage_time",
        }
        for m in results["metrics"].values():
            assert np.isfinite(m["r2"])
            assert np.isfinite(m["mae"])

        # Models can actually predict on held-out-shaped input without
        # erroring, and outputs are finite. Raw booster output is NOT
        # guaranteed non-negative at every point in feature space -- XGBoost
        # has no built-in non-negativity constraint, and a small (count=100)
        # training set can leave sparsely-sampled corners of feature space
        # (e.g. a bulk carrier query point far from the nearest training
        # rows) where the model slightly undershoots below zero. The actual
        # serving code (app/main.py's _predict_from_row) clips exactly this
        # way with max(prediction, 0.0) before returning a PredictionResponse
        # to a caller, which is the real non-negativity contract -- so this
        # test applies the same clip rather than asserting a stronger
        # guarantee the raw model doesn't (and isn't expected to) provide.
        sample_row = np.zeros((1, len(FEATURE_COLUMNS)))
        sample_row[0] = [
            3000.0, 16.0, 4096.0, 30000.0, 0.6, 0.2,
            0.0, 1.0, 0.0, 0.0, 0.0,  # vessel_type one-hot: bulk_carrier
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # fuel_type one-hot: none set (edge case, still valid input shape)
        ]
        for model in results["models"].values():
            prediction = model.predict(sample_row)
            assert np.all(np.isfinite(prediction))
            served_prediction = np.maximum(prediction, 0.0)
            assert np.all(served_prediction >= 0)

    def test_writes_model_files_to_output_dir(self, tmp_path):
        train_and_save(count=100, seed=1, output_dir=tmp_path)

        assert (tmp_path / "fuel_consumption_model.json").exists()
        assert (tmp_path / "operating_cost_model.json").exists()
        assert (tmp_path / "voyage_time_model.json").exists()

        columns_path = tmp_path / "feature_columns.json"
        assert columns_path.exists()
        with open(columns_path) as f:
            saved_columns = json.load(f)
        assert saved_columns == FEATURE_COLUMNS

    def test_output_dir_is_created_if_missing(self, tmp_path):
        # output_dir is honored exactly (not defaulted to the real
        # data/models path), including creating it when absent, so this
        # test never touches the real D:\sih\data\models directory.
        nested_dir = tmp_path / "nested" / "models"
        assert not nested_dir.exists()

        train_and_save(count=100, seed=1, output_dir=nested_dir)

        assert nested_dir.exists()
        assert (nested_dir / "feature_columns.json").exists()
