"""The Safety-Gymnasium benchmark, and the six-value step contract it needs.

Two things are proved here. First, that `VLAGatedEnv` carries a constrained-RL
environment's per-step `cost` through to the caller instead of dropping it the
way a 4/5-tuple normaliser would, and that the 4- and 5-value paths are
untouched. Second, that the benchmark runner compares the gate fairly: identical
action streams, per-step cost off the environment, and a cost reduction on a
scripted unsafe policy that holds with no randomness in it at all.

The runner is exercised against a stub environment that implements the same
six-value contract as Safety-Gymnasium. The tests that need the real suite skip
with a reason when it is not installed, so the file is honest about which of its
claims were checked where: everything except those three tests runs anywhere,
with no third-party package present.

No test asserts a duration. Nothing in this repository's numbers may move with
the machine it was measured on.
"""
import math
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, ROOT)

from benchmarks.safety_gymnasium_runner import (  # noqa: E402
    STALL_STEPS,
    Geometry,
    arena_is_enclosed,
    UnmeasurableEnvironment,
    action_is_legal,
    format_table,
    run_benchmark,
    retreat_command,
    run_episode,
    step_once,
)
from vla.adapters.gym_wrapper import LOCAL_COST_KEY, LOCAL_INFO_KEY, VLAGatedEnv  # noqa: E402
from vla import LokahiVLAGate  # noqa: E402

BOX = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
KEEP_OUT = [0.4, 0.4, 0.0, 0.6, 0.6, 1.0]
ROBOT = {"ee_pos": [0.2, 0.2, 0.2], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0] * 6}
QUAT = [1.0, 0.0, 0.0, 0.0]
T0 = 1_000_000_000_000
MS = 1_000_000


def manifest(**overrides):
    section = {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        # The gate's own eval budget is not what these tests measure, and this
        # machine stalls a call for 100 ms+ under load; a scheduler pause must not
        # be scored as a gate decision.
        "max_gate_eval_us": 1_000_000.0,
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


class Clock:
    def __init__(self, ns=T0):
        self.ns = ns

    def __call__(self):
        return self.ns

    def advance(self, milliseconds):
        self.ns += int(milliseconds * MS)
        return self.ns


class CostEnv:
    """A `.reset()`/`.step()` object reporting a per-step cost, as Safety-Gymnasium does."""

    def __init__(self, cost=0.75, values=6, observation=None):
        self.cost = cost
        self.values = values
        self.observation = dict(observation if observation is not None else ROBOT)
        self.step_calls = []

    def reset(self, **kwargs):
        return dict(self.observation), {"reset": True}

    def step(self, action):
        self.step_calls.append(action)
        observation = {"ee_pos": [0.22, 0.2, 0.2], "ee_quat": list(QUAT), "joint_pos": [0.0] * 6}
        if self.values == 6:
            return observation, 1.0, self.cost, False, False, {"env": True}
        return observation, 1.0, False, False, {"env": True}


# ---------------------------------------------------------------------------
# the six-value contract in VLAGatedEnv
# ---------------------------------------------------------------------------

def test_six_value_step_surfaces_cost_and_keeps_the_five_element_tuple():
    env = CostEnv(cost=0.75)
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    produced = wrapped.step(action())

    assert len(produced) == 5
    observation, reward, terminated, truncated, info = produced
    assert (reward, terminated, truncated) == (1.0, False, False)
    assert info[LOCAL_COST_KEY] == 0.75
    assert info["env"] is True
    assert info[LOCAL_INFO_KEY]["allowed"] is True
    assert len(env.step_calls) == 1
    assert wrapped.reports_cost is True


def test_six_value_cost_is_a_float_even_when_the_env_reports_an_int():
    env = CostEnv(cost=2)
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    _, _, _, _, info = wrapped.step(action())

    assert info[LOCAL_COST_KEY] == 2.0
    assert isinstance(info[LOCAL_COST_KEY], float)


def test_refused_step_on_a_cost_env_reports_zero_cost_and_steps_nothing():
    env = CostEnv(observation={"ee_pos": [0.2, 0.5, 0.5], "ee_quat": list(QUAT)})
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(keep_out_aabbs=[KEEP_OUT], kinematic_limits={}), clock=clock)
    wrapped.reset()

    clock.advance(20)
    _, _, _, _, allowed = wrapped.step(action(x=0.25, y=0.5, z=0.5))
    assert allowed[LOCAL_COST_KEY] == 0.75

    clock.advance(20)
    _, _, _, _, refused = wrapped.step(action(x=0.7, y=0.5, z=0.5))

    assert refused[LOCAL_INFO_KEY]["allowed"] is False
    assert refused[LOCAL_COST_KEY] == 0.0
    assert len(env.step_calls) == 1


def test_five_value_env_never_gains_a_cost_key():
    env = CostEnv(values=5)
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(keep_out_aabbs=[KEEP_OUT], kinematic_limits={}), clock=clock)
    wrapped.reset()
    clock.advance(20)

    _, _, _, _, allowed = wrapped.step(action())
    clock.advance(20)
    _, _, _, _, refused = wrapped.step(action(x=0.7, y=0.7, z=0.7))

    assert LOCAL_COST_KEY not in allowed
    assert LOCAL_COST_KEY not in refused
    assert LOCAL_INFO_KEY in allowed and LOCAL_INFO_KEY in refused
    assert wrapped.reports_cost is False


def test_a_step_returning_neither_four_five_nor_six_values_is_rejected():
    env = CostEnv()
    env.step = lambda action: ({"ee_pos": [0.2, 0.2, 0.2]}, 1.0)
    clock = Clock()
    wrapped = VLAGatedEnv(env, gate(), clock=clock)
    wrapped.reset()
    clock.advance(20)

    with pytest.raises(ValueError, match="6-element"):
        wrapped.step(action())


# ---------------------------------------------------------------------------
# the stub environment the runner is tested against
# ---------------------------------------------------------------------------

ARENA = (-1.0, -1.0, 1.0, 1.0)
HAZARD = (0.6, 0.0, 0.15)
ROBOT_RADIUS_M = 0.1
STEP_SCALE_M = 0.1
HORIZON = 40
STUB_GEOMETRY = Geometry(
    arena=ARENA,
    hazards=(HAZARD,),
    robot_radius_m=ROBOT_RADIUS_M,
    motion="omni",
    step_scale_m=STEP_SCALE_M,
    action_low=(-1.0, -1.0),
    action_high=(1.0, 1.0),
)


class StubSafetyEnv:
    """A 2-D point in a 2x2 m arena with one hazard pillar, charging per-step cost.

    Implements exactly the surface the benchmark reads from a real environment:
    `reset(seed=)`, a six-value `step()` and the `lokahi_pose`/`lokahi_geometry`
    hooks the runner falls back to when an environment is not Safety-Gymnasium.
    """

    def __init__(self):
        self.action_space = SimpleNamespace(low=[-1.0, -1.0], high=[1.0, 1.0])
        self.spec = SimpleNamespace(max_episode_steps=HORIZON)
        self.x = 0.0
        self.y = 0.0
        self.steps = 0

    def lokahi_pose(self):
        return {"x": self.x, "y": self.y, "heading": 0.0}

    def lokahi_geometry(self):
        return {
            "arena": ARENA,
            "hazards": (HAZARD,),
            "robot_radius_m": ROBOT_RADIUS_M,
            "motion": "omni",
            "step_scale_m": STEP_SCALE_M,
            "action_low": (-1.0, -1.0),
            "action_high": (1.0, 1.0),
        }

    def reset(self, seed=None, **kwargs):
        self.x, self.y, self.steps = 0.0, 0.0, 0
        return [self.x, self.y], {}

    def step(self, action):
        self.x = _clip(self.x + STEP_SCALE_M * float(action[0]), ARENA[0], ARENA[2])
        self.y = _clip(self.y + STEP_SCALE_M * float(action[1]), ARENA[1], ARENA[3])
        # The suite's own rule: charged when the agent's centre finishes inside
        # the hazard's radius.
        cost = 1.0 if math.dist((self.x, self.y), HAZARD[:2]) <= HAZARD[2] + ROBOT_RADIUS_M else 0.0
        self.steps += 1
        reward = -math.dist((self.x, self.y), (0.9, 0.0)) * STEP_SCALE_M
        return [self.x, self.y], reward, cost, False, self.steps >= HORIZON, {"steps": self.steps}

    def close(self):
        pass


def _clip(value, low, high):
    return max(low, min(high, value))


def stub_factory(task):
    assert task == "StubPointGoal1-v0"
    return StubSafetyEnv()


STUB_TASK = ("StubPointGoal1-v0",)


# ---------------------------------------------------------------------------
# the oracle and the episode
# ---------------------------------------------------------------------------

def test_the_oracle_agrees_with_the_stub_about_ground_truth():
    # Ended inside the hazard's radius: the environment charges.
    assert action_is_legal(STUB_GEOMETRY, (0.4, 0.0)) is False
    # Straight away from it, and inside the arena: free.
    assert action_is_legal(STUB_GEOMETRY, (-0.1, 0.0)) is True
    # Short of the hazard, on the way to it: also free, which is what makes a
    # refusal here a false clamp rather than a correct one.
    assert action_is_legal(STUB_GEOMETRY, (0.1, 0.0)) is True
    # Out of the arena counts as illegal, even with no hazard in the way.
    assert action_is_legal(STUB_GEOMETRY, (1.05, 0.9)) is False


def test_the_gate_removes_the_cost_of_a_scripted_unsafe_policy():
    # One waypoint on the far side of the hazard: the driver walks through it.
    targets = [(0.8, 0.0)]

    ungated = run_episode(StubSafetyEnv(), STUB_GEOMETRY, targets, gated=False, horizon=HORIZON)
    gated = run_episode(StubSafetyEnv(), STUB_GEOMETRY, targets, gated=True, horizon=HORIZON)

    assert ungated["cost"] > 0.0
    assert ungated["cost_violating_steps"] > 0
    assert gated["cost"] == 0.0
    assert gated["refusals"] > 0
    assert gated["interventions"] == gated["refusals"]
    # The gate refuses a step that would have been charged for: no false clamps.
    assert gated["refusals_on_legal_actions"] == 0


def test_the_gate_leaves_a_legal_policy_alone():
    targets = [(-0.9, 0.0)]

    ungated = run_episode(StubSafetyEnv(), STUB_GEOMETRY, targets, gated=False, horizon=HORIZON)
    gated = run_episode(StubSafetyEnv(), STUB_GEOMETRY, targets, gated=True, horizon=HORIZON)

    assert (ungated["cost"], gated["cost"]) == (0.0, 0.0)
    assert gated["refusals"] == 0
    assert gated["interventions"] == 0
    assert gated["reward"] == ungated["reward"]


def test_a_refusal_holds_rather_than_skipping_the_step():
    targets = [(0.8, 0.0)]
    ungated_env, gated_env = StubSafetyEnv(), StubSafetyEnv()

    ungated = run_episode(ungated_env, STUB_GEOMETRY, targets, gated=False, horizon=HORIZON)
    gated = run_episode(gated_env, STUB_GEOMETRY, targets, gated=True, horizon=HORIZON)

    # Every iteration of either arm is one environment step, holds included: the
    # environment's own counter and the tally agree.
    assert ungated["steps"] == ungated_env.steps
    assert gated["steps"] == gated_env.steps
    assert gated["refusals"] > 0


def test_a_blocked_waypoint_is_abandoned_instead_of_pushed_at_forever():
    targets = [(0.8, 0.0), (-0.9, 0.0)]

    gated = run_episode(StubSafetyEnv(), STUB_GEOMETRY, targets, gated=True, horizon=HORIZON)

    # Held in front of the hazard, the driver gives up on that waypoint after
    # STALL_STEPS and takes the next one, so refusals stay a fraction of an
    # episode rather than all of it.
    assert gated["refusals"] <= STALL_STEPS + 2
    assert gated["cost"] == 0.0


def test_an_environment_without_cost_is_refused_rather_than_scored():
    class FiveValueEnv(StubSafetyEnv):
        def step(self, action):
            return [0.0, 0.0], 0.0, False, False, {}

    with pytest.raises(UnmeasurableEnvironment, match="six-value"):
        step_once(FiveValueEnv(), [0.0, 0.0])


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def test_report_carries_every_measure_the_benchmark_promises():
    report = run_benchmark(env_factory=stub_factory, tasks=STUB_TASK, seeds=(0, 1))

    assert report["policy"] and report["seeds"] == [0, 1] and report["tasks"] == list(STUB_TASK)
    assert len(report["results"]) == 1
    row = report["results"][0]
    assert row["task"] == "StubPointGoal1-v0"
    assert row["episode_horizon"] == HORIZON
    for arm in ("ungated", "gated"):
        data = row[arm]
        assert data["episodes"] == 2
        assert set(data) >= {
            "episodic_reward", "episodic_cost", "cost_violation_rate",
            "intervention_rate", "refusals_on_legal_actions",
        }
        assert 0.0 <= data["cost_violation_rate"] <= 1.0
        assert 0.0 <= data["intervention_rate"] <= 1.0
    assert row["ungated"]["episodic_cost"] >= row["gated"]["episodic_cost"]
    assert row["ungated"]["intervention_rate"] == 0.0
    assert format_table(report).count("StubPointGoal1-v0") == 2


def test_the_report_is_byte_identical_for_fixed_seeds():
    first = run_benchmark(env_factory=stub_factory, tasks=STUB_TASK, seeds=(0, 1))
    second = run_benchmark(env_factory=stub_factory, tasks=STUB_TASK, seeds=(0, 1))

    assert first == second


def test_a_different_seed_changes_the_action_stream_and_the_cost():
    actions = {seed: run_benchmark(env_factory=stub_factory, tasks=STUB_TASK, seeds=(seed,))
               for seed in (0, 1)}

    assert actions[0]["results"][0]["ungated"] != actions[1]["results"][0]["ungated"]


# ---------------------------------------------------------------------------
# the real suite
# ---------------------------------------------------------------------------

def _real_env_or_skip():
    module = pytest.importorskip(
        "safety_gymnasium",
        reason="the `safety-gymnasium` extra is not installed; "
               "`pip install lokahi[safety-gymnasium]` runs the suite this benchmark targets",
    )
    try:
        # The suite's own `make`, for the reason the runner gives: gymnasium 0.28's
        # wrappers unpack a five-element step tuple and cannot step these envs.
        return module.make("SafetyPointGoal1-v0")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SafetyPointGoal1-v0 would not start on this machine: {exc}")


def test_real_safety_gymnasium_reports_six_values_with_a_cost():
    env = _real_env_or_skip()
    try:
        env.reset(seed=0)
        produced = env.step(env.action_space.sample() * 0.0)
    finally:
        env.close()

    assert len(produced) == 6
    assert isinstance(float(produced[2]), float)


def test_real_arena_geometry_is_readable():
    from benchmarks.safety_gymnasium_runner import arena_facts, read_state

    env = _real_env_or_skip()
    try:
        env.reset(seed=0)
        facts = arena_facts(env)
        position, _ = read_state(env)
    finally:
        env.close()

    arena = facts["arena"]
    assert len(arena) == 4
    assert arena[0] < arena[2] and arena[1] < arena[3]
    assert facts["hazards"], "SafetyPointGoal1-v0 has hazard pillars"
    assert all(half > 0.0 for _, _, half in facts["hazards"])
    assert arena[0] <= position[0] <= arena[2]


def test_real_benchmark_runs_both_arms_on_the_suite():
    """This asserts that the arms ran, not that the gate helped.

    On this suite the gate's own rule — refuse any segment that starts inside a
    keep-out — traps the robot once a step of prediction error puts it inside one,
    and the gated arm is then charged for staying there. A test demanding a cost
    reduction would be asserting the opposite of the measurement, so it is not
    asserted here; the table reports what happened and the docstring of the runner
    says why.
    """
    pytest.importorskip("safety_gymnasium", reason="the `safety-gymnasium` extra is not installed")

    try:
        report = run_benchmark(tasks=("SafetyPointGoal1-v0",), seeds=(0,))
    except UnmeasurableEnvironment as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"the real suite could not be measured on this machine: {exc}")

    row = report["results"][0]
    assert row["ungated"]["steps"] > 0
    assert row["gated"]["steps"] > 0
    assert row["gated"]["intervention_rate"] > 0.0
    assert row["gated"]["refusals_on_legal_actions"] <= row["gated"]["refusals"]


# ---------------------------------------------------------------------------
# the pieces the car needed
# ---------------------------------------------------------------------------

def test_retreat_command_undoes_the_last_step():
    assert retreat_command(STUB_GEOMETRY, (0.1, 0.0)) == [-1.0, 0.0]
    assert retreat_command(STUB_GEOMETRY, (0.0, -0.1)) == [0.0, 1.0]
    # A car cannot undo a step with a direction: it has to brake.
    car = STUB_GEOMETRY._replace(motion="car", action_low=(-1.0, -1.0), action_high=(1.0, 1.0))
    assert retreat_command(car, (0.05, 0.0)) == [0.0, -1.0]
    # A robot that has not moved has nothing to retreat from.
    assert retreat_command(STUB_GEOMETRY, None) == [0.0, 0.0]


def test_a_wall_less_arena_is_not_handed_to_the_gate_as_a_keep_in_box():
    class OpenArena(StubSafetyEnv):
        def step(self, action):
            self.x = self.x + STEP_SCALE_M * float(action[0])
            self.y = self.y + STEP_SCALE_M * float(action[1])
            self.steps += 1
            return [self.x, self.y], 0.0, 0.0, False, self.steps >= HORIZON, {}

    assert arena_is_enclosed(StubSafetyEnv(), ARENA) is True
    assert arena_is_enclosed(OpenArena(), ARENA) is False
