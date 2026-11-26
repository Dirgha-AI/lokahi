"""Translation between recorded ROS 2 messages and the gate's formats.

This module is translation only: message dicts in, gate dicts out, and back.
It opens no socket, declares no node and holds no subscription.

What it has and has not seen
----------------------------
**This is fixture-tested translation. It has never been run against a live
rclpy node, a real DDS graph or a real robot.** The fixtures under
`data/vla_fixtures/` are hand-recorded JSON objects with ROS 2 field names, not
captures from a running system. Read `data/vla_fixtures/README.md` before
trusting a field name; treat this module as the shape contract and the wiring
as still unproven.

Message dicts are the form `rosidl_runtime_py.convert.message_to_ordereddict`
produces from a live message and `json.dumps` then serialises, so a recorded
bag can be replayed through these functions with no rclpy in the process.

A JointTrajectory carries joint angles and the gate works in Cartesian space, so
something has to close that gap. There are two ways, and
`joint_trajectory_to_chunk` takes exactly one of them:

* `cartesian_path` — the path the caller already captured, from TF or from the
  policy's own Cartesian output.
* `chain` — a `vla.kinematics.UrdfChain`. The adapter then runs forward
  kinematics on each point's `positions` itself, with no extra dependency: the
  URDF evaluator in `vla/kinematics.py` is plain Python. `Pinocchio` is the
  optional accelerator for models beyond a serial chain.

A JointTrajectory with neither is refused, and pretending otherwise would let an
unchecked path through.

Timestamps are nanosecond integers throughout, which is what the gate takes:
ROS 2 `builtin_interfaces/Time` is `{sec, nanosec}` and a `Duration` is the
same pair without the epoch meaning.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

NANOSEC_IN_SEC = 1_000_000_000


class ROS2AdapterError(ValueError):
    """A message dict that does not have the ROS 2 shape this adapter expects."""


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

def stamp_to_ns(stamp: Any) -> int:
    """`builtin_interfaces/Time` or `Duration` -> nanoseconds. An int passes through."""
    if stamp is None:
        raise ROS2AdapterError("stamp is missing")
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        return stamp
    if not isinstance(stamp, Mapping):
        raise ROS2AdapterError(f"stamp must be an object with sec and nanosec, got {type(stamp).__name__}")
    seconds = _integer(stamp.get("sec"), "sec")
    nanoseconds = _integer(stamp.get("nanosec"), "nanosec")
    return seconds * NANOSEC_IN_SEC + nanoseconds


def ns_to_stamp(value: int) -> dict[str, int]:
    """Nanoseconds -> `{sec, nanosec}` for a Time or a Duration header field."""
    total = _integer(value, "ns")
    seconds, remainder = divmod(abs(total), NANOSEC_IN_SEC)
    sign = -1 if total < 0 else 1
    return {"sec": sign * seconds, "nanosec": sign * remainder}


def duration_to_ns(duration: Any) -> int:
    """`builtin_interfaces/Duration` -> nanoseconds. Same shape as a Time stamp."""
    return stamp_to_ns(duration)


def ns_to_duration(value: int) -> dict[str, int]:
    """Nanoseconds -> `{sec, nanosec}` for a Duration field."""
    return ns_to_stamp(value)


def header_stamp_ns(message: Mapping[str, Any]) -> int | None:
    """The message's `header.stamp` in nanoseconds, or None when it has no header."""
    header = _field(message, "header")
    if header is None:
        return None
    stamp = _field(header, "stamp")
    return None if stamp is None else stamp_to_ns(stamp)


def header_frame_id(message: Mapping[str, Any]) -> str:
    header = _field(message, "header")
    if header is None:
        return ""
    frame = _field(header, "frame_id")
    return "" if frame is None else str(frame)


# ---------------------------------------------------------------------------
# sensor_msgs/JointState -> the gate's robot_state
# ---------------------------------------------------------------------------

def joint_state_to_robot_state(
    message: Mapping[str, Any],
    *,
    ee_pos: Any = None,
    ee_quat: Any = None,
) -> dict[str, Any]:
    """Build a gate `robot_state` from a JointState plus a Cartesian pose.

    The end-effector pose is not in a JointState — it comes from TF at the same
    stamp — so the caller supplies it. `joint_names` is carried through so the
    joint order the gate validates against is visible downstream; the gate
    ignores the key.
    """
    positions = _numbers(_field(message, "position"), "JointState.position")
    state: dict[str, Any] = {"joint_pos": positions}
    names = _field(message, "name")
    if names is not None:
        state["joint_names"] = [str(name) for name in _sequence(names, "JointState.name")]
    if ee_pos is not None:
        state["ee_pos"] = _xyz(ee_pos, "ee_pos")
    if ee_quat is not None:
        state["ee_quat"] = _numbers(ee_quat, "ee_quat")
    return state


# ---------------------------------------------------------------------------
# control_msgs/JointTrajectory <-> gate chunk
# ---------------------------------------------------------------------------

def _kinematics_path(message: Mapping[str, Any], points: Sequence[Any],
                     chain: Any) -> tuple[list[list[float]], list[list[float]]]:
    """FK for every trajectory point, using the joint names the message carries."""
    names = _sequence(_field(message, "joint_names"), "JointTrajectory.joint_names")
    poses: list[list[float]] = []
    quaternions: list[list[float]] = []
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            raise ROS2AdapterError(f"points[{index}] must be an object")
        positions = _numbers(_field(point, "positions"), f"points[{index}].positions")
        if len(positions) != len(names):
            raise ROS2AdapterError(
                f"points[{index}].positions has {len(positions)} values for {len(names)} joint names"
            )
        try:
            pose = chain.forward(dict(zip(names, positions)))
        except Exception as exc:
            raise ROS2AdapterError(f"forward kinematics failed at point {index}: {exc}") from exc
        poses.append(list(pose.position))
        quaternions.append(list(pose.quaternion))
    return poses, quaternions


def joint_trajectory_to_chunk(
    message: Mapping[str, Any],
    *,
    cartesian_path: Sequence[Any] | None = None,
    ee_quat: Sequence[Any] | None = None,
    chain: Any = None,
) -> list[dict[str, Any]]:
    """Recorded JointTrajectory -> gate chunk, one element per trajectory point.

    `duration_ms` is the gap between successive `time_from_start` values, which
    is what the gate's freshness sweep needs; the first element's duration is
    its own `time_from_start` measured from the trajectory's stamp.

    Supply either `cartesian_path` or `chain`, never both: a path captured
    alongside the trajectory and a path computed from it are different sources
    of truth, and silently preferring one would hide which was gated.
    """
    points = _sequence(_field(message, "points"), "JointTrajectory.points")
    if not points:
        raise ROS2AdapterError("JointTrajectory has no points")
    if chain is not None and cartesian_path is not None:
        raise ROS2AdapterError("give either cartesian_path or chain, not both")
    if chain is not None:
        poses, derived_quaternions = _kinematics_path(message, points, chain)
        if ee_quat is not None:
            raise ROS2AdapterError("ee_quat is derived from chain; do not pass it as well")
        cartesian_path = poses
        ee_quat = derived_quaternions
    if cartesian_path is None:
        raise ROS2AdapterError(
            "a JointTrajectory needs cartesian_path or chain; joint angles alone cannot be gated"
        )
    poses = [_xyz(pose, f"cartesian_path[{index}]") for index, pose in enumerate(cartesian_path)]
    if len(poses) != len(points):
        raise ROS2AdapterError(
            f"cartesian_path has {len(poses)} poses for {len(points)} trajectory points"
        )
    quaternions = None
    if ee_quat is not None:
        quaternions = [_numbers(quat, f"ee_quat[{index}]") for index, quat in enumerate(ee_quat)]
        if len(quaternions) != len(points):
            raise ROS2AdapterError(
                f"ee_quat has {len(quaternions)} quaternions for {len(points)} trajectory points"
            )

    chunk: list[dict[str, Any]] = []
    previous_ns = 0
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            raise ROS2AdapterError(f"points[{index}] must be an object")
        reached_ns = duration_to_ns(point.get("time_from_start"))
        if reached_ns < previous_ns:
            raise ROS2AdapterError(f"points[{index}].time_from_start goes backwards")
        element: dict[str, Any] = {
            "ee_pos": poses[index],
            "duration_ms": (reached_ns - previous_ns) / 1e6,
        }
        if quaternions is not None:
            element["ee_quat"] = quaternions[index]
        positions = point.get("positions")
        if positions is not None:
            element["joint_pos"] = _numbers(positions, f"points[{index}].positions")
        chunk.append(element)
        previous_ns = reached_ns
    return chunk


def chunk_to_joint_trajectory(
    chunk: Sequence[Mapping[str, Any]],
    *,
    joint_names: Sequence[Any],
    frame_id: str = "",
    stamp_ns: int | None = None,
) -> dict[str, Any]:
    """Gate chunk -> JointTrajectory dict, with cumulative `time_from_start`.

    A JointTrajectory carries joint positions and nothing Cartesian, so an
    element without `joint_pos` is an error rather than a silently empty point.
    """
    names = [str(name) for name in _sequence(joint_names, "joint_names")]
    if not names:
        raise ROS2AdapterError("joint_names is empty")

    points: list[dict[str, Any]] = []
    elapsed_ns = 0
    for index, element in enumerate(chunk):
        if not isinstance(element, Mapping):
            raise ROS2AdapterError(f"chunk[{index}] must be an object")
        joints = element.get("joint_pos")
        if joints is None:
            raise ROS2AdapterError(
                f"chunk[{index}] has no joint_pos; a JointTrajectory carries joint positions only"
            )
        values = _numbers(joints, f"chunk[{index}].joint_pos")
        if len(values) != len(names):
            raise ROS2AdapterError(
                f"chunk[{index}].joint_pos has {len(values)} values for {len(names)} joint names"
            )
        elapsed_ns += int(round(float(element.get("duration_ms", 0.0)) * 1e6))
        points.append({
            "positions": values,
            "velocities": [],
            "accelerations": [],
            "effort": [],
            "time_from_start": ns_to_duration(elapsed_ns),
        })
    return {
        "header": {"stamp": ns_to_stamp(0 if stamp_ns is None else stamp_ns), "frame_id": frame_id},
        "joint_names": names,
        "points": points,
    }


# ---------------------------------------------------------------------------
# geometry_msgs/TwistStamped
# ---------------------------------------------------------------------------

def twist_stamped_to_velocity(message: Mapping[str, Any]) -> dict[str, Any]:
    """TwistStamped -> `{linear, angular, frame_id, stamp_ns}`.

    A twist is a velocity command, not a chunk: the gate sweeps positions, so a
    twist has to be integrated into a Cartesian path before it can be gated.
    This translation exists so a teleoperation bridge can do exactly that with
    one conversion it can test.
    """
    twist = _field(message, "twist")
    if not isinstance(twist, Mapping):
        raise ROS2AdapterError("TwistStamped.twist is missing or not an object")
    return {
        "linear": _xyz(_field(twist, "linear"), "twist.linear"),
        "angular": _xyz(_field(twist, "angular"), "twist.angular"),
        "frame_id": header_frame_id(message),
        "stamp_ns": header_stamp_ns(message),
    }


def velocity_to_twist_stamped(
    velocity: Mapping[str, Any],
    *,
    frame_id: str = "",
    stamp_ns: int | None = None,
) -> dict[str, Any]:
    """`{linear, angular}` -> TwistStamped dict, for a command the gate approved."""
    return {
        "header": {"stamp": ns_to_stamp(0 if stamp_ns is None else stamp_ns), "frame_id": frame_id},
        "twist": {
            "linear": _vector3(velocity.get("linear"), "linear"),
            "angular": _vector3(velocity.get("angular"), "angular"),
        },
    }


# ---------------------------------------------------------------------------
# geometry_msgs/WrenchStamped <-> gate wrench
# ---------------------------------------------------------------------------

def wrench_stamped_to_gate(message: Mapping[str, Any]) -> dict[str, Any]:
    """WrenchStamped -> the gate's `wrench` argument, in newtons and newton-metres."""
    wrench = _field(message, "wrench")
    if not isinstance(wrench, Mapping):
        raise ROS2AdapterError("WrenchStamped.wrench is missing or not an object")
    return {
        "force_n": _xyz(_field(wrench, "force"), "wrench.force"),
        "torque_nm": _xyz(_field(wrench, "torque"), "wrench.torque"),
    }


def wrench_gate_to_stamped(
    wrench: Mapping[str, Any],
    *,
    frame_id: str = "",
    stamp_ns: int | None = None,
) -> dict[str, Any]:
    """The gate's wrench dict -> WrenchStamped dict, for logging on a topic."""
    return {
        "header": {"stamp": ns_to_stamp(0 if stamp_ns is None else stamp_ns), "frame_id": frame_id},
        "wrench": {
            "force": _vector3(wrench.get("force_n"), "force_n"),
            "torque": _vector3(wrench.get("torque_nm"), "torque_nm"),
        },
    }


# ---------------------------------------------------------------------------
# field coercion
# ---------------------------------------------------------------------------

def _field(source: Any, key: str) -> Any:
    if not isinstance(source, Mapping):
        raise ROS2AdapterError(f"expected an object carrying {key!r}, got {type(source).__name__}")
    return source.get(key)


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ROS2AdapterError(f"{name} must be an integer, got {type(value).__name__}")
    return value


def _sequence(value: Any, name: str) -> list:
    if value is None:
        raise ROS2AdapterError(f"{name} is missing")
    converter = getattr(value, "tolist", None)
    if callable(converter):
        value = converter()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ROS2AdapterError(f"{name} must be an array, got {type(value).__name__}")
    return list(value)


def _numbers(value: Any, name: str) -> list[float]:
    out = []
    for position, item in enumerate(_sequence(value, name)):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ROS2AdapterError(f"{name}[{position}] is not a number")
        out.append(float(item))
    return out


def _xyz(value: Any, name: str) -> list[float]:
    """geometry_msgs/Vector3 as an object, or a three-element array."""
    if isinstance(value, Mapping):
        return [
            _coordinate(value.get("x"), f"{name}.x"),
            _coordinate(value.get("y"), f"{name}.y"),
            _coordinate(value.get("z"), f"{name}.z"),
        ]
    numbers = _numbers(value, name)
    if len(numbers) != 3:
        raise ROS2AdapterError(f"{name} must have 3 components, got {len(numbers)}")
    return numbers


def _vector3(value: Any, name: str) -> dict[str, float]:
    numbers = _xyz(value, name)
    return {"x": numbers[0], "y": numbers[1], "z": numbers[2]}


def _coordinate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ROS2AdapterError(f"{name} is not a number")
    return float(value)
