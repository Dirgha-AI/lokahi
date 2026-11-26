"""Tests for the stop profile a clamped chunk is replaced with.

The profile is what an actuator executes after a refusal, so the assertions are
about physical admissibility (bounded acceleration and jerk, monotone approach,
rest at the end) rather than about an exact curve.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import replanning
from vla.replanning import Limits, ReplanError, stop_duration, stop_profile

LIMITS = Limits(max_acceleration=2.0, max_jerk=10.0, max_velocity=1.5)
DT = 0.005


def test_a_trapezoid_stop_ends_at_rest_at_the_analytic_time():
    profile = stop_profile((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), LIMITS, control_period_s=DT)
    # 2*A/J + (v - A^2/J)/A = 0.4 + 0.3 = 0.7s
    assert profile[-1].time_s == pytest.approx(0.7, abs=1e-9)
    assert profile[-1].velocity == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


def test_a_stop_never_reverses_direction():
    profile = stop_profile((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), LIMITS, control_period_s=DT)
    positions = [waypoint.position[0] for waypoint in profile]
    assert all(later >= earlier for earlier, later in zip(positions, positions[1:]))
    assert positions[-1] > 0.0


def test_a_stop_respects_the_acceleration_and_jerk_limits():
    profile = stop_profile((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), LIMITS, control_period_s=DT)
    accelerations = [waypoint.acceleration[0] for waypoint in profile]
    assert max(abs(value) for value in accelerations) <= LIMITS.max_acceleration + 1e-9
    jerks = [(later - earlier) / DT for earlier, later in zip(accelerations, accelerations[1:])]
    assert max(abs(value) for value in jerks) <= LIMITS.max_jerk + 1e-6


def test_a_slow_axis_uses_the_triangular_profile_without_exceeding_limits():
    # v below A^2/J: peak deceleration can never be reached.
    profile = stop_profile((0.0, 0.0, 0.0), (0.01, 0.0, 0.0), LIMITS, control_period_s=DT)
    accelerations = [waypoint.acceleration[0] for waypoint in profile]
    assert max(abs(value) for value in accelerations) <= math.sqrt(0.01 * LIMITS.max_jerk) + 1e-9
    assert profile[-1].velocity[0] == pytest.approx(0.0, abs=1e-9)


def test_a_negative_velocity_stops_in_the_negative_direction():
    profile = stop_profile((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), LIMITS, control_period_s=DT)
    assert profile[-1].position[0] < 0.0
    assert profile[-1].velocity[0] == pytest.approx(0.0, abs=1e-9)


def test_an_axis_at_rest_does_not_move():
    profile = stop_profile((0.2, 0.3, 0.4), (1.0, 0.0, 0.0), LIMITS, control_period_s=DT)
    assert [waypoint.position[1] for waypoint in profile] == pytest.approx([0.3] * len(profile))
    assert [waypoint.position[2] for waypoint in profile] == pytest.approx([0.4] * len(profile))


def test_a_stop_from_rest_is_a_single_waypoint_at_the_current_pose():
    profile = stop_profile((0.2, 0.3, 0.4), (0.0, 0.0, 0.0), LIMITS, control_period_s=DT)
    assert len(profile) == 1
    assert profile[0].position == pytest.approx((0.2, 0.3, 0.4))
    assert stop_duration((0.0, 0.0, 0.0), LIMITS) == 0.0


def test_the_longest_axis_sets_the_duration():
    assert stop_duration((1.0, 0.5, 0.0), LIMITS) == pytest.approx(
        stop_duration((1.0, 0.0, 0.0), LIMITS))


@pytest.mark.parametrize("kwargs", [
    {"max_acceleration": 0.0, "max_jerk": 1.0},
    {"max_acceleration": 1.0, "max_jerk": 0.0},
    {"max_acceleration": 1.0, "max_jerk": 1.0, "max_velocity": 0.0},
])
def test_impossible_limits_are_refused(kwargs):
    with pytest.raises(ReplanError):
        Limits(**kwargs)


def test_a_bad_control_period_is_refused():
    with pytest.raises(ReplanError):
        stop_profile((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), LIMITS, control_period_s=0.0)


def test_a_stop_that_would_run_too_long_is_refused_rather_than_truncated():
    with pytest.raises(ReplanError, match="max_duration_s"):
        stop_profile((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), LIMITS,
                     control_period_s=DT, max_duration_s=0.1)


def test_the_analytic_backend_is_always_available():
    assert replanning.backends()["analytic"] is True


def test_ruckig_without_the_extra_names_the_extra():
    if replanning.ruckig_available():  # pragma: no cover - environment dependent
        pytest.skip("Ruckig is installed here; the refusal path needs it absent")
    with pytest.raises(ReplanError, match="vla-replan"):
        replanning.ruckig_stop_profile((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), LIMITS)


def test_auto_never_requires_the_extra():
    profile = replanning.make_stop_profile((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), LIMITS,
                                           control_period_s=DT)
    assert profile[-1].velocity[0] == pytest.approx(0.0, abs=1e-9)


def test_an_unknown_backend_is_refused():
    with pytest.raises(ReplanError, match="unknown"):
        replanning.make_stop_profile((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), LIMITS, backend="magic")
