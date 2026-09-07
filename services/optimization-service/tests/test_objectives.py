"""Tests for the reliability/cargo_satisfaction/fleet_utilization proxy
formulas in app/objectives.py (fuel_consumption/operating_cost/
lifecycle_ghg are pass-throughs from prediction/emission responses and are
only sanity-checked here, not re-derived)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.objectives import _cargo_satisfaction, _fleet_utilization, _reliability, evaluate_candidate
from common.schemas import EmissionResponse, FuelType, PredictionResponse

PREDICTION = PredictionResponse(fuel_consumption=120.0, operating_cost=45000.0, voyage_time=96.0)
EMISSION = EmissionResponse(lifecycle_ghg=600.0)


def test_evaluate_candidate_populates_all_six_fields_in_range():
    objectives = evaluate_candidate(
        vessel="V1",
        route="R1",
        speed=14.0,
        fuel=FuelType.HFO,
        prediction=PREDICTION,
        emission=EMISSION,
        weather_severity=0.3,
        cargo_utilization=0.7,
    )
    assert objectives.fuel_consumption == PREDICTION.fuel_consumption
    assert objectives.operating_cost == PREDICTION.operating_cost
    assert objectives.lifecycle_ghg == EMISSION.lifecycle_ghg
    for field in ("reliability", "cargo_satisfaction", "fleet_utilization"):
        value = getattr(objectives, field)
        assert 0.0 <= value <= 1.0


# --- reliability -----------------------------------------------------------


def test_reliability_higher_weather_severity_lowers_reliability():
    calm = _reliability(weather_severity=0.0, speed_knots=14.0)
    stormy = _reliability(weather_severity=0.9, speed_knots=14.0)
    assert stormy < calm


def test_reliability_excessive_speed_in_bad_weather_lowers_reliability():
    moderate_speed = _reliability(weather_severity=0.8, speed_knots=14.0)
    excessive_speed = _reliability(weather_severity=0.8, speed_knots=23.0)
    assert excessive_speed < moderate_speed


def test_reliability_high_speed_in_calm_weather_is_not_penalized_as_much_as_in_storm():
    # Same high speed, different weather -- storm should be penalized more
    # since the safe-speed ceiling is lower in bad weather.
    calm_high_speed = _reliability(weather_severity=0.0, speed_knots=21.0)
    storm_high_speed = _reliability(weather_severity=1.0, speed_knots=21.0)
    assert storm_high_speed < calm_high_speed


def test_reliability_stays_within_bounds():
    for weather in (0.0, 0.5, 1.0):
        for speed in (8.0, 14.0, 24.0):
            value = _reliability(weather_severity=weather, speed_knots=speed)
            assert 0.0 <= value <= 1.0


def test_reliability_perfect_calm_low_speed_is_maximal():
    value = _reliability(weather_severity=0.0, speed_knots=8.0)
    assert value == 1.0


# --- cargo_satisfaction ------------------------------------------------


def test_cargo_satisfaction_sweet_spot_beats_extremes():
    sweet_spot = _cargo_satisfaction(0.7)
    near_empty = _cargo_satisfaction(0.05)
    near_full = _cargo_satisfaction(0.99)
    assert sweet_spot > near_empty
    assert sweet_spot > near_full


def test_cargo_satisfaction_zero_utilization_is_zero():
    assert _cargo_satisfaction(0.0) == 0.0


def test_cargo_satisfaction_full_utilization_has_nonzero_floor():
    value = _cargo_satisfaction(1.0)
    assert value > 0.0
    assert value < 1.0


def test_cargo_satisfaction_monotonic_increasing_below_band():
    low = _cargo_satisfaction(0.1)
    higher = _cargo_satisfaction(0.4)
    assert higher > low


def test_cargo_satisfaction_monotonic_decreasing_above_band():
    high_edge = _cargo_satisfaction(0.9)
    higher_util = _cargo_satisfaction(0.98)
    assert higher_util < high_edge


def test_cargo_satisfaction_stays_within_bounds():
    for u in (0.0, 0.1, 0.5, 0.85, 0.95, 1.0):
        value = _cargo_satisfaction(u)
        assert 0.0 <= value <= 1.0


# --- fleet_utilization ---------------------------------------------------


def test_fleet_utilization_monotonic_in_cargo_utilization():
    low = _fleet_utilization(0.2)
    mid = _fleet_utilization(0.5)
    high = _fleet_utilization(0.95)
    assert low < mid < high


def test_fleet_utilization_is_distinct_from_cargo_satisfaction_at_high_utilization():
    # At high utilization, fleet_utilization keeps rising while
    # cargo_satisfaction falls off -- the two proxies must diverge, not be
    # a disguised duplicate of each other.
    u = 0.97
    assert _fleet_utilization(u) > _cargo_satisfaction(u)


def test_fleet_utilization_stays_within_bounds():
    for u in (0.0, 0.5, 1.0):
        value = _fleet_utilization(u)
        assert 0.0 <= value <= 1.0


# --- deadline-aware reliability -------------------------------------------


def test_no_deadline_given_reliability_unchanged_from_baseline():
    """Regression test: omitting delivery_deadline/departure_time (the
    default) must produce byte-identical reliability to before this
    parameter pair existed."""
    baseline = _reliability(weather_severity=0.3, speed_knots=14.0)
    with_voyage_time_but_no_deadline = _reliability(
        weather_severity=0.3, speed_knots=14.0, voyage_time_hours=96.0
    )
    assert with_voyage_time_but_no_deadline == baseline


def test_deadline_comfortably_met_no_penalty():
    departure = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = departure + timedelta(hours=200)  # generous
    with_margin = _reliability(
        weather_severity=0.3,
        speed_knots=14.0,
        voyage_time_hours=96.0,  # arrives well before the 200h deadline
        delivery_deadline=deadline,
        departure_time=departure,
    )
    without_deadline = _reliability(weather_severity=0.3, speed_knots=14.0)
    assert with_margin == without_deadline


def test_deadline_missed_meaningfully_reduces_reliability():
    departure = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = departure + timedelta(hours=48)
    on_time = _reliability(
        weather_severity=0.2,
        speed_knots=14.0,
        voyage_time_hours=40.0,  # arrives before the 48h deadline
        delivery_deadline=deadline,
        departure_time=departure,
    )
    late = _reliability(
        weather_severity=0.2,
        speed_knots=14.0,
        voyage_time_hours=90.0,  # arrives 42h past the 48h deadline
        delivery_deadline=deadline,
        departure_time=departure,
    )
    assert late < on_time


def test_deadline_penalty_scales_with_hours_late():
    departure = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = departure + timedelta(hours=48)
    slightly_late = _reliability(
        weather_severity=0.0,
        speed_knots=10.0,
        voyage_time_hours=50.0,  # 2h late
        delivery_deadline=deadline,
        departure_time=departure,
    )
    very_late = _reliability(
        weather_severity=0.0,
        speed_knots=10.0,
        voyage_time_hours=100.0,  # 52h late
        delivery_deadline=deadline,
        departure_time=departure,
    )
    assert very_late < slightly_late


def test_deadline_aware_reliability_stays_within_bounds():
    departure = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = departure + timedelta(hours=10)
    value = _reliability(
        weather_severity=0.9,
        speed_knots=23.0,
        voyage_time_hours=1000.0,  # wildly late
        delivery_deadline=deadline,
        departure_time=departure,
    )
    assert 0.0 <= value <= 1.0


def test_evaluate_candidate_with_deadline_reduces_reliability_when_late():
    departure = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tight_deadline = departure + timedelta(hours=10)  # PREDICTION.voyage_time=96h, way late

    objectives_no_deadline = evaluate_candidate(
        vessel="V1",
        route="R1",
        speed=14.0,
        fuel=FuelType.HFO,
        prediction=PREDICTION,
        emission=EMISSION,
        weather_severity=0.3,
        cargo_utilization=0.7,
    )
    objectives_with_deadline = evaluate_candidate(
        vessel="V1",
        route="R1",
        speed=14.0,
        fuel=FuelType.HFO,
        prediction=PREDICTION,
        emission=EMISSION,
        weather_severity=0.3,
        cargo_utilization=0.7,
        delivery_deadline=tight_deadline,
        departure_time=departure,
    )
    assert objectives_with_deadline.reliability < objectives_no_deadline.reliability
    assert 0.0 <= objectives_with_deadline.reliability <= 1.0


def test_evaluate_candidate_without_deadline_args_matches_baseline_exactly():
    """Regression test: evaluate_candidate's default (no deadline args)
    must be identical to before this sub-phase's change."""
    objectives = evaluate_candidate(
        vessel="V1",
        route="R1",
        speed=14.0,
        fuel=FuelType.HFO,
        prediction=PREDICTION,
        emission=EMISSION,
        weather_severity=0.3,
        cargo_utilization=0.7,
    )
    expected_reliability = _reliability(weather_severity=0.3, speed_knots=14.0)
    assert objectives.reliability == expected_reliability
