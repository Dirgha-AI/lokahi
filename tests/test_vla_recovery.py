"""Tests for escape planning: a refusal inside a keep-out must still offer a way out.

The freeze this file is about is subtle. Refusing an illegal one-step move is
right, but when the robot's own pose is already inside a keep-out, every one-step
move that would leave is refused and the robot stays in the hazard while the
outside world keeps charging it. These tests pin the second artefact: a path
that strictly increases clearance, stays in the workspace, and never softens the
verdict, the state or the interlock.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import geometry, recovery
from vla.physical_manifest import VLAManifestError, load_physical_vla
from vla.vla_gate import (
    ARMED,
    E_STOP,
    SOFT_STOP,
    LokahiVLAGate,
    _grow,
)

BOX = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
OBSTACLE = [0.4, 0.4, 0.0, 0.6, 0.6, 1.0]
CIRCLE = {"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}
T0 = 1_000_000_000_000
MS = 1_000_000


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


MEASURED_WRENCH = {"force_n": 1.0, "torque_nm": 1.0}


def verified(_document):
    """This file's manifests are its own fixture, so the verified path is the one under test."""
    return True


class _MeasuredGate(LokahiVLAGate):
    """The fixture's adapter measures the wrench the manifest's ceilings apply to.

    The gate refuses a configured force or torque ceiling it has no measurement
    for (`wrench:wrench_unverifiable`), so every step in a deployment with those
    ceilings carries a wrench. Saying that once here keeps each test about the
    check it names; tests that are about the wrench pass their own.
    """

    def evaluate_step(self, obs_ts_ns, now_ts_ns, robot_state, action_chunk,
                      wrench=MEASURED_WRENCH, **kwargs):
        return super().evaluate_step(
            obs_ts_ns, now_ts_ns, robot_state, action_chunk, wrench=wrench, **kwargs
        )


def gate(**overrides):
    return _MeasuredGate.from_manifest(manifest(**overrides), verifier=verified)


def state(x=0.5, y=0.5, z=0.5):
    return {"ee_pos": [x, y, z], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}


def chunk(*points, duration_ms=20.0):
    return [
        {"ee_pos": list(point), "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": duration_ms}
        for point in points
    ]


def clearances(path, boxes=(), shapes=()):
    return [recovery.minimum_clearance(pose, list(boxes), list(shapes)) for pose in path]


# ---------------------------------------------------------------------------
# clearance, the measure the escape rule is written in
# ---------------------------------------------------------------------------

def test_clearance_is_negative_inside_and_positive_outside():
    assert recovery.minimum_clearance([0.5, 0.5, 0.5], [OBSTACLE]) == pytest.approx(-0.1)
    assert recovery.minimum_clearance([0.3, 0.5, 0.5], [OBSTACLE]) == pytest.approx(0.1)
    assert recovery.minimum_clearance([0.3, 0.3, 0.5], [OBSTACLE]) == pytest.approx(0.1 * 2 ** 0.5)
    assert recovery.minimum_clearance([0.5, 0.5, 0.5], [], [CIRCLE]) == pytest.approx(-0.2)
    assert recovery.minimum_clearance([0.8, 0.5, 0.5], [], [CIRCLE]) == pytest.approx(0.1)


def test_no_keep_out_means_infinite_clearance():
    assert recovery.minimum_clearance([0.5, 0.5, 0.5]) == float("inf")


# ---------------------------------------------------------------------------
# the escape path itself
# ---------------------------------------------------------------------------

def test_each_step_strictly_increases_the_minimum_clearance():
    path = recovery.escape_path([0.42, 0.5, 0.5], [OBSTACLE], [], BOX, 0.02, 50)
    assert path, "an escape from inside a keep-out must be found"
    values = [-0.02] + clearances(path, [OBSTACLE])
    for earlier, later in zip(values, values[1:]):
        assert later > earlier + 1e-9, values


def test_a_shaped_keep_out_is_escaped_by_increasing_radial_clearance():
    path = recovery.escape_path([0.35, 0.5, 0.5], [], [CIRCLE], BOX, 0.02, 50)
    assert path
    values = [-0.15] + clearances(path, shapes=[CIRCLE])
    for earlier, later in zip(values, values[1:]):
        assert later > earlier + 1e-9, values
    assert path[-1][0] < 0.35, "the disc centre is at x=0.5, so the way out is -x"


def test_the_path_never_leaves_the_workspace():
    hugging_edge = [0.0, 0.0, 0.0, 0.2, 1.0, 1.0]
    path = recovery.escape_path([0.1, 0.5, 0.5], [hugging_edge], [], BOX, 0.02, 50)
    assert path
    for pose in path:
        assert all(BOX[axis] <= pose[axis] <= BOX[axis + 3] for axis in range(3)), pose


def test_a_clear_start_gets_an_empty_path():
    assert recovery.escape_path([0.2, 0.5, 0.5], [OBSTACLE], [], BOX, 0.02, 50) == []


def test_a_pose_with_no_improving_direction_gets_an_empty_path():
    """Cornered: the keep-out runs past the workspace, so every improving step is out."""
    overshoot = [0.0, 0.0, 0.0, 1.4, 1.4, 1.4]
    assert recovery.escape_path([1.0, 1.0, 1.0], [overshoot], [], BOX, 0.02, 50) == []


def test_planning_is_deterministic():
    first = recovery.escape_path([0.42, 0.5, 0.5], [OBSTACLE], [], BOX, 0.02, 50)
    second = recovery.escape_path([0.42, 0.5, 0.5], [OBSTACLE], [], BOX, 0.02, 50)
    assert first == second
    assert all(isinstance(pose, list) and len(pose) == 3 for pose in first)


def test_max_steps_bounds_the_path():
    path = recovery.escape_path([0.42, 0.5, 0.5], [OBSTACLE], [], BOX, 0.02, 3)
    assert len(path) == 3


def test_a_planar_keep_out_is_escaped_in_the_plane():
    path = recovery.escape_path([0.35, 0.5, 0.5], [], [CIRCLE], BOX, 0.02, 50)
    assert path
    assert {pose[2] for pose in path} == {0.5}


def test_a_step_that_tunnels_through_a_thin_keep_out_is_not_proposed():
    """The landing pose can be clear only by crossing the wall that is in the way."""
    enclosure = [0.0, 0.0, 0.0, 0.2, 1.0, 1.0]
    wall = [0.22, 0.0, 0.0, 0.26, 1.0, 1.0]
    start = [0.19, 0.5, 0.5]
    assert recovery.escape_path(start, [enclosure], [], BOX, 0.12, 50)[0][0] > 0.26
    assert recovery.escape_path(start, [enclosure, wall], [], BOX, 0.12, 50) == []


@pytest.mark.parametrize("bad_step", [0.0, -0.02, float("nan"), float("inf")])
def test_a_non_positive_step_is_refused(bad_step):
    with pytest.raises(recovery.RecoveryError):
        recovery.escape_path([0.42, 0.5, 0.5], [OBSTACLE], [], BOX, bad_step, 50)


def test_a_negative_max_steps_is_refused():
    with pytest.raises(recovery.RecoveryError):
        recovery.escape_path([0.42, 0.5, 0.5], [OBSTACLE], [], BOX, 0.02, -1)


def test_a_mesh_keep_out_is_refused_rather_than_guessed():
    mesh = {"kind": "mesh", "radius": 0.1}
    with pytest.raises(recovery.RecoveryError, match="mesh"):
        recovery.escape_path([0.5, 0.5, 0.5], [], [mesh], BOX, 0.02, 50)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def test_recovery_keys_load_with_safe_defaults():
    config = load_physical_vla(manifest())
    assert config.recovery_planning is False
    assert config.recovery_step_m == pytest.approx(0.02)
    assert config.recovery_max_steps == 50

    tuned = load_physical_vla(manifest(recovery_planning=True, recovery_step_m=0.01,
                                       recovery_max_steps=12))
    assert tuned.recovery_planning is True
    assert tuned.recovery_step_m == pytest.approx(0.01)
    assert tuned.recovery_max_steps == 12


@pytest.mark.parametrize("overrides", [
    {"recovery_planning": "yes"},
    {"recovery_planning": 1},
    {"recovery_step_m": 0.0},
    {"recovery_step_m": -0.01},
    {"recovery_max_steps": 0},
    {"recovery_max_steps": -5},
    {"recovery_max_steps": 2.5},
    {"recovery_stepsize_m": 0.02},
])
def test_bad_recovery_settings_are_refused(overrides):
    with pytest.raises(VLAManifestError):
        load_physical_vla(manifest(**overrides))


# ---------------------------------------------------------------------------
# the gate: same refusal, plus a way out
# ---------------------------------------------------------------------------

def test_a_deeper_chunk_is_refused_and_an_escape_is_offered():
    g = gate(keep_out_aabbs=[OBSTACLE], recovery_planning=True)
    result = g.evaluate_step(T0, T0, state(0.42, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert result.allowed is False
    assert result.state == SOFT_STOP
    assert result.interlock == "safe_mechanical_hold"
    assert any("recovery_required:keep_out" in reason for reason in result.violated_invariants)
    assert result.safe_action["mode"] == "escape"
    assert len(result.safe_action["setpoints"]) == len(result.escape_path)
    assert result.escape_path
    assert clearances(result.escape_path, [OBSTACLE])[-1] > 0.0


def test_the_escape_path_is_monotone_and_inside_the_workspace():
    g = gate(keep_out_aabbs=[OBSTACLE], recovery_planning=True, tool_radius_m=0.02)
    result = g.evaluate_step(T0, T0, state(0.41, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    grown = [_grow(OBSTACLE, 0.02)]
    values = clearances(result.escape_path, grown)
    assert values[-1] > 0.0
    for earlier, later in zip(values, values[1:]):
        assert later > earlier + 1e-9
    keep_in = [BOX[0] + 0.02, BOX[1] + 0.02, BOX[2] + 0.02,
               BOX[3] - 0.02, BOX[4] - 0.02, BOX[5] - 0.02]
    for pose in result.escape_path:
        assert all(keep_in[axis] <= pose[axis] <= keep_in[axis + 3] for axis in range(3)), pose


def test_recovery_planning_offers_nothing_by_default():
    g = gate(keep_out_aabbs=[OBSTACLE])
    result = g.evaluate_step(T0, T0, state(0.42, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert result.escape_path == []
    assert result.safe_action["mode"] == "safe_mechanical_hold"


def test_no_escape_payload_under_the_e_stop_latch():
    """A latched E_STOP needs a human reset: it must never emit a motion."""
    g = gate(keep_out_aabbs=[OBSTACLE], recovery_planning=True)
    entered = g.evaluate_step(T0, T0, state(0.2, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert entered.state == E_STOP

    latched = g.evaluate_step(T0 + 20 * MS, T0 + 20 * MS, state(0.42, 0.5, 0.5),
                              chunk([0.5, 0.5, 0.5]))
    assert latched.state == E_STOP
    assert latched.verdict == "FORBIDDEN"
    assert latched.escape_path == []
    assert latched.safe_action["mode"] != "escape"
    assert "setpoints" not in latched.safe_action


def test_the_escape_path_ends_clear_enough_for_the_next_chunk_to_pass():
    g = gate(keep_out_aabbs=[OBSTACLE], recovery_planning=True)
    refused = g.evaluate_step(T0, T0, state(0.42, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert refused.verdict == "FORBIDDEN" and refused.escape_path

    exit_pose = refused.escape_path[-1]
    assert recovery.minimum_clearance(exit_pose, [OBSTACLE]) > 0.0

    resumed = g.evaluate_step(T0 + 20 * MS, T0 + 20 * MS, state(*exit_pose),
                              chunk([exit_pose[0] + 0.05, exit_pose[1], exit_pose[2]]))
    assert resumed.verdict == "COMPLIANT", resumed.violated_invariants
    assert resumed.state == ARMED
    assert resumed.escape_path == []


def test_a_shaped_keep_out_refusal_also_gets_an_escape():
    g = gate(keep_out_shapes=[CIRCLE], recovery_planning=True)
    result = g.evaluate_step(T0, T0, state(0.45, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert result.verdict == "FORBIDDEN"
    assert result.state == SOFT_STOP
    assert result.escape_path
    assert clearances(result.escape_path, shapes=[CIRCLE])[-1] > 0.0




def clustered_shapes():
    """A central disc ringed by seven, all touching: eight packed keep-outs."""
    shapes = [{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}]
    for index in range(7):
        angle = 2.0 * math.pi * index / 7.0
        shapes.append({"kind": "circle", "radius": 0.2,
                       "centre": [0.5 + 0.38 * math.cos(angle),
                                  0.5 + 0.38 * math.sin(angle)]})
    return shapes


def clustered_start():
    """Inside the central disc, 17 cm out along 60 degrees."""
    angle = math.radians(60.0)
    return [0.5 + 0.17 * math.cos(angle), 0.5 + 0.17 * math.sin(angle), 0.5]


def wall_shapes():
    """Three hazard discs along the 45-degree line: a wall with no gap in it."""
    step = 0.14 * 2 ** -0.5
    return [{"kind": "circle", "radius": 0.2,
             "centre": [0.5 + offset * step, 0.5 + offset * step]}
            for offset in (-1.0, 1.0, 2.0)]


def test_eight_packed_hazards_no_longer_trap_the_robot():
    """Every axis step reduces clearance to a neighbour; the search must still leave.

    This is the scene the six-direction search returned [] for: the robot sits in
    one of eight touching discs, and moving 2 cm towards open space moves 2 cm
    towards another disc. The escape exists, but it is not along an axis.
    """
    shapes = clustered_shapes()
    start = clustered_start()
    assert recovery.minimum_clearance(start, (), shapes) < 0.0

    path = recovery.escape_path(start, (), shapes, BOX, 0.02, 50)
    assert path, "a packed cluster must still yield a way out"
    assert recovery.minimum_clearance(path[-1], (), shapes) > 0.0, path[-1]

    floor = recovery.minimum_clearance(start, (), shapes)
    for pose in path:
        clearance = recovery.minimum_clearance(pose, (), shapes)
        assert clearance > floor, (floor, clearance)
        floor = clearance


def test_a_wall_of_hazards_is_left_around_its_end():
    """The direct way out is blocked, so the only admissible step is tangential."""
    shapes = wall_shapes()
    start = [0.5, 0.5, 0.5]
    floor = recovery.minimum_clearance(start, (), shapes)
    assert floor < 0.0

    step = 0.02
    inward = [start[0] - step * 2 ** -0.5, start[1] - step * 2 ** -0.5, 0.5]
    assert recovery.minimum_clearance(inward, (), shapes) <= floor, \
        "the direct line through the wall must not be an escape"

    path = recovery.escape_path(start, (), shapes, BOX, step, 50)
    assert path
    assert recovery.minimum_clearance(path[-1], (), shapes) > 0.0

    first = (path[0][0] - start[0], path[0][1] - start[1])
    assert abs(first[0]) > 0.01 and abs(first[1]) > 0.01, first


def test_a_packed_cluster_plans_deterministically():
    shapes = clustered_shapes()
    first = recovery.escape_path(clustered_start(), (), shapes, BOX, 0.02, 50)
    second = recovery.escape_path(clustered_start(), (), shapes, BOX, 0.02, 50)
    assert first and first == second


def test_a_packed_cluster_respects_workspace_steps_and_the_swept_test():
    shapes = clustered_shapes()
    start = clustered_start()

    clipped = recovery.escape_path(start, (), shapes, BOX, 0.02, 3)
    assert len(clipped) == 3

    path = recovery.escape_path(start, (), shapes, BOX, 0.02, 50)
    assert path
    previous = start
    for pose in path:
        assert all(BOX[axis] <= pose[axis] <= BOX[axis + 3] for axis in range(3)), pose
        for shape in shapes:
            if recovery.obstacle_clearance(previous, shape)[0] > 0.0:
                assert not geometry.sweep_hits(shape, previous, pose), (previous, pose)
        previous = pose
