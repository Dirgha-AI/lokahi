"""Fail-closed regression tests for the audit findings of 2026-11-27.

Each test here reproduces one of the audit's triggers and asserts a named
refusal instead of a COMPLIANT result. They are written against
`docs/AUDIT-FINDINGS-2026-11-27.md` (F1, F2, F3, F4, F5, F6, F7) and fail on the
tree before the hardening: a check that is configured but not run is the one
failure mode a gate must never have, because the record then says the arm was
cleared.

The wording of every refusal is part of the contract, so the assertions match on
the exact named violation rather than on "any violation".
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla.physical_manifest import VLAManifestError, load_physical_vla
from vla.vla_gate import ARMED, CLAMPED, E_STOP, SOFT_STOP, LokahiVLAGate

BOX = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
OBSTACLE = [0.4, 0.4, 0.0, 0.6, 0.6, 1.0]
T0 = 1_000_000_000_000
MS = 1_000_000

# A wrench every step supplies, so the fail-closed wrench check is not what a
# test about geometry or timing happens to trip on.
WRENCH = {"force_n": 1.0, "torque_nm": 1.0}


def manifest(**overrides):
    section = {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        "control_period_ms": 20.0,
        "max_gate_eval_us": 10_000_000.0,
        "workspace_aabb": BOX,
        "keep_out_aabbs": [],
        "kinematic_limits": {"max_pos_delta_m": 0.25},
    }
    section.update(overrides)
    return {
        "node": "robot:test",
        "physical_envelope": {"max_contact_force_n": 10.0, "max_joint_torque_nm": 45.0},
        "physical_vla": section,
    }


def verified(_document):
    return True


def bare_manifest(**section):
    """A section with the timing keys and nothing geometric, so one source can be tested alone."""
    base = {"max_action_age_ms": 150.0, "watchdog_timeout_ms": 500.0, "control_period_ms": 20.0,
            "max_gate_eval_us": 10_000_000.0}
    base.update(section)
    return {
        "node": "robot:test",
        "physical_envelope": {"max_contact_force_n": 10.0, "max_joint_torque_nm": 45.0},
        "physical_vla": base,
    }


def gate(**overrides):
    return LokahiVLAGate.from_manifest(manifest(**overrides), verifier=verified)


def bare_gate(**section):
    return LokahiVLAGate.from_manifest(bare_manifest(**section), verifier=verified)


def state(x=0.5, y=0.5, z=0.5, **extra):
    robot_state = {"ee_pos": [x, y, z], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}
    robot_state.update(extra)
    return robot_state


def chunk(*points, duration_ms=20.0):
    return [
        {"ee_pos": list(point), "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": duration_ms}
        for point in points
    ]


def reasons(result):
    return result.violated_invariants


# ---------------------------------------------------------------------------
# F1 — duration_ms is validated before any check uses it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0, 0.0, "20", None, True])
@pytest.mark.parametrize("position", [0, 1, -1])
def test_a_non_finite_or_non_positive_duration_is_a_structural_refusal(bad, position):
    """A NaN duration made the tail's age NaN, and NaN comparisons are all False."""
    points = [[0.45, 0.5, 0.5], [0.5, 0.5, 0.5], [0.55, 0.5, 0.5]]
    elements = chunk(*points)
    elements[position]["duration_ms"] = bad
    result = gate().evaluate_step(T0, T0 + 10 * MS, state(), elements, wrench=WRENCH)
    assert result.verdict == "FORBIDDEN", result.violated_invariants
    index = position if position >= 0 else len(points) + position
    assert any(reason.startswith(f"structural:chunk[{index}]:duration_ms") for reason in reasons(result)), reasons(result)


def test_a_missing_duration_is_refused_rather_than_defaulted():
    elements = [{"ee_pos": [0.55, 0.5, 0.5], "ee_quat": [1.0, 0.0, 0.0, 0.0]}]
    result = gate().evaluate_step(T0, T0, state(), elements, wrench=WRENCH)
    assert result.verdict == "FORBIDDEN"
    assert any("structural:chunk[0]:duration_ms" in reason for reason in reasons(result)), reasons(result)


def test_a_nan_duration_cannot_hide_a_stale_tail():
    """F1's reproduction: NaN on element 0 made the tail's age NaN, which is never > the limit."""
    stale_tail = [
        {"ee_pos": [0.51, 0.5, 0.5], "duration_ms": float("nan")},
        {"ee_pos": [0.52, 0.5, 0.5], "duration_ms": 20.0},
    ]
    control = gate().evaluate_step(
        T0, T0 + 100 * MS, state(),
        [{"ee_pos": [0.51, 0.5, 0.5], "duration_ms": 1000.0},
         {"ee_pos": [0.52, 0.5, 0.5], "duration_ms": 20.0}],
        wrench=WRENCH,
    )
    assert any("stale_action" in reason for reason in reasons(control))
    result = gate().evaluate_step(T0, T0 + 100 * MS, state(), stale_tail, wrench=WRENCH)
    assert result.verdict == "FORBIDDEN"
    assert result.allowed is False
    assert any("duration_ms" in reason for reason in reasons(result)), reasons(result)


def test_no_stop_profile_can_carry_a_non_finite_velocity():
    """F1's second consequence: a NaN duration divided into an "empty" deceleration."""
    g = gate(clamp_strategy="replan", replan_limits={"max_acceleration_m_s2": 4.0, "max_jerk_m_s3": 40.0})
    elements = chunk([0.55, 0.5, 0.5], [0.6, 0.5, 0.5], [0.95, 0.5, 0.5])
    elements[-1]["duration_ms"] = float("nan")
    result = g.evaluate_step(T0, T0, state(), elements, wrench=WRENCH)
    assert result.verdict == "FORBIDDEN", reasons(result)
    assert any("duration_ms" in reason for reason in reasons(result)), reasons(result)
    for waypoint in result.replanned_path:
        assert all(abs(component) < float("inf") for component in waypoint["ee_vel"])
    for waypoint in (result.safe_action or {}).get("stop_profile", []):
        assert all(abs(component) < float("inf") for component in waypoint["ee_vel"])


# ---------------------------------------------------------------------------
# F2 — a missing ee_pos is refused whenever any configured check needs the pose
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("section", [
    {"workspace_aabb": BOX},
    {"keep_out_aabbs": [OBSTACLE]},
    {"keep_out_shapes": [{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.3}]},
    {"kinematic_limits": {"max_pos_delta_m": 0.25}},
    {"kinematic_limits": {"max_rot_delta_rad": 0.35}},
])
def test_missing_ee_pos_is_refused_for_every_geometry_source(section):
    """F2's reproduction: a shapes-only manifest passed a setpoint at a keep-out centre."""
    g = bare_gate(**section)
    result = g.evaluate_step(
        T0, T0, {"joint_pos": [0.0, 0.0]}, chunk([0.55, 0.5, 0.5]), wrench=WRENCH
    )
    assert result.verdict == "FORBIDDEN"
    assert "structural:robot_state_missing_ee_pos" in reasons(result), reasons(result)


def test_a_shapes_only_manifest_cannot_clear_a_setpoint_at_the_keep_out_centre():
    g = bare_gate(keep_out_shapes=[{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.3}])
    result = g.evaluate_step(
        T0, T0, {"joint_pos": [0.0, 0.0]}, chunk([0.5, 0.5, 0.5]), wrench=WRENCH
    )
    assert result.allowed is False
    assert "structural:robot_state_missing_ee_pos" in reasons(result), reasons(result)


def test_a_dynamic_keep_out_also_needs_the_current_pose():
    g = bare_gate(max_dynamic_keep_outs=1, max_dynamic_radius_m=0.5)
    result = g.evaluate_step(
        T0, T0, {"joint_pos": [0.0, 0.0]}, chunk([0.55, 0.5, 0.5]), wrench=WRENCH,
        dynamic_keep_outs=[{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}],
    )
    assert result.verdict == "FORBIDDEN"
    assert "structural:robot_state_missing_ee_pos" in reasons(result), reasons(result)


# ---------------------------------------------------------------------------
# F3 — a configured joint check refuses instead of skipping
# ---------------------------------------------------------------------------

JOINT_LIMITS = {"joint_limits": [[-1.0, 1.0]] * 6, "max_joint_vel_rad_s": 1.0}


def test_a_missing_current_joint_vector_is_refused_when_joint_limits_are_configured():
    """F3's reproduction: joints [9]*6 against [-1, 1] but robot_state carried no joint_pos."""
    g = gate(kinematic_limits=JOINT_LIMITS)
    robot_state = {"ee_pos": [0.5, 0.5, 0.5]}
    elements = [{"ee_pos": [0.51, 0.5, 0.5], "joint_pos": [9.0] * 6, "duration_ms": 20.0}]
    result = g.evaluate_step(T0, T0, robot_state, elements, wrench=WRENCH)
    assert result.verdict == "FORBIDDEN"
    assert "structural:robot_state_missing_joint_pos" in reasons(result), reasons(result)


@pytest.mark.parametrize("limits", [
    {"joint_limits": [[-1.0, 1.0]] * 6},
    {"max_joint_vel_rad_s": 1.0},
    {"max_joint_acc_rad_s2": 5.0},
])
def test_every_configured_joint_check_needs_the_current_joint_vector(limits):
    g = gate(kinematic_limits=limits)
    result = g.evaluate_step(
        T0, T0, {"ee_pos": [0.5, 0.5, 0.5]},
        [{"ee_pos": [0.51, 0.5, 0.5], "joint_pos": [0.0] * 6, "duration_ms": 20.0}],
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN"
    assert "structural:robot_state_missing_joint_pos" in reasons(result), reasons(result)


def test_a_chunk_element_without_joint_pos_is_refused_when_a_joint_check_is_configured():
    g = gate(kinematic_limits=JOINT_LIMITS)
    result = g.evaluate_step(
        T0, T0, state(joint_pos=[0.0] * 6),
        [{"ee_pos": [0.51, 0.5, 0.5], "duration_ms": 20.0}],
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN"
    assert any("joint_pos" in reason for reason in reasons(result)), reasons(result)


def test_an_unconfigured_joint_check_is_still_skipped():
    """The old behaviour is only wrong when the manifest configured the check."""
    result = gate().evaluate_step(
        T0, T0, {"ee_pos": [0.5, 0.5, 0.5]},
        [{"ee_pos": [0.51, 0.5, 0.5], "duration_ms": 20.0}],
        wrench=WRENCH,
    )
    assert result.verdict == "COMPLIANT", reasons(result)


def test_a_configured_joint_limit_is_still_evaluated_per_element():
    g = gate(kinematic_limits=JOINT_LIMITS)
    result = g.evaluate_step(
        T0, T0, state(joint_pos=[0.0] * 6),
        [{"ee_pos": [0.51, 0.5, 0.5], "joint_pos": [9.0] * 6, "duration_ms": 20.0}],
        wrench=WRENCH,
    )
    assert any("kinematic:joint_limit" in reason for reason in reasons(result)), reasons(result)


# ---------------------------------------------------------------------------
# F4 — a configured force or torque ceiling refuses an unverifiable wrench
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wrench", [
    None,
    {},
    {"force_n": float("nan")},
    {"force_n": float("inf")},
    {"force_n": "12.0"},
    {"force_n": [1.0, float("nan"), 0.0]},
])
def test_a_configured_force_ceiling_refuses_an_unverifiable_wrench(wrench):
    """F4's reproduction: NaN or no wrench read as "force fine"."""
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench=wrench)
    assert result.verdict == "FORBIDDEN"
    assert any("wrench:wrench_unverifiable" in reason for reason in reasons(result)), reasons(result)


def test_a_configured_torque_ceiling_refuses_an_unverifiable_torque():
    result = gate().evaluate_step(
        T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench={"force_n": 1.0, "torque_nm": float("nan")}
    )
    assert result.verdict == "FORBIDDEN"
    assert any("wrench:wrench_unverifiable:torque" in reason for reason in reasons(result)), reasons(result)


def test_a_measured_wrench_within_the_ceiling_still_passes():
    result = gate().evaluate_step(
        T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench={"force_n": 9.0, "torque_nm": 40.0}
    )
    assert result.verdict == "COMPLIANT", reasons(result)


def test_absent_ceilings_keep_the_old_behaviour():
    document = manifest()
    document["physical_envelope"] = {}
    g = LokahiVLAGate.from_manifest(document, verifier=verified)
    result = g.evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench=None)
    assert result.verdict == "COMPLIANT", reasons(result)


def test_a_measured_overload_is_still_a_wrench_violation():
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench={"force_n": 500.0})
    assert any("wrench:force" in reason for reason in reasons(result)), reasons(result)


# ---------------------------------------------------------------------------
# F5 — recovery is a bounded excursion, not an end-pose comparison
# ---------------------------------------------------------------------------

def test_a_recovery_chunk_may_not_leave_and_re_enter_the_same_keep_out():
    """F5's reproduction: out to 0.70 and back to 0.55 was COMPLIANT."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(
        T0, T0, state(0.55, 0.5, 0.52),
        chunk([0.70, 0.5, 0.52], [0.55, 0.5, 0.51]),
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN", reasons(result)
    assert any(
        "workspace_keep_out" in reason or "recovery_required:keep_out" in reason
        for reason in reasons(result)
    ), reasons(result)


def test_a_recovery_chunk_may_not_travel_further_out_of_the_workspace_than_it_gains():
    """F5's reproduction: 0.5 m outside, 2 m outside mid-chunk, 0.4 m outside at the end."""
    result = gate().evaluate_step(
        T0, T0, state(1.5, 0.5, 0.5),
        chunk([3.0, 0.5, 0.5], [1.4, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN", reasons(result)
    assert any(reason.startswith("recovery_required:keep_in:excursion") for reason in reasons(result)), reasons(result)


def test_a_recovery_chunk_may_not_dig_deeper_mid_chunk_than_it_gains():
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(
        T0, T0, state(0.45, 0.5, 0.5),
        chunk([0.5, 0.5, 0.5], [0.45, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN", reasons(result)
    assert any(reason.startswith("recovery_required:keep_out:0:excursion") for reason in reasons(result)), reasons(result)


def test_a_second_keep_out_is_not_licence_to_dig_into_it_while_leaving_the_first():
    """Leaving one hazard is not permission to deepen in another."""
    overlapping = [0.5, 0.3, 0.0, 0.9, 0.7, 1.0]
    g = gate(keep_out_aabbs=[OBSTACLE, overlapping])
    result = g.evaluate_step(
        T0, T0, state(0.55, 0.5, 0.5),
        chunk([0.58, 0.5, 0.5], [0.65, 0.35, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN", reasons(result)
    assert any(reason.startswith("recovery_required:keep_out:1:excursion") for reason in reasons(result)), reasons(result)


def test_a_clean_exit_from_a_keep_out_is_still_allowed():
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.5, 0.5, 0.5), chunk([0.25, 0.5, 0.5]), wrench=WRENCH)
    assert result.verdict == "COMPLIANT", reasons(result)
    assert result.margins["keep_out_recovery_0_m"] == pytest.approx(0.1)


def test_a_hold_inside_a_keep_out_is_still_allowed():
    """A refusal here would be a trap: the arm cannot hold while it waits for a plan."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.5, 0.5, 0.5), chunk([0.5, 0.5, 0.5]), wrench=WRENCH)
    assert result.verdict == "COMPLIANT", reasons(result)


def test_a_turn_that_deepens_towards_the_exit_is_still_allowed():
    """The excursion is bounded by the clearance the chunk gains: 0.05 m in, 0.15 m out."""
    g = gate(keep_out_shapes=[{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}],
             keep_out_aabbs=[])
    result = g.evaluate_step(
        T0, T0, state(0.5, 0.55, 0.5),
        chunk([0.5, 0.5, 0.5], [0.5, 0.3, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == "COMPLIANT", reasons(result)


def test_recovery_margins_carry_the_excursion_beside_the_gain():
    g = gate(keep_out_shapes=[{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}],
             keep_out_aabbs=[])
    result = g.evaluate_step(
        T0, T0, state(0.5, 0.55, 0.5),
        chunk([0.5, 0.5, 0.5], [0.5, 0.3, 0.5]),
        wrench=WRENCH,
    )
    assert result.margins["keep_out_recovery_shape_0_m"] == pytest.approx(0.15, abs=1e-6)
    assert result.margins["keep_out_recovery_shape_0_excursion_m"] == pytest.approx(0.05, abs=1e-6)


def test_a_contact_that_leaves_a_box_face_is_treated_as_leaving():
    """A start pose exactly on a face reads as depth 0, which used to latch an E_STOP."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(
        T0, T0, state(0.4, 0.5, 0.5), chunk([0.32, 0.5, 0.5]), wrench=WRENCH
    )
    assert result.verdict == "COMPLIANT", reasons(result)
    assert result.state == ARMED


def test_a_contact_that_leaves_a_circle_surface_is_treated_as_leaving():
    g = gate(keep_out_shapes=[{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}],
             keep_out_aabbs=[])
    result = g.evaluate_step(
        T0, T0, state(0.5, 0.7, 0.5), chunk([0.5, 0.76, 0.5]), wrench=WRENCH
    )
    assert result.verdict == "COMPLIANT", reasons(result)
    assert result.state == ARMED


def test_a_surface_start_that_pushes_in_is_still_refused():
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(
        T0, T0, state(0.4, 0.5, 0.5), chunk([0.45, 0.5, 0.5]), wrench=WRENCH
    )
    assert result.verdict == "FORBIDDEN", reasons(result)


# ---------------------------------------------------------------------------
# fuzz: orientation, and inputs that used to raise out of the gate
# ---------------------------------------------------------------------------

def _rotated(angle, z=0.5):
    """A setpoint at z rotated by `angle` about x, as a unit (w, x, y, z)."""
    return [math.cos(angle / 2.0), math.sin(angle / 2.0), 0.0, 0.0]


def test_a_quaternionless_element_does_not_disable_the_rotation_bound():
    """The bound used to be lost for every element after the first bare one."""
    g = gate(kinematic_limits={"max_pos_delta_m": 0.25, "max_rot_delta_rad": 0.05})
    elements = [
        {"ee_pos": [0.55, 0.5, 0.5], "duration_ms": 20.0},
        {"ee_pos": [0.6, 0.5, 0.5], "ee_quat": _rotated(math.pi / 2.0), "duration_ms": 20.0},
    ]
    result = g.evaluate_step(T0, T0, state(), elements, wrench=WRENCH)
    assert any("kinematic:rot_delta:1" in reason for reason in reasons(result)), reasons(result)


def test_a_scaled_robot_quaternion_is_normalised_not_ignored():
    """`|q| = 2` measured a 90° setpoint as 0 rad; normalised, it is 90°."""
    g = gate(kinematic_limits={"max_pos_delta_m": 0.25, "max_rot_delta_rad": 0.05})
    elements = [{"ee_pos": [0.55, 0.5, 0.5], "ee_quat": _rotated(math.pi / 2.0), "duration_ms": 20.0}]
    result = g.evaluate_step(
        T0, T0,
        {"ee_pos": [0.5, 0.5, 0.5], "ee_quat": [2.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]},
        elements, wrench=WRENCH,
    )
    assert any("kinematic:rot_delta:0" in reason for reason in reasons(result)), reasons(result)


@pytest.mark.parametrize("bad_quat", [
    [0.0, 0.0, 0.0, 0.0],
    [float("nan"), 0.0, 0.0, 1.0],
    [0.0, 0.0, 1.0],
    "0100",
])
def test_a_degenerate_or_non_finite_robot_quaternion_is_refused(bad_quat):
    result = gate(kinematic_limits={"max_pos_delta_m": 0.25, "max_rot_delta_rad": 0.05}).evaluate_step(
        T0, T0,
        {"ee_pos": [0.5, 0.5, 0.5], "ee_quat": bad_quat, "joint_pos": [0.0, 0.0]},
        chunk([0.55, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN"
    assert "structural:robot_state_invalid_ee_quat" in reasons(result), reasons(result)


def test_a_non_mapping_wrench_is_refused_not_raised():
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench="12N")
    assert result.verdict == "FORBIDDEN"
    assert "wrench:wrench_unverifiable:not_an_object" in reasons(result), reasons(result)


def test_a_non_sequence_chunk_is_refused_not_raised():
    result = gate().evaluate_step(T0, T0, state(), 5, wrench=WRENCH)
    assert result.verdict == "FORBIDDEN"
    assert "structural:action_chunk_not_a_sequence" in reasons(result), reasons(result)


# ---------------------------------------------------------------------------
# F7 — the replanned stop is swept before it becomes a payload
# ---------------------------------------------------------------------------

F7_SECTION = {
    "workspace_aabb": [0.0, 0.0, 0.0, 10.0, 1.0, 1.0],
    "keep_out_aabbs": [[2.0, 0.0, 0.0, 3.0, 1.0, 1.0]],
    "kinematic_limits": {"max_pos_delta_m": 0.1},
    "clamp_strategy": "replan",
    "replan_limits": {"max_acceleration_m_s2": 2.0, "max_jerk_m_s3": 100.0},
}


def test_a_stop_profile_that_drives_through_a_hazard_is_dropped():
    """F7's reproduction: 12 of 127 waypoints sat inside the keep-out box."""
    g = gate(**F7_SECTION)
    result = g.evaluate_step(
        T0, T0, state(0.0, 0.5, 0.5),
        chunk([0.1, 0.5, 0.5], [0.2, 0.5, 0.5], [0.35, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == CLAMPED
    assert result.safe_action["mode"] == "clamped_chunk"
    assert "stop_profile" not in result.safe_action
    assert result.replanned_path == []
    assert result.safe_action["replan_refused"].startswith("replanned_stop:unsafe"), result.safe_action
    assert len(result.safe_action["actions"]) == 2


def test_every_waypoint_of_an_accepted_stop_profile_is_clear():
    g = gate(**{**F7_SECTION, "keep_out_aabbs": []})
    result = g.evaluate_step(
        T0, T0, state(0.0, 0.5, 0.5),
        chunk([0.1, 0.5, 0.5], [0.2, 0.5, 0.5], [0.35, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.safe_action["mode"] == "replanned_stop"
    assert result.replanned_path
    for waypoint in result.replanned_path:
        assert 0.0 <= waypoint["ee_pos"][0] <= 10.0


def test_a_stop_profile_that_leaves_the_workspace_is_dropped():
    g = gate(**{**F7_SECTION, "workspace_aabb": BOX, "keep_out_aabbs": []})
    result = g.evaluate_step(
        T0, T0, state(0.2, 0.5, 0.5),
        chunk([0.28, 0.5, 0.5], [0.36, 0.5, 0.5], [0.62, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.safe_action["mode"] == "clamped_chunk"
    assert result.safe_action["replan_refused"].startswith("replanned_stop:unsafe:workspace_keep_in")
    assert result.replanned_path == []


def test_a_refused_chunk_never_carries_a_motion_profile():
    """A refusal has no legal prefix to stop from, so it gets the hold it was given."""
    g = gate(clamp_strategy="replan", replan_limits={"max_acceleration_m_s2": 4.0, "max_jerk_m_s3": 40.0},
             max_action_age_ms=50.0)
    result = g.evaluate_step(
        T0, T0 + 500 * MS, state(),
        chunk([0.55, 0.5, 0.5], [0.6, 0.5, 0.5]),
        wrench=WRENCH,
    )
    assert result.verdict == "FORBIDDEN"
    assert result.safe_action["mode"] == "safe_mechanical_hold"
    assert "stop_profile" not in result.safe_action


# ---------------------------------------------------------------------------
# F6 — a gate is not built from an unverified manifest without an opt-out
# ---------------------------------------------------------------------------

BOUNDS_FREE = {
    "node": "robot:test",
    "physical_vla": {"max_action_age_ms": 150.0, "watchdog_timeout_ms": 500.0},
}


def test_a_manifest_with_no_verifier_is_refused_by_the_gate():
    with pytest.raises(VLAManifestError, match="unverified_manifest"):
        LokahiVLAGate.from_manifest(manifest())


def test_the_audited_bounds_free_manifest_cannot_build_a_gate():
    """F6's reproduction: 70 m outside the workspace was COMPLIANT."""
    with pytest.raises(VLAManifestError, match="unverified_manifest"):
        LokahiVLAGate.from_manifest(BOUNDS_FREE)


def test_an_explicit_opt_out_still_builds_and_checks():
    g = LokahiVLAGate.from_manifest(manifest(), allow_unverified_manifest=True)
    result = g.evaluate_step(
        T0, T0, state(), chunk([1.4, 0.5, 0.5], duration_ms=5.0), wrench=WRENCH
    )
    assert result.verdict == "FORBIDDEN"
    assert any("workspace_keep_in" in reason for reason in reasons(result)), reasons(result)


def test_the_loader_stays_usable_without_a_verifier():
    config = load_physical_vla(manifest())
    assert config.workspace_aabb == (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    assert config.manifest_verified is False


def test_a_verified_manifest_records_its_verification():
    config = load_physical_vla(manifest(), verifier=verified)
    assert config.manifest_verified is True


def test_a_rejecting_verifier_is_still_fatal():
    with pytest.raises(VLAManifestError, match="unverifiable_manifest"):
        load_physical_vla(manifest(), verifier=lambda _document: False)
