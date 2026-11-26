"""Deterministic escape planning for a pose that is already inside a keep-out.

A gate that refuses every illegal one-step move turns a policy error into a
permanent one: the robot sits in the hazard and the outside world keeps charging
it. The refusal is still correct — the chunk must not execute — so what this
module supplies is a second artefact beside the refusal: a path that leaves.

Everything here is a pure function of its arguments. No clock, no randomness, no
third-party import: the same inputs give the same path on every call, so a
replay of a logged decision reproduces the escape it proposed.

Clearance is the signed distance from a pose to the nearest keep-out surface:
positive outside, negative inside, and measured against geometry the caller has
already grown by the tool radius. The escape rule is that minimum clearance
strictly increases at every step — moving deeper, or sideways at the same depth,
is not escape and is never proposed.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from . import geometry

AXIS_DIRECTIONS = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
)
PLANAR_DIRECTIONS = AXIS_DIRECTIONS[:4]

# A step that improves clearance by less than this is float noise on a pose that
# is not moving, not an escape. It keeps "strictly increases" meaningful.
_MIN_GAIN_M = 1e-12
_EPS = 1e-12


class RecoveryError(ValueError):
    """A keep-out whose clearance this build cannot measure. Never guess a path."""


# ---------------------------------------------------------------------------
# clearance
# ---------------------------------------------------------------------------

def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecoveryError(f"{name} contains a non-number")
    number = float(value)
    if not math.isfinite(number):
        raise RecoveryError(f"{name} contains a non-finite value")
    return number


def _pose(name: str, value: Any) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RecoveryError(f"{name} must be a list of 3 numbers")
    if len(value) != 3:
        raise RecoveryError(f"{name} must have 3 elements, got {len(value)}")
    return (*(_finite(name, item) for item in value),)  # type: ignore[return-value]


def _unit(vector: Sequence[float]) -> tuple[float, float, float] | None:
    norm = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
    if norm < _EPS:
        return None
    return (vector[0] / norm, vector[1] / norm, vector[2] / norm)


def _box_distance(point: Sequence[float], box: Sequence[float]) -> tuple[float, tuple[float, float, float]]:
    """Signed distance to an AABB and the unit direction of steepest increase.

    Outside the box the direction is away from the nearest face or edge; inside
    it, the direction of increasing signed distance points at the nearest way
    out. Both are the gradient of the same function, which is what makes the
    per-step comparison in `escape_path` monotone.
    """
    outside_sq = 0.0
    outside = [0.0, 0.0, 0.0]
    inside_axis = -1
    inside_distance = math.inf
    inside_sign = 0.0
    for axis in range(3):
        low, high = float(box[axis]), float(box[axis + 3])
        value = point[axis]
        if value < low:
            gap = low - value
            outside_sq += gap * gap
            outside[axis] = -1.0
        elif value > high:
            gap = value - high
            outside_sq += gap * gap
            outside[axis] = 1.0
        else:
            to_low = value - low
            to_high = high - value
            if to_low <= to_high:
                distance, sign = to_low, -1.0
            else:
                distance, sign = to_high, 1.0
            if distance < inside_distance:
                inside_distance, inside_axis, inside_sign = distance, axis, sign
    if outside_sq > 0.0:
        return math.sqrt(outside_sq), _unit(outside) or (1.0, 0.0, 0.0)
    direction = [0.0, 0.0, 0.0]
    direction[inside_axis] = inside_sign
    return -inside_distance, (direction[0], direction[1], direction[2])


def _circle_distance(point: Sequence[float], centre: Sequence[float],
                     radius: float) -> tuple[float, tuple[float, float, float]]:
    dx, dy = point[0] - float(centre[0]), point[1] - float(centre[1])
    distance = math.hypot(dx, dy)
    if distance < _EPS:
        return -radius, (1.0, 0.0, 0.0)
    return distance - radius, (dx / distance, dy / distance, 0.0)


def _cylinder_distance(point: Sequence[float], centre: Sequence[float], radius: float,
                       height: float) -> tuple[float, tuple[float, float, float]]:
    dx, dy = point[0] - float(centre[0]), point[1] - float(centre[1])
    low = float(centre[2])
    high = low + height
    radial = math.hypot(dx, dy)
    dr = radial - radius
    dz_low = low - point[2]
    dz_high = point[2] - high
    if dr > 0.0 or dz_low > 0.0 or dz_high > 0.0:
        radial_unit = (dx / radial, dy / radial, 0.0) if radial > _EPS else (0.0, 0.0, 0.0)
        radial_out = max(dr, 0.0)
        axial_out = max(dz_low, dz_high, 0.0)
        axial_sign = -1.0 if dz_low > dz_high else 1.0
        vector = (radial_unit[0] * radial_out, radial_unit[1] * radial_out, axial_sign * axial_out)
        direction = _unit(vector) or (1.0, 0.0, 0.0)
        return math.hypot(radial_out, axial_out), direction
    # Inside: the nearest of the three surfaces wins, exactly as for a box.
    candidates = (dr, dz_low, dz_high)
    binding = max(range(3), key=lambda index: candidates[index])
    if binding == 0:
        direction = (dx / radial, dy / radial, 0.0) if radial > _EPS else (1.0, 0.0, 0.0)
    else:
        direction = (0.0, 0.0, -1.0 if binding == 1 else 1.0)
    return candidates[binding], direction


def _segment_point(point: Sequence[float], start: Sequence[float],
                   end: Sequence[float]) -> tuple[float, tuple[float, float, float]]:
    ax, ay, az = float(start[0]), float(start[1]), float(start[2])
    dx, dy, dz = float(end[0]) - ax, float(end[1]) - ay, float(end[2]) - az
    denominator = dx * dx + dy * dy + dz * dz
    if denominator < _EPS:
        offset = (point[0] - ax, point[1] - ay, point[2] - az)
        distance = math.sqrt(offset[0] ** 2 + offset[1] ** 2 + offset[2] ** 2)
        return distance, _unit(offset) or (1.0, 0.0, 0.0)
    t = ((point[0] - ax) * dx + (point[1] - ay) * dy + (point[2] - az) * dz) / denominator
    t = max(0.0, min(1.0, t))
    offset = (point[0] - (ax + t * dx), point[1] - (ay + t * dy), point[2] - (az + t * dz))
    distance = math.sqrt(offset[0] ** 2 + offset[1] ** 2 + offset[2] ** 2)
    return distance, _unit(offset) or (1.0, 0.0, 0.0)


def obstacle_clearance(point: Sequence[float], obstacle: Sequence[float] | Mapping[str, Any]
                       ) -> tuple[float, tuple[float, float, float]]:
    """Signed clearance from a pose to one keep-out, plus its gradient direction."""
    if isinstance(obstacle, Mapping):
        kind = obstacle.get("kind") or obstacle.get("shape")
        radius = float(obstacle.get("radius") or 0.0)
        centre = obstacle.get("centre")
        if kind == "circle":
            return _circle_distance(point, centre, radius)
        if kind == "cylinder":
            return _cylinder_distance(point, centre, radius, float(obstacle.get("height") or 0.0))
        if kind == "sphere":
            distance, direction = _segment_point(point, centre, centre)
            return distance - radius, direction
        if kind == "capsule":
            distance, direction = _segment_point(point, obstacle["start"], obstacle["end"])
            return distance - radius, direction
        raise RecoveryError(
            f"escape planning cannot measure a {kind!r} keep-out; "
            "it is offered only for boxes and analytic shapes"
        )
    return _box_distance(point, obstacle)


def _min_clearance(pose: Sequence[float], obstacles: Sequence[Any]) -> float:
    if not obstacles:
        return math.inf
    return min(obstacle_clearance(pose, obstacle)[0] for obstacle in obstacles)


def minimum_clearance(point: Sequence[float],
                      boxes: Sequence[Sequence[float]] = (),
                      shapes: Sequence[Mapping[str, Any]] = ()) -> float:
    """Distance from a pose to the nearest keep-out surface; negative inside one.

    `math.inf` when there is no keep-out at all. Callers supply geometry already
    grown by the tool radius, so this is the clearance of the tool, not the point.
    """
    pose = _pose("point", point)
    return _min_clearance(pose, list(boxes) + list(shapes))


def _nearest(pose: Sequence[float], obstacles: Sequence[Any]
             ) -> tuple[float, tuple[float, float, float]]:
    best: tuple[float, tuple[float, float, float]] | None = None
    for obstacle in obstacles:
        clearance, direction = obstacle_clearance(pose, obstacle)
        if best is None or clearance < best[0]:
            best = (clearance, direction)
    assert best is not None
    return best


# ---------------------------------------------------------------------------
# escape
# ---------------------------------------------------------------------------

def _is_planar(boxes: Sequence[Sequence[float]], shapes: Sequence[Mapping[str, Any]],
               workspace_aabb: Sequence[float]) -> bool:
    """Whether every keep-out is invariant along z over the workspace.

    A circle is 2D by definition; a box or cylinder is only 2D if it spans the
    whole workspace extent, since otherwise going over the top is a way out and
    a planner restricted to the plane would never find it.
    """
    for box in boxes:
        if float(box[2]) > float(workspace_aabb[2]) or float(box[5]) < float(workspace_aabb[5]):
            return False
    for shape in shapes:
        kind = shape.get("kind") or shape.get("shape")
        if kind == "circle":
            continue
        if kind == "cylinder":
            low = float(shape["centre"][2])
            high = low + float(shape["height"])
            if low <= float(workspace_aabb[2]) and high >= float(workspace_aabb[5]):
                continue
        return False
    return True


def _directions(pose: Sequence[float], obstacles: Sequence[Any], planar: bool
                ) -> list[tuple[float, float, float]]:
    """The fixed candidate set: axis directions, then the clearance gradient.

    The set is fixed on purpose. A planner that enumerated a finer fan of angles
    would find slightly better paths on some scenes and different ones on the
    next run of the same scene; these candidates are cheap, exhaustive along the
    axes, and reproducible.
    """
    directions = list(PLANAR_DIRECTIONS if planar else AXIS_DIRECTIONS)
    _, gradient = _nearest(pose, obstacles)
    if planar:
        gradient = _unit((gradient[0], gradient[1], 0.0))
    if gradient is not None:
        directions.append(gradient)
    return directions


def _within(pose: Sequence[float], workspace_aabb: Sequence[float] | None) -> bool:
    if workspace_aabb is None:
        return True
    return (workspace_aabb[0] <= pose[0] <= workspace_aabb[3]
            and workspace_aabb[1] <= pose[1] <= workspace_aabb[4]
            and workspace_aabb[2] <= pose[2] <= workspace_aabb[5])


def _crosses_unentered_obstacle(start: Sequence[float], end: Sequence[float],
                                obstacles: Sequence[Any]) -> bool:
    """Whether the step would newly enter a keep-out it is not already inside.

    Without this the best-clearance pose could be on the far side of the very
    obstacle being escaped, and the escape path would tunnel through it.
    """
    for obstacle in obstacles:
        if obstacle_clearance(start, obstacle)[0] <= 0.0:
            continue
        if isinstance(obstacle, Mapping):
            if geometry.sweep_hits(obstacle, start, end):
                return True
        else:
            if _segment_hits_box(start, end, obstacle):
                return True
    return False


def _segment_hits_box(start: Sequence[float], end: Sequence[float],
                      box: Sequence[float]) -> bool:
    t_enter, t_exit = 0.0, 1.0
    for axis in range(3):
        origin = start[axis]
        delta = end[axis] - origin
        low, high = float(box[axis]), float(box[axis + 3])
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


def escape_path(
    start_pose: Sequence[float],
    boxes: Sequence[Sequence[float]] = (),
    shapes: Sequence[Mapping[str, Any]] = (),
    workspace_aabb: Sequence[float] | None = None,
    step_m: float = 0.02,
    max_steps: int = 50,
) -> list[list[float]]:
    """Poses leaving the keep-outs around `start_pose`, clearance rising each step.

    Returns an empty list when the start pose is already clear, when no candidate
    direction improves clearance, or when there is nothing to escape from. The
    returned path never leaves `workspace_aabb`, never has more than `max_steps`
    poses, and never contains a pose whose minimum clearance is less than the one
    before it.

    `boxes` and `shapes` are keep-outs with the tool radius already applied.
    """
    pose = _pose("start_pose", start_pose)
    step = float(step_m)
    if not math.isfinite(step) or step <= 0.0:
        raise RecoveryError("step_m must be a finite number greater than zero")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise RecoveryError("max_steps must be an integer")
    if max_steps < 0:
        raise RecoveryError("max_steps must not be negative")

    obstacles: list[Any] = [tuple(float(value) for value in box) for box in boxes]
    obstacles += [dict(shape) for shape in shapes]
    if not obstacles:
        return []

    workspace: tuple[float, ...] | None = None
    if workspace_aabb is not None:
        if len(workspace_aabb) != 6:
            raise RecoveryError("workspace_aabb must have exactly 6 elements")
        workspace = tuple(_finite("workspace_aabb", value) for value in workspace_aabb)
    # Shape kinds are validated before any clearance is measured, so a mesh
    # raises here instead of after a partially planned path.
    for obstacle in obstacles:
        obstacle_clearance(pose, obstacle)

    clearance = _min_clearance(pose, obstacles)
    if clearance >= 0.0:
        # The gate treats zero depth as outside, so a pose on a surface is clear
        # here too. Offering it a path would be offering to leave nothing.
        return []

    planar = workspace is not None and _is_planar(
        [obstacle for obstacle in obstacles if not isinstance(obstacle, Mapping)],
        [obstacle for obstacle in obstacles if isinstance(obstacle, Mapping)],
        workspace,
    )
    path: list[list[float]] = []
    current = pose
    for _ in range(max_steps):
        best: tuple[float, tuple[float, float, float]] | None = None
        for direction in _directions(current, obstacles, planar):
            candidate = (current[0] + direction[0] * step,
                         current[1] + direction[1] * step,
                         current[2] + direction[2] * step)
            if not _within(candidate, workspace):
                continue
            if _crosses_unentered_obstacle(current, candidate, obstacles):
                continue
            candidate_clearance = _min_clearance(candidate, obstacles)
            if candidate_clearance <= clearance + _MIN_GAIN_M:
                continue
            if best is None or candidate_clearance > best[0]:
                best = (candidate_clearance, candidate)
        if best is None:
            break
        clearance, current = best
        path.append([current[0], current[1], current[2]])
    return path

