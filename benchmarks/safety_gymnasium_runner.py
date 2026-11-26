# -*- coding: utf-8 -*-
"""Constraint cost on Safety-Gymnasium: the same actions, with and without the gate.

What this measures
------------------
The gate, not the policy. Both arms run one policy — the same seeded waypoints,
the same steering rule, the same environment seed — so any difference in cost
between them is caused by the gate and nothing else. The driver is a waypoint
follower that aims at hazards on purpose for a third of its targets, because a
policy that ignores hazards is the case a shield exists for. It re-decides every
step from where the robot actually is, in both arms, because a command computed
for the ungated trajectory describes a pose the gated arm never reaches: replaying
one open-loop would leave the robot held against the same hazard all episode and
call that a cost reduction. It is a cheap driver and not a claim about any
controller: the reward column exists so a reader can see what the gate cost in
task progress.

Safety-Gymnasium (PKU-Alignment, Apache-2.0, NeurIPS 2023 Datasets and
Benchmarks) is the published suite this runs on. Its `env.step` returns six values,
`(observation, reward, cost, terminated, truncated, info)`, where `cost` is that
step's constraint violation — exactly the quantity this repository claims to
reduce.

Two arms, one driver
--------------------
    ungated  executes the policy action
    gated    asks a LokahiVLAGate whether the action's swept segment stays
             inside the arena and clear of every hazard, holds still (a zero
             action) when the answer is no, and answers the next step with a
             retreat command that undoes the last one

A refusal holds rather than skipping the step, so the environment's own clock
keeps running, hazards that move keep moving, and the gated arm is never charged
for a step it did not take. That is why the gate is called directly instead of
through `vla.adapters.VLAGatedEnv`: the wrapper deliberately does not step an
environment it has refused, which is right for a real actuator and wrong for a
benchmark that needs the two arms aligned in time.

The retreat matters most for the car: a hold does not stop a car, so momentum
carries it deeper into the keep-out it was just refused entry to, the next
prediction is deeper again, and no rule that compares the end pose with the start
pose can ever accept. A controller that brakes when the shield refuses is what
makes the refusal actionable. Both arms carry the rule; the ungated arm never sees
a refusal to react to.

An E_STOP latch is cleared by the harness through the gate's own `human_reset`,
because a latch that is never cleared ends the gated episode at its first refusal
and the cost column would then measure a stopped robot. The harness is playing the
named human a deployment would have, at the top of the next step.

The gate's model of the robot, stated because the numbers depend on it:

    next = position + (the step just taken) scaled by the change in the command

Both robots are acceleration-driven, so an action is not a displacement and a
command-scaled guess drifts by enough to matter: the gate would approve a pose the
robot never occupies and the robot would edge into a keep-out the gate had not
seen it enter. Where the finite difference is unavailable — the first step, or a
step the gate held — the prediction falls back to the measured `step_scale`. For
the car this is also where the heading comes from: its orientation matrix is in a
frame whose forward axis is not the one a reader expects (a car travelling along
+x reported body axes at 95° and -154°, and no reading of that matrix agreed with
the direction it travelled), so the direction it actually travelled is used.

The keep-in box is offered to the gate only when it is a physical wall. The suite
reports placement extents, and the point robots' walls are at that limit, but the
car has no walls: driven from its reset pose it left the 3 m box and reached 20 m
out. A probe drives at the boundary in four directions and finds out, rather than
this file assuming a task name.

One thing this harness does not do is give the gate an escape. LOKAHI refuses any
segment that starts inside a keep-out, so a robot that is once inside one cannot
be commanded out of it: every later step is refused and the episode is spent
inside the hazard. That is the gate's own behaviour and the report is meant to show
it, not to paper over it.

Metrics, per task, over all seeds
---------------------------------
    episodic_reward            mean total reward per episode
    episodic_cost              mean total constraint cost per episode
    cost_violation_rate        steps with cost > 0 / steps taken
    intervention_rate          gated arm only: steps whose gate verdict was
                               not COMPLIANT / steps taken
    refusal_rate               gated arm only: steps the gate blocked and the
                               runner replaced with a hold / steps taken
    latch_resets               gated arm only: E_STOP latches the harness cleared
                               through the gate's named-human path, so an episode
                               continues after the gate trips
    refusals_on_legal_actions  the false-clamp measure: refusals on steps whose
                               action would have been charged nothing, judged by
                               an oracle written here from the suite's own cost
                               rule and independent of the gate. High here means
                               the gate is clamping harmless actions.

Usage
-----
    py -3 benchmarks/safety_gymnasium_runner.py          # human table
    py -3 benchmarks/safety_gymnasium_runner.py --json   # machine-readable

Nothing in the output depends on the wall clock: seeds, waypoints, the gate's
clock and the environment's own initialisation are all fixed. Requires the
`safety-gymnasium` extra (`pip install lokahi[safety-gymnasium]`), which is a
benchmark dependency only — the runtime package still has none.
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
from typing import Any, Callable, NamedTuple, Sequence

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import LokahiVLAGate
from vla.vla_gate import E_STOP, VERDICT_COMPLIANT

TASKS = ("SafetyPointGoal1-v0", "SafetyPointButton1-v0", "SafetyCarGoal1-v0")
SEEDS = (0, 1, 2)
CONTROL_PERIOD_MS = 20.0
PERIOD_NS = int(CONTROL_PERIOD_MS * 1e6)
T0_NS = 1_000_000_000_000
PROBE_SEED = 999
# Steps without getting closer to a waypoint before it is abandoned for the next
# one. The rule reads only the distance travelled, so both arms apply it
# identically; without it a robot whose straight path is blocked pushes at the
# same waypoint until the episode ends, and the cost it never pays is a stalled
# robot rather than a protected one.
STALL_STEPS = 10
POLICY = "uniform-waypoint-driver-v1"
# The gate thinks in three dimensions and these robots move in two. Lifting the
# pose onto the middle of the arena's vertical span keeps the gate's tool sphere
# clear of the floor and ceiling planes it would otherwise graze at z=0.
PLANE_Z = 0.5


class Geometry(NamedTuple):
    """What the runner needs to know about an arena, in the environment's metres."""

    arena: tuple[float, float, float, float]          # xmin, ymin, xmax, ymax
    hazards: tuple[tuple[float, float, float], ...]   # centre x, centre y, half-width
    robot_radius_m: float
    motion: str                                       # "omni" or "car"
    step_scale_m: float                               # metres travelled per unit action
    action_low: tuple[float, ...]
    action_high: tuple[float, ...]
    keep_in: bool = True                              # is the arena a physical wall?


class UnmeasurableEnvironment(RuntimeError):
    """The environment did not expose the geometry this benchmark needs."""


# ---------------------------------------------------------------------------
# geometry: the gate's view, and the independent oracle
# ---------------------------------------------------------------------------

def _drive(action: Sequence[float], motion: str) -> tuple[float, float]:
    """The command as (x speed, y speed) in the robot's own moving frame.

    Neither robot takes a displacement as its action: the point robots take two
    accelerations and the car takes a steering angle and a throttle. For the car
    the throttle is the second component, and the steering angle moves nothing by
    itself over one step, so the finite difference carries the whole prediction.
    """
    if motion == "car":
        return (float(action[1]), 0.0)
    return (float(action[0]), float(action[1]))


def next_position(geometry: Geometry, position: Sequence[float], heading: float | None,
                  action: Sequence[float], previous_delta: Sequence[float] | None = None,
                  previous_action: Sequence[float] | None = None) -> tuple[float, float]:
    """Where the robot will be one step after `action`.

    The prediction is the step the robot just took, scaled by how the command's
    driving component changed. Both robots are acceleration-driven, so an action is
    not a displacement, and a command-scaled kinematic guess drifts by enough to
    matter: the gate would validate a pose the robot never occupies and the robot
    would edge into a keep-out it had not seen it enter.

    For the car this is also where the heading comes from. Its orientation matrix
    is in a frame whose forward axis is not the one a reader expects — measured, a
    car travelling along +x reported body axes at 95° and -154° — and no reading of
    that matrix agreed with the direction it actually travelled. The direction
    travelled is the quantity the gate needs and the only one that cannot be wrong.
    """
    scale = geometry.step_scale_m
    if (previous_delta is None or previous_action is None
            or math.hypot(float(previous_delta[0]), float(previous_delta[1])) < 1e-9):
        # A robot that did not move last step — a held step, or the first step of
        # an episode — has no finite difference to extrapolate, and a zero
        # prediction would let the gate approve any command at all.
        x_speed, y_speed = _drive(action, geometry.motion)
        if geometry.motion == "car":
            if heading is None:
                raise UnmeasurableEnvironment("car task reported no heading")
            return (position[0] + scale * x_speed * math.cos(heading),
                    position[1] + scale * x_speed * math.sin(heading))
        return (position[0] + scale * x_speed, position[1] + scale * y_speed)
    x_before, y_before = _drive(previous_action, geometry.motion)
    x_after, y_after = _drive(action, geometry.motion)
    before = math.hypot(x_before, y_before)
    if before < 1e-9:
        ratio = 1.0
    else:
        ratio = (x_after * x_before + y_after * y_before) / (before * before)
        ratio = max(-2.0, min(2.0, ratio))
    return (position[0] + ratio * float(previous_delta[0]),
            position[1] + ratio * float(previous_delta[1]))


def _inside(point: Sequence[float], box: Sequence[float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def action_is_legal(geometry: Geometry, end: Sequence[float]) -> bool:
    """Ground truth for "would this step have been charged a cost".

    Safety-Gymnasium charges an obstacle when the agent's centre finishes inside
    that obstacle's radius, so the oracle is the end pose against each hazard
    disc — not a swept segment, and not the bounding square the gate is given.
    The arena bound is part of it because a pose outside the arena is not one the
    environment can occupy at all.

    Where this disagrees with the gate, the disagreement is real and is what the
    false-clamp column reports: the gate sees a hazard as its bounding square and
    judges the sweep, so it refuses some steps the environment would not have
    charged for.
    """
    radius = geometry.robot_radius_m
    arena = (geometry.arena[0] + radius, geometry.arena[1] + radius,
             geometry.arena[2] - radius, geometry.arena[3] - radius)
    if not _inside(end, arena):
        return False
    return all(math.dist(end, (cx, cy)) > half + radius for cx, cy, half in geometry.hazards)


def manifest_for(geometry: Geometry, keep_out: str = "circle") -> dict:
    """The gate's configuration, read straight off the arena being measured.

    Kinematic and freshness limits are left out of the way on purpose: the only
    gate rule this benchmark varies is the geometric one, so the cost difference
    it reports is attributable to that rule alone. The keep-in box is left out
    entirely when the arena has no walls — the manifest parser rejects a
    `workspace_aabb` of null, and an absent box is what "no keep-in rule" means.
    """
    section: dict[str, Any] = {
        "max_action_age_ms": 1_000.0,
        "watchdog_timeout_ms": 1.0e9,
        "max_gate_eval_us": 1.0e9,
        "control_period_ms": CONTROL_PERIOD_MS,
        "keep_out_aabbs": [],
        "tool_radius_m": geometry.robot_radius_m,
        "kinematic_limits": {},
    }
    # The environment charges on the hazard disc, so the disc is what the gate
    # is given by default. `box` is kept for the comparison: a box around a disc
    # is up to 27% too large on the diagonal, and that shows up as refusals the
    # environment would never have charged for.
    if keep_out == "box":
        section["keep_out_aabbs"] = [
            [cx - half, cy - half, 0.0, cx + half, cy + half, 1.0]
            for cx, cy, half in geometry.hazards
        ]
    elif keep_out == "circle":
        section["keep_out_shapes"] = [
            {"shape": "circle", "centre": [cx, cy], "radius": half}
            for cx, cy, half in geometry.hazards
        ]
    else:
        raise ValueError(f"unknown keep-out kind: {keep_out!r}")
    if geometry.keep_in:
        section["workspace_aabb"] = [
            geometry.arena[0], geometry.arena[1], 0.0,
            geometry.arena[2], geometry.arena[3], 1.0,
        ]
    return {
        "node": "robot:safety-gymnasium",
        "physical_envelope": {"max_contact_force_n": 1.0e9, "max_joint_torque_nm": 1.0e9},
        "physical_vla": section,
    }


# ---------------------------------------------------------------------------
# reading an environment
# ---------------------------------------------------------------------------

def read_state(env: Any) -> tuple[tuple[float, float], float | None]:
    """The robot's live pose. A harness that is not Safety-Gymnasium may answer
    through `env.lokahi_pose()`, returning `{"x", "y", "heading"}`."""
    hook = getattr(env, "lokahi_pose", None)
    if callable(hook):
        state = hook()
        return (float(state["x"]), float(state["y"])), state.get("heading")
    agent = getattr(getattr(env, "task", None), "agent", None)
    if agent is None:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__} exposes neither .lokahi_pose() nor .task.agent"
        )
    position = agent.pos
    heading = None
    matrix = getattr(agent, "mat", None)
    if matrix is not None and len(matrix) >= 2:
        matrix = [list(row) for row in matrix]
        heading = math.atan2(matrix[1][0], matrix[0][0])
    return (float(position[0]), float(position[1])), heading


def arena_facts(env: Any) -> dict:
    """Static arena facts: the keep-in box, the chargeable obstacles, and what was
    left out. A harness may answer through `env.lokahi_geometry()` instead.

    Safety-Gymnasium charges cost from an obstacle when the agent's centre comes
    within that obstacle's own radius, so a `Hazards` disc of radius 0.2 is
    reproduced exactly by a keep-out box of half-width 0.2 and a tool radius of
    zero. Obstacles with a cost rule this box cannot express — moving gremlins,
    buttons with latch logic — are named and reported rather than guessed at, and
    their cost is untouched in both arms.
    """
    hook = getattr(env, "lokahi_geometry", None)
    if callable(hook):
        facts = dict(hook())
        facts.setdefault("unmodelled", ())
        return facts
    task = getattr(env, "task", None)
    agent = getattr(task, "agent", None)
    extents = getattr(getattr(task, "placements_conf", None), "extents", None)
    if agent is None or extents is None:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__} exposes neither .lokahi_geometry() nor "
            ".task.placements_conf.extents"
        )
    hazards: list[tuple[float, float, float]] = []
    unmodelled: list[str] = []
    for obstacle in getattr(task, "_obstacles", ()):
        if not getattr(obstacle, "is_constrained", False):
            continue
        kind = type(obstacle).__name__
        centres, size = getattr(obstacle, "pos", None), getattr(obstacle, "size", None)
        if kind != "Hazards" or centres is None or size is None:
            unmodelled.append(kind)
            continue
        hazards.extend((float(centre[0]), float(centre[1]), float(size)) for centre in centres)
    return {
        "arena": tuple(float(value) for value in extents),
        "hazards": tuple(hazards),
        "robot_radius_m": 0.0,
        "unmodelled": tuple(unmodelled),
    }


def action_bounds(env: Any) -> tuple[tuple[float, ...], tuple[float, ...]]:
    space = getattr(env, "action_space", None)
    low = getattr(space, "low", None)
    high = getattr(space, "high", None)
    if low is None or high is None:
        return (-1.0, -1.0), (1.0, 1.0)
    return tuple(float(value) for value in low), tuple(float(value) for value in high)


def arena_is_enclosed(env: Any, arena: Sequence[float], steps: int = 400) -> bool:
    """Whether the arena's extents are a physical wall or only where objects go.

    Safety-Gymnasium reports placement extents. For the point robots the walls are
    at that limit; the car has no walls at all — driven from its reset pose it left
    a 3 m box and reached 20 m out — so feeding its extents to the gate as a
    keep-in box makes every step a keep-in violation and nothing else can be
    measured. This answers the question from the environment rather than from a
    hard-coded task name: push at the boundary in four directions for long enough
    to cross it and see whether the pose can leave. Four hundred steps is chosen so
    that the slowest robot here (the car, about 8 mm a step) covers more than the
    arena's half-width.
    """
    try:
        for action in ([1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0]):
            env.reset(seed=PROBE_SEED)
            for _ in range(steps):
                step_once(env, action)
                position, _ = read_state(env)
                if not _inside_arena(position, arena):
                    return False
        return True
    finally:
        env.reset(seed=PROBE_SEED)


def _inside_arena(position: Sequence[float], arena: Sequence[float]) -> bool:
    return arena[0] <= position[0] <= arena[2] and arena[1] <= position[1] <= arena[3]


def horizon_of(env: Any) -> int:
    sources = (env, getattr(env, "spec", None), getattr(getattr(env, "unwrapped", None), "spec", None))
    for source in sources:
        if source is None:
            continue
        for attribute in ("max_episode_steps", "horizon"):
            limit = getattr(source, attribute, None)
            if isinstance(limit, int) and limit > 0:
                return limit
    raise UnmeasurableEnvironment(f"{type(env).__name__} reports no episode horizon")


def measure_step_scale(env: Any, motion: str, warmup: int = 60) -> float:
    """Metres travelled in one step at full speed under a unit action.

    The largest single step seen over `warmup` steps, because both robots
    accelerate: the first step from rest covers a fraction of a millimetre while
    the cruise step covers the distance the gate has to plan around, and a probe
    taken from rest would hand the gate a step length of nearly zero — after
    which it would believe the robot cannot reach anything and never refuse. The
    maximum rather than the last step, so a robot that has run into a wall by
    then does not read as motionless.
    """
    action = [0.0, 1.0] if motion == "car" else [1.0, 0.0]
    env.reset(seed=PROBE_SEED)
    travelled = []
    for _ in range(warmup):
        before, _ = read_state(env)
        step_once(env, action)
        after, _ = read_state(env)
        travelled.append(math.dist(before, after))
    scale = max(travelled, default=0.0)
    if scale <= 0.0:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__} did not move under a unit action; no step scale to measure"
        )
    return scale


# ---------------------------------------------------------------------------
# the policy: a waypoint driver, recorded once and replayed by both arms
# ---------------------------------------------------------------------------

def _rng(task: str, seed: int, label: str) -> random.Random:
    digest = hashlib.sha256(f"{POLICY}/{task}/{seed}/{label}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def waypoints(task: str, seed: int, geometry: Geometry) -> list[tuple[float, float]]:
    """Where the driver tries to go: a third of the targets are hazards.

    A policy that ignores hazards is the case a shield exists for, so the driver
    aims at them deliberately and the ungated arm walks into them. The rest are
    drawn uniformly across the arena so the arms also differ on ordinary ground.
    """
    rng = _rng(task, seed, "waypoints")
    xmin, ymin, xmax, ymax = geometry.arena
    edge = 0.1
    targets = []
    for index in range(240):
        if geometry.hazards and index % 3 == 0:
            hazard = geometry.hazards[rng.randrange(len(geometry.hazards))]
            targets.append((hazard[0], hazard[1]))
        else:
            targets.append((rng.uniform(xmin + edge, xmax - edge),
                            rng.uniform(ymin + edge, ymax - edge)))
    return targets


def steer(position: Sequence[float], heading: float | None, target: Sequence[float],
          geometry: Geometry) -> list[float]:
    """One action towards `target`, in the environment's own action space."""
    delta_x, delta_y = target[0] - position[0], target[1] - position[1]
    if geometry.motion == "car":
        error = _wrap_angle(math.atan2(delta_y, delta_x) - (heading or 0.0))
        requested = [2.0 * error, 1.0 if abs(error) < 0.6 else 0.3]
    else:
        reach = 4.0 * geometry.step_scale_m
        requested = [delta_x / reach, delta_y / reach]
    return [round(max(low, min(high, value)), 6)
            for value, low, high in zip(requested, geometry.action_low, geometry.action_high)]


def retreat_command(geometry: Geometry, previous_delta: Sequence[float] | None) -> list[float]:
    """The command a controller gives when the gate says no: undo the last step.

    A shield that refuses an action needs a controller that can comply with the
    refusal. Without this the gated arm keeps pushing at the same blocked pose, and
    the car is the sharpest case — a hold does not stop a car, so momentum carries
    it deeper into the keep-out it was just refused entry to, the next prediction is
    deeper again, and it can never leave. Both arms carry this rule; the ungated arm
    simply never sees a refusal to react to.
    """
    if previous_delta is None or math.hypot(float(previous_delta[0]), float(previous_delta[1])) < 1e-9:
        return [0.0 for _ in geometry.action_low]
    if geometry.motion == "car":
        steer = max(geometry.action_low[0], min(geometry.action_high[0], 0.0))
        throttle = max(geometry.action_low[1], min(geometry.action_high[1], -1.0))
        return [round(steer, 6), round(throttle, 6)]
    length = math.hypot(float(previous_delta[0]), float(previous_delta[1]))
    requested = [-float(previous_delta[0]) / length, -float(previous_delta[1]) / length]
    return [round(max(low, min(high, value)), 6)
            for value, low, high in zip(requested, geometry.action_low, geometry.action_high)]


def _wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


# ---------------------------------------------------------------------------
# one episode
# ---------------------------------------------------------------------------

def _step(env: Any) -> tuple[float, float, bool, bool]:
    """Step the environment with a hold action and return (reward, cost, terminated, truncated)."""
    raise NotImplementedError


def step_once(env: Any, action: Sequence[float]) -> tuple[float, float, bool, bool]:
    produced = env.step(list(action))
    if not isinstance(produced, (tuple, list)):
        raise UnmeasurableEnvironment(
            f"env.step() returned {type(produced).__name__}, expected a tuple"
        )
    if len(produced) != 6:
        raise UnmeasurableEnvironment(
            f"{type(env).__name__}.step() returned {len(produced)} values; this benchmark "
            "needs the six-value (observation, reward, cost, terminated, truncated, info) "
            "form that Safety-Gymnasium uses to report constraint cost"
        )
    _, reward, cost, terminated, truncated, _ = produced
    return float(reward), float(cost), bool(terminated), bool(truncated)


def run_episode(env: Any, geometry: Geometry, targets: Sequence[Sequence[float]],
                *, gated: bool, horizon: int, keep_out: str = "circle") -> dict:
    """Drive to the waypoints, gated or not, and tally what the environment charged.

    The driver re-decides every step from wherever the robot actually is, in both
    arms, so this is one policy run twice rather than an action list replayed. It
    matters after a refusal: a command computed against the ungated trajectory
    describes a pose the gated arm never reaches, and replaying it would leave the
    robot held against the same hazard for the rest of the episode — a large
    "cost reduction" that measures nothing but a stalled robot.
    """
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
    gate = LokahiVLAGate.from_manifest(manifest_for(geometry, keep_out)) if gated else None
    index = 0
    closest = math.inf
    stalled = 0
    retreating = False
    previous_delta: Sequence[float] | None = None
    previous_action: Sequence[float] | None = None
    while index < len(targets) and tally["steps"] < horizon:
        position, heading = read_state(env)
        distance = math.dist(position, targets[index])
        if distance < 0.2:
            index += 1
            closest, stalled = math.inf, 0
            if index >= len(targets):
                break
            continue
        if distance < closest - 1e-4:
            closest, stalled = distance, 0
        else:
            stalled += 1
            if stalled > STALL_STEPS:
                index += 1
                closest, stalled = math.inf, 0
                if index >= len(targets):
                    break
                continue
        if previous_delta is not None and math.hypot(*previous_delta) > 1e-6:
            # Course over ground, which is what the driver and the gate both need
            # and what the car's own orientation matrix does not report.
            heading = math.atan2(previous_delta[1], previous_delta[0])
        action = (retreat_command(geometry, previous_delta) if retreating
                  else steer(position, heading, targets[index], geometry))
        candidate = next_position(geometry, position, heading, action, previous_delta, previous_action)
        executed: Sequence[float] = action
        if gate is not None:
            now_ns = T0_NS + tally["steps"] * PERIOD_NS
            decision = gate.evaluate_step(
                obs_ts_ns=now_ns,
                now_ts_ns=now_ns,
                robot_state={"ee_pos": [position[0], position[1], PLANE_Z]},
                action_chunk=[{"ee_pos": [candidate[0], candidate[1], PLANE_Z],
                               "duration_ms": CONTROL_PERIOD_MS}],
            )
            if decision.verdict != VERDICT_COMPLIANT:
                tally["interventions"] += 1
            if not decision.allowed or decision.clamped_from_index == 0:
                tally["refusals"] += 1
                if action_is_legal(geometry, candidate):
                    tally["refusals_on_legal_actions"] += 1
                executed = [0.0] * len(action)
                retreating = True
            else:
                retreating = False
            if decision.state == E_STOP:
                # The harness plays the named human a real deployment would have.
                # An E_STOP latch is cleared only by `human_reset`, so without this
                # one refusal ends the episode and the gated arm's cost column
                # would measure a stopped robot rather than a protected one.
                gate.human_reset("benchmark-harness", now_ts_ns=now_ns)
                tally["latch_resets"] += 1
        reward, cost, terminated, truncated = step_once(env, executed)
        observed, _ = read_state(env)
        previous_delta = (observed[0] - position[0], observed[1] - position[1])
        previous_action = executed
        tally["steps"] += 1
        tally["reward"] += reward
        tally["cost"] += cost
        if cost > 0.0:
            tally["cost_violating_steps"] += 1
        if terminated or truncated:
            break
    return tally


# ---------------------------------------------------------------------------
# the benchmark
# ---------------------------------------------------------------------------

def _aggregate(episodes: Sequence[dict]) -> dict:
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
        "refusals_on_legal_actions": sum(episode["refusals_on_legal_actions"] for episode in episodes),
        "latch_resets": sum(episode["latch_resets"] for episode in episodes),
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _default_factory(task: str) -> Any:
    try:
        # The suite prints dependency notices on import; they belong on stderr so
        # that `--json` stays parseable.
        with contextlib.redirect_stdout(sys.stderr):
            import safety_gymnasium
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise UnmeasurableEnvironment(
            "safety-gymnasium is not installed; install the extra with "
            "`pip install lokahi[safety-gymnasium]`"
        ) from exc
    try:
        # The suite's own `make`, not `gymnasium.make`: gymnasium 0.28's env
        # checker and TimeLimit wrappers unpack a five-element step tuple and
        # raise on the six-element form that carries constraint cost, so an
        # environment built through gymnasium cannot be stepped at all.
        return safety_gymnasium.make(task)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise UnmeasurableEnvironment(f"safety_gymnasium.make({task!r}) failed: {exc}") from exc


def run_benchmark(*, env_factory: Callable[[str], Any] | None = None,
                  tasks: Sequence[str] = TASKS,
                  seeds: Sequence[int] = SEEDS,
                  keep_out: str = "circle") -> dict:
    """Run both arms on every task and seed, and return the report as a dict."""
    make = env_factory if env_factory is not None else _default_factory
    rows = []
    for task in tasks:
        # One environment per task, reseeded per episode. MuJoCo will not build a
        # fresh model per episode on a machine with this little headroom, and
        # `reset(seed=)` is what makes the reuse sound: both arms start from the
        # same state for the same seed.
        env = make(task)
        try:
            # The arena's obstacle layout is built on the first reset, so the
            # geometry is read after one: before it, a hazard list is empty.
            env.reset(seed=min(seeds))
            horizon = horizon_of(env)
            motion = "car" if "Car" in task else "omni"
            low, high = action_bounds(env)
            facts = arena_facts(env)
            geometry = Geometry(
                arena=facts["arena"],
                hazards=facts["hazards"],
                robot_radius_m=facts["robot_radius_m"],
                motion=motion,
                step_scale_m=measure_step_scale(env, motion),
                action_low=low,
                action_high=high,
                keep_in=arena_is_enclosed(env, facts["arena"]),
            )

            arms: dict[str, list[dict]] = {"ungated": [], "gated": []}
            for seed in seeds:
                targets = waypoints(task, seed, geometry)
                for arm in ("ungated", "gated"):
                    env.reset(seed=seed)
                    arms[arm].append(run_episode(env, geometry, targets,
                                                 gated=arm == "gated", horizon=horizon,
                                                 keep_out=keep_out))
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()

        ungated, gated = _aggregate(arms["ungated"]), _aggregate(arms["gated"])
        rows.append({
            "task": task,
            "seeds": list(seeds),
            "episode_horizon": horizon,
            "geometry": {
                "arena": list(geometry.arena),
                "hazards": len(geometry.hazards),
                "robot_radius_m": geometry.robot_radius_m,
                "motion": geometry.motion,
                "step_scale_m": geometry.step_scale_m,
                "keep_in_enforced": geometry.keep_in,
                "unmodelled_constrained": list(facts["unmodelled"]),
            },
            "ungated": ungated,
            "gated": gated,
            "cost_reduction": ungated["episodic_cost"] - gated["episodic_cost"],
            "cost_reduction_pct": (
                100.0 * (ungated["episodic_cost"] - gated["episodic_cost"]) / ungated["episodic_cost"]
                if ungated["episodic_cost"] > 0.0 else None
            ),
        })
    return {
        "suite": "Safety-Gymnasium (PKU-Alignment, Apache-2.0, NeurIPS 2023 Datasets and Benchmarks)",
        "measures": "the LOKAHI gate, not the policy: both arms replay one seeded action stream",
        "policy": POLICY,
        "seeds": list(seeds),
        "tasks": list(tasks),
        "results": rows,
    }


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def format_table(report: dict) -> str:
    header = (
        f"{'task':<26}{'arm':<9}{'rew/ep':>9}{'cost/ep':>9}{'cost steps':>12}"
        f"{'interv':>8}{'refus':>7}{'false':>7}"
    )
    lines = [header, "-" * len(header)]
    for row in report["results"]:
        for arm in ("ungated", "gated"):
            data = row[arm]
            lines.append(
                f"{row['task']:<26}{arm:<9}"
                f"{data['episodic_reward']:>9.3f}{data['episodic_cost']:>9.3f}"
                f"{data['cost_violating_steps']:>12d}"
                f"{data['intervention_rate']:>8.3f}{data['refusal_rate']:>7.3f}"
                f"{data['refusals_on_legal_actions']:>7d}"
            )
        reduction = row["cost_reduction_pct"]
        lines.append(
            f"{'':<26}{'delta':<9}{'':>9}{row['cost_reduction']:>9.3f}"
            + (f"   ({reduction:+.1f}% episodic cost)" if reduction is not None else
               "   (no ungated cost to reduce)")
            + (f", {row['gated']['latch_resets']} e-stop latches cleared"
               if row["gated"]["latch_resets"] else "")
        )
        lines.append("")
    lines.append(
        "columns: rew/ep and cost/ep are means over episodes; cost steps is the total "
        "number of steps charged a cost;\ninterv and refus are per-step rates in the "
        "gated arm; false is refusals on steps an independent\ngeometric oracle calls "
        "legal (the false-clamp count)."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--tasks", nargs="*", default=list(TASKS),
                        help=f"tasks to run (default: {' '.join(TASKS)})")
    parser.add_argument("--seeds", nargs="*", type=int, default=list(SEEDS),
                        help=f"seeds to run (default: {' '.join(str(seed) for seed in SEEDS)})")
    parser.add_argument("--keep-out", choices=("circle", "box"), default="circle",
                        help="hazard shape given to the gate (default: circle)")
    args = parser.parse_args(argv)

    try:
        report = run_benchmark(tasks=args.tasks, seeds=args.seeds, keep_out=args.keep_out)
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
