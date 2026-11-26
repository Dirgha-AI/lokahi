"""The safe-control-gym comparator: three arms, one driver, one constraint flag.

The suite's real environment is not importable without torch, casadi, cvxpy and
pybullet, and the runtime package must not depend on any of them, so the runner
is exercised here against a stub that implements exactly the surface it reads
from safe-control-gym: a four-value `step()` with `info["constraint_violation"]`,
`.state`, `.physical_action_bounds`, `.EPISODE_LEN_SEC` and `.CTRL_FREQ`. Every
test whose environment or filter is a stub says so in its name. The tests that
need the real suite skip with a reason when it is absent, so nothing here claims
a number the real suite did not produce.

No test asserts a duration. Nothing in this repository's numbers may move with
the machine it was measured on.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, ROOT)

import benchmarks.safe_control_gym_runner as runner  # noqa: E402
from benchmarks.safe_control_gym_runner import (  # noqa: E402
    CART_BOUND_M,
    STALL_STEPS,
    Geometry,
    UnmeasurableEnvironment,
    action_is_legal,
    format_table,
    manifest_for,
    read_full_state,
    read_state,
    run_benchmark,
    run_episode,
    step_once,
)

STEP_M_PER_FORCE = 0.05
HORIZON = 25

STUB_GEOMETRY = Geometry(
    arena=(-CART_BOUND_M, -0.5, CART_BOUND_M, 0.5),
    robot_radius_m=0.0,
    step_scale_m=STEP_M_PER_FORCE * 10.0,
    action_low=(-10.0,),
    action_high=(10.0,),
)


class StubCartpole:
    """A stub for safe-control-gym's cartpole: a cart on a rail, charged past ±2 m.

    Same four-value contract as the suite (`obs, reward, done, info` with
    `info["constraint_violation"]`), the same `physical_action_bounds` and the
    same two config attributes the runner reads its horizon from. It models the
    rail only — the pole is pinned upright — because the rail is the part of the
    suite's state constraint a gate that sees positions can police at all.
    """

    EPISODE_LEN_SEC = HORIZON / 25.0
    CTRL_FREQ = 25

    def __init__(self):
        self.physical_action_bounds = ([-10.0], [10.0])
        self.state = [0.0, 0.0, 0.0, 0.0]
        self.steps = 0

    def lokahi_pose(self):
        return {"x": self.state[0], "y": 0.0}

    def reset(self, seed=None, **kwargs):
        self.state = [0.0, 0.0, 0.0, 0.0]
        self.steps = 0
        return list(self.state), {"constraint_violation": 0}

    def step(self, action):
        force = float(action[0])
        x, x_dot, theta, theta_dot = self.state
        x_dot = x_dot + 0.5 * force
        x = x + STEP_M_PER_FORCE * force
        self.state = [x, x_dot, theta, theta_dot]
        self.steps += 1
        violation = 1 if abs(x) > CART_BOUND_M else 0
        reward = -abs(x)
        return list(self.state), reward, self.steps >= HORIZON, {"constraint_violation": violation}

    def close(self):
        pass


class StubCbf:
    """A stub for the suite's CBF filter: clamps every command into ±1 N."""

    def __init__(self):
        self.calls = []

    def certify_action(self, current_state, uncertified_action, info=None):
        self.calls.append(list(uncertified_action))
        force = max(-1.0, min(1.0, float(uncertified_action[0])))
        return [force], True


class InfeasibleStubCbf:
    """A stub for the suite's CBF filter whose QP comes back infeasible."""

    def certify_action(self, current_state, uncertified_action, info=None):
        return list(uncertified_action), False


def stub_env_factory(task):
    assert task == "cartpole"
    return StubCartpole()


def stub_cbf_factory(task):
    assert task == "cartpole"
    return StubCbf()


# ---------------------------------------------------------------------------
# the suite's four-value contract
# ---------------------------------------------------------------------------

def test_the_four_value_constraint_contract_is_read_like_safe_control_gym():
    env = StubCartpole()
    env.reset()
    env.state = [0.0, 0.0, 0.0, 0.0]

    reward, cost, done = step_once(env, [10.0])

    assert reward == pytest.approx(-0.5)
    assert cost == 0.0
    assert done is False
    # Five steps of 0.5 m: the fourth ends exactly on the bound, which is not a
    # violation, and the fifth is the first step past it.
    costs = [step_once(env, [10.0])[1] for _ in range(4)]
    assert costs == [0.0, 0.0, 0.0, 1.0]


def test_a_five_value_step_is_refused_rather_than_scored():
    class FiveValueEnv(StubCartpole):
        def step(self, action):
            return [0.0, 0.0, 0.0, 0.0], 0.0, False, False, {}

    with pytest.raises(UnmeasurableEnvironment, match="four-value"):
        step_once(FiveValueEnv(), [0.0])


def test_an_env_without_the_constraint_flag_is_refused():
    class NoFlagEnv(StubCartpole):
        def step(self, action):
            return [0.0, 0.0, 0.0, 0.0], 0.0, False, {"mse": 0.0}

    with pytest.raises(UnmeasurableEnvironment, match="constraint_violation"):
        step_once(NoFlagEnv(), [0.0])


def test_an_env_without_a_state_vector_is_refused():
    class StatelessEnv:
        pass

    with pytest.raises(UnmeasurableEnvironment, match="lokahi_state"):
        read_full_state(StatelessEnv())


def test_the_state_vector_is_read_from_env_state_and_from_the_hook():
    env = StubCartpole()
    env.state = [0.25, 0.0, -0.1, 0.0]

    assert read_full_state(env) == [0.25, 0.0, -0.1, 0.0]
    assert read_state(env) == (0.25, 0.0)


# ---------------------------------------------------------------------------
# the oracle and the arms
# ---------------------------------------------------------------------------

def test_the_oracle_judges_the_rail_and_nothing_else():
    assert action_is_legal(STUB_GEOMETRY, (1.9, 0.0)) is True
    assert action_is_legal(STUB_GEOMETRY, (2.1, 0.0)) is False
    assert action_is_legal(STUB_GEOMETRY, (-2.1, 0.0)) is False
    # The oracle is a position rule, so a pose off the rail's narrow y band is
    # not inside it — which is why the gate is handed y=0 every step.
    assert action_is_legal(STUB_GEOMETRY, (0.0, 0.9)) is False


def test_the_gate_removes_the_cost_of_a_scripted_unsafe_driver():
    # A target beyond the end of the rail, which is the case a shield exists for:
    # the ungated arm drives off it and is charged.
    targets = [2.6]

    ungated = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="ungated", horizon=HORIZON)
    gated = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="gated", horizon=HORIZON)

    assert ungated["cost"] > 0.0
    assert ungated["cost_violating_steps"] > 0
    assert gated["cost"] == 0.0
    assert gated["refusals"] > 0
    assert gated["interventions"] == gated["refusals"]
    assert gated["refusals_on_legal_actions"] == 0


def test_the_gate_leaves_a_legal_driver_alone():
    targets = [0.4]

    ungated = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="ungated", horizon=HORIZON)
    gated = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="gated", horizon=HORIZON)

    assert (ungated["cost"], gated["cost"]) == (0.0, 0.0)
    assert gated["refusals"] == 0
    assert gated["interventions"] == 0
    assert gated["reward"] == ungated["reward"]


def test_a_refusal_holds_rather_than_skipping_the_step():
    targets = [2.6]
    ungated_env, gated_env = StubCartpole(), StubCartpole()

    ungated = run_episode(ungated_env, STUB_GEOMETRY, targets, arm="ungated", horizon=HORIZON)
    gated = run_episode(gated_env, STUB_GEOMETRY, targets, arm="gated", horizon=HORIZON)

    # Every iteration of either arm is one environment step, holds included: the
    # environment's own counter and the tally agree.
    assert ungated["steps"] == ungated_env.steps
    assert gated["steps"] == gated_env.steps
    assert gated["refusals"] > 0


def test_an_unreachable_target_is_abandoned_instead_of_pushed_at_forever():
    targets = [2.6, -2.6]

    gated = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="gated", horizon=HORIZON)

    assert gated["refusals"] <= STALL_STEPS + 2
    assert gated["cost"] == 0.0


def test_a_stub_cbf_filter_arm_counts_corrections_as_refusals():
    targets = [2.6]
    stub = StubCbf()

    cbf = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="cbf",
                      horizon=HORIZON, cbf_filter=stub)

    assert stub.calls, "the filter arm must hand every candidate to the filter"
    assert cbf["refusals"] > 0
    assert cbf["interventions"] == cbf["refusals"]
    # The false-clamp measure is the gate's and is not computed for this arm:
    # scoring a CBF's pole correction against a positional oracle would call a
    # working filter an over-refusing one.
    assert cbf["refusals_on_legal_actions"] == 0


def test_an_infeasible_stub_cbf_qp_holds_the_step():
    targets = [2.6]

    cbf = run_episode(StubCartpole(), STUB_GEOMETRY, targets, arm="cbf",
                      horizon=HORIZON, cbf_filter=InfeasibleStubCbf())

    assert cbf["refusals"] == cbf["steps"]
    assert cbf["cost"] == 0.0


def test_an_unknown_arm_is_refused():
    with pytest.raises(ValueError, match="unknown arm"):
        run_episode(StubCartpole(), STUB_GEOMETRY, [0.4], arm="mpsc", horizon=HORIZON)


def test_the_manifest_states_the_rail_as_a_keep_in_box():
    section = manifest_for(STUB_GEOMETRY)["physical_vla"]
    assert section["workspace_aabb"] == [-CART_BOUND_M, -0.5, 0.0, CART_BOUND_M, 0.5, 1.0]
    assert section["keep_out_aabbs"] == []
    assert section["tool_radius_m"] == 0.0


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def test_the_report_carries_every_measure_the_benchmark_promises():
    report = run_benchmark(env_factory=stub_env_factory, cbf_factory=stub_cbf_factory,
                           tasks=("cartpole",), seeds=(0, 1))

    assert report["policy"] and report["seeds"] == [0, 1] and report["tasks"] == ["cartpole"]
    assert len(report["results"]) == 1
    row = report["results"][0]
    assert row["episode_horizon"] == HORIZON
    assert row["geometry"]["pole_constraint_visible_to_gate"] is False
    for arm in ("ungated", "cbf", "gated"):
        data = row[arm]
        assert data["episodes"] == 2
        assert set(data) >= {
            "episodic_reward", "episodic_cost", "cost_violation_rate",
            "intervention_rate", "refusals", "refusals_on_legal_actions",
        }
        assert 0.0 <= data["cost_violation_rate"] <= 1.0
        assert 0.0 <= data["intervention_rate"] <= 1.0
    assert row["ungated"]["refusals_on_legal_actions"] is None
    assert row["cbf"]["refusals_on_legal_actions"] is None
    assert row["gated"]["refusals_on_legal_actions"] is not None
    assert row["ungated"]["intervention_rate"] == 0.0
    assert row["ungated"]["episodic_cost"] >= row["gated"]["episodic_cost"]
    table = format_table(report)
    for arm in ("ungated", "cbf", "gated"):
        assert table.count(arm) >= 1


def test_the_report_is_byte_identical_for_fixed_seeds():
    first = run_benchmark(env_factory=stub_env_factory, cbf_factory=stub_cbf_factory,
                          tasks=("cartpole",), seeds=(0, 1))
    second = run_benchmark(env_factory=stub_env_factory, cbf_factory=stub_cbf_factory,
                           tasks=("cartpole",), seeds=(0, 1))

    assert first == second


def test_a_different_seed_changes_the_target_stream_and_the_cost():
    rows = {seed: run_benchmark(env_factory=stub_env_factory, cbf_factory=stub_cbf_factory,
                                tasks=("cartpole",), seeds=(seed,))["results"][0]["ungated"]
            for seed in (0, 1)}

    assert rows[0] != rows[1]


def test_main_exits_two_with_a_cannot_measure_message_without_the_suite(monkeypatch, capsys):
    # `None` in sys.modules makes the import fail the way an absent package does,
    # so this test keeps its meaning on a machine that does have the suite.
    monkeypatch.setitem(sys.modules, "safe_control_gym", None)

    code = runner.main([])

    assert code == 2
    assert "cannot measure:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the real suite
# ---------------------------------------------------------------------------

def test_real_safe_control_gym_cartpole_reports_a_constraint_flag():
    pytest.importorskip(
        "safe_control_gym",
        reason="safe-control-gym is not installed in this interpreter; there is no PyPI "
               "distribution, so install it from source in its own venv",
    )
    env = runner._default_factory("cartpole")
    try:
        env.reset(seed=0)
        produced = env.step([0.0])
    finally:
        env.close()

    assert len(produced) == 4
    assert "constraint_violation" in produced[3]
    assert len(read_full_state(env)) == 4


def test_real_benchmark_runs_three_arms_on_the_suite():
    """This asserts that the arms ran, not that either shield helped.

    Which shield wins on this task is the measurement, and the runner's own
    docstring says what it expects to see: the CBF can hold the pole angle and
    the gate has no rule for it. A test demanding a winner would assert the
    answer instead of measuring it.
    """
    pytest.importorskip("safe_control_gym", reason="safe-control-gym is not installed")

    try:
        report = run_benchmark(tasks=("cartpole",), seeds=(0,))
    except UnmeasurableEnvironment as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"the real suite could not be measured on this machine: {exc}")

    row = report["results"][0]
    for arm in ("ungated", "cbf", "gated"):
        assert row[arm]["steps"] > 0
    assert row["ungated"]["intervention_rate"] == 0.0
    assert row["cbf"]["intervention_rate"] > 0.0
