"""Tests for the URDF forward-kinematics used to gate a joint trajectory.

The values are computed by hand from the URDF below, so a regression in the
chain composition shows up as a wrong number rather than as a shape mismatch.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import kinematics
from vla.kinematics import KinematicsError, UrdfChain

URDF = """<?xml version="1.0"?>
<robot name="two_joint">
  <link name="base"/>
  <link name="l1"/>
  <link name="l2"/>
  <link name="tool"/>
  <joint name="j1" type="revolute">
    <parent link="base"/>
    <child link="l1"/>
    <origin xyz="0 0 0.5" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="-3.14" upper="3.14"/>
  </joint>
  <joint name="j2" type="prismatic">
    <parent link="l1"/>
    <child link="l2"/>
    <origin xyz="0.3 0 0" rpy="0 0 0"/>
    <axis xyz="1 0 0"/>
    <limit lower="0" upper="1"/>
  </joint>
  <joint name="tcp" type="fixed">
    <parent link="l2"/>
    <child link="tool"/>
    <origin xyz="0 0 0.1" rpy="0 0 0"/>
  </joint>
</robot>
"""


def chain() -> UrdfChain:
    return UrdfChain.from_urdf(URDF)


def test_the_chain_is_found_from_the_leaf_link():
    found = chain()
    assert found.joint_names == ("j1", "j2")
    assert found.base_link == "base"
    assert found.tip_link == "tool"


def test_zero_configuration_is_the_sum_of_the_origins():
    pose = chain().forward({"j1": 0.0, "j2": 0.0})
    assert pose.position == pytest.approx((0.3, 0.0, 0.6))
    assert pose.quaternion == pytest.approx((1.0, 0.0, 0.0, 0.0))


def test_a_revolute_joint_rotates_the_downstream_chain():
    pose = chain().forward({"j1": math.pi / 2, "j2": 0.0})
    assert pose.position == pytest.approx((0.0, 0.3, 0.6), abs=1e-12)
    assert pose.quaternion == pytest.approx(
        (math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4)), abs=1e-12)


def test_a_prismatic_joint_translates_along_its_axis():
    pose = chain().forward({"j1": 0.0, "j2": 0.2})
    assert pose.position == pytest.approx((0.5, 0.0, 0.6))


def test_a_fixed_joint_contributes_an_offset_but_no_coordinate():
    assert "tcp" not in chain().joint_names
    assert chain().forward({"j1": 0.0, "j2": 0.0}).position[2] == pytest.approx(0.6)


def test_a_trajectory_is_converted_point_by_point():
    poses = chain().trajectory_poses([
        {"j1": 0.0, "j2": 0.0},
        {"j1": 0.0, "j2": 0.1},
    ])
    assert [pose.position[0] for pose in poses] == pytest.approx([0.3, 0.4])


def test_a_pose_feeds_the_gate_state_directly():
    fields = chain().forward({"j1": 0.0, "j2": 0.0}).as_state_fields()
    assert set(fields) == {"ee_pos", "ee_quat"}
    assert fields["ee_pos"] == pytest.approx([0.3, 0.0, 0.6])


def test_a_missing_joint_position_is_fatal():
    with pytest.raises(KinematicsError, match="j2"):
        chain().forward({"j1": 0.0})


def test_an_unsupported_joint_type_is_fatal_and_names_the_extra():
    floating = """<robot name="f"><link name="a"/><link name="b"/>
      <joint name="j" type="floating"><parent link="a"/><child link="b"/></joint></robot>"""
    with pytest.raises(KinematicsError, match="vla-kinematics"):
        UrdfChain.from_urdf(floating)


def test_an_ambiguous_leaf_is_fatal_without_a_named_tip():
    forked = """<robot name="f"><link name="a"/><link name="b"/><link name="c"/>
      <joint name="j1" type="revolute"><parent link="a"/><child link="b"/></joint>
      <joint name="j2" type="revolute"><parent link="a"/><child link="c"/></joint></robot>"""
    with pytest.raises(KinematicsError, match="tip_link"):
        UrdfChain.from_urdf(forked)
    assert UrdfChain.from_urdf(forked, tip_link="c").tip_link == "c"


def test_malformed_xml_is_fatal():
    with pytest.raises(KinematicsError):
        UrdfChain.from_urdf("<robot><joint")


def test_the_analytic_backend_is_always_available():
    assert kinematics.backends()["urdf_chain"] is True


def test_pinocchio_without_the_extra_names_the_extra(tmp_path):
    if kinematics.pinocchio_available():  # pragma: no cover - environment dependent
        pytest.skip("Pinocchio is installed here; the refusal path needs it absent")
    path = tmp_path / "robot.urdf"
    path.write_text(URDF, encoding="utf-8")
    with pytest.raises(KinematicsError, match="vla-kinematics"):
        kinematics.pinocchio_forward(str(path), {"j1": 0.0, "j2": 0.0})
