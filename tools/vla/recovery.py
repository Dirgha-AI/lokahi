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
already grown by the tool radius.

The escape rule is depth, not direction. A dense cluster of hazards can leave
every step that raises clearance to all of them geometrically impossible while a
tangential step plainly walks out: with eight packed discs, moving 2 cm towards
open space also moves 2 cm towards a neighbour. What is required here instead is
that the pose's minimum clearance strictly rise at every step, which is the same
statement as "the deepest keep-out gains clearance and no keep-out ends below
the depth the step started at". That admits the sideways step along a neighbour
it cannot yet leave and still refuses anything that finishes deeper than it
began. A step that raises every clearance is preferred when candidates tie on
minimum clearance, so an isolated keep-out is escaped exactly as before.
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
# Evenly spaced in-plane directions, so a destination that is diagonal to the
# arena is a direction rather than a staircase of axis steps. Sixteen gives a
# 22.5-degree gap: fine enough that some candidate always points partly along a
# genuinely tangential channel, coarse enough that the whole set stays cheap.
PLANE_DIRECTION_COUNT = 16
_PLANE_FAN = tuple(
    (math.cos(2.0 * math.pi * index / PLANE_DIRECTION_COUNT),
     math.sin(2.0 * math.pi * index / PLANE_DIRECTION_COUNT),
     0.0)
    for index in range(PLANE_DIRECTION_COUNT)
)

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


def _deepest(pose: Sequence[float], obstacles: Sequence[Any]
             ) -> tuple[int, float, tuple[float, float, float]]:
    """The keep-out the pose is deepest into: its index, clearance and gradient."""
    best_index = 0
    best_clearance, best_direction = obstacle_clearance(pose, obstacles[0])
    for index in range(1, len(obstacles)):
        clearance, direction = obstacle_clearance(pose, obstacles[index])
        if clearance < best_clearance:
            best_index, best_clearance, best_direction = index, clearance, direction
    return best_index, best_clearance, best_direction


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


def _is_in_plane(obstacle: Any, planar: bool) -> bool:
    """Whether this keep-out is genuinely 2D, so radial escape belongs in the plane."""
    if planar:
        return True
    if isinstance(obstacle, Mapping):
        return (obstacle.get("kind") or obstacle.get("shape")) == "circle"
    return False


def _cross(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _tangents(direction: Sequence[float], in_plane: bool
              ) -> tuple[tuple[float, float, float], ...]:
    """Two unit directions perpendicular to `direction`, both ways on the same line.

    In the plane the tangent is the exact 90-degree turn, which is what slides
    along a hazard's surface without approaching it. Out of the plane any fixed
    perpendicular pair will do; the first is the stable one.
    """
    if in_plane:
        return ((-direction[1], direction[0], 0.0), (direction[1], -direction[0], 0.0))
    first = (_unit(_cross(direction, (0.0, 0.0, 1.0)))
             or _unit(_cross(direction, (1.0, 0.0, 0.0))))
    assert first is not None
    second = _unit(_cross(direction, first))
    assert second is not None
    return (first, second)


def _same_direction(left: Sequence[float], right: Sequence[float]) -> bool:
    return abs(left[0] * right[0] + left[1] * right[1] + left[2] * right[2] - 1.0) < 1e-9


def _escape_directions(pose: Sequence[float], obstacles: Sequence[Any], planar: bool
                       ) -> list[tuple[float, float, float]]:
    """Axis directions, then the radial from the deepest hazard and its tangents,
    then an even in-plane fan.

    The order is fixed because it is the final tie-break: two steps that reach the
    same clearance resolve to the earlier direction, so a replay of a logged
    decision proposes the same escape. The radial and tangent pair is exact on
    purpose. In a packed cluster a fan direction a few degrees off the tangent can
    still approach a neighbour, while the exact tangent only slides along the
    surface, and that narrow channel is the case this planner exists for.
    """
    deepest_index, _, radial = _deepest(pose, obstacles)
    in_plane = _is_in_plane(obstacles[deepest_index], planar)
    if in_plane:
        radial = _unit((radial[0], radial[1], 0.0)) or (1.0, 0.0, 0.0)
    else:
        radial = _unit(radial) or (1.0, 0.0, 0.0)

    directions: list[tuple[float, float, float]] = list(AXIS_DIRECTIONS)
    directions.append(radial)
    directions.extend(_tangents(radial, in_plane))
    directions.extend(_PLANE_FAN)

    unique: list[tuple[float, float, float]] = []
    for direction in directions:
        unit = _unit(direction)
        if unit is None or any(_same_direction(unit, kept) for kept in unique):
            continue
        unique.append(unit)
    return unique


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
    """Poses leaving the keep-outs around `start_pose`, one bounded step at a time.

    Returns an empty list when the start pose is already clear, when no candidate
    is admissible, or when there is nothing to escape from. The returned path
    never leaves `workspace_aabb`, never has more than `max_steps` poses, and at
    every step raises the pose's minimum clearance: the keep-out it is deepest
    into gains clearance and no keep-out finishes below the depth the step
    started at. A step that raises every clearance is preferred when candidates
    tie, so an isolated keep-out is escaped exactly as before.

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

    if _min_clearance(pose, obstacles) >= 0.0:
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
    start_clearances = [obstacle_clearance(current, obstacle)[0] for obstacle in obstacles]
    for _ in range(max_steps):
        deepest_index = min(range(len(obstacles)), key=start_clearances.__getitem__)
        floor = start_clearances[deepest_index]
        # Keyed on (minimum clearance, strict everywhere, earlier direction):
        # clearance decides, and the other two only settle a tie.
        best_key: tuple[float, bool, int] | None = None
        best: tuple[float, float, float] | None = None
        for order, direction in enumerate(_escape_directions(current, obstacles, planar)):
            candidate = (current[0] + direction[0] * step,
                         current[1] + direction[1] * step,
                         current[2] + direction[2] * step)
            if not _within(candidate, workspace):
                continue
            if _crosses_unentered_obstacle(current, candidate, obstacles):
                continue
            values = [obstacle_clearance(candidate, obstacle)[0] for obstacle in obstacles]
            # The step must raise the pose's minimum clearance: the deepest
            # keep-out gains clearance and nothing else finishes below where the
            # step started. That floor is what a tangential step along a
            # neighbour preserves, and refusing to drop below it is what stops a
            # path tunnelling from one hazard into the next. Demanding that every
            # keep-out improve instead admits no step at all in a packed cluster.
            if values[deepest_index] <= floor + _MIN_GAIN_M:
                continue
            if min(values) < floor - _MIN_GAIN_M:
                continue
            strict_everywhere = all(value > start
                                    for value, start in zip(values, start_clearances))
            key = (min(values), strict_everywhere, -order)
            if best_key is None or key > best_key:
                best_key, best = key, candidate
        if best is None:
            break
        current = best
        path.append([current[0], current[1], current[2]])
        start_clearances = [obstacle_clearance(current, obstacle)[0] for obstacle in obstacles]
    return path

