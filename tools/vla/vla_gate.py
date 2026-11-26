"""Deterministic hot-path gate for VLA action chunks.

The check order below is normative and cheap-first, the same discipline as the
15 rules in `lokahi.evaluate`: anything that makes the action meaningless is
decided before anything that merely makes it unsafe.

    1. latch      — E_STOP is absorbing until a human re-arms
    2. clock      — non-monotonic or future timestamps
    3. structural — NaN/Inf, shape mismatch, non-unit quaternion, and the
                    caller's dynamic keep-out set against the manifest's caps
    4. freshness  — per-element age against max_action_age_ms
    5. watchdog   — gap since the last completed step
    6. geometry   — keep-in and keep-out sweep over the whole chunk
    7. kinematics — displacement, joint limits, velocity and acceleration
    8. wrench     — force, torque, compliance offset
    9. budget     — the gate's own evaluation time

Geometry note the code relies on: a keep-in region is convex, so a polyline
lies entirely inside it exactly when every vertex does. A keep-out region is the
opposite test, and needs the segment-vs-box slab intersection — checking only
vertices would let a chunk pass straight through an obstacle.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from lokahi import COMPLIANT, FORBIDDEN

from . import geometry, recovery, replanning
from .physical_manifest import (
    PhysicalVLAConfig,
    VLAManifestError,
    load_physical_vla,
    load_physical_vla_file,
    parse_keep_out_shape,
)

ARMED = "ARMED"
CLAMPED = "CLAMPED"
SOFT_STOP = "SOFT_STOP"
E_STOP = "E_STOP"
HUMAN_RESET = "HUMAN_RESET"

VERDICT_COMPLIANT = "COMPLIANT"
VERDICT_CLAMPED = "CLAMPED"
VERDICT_FORBIDDEN = "FORBIDDEN"

# Projection onto the engine's existing vocabulary. There is no second state
# machine: FailSecureManager owns secure-state entry and re-arm, and these names
# only say what the interlock should do on this step.
STATE_INTERLOCK = {
    ARMED: "pass",
    CLAMPED: "pass",
    SOFT_STOP: "safe_mechanical_hold",
    E_STOP: "normal_retract",
    HUMAN_RESET: "safe_mechanical_hold",
}

_EPS = 1e-9
_QUAT_TOL = 1e-6

KEEP_OUT_SOURCE_MANIFEST = "manifest"
KEEP_OUT_SOURCE_CALLER = "caller"


@dataclass
class VLADecisionResult:
    allowed: bool
    verdict: str
    state: str
    safe_action: dict | None
    violated_invariants: list[str]
    margins: dict[str, float]
    recovery_hint: dict
    gate_eval_us: float
    interlock: str = "pass"
    clamped_from_index: int | None = None
    replanned_path: list[dict[str, Any]] = field(default_factory=list)
    # Poses that leave the keep-out, when recovery_planning is on and a
    # keep-out refusal fired. Empty otherwise. The refusal itself is unchanged.
    escape_path: list[list[float]] = field(default_factory=list)
    # Provenance of the keep-out set this step was checked against, so a
    # receipt can say where the obstacles came from and an auditor can tell a
    # static manifest from a caller that moved one.
    keep_out_source: str = KEEP_OUT_SOURCE_MANIFEST
    dynamic_keep_out_count: int = 0

    def to_decision(self):
        """A `lokahi.Decision` so the existing ledger carries VLA steps unchanged."""
        from lokahi import Decision

        if self.verdict == VERDICT_FORBIDDEN:
            return Decision(FORBIDDEN, list(self.violated_invariants), actuator_interlock=self.interlock)
        return Decision(COMPLIANT, list(self.violated_invariants), actuator_interlock=self.interlock)


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def _finite_vector(name: str, value: Any, length: int) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a list of {length} numbers")
    if len(value) != length:
        raise ValueError(f"{name} must have {length} elements, got {len(value)}")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{name} contains a non-number")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"{name} contains a non-finite value")
        out.append(number)
    return out


def _inside(point: Sequence[float], box: Sequence[float]) -> bool:
    return (
        box[0] <= point[0] <= box[3]
        and box[1] <= point[1] <= box[4]
        and box[2] <= point[2] <= box[5]
    )


def _grow(box: Sequence[float], radius: float) -> tuple[float, ...]:
    return (box[0] - radius, box[1] - radius, box[2] - radius,
            box[3] + radius, box[4] + radius, box[5] + radius)


def _shrink(box: Sequence[float], radius: float) -> tuple[float, ...] | None:
    shrunk = (box[0] + radius, box[1] + radius, box[2] + radius,
              box[3] - radius, box[4] - radius, box[5] - radius)
    if shrunk[0] >= shrunk[3] or shrunk[1] >= shrunk[4] or shrunk[2] >= shrunk[5]:
        return None
    return shrunk


def segment_hits_box(
    start: Sequence[float], end: Sequence[float], box: Sequence[float]
) -> bool:
    """Slab method: does the closed segment start->end intersect the closed box?"""
    t_enter, t_exit = 0.0, 1.0
    for axis in range(3):
        origin = start[axis]
        delta = end[axis] - origin
        low, high = box[axis], box[axis + 3]
        if abs(delta) < _EPS:
            if origin < low or origin > high:
                return False
            continue
        t1 = (low - origin) / delta
        t2 = (high - origin) / delta
        if t1 > t2:
            t1, t2 = t2, t1
        t_enter = max(t_enter, t1)
        t_exit = min(t_exit, t2)
        if t_enter > t_exit:
            return False
    return True


def _segment_point_distance(start: Sequence[float], end: Sequence[float], centre: Sequence[float]) -> float:
    """Distance from a 2D segment to a point, in the plane."""
    ax, ay = start[0], start[1]
    dx, dy = end[0] - ax, end[1] - ay
    denominator = dx * dx + dy * dy
    if denominator < _EPS:
        return math.hypot(centre[0] - ax, centre[1] - ay)
    t = ((centre[0] - ax) * dx + (centre[1] - ay) * dy) / denominator
    t = max(0.0, min(1.0, t))
    return math.hypot(centre[0] - (ax + t * dx), centre[1] - (ay + t * dy))


def segment_hits_circle(start, end, centre, radius) -> bool:
    return geometry.segment_point_distance_2d(start, end, centre) <= radius


def segment_hits_cylinder(start, end, centre, radius, height) -> bool:
    return geometry.sweep_hits({"kind": "cylinder", "centre": centre, "radius": radius,
                                "height": height}, start, end)


def _outside_distance(point: Sequence[float], box: Sequence[float]) -> float:
    """Zero when the point is inside; otherwise the Euclidean distance outside."""
    offsets = []
    for axis in range(3):
        if point[axis] < box[axis]:
            offsets.append(box[axis] - point[axis])
        elif point[axis] > box[axis + 3]:
            offsets.append(point[axis] - box[axis + 3])
        else:
            offsets.append(0.0)
    return math.sqrt(sum(offset * offset for offset in offsets))


def _penetration_depth(point: Sequence[float], box: Sequence[float]) -> float:
    """Zero when the point is outside; otherwise depth inside, the distance to the nearest face."""
    if not _inside(point, box):
        return 0.0
    return min(min(point[axis] - box[axis], box[axis + 3] - point[axis]) for axis in range(3))


def _quat_norm(quat: Sequence[float]) -> float:
    return math.sqrt(sum(component * component for component in quat))


def _angle_between(first: Sequence[float], second: Sequence[float]) -> float:
    """Geodesic angle between unit quaternions (w, x, y, z)."""
    dot = abs(sum(a * b for a, b in zip(first, second)))
    return 2.0 * math.acos(min(1.0, dot))


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------

class LokahiVLAGate:
    """One gate per robot. Stateful only in its latch and last-seen timestamps."""

    def __init__(self, config: PhysicalVLAConfig) -> None:
        self.config = config
        self.state = ARMED
        self.last_now_ns: int | None = None
        self.last_step_ns: int | None = None
        self.latch_reason: str | None = None
        self.steps_evaluated = 0
        self.interventions = 0

    @classmethod
    def from_manifest(cls, source, verifier=None) -> "LokahiVLAGate":
        if isinstance(source, Mapping):
            return cls(load_physical_vla(source, verifier=verifier))
        return cls(load_physical_vla_file(str(source), verifier=verifier))

    # -- state transitions --------------------------------------------------

    def human_reset(self, operator: str, now_ts_ns: int | None = None) -> dict:
        """Clear a latch. Only a named human may do this; never a policy."""
        if not operator:
            raise ValueError("human_reset requires a named operator")
        previous = self.state
        self.state = HUMAN_RESET
        self.latch_reason = None
        entry = {
            "kind": "vla_human_reset",
            "operator": operator,
            "previous_state": previous,
            "ts_ns": int(now_ts_ns if now_ts_ns is not None else time.time_ns()),
        }
        self.state = ARMED
        return entry

    def _latch(self, state: str, reason: str) -> None:
        if state == E_STOP:
            self.state = E_STOP
            self.latch_reason = reason
        elif self.state not in (E_STOP,):
            self.state = state

    # -- main path ----------------------------------------------------------

    def evaluate_step(
        self,
        obs_ts_ns: int,
        now_ts_ns: int,
        robot_state: Mapping[str, Any],
        action_chunk: Sequence[Mapping[str, Any]],
        wrench: Mapping[str, Any] | None = None,
        source: str = "vla",
        dynamic_keep_outs: Sequence[Mapping[str, Any]] | None = None,
    ) -> VLADecisionResult:
        started = time.perf_counter_ns()
        config = self.config
        violations: list[tuple[str, str]] = []
        margins: dict[str, float] = {}

        # 1. latch
        if self.state == E_STOP:
            return self._result(
                started, [(("structural"), "e_stop_latched")], {}, robot_state,
                VERDICT_FORBIDDEN, E_STOP, clamped_index=None,
            )

        # 2. clock
        now_ns = int(now_ts_ns)
        if self.last_now_ns is not None and now_ns < self.last_now_ns:
            violations.append(("clock_skew", f"clock_skew:backwards:{now_ns - self.last_now_ns}ns"))
        if int(obs_ts_ns) > now_ns:
            violations.append(("clock_skew", "clock_skew:observation_from_the_future"))
        self.last_now_ns = now_ns

        # 3. structural
        chunk = list(action_chunk or [])
        if not chunk:
            violations.append(("structural", "structural:empty_chunk"))
        current = _try_vector(robot_state, "ee_pos", 3)
        joint_now = _try_vector(robot_state, "joint_pos", None)
        parsed: list[dict[str, Any]] = []
        for index, element in enumerate(chunk):
            try:
                if not isinstance(element, Mapping):
                    raise ValueError("element is not an object")
                position = _finite_vector(f"chunk[{index}].ee_pos", element.get("ee_pos"), 3)
                quat = element.get("ee_quat")
                if quat is not None:
                    quat = _finite_vector(f"chunk[{index}].ee_quat", quat, 4)
                    norm = _quat_norm(quat)
                    if abs(norm - 1.0) > _QUAT_TOL:
                        raise ValueError(f"quaternion is not unit (|q|={norm:.6f})")
                joints = element.get("joint_pos")
                if joints is not None:
                    joints = _finite_vector(f"chunk[{index}].joint_pos", joints, len(joints))
                parsed.append({
                    "ee_pos": position,
                    "ee_quat": quat,
                    "joint_pos": joints,
                    "duration_ms": float(element.get("duration_ms", config.control_period_ms)),
                })
            except (ValueError, TypeError) as exc:
                violations.append(("structural", f"structural:chunk[{index}]:{exc}"))

        if current is None and (
            config.workspace_aabb is not None
            or config.keep_out_aabbs
            or config.max_pos_delta_m is not None
            or config.max_rot_delta_rad is not None
        ):
            # Fail closed. Without the current pose the geometric and kinematic
            # checks cannot run at all, and a gate that silently skips its own
            # checks is worse than no gate: it reports COMPLIANT.
            violations.append(("structural", "structural:robot_state_missing_ee_pos"))

        # Per-step keep-outs are input, not manifest state, so they are
        # validated next to the chunk's own structure and before anything
        # geometric: an unauthorised or oversized set is refused even on a step
        # whose geometry cannot run at all.
        dynamic_shapes, dynamic_count, dynamic_reasons = _parse_dynamic_keep_outs(
            config, dynamic_keep_outs
        )
        violations.extend(("dynamic_keep_out", reason) for reason in dynamic_reasons)
        keep_out_source = (
            KEEP_OUT_SOURCE_CALLER if dynamic_count or dynamic_reasons else KEEP_OUT_SOURCE_MANIFEST
        )

        # 4. freshness, per element, not per chunk
        fresh = len(parsed)
        if parsed and not any(kind == "structural" for kind, _ in violations):
            elapsed_ms = (now_ns - int(obs_ts_ns)) / 1e6
            age = elapsed_ms
            for index, element in enumerate(parsed):
                if index:
                    age += parsed[index - 1]["duration_ms"]
                if age > config.max_action_age_ms:
                    fresh = index
                    violations.append(
                        ("stale_action", f"stale_action:{index}:{age:.1f}ms>{config.max_action_age_ms:.1f}ms")
                    )
                    break
            margins["oldest_action_age_ms"] = age

        # 5. watchdog
        if self.last_step_ns is not None:
            gap_ms = (now_ns - self.last_step_ns) / 1e6
            margins["watchdog_gap_ms"] = gap_ms
            if gap_ms > config.watchdog_timeout_ms:
                violations.append(("watchdog", f"watchdog:{gap_ms:.1f}ms>{config.watchdog_timeout_ms:.1f}ms"))

        # 6. geometry over the swept polyline
        if current is not None and parsed and all(v is not None for v in (current,)):
            points = [current] + [element["ee_pos"] for element in parsed]
            if config.workspace_aabb is not None:
                keep_in = _shrink(config.workspace_aabb, config.tool_radius_m)
                if keep_in is None:
                    violations.append(("workspace_keep_in", "workspace_keep_in:tool_radius_exceeds_box"))
                else:
                    start_violation = _outside_distance(points[0], keep_in)
                    if start_violation > 0.0:
                        # Already outside: a state this gate did not create, and
                        # one that refusing every move would make permanent. A
                        # simulator caught exactly that: a policy drifted into a
                        # hazard at step 72 and every later step was refused, so
                        # the arm stayed in it for the rest of the episode.
                        #
                        # The test is on the end pose, and it is non-worsening
                        # rather than strict improvement. Both details came from
                        # a simulator. Requiring every waypoint to be no worse
                        # trapped a car, because it must turn before it can
                        # leave and its penetration deepens mid-chunk. Requiring
                        # strict improvement trapped it again, because turning in
                        # place ends at the same depth — so the one move that
                        # points it at the exit was refused. A hold inside a
                        # hazard is charged either way; a refusal only removes
                        # the means of escape.
                        distances = [_outside_distance(point, keep_in) for point in points]
                        if distances[-1] <= start_violation:
                            margins["keep_in_recovery_m"] = start_violation - distances[-1]
                        else:
                            violations.append(
                                ("recovery", f"recovery_required:keep_in:start={start_violation:.4f}m")
                            )
                    elif [point for point in points if not _inside(point, keep_in)]:
                        violations.append(
                            ("workspace_keep_in", "workspace_keep_in:vertices_outside")
                        )
                    else:
                        margins["keep_in_margin_m"] = min(
                            min(point[axis] - keep_in[axis], keep_in[axis + 3] - point[axis])
                            for point in points
                            for axis in range(3)
                        )
            for box_index, box in enumerate(config.keep_out_aabbs):
                obstacle = _grow(box, config.tool_radius_m)
                start_depth = _penetration_depth(points[0], obstacle)
                if start_depth > 0.0:
                    depths = [_penetration_depth(point, obstacle) for point in points]
                    if depths[-1] <= start_depth:
                        margins[f"keep_out_recovery_{box_index}_m"] = start_depth - depths[-1]
                        continue
                    violations.append(
                        ("recovery", f"recovery_required:keep_out:{box_index}:depth={start_depth:.4f}m")
                    )
                    continue
                for step in range(len(points) - 1):
                    if segment_hits_box(points[step], points[step + 1], obstacle):
                        violations.append(
                            ("workspace_keep_out", f"workspace_keep_out:{box_index}:segment_{step}")
                        )
                        break
            # Hazards with a real shape. A box around a disc refuses legal poses,
            # which showed up as 467 over-refusals on one benchmark task.
            #
            # A caller-supplied set rides the same sweep and the same monotone
            # recovery rule; only the label differs, so a refusal names which
            # set the obstacle came from.
            sweep_targets = [
                (f"shape_{shape_index}", f"{shape_index}", shape,
                 f"keep_out_recovery_shape_{shape_index}_m")
                for shape_index, shape in enumerate(config.keep_out_shapes)
            ]
            sweep_targets += [
                (f"dynamic_{index}", f"dynamic_{index}", shape,
                 f"keep_out_recovery_dynamic_{index}_m")
                for index, shape in enumerate(dynamic_shapes)
            ]
            for sweep_label, recovery_label, shape, recovery_key in sweep_targets:
                margin = config.tool_radius_m
                start_depth = geometry.inside_depth(shape, points[0], margin)
                if start_depth > 0.0:
                    depths = [geometry.inside_depth(shape, point, margin) for point in points]
                    if depths[-1] <= start_depth:
                        margins[recovery_key] = start_depth - depths[-1]
                        continue
                    violations.append(
                        ("recovery", f"recovery_required:keep_out:{recovery_label}:depth={start_depth:.4f}m")
                    )
                    continue
                for step in range(len(points) - 1):
                    if geometry.sweep_hits(shape, points[step], points[step + 1], margin):
                        violations.append(
                            ("workspace_keep_out", f"workspace_keep_out:{sweep_label}:segment_{step}")
                        )
                        break

        # 7. kinematics
        clamp_limit = fresh
        if current is not None and parsed:
            previous = current
            previous_quat = _try_vector(robot_state, "ee_quat", 4)
            for index, element in enumerate(parsed):
                delta = math.dist(previous, element["ee_pos"])
                if config.max_pos_delta_m is not None and delta > config.max_pos_delta_m:
                    violations.append(
                        ("kinematic", f"kinematic:pos_delta:{index}:{delta:.4f}m>{config.max_pos_delta_m}m")
                    )
                    clamp_limit = min(clamp_limit, index)
                if element["ee_quat"] is not None and previous_quat is not None:
                    angle = _angle_between(previous_quat, element["ee_quat"])
                    if config.max_rot_delta_rad is not None and angle > config.max_rot_delta_rad:
                        violations.append(
                            ("kinematic", f"kinematic:rot_delta:{index}:{angle:.4f}rad>{config.max_rot_delta_rad}rad")
                        )
                        clamp_limit = min(clamp_limit, index)
                previous = element["ee_pos"]
                previous_quat = element["ee_quat"]
            if config.joint_limits and joint_now is not None:
                for index, element in enumerate(parsed):
                    if element["joint_pos"] is None:
                        continue
                    if len(element["joint_pos"]) != len(config.joint_limits):
                        violations.append(("kinematic", f"kinematic:joint_shape:{index}"))
                        clamp_limit = min(clamp_limit, index)
                        continue
                    for joint, (position, bounds) in enumerate(zip(element["joint_pos"], config.joint_limits)):
                        if not bounds[0] <= position <= bounds[1]:
                            violations.append(
                                ("kinematic", f"kinematic:joint_limit:{index}:{joint}:{position:.3f}")
                            )
                            clamp_limit = min(clamp_limit, index)
            if (
                config.max_joint_vel_rad_s is not None or config.max_joint_acc_rad_s2 is not None
            ) and joint_now is not None:
                previous_joints = list(joint_now)
                previous_velocity: list[float] | None = None
                for index, element in enumerate(parsed):
                    joints = element["joint_pos"]
                    if joints is None:
                        continue
                    seconds = element["duration_ms"] / 1000.0
                    if seconds <= 0.0:
                        violations.append(("kinematic", f"kinematic:non_positive_duration:{index}"))
                        clamp_limit = min(clamp_limit, index)
                        continue
                    if len(joints) != len(previous_joints):
                        violations.append(("kinematic", f"kinematic:joint_shape:{index}"))
                        clamp_limit = min(clamp_limit, index)
                        continue
                    velocity = [(b - a) / seconds for a, b in zip(previous_joints, joints)]
                    if config.max_joint_vel_rad_s is not None:
                        peak = max(abs(component) for component in velocity)
                        if peak > config.max_joint_vel_rad_s:
                            violations.append(
                                ("kinematic", f"kinematic:joint_vel:{index}:{peak:.3f}rad/s>{config.max_joint_vel_rad_s}")
                            )
                            clamp_limit = min(clamp_limit, index)
                    if config.max_joint_acc_rad_s2 is not None and previous_velocity is not None:
                        peak_acc = max(
                            abs((now_v - was_v) / seconds)
                            for now_v, was_v in zip(velocity, previous_velocity)
                        )
                        if peak_acc > config.max_joint_acc_rad_s2:
                            violations.append(
                                ("kinematic", f"kinematic:joint_acc:{index}:{peak_acc:.3f}rad/s2>{config.max_joint_acc_rad_s2}")
                            )
                            clamp_limit = min(clamp_limit, index)
                    previous_joints = list(joints)
                    previous_velocity = velocity

        # 8. wrench
        if wrench:
            force = _scalar_or_norm("force", wrench.get("force_n"))
            torque = _scalar_or_norm("torque", wrench.get("torque_nm"))
            if force is not None and config.max_force_n is not None:
                margins["force_margin_n"] = config.max_force_n - force
                if force > config.max_force_n:
                    violations.append(("wrench", f"wrench:force:{force:.2f}N>{config.max_force_n}N"))
            if torque is not None and config.max_torque_nm is not None:
                margins["torque_margin_nm"] = config.max_torque_nm - torque
                if torque > config.max_torque_nm:
                    violations.append(("wrench", f"wrench:torque:{torque:.2f}Nm>{config.max_torque_nm}Nm"))

        self.steps_evaluated += 1
        self.last_step_ns = now_ns

        # 9. severity, then the budget check on the gate's own work
        if not violations:
            self.state = ARMED
            return self._result(
                started, [], margins, robot_state, VERDICT_COMPLIANT, ARMED, None,
                keep_out_source=keep_out_source, dynamic_keep_out_count=dynamic_count,
            )

        self.interventions += 1
        decision = _decide(config, violations, clamp_limit)
        state = decision["state"]
        self._latch(state, violations[0][1])
        return self._result(
            started,
            violations,
            margins,
            robot_state,
            decision["verdict"],
            self.state,
            decision["clamped_index"],
            chunk=parsed,
            keep_out_source=keep_out_source,
            dynamic_keep_out_count=dynamic_count,
        )

    # -- result assembly ----------------------------------------------------

    def _result(
        self,
        started_ns: int,
        violations: list[tuple[str, str]],
        margins: dict[str, float],
        robot_state: Mapping[str, Any],
        verdict: str,
        state: str,
        clamped_index: int | None,
        chunk: list[dict[str, Any]] | None = None,
        keep_out_source: str = KEEP_OUT_SOURCE_MANIFEST,
        dynamic_keep_out_count: int = 0,
    ) -> VLADecisionResult:
        safe_action = None
        if verdict == VERDICT_CLAMPED and chunk:
            keep = clamped_index if clamped_index is not None else len(chunk)
            safe_action = {"mode": "clamped_chunk", "actions": chunk[:keep]}
        elif state in (SOFT_STOP, E_STOP, HUMAN_RESET):
            safe_action = {
                "mode": STATE_INTERLOCK.get(state, "safe_mechanical_hold"),
                "hold_pose": _try_vector(robot_state, "ee_pos", 3),
                "hold_joints": _try_vector(robot_state, "joint_pos", None),
            }
            if state == E_STOP:
                # No retract path is planned or gated, so the payload is a hold at
                # the last observed pose. Saying "retract" without a path would be
                # a claim the code cannot keep.
                safe_action["note"] = "hold at last observed pose; no retract path is planned"

        replanned: list[dict[str, Any]] = []
        if self.config.clamp_strategy == "replan" and chunk:
            keep = clamped_index if clamped_index is not None else len(chunk)
            legal = chunk[:keep]
            velocity = _chunk_velocity(legal)
            start = None
            if legal:
                start = _try_vector(legal[-1], "ee_pos", 3)
            if start is None:
                start = _try_vector(robot_state, "ee_pos", 3)
            if velocity is not None and start is not None:
                limits = replanning.Limits(
                    max_acceleration=self.config.replan_limits.get("max_acceleration_m_s2", 1.0),
                    max_jerk=self.config.replan_limits.get("max_jerk_m_s3", 10.0),
                    max_velocity=self.config.replan_limits.get("max_velocity_m_s"),
                )
                try:
                    profile = replanning.stop_profile(
                        start, velocity, limits,
                        control_period_s=self.config.control_period_ms / 1000.0,
                    )
                except replanning.ReplanError as exc:
                    # A profile that cannot be produced is not a reason to hand
                    # back a chunk. Fall back to the prefix and say why.
                    if safe_action is not None:
                        safe_action["replan_refused"] = str(exc)
                else:
                    replanned = [
                        {"t_ms": round(waypoint.time_s * 1000.0, 3),
                         "ee_pos": list(waypoint.position),
                         "ee_vel": list(waypoint.velocity)}
                        for waypoint in profile
                    ]
                    safe_action = {
                        "mode": "replanned_stop",
                        "actions": legal,
                        "stop_profile": replanned,
                        "note": "deceleration from the last legal pose; gated again before execution",
                    }

        escape: list[list[float]] = []
        if (
            self.config.recovery_planning
            and verdict == VERDICT_FORBIDDEN
            and state != E_STOP
            and _keep_out_refusal(violations)
        ):
            escape, payload = self._escape_payload(robot_state)
            if payload is not None:
                safe_action = payload

        elapsed_us = (time.perf_counter_ns() - started_ns) / 1000.0
        reasons = [reason for _, reason in violations]
        if elapsed_us > self.config.max_gate_eval_us:
            # A gate that overruns its deadline has produced a stale decision, so
            # it refuses. It may only escalate the state: an E_STOP that already
            # fired must not be reported as a softer stop because the clock ran out.
            verdict = VERDICT_FORBIDDEN
            if state != E_STOP:
                state = SOFT_STOP
            reasons = reasons + [f"budget_exceeded:{elapsed_us:.0f}us>{self.config.max_gate_eval_us:.0f}us"]
            self._latch(state, "budget_exceeded")
            if safe_action is None:
                safe_action = {
                    "mode": "safe_mechanical_hold",
                    "hold_pose": _try_vector(robot_state, "ee_pos", 3),
                }

        return VLADecisionResult(
            allowed=verdict in (VERDICT_COMPLIANT, VERDICT_CLAMPED),
            verdict=verdict,
            state=state,
            safe_action=safe_action,
            violated_invariants=reasons,
            margins=margins,
            recovery_hint=_recovery_hint(config=self.config, reasons=reasons, state=state, chunk=chunk),
            gate_eval_us=elapsed_us,
            interlock=STATE_INTERLOCK.get(state, "safe_mechanical_hold"),
            clamped_from_index=clamped_index,
            replanned_path=replanned,
            escape_path=escape,
            keep_out_source=keep_out_source,
            dynamic_keep_out_count=dynamic_keep_out_count,
        )

    def _escape_payload(
        self, robot_state: Mapping[str, Any]
    ) -> tuple[list[list[float]], dict[str, Any] | None]:
        """A path out of the keep-out, and the payload that carries it.

        The path is an offer beside the refusal, never a release of it: the
        verdict, state and interlock are already decided and this function only
        builds the second artefact. `tool_radius_m` is applied here so the
        planner sees the swept volume, exactly as the geometry check does.
        """
        start = _try_vector(robot_state, "ee_pos", 3)
        if start is None:
            return [], None
        radius = self.config.tool_radius_m
        boxes = [_grow(box, radius) for box in self.config.keep_out_aabbs]
        shapes = []
        for shape in self.config.keep_out_shapes:
            grown = dict(shape)
            grown["radius"] = float(shape["radius"]) + radius
            shapes.append(grown)
        workspace = self.config.workspace_aabb
        if workspace is not None:
            workspace = _shrink(workspace, radius)
            if workspace is None:
                return [], None
        try:
            path = recovery.escape_path(
                start, boxes, shapes, workspace,
                self.config.recovery_step_m, self.config.recovery_max_steps,
            )
        except recovery.RecoveryError:
            # A keep-out whose clearance cannot be measured gets no path. The
            # hold stands; proposing an unverified escape would be worse.
            return [], None
        if not path:
            return [], None
        return path, {
            "mode": "escape",
            "setpoints": [{"ee_pos": list(pose)} for pose in path],
            "note": "escape proposal only; the interlock holds until these setpoints are gated",
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_dynamic_keep_outs(
    config: PhysicalVLAConfig,
    supplied: Any,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Normalise a caller's per-step keep-outs against the manifest's caps.

    Returns the shapes the sweep may use, how many the caller supplied, and the
    reasons any of it was refused. A refusal returns no shapes at all: half a
    hazard set is a gate that reports COMPLIANT while an obstacle is missing
    from its view, so a set is either wholly checkable or wholly refused.
    """
    if supplied is None:
        return [], 0, []
    if not isinstance(supplied, Sequence) or isinstance(supplied, (str, bytes)):
        return [], 0, ["dynamic_keep_out:not_a_shape_list"]

    count = len(supplied)
    if config.max_dynamic_keep_outs <= 0:
        return [], count, [f"dynamic_keep_out:disabled:count={count}"]
    if count > config.max_dynamic_keep_outs:
        return [], count, [f"dynamic_keep_out:count:{count}>{config.max_dynamic_keep_outs}"]

    shapes: list[dict[str, Any]] = []
    reasons: list[str] = []
    radius_cap = config.max_dynamic_radius_m
    for index, raw in enumerate(supplied):
        try:
            shape = parse_keep_out_shape(f"dynamic_keep_outs[{index}]", raw)
        except VLAManifestError as exc:
            reasons.append(f"dynamic_keep_out:shape:{index}:{exc}")
            continue
        radius = shape.get("radius")
        if radius_cap is not None and radius is not None and radius > radius_cap:
            reasons.append(f"dynamic_keep_out:radius:{index}:{radius:.4f}m>{radius_cap}m")
            continue
        shapes.append(shape)

    if reasons:
        return [], count, reasons
    return shapes, count, []


def _chunk_velocity(chunk: Sequence[Mapping[str, Any]]) -> list[float] | None:
    """EE velocity over the last interval of a chunk, in m/s, or None if unknown."""
    if len(chunk) < 2:
        return None
    previous = _try_vector(chunk[-2], "ee_pos", 3)
    latest = _try_vector(chunk[-1], "ee_pos", 3)
    duration_ms = chunk[-1].get("duration_ms") or chunk[-2].get("duration_ms")
    if previous is None or latest is None or not duration_ms:
        return None
    seconds = float(duration_ms) / 1000.0
    if seconds <= 0.0:
        return None
    return [(latest[axis] - previous[axis]) / seconds for axis in range(3)]


def _try_vector(source: Mapping[str, Any], key: str, length: int | None) -> list[float] | None:
    if not isinstance(source, Mapping) or key not in source or source[key] is None:
        return None
    value = source[key]
    try:
        if length is None:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return None
            out = [float(item) for item in value]
            return out if all(math.isfinite(item) for item in out) else None
        return _finite_vector(key, value, length)
    except (ValueError, TypeError):
        return None


def _scalar_or_norm(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        try:
            components = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(item) for item in components):
            return None
        return math.sqrt(sum(item * item for item in components))
    return None


def _decide(
    config: PhysicalVLAConfig,
    violations: list[tuple[str, str]],
    clamp_limit: int,
) -> dict[str, Any]:
    """Pick the most severe configured action across the classes that fired."""
    order = {"CLAMP": 1, "SOFT_STOP": 2, "E_STOP": 3}
    worst = "CLAMP"
    clamped_index = None
    for violation_class, _reason in violations:
        action = config.policy_for(violation_class)
        if order[action] > order[worst]:
            worst = action
        if violation_class in ("stale_action", "kinematic") and action == "CLAMP":
            # Clamp to the first element that may still execute. A kinematic
            # violation at index 0 leaves nothing legal to execute, and the
            # caller gets a stop rather than a full chunk that happens to be
            # labelled CLAMPED.
            clamped_index = clamp_limit if clamped_index is None else min(clamped_index, clamp_limit)

    if worst == "CLAMP":
        if clamped_index is None or clamped_index <= 0:
            return {"verdict": VERDICT_FORBIDDEN, "state": SOFT_STOP, "clamped_index": None}
        return {"verdict": VERDICT_CLAMPED, "state": CLAMPED, "clamped_index": clamped_index}
    if worst == "SOFT_STOP":
        return {"verdict": VERDICT_FORBIDDEN, "state": SOFT_STOP, "clamped_index": None}
    return {"verdict": VERDICT_FORBIDDEN, "state": E_STOP, "clamped_index": None}


def _keep_out_refusal(violations: Sequence[tuple[str, str]]) -> bool:
    """Whether this refusal is about a keep-out volume.

    Both spellings count: `workspace_keep_out` is a chunk that enters one, and
    `recovery_required:keep_out` is a chunk that digs deeper into one the robot
    is already inside. The second is the freeze this payload exists for — a gate
    that only offered an escape from a clean start would offer it never.
    """
    for violation_class, reason in violations:
        if violation_class == "workspace_keep_out":
            return True
        if violation_class == "recovery" and "keep_out" in reason:
            return True
    return False


def _recovery_hint(
    config: PhysicalVLAConfig,
    reasons: list[str],
    state: str,
    chunk: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Machine-readable direction for a recovery policy. Never a suggestion to relax a limit."""
    classes = sorted({reason.split(":", 1)[0] for reason in reasons})
    hint: dict[str, Any] = {"state": state, "violated_classes": classes, "requires_human_reset": state == E_STOP}
    if "stale_action" in classes:
        hint["action"] = "reobserve_and_replan_within_max_action_age_ms"
        hint["max_action_age_ms"] = config.max_action_age_ms
    elif "dynamic_keep_out" in classes:
        hint["action"] = "supply_a_manifest_authorised_keep_out_set"
        hint["max_dynamic_keep_outs"] = config.max_dynamic_keep_outs
        if config.max_dynamic_radius_m is not None:
            hint["max_dynamic_radius_m"] = config.max_dynamic_radius_m
    elif "workspace_keep_out" in classes or "workspace_keep_in" in classes:
        hint["action"] = "replan_path_around_obstacle"
    elif "kinematic" in classes:
        hint["action"] = "reduce_step_size_or_split_chunk"
    elif "wrench" in classes:
        hint["action"] = "reduce_contact_or_reduce_speed"
    elif state == SOFT_STOP:
        hint["action"] = "hold_and_replan"
    else:
        hint["action"] = "none"
    if chunk:
        hint["chunk_length"] = len(chunk)
    return hint
