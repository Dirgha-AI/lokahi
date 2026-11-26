# -*- coding: utf-8 -*-
"""Constraint cost against a CBF shield: the comparator defect D-4 asks for.

What this measures
------------------
Whether the LOKAHI gate is better or worse than a control-barrier-function
shield. Both shields are handed the same driver's action on the same
environment, so the difference between the columns is the shield and nothing
else. Three arms:

    ungated  executes the driver's action
    cbf      hands it to safe-control-gym's own CBF safety filter through the
             suite's documented `certify_action(state, action, info)`, and
             executes the certified action
    gated    asks a LokahiVLAGate whether the action's predicted end pose stays
             outside every keep-out and inside the keep-in box, holds the cart
             still when the answer is no, and answers the next step with a
             command that undoes the last one

safe-control-gym (utiasDSL, MIT) is the only published suite that ships MPSC,
CBF and neural CBF as first-class filters and reports constraint evaluations in
`info` on every step. Its `env.step` returns four values —
`(observation, reward, done, info)` — with `info["constraint_violation"]` set to
1 on a step the suite charges and `info["constraint_values"]` carrying the raw
evaluation of each constraint (negative is safe). The cost column below is that
flag summed over an episode, which is the quantity both shields claim to move.

What the gate cannot see, stated up front
-----------------------------------------
The cartpole constraint this file configures is the suite's own state box:
cart position within ±2 m and **pole angle within ±0.5 rad**. The pole angle is
a joint-space quantity; LOKAHI's gate polices the swept segment of a point in
space, so it has no rule for it at all. The gate is therefore given the part of
the constraint it can express — the cart's position along its rail — and the
CBF is given the state box the suite built it for. That asymmetry is the
measurement, not a mistake in it: the report separates the steps charged for
the cart leaving the rail from the steps charged for the pole falling, so a
reader can see a shield that holds the pole and a gate that cannot watch it.
Any claim that this gate replaces a CBF has to survive exactly this table.

The driver is a cart-position proportional-derivative rule chasing a seeded
target sequence. It does not stabilise the pole, and it is not meant to: a
controller that leaves the pole unattended is the case a shield exists for. It
re-decides every step from where the cart actually is, in all three arms,
because a command computed for the ungated trajectory describes a pose the
gated arm never reaches.

A refusal holds rather than skipping the step, so the environment's clock keeps
running and the gated arm is never charged for a step it did not take. A hold
does not stop a cart, so the gated arm also carries a retreat command that
undoes the last step; without it the cart carries momentum deeper into what it
was just refused, the next prediction is deeper again, and no rule comparing the
end pose with the start pose can ever accept. All arms carry the rule; the other
two never see a refusal to react to.

An E_STOP latch is cleared by the harness through the gate's own `human_reset`,
because a latch that is never cleared ends the gated episode at its first
refusal and the cost column would then measure a stopped cart. The harness is
playing the named human a deployment would have, at the top of the next step.

Metrics, per task, over all seeds
---------------------------------
    episodic_reward            mean total reward per episode
    episodic_cost              mean total constraint cost per episode
    cost_violation_rate        steps charged / steps taken
    intervention_rate          cbf arm: steps whose certified action was
                               corrected / steps taken
                               gated arm: steps whose gate verdict was not
                               COMPLIANT / steps taken
    refusal_rate               cbf arm: corrections / steps taken
                               gated arm: steps the gate blocked and the
                               runner replaced with a hold / steps taken
    refusals_on_legal_actions  the gate's false-clamp measure: blocked steps whose
                               predicted cart position was still inside the
                               rail, judged by an oracle written here from the
                               suite's configured bounds and independent of both
                               shields. High here means the gate is clamping
                               steps its own geometric rule should have allowed.
                               It is blank for the CBF arm on purpose: that
                               filter corrects for the pole, which a positional
                               oracle calls legal, so scoring it would report a
                               working CBF as an over-refusing one.
    latch_resets               gated arm only: E_STOP latches the harness cleared

Usage
-----
    py -3 benchmarks/safe_control_gym_runner.py          # human table
    py -3 benchmarks/safe_control_gym_runner.py --json   # machine-readable

Nothing in the output depends on the wall clock: seeds, targets, the gate's
clock and the environment's own initialisation are all fixed.

This needs safe-control-gym in its own interpreter, because it pulls torch,
casadi, cvxpy and pybullet:

    py -3 -m venv D:/tmp/scg
    D:/tmp/scg/Scripts/python.exe -m pip install "git+https://github.com/utiasDSL/safe-control-gym.git"
    D:/tmp/scg/Scripts/python.exe benchmarks/safe_control_gym_runner.py --seeds 0 1 2

There is no `safe-control-gym` distribution on PyPI; `pip install
safe-control-gym` reports "No matching distribution found". `docs/SAFE_CONTROL_GYM.md`
records what that install attempt did on this machine and why no numbers from
this file exist yet. The extra is a benchmark dependency only: the runtime
package still has none.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import random
import sys
from functools import partial
from typing import Any, Callable, NamedTuple, Sequence

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import LokahiVLAGate
from vla.vla_gate import E_STOP, VERDICT_COMPLIANT

TASKS = ("cartpole",)
SEEDS = (0, 1, 2)
CTRL_FREQ = 25
EPISODE_LEN_SEC = 5.0
CONTROL_PERIOD_MS = 1000.0 / CTRL_FREQ
PERIOD_NS = int(CONTROL_PERIOD_MS * 1e6)
T0_NS = 1_000_000_000_000
# The gate thinks in three dimensions and the cart moves along one. Lifting the
# cart onto the middle of the arena's vertical span keeps the gate's tool sphere
# clear of the floor and ceiling planes it would otherwise graze at z=0, and the
# rail is a narrow box in y so nothing but the rail can ever trip the box.
PLANE_Z = 0.5
RAIL_HALF_WIDTH_M = 0.5
# The suite's own state box, set here rather than inherited so that the runner
# polices the constraint it configured. Upper and lower bounds are the cartpole
# config's schema; the pole bound is what the CBF's superlevel set is built on.
CART_BOUND_M = 2.0
POLE_BOUND_RAD = 0.5
TASK_CONFIG = {
    "seed": 0,
    "ctrl_freq": CTRL_FREQ,
    "pyb_freq": 500,
    "episode_len_sec": EPISODE_LEN_SEC,
    "cost": "quadratic",
    "task": "stabilization",
    "task_info": {"stabilization_goal": [0.0, 0.0], "stabilization_goal_tolerance": 0.05},
    # A violating step is charged and the episode continues, so cost accumulates
    # the way the Safety-Gymnasium suite's does. Termination on the pole leaving
    # the suite's own 90° out-of-bounds limit still bounds every episode.
    "done_on_violation": False,
    "done_on_out_of_bound": True,
    "constraints": [
        {
            "constraint_form": "default_constraint",
            "constrained_variable": "state",
            "upper_bounds": [CART_BOUND_M, 2, POLE_BOUND_RAD, 2],
            "lower_bounds": [-CART_BOUND_M, -2, -POLE_BOUND_RAD, -2],
        },
        {"constraint_form": "default_constraint", "constrained_variable": "input"},
    ],
}
SF_CONFIG = {
    "slope": 0.1,
    "soft_constrained": True,
    "slack_weight": 10000.0,
    "slack_tolerance": 1.0e-3,
}
POLICY = "rail-pd-driver-v1"
# Steps without getting closer to a target before it is abandoned for the next.
# The rule reads only the distance travelled, so every arm applies it identically.
STALL_STEPS = 10
# Below this the CBF's certified action is the action it was given: the suite's
# own experiment uses the same threshold on the same recorded correction.
CORRECTION_EPS = 1e-6
ARMS = ("ungated", "cbf", "gated")


class Geometry(NamedTuple):
    """What the runner needs to know about the rail, in the environment's metres."""

    arena: tuple[float, float, float, float]          # xmin, ymin, xmax, ymax
    robot_radius_m: float
    step_scale_m: float                               # metres per unit action at cruise
    action_low: tuple[float, ...]
    action_high: tuple[float, ...]
    keep_in: bool = True


class UnmeasurableEnvironment(RuntimeError):
    """The environment or the filter did not expose what this benchmark needs."""


# ---------------------------------------------------------------------------
# geometry: the gate's view, and the independent oracle
# ---------------------------------------------------------------------------

def next_position(geometry: Geometry, position: Sequence[float], action: Sequence[float],
                  previous_delta: Sequence[float] | None = None) -> tuple[float, float]:
    """Where the cart will be one step after `action`.

    A cartpole action is a force, not a displacement, so the prediction is the
    step the cart just took — not a force-scaled guess, which drifts by enough
    to matter: the gate would validate a pose the cart never occupies. Where the
    finite difference is unavailable (the first step, or a step that was held)
    the prediction falls back to the measured `step_scale`, because a zero
    prediction would let the gate approve any command at all.
    """
    if (previous_delta is None
            or math.hypot(float(previous_delta[0]), float(previous_delta[1])) < 1e-9):
        return (position[0] + geometry.step_scale_m * float(action[0]), position[1])
    return (position[0] + float(previous_delta[0]), position[1] + float(previous_delta[1]))


def action_is_legal(geometry: Geometry, end: Sequence[float]) -> bool:
    """Ground truth for "would the environment have charged this step for position".

    The oracle is the rail itself: the configured cart-position bound, less the
    tool radius, against the predicted end pose. It is written from the bounds
    this runner configures, not from either shield, and it deliberately judges
    one part of the constraint. The pole angle is outside what a position can
    express, so no refusal is ever counted false for protecting it.
    """
    radius = geometry.robot_radius_m
    arena = (geometry.arena[0] + radius, geometry.arena[1] + radius,
             geometry.arena[2] - radius, geometry.arena[3] - radius)
    return arena[0] <= end[0] <= arena[2] and arena[1] <= end[1] <= arena[3]


def manifest_for(geometry: Geometry) -> dict:
    """The gate's configuration, read straight off the rail being measured.

    Kinematic and freshness limits are left out of the way on purpose: the only
    gate rule this benchmark varies is the geometric one, so the cost difference
    it reports is attributable to that rule alone. There are no keep-outs on a
    cartpole rail — the constraint is a keep-in box — so the keep-out list is
    empty and the box is the whole hazard model.
    """
    return {
        "node": "robot:safe-control-gym",
        "physical_envelope": {"max_contact_force_n": 1.0e9, "max_joint_torque_nm": 1.0e9},
        "physical_vla": {
            "max_action_age_ms": 1_000.0,
            "watchdog_timeout_ms": 1.0e9,
            "max_gate_eval_us": 1.0e9,
            "control_period_ms": CONTROL_PERIOD_MS,
            "keep_out_aabbs": [],
            "tool_radius_m": geometry.robot_radius_m,
            "kinematic_limits": {},
            "workspace_aabb": [
                geometry.arena[0], geometry.arena[1], 0.0,
                geometry.arena[2], geometry.arena[3], 1.0,
            ],
        },
    }


# ---------------------------------------------------------------------------
# reading an environment
# ---------------------------------------------------------------------------

def read_full_state(env: Any) -> list[float]:
    """The environment's own state vector, which the CBF filter is handed.

    A harness that is not safe-control-gym may answer through `lokahi_state()`.
    """
    hook = getattr(env, "lokahi_state", None)
    if callable(hook):
        return [float(value) for value in hook()]
    state = getattr(env, "state", None)
    if state is None:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__} exposes neither .lokahi_state() nor .state"
        )
    return [float(value) for value in state]


def read_state(env: Any) -> tuple[float, float]:
    """The cart's live position along its rail. A harness may answer through
    `env.lokahi_pose()`, returning `{"x", "y"}`."""
    hook = getattr(env, "lokahi_pose", None)
    if callable(hook):
        state = hook()
        return float(state["x"]), float(state.get("y", 0.0))
    return read_full_state(env)[0], 0.0


def action_bounds(env: Any) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The force the suite accepts, from its own `physical_action_bounds`."""
    bounds = getattr(env, "physical_action_bounds", None)
    if bounds is not None and len(bounds) == 2:
        return (tuple(float(value) for value in bounds[0]),
                tuple(float(value) for value in bounds[1]))
    space = getattr(env, "action_space", None)
    low = getattr(space, "low", None)
    high = getattr(space, "high", None)
    if low is None or high is None:
        return (-10.0,), (10.0,)
    return tuple(float(value) for value in low), tuple(float(value) for value in high)


def horizon_of(env: Any) -> int:
    """Steps in an episode, from the suite's own two config values."""
    length = getattr(env, "EPISODE_LEN_SEC", None)
    frequency = getattr(env, "CTRL_FREQ", None)
    if length and frequency:
        return int(round(float(length) * float(frequency)))
    sources = (env, getattr(env, "spec", None), getattr(getattr(env, "unwrapped", None), "spec", None))
    for source in sources:
        if source is None:
            continue
        for attribute in ("max_episode_steps", "horizon"):
            limit = getattr(source, attribute, None)
            if isinstance(limit, int) and limit > 0:
                return limit
    raise UnmeasurableEnvironment(f"{type(env).__name__} reports no episode horizon")


def measure_step_scale(env: Any, warmup: int = 20) -> float:
    """Metres travelled in one step per unit of action.

    The largest single step seen over `warmup` steps under a unit action, because
    a cart accelerates: the first step from rest covers a fraction of a
    millimetre while the cruise step covers the distance the gate has to plan
    around. Per unit of action rather than at saturation, so the prediction's
    `step_scale * action` means what it says on a force whose bound is not 1.
    """
    action = [1.0]
    env.reset(seed=0)
    travelled = []
    for _ in range(warmup):
        before, _ = read_state(env)
        step_once(env, action)
        after, _ = read_state(env)
        travelled.append(abs(after - before))
    scale = max(travelled, default=0.0)
    if scale <= 0.0:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__} did not move under a unit action; no step scale to measure"
        )
    return scale


# ---------------------------------------------------------------------------
# the driver: a rail PD, recorded once and replayed by every arm
# ---------------------------------------------------------------------------

def _rng(task: str, seed: int, label: str) -> random.Random:
    digest = hashlib.sha256(f"{POLICY}/{task}/{seed}/{label}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def targets(task: str, seed: int, geometry: Geometry) -> list[float]:
    """Where the driver tries to put the cart.

    A third of the targets sit just beyond each end of the rail, because a driver
    that chases a position it is not allowed to reach is the case a shield exists
    for: the ungated arm drives off the rail and is charged, and both shields are
    asked to stop it. The rest are drawn inside the rail, so the arms also differ
    on ordinary ground. The driver ignores the pole, which is why it topples.
    """
    rng = _rng(task, seed, "targets")
    xmin, xmax = geometry.arena[0], geometry.arena[2]
    inner = 0.1 * (xmax - xmin)
    beyond = (xmin - inner, xmax + inner)
    return [beyond[index % 2] if index % 3 == 0 else rng.uniform(xmin + inner, xmax - inner)
            for index in range(240)]


def steer(position: float, target: float, previous_delta: Sequence[float] | None,
          geometry: Geometry) -> list[float]:
    """One force towards `target`, in the environment's own action space.

    A proportional term on the rail error with a derivative on the step the cart
    just took, both written as a desired displacement in metres and divided by
    the measured step so the rule reads the same on any cart. Saturation is what
    makes a shield necessary: chasing a target beyond the rail pins the force at
    the suite's limit, and nothing in this rule knows where the rail ends.
    """
    scale = geometry.step_scale_m or 1.0
    previous = float(previous_delta[0]) if previous_delta is not None else 0.0
    desired = 0.6 * (target - position) - 0.5 * previous
    return [round(max(geometry.action_low[0], min(geometry.action_high[0], desired / scale)), 6)]


def retreat_command(geometry: Geometry, previous_delta: Sequence[float] | None) -> list[float]:
    """The command a controller gives when a shield says no: undo the last step."""
    if previous_delta is None or abs(float(previous_delta[0])) < 1e-9:
        return [0.0 for _ in geometry.action_low]
    requested = -float(previous_delta[0]) / geometry.step_scale_m
    return [round(max(geometry.action_low[0], min(geometry.action_high[0], requested)), 6)]


# ---------------------------------------------------------------------------
# one episode
# ---------------------------------------------------------------------------

def step_once(env: Any, action: Sequence[float]) -> tuple[float, float, bool]:
    """Step the environment and return (reward, cost, done).

    The cost is the suite's own verdict for the step: `info["constraint_violation"]`
    is 1 exactly on the steps its constraint evaluator charges. An environment
    that does not report it cannot be scored here, and saying so is better than
    scoring every step zero.
    """
    produced = env.step(list(action))
    if not isinstance(produced, (tuple, list)) or len(produced) != 4:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__}.step() returned {type(produced).__name__} of "
            f"{len(produced) if isinstance(produced, (tuple, list)) else 1} values; this "
            "benchmark needs the four-value (observation, reward, done, info) form that "
            "safe-control-gym uses, with constraint evaluations in info"
        )
    _, reward, done, info = produced
    if not isinstance(info, dict) or "constraint_violation" not in info:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__}.step() returned no info['constraint_violation']; "
            "this benchmark scores the suite's own constraint flag"
        )
    return float(reward), float(info["constraint_violation"]), bool(done)


def run_episode(env: Any, geometry: Geometry, targets_: Sequence[float], *,
                arm: str, horizon: int, cbf_filter: Any = None) -> dict:
    """Drive to the targets under one arm and tally what the environment charged."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm!r}")
    tally = {
        "steps": 0,
        "reward": 0.0,
        "cost": 0.0,
        "cost_violating_steps": 0,
        "interventions": 0,
        "refusals": 0,
        "refusals_on_legal_actions": 0,
        "latch_resets": 0,
    }
    # Bench manifest, no signing key in this process: the opt-out is explicit.
    gate = (
        LokahiVLAGate.from_manifest(manifest_for(geometry), allow_unverified_manifest=True)
        if arm == "gated" else None
    )
    index = 0
    closest = math.inf
    stalled = 0
    retreating = False
    previous_delta: Sequence[float] | None = None
    while index < len(targets_) and tally["steps"] < horizon:
        position = read_state(env)[0]
        distance = abs(position - targets_[index])
        if distance < 0.2:
            index += 1
            closest, stalled = math.inf, 0
            if index >= len(targets_):
                break
            continue
        if distance < closest - 1e-4:
            closest, stalled = distance, 0
        else:
            stalled += 1
            if stalled > STALL_STEPS:
                index += 1
                closest, stalled = math.inf, 0
                if index >= len(targets_):
                    break
                continue
        action = (retreat_command(geometry, previous_delta) if retreating
                  else steer(position, targets_[index], previous_delta, geometry))
        predicted = next_position(geometry, (position, 0.0), action, previous_delta)
        executed: Sequence[float] = action
        if gate is not None:
            now_ns = T0_NS + tally["steps"] * PERIOD_NS
            decision = gate.evaluate_step(
                obs_ts_ns=now_ns,
                now_ts_ns=now_ns,
                robot_state={"ee_pos": [position, 0.0, PLANE_Z]},
                action_chunk=[{"ee_pos": [predicted[0], predicted[1], PLANE_Z],
                               "duration_ms": CONTROL_PERIOD_MS}],
                # 1e9 sentinel ceiling and cost-based contact scoring: see
                # safety_gymnasium_runner for the same reasoning.
                wrench={"force_n": 0.0, "torque_nm": 0.0},
            )
            if decision.verdict != VERDICT_COMPLIANT:
                tally["interventions"] += 1
            if not decision.allowed or decision.clamped_from_index == 0:
                tally["refusals"] += 1
                if action_is_legal(geometry, predicted):
                    tally["refusals_on_legal_actions"] += 1
                executed = [0.0] * len(action)
                retreating = True
            else:
                retreating = False
            if decision.state == E_STOP:
                gate.human_reset("benchmark-harness", now_ts_ns=now_ns)
                tally["latch_resets"] += 1
        elif cbf_filter is not None:
            import numpy as np

            certified, feasible = cbf_filter.certify_action(
                np.array(read_full_state(env), dtype=float),
                np.array(action, dtype=float),
            )
            certified = [float(value) for value in np.atleast_1d(certified)]
            corrected = math.dist(certified, list(action)) > CORRECTION_EPS
            if not feasible:
                # An infeasible QP has no certified action to trust; the suite's
                # own experiment plots the uncertified input there, so the step
                # is held rather than executed on a guess.
                tally["interventions"] += 1
                tally["refusals"] += 1
                certified = [0.0] * len(action)
            elif corrected:
                tally["interventions"] += 1
                tally["refusals"] += 1
            executed = certified
        reward, cost, done = step_once(env, executed)
        observed = read_state(env)[0]
        previous_delta = (observed - position, 0.0)
        tally["steps"] += 1
        tally["reward"] += reward
        tally["cost"] += cost
        if cost > 0.0:
            tally["cost_violating_steps"] += 1
        if done:
            break
    return tally


# ---------------------------------------------------------------------------
# the benchmark
# ---------------------------------------------------------------------------

def _aggregate(episodes: Sequence[dict], arm: str) -> dict:
    steps = sum(episode["steps"] for episode in episodes)
    violating = sum(episode["cost_violating_steps"] for episode in episodes)
    refusals = sum(episode["refusals"] for episode in episodes)
    return {
        "episodes": len(episodes),
        "steps": steps,
        "episodic_reward": _mean([episode["reward"] for episode in episodes]),
        "episodic_cost": _mean([episode["cost"] for episode in episodes]),
        "cost_violation_rate": violating / steps if steps else 0.0,
        "cost_violating_steps": violating,
        "intervention_rate": (sum(episode["interventions"] for episode in episodes) / steps
                              if steps else 0.0),
        "refusals": refusals,
        "refusal_rate": refusals / steps if steps else 0.0,
        # The false-clamp measure is the gate's: it asks whether the gate
        # refused a step the environment would not have charged for. A CBF
        # corrects rather than refuses, and corrects for the pole, which a
        # positional oracle calls legal by construction — scoring it there would
        # report a working CBF as an over-refusing one.
        "refusals_on_legal_actions": (sum(episode["refusals_on_legal_actions"] for episode in episodes)
                                      if arm == "gated" else None),
        "latch_resets": sum(episode["latch_resets"] for episode in episodes),
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _default_factory(task: str) -> Any:
    try:
        # The suite prints dependency notices on import; they belong on stderr so
        # that `--json` stays parseable.
        with contextlib.redirect_stdout(sys.stderr):
            from safe_control_gym.utils.registration import make
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise UnmeasurableEnvironment(
            "safe-control-gym is not installed in this interpreter; there is no PyPI "
            "distribution, so install it from source with `pip install "
            "git+https://github.com/utiasDSL/safe-control-gym.git` in a venv"
        ) from exc
    try:
        return make(task, **TASK_CONFIG)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise UnmeasurableEnvironment(f"safe_control_gym make({task!r}) failed: {exc}") from exc


def _cbf_factory(task: str) -> Any:
    """The suite's own CBF safety filter, built the way its own example builds it."""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from safe_control_gym.utils.registration import make
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise UnmeasurableEnvironment(
            "safe-control-gym is not installed in this interpreter; the CBF column is "
            "the comparison and cannot be run without it"
        ) from exc
    try:
        safety_filter = make("cbf", partial(make, task, **TASK_CONFIG), **SF_CONFIG)
        safety_filter.reset()
        return safety_filter
    except Exception as exc:  # pragma: no cover - environment dependent
        raise UnmeasurableEnvironment(f"safe_control_gym make('cbf', {task!r}) failed: {exc}") from exc


def run_benchmark(*, env_factory: Callable[[str], Any] | None = None,
                  cbf_factory: Callable[[str], Any] | None = None,
                  tasks: Sequence[str] = TASKS,
                  seeds: Sequence[int] = SEEDS) -> dict:
    """Run all three arms on every task and seed, and return the report as a dict."""
    make_env = env_factory if env_factory is not None else _default_factory
    make_cbf = cbf_factory if cbf_factory is not None else _cbf_factory
    rows = []
    for task in tasks:
        # One environment per task, reseeded per episode: both arms start from the
        # same state for the same seed, which is what makes the columns comparable.
        env = make_env(task)
        try:
            env.reset(seed=min(seeds))
            horizon = horizon_of(env)
            low, high = action_bounds(env)
            geometry = Geometry(
                arena=(-CART_BOUND_M, -RAIL_HALF_WIDTH_M, CART_BOUND_M, RAIL_HALF_WIDTH_M),
                robot_radius_m=0.0,
                step_scale_m=measure_step_scale(env),
                action_low=low,
                action_high=high,
            )

            arms: dict[str, list[dict]] = {arm: [] for arm in ARMS}
            for seed in seeds:
                target_list = targets(task, seed, geometry)
                for arm in ARMS:
                    env.reset(seed=seed)
                    cbf_filter = make_cbf(task) if arm == "cbf" else None
                    try:
                        arms[arm].append(run_episode(env, geometry, target_list, arm=arm,
                                                     horizon=horizon, cbf_filter=cbf_filter))
                    finally:
                        # Each filter builds an environment of its own, and its
                        # recorded results are per-instance: one per episode.
                        close_filter = getattr(cbf_filter, "close", None)
                        if callable(close_filter):
                            close_filter()
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()

        aggregated = {arm: _aggregate(arms[arm], arm) for arm in ARMS}
        rows.append({
            "task": task,
            "seeds": list(seeds),
            "episode_horizon": horizon,
            "geometry": {
                "rail_m": [-CART_BOUND_M, CART_BOUND_M],
                "cart_bound_m": CART_BOUND_M,
                "pole_bound_rad": POLE_BOUND_RAD,
                "step_scale_m": geometry.step_scale_m,
                "position_constraint_visible_to_gate": True,
                "pole_constraint_visible_to_gate": False,
            },
            **aggregated,
            "cost_reduction_cbf": aggregated["ungated"]["episodic_cost"] - aggregated["cbf"]["episodic_cost"],
            "cost_reduction_gate": aggregated["ungated"]["episodic_cost"] - aggregated["gated"]["episodic_cost"],
            "cost_reduction_cbf_pct": _percent(aggregated["ungated"]["episodic_cost"],
                                               aggregated["cbf"]["episodic_cost"]),
            "cost_reduction_gate_pct": _percent(aggregated["ungated"]["episodic_cost"],
                                                aggregated["gated"]["episodic_cost"]),
        })
    return {
        "suite": "safe-control-gym (utiasDSL, MIT): CBF safety filter against the LOKAHI gate",
        "measures": ("two shields on one driver: the suite's own CBF, and the gate. Both "
                     "arms replay one seeded target sequence; the gate has no rule for the "
                     "pole angle and is given the cart's rail position only"),
        "policy": POLICY,
        "seeds": list(seeds),
        "tasks": list(tasks),
        "results": rows,
    }


def _percent(baseline: float, shielded: float) -> float | None:
    if baseline <= 0.0:
        return None
    return 100.0 * (baseline - shielded) / baseline


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def format_table(report: dict) -> str:
    header = (
        f"{'task':<14}{'arm':<9}{'rew/ep':>10}{'cost/ep':>9}{'cost steps':>12}"
        f"{'interv':>8}{'refus':>7}{'false':>7}"
    )
    lines = [header, "-" * len(header)]
    for row in report["results"]:
        for arm in ARMS:
            data = row[arm]
            false_clamps = data["refusals_on_legal_actions"]
            lines.append(
                f"{row['task']:<14}{arm:<9}"
                f"{data['episodic_reward']:>10.3f}{data['episodic_cost']:>9.3f}"
                f"{data['cost_violating_steps']:>12d}"
                f"{data['intervention_rate']:>8.3f}{data['refusal_rate']:>7.3f}"
                f"{'-' if false_clamps is None else f'{false_clamps:>7d}'}"
            )
        for arm, label in (("cbf", "cbf delta"), ("gated", "gate delta")):
            reduction = row[f"cost_reduction_{'cbf' if arm == 'cbf' else 'gate'}"]
            percent = row[f"cost_reduction_{'cbf' if arm == 'cbf' else 'gate'}_pct"]
            lines.append(
                f"{'':<14}{label:<9}{'':>10}{-reduction:>9.3f}"
                + (f"   ({percent:+.1f}% episodic cost)" if percent is not None else
                   "   (no ungated cost to reduce)")
            )
        if row["gated"]["latch_resets"]:
            lines.append(f"{'':<14}{'':<9}{row['gated']['latch_resets']} e-stop latches cleared")
        lines.append("")
    lines.append(
        "columns: rew/ep and cost/ep are means over episodes; cost steps is the total "
        "number of steps the suite\ncharged; interv and refus are per-step rates; false is "
        "the gate's refusals on steps whose predicted\ncart position was still on the rail, "
        "by an oracle independent of both shields, and is blank for\nthe arms that measure "
        "does not apply to. The pole angle is not in that oracle: the\ngate has no rule for "
        "it, and the CBF's corrections are for it, so a working CBF would score as a false "
        "clamp there."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--tasks", nargs="*", default=list(TASKS),
                        help=f"tasks to run (default: {' '.join(TASKS)})")
    parser.add_argument("--seeds", nargs="*", type=int, default=list(SEEDS),
                        help=f"seeds to run (default: {' '.join(str(seed) for seed in SEEDS)})")
    args = parser.parse_args(argv)

    try:
        report = run_benchmark(tasks=args.tasks, seeds=args.seeds)
    except UnmeasurableEnvironment as exc:
        print(f"cannot measure: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(format_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
