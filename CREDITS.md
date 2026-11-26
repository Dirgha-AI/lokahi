# Credits

LOKAHI is Apache-2.0. This file records the external work the VLA safety gate
builds on, what each one contributes, and how it is linked, so a licence review
does not have to reconstruct it from imports.

**Nothing here is vendored.** No source file from these projects is copied into
this repository. Where a project is used at runtime it is an *optional* extra:
the core gate imports only the standard library, every primitive it needs has an
analytic implementation in `tools/vla/`, and a manifest that names something only
an extra can check is refused with a message naming that extra. A refused
manifest is loud; a silently skipped check would not be.

## Runtime backends (optional extras)

| Project | Licence | Extra | Contributes | Where |
|---|---|---|---|---|
| [COAL](https://github.com/coal-library/coal) (`coal-library/coal`) | BSD-3-Clause | `lokahi[vla-geometry]` | Triangle-mesh collision and distance queries with security margins, for a `keep_out_shapes` entry of kind `mesh`. Everything else — circle, cylinder, sphere, capsule — is analytic here. | `tools/vla/geometry.py` |
| [Ruckig](https://github.com/pantor/ruckig) (`pantor/ruckig`) | MIT | `lokahi[vla-replan]` | Time-optimal, jerk-limited online trajectory generation, used to re-plan a stop after a clamp instead of truncating the chunk. The analytic stop profile in the same module is the dependency-free fallback. | `tools/vla/replanning.py` |
| [Pinocchio](https://github.com/stack-of-tasks/pinocchio) (`stack-of-tasks/pinocchio`) | BSD-2-Clause | `lokahi[vla-kinematics]` | Forward kinematics for models beyond a serial URDF chain, so a ROS 2 `JointTrajectory` can be gated in Cartesian space. The built-in URDF evaluator covers serial chains with no dependency. | `tools/vla/kinematics.py` |

## Benchmarks (never a runtime dependency)

| Project | Licence | Extra | Contributes | Where |
|---|---|---|---|---|
| [Safety-Gymnasium](https://github.com/PKU-Alignment/safety-gymnasium) (`PKU-Alignment/safety-gymnasium`) | Apache-2.0 | `lokahi[safety-gymnasium]` | The published constraint-cost suite the gate is measured against: three tasks, three seeds, 1,000 steps per episode, with the environment's own cost signal as the ground truth. | `benchmarks/safety_gymnasium_runner.py` |
| [Gymnasium](https://github.com/Farama-Foundation/Gymnasium) (`Farama-Foundation/Gymnasium`) | MIT | via Safety-Gymnasium | The `Env` interface the benchmark and the `VLAGatedEnv` adapter are written against. | `benchmarks/`, `tools/vla/adapters/gym_wrapper.py` |
| [MuJoCo](https://github.com/google-deepmind/mujoco) (`google-deepmind/mujoco`) | Apache-2.0 | via Safety-Gymnasium | The physics the benchmark tasks run in. | — |

## Reference implementations and algorithms

| Source | Licence | Contributes | Where |
|---|---|---|---|
| [safe-control-gym](https://github.com/utiasDSL/safe-control-gym) (`utiasDSL/safe-control-gym`) | MIT | The filter-comparison baseline: the MPC and CBF shields this gate has to be measured against before any claim that it is better than a filter. **Not yet integrated** — tracked as D-4 in `HANDOVER.md`. | — |
| [Mujoco Menagerie](https://github.com/google-deepmind/mujoco_menagerie) (`google-deepmind/mujoco_menagerie`) | Apache-2.0, per-model licences (Franka Panda, Franka FR3: Apache-2.0; UR5e, Go2, Spot: BSD-3-Clause) | Robot models for exercising the adapters against a real machine description rather than a synthetic URDF. **Not yet integrated.** | — |
| [LeRobot](https://github.com/huggingface/lerobot) (`huggingface/lerobot`) | Apache-2.0 | The action-queue and rollout record layout the LeRobot hook mirrors. | `tools/vla/adapters/lerobot_hook.py` |
| [openpi](https://github.com/Physical-Intelligence/openpi) (`Physical-Intelligence/openpi`) | Apache-2.0 | The real-time chunking reference the action-chunk handling follows. | `tools/vla/vla_gate.py` |
| Christer Ericson, *Real-Time Collision Detection* (2004), §5.1.9 | Textbook algorithm | Shortest distance between two segments, implemented independently here for the capsule sweep. No code copied. | `tools/vla/geometry.py` (`segments_distance`) |
| J. R. Shewchuk / standard quaternion-from-matrix formulation | Textbook algorithm | Rotation matrix to quaternion conversion in the URDF evaluator. | `tools/vla/kinematics.py` (`_quaternion`) |

## Standards cited, not implemented

`docs/STANDARDS_MATRIX.md` maps this project's checks to ISO 10218, ISO/TS 15066,
IEC 61508, IEC 62443, ISO/IEC 42001 and UL 4600. Citing a standard as a design
reference is not a claim of compliance with it, and LOKAHI does not claim to be a
safety-rated control function, PL d, SIL-rated, or AgPL-rated. The matrix says
what is covered and what is not, and the non-claims there are binding.

## How to add a credit

Any new optional backend gets a row above with its licence, its extra, the file
it lands in, and a line in `[project.optional-dependencies]`. An integration
without an extra, or an extra without a row here, is an incomplete change.
