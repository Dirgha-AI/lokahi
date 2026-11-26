"""Swept-primitive tests for shaped keep-outs.

Every primitive here is analytic, so the gate keeps its zero-runtime-dependency
guarantee: a manifest can name a circle, cylinder, sphere or capsule and be
checked with arithmetic only. Triangle meshes have no closed form, and those go
through COAL (BSD-3-Clause, the maintained fork of FCL) when it is installed:

    pip install "lokahi[vla-geometry]"

A mesh in a manifest without COAL is a hard error naming the extra, never a
silent pass. Every function here is a pure predicate over geometry: nothing
touches the filesystem, the clock, or global state.

Credits: the mesh path uses COAL (https://github.com/coal-library/coal),
BSD-3-Clause. See CREDITS.md.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

SHAPE_KINDS = ("circle", "cylinder", "sphere", "capsule", "mesh")
MESH_EXTRA = "lokahi[vla-geometry]"

_EPS = 1e-12


class UnsupportedShape(ValueError):
    """A shape this build cannot check. Never downgrade this to a pass."""


def _sub(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(_dot(a, a))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def coal_available() -> bool:
    """Whether the optional mesh backend can be imported right now."""
    try:  # pragma: no cover - depends on the installed environment
        import coal  # noqa: F401
    except Exception:
        return False
    return True


def backends() -> dict[str, bool]:
    """What this deployment can check. A mesh needs the extra; the rest never does."""
    return {"analytic": True, "mesh": coal_available()}


def segment_point_distance_2d(start: Sequence[float], end: Sequence[float],
                              centre: Sequence[float]) -> float:
    ax, ay = start[0], start[1]
    dx, dy = end[0] - ax, end[1] - ay
    denominator = dx * dx + dy * dy
    if denominator < _EPS:
        return math.hypot(centre[0] - ax, centre[1] - ay)
    t = _clamp01(((centre[0] - ax) * dx + (centre[1] - ay) * dy) / denominator)
    return math.hypot(centre[0] - (ax + t * dx), centre[1] - (ay + t * dy))


def segment_point_distance_3d(start: Sequence[float], end: Sequence[float],
                              centre: Sequence[float]) -> float:
    ax, ay, az = start[0], start[1], start[2]
    dx, dy, dz = end[0] - ax, end[1] - ay, end[2] - az
    denominator = dx * dx + dy * dy + dz * dz
    if denominator < _EPS:
        return math.dist((ax, ay, az), tuple(centre[:3]))
    t = _clamp01(((centre[0] - ax) * dx + (centre[1] - ay) * dy
                  + (centre[2] - az) * dz) / denominator)
    return math.dist((ax + t * dx, ay + t * dy, az + t * dz), tuple(centre[:3]))


def segments_distance(start_a: Sequence[float], end_a: Sequence[float],
                      start_b: Sequence[float], end_b: Sequence[float]) -> float:
    """Shortest distance between two 3D segments (Ericson, Real-Time Collision Detection)."""
    p1 = tuple(float(v) for v in start_a[:3])
    q1 = tuple(float(v) for v in end_a[:3])
    p2 = tuple(float(v) for v in start_b[:3])
    q2 = tuple(float(v) for v in end_b[:3])
    d1 = _sub(q1, p1)
    d2 = _sub(q2, p2)
    r = _sub(p1, p2)
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    f = _dot(d2, r)
    if a <= _EPS and e <= _EPS:
        return _norm(r)
    if a <= _EPS:
        s = 0.0
        t = _clamp01(f / e)
    else:
        c = _dot(d1, r)
        if e <= _EPS:
            t = 0.0
            s = _clamp01(-c / a)
        else:
            b = _dot(d1, d2)
            denominator = a * e - b * b
            s = _clamp01((b * f - c * e) / denominator) if abs(denominator) > _EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = _clamp01(-c / a)
            elif t > 1.0:
                t = 1.0
                s = _clamp01((b - c) / a)
    closest_a = (p1[0] + d1[0] * s, p1[1] + d1[1] * s, p1[2] + d1[2] * s)
    closest_b = (p2[0] + d2[0] * t, p2[1] + d2[1] * t, p2[2] + d2[2] * t)
    return _norm(_sub(closest_a, closest_b))


def _coal_mesh_hit(shape: Mapping[str, Any], start, end, margin: float) -> bool:
    try:  # pragma: no cover - requires the extra
        import coal
    except Exception as exc:  # pragma: no cover
        raise UnsupportedShape(
            f"a mesh keep-out needs COAL: pip install '{MESH_EXTRA}'"
        ) from exc
    try:  # pragma: no cover - requires the extra
        vertices = list(shape["vertices"])
        triangles = list(shape["triangles"])
        model_vertices = coal.StdVec_Vec3f(
            [coal.Vec3f(float(v[0]), float(v[1]), float(v[2])) for v in vertices]
        )
        model_triangles = coal.StdVec_Triangle([
            coal.Triangle(int(t[0]), int(t[1]), int(t[2])) for t in triangles
        ])
        model = coal.BVHModel()
        model.beginModel(len(model_vertices), len(model_triangles))
        model.addSubModel(model_vertices, model_triangles)
        model.endModel()
        request = coal.CollisionRequest()
        request.security_margin = float(margin)
        request.enable_contact = True
        collide = coal.CollisionObject(model, coal.Transform3s())
        capsule = coal.Capsule(float(margin) or 1e-6, 1e-6)
        sweep = coal.CollisionObject(capsule, coal.Transform3s())
        result = coal.CollisionResult()
        hit = False
        steps = 64
        for step in range(steps + 1):
            t = step / steps
            position = [start[i] + (end[i] - start[i]) * t for i in range(3)]
            sweep.setTranslation(coal.Vec3f(*[float(v) for v in position]))
            coal.collide(collide, sweep, request, result)
            if result.isCollision():
                hit = True
                break
        return hit
    except UnsupportedShape:
        raise
    except Exception as exc:  # pragma: no cover - binding drift is a hard error
        raise UnsupportedShape(
            f"COAL could not check a mesh keep-out ({exc}); refusing rather than passing"
        ) from exc


def _segments_of(shape: Mapping[str, Any]) -> tuple[Sequence[float], Sequence[float]]:
    start = shape.get("start")
    end = shape.get("end")
    if start is None or end is None:
        raise UnsupportedShape("a capsule needs start and end points")
    return start, end


def sweep_hits(shape: Mapping[str, Any], start: Sequence[float], end: Sequence[float],
               margin: float = 0.0) -> bool:
    """Whether the swept segment comes within the shape grown by `margin`."""
    kind = shape.get("kind") or shape.get("shape")
    centre = shape.get("centre")
    radius = float(shape.get("radius") or 0.0) + float(margin)
    if kind == "circle":
        return segment_point_distance_2d(start, end, centre) <= radius
    if kind == "cylinder":
        low = float(centre[2])
        high = low + float(shape.get("height") or 0.0)
        if max(start[2], end[2]) < low or min(start[2], end[2]) > high:
            return False
        return segment_point_distance_2d(start, end, centre) <= radius
    if kind == "sphere":
        return segment_point_distance_3d(start, end, centre) <= radius
    if kind == "capsule":
        capsule_start, capsule_end = _segments_of(shape)
        return segments_distance(start, end, capsule_start, capsule_end) <= radius
    if kind == "mesh":
        return _coal_mesh_hit(shape, start, end, margin)
    raise UnsupportedShape(f"unknown keep-out shape: {kind!r}")


def inside_depth(shape: Mapping[str, Any], point: Sequence[float],
                 margin: float = 0.0) -> float:
    """How far inside the shape a point sits; 0 when it is on or outside the surface.

    The recovery rule compares this at the two ends of a chunk: a chunk may stay
    in a keep-out only while it is monotonically leaving it.
    """
    kind = shape.get("kind") or shape.get("shape")
    centre = shape.get("centre")
    radius = float(shape.get("radius") or 0.0) + float(margin)
    if kind == "circle":
        return max(0.0, radius - math.hypot(point[0] - centre[0], point[1] - centre[1]))
    if kind == "cylinder":
        radial = radius - math.hypot(point[0] - centre[0], point[1] - centre[1])
        low = float(centre[2])
        high = low + float(shape.get("height") or 0.0)
        axial = min(point[2] - low, high - point[2])
        if radial <= 0.0 or axial <= 0.0:
            return 0.0
        return min(radial, axial)
    if kind == "sphere":
        return max(0.0, radius - math.dist(tuple(point[:3]), tuple(centre[:3])))
    if kind == "capsule":
        capsule_start, capsule_end = _segments_of(shape)
        return max(0.0, radius - segments_distance(point, point, capsule_start, capsule_end))
    if kind == "mesh":
        try:  # pragma: no cover - requires the extra
            import coal
        except Exception as exc:  # pragma: no cover
            raise UnsupportedShape(
                f"a mesh keep-out needs COAL: pip install '{MESH_EXTRA}'"
            ) from exc
        raise UnsupportedShape(
            "mesh recovery depth is not defined by this build; a mesh keep-out "
            "must be entered already clear or the gate refuses it"
        )
    raise UnsupportedShape(f"unknown keep-out shape: {kind!r}")
