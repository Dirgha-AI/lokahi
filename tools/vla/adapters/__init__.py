"""Adapters that put the LOKAHI VLA gate in front of other stacks.

Three surfaces, one rule: this package imports the standard library and nothing
else. No numpy, no torch, no gymnasium, no rclpy — not at module level, not
inside a function. Everything is duck-typed against plain Python lists and
mappings, plus anything that exposes `.tolist()` (numpy arrays, torch tensors,
rosidl arrays).

The direction of the dependency is deliberate. A gate that can only be used
inside a stack that already installed every ML framework is a gate that gets
skipped, and LOKAHI's entire claim is that the check runs on the hot path. So
the heavy stack imports the gate, never the other way round —
`tests/test_vla_adapters.py` proves it in a fresh interpreter.

- `gym_wrapper.VLAGatedEnv` — a `.reset()`/`.step()` wrapper for a training
  loop, which refuses before the environment is stepped.
- `lerobot_hook.filter_action_chunk` — one gate evaluation over a policy's
  action chunk, returning the rows the caller may still execute.
- `ros2_adapter` — translation between recorded ROS 2 message dicts and the
  gate's chunk and wrench formats. Fixture-tested only; see its docstring.
"""
from .gym_wrapper import LOCAL_INFO_KEY, VLAGatedEnv
from .lerobot_hook import filter_action_chunk
from .ros2_adapter import (
    ROS2AdapterError,
    chunk_to_joint_trajectory,
    duration_to_ns,
    header_frame_id,
    header_stamp_ns,
    joint_state_to_robot_state,
    joint_trajectory_to_chunk,
    ns_to_duration,
    ns_to_stamp,
    stamp_to_ns,
    twist_stamped_to_velocity,
    velocity_to_twist_stamped,
    wrench_gate_to_stamped,
    wrench_stamped_to_gate,
)

__all__ = [
    "LOCAL_INFO_KEY",
    "ROS2AdapterError",
    "VLAGatedEnv",
    "chunk_to_joint_trajectory",
    "duration_to_ns",
    "filter_action_chunk",
    "header_frame_id",
    "header_stamp_ns",
    "joint_state_to_robot_state",
    "joint_trajectory_to_chunk",
    "ns_to_duration",
    "ns_to_stamp",
    "stamp_to_ns",
    "twist_stamped_to_velocity",
    "velocity_to_twist_stamped",
    "wrench_gate_to_stamped",
    "wrench_stamped_to_gate",
]
