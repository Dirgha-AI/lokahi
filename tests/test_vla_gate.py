"""Tests for the physical_vla manifest section and the gate.

The gate is the only thing between a learned policy and an actuator, so these
tests assert exactly that. Every check here fails before the code was written.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import physical_manifest, vla_gate
from vla.physical_manifest import PhysicalVLAConfig, VLAManifestError, load_physical_vla
from vla.vla_gate import (
    ARMED,
    CLAMPED,
    E_STOP,
    HUMAN_RESET,
    SOFT_STOP,
    LokahiVLAGate,
    VLADecisionResult,
    segment_hits_box,
    segment_hits_circle,
    segment_hits_cylinder,
)

BOX = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
OBSTACLE = [0.4, 0.4, 0.0, 0.6, 0.6, 1.0]
T0 = 1_000_000_000_000
MS = 1_000_000


def manifest(**overrides):
    section = {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        "control_period_ms": 20.0,
        # A generous gate budget by default: these tests are about the checks,
        # not about this machine's scheduling noise. The budget check gets its
        # own test with a deliberately impossible deadline.
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


def gate(**overrides):
    return LokahiVLAGate.from_manifest(manifest(**overrides))


def state(x=0.5, y=0.5, z=0.5):
    return {"ee_pos": [x, y, z], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}


def chunk(*points, duration_ms=20.0):
    return [
        {"ee_pos": list(point), "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": duration_ms}
        for point in points
    ]


# ---------------------------------------------------------------------------
# manifest validation
# ---------------------------------------------------------------------------

def test_valid_section_loads():
    config = load_physical_vla(manifest())
    assert isinstance(config, PhysicalVLAConfig)
    assert config.max_force_n == 10.0
    assert config.max_torque_nm == 45.0
    assert config.policy_for("workspace_keep_out") == "E_STOP"


def test_missing_section_is_fatal():
    document = manifest()
    del document["physical_vla"]
    with pytest.raises(VLAManifestError, match="no physical_vla"):
        load_physical_vla(document)


def test_unverifiable_manifest_is_fatal():
    with pytest.raises(VLAManifestError, match="unverifiable_manifest"):
        load_physical_vla(manifest(), verifier=lambda _doc: False)


def test_restating_an_envelope_limit_is_refused():
    with pytest.raises(VLAManifestError, match="may not restate envelope limits"):
        load_physical_vla(manifest(max_force_n=10.0))


def test_unknown_key_is_refused():
    with pytest.raises(VLAManifestError, match="unknown keys"):
        load_physical_vla(manifest(max_speed_mps=1.0))


@pytest.mark.parametrize("bad", [0, -1.0, "150", float("inf"), None])
def test_non_positive_timeout_is_refused(bad):
    with pytest.raises(VLAManifestError):
        load_physical_vla(manifest(max_action_age_ms=bad))


@pytest.mark.parametrize(
    "box",
    [[0, 0, 0, 1, 1], [1, 0, 0, 0, 1, 1], [0, 0, 0, float("nan"), 1, 1], "000111"],
)
def test_bad_aabb_is_refused(box):
    with pytest.raises(VLAManifestError):
        load_physical_vla(manifest(workspace_aabb=box))


def test_unknown_policy_action_is_refused():
    with pytest.raises(VLAManifestError, match="policy_on_violation"):
        load_physical_vla(manifest(policy_on_violation={"kinematic": "IGNORE"}))


def test_unknown_execution_mode_is_refused():
    with pytest.raises(VLAManifestError, match="execution_mode"):
        load_physical_vla(manifest(execution_mode="fire_and_forget"))


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def test_slab_intersection_detects_a_crossing_segment():
    assert segment_hits_box([0.1, 0.5, 0.5], [0.9, 0.5, 0.5], OBSTACLE)


def test_slab_misses_a_segment_that_stops_short():
    assert not segment_hits_box([0.0, 0.0, 0.5], [0.3, 0.3, 0.5], OBSTACLE)


def test_slab_handles_a_segment_parallel_to_an_axis():
    assert not segment_hits_box([0.1, 0.9, 0.5], [0.9, 0.9, 0.5], OBSTACLE)
    assert segment_hits_box([0.1, 0.5, 0.5], [0.9, 0.5, 0.5], [0.4, 0.4, 0.0, 0.6, 0.6, 1.0])


# ---------------------------------------------------------------------------
# gate: the happy path
# ---------------------------------------------------------------------------

def test_a_short_in_bounds_chunk_is_compliant():
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5], [0.6, 0.5, 0.5]))
    assert result.verdict == "COMPLIANT"
    assert result.allowed is True
    assert result.state == ARMED
    assert result.violated_invariants == []
    assert result.interlock == "pass"
    assert result.gate_eval_us > 0.0


def test_result_maps_to_a_lokahi_decision_for_the_ledger():
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]))
    decision = result.to_decision()
    assert decision.verdict == "compliant"


def test_gate_is_deterministic_for_identical_input():
    first = gate()
    second = gate()
    args = (T0, T0 + 10 * MS, state(), chunk([0.55, 0.5, 0.5], [0.6, 0.5, 0.5]))
    a = first.evaluate_step(*args)
    b = second.evaluate_step(*args)
    assert (a.verdict, a.state, a.violated_invariants) == (b.verdict, b.state, b.violated_invariants)


# ---------------------------------------------------------------------------
# gate: geometry
# ---------------------------------------------------------------------------

def test_keep_out_penetration_is_caught_even_when_both_endpoints_are_legal():
    """The swept test is the whole reason the gate exists; endpoints alone miss it."""
    penetrate = chunk([0.1, 0.5, 0.5], [0.9, 0.5, 0.5])
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.05, 0.5, 0.5), penetrate)
    assert result.verdict == "FORBIDDEN"
    assert result.state == E_STOP
    assert any("workspace_keep_out" in reason for reason in result.violated_invariants)
    assert result.interlock == "normal_retract"


def test_keep_in_violation_is_caught_at_the_endpoint():
    g = gate()
    result = g.evaluate_step(T0, T0, state(0.5, 0.5, 0.5), chunk([1.4, 0.5, 0.5], duration_ms=5.0))
    assert result.verdict == "FORBIDDEN"
    assert result.state == E_STOP


def test_tool_radius_shrinks_keep_in_and_grows_keep_out():
    """A 0.1 m tool means the centre may only reach 0.9, not 1.0."""
    g = gate(tool_radius_m=0.1)
    assert g.config.tool_radius_m == 0.1
    legal = g.evaluate_step(T0, T0, state(0.8, 0.5, 0.5), chunk([0.85, 0.5, 0.5]))
    assert legal.verdict == "COMPLIANT", legal.violated_invariants
    refused = g.evaluate_step(T0, T0, state(0.85, 0.5, 0.5), chunk([0.95, 0.5, 0.5]))
    assert refused.verdict == "FORBIDDEN"
    assert any("workspace_keep_in" in reason for reason in refused.violated_invariants)


# ---------------------------------------------------------------------------
# gate: time
# ---------------------------------------------------------------------------

def test_stale_chunk_soft_stops_by_default():
    result = gate().evaluate_step(T0, T0 + 900 * MS, state(), chunk([0.55, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert result.state == SOFT_STOP
    assert result.safe_action["mode"] == "safe_mechanical_hold"
    assert any("stale_action" in reason for reason in result.violated_invariants)


def test_late_elements_are_stale_before_early_ones():
    """Age is per element: the tail of a long chunk goes stale first."""
    g = gate(max_action_age_ms=60.0, policy_on_violation={"stale_action": "CLAMP"})
    result = g.evaluate_step(
        T0,
        T0 + 10 * MS,
        state(),
        chunk([0.52, 0.5, 0.5], [0.55, 0.5, 0.5], [0.58, 0.5, 0.5], duration_ms=30.0),
    )
    assert result.state == CLAMPED
    assert result.verdict == "CLAMPED"
    assert result.clamped_from_index == 2
    assert len(result.safe_action["actions"]) == 2


def test_a_stale_first_element_cannot_be_clamped_to_nothing():
    g = gate(max_action_age_ms=5.0, policy_on_violation={"stale_action": "CLAMP"})
    result = g.evaluate_step(T0, T0 + 100 * MS, state(), chunk([0.55, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert result.state == SOFT_STOP


def test_watchdog_gap_soft_stops():
    g = gate()
    g.evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]))
    result = g.evaluate_step(T0 + 900 * MS, T0 + 900 * MS, state(), chunk([0.56, 0.5, 0.5]))
    assert result.state == SOFT_STOP
    assert any("watchdog" in reason for reason in result.violated_invariants)


def test_backwards_clock_is_refused():
    g = gate()
    g.evaluate_step(T0, T0 + 100 * MS, state(), chunk([0.55, 0.5, 0.5]))
    result = g.evaluate_step(T0, T0 + 50 * MS, state(), chunk([0.56, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert any("clock_skew" in reason for reason in result.violated_invariants)


def test_observation_from_the_future_is_refused():
    result = gate().evaluate_step(T0 + 50 * MS, T0, state(), chunk([0.55, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert any("clock_skew" in reason for reason in result.violated_invariants)


# ---------------------------------------------------------------------------
# gate: structure and wrench
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_point",
    [[float("nan"), 0.5, 0.5], [float("inf"), 0.5, 0.5], [0.5, 0.5]],
)
def test_non_finite_or_misshaped_position_is_refused(bad_point):
    result = gate().evaluate_step(T0, T0, state(), [{"ee_pos": bad_point}])
    assert result.verdict == "FORBIDDEN"
    assert any("structural" in reason for reason in result.violated_invariants)


def test_non_unit_quaternion_is_refused():
    bad = [{"ee_pos": [0.55, 0.5, 0.5], "ee_quat": [2.0, 0.0, 0.0, 0.0]}]
    result = gate().evaluate_step(T0, T0, state(), bad)
    assert result.verdict == "FORBIDDEN"
    assert any("not unit" in reason for reason in result.violated_invariants)


def test_empty_chunk_is_refused():
    result = gate().evaluate_step(T0, T0, state(), [])
    assert result.verdict == "FORBIDDEN"


def test_force_overload_soft_stops_and_reports_a_margin():
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench={"force_n": 12.5})
    assert result.verdict == "FORBIDDEN"
    assert result.state == SOFT_STOP
    assert result.margins["force_margin_n"] == pytest.approx(-2.5)
    assert any("wrench:force" in reason for reason in result.violated_invariants)


def test_three_axis_wrench_uses_the_l2_norm():
    result = gate().evaluate_step(
        T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench={"force_n": [8.0, 8.0, 0.0]}
    )
    assert any("wrench:force" in reason for reason in result.violated_invariants)


def test_force_within_envelope_passes():
    result = gate().evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]), wrench={"force_n": 9.0})
    assert result.verdict == "COMPLIANT"


def test_kinematic_delta_clamps_by_default():
    g = gate(kinematic_limits={"max_pos_delta_m": 0.05})
    result = g.evaluate_step(T0, T0, state(), chunk([0.9, 0.5, 0.5]))
    assert result.verdict in ("FORBIDDEN", "CLAMPED")
    assert any("kinematic:pos_delta" in reason for reason in result.violated_invariants)


# ---------------------------------------------------------------------------
# gate: the latch
# ---------------------------------------------------------------------------

def test_e_stop_is_absorbing_until_a_human_resets_it():
    g = gate(keep_out_aabbs=[OBSTACLE])
    first = g.evaluate_step(T0, T0, state(0.05, 0.5, 0.5), chunk([0.9, 0.5, 0.5], duration_ms=5.0))
    assert first.state == E_STOP

    second = g.evaluate_step(T0 + 20 * MS, T0 + 20 * MS, state(0.05, 0.5, 0.5), chunk([0.1, 0.5, 0.5]))
    assert second.verdict == "FORBIDDEN"
    assert second.state == E_STOP
    assert any("e_stop_latched" in reason for reason in second.violated_invariants)

    with pytest.raises(ValueError):
        g.human_reset("")

    entry = g.human_reset("operator:salik", T0 + 40 * MS)
    assert entry["kind"] == "vla_human_reset"
    assert entry["operator"] == "operator:salik"

    third = g.evaluate_step(T0 + 60 * MS, T0 + 60 * MS, state(0.05, 0.5, 0.5), chunk([0.1, 0.5, 0.5]))
    assert third.verdict == "COMPLIANT"
    assert third.state == ARMED


# ---------------------------------------------------------------------------
# gate: its own budget
# ---------------------------------------------------------------------------

def test_overrunning_the_gate_budget_refuses_instead_of_reporting():
    g = gate(max_gate_eval_us=0.0001)
    result = g.evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert any("budget_exceeded" in reason for reason in result.violated_invariants)


def test_counters_track_steps_and_interventions():
    g = gate()
    g.evaluate_step(T0, T0, state(), chunk([0.55, 0.5, 0.5]))
    g.evaluate_step(T0 + 20 * MS, T0 + 20 * MS, state(), [])
    assert g.steps_evaluated == 2
    assert g.interventions == 1


# ---------------------------------------------------------------------------
# fail-closed on an unusable robot_state
# ---------------------------------------------------------------------------

def test_missing_ee_pos_refuses_instead_of_skipping_geometry():
    """Without a pose the geometry cannot run, so the gate must not report COMPLIANT."""
    g = gate(workspace_aabb=BOX)
    result = g.evaluate_step(T0, T0, {"joint_pos": [0.0, 0.0]}, chunk([5.0, 5.0, 5.0]))
    assert result.verdict == "FORBIDDEN"
    assert any("robot_state_missing_ee_pos" in reason for reason in result.violated_invariants)


def test_unusable_ee_pos_refuses():
    g = gate()
    result = g.evaluate_step(T0, T0, {"ee_pos": [float("nan"), 0.0, 0.0]}, chunk([0.55, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"


# ---------------------------------------------------------------------------
# joint velocity and acceleration limits are enforced, not just stored
# ---------------------------------------------------------------------------

def _joint_chunk(*positions, duration_ms=20.0):
    return [
        {"ee_pos": [0.5, 0.5, 0.5], "joint_pos": list(position), "duration_ms": duration_ms}
        for position in positions
    ]


def test_joint_velocity_limit_is_enforced():
    g = gate(kinematic_limits={"max_joint_vel_rad_s": 1.0}, policy_on_violation={"kinematic": "CLAMP"})
    result = g.evaluate_step(
        T0, T0, {"ee_pos": [0.5, 0.5, 0.5], "joint_pos": [0.0, 0.0]}, _joint_chunk([0.5, 0.0], duration_ms=20.0)
    )
    assert any("joint_vel" in reason for reason in result.violated_invariants)


def test_joint_velocity_within_limit_passes():
    g = gate(kinematic_limits={"max_joint_vel_rad_s": 50.0})
    result = g.evaluate_step(
        T0, T0, {"ee_pos": [0.5, 0.5, 0.5], "joint_pos": [0.0, 0.0]}, _joint_chunk([0.5, 0.0], duration_ms=20.0)
    )
    assert result.verdict == "COMPLIANT"


def test_joint_acceleration_limit_is_enforced():
    g = gate(kinematic_limits={"max_joint_acc_rad_s2": 5.0})
    result = g.evaluate_step(
        T0,
        T0,
        {"ee_pos": [0.5, 0.5, 0.5], "joint_pos": [0.0, 0.0]},
        _joint_chunk([0.2, 0.0], [0.6, 0.0], duration_ms=20.0),
    )
    assert any("joint_acc" in reason for reason in result.violated_invariants)


def test_non_positive_duration_is_refused():
    g = gate(kinematic_limits={"max_joint_vel_rad_s": 1.0})
    result = g.evaluate_step(
        T0, T0, {"ee_pos": [0.5, 0.5, 0.5], "joint_pos": [0.0, 0.0]}, _joint_chunk([0.1, 0.0], duration_ms=0.0)
    )
    assert any("non_positive_duration" in reason for reason in result.violated_invariants)


# ---------------------------------------------------------------------------
# the manifest signs its own VLA bounds
# ---------------------------------------------------------------------------

def test_manifest_signature_covers_physical_vla():
    from lokahi import Manifest, Signer

    key = b"vla-manifest-signing-key"
    signed = Manifest(
        node="robot:sign-test",
        informational=["joint_positions"],
        forbidden_targets=[],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 2, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:dirgha.example/sign",
        physical_vla=dict(manifest()["physical_vla"]),
    ).sign(Signer(key))

    assert "physical_vla" in signed.to_dict()
    assert signed.verify(Signer(key))

    tampered = Manifest.from_dict(dict(signed.to_dict()))
    tampered.physical_vla["max_action_age_ms"] = 100000.0
    assert not tampered.verify(Signer(key))


# ---------------------------------------------------------------------------
# recovery: a gate that traps the robot inside a violated region is worse
# than no gate
# ---------------------------------------------------------------------------

def test_escape_from_a_keep_out_box_is_allowed():
    """Already inside a hazard, moving out must not be refused."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.5, 0.5, 0.5), chunk([0.25, 0.5, 0.5]))
    assert result.verdict == "COMPLIANT", result.violated_invariants
    assert result.margins["keep_out_recovery_0_m"] > 0.0


def test_moving_deeper_into_a_keep_out_box_is_refused():
    """Going further in is not recovery. Depth rising means the arm is going the wrong way."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.42, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert any("recovery_required:keep_out" in reason for reason in result.violated_invariants)


def test_a_recovery_refusal_does_not_latch_so_the_next_step_can_fix_it():
    """SOFT_STOP, not E_STOP: latching here would hold the arm inside the hazard."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    refused = g.evaluate_step(T0, T0, state(0.42, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert refused.state == SOFT_STOP

    escaped = g.evaluate_step(T0 + 20 * MS, T0 + 20 * MS, state(0.42, 0.5, 0.5), chunk([0.25, 0.5, 0.5]))
    assert escaped.verdict == "COMPLIANT", escaped.violated_invariants


def test_keep_in_recovery_towards_the_workspace_is_allowed():
    g = gate()
    result = g.evaluate_step(T0, T0, state(1.2, 0.5, 0.5), chunk([1.1, 0.5, 0.5]))
    assert result.verdict == "COMPLIANT", result.violated_invariants


def test_keep_in_recovery_away_from_the_workspace_is_refused():
    g = gate()
    result = g.evaluate_step(T0, T0, state(1.2, 0.5, 0.5), chunk([1.4, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert any("recovery_required:keep_in" in reason for reason in result.violated_invariants)


def test_a_clean_start_still_refuses_entering_a_keep_out_box():
    """Recovery must not become a general licence to enter."""
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.2, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert result.state == E_STOP
    assert any("workspace_keep_out" in reason for reason in result.violated_invariants)


# --- shaped keep-outs -------------------------------------------------------
#
# A box circumscribing a disc is up to 27% too large on its diagonal. Against a
# simulator that charges on the disc, that shows up as refused poses the
# environment would have allowed, so the manifest can name the real shape.

def circle_manifest(**overrides):
    section = {"keep_out_aabbs": [],
               "keep_out_shapes": [{"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2}],
               "tool_radius_m": 0.0}
    section.update(overrides)
    return manifest(**section)


@pytest.mark.parametrize("shape,expected", [
    ({"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2}, "circle"),
    ({"shape": "cylinder", "centre": [0.5, 0.5, 0.0], "radius": 0.2, "height": 1.0}, "cylinder"),
    ({"shape": "sphere", "centre": [0.5, 0.5, 0.5], "radius": 0.2}, "sphere"),
    ({"shape": "capsule", "start": [0.2, 0.5, 0.5], "end": [0.8, 0.5, 0.5], "radius": 0.1}, "capsule"),
])
def test_shaped_keep_out_loads(shape, expected):
    config = load_physical_vla(manifest(keep_out_shapes=[shape]))
    stored = config.keep_out_shapes[0]
    assert stored["kind"] == expected
    assert stored["radius"] == pytest.approx(shape["radius"])


def test_a_mesh_keep_out_loads_and_is_refused_without_the_extra():
    mesh = {"shape": "mesh",
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "triangles": [[0, 1, 2]]}
    config = load_physical_vla(manifest(keep_out_shapes=[mesh]))
    stored = config.keep_out_shapes[0]
    assert stored["kind"] == "mesh"
    assert stored["triangles"] == ((0, 1, 2),)


@pytest.mark.parametrize("shape", [
    {"shape": "torus", "centre": [0.5, 0.5], "radius": 0.2},
    {"shape": "circle", "centre": [0.5, 0.5, 0.5], "radius": 0.2},
    {"shape": "circle", "centre": [0.5], "radius": 0.2},
    {"shape": "circle", "centre": [0.5, 0.5], "radius": 0.0},
    {"shape": "cylinder", "centre": [0.5, 0.5, 0.0], "radius": 0.2},
    {"shape": "capsule", "start": [0.0, 0.0, 0.0], "radius": 0.1},
    {"shape": "mesh", "vertices": [[0, 0, 0]], "triangles": [[0, 1, 2]]},
    {"shape": "mesh", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": [[0, 1, 9]]},
    {"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2, "colour": "red"},
])
def test_a_malformed_shape_is_fatal(shape):
    with pytest.raises(VLAManifestError):
        load_physical_vla(manifest(keep_out_shapes=[shape]))


def test_circle_sweep_detects_a_crossing_segment():
    assert segment_hits_circle([0.1, 0.5], [0.9, 0.5], [0.5, 0.5], 0.2)


def test_circle_sweep_allows_a_segment_that_stops_short():
    assert not segment_hits_circle([0.1, 0.5], [0.25, 0.5], [0.5, 0.5], 0.2)


def test_circle_sweep_measures_from_the_closest_point_on_the_segment():
    # The segment's nearest approach is its endpoint, not the infinite line.
    assert not segment_hits_circle([0.5, 0.9], [0.9, 0.9], [0.5, 0.5], 0.2)


def test_a_cylinder_only_blocks_the_slab_it_spans():
    centre = [0.5, 0.5, 0.0]
    assert segment_hits_cylinder([0.1, 0.5, 0.5], [0.9, 0.5, 0.5], centre, 0.2, 1.0)
    assert not segment_hits_cylinder([0.1, 0.5, 2.0], [0.9, 0.5, 2.0], centre, 0.2, 1.0)


def test_a_chunk_entering_a_circle_is_refused():
    result = gate(keep_out_aabbs=[],
                  keep_out_shapes=[{"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2}])         .evaluate_step(T0, T0, state(0.9, 0.5, 0.5), chunk([0.85, 0.5, 0.5], [0.65, 0.5, 0.5]))
    assert result.state == E_STOP
    assert any("workspace_keep_out:shape_0" in v for v in result.violated_invariants)


def test_the_disc_corner_a_box_would_refuse_is_armed():
    """The regression the disc exists for: this pose is inside the bounding
    square and outside the disc, so the gate must not refuse it."""
    section = {"keep_out_aabbs": [], "tool_radius_m": 0.0,
               "keep_out_shapes": [{"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2}]}
    result = gate(**section).evaluate_step(
        T0, T0, state(0.5, 0.85, 0.5), chunk([0.5, 0.85, 0.5], [0.55, 0.86, 0.5]))
    assert result.state == ARMED


def test_recovery_out_of_a_circle_is_allowed():
    section = {"keep_out_aabbs": [], "tool_radius_m": 0.0,
               "keep_out_shapes": [{"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2}]}
    result = gate(**section).evaluate_step(
        T0, T0, state(0.5, 0.55, 0.5), chunk([0.5, 0.55, 0.5], [0.5, 0.5, 0.5], [0.5, 0.3, 0.5]))
    assert result.state == ARMED


def test_a_chunk_that_digs_deeper_into_a_circle_is_a_recovery_violation():
    section = {"keep_out_aabbs": [], "tool_radius_m": 0.0,
               "keep_out_shapes": [{"shape": "circle", "centre": [0.5, 0.5], "radius": 0.2}]}
    result = gate(**section).evaluate_step(
        T0, T0, state(0.5, 0.68, 0.5), chunk([0.5, 0.68, 0.5], [0.5, 0.5, 0.5]))
    assert result.state == SOFT_STOP
    assert any("recovery_required:keep_out" in v for v in result.violated_invariants)


# --- clamp strategy: truncate or replan ------------------------------------

def replan_gate(**overrides):
    section = {"clamp_strategy": "replan",
               "replan_limits": {"max_acceleration_m_s2": 4.0, "max_jerk_m_s3": 40.0}}
    section.update(overrides)
    return gate(**section)


def test_replan_without_acceleration_limits_is_fatal():
    with pytest.raises(VLAManifestError, match="max_acceleration_m_s2"):
        load_physical_vla(manifest(clamp_strategy="replan"))


def test_an_unknown_clamp_strategy_is_fatal():
    with pytest.raises(VLAManifestError, match="clamp_strategy"):
        load_physical_vla(manifest(clamp_strategy="hope"))


def test_an_unknown_replan_limit_is_fatal():
    with pytest.raises(VLAManifestError, match="replan_limits"):
        load_physical_vla(manifest(clamp_strategy="replan",
                                   replan_limits={"max_acceleration_m_s2": 1.0, "speed": 3.0}))


def _kinematic_clamp(gate_object):
    # Third point exceeds max_pos_delta_m from the second, so two points stay legal.
    return gate_object.evaluate_step(
        T0, T0, state(0.5, 0.5, 0.5),
        chunk([0.55, 0.5, 0.5], [0.6, 0.5, 0.5], [0.95, 0.5, 0.5]))


def test_truncate_is_the_default_and_plans_nothing():
    result = _kinematic_clamp(gate())
    assert result.state == CLAMPED
    assert result.replanned_path == []
    assert result.safe_action["mode"] == "clamped_chunk"


def test_replan_returns_a_deceleration_that_ends_at_rest():
    result = _kinematic_clamp(replan_gate())
    assert result.state == CLAMPED
    assert result.safe_action["mode"] == "replanned_stop"
    assert len(result.replanned_path) > 2
    assert result.replanned_path[-1]["ee_vel"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    assert result.replanned_path[-1]["t_ms"] > result.replanned_path[0]["t_ms"]


def test_the_replanned_stop_starts_where_the_legal_prefix_ends():
    result = _kinematic_clamp(replan_gate())
    first = result.replanned_path[0]
    assert first["ee_pos"] == pytest.approx([0.6, 0.5, 0.5])
    assert first["t_ms"] == 0.0


def test_a_replan_that_cannot_finish_falls_back_to_the_prefix_and_says_so():
    tight = replan_gate(replan_limits={"max_acceleration_m_s2": 0.01, "max_jerk_m_s3": 0.01})
    result = _kinematic_clamp(tight)
    assert result.replanned_path == []
    assert result.safe_action["mode"] == "clamped_chunk"
    assert "replan_refused" in result.safe_action
