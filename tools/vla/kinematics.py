"""Forward kinematics between a joint command and the Cartesian pose the gate checks.

A ROS 2 `JointTrajectory` carries joint positions and nothing else, so a
deployment that gates Cartesian pose has to run FK itself. This module is that
step. The core evaluator reads URDF and composes the chain in plain Python, so
it needs no extra package; Pinocchio (BSD-2-Clause) is used when it is
installed, for models larger than a chain or for URDF features this evaluator
does not claim to support:

    pip install "lokahi[vla-kinematics]"

The core evaluator is deliberately narrow: serial chains of revolute,
continuous, prismatic and fixed joints. It raises on anything else rather than
guessing, because a guessed pose is a wrong pose at an actuator.

Credits: the optional backend is Pinocchio
(https://github.com/stack-of-tasks/pinocchio), BSD-2-Clause. See CREDITS.md.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

KINEMATICS_EXTRA = "lokahi[vla-kinematics]"
_SUPPORTED_JOINTS = ("revolute", "continuous", "prismatic", "fixed")

Matrix = tuple[tuple[float, ...], ...]


class KinematicsError(ValueError):
    """The model cannot be evaluated by this build. Never a silent identity."""


@dataclass(frozen=True)
class Pose:
    position: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]  # w, x, y, z

    def as_state_fields(self) -> dict[str, list[float]]:
        return {"ee_pos": list(self.position), "ee_quat": list(self.quaternion)}


@dataclass(frozen=True)
class Joint:
    name: str
    kind: str
    parent: str
    child: str
    axis: tuple[float, float, float]
    origin_xyz: tuple[float, float, float]
    origin_rpy: tuple[float, float, float]
    lower: float = 0.0
    upper: float = 0.0

    @property
    def moves(self) -> bool:
        return self.kind != "fixed"


def pinocchio_available() -> bool:
    """Whether the optional Pinocchio backend can be imported right now."""
    try:  # pragma: no cover - depends on the installed environment
        import pinocchio  # noqa: F401
    except Exception:
        return False
    return True


def backends() -> dict[str, bool]:
    return {"urdf_chain": True, "pinocchio": pinocchio_available()}


def _identity() -> Matrix:
    return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return tuple(
        tuple(sum(a[row][k] * b[k][column] for k in range(4)) for column in range(4))
        for row in range(4)
    )


def _rot_x(angle: float) -> Matrix:
    c, s = math.cos(angle), math.sin(angle)
    return ((1.0, 0.0, 0.0, 0.0), (0.0, c, -s, 0.0), (0.0, s, c, 0.0), (0.0, 0.0, 0.0, 1.0))


def _rot_y(angle: float) -> Matrix:
    c, s = math.cos(angle), math.sin(angle)
    return ((c, 0.0, s, 0.0), (0.0, 1.0, 0.0, 0.0), (-s, 0.0, c, 0.0), (0.0, 0.0, 0.0, 1.0))


def _rot_z(angle: float) -> Matrix:
    c, s = math.cos(angle), math.sin(angle)
    return ((c, -s, 0.0, 0.0), (s, c, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def _rotate_about(axis: Sequence[float], angle: float) -> Matrix:
    """Rotation by `angle` about an arbitrary unit axis (Rodrigues)."""
    length = math.sqrt(sum(component * component for component in axis))
    if length < 1e-12:
        raise KinematicsError("joint axis is degenerate")
    x, y, z = (component / length for component in axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _quaternion(matrix: Matrix) -> tuple[float, float, float, float]:
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (matrix[2][1] - matrix[1][2]) / scale
        y = (matrix[0][2] - matrix[2][0]) / scale
        z = (matrix[1][0] - matrix[0][1]) / scale
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2.0
        w = (matrix[2][1] - matrix[1][2]) / scale
        x = 0.25 * scale
        y = (matrix[0][1] + matrix[1][0]) / scale
        z = (matrix[0][2] + matrix[2][0]) / scale
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2.0
        w = (matrix[0][2] - matrix[2][0]) / scale
        x = (matrix[0][1] + matrix[1][0]) / scale
        y = 0.25 * scale
        z = (matrix[1][2] + matrix[2][1]) / scale
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2.0
        w = (matrix[1][0] - matrix[0][1]) / scale
        x = (matrix[0][2] + matrix[2][0]) / scale
        y = (matrix[1][2] + matrix[2][1]) / scale
        z = 0.25 * scale
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-12:
        raise KinematicsError("rotation matrix produced a degenerate quaternion")
    return (w / norm, x / norm, y / norm, z / norm)


def _numbers(text: str | None, size: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if text is None:
        return default
    parts = text.split()
    if len(parts) != size:
        raise KinematicsError(f"expected {size} numbers, got {len(parts)}: {text!r}")
    return tuple(float(part) for part in parts)


class UrdfChain:
    """A serial chain from `base_link` to `tip_link`, evaluated in plain Python."""

    def __init__(self, joints: Sequence[Joint], base_link: str, tip_link: str):
        self.joints = tuple(joints)
        self.base_link = base_link
        self.tip_link = tip_link

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(joint.name for joint in self.joints if joint.moves)

    @classmethod
    def from_urdf(cls, document: str, base_link: str | None = None,
                  tip_link: str | None = None) -> "UrdfChain":
        try:
            root = ElementTree.fromstring(document)
        except ElementTree.ParseError as exc:
            raise KinematicsError(f"URDF is not well-formed XML: {exc}") from exc
        if root.tag != "robot":
            raise KinematicsError(f"URDF root must be <robot>, got <{root.tag}>")
        all_joints: list[Joint] = []
        for element in root.findall("joint"):
            name = element.get("name")
            kind = element.get("type")
            if not name or not kind:
                raise KinematicsError("every <joint> needs a name and a type")
            if kind not in _SUPPORTED_JOINTS:
                raise KinematicsError(
                    f"joint {name!r} is type {kind!r}, which this evaluator does not model; "
                    f"install '{KINEMATICS_EXTRA}' and use the Pinocchio backend"
                )
            parent = element.find("parent")
            child = element.find("child")
            if parent is None or child is None:
                raise KinematicsError(f"joint {name!r} needs a parent and a child link")
            origin = element.find("origin")
            axis_element = element.find("axis")
            limit = element.find("limit")
            all_joints.append(Joint(
                name=name,
                kind=kind,
                parent=parent.get("link") or "",
                child=child.get("link") or "",
                axis=_numbers(axis_element.get("xyz") if axis_element is not None else None,
                              3, (1.0, 0.0, 0.0)),
                origin_xyz=_numbers(origin.get("xyz") if origin is not None else None,
                                    3, (0.0, 0.0, 0.0)),
                origin_rpy=_numbers(origin.get("rpy") if origin is not None else None,
                                    3, (0.0, 0.0, 0.0)),
                lower=float(limit.get("lower")) if limit is not None and limit.get("lower") else 0.0,
                upper=float(limit.get("upper")) if limit is not None and limit.get("upper") else 0.0,
            ))

        by_child = {joint.child: joint for joint in all_joints}
        resolved_tip = tip_link or cls._pick(all_joints, want_child=True)
        resolved_base = base_link
        chain: list[Joint] = []
        link = resolved_tip
        guard = 0
        while True:
            joint = by_child.get(link)
            if joint is None:
                break
            chain.append(joint)
            link = joint.parent
            if resolved_base is not None and link == resolved_base:
                break
            guard += 1
            if guard > len(all_joints) + 1:
                raise KinematicsError("URDF joint graph has a cycle")
        if not chain:
            raise KinematicsError(f"no joint chain ends at link {resolved_tip!r}")
        if resolved_base is None:
            resolved_base = link
        return cls(tuple(reversed(chain)), resolved_base, resolved_tip)

    @staticmethod
    def _pick(joints: Sequence[Joint], want_child: bool) -> str:
        parents = {joint.parent for joint in joints}
        leaves = [joint.child for joint in joints if joint.child not in parents]
        if len(leaves) != 1:
            raise KinematicsError(
                f"URDF has {len(leaves)} leaf links; name tip_link explicitly"
            )
        return leaves[0]

    def forward(self, positions: Mapping[str, float]) -> Pose:
        transform = _identity()
        for joint in self.joints:
            transform = _multiply(transform, _origin_matrix(joint))
            if not joint.moves:
                continue
            if joint.name not in positions:
                raise KinematicsError(f"joint {joint.name!r} has no commanded position")
            value = float(positions[joint.name])
            if joint.kind == "prismatic":
                movement = list(_identity())
                movement[0] = (1.0, 0.0, 0.0, joint.axis[0] * value)
                movement[1] = (0.0, 1.0, 0.0, joint.axis[1] * value)
                movement[2] = (0.0, 0.0, 1.0, joint.axis[2] * value)
                transform = _multiply(transform, tuple(movement))
            else:
                transform = _multiply(transform, _rotate_about(joint.axis, value))
        position = (transform[0][3], transform[1][3], transform[2][3])
        return Pose(position=position, quaternion=_quaternion(transform))

    def trajectory_poses(self, trajectory: Sequence[Mapping[str, float]]) -> list[Pose]:
        """FK for every point of a joint trajectory, in order."""
        return [self.forward(point) for point in trajectory]


def _origin_matrix(joint: Joint) -> Matrix:
    roll, pitch, yaw = joint.origin_rpy
    rotation = _multiply(_multiply(_rot_z(yaw), _rot_y(pitch)), _rot_x(roll))
    return (
        (rotation[0][0], rotation[0][1], rotation[0][2], joint.origin_xyz[0]),
        (rotation[1][0], rotation[1][1], rotation[1][2], joint.origin_xyz[1]),
        (rotation[2][0], rotation[2][1], rotation[2][2], joint.origin_xyz[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def load_urdf(path: str, base_link: str | None = None,
              tip_link: str | None = None) -> UrdfChain:
    with open(path, "r", encoding="utf-8") as handle:
        return UrdfChain.from_urdf(handle.read(), base_link=base_link, tip_link=tip_link)


def pinocchio_forward(urdf_path: str, positions: Mapping[str, float]) -> Pose:
    """Pinocchio FK, for models beyond a serial chain. Requires the optional extra."""
    try:  # pragma: no cover - requires the extra
        import pinocchio
    except Exception as exc:  # pragma: no cover
        raise KinematicsError(
            f"the Pinocchio backend needs pinocchio: pip install '{KINEMATICS_EXTRA}'"
        ) from exc
    try:  # pragma: no cover - requires the extra
        import numpy  # noqa: F401  # Pinocchio imports numpy itself

        model = pinocchio.buildModelFromUrdf(urdf_path)
        data = model.createData()
        configuration = pinocchio.neutral(model)
        for name, value in positions.items():
            joint_id = model.getJointId(name)
            if joint_id == 0:
                raise KinematicsError(f"model has no joint named {name!r}")
            index = model.joints[joint_id].idx_q
            if index >= len(configuration):
                raise KinematicsError(f"joint {name!r} has no configuration slot")
            configuration[index] = float(value)
        pinocchio.forwardKinematics(model, data, configuration)
        frame_id = model.getFrameId(model.frames[-1].name)
        placement = data.oMf[frame_id]
        translation = placement.translation
        quaternion = pinocchio.Quaternion(placement.rotation)
        return Pose(
            position=(float(translation[0]), float(translation[1]), float(translation[2])),
            quaternion=(float(quaternion.w), float(quaternion.x),
                        float(quaternion.y), float(quaternion.z)),
        )
    except KinematicsError:
        raise
    except Exception as exc:  # pragma: no cover
        raise KinematicsError(f"Pinocchio could not evaluate this model: {exc}") from exc
