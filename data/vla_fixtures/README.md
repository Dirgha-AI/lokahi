# ROS 2 fixture messages for the VLA adapters

Four JSON objects with ROS 2 field names, used by `tests/test_vla_adapters.py`
to exercise `tools/vla/adapters/ros2_adapter.py`.

**These are hand-recorded, not captured from a live system.** The field names
and the nesting follow the installed ROS 2 message definitions; the numbers are
plausible values written by hand for a six-axis arm (UR-style joint names),
not a bag recorded on hardware. Nothing here is evidence that the adapter works
against a real node — only that it translates the documented shapes.

The JSON convention matches what a recorded bag replays as:
`rosidl_runtime_py.convert.message_to_ordereddict(msg)` followed by
`json.dumps`, so a message dumped that way round-trips through these files
unchanged.

| File | Message type | Why it is here |
| --- | --- | --- |
| `joint_trajectory.json` | `control_msgs/msg/JointTrajectory` | The shape a ROS 2 controller consumes; source of the gate's `joint_pos` and `duration_ms`. |
| `joint_state.json` | `sensor_msgs/msg/JointState` | Source of `robot_state["joint_pos"]`. |
| `twist_stamped.json` | `geometry_msgs/msg/TwistStamped` | A teleoperation velocity command, for the bridge that integrates it into a Cartesian path. |
| `wrench_stamped.json` | `geometry_msgs/msg/WrenchStamped` | Source of the gate's `wrench` argument. `frame_id` is `tool0` because a wrist sensor publishes in its own frame. |

Field notes, so a wrong assumption is visible rather than buried:

- In ROS 2 the trajectory message lives in `control_msgs`, not in
  `trajectory_msgs` as it did in ROS 1. Each point is a
  `trajectory_msgs/msg/JointTrajectoryPoint`: `positions`, `velocities`,
  `accelerations`, `effort`, `time_from_start`.
- `time_from_start` is a `builtin_interfaces/Duration` — `{sec, nanosec}`, no
  epoch meaning — and it is cumulative from the trajectory's stamp, which is
  how `joint_trajectory_to_chunk` derives each element's duration.
- `joint_state.json` has an empty `frame_id`: a `JointState` names joints, not
  a frame.
- **A JointTrajectory carries no end-effector pose.** The gate sweeps Cartesian
  positions, so the Cartesian path has to be captured alongside the trajectory
  (from TF at the same stamp, or from the policy's own Cartesian output) and
  passed to `joint_trajectory_to_chunk` as `cartesian_path`. There is no
  fixture for it here, because no standard ROS 2 message carries it; the tests
  supply a literal path and say so.
- Nothing here is signed, timestamped consistently across files, or claimed to
  be a coherent recording: the four stamps are within 20 ms of each other only
  so an end-to-end test can compute a believable action age.
