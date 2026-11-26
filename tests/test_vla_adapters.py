"""The zero-dependency adapters: gym wrapper, LeRobot hook, ROS 2 translation.

Two things are proved here. First, that a policy's action chunk can be gated
before it reaches an actuator through each of the three integration surfaces.
Second, that importing these adapters pulls in none of numpy, gymnasium, torch
or rclpy — the last two tests check that by diffing `sys.modules` and by
importing the package in a fresh interpreter, rather than trusting this one.

Every refusal test asserts the wrapped environment was never stepped. A gate
that returns `allowed=False` while the action still reached the robot would be
the worst possible outcome here, so it is checked mechanically.
"""
import copy
import json
import math
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
FIXTURES = os.path.abspath(os.path.join(HERE, "..", "data", "vla_fixtures"))

sys.path.insert(0, TOOLS)

from vla.adapters import (  # noqa: E402
    ROS2AdapterError,
    VLAGatedEnv,
    chunk_to_joint_trajectory,
    filter_action_chunk,
    header_frame_id,
    header_stamp_ns,
    joint_state_to_robot_state,
    joint_trajectory_to_chunk,
    ns_to_duration,
    ns_to_stamp,
    stamp_to_ns,
    twist_stamped_to_velocity,
    velocity_to_twist_stamped,
    wrench_gate_to_stamped,
    wrench_stamped_to_gate,
)
from vla.vla_gate import LokahiVLAGate  # noqa: E402

BOX = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
OBSTACLE = [0.4, 0.4, 0.0, 0.6, 0.6, 1.0]
T0 = 1_000_000_000_000
MS = 1_000_000

ADAPTER_MAPPING = {
    "ee_pos": (0, 3),
    "ee_quat": (3, 7),
    "joint_pos": (7, 13),
    "duration_ms": 20.0,
}
ROBOT = {"ee_pos": [0.2, 0.2, 0.2], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0] * 6}
QUAT = [1.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def manifest(**overrides):
    section = {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        "control_period_ms": 20.0,
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


def action(x=0.22, y=0.2, z=0.2):
    return [{"ee_pos": [x, y, z], "ee_quat": list(QUAT), "joint_pos": [0.0] * 6, "duration_ms": 20.0}]


def row(x=0.2, y=0.2, z=0.2):
    return [x, y, z, *QUAT, *([0.0] * 6)]


class Clock:
    def __init__(self, ns=T0):
        self.ns = ns

    def __call__(self):
        return self.ns

    def advance(self, milliseconds):
        self.ns += int(milliseconds * MS)
        return self.ns


class FakeEnv:
    """A `.reset()`/`.step()` object with no gymnasium in sight."""

    def __init__(self, observation=None, step_values=5):
        self.observation = dict(observation if observation is not None else ROBOT)
        self.step_values = step_values
        self.step_calls = []
        self.reset_calls = 0
        self.action_space = "fake-space"

    def reset(self, **kwargs):
        self.reset_calls += 1
        return dict(self.observation), {"reset": True}

    def step(self, action):
        self.step_calls.append(action)
        observation = {"ee_pos": [0.22, 0.2, 0.2], "ee_quat": list(QUAT), "joint_pos": [0.0] * 6}
        if self.step_values == 5:
            return observation, 1.0, False, False, {"env": True}
        return observation, 1.0, False, {"env": True}


class Array:
    """Duck-typed stand-in for a numpy array or torch tensor."""

    def __init__(self, data):
        self._data = data

    def tolist(self):
        return self._data


def fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


CARTESIAN = [
    [0.30, 0.10, 0.40],
    [0.31, 0.10, 0.40],
    [0.32, 0.10, 0.40],
    [0.33, 0.10, 0.40],
    [0.34, 0.10, 0.40],
]


# ---------------------------------------------------------------------------
# gym wrapper
# ---------------------------------------------------------------------------

def test_gym_allowed_step_returns_five_tuple_and_verdict():
    env = FakeEnv()
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    observation, info = wrapped.reset()
    assert info == {"reset": True}
    clock.advance(20)

    chunk = action()
    observation, reward, terminated, truncated, info = wrapped.step(chunk)

    assert (reward, terminated, truncated) == (1.0, False, False)
    assert info["env"] is True
    assert info["lokahi_vla"]["verdict"] == "COMPLIANT"
    assert info["lokahi_vla"]["allowed"] is True
    assert env.step_calls == [chunk]
    assert wrapped.observation is observation
    assert (wrapped.steps, wrapped.refusals) == (1, 0)
    assert "safe_action" not in info["lokahi_vla"]


def test_gym_four_element_env_is_normalised():
    env = FakeEnv(step_values=4)
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    observation, reward, terminated, truncated, info = wrapped.step(action())

    assert info["env"] is True
    assert (terminated, truncated) == (False, False)


def test_gym_four_element_env_terminated_maps_done():
    env = FakeEnv(step_values=4)
    env.step = lambda action: ({"ee_pos": [0.22, 0.2, 0.2], "ee_quat": list(QUAT)}, 0.0, True, {})
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    _, _, terminated, truncated, _ = wrapped.step(action())

    assert (terminated, truncated) == (True, False)


def test_gym_refused_step_never_reaches_the_env():
    env = FakeEnv(observation={"ee_pos": [0.2, 0.5, 0.5], "ee_quat": list(QUAT)})
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(keep_out_aabbs=[OBSTACLE], kinematic_limits={}), clock=clock)
    previous, _ = wrapped.reset()
    clock.advance(20)

    observation, reward, terminated, truncated, info = wrapped.step(action(x=0.7, y=0.5, z=0.5))

    assert observation is previous
    assert (reward, terminated, truncated) == (0.0, False, False)
    assert env.step_calls == []
    assert info["lokahi_vla"]["allowed"] is False
    assert info["lokahi_vla"]["state"] == "E_STOP"
    assert info["lokahi_vla"]["interlock"] == "normal_retract"
    assert any("workspace_keep_out" in reason for reason in info["lokahi_vla"]["violated_invariants"])
    assert wrapped.refusals == 1


def test_gym_estop_latch_survives_until_a_human_resets():
    env = FakeEnv(observation={"ee_pos": [0.2, 0.5, 0.5], "ee_quat": list(QUAT)})
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(keep_out_aabbs=[OBSTACLE], kinematic_limits={}), clock=clock)
    wrapped.reset()

    clock.advance(20)
    _, _, _, _, first = wrapped.step(action(x=0.7, y=0.5, z=0.5))
    assert first["lokahi_vla"]["state"] == "E_STOP"

    clock.advance(20)
    _, _, _, _, second = wrapped.step(action(x=0.25, y=0.5, z=0.5))
    assert second["lokahi_vla"]["allowed"] is False
    assert second["lokahi_vla"]["state"] == "E_STOP"
    assert env.step_calls == []

    wrapped.human_reset("alice", now_ts_ns=clock.ns)

    clock.advance(20)
    _, _, _, _, third = wrapped.step(action(x=0.25, y=0.5, z=0.5))
    assert third["lokahi_vla"]["allowed"] is True
    assert len(env.step_calls) == 1


def test_gym_clamped_chunk_executes_only_the_prefix():
    env = FakeEnv()
    clock = Clock()
    wrapped = VLAGatedEnv(
        env,
        gate(policy_on_violation={"stale_action": "CLAMP"}),
        clock=clock,
    )
    wrapped.reset()
    clock.advance(125)

    chunk = [{"ee_pos": [0.2 + 0.01 * index, 0.2, 0.2], "ee_quat": list(QUAT)} for index in range(6)]
    _, _, _, _, info = wrapped.step(chunk)

    decision = info["lokahi_vla"]
    assert decision["verdict"] == "CLAMPED"
    assert decision["allowed"] is True
    assert decision["clamped_from_index"] == 2
    assert any("stale_action" in reason for reason in decision["violated_invariants"])
    assert len(env.step_calls) == 1
    assert len(env.step_calls[0]) == 2


def test_gym_translation_failure_fails_closed():
    env = FakeEnv()
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    _, reward, terminated, truncated, info = wrapped.step("not an action")

    assert (reward, terminated, truncated) == (0.0, False, False)
    assert env.step_calls == []
    assert info["lokahi_vla"]["allowed"] is False
    assert info["lokahi_vla"]["violated_invariants"][0].startswith("adapters.translation_failed:")


def test_gym_observation_without_robot_state_fails_closed():
    env = FakeEnv(observation={"joint_pos": [0.0] * 6})
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    _, _, _, _, info = wrapped.step(action())

    assert env.step_calls == []
    assert info["lokahi_vla"]["allowed"] is False
    assert "adapters.translation_failed:" in info["lokahi_vla"]["violated_invariants"][0]


def test_gym_custom_translators_are_used():
    env = FakeEnv()
    clock = Clock()
    calls = {}

    def to_chunk(policy_output):
        calls["chunk"] = policy_output
        return [{"ee_pos": policy_output, "ee_quat": list(QUAT)}]

    def from_chunk(chunk, result):
        calls["from"] = result.verdict
        return [element["ee_pos"] for element in chunk]

    wrapped = VLAGatedEnv(env, gate(), clock=clock, to_chunk=to_chunk, from_chunk=from_chunk)
    wrapped.reset()
    clock.advance(20)
    wrapped.step([0.22, 0.2, 0.2])

    assert calls == {"chunk": [0.22, 0.2, 0.2], "from": "COMPLIANT"}
    assert env.step_calls == [[[0.22, 0.2, 0.2]]]


def test_gym_step_before_reset_raises():
    wrapped = VLAGatedEnv(FakeEnv(), gate(), clock=Clock())
    with pytest.raises(RuntimeError, match="before reset"):
        wrapped.step(action())


def test_gym_delegates_unknown_attributes():
    env = FakeEnv()
    wrapped = VLAGatedEnv(env, gate(), clock=Clock())
    assert wrapped.action_space == "fake-space"
    assert wrapped.unwrapped is env


def test_gym_refuses_a_bad_env_or_a_missing_gate():
    with pytest.raises(TypeError, match="reset"):
        VLAGatedEnv(object(), gate())
    with pytest.raises(ValueError, match="gate"):
        VLAGatedEnv(FakeEnv(), None)


# ---------------------------------------------------------------------------
# LeRobot hook
# ---------------------------------------------------------------------------

def test_lerobot_allows_the_whole_chunk():
    rows = [row(0.2 + 0.01 * index) for index in range(4)]
    allowed, result = filter_action_chunk(
        gate(),
        rows,
        mapping=ADAPTER_MAPPING,
        robot_state=ROBOT,
        obs_ts_ns=T0,
        now_ts_ns=T0 + 20 * MS,
    )

    assert result.allowed is True
    assert result.verdict == "COMPLIANT"
    assert allowed == rows
    assert all(isinstance(line, list) for line in allowed)


def test_lerobot_reads_duck_typed_arrays():
    rows = Array([Array(row(0.2 + 0.01 * index)) for index in range(3)])
    allowed, result = filter_action_chunk(
        gate(),
        rows,
        mapping=ADAPTER_MAPPING,
        robot_state=ROBOT,
        obs_ts_ns=T0,
        now_ts_ns=T0 + 20 * MS,
    )

    assert result.allowed is True
    assert len(allowed) == 3
    assert allowed[0] == row(0.2)


def test_lerobot_unwraps_a_single_element_batch():
    batched = Array([[row(0.2), row(0.21), row(0.22)]])
    allowed, result = filter_action_chunk(
        gate(),
        batched,
        mapping=ADAPTER_MAPPING,
        robot_state=ROBOT,
        obs_ts_ns=T0,
        now_ts_ns=T0 + 20 * MS,
    )

    assert result.allowed is True
    assert len(allowed) == 3


def test_lerobot_clamps_to_the_prefix():
    rows = [row(0.2 + 0.01 * index) for index in range(6)]
    allowed, result = filter_action_chunk(
        gate(policy_on_violation={"stale_action": "CLAMP"}),
        rows,
        mapping=ADAPTER_MAPPING,
        robot_state=ROBOT,
        obs_ts_ns=T0,
        now_ts_ns=T0 + 125 * MS,
    )

    assert result.verdict == "CLAMPED"
    assert result.clamped_from_index == 2
    assert allowed == rows[:2]


def test_lerobot_refuses_a_chunk_through_an_obstacle():
    rows = [row(x, 0.5, 0.5) for x in (0.2, 0.35, 0.5, 0.65)]
    allowed, result = filter_action_chunk(
        gate(keep_out_aabbs=[OBSTACLE], kinematic_limits={}),
        rows,
        mapping=ADAPTER_MAPPING,
        robot_state={"ee_pos": [0.2, 0.5, 0.5], "ee_quat": list(QUAT)},
        obs_ts_ns=T0,
        now_ts_ns=T0 + 20 * MS,
    )

    assert result.allowed is False
    assert result.state == "E_STOP"
    assert allowed == []


def test_lerobot_refuses_non_finite_values():
    rows = [row(0.2), [float("nan"), 0.2, 0.2, *QUAT, *([0.0] * 6)]]
    allowed, result = filter_action_chunk(
        gate(),
        rows,
        mapping=ADAPTER_MAPPING,
        robot_state=ROBOT,
        obs_ts_ns=T0,
        now_ts_ns=T0 + 20 * MS,
    )

    assert result.allowed is False
    assert any("structural" in reason for reason in result.violated_invariants)
    assert allowed == []


def test_lerobot_duration_falls_back_to_the_gate_control_period():
    allowed, result = filter_action_chunk(
        gate(),
        [row(0.2)],
        mapping={"ee_pos": (0, 3)},
        robot_state=ROBOT,
        obs_ts_ns=T0,
        now_ts_ns=T0 + 20 * MS,
    )

    assert result.allowed is True
    assert allowed == [row(0.2)]


def test_lerobot_requires_robot_state():
    with pytest.raises(ValueError, match="robot_state is required"):
        filter_action_chunk(gate(), [row()], mapping=ADAPTER_MAPPING, robot_state=None)


def test_lerobot_rejects_an_empty_chunk():
    with pytest.raises(ValueError, match="empty"):
        filter_action_chunk(gate(), [], mapping=ADAPTER_MAPPING, robot_state=ROBOT)


def test_lerobot_rejects_a_mapping_that_selects_nothing():
    with pytest.raises(ValueError, match="selected no values"):
        filter_action_chunk(
            gate(),
            [row()],
            mapping={"ee_pos": (0, 3), "ee_quat": (40, 44)},
            robot_state=ROBOT,
            obs_ts_ns=T0,
            now_ts_ns=T0 + 20 * MS,
        )


# ---------------------------------------------------------------------------
# ROS 2 translation
# ---------------------------------------------------------------------------

def test_joint_trajectory_fixture_to_chunk():
    trajectory = fixture("joint_trajectory.json")
    chunk = joint_trajectory_to_chunk(trajectory, cartesian_path=CARTESIAN)

    assert len(chunk) == len(trajectory["points"]) == 5
    assert [element["duration_ms"] for element in chunk] == pytest.approx([20.0] * 5)
    assert chunk[0]["ee_pos"] == [0.30, 0.10, 0.40]
    assert chunk[0]["joint_pos"] == pytest.approx([0.10, -1.20, 1.40, -1.75, -1.57, 0.00])
    assert "ee_quat" not in chunk[0]


def test_joint_trajectory_round_trip():
    trajectory = fixture("joint_trajectory.json")
    chunk = joint_trajectory_to_chunk(
        trajectory,
        cartesian_path=CARTESIAN,
        ee_quat=[list(QUAT) for _ in CARTESIAN],
    )
    back = chunk_to_joint_trajectory(
        chunk,
        joint_names=trajectory["joint_names"],
        frame_id=header_frame_id(trajectory),
        stamp_ns=header_stamp_ns(trajectory),
    )

    assert back["joint_names"] == trajectory["joint_names"]
    assert back["header"] == trajectory["header"]
    for expected, actual in zip(trajectory["points"], back["points"]):
        assert actual["positions"] == pytest.approx(expected["positions"])
        assert actual["time_from_start"] == expected["time_from_start"]


def test_joint_trajectory_requires_a_pose_per_point():
    trajectory = fixture("joint_trajectory.json")
    with pytest.raises(ROS2AdapterError, match="cartesian_path has 4 poses"):
        joint_trajectory_to_chunk(trajectory, cartesian_path=CARTESIAN[:4])


def test_joint_trajectory_requires_timing():
    trajectory = fixture("joint_trajectory.json")
    broken = copy.deepcopy(trajectory)
    del broken["points"][2]["time_from_start"]
    with pytest.raises(ROS2AdapterError, match="stamp is missing"):
        joint_trajectory_to_chunk(broken, cartesian_path=CARTESIAN)


def test_chunk_to_joint_trajectory_requires_joint_positions():
    chunk = [{"ee_pos": [0.3, 0.1, 0.4], "ee_quat": list(QUAT)}]
    with pytest.raises(ROS2AdapterError, match="no joint_pos"):
        chunk_to_joint_trajectory(chunk, joint_names=["a", "b"])


def test_joint_state_fixture_builds_robot_state():
    state = joint_state_to_robot_state(
        fixture("joint_state.json"),
        ee_pos=[0.30, 0.10, 0.40],
        ee_quat=list(QUAT),
    )

    assert state["joint_pos"] == pytest.approx([-0.05, -1.20, 1.40, -1.75, -1.57, 0.00])
    assert state["joint_names"][0] == "shoulder_pan_joint"
    assert state["ee_pos"] == [0.30, 0.10, 0.40]
    assert state["ee_quat"] == list(QUAT)


def test_wrench_fixture_round_trip():
    message = fixture("wrench_stamped.json")
    wrench = wrench_stamped_to_gate(message)

    assert wrench == {"force_n": [1.2, -0.4, 8.7], "torque_nm": [0.3, 0.1, -0.2]}

    back = wrench_gate_to_stamped(
        wrench,
        frame_id=header_frame_id(message),
        stamp_ns=header_stamp_ns(message),
    )
    assert back == message


def test_twist_fixture_round_trip():
    message = fixture("twist_stamped.json")
    velocity = twist_stamped_to_velocity(message)

    assert velocity["linear"] == pytest.approx([0.05, 0.0, -0.02])
    assert velocity["angular"] == pytest.approx([0.0, 0.0, 0.10])
    assert velocity["frame_id"] == "base_link"
    assert velocity["stamp_ns"] == 1732099215310000000

    back = velocity_to_twist_stamped(
        velocity,
        frame_id=velocity["frame_id"],
        stamp_ns=velocity["stamp_ns"],
    )
    assert back == message


def test_stamp_and_duration_conversion_round_trips():
    assert stamp_to_ns(ns_to_stamp(1732099215305000000)) == 1732099215305000000
    assert stamp_to_ns({"sec": 0, "nanosec": 20000000}) == 20 * MS
    # A negative Duration keeps both components the same sign, as rmw requires.
    assert stamp_to_ns(ns_to_duration(-1_500_000_000)) == -1_500_000_000


def test_recorded_fixtures_gate_end_to_end():
    trajectory = fixture("joint_trajectory.json")
    joints = fixture("joint_state.json")
    wrench = fixture("wrench_stamped.json")

    state = joint_state_to_robot_state(joints, ee_pos=[0.30, 0.10, 0.40], ee_quat=list(QUAT))
    chunk = joint_trajectory_to_chunk(trajectory, cartesian_path=CARTESIAN, ee_quat=[list(QUAT)] * 5)
    result = gate(
        kinematic_limits={
            "max_pos_delta_m": 0.25,
            "joint_limits": [[-3.14, 3.14]] * 6,
        },
    ).evaluate_step(
        obs_ts_ns=header_stamp_ns(joints),
        now_ts_ns=header_stamp_ns(trajectory),
        robot_state=state,
        action_chunk=chunk,
        wrench=wrench_stamped_to_gate(wrench),
    )

    assert result.allowed is True, result.violated_invariants
    assert result.verdict == "COMPLIANT"
    assert result.margins["force_margin_n"] == pytest.approx(10.0 - math.sqrt(77.29))


def test_recorded_wrench_over_the_envelope_is_refused():
    trajectory = fixture("joint_trajectory.json")
    wrench = copy.deepcopy(fixture("wrench_stamped.json"))
    wrench["wrench"]["force"]["z"] = 20.0

    chunk = joint_trajectory_to_chunk(trajectory, cartesian_path=CARTESIAN)
    result = gate().evaluate_step(
        obs_ts_ns=header_stamp_ns(trajectory) - 5 * MS,
        now_ts_ns=header_stamp_ns(trajectory),
        robot_state={"ee_pos": [0.30, 0.10, 0.40], "ee_quat": list(QUAT)},
        action_chunk=chunk,
        wrench=wrench_stamped_to_gate(wrench),
    )

    assert result.allowed is False
    assert any("wrench:force" in reason for reason in result.violated_invariants)


# ---------------------------------------------------------------------------
# the dependency claim
# ---------------------------------------------------------------------------

BLOCKED = ("numpy", "gymnasium", "gym", "torch", "rclpy")


def test_importing_the_adapters_adds_no_heavy_module():
    for name in [module for module in sys.modules if module.split(".")[:2] == ["vla", "adapters"]]:
        del sys.modules[name]
    before = set(sys.modules)

    import vla.adapters  # noqa: F401

    added = {module.split(".")[0] for module in set(sys.modules) - before}
    assert not (added & set(BLOCKED)), sorted(added & set(BLOCKED))


def test_adapters_import_clean_in_a_fresh_interpreter():
    script = "\n".join([
        "import sys",
        f"sys.path.insert(0, {TOOLS!r})",
        "import vla.adapters",
        "from vla.adapters import VLAGatedEnv, filter_action_chunk, joint_trajectory_to_chunk",
        "blocked = [name for name in %r if name in sys.modules]" % (BLOCKED,),
        "print(','.join(blocked) or 'clean')",
        "sys.exit(1 if blocked else 0)",
    ])
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "clean"
