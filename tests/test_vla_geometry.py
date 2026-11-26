"""Tests for the swept-primitive geometry.

These are the primitives the gate trusts for a keep-out it cannot box. Each
assertion is a case a bounding box gets wrong or a naive implementation gets
wrong, so the file doubles as the shape contract.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import geometry
from vla.geometry import UnsupportedShape

CIRCLE = {"kind": "circle", "centre": (0.5, 0.5), "radius": 0.2}
SPHERE = {"kind": "sphere", "centre": (0.5, 0.5, 0.5), "radius": 0.2}
CYLINDER = {"kind": "cylinder", "centre": (0.5, 0.5, 0.0), "radius": 0.2, "height": 1.0}
CAPSULE = {"kind": "capsule", "start": (0.2, 0.5, 0.5), "end": (0.8, 0.5, 0.5), "radius": 0.1}


def test_the_analytic_backend_is_always_present():
    assert geometry.backends()["analytic"] is True


def test_circle_sweep_catches_a_crossing_segment():
    assert geometry.sweep_hits(CIRCLE, (0.1, 0.5, 0.5), (0.9, 0.5, 0.5))


def test_circle_sweep_allows_a_segment_that_stops_short():
    assert not geometry.sweep_hits(CIRCLE, (0.1, 0.5, 0.5), (0.25, 0.5, 0.5))


def test_circle_sweep_is_measured_from_the_segment_not_the_line():
    # The infinite line through this segment passes through the centre; the
    # finite segment does not.
    assert not geometry.sweep_hits(CIRCLE, (0.9, 0.5, 0.5), (0.9, 0.9, 0.5))


def test_the_margin_grows_the_shape():
    start, end = (0.1, 0.5, 0.5), (0.28, 0.5, 0.5)  # stops 0.22 from the centre
    assert not geometry.sweep_hits(CIRCLE, start, end)
    assert geometry.sweep_hits(CIRCLE, start, end, margin=0.05)


def test_a_sphere_is_checked_in_three_dimensions():
    assert geometry.sweep_hits(SPHERE, (0.1, 0.5, 0.5), (0.9, 0.5, 0.5))
    # Above the sphere by more than its radius: the 2D projection still crosses.
    assert not geometry.sweep_hits(SPHERE, (0.1, 0.5, 0.75), (0.9, 0.5, 0.75))


def test_a_cylinder_blocks_only_its_own_slab():
    assert geometry.sweep_hits(CYLINDER, (0.1, 0.5, 0.5), (0.9, 0.5, 0.5))
    assert not geometry.sweep_hits(CYLINDER, (0.1, 0.5, 1.5), (0.9, 0.5, 1.5))


def test_a_capsule_is_a_swept_sphere_between_its_endpoints():
    assert geometry.sweep_hits(CAPSULE, (0.5, 0.62, 0.5), (0.5, 0.5, 0.5))
    assert not geometry.sweep_hits(CAPSULE, (0.5, 0.72, 0.5), (0.5, 0.62, 0.5))


def test_segment_distance_handles_parallel_and_degenerate_cases():
    assert geometry.segments_distance((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)) == pytest.approx(1.0)
    assert geometry.segments_distance((0, 0, 0), (0, 0, 0), (0, 2, 0), (0, 2, 0)) == pytest.approx(2.0)
    assert geometry.segments_distance((0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 0, 0)) == pytest.approx(1.0)


def test_inside_depth_is_zero_on_the_surface_and_positive_within():
    assert geometry.inside_depth(CIRCLE, (0.5, 0.5, 0.5)) == pytest.approx(0.2)
    assert geometry.inside_depth(CIRCLE, (0.7, 0.5, 0.5)) == pytest.approx(0.0)
    assert geometry.inside_depth(CIRCLE, (0.9, 0.5, 0.5)) == 0.0


def test_inside_depth_respects_the_margin():
    assert geometry.inside_depth(CIRCLE, (0.72, 0.5, 0.5), margin=0.05) == pytest.approx(0.03)


def test_inside_depth_for_a_cylinder_is_the_nearest_exit():
    # Deeper than the radius from the wall but close to the top face.
    assert geometry.inside_depth(CYLINDER, (0.5, 0.5, 0.98)) == pytest.approx(0.02)
    assert geometry.inside_depth(CYLINDER, (0.5, 0.5, 2.0)) == 0.0


def test_inside_depth_for_a_capsule_and_a_sphere():
    assert geometry.inside_depth(CAPSULE, (0.5, 0.55, 0.5)) == pytest.approx(0.05)
    assert geometry.inside_depth(SPHERE, (0.5, 0.5, 0.6)) == pytest.approx(0.1)


def test_an_unknown_shape_is_refused_rather_than_ignored():
    with pytest.raises(UnsupportedShape):
        geometry.sweep_hits({"kind": "torus"}, (0, 0, 0), (1, 1, 1))


def test_a_mesh_without_the_extra_names_the_extra():
    mesh = {"kind": "mesh", "vertices": ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            "triangles": ((0, 1, 2),)}
    if geometry.coal_available():  # pragma: no cover - depends on the environment
        pytest.skip("COAL is installed here; the refusal path is exercised without it")
    with pytest.raises(UnsupportedShape, match="vla-geometry"):
        geometry.sweep_hits(mesh, (0, 0, 0), (1, 1, 1))
