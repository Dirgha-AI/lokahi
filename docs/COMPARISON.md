# What else gates robot actions, and what each thing actually covers

This is a landscape of the systems a buyer will put next to LOKAHI when the question is
"what stops a bad action reaching the actuator". It is written against the tree at
`v1.3.1` (the VLA gate is `v1.4.0-draft` inside that branch, see
[LOKAHI_VLA.md](LOKAHI_VLA.md)), and every claim about a third-party system carries the URL
it was read from.

Three rules governed the table:

1. **Nothing here is a certification**, including LOKAHI's own row. The non-claims in
   [LOKAHI_VLA.md](LOKAHI_VLA.md) §"What this is not" apply to this document unchanged: LOKAHI is
   not a safety-rated control function, cannot be credited as PL d under ISO 13849-1, and does not
   replace a protective stop. No row below, ours included, changes that.
2. **A cell only says "covers" when a fetched source or this repository's own code says so**, not
   when a product category implies it. A dagger (†) means the cell rests on something I could not
   verify from a source I actually read; §5 lists every one.
3. **"Partial" is the honest answer for most of the field.** The systems that fully cover a
   dimension usually cover exactly one of them, and several of the partials are partial for
   interesting reasons rather than for lack of engineering.

---

## 1. The distinction that matters

The class that matters is not "robot software that checks things" — that class is crowded and
mostly uncertified — but whether a **runtime filter standing between a policy and an actuator**
is the same object as a **safety-rated protective function**. It is not. A filter sees the proposed
action and decides whether to forward it; it runs on a general-purpose computer, has no
diagnostic coverage, no redundant channel and no measured failure rate, and the moment it fails it
fails silently — the policy's action simply continues. A safety-rated function is the opposite
object: a protective device and a safety controller, built to ISO 13849-1 or IEC 62061, with a
declared category, MTTFd, DCavg, a PFHd or a SIL claim limit, a validated configuration, and an
output that removes drive power on its own independent path. Neither object substitutes for the
other. A software gate makes bad *setpoints* less likely to be issued; a protective stop makes a
*machine* stop moving when a person is in the field, and no software gate can be the thing that
does that, because the software is what failed. This document is written so that a reader can see
exactly which class each product belongs to before comparing anything else.

**The short version, before the table: LOKAHI is behind every safety-rated item in this document
on the only axis that a machine builder signs for.** It has no PL, no SIL, no AgPL, no category, no
DCavg, no MTTFd, no validated toolchain and no robot-cell validation. It also has no certified
presence sensing, no protective-stop hardware, and no field-proven deployment. §3 says this at
length; read it before reading a "covers" in the LOKAHI row as an advantage.

---

## 2. Coverage matrix

The ten dimensions are the axes on which these systems can actually be compared. They are derived
from what LOKAHI claims for itself in [LOKAHI_VLA.md](LOKAHI_VLA.md) — the nine checks plus the
record and the latch — because a comparison in which we define the axes and then win them is not a
comparison. Anything LOKAHI does that no other system claims is marked as such in §3, and anything
another system does that LOKAHI does not is marked the same way.

| | D1 runtime filter | D2 policy-agnostic | D3 temporal integrity | D4 swept geometry | D5 per-setpoint kinematics | D6 force/torque ceilings | D7 fail-closed signed config | D8 tamper-evident record | D9 latched stop, human reset | D10 certified safety function |
|---|---|---|---|---|---|---|---|---|---|---|
| **LOKAHI VLA gate** (this repo) | covers | covers | partial | partial | partial | covers | covers | partial | partial | no |
| **MoveIt 2 + FCL** (incl. MoveIt Servo) | partial | partial | no | covers | partial | no | no | no | no | no |
| **ros2_control** (rolling) limiters + JTC | covers | covers | no | no | covers | partial | no | no | no | no |
| **safe-control-gym** MPC / CBF shields | covers (sim only) | covers | no | partial | partial | no | no | no | no | no |
| **NVIDIA Isaac Sim / Isaac Lab** | no | n/a | no | partial | partial† | no | no | no | no | no |
| **SICK microScan3 + Flexi Soft** | partial | covers | partial | covers | no | no | partial† | no | covers† | covers |
| **Pilz PNOZmulti 2** | partial | covers | partial | no | no | no | partial† | no | covers† | covers |
| **NVIDIA NeMo Guardrails** | no | covers | n/a | n/a | n/a | n/a | no | no | no | no |

Legend — **D1** decides on the proposed action, before motion, every step; **D2** works on an
arbitrary proposed action without retraining or a model of the policy; **D3** bounds staleness per
element, detects a watchdog gap, refuses a non-monotonic clock; **D4** checks a keep-in/keep-out
region against the swept path, not just the endpoints; **D5** bounds displacement, rotation and
joint limits per setpoint; **D6** bounds measured contact force and torque; **D7** refuses to run on
unsigned, unverifiable or unknown-keyed configuration rather than falling back to a default;
**D8** produces a record that is tamper-evident and verifiable offline; **D9** has an absorbing
stop state that only an attributable human action clears; **D10** is a safety function with a
declared PL or SIL, a DCavg or PFHd, and a validated toolchain.

### Evidence for the non-obvious cells

- **LOKAHI D1/D2** — the gate reads a chunk of setpoints per control period and returns
  `pass` / `safe_mechanical_hold` / `normal_retract`; it schedules one chunk per call in every
  `execution_mode`. See [LOKAHI_VLA.md](LOKAHI_VLA.md) §Flow, §Limits. **D3 partial**: staleness
  and the watchdog are per step, but the watchdog only fires if the caller calls again, and the
  gate never measures inference latency. **D4 partial**: swept polyline against static boxes and
  primitives; nothing tracks a moving hazard, and the tool is one scalar sphere. **D5 partial**:
  `max_joint_vel_rad_s` and `max_joint_acc_rad_s2` are accepted and never evaluated. **D6
  covers**: one L2 norm of force and one torque scalar against `physical_envelope`; no
  body-region table from ISO/TS 15066. **D8 partial**: SHA-256 hash chain, verified offline, but
  the reference ledger is an in-memory list with no persistence and no signature. **D9 partial**:
  `E_STOP` is absorbing until `human_reset(operator)`, but *operator* is an unauthenticated string
  and there is no monitored reset device. **D10 no**: stated in [LOKAHI_VLA.md](LOKAHI_VLA.md)
  and mapped clause by clause in [STANDARDS_MATRIX.md](STANDARDS_MATRIX.md).
- **MoveIt D4 covers** — FCL is shipped as MoveIt's collision checker, with Bullet offered as the
  alternative ("In addition to the Flexible Collision Library (FCL), Bullet Collision Detection is
  available as a collision checker",
  <https://moveit.picknik.ai/humble/doc/examples/bullet_collision_checker/bullet_collision_checker.html>).
  Continuous collision detection is available through Bullet on the same page.
- **MoveIt D1 partial** — the only runtime path is MoveIt Servo, which checks singularities and
  collisions and **scales velocities down** rather than refusing a chunk: "Singularity checking and
  collision checking are safety features that scale down the velocities when approaching
  singularities or collisions",
  <https://moveit.picknik.ai/main/doc/examples/realtime_servo/realtime_servo_tutorial.html>.
  Everything else in MoveIt checks at planning time, which is one decision per plan, not per
  control period.
- **MoveIt D3 no** — Servo takes joint velocities, Cartesian twists and poses; none of those pages
  describe a timestamped chunk, a per-element age bound or a watchdog over the gap between calls.
  Treated as an absence in the documentation I read, not a proven absence in the code.
- **ros2_control D1/D5 covers** — the controller manager runs a `JointLimiter` per joint and the
  documentation lists exactly what it bounds: position `[-1, 1]`, velocity, acceleration,
  deceleration, jerk and effort limits, with `enforce_command_limits` as the global switch and
  `<limits enable="false"/>` per joint or per interface,
  <https://control.ros.org/rolling/doc/ros2_control/hardware_interface/doc/joint_limiting.html>.
  It clamps; it does not refuse and it does not latch.
- **ros2_control D6 partial** — effort limits are a configured number, not a measured contact
  torque. No force/torque sensor ceiling appears in the limiter documentation.
- **ros2_control JTC** — the trajectory controller forwards position commands and interpolates:
  "For command interfaces `position`, the desired positions are simply forwarded to the joints",
  <https://control.ros.org/rolling/doc/ros2_controllers/joint_trajectory_controller/doc/userdoc.html>.
  Its `constraints.trajectory`, `constraints.goal`, `constraints.goal_time` and `stopped_velocity_tolerance`
  parameters exist, and `decelerate_on_cancel` describes decelerating "when the request is canceled
  or the goal constraints are violated"
  (<https://raw.githubusercontent.com/ros-controls/ros2_controllers/master/joint_trajectory_controller/src/joint_trajectory_controller_parameters.yaml>);
  that is a goal-abort monitor, not a keep-out check and not a latch.
- **safe-control-gym D1/D4** — the repository ships `safe_control_gym/safety_filters/` with `cbf/`
  and `mpsc/` subpackages and a `base_safety_filter.py`
  (<https://github.com/learnsyslab/safe-control-gym/tree/main/safe_control_gym/safety_filters>), and
  the README describes environments with "symbolic safety constraints" plus input, parameter and
  dynamics disturbances (<https://github.com/learnsyslab/safe-control-gym>). Constraints are on
  states and inputs of CartPole and Quadrotor in PyBullet, so "D4 partial" means constraint
  satisfaction in simulation, not an AABB keep-out around a robot arm in a cell.
- **safe-control-gym D10 no / deployment class** — MIT licence, an academic benchmark reproduced
  from branches `ar` and `submission` of the same repo; nothing in the README describes a
  deployment on hardware.
- **Isaac Sim/Lab D4 partial, D6 no** — Isaac Lab ships USD-schema contact sensors ("contact sensors
  and frame transformers",
  <https://isaac-sim.github.io/IsaacLab/main/source/api/lab/isaaclab.sensors.html>), which measure
  contact in simulation; measuring is not gating. Collision and contact are physics-solver
  behaviour inside a simulator that has no path to a real drive.
- **Isaac D5 partial†** — see §5. I did not read a page stating how joint limits are enforced, so
  the cell is marked unverified.
- **SICK D4/D10 covers** — the microScan3 product overview gives "Safety level | PL d, SIL 2, type
  3", a 275° horizontal scanning angle and protective field ranges of 4 m, 5.5 m or 9 m,
  <https://www.sick.com/media/productoverview/7/57/657/productoverview_microScan3_g295657_en.pdf>.
  Fields are configured in Safety Designer; the overview describes up to 128 freely configurable
  fields with up to 8 monitored simultaneously, and per-variant figures in its technical table differ.
- **SICK Flexi Soft D10 covers** — the FX3-CPU000000 data sheet gives "Safety integrity level |
  SIL 3 (IEC 61508)", "Category | Category 4 (EN ISO 13849)", "Performance level | PL e (EN ISO
  13849)", PFHd 1.07 × 10⁻⁹ and a 20-year mission time,
  <https://www.sick.com/media/pdf/7/77/477/dataSheet_FX3-CPU000000_1043783_en.pdf>. That is the
  safety-rated class: a declared architecture, a declared failure rate, a declared mission time.
- **SICK D1 partial, D4 covers** — the filter is real-time and independent of any policy, which is a
  *better* property than D2 asks for, but it decides on the presence of an object in a protective
  field, not on a proposed setpoint chunk. It cannot see that a chunk is stale, only that a person is
  there. D4 is "covers" because the field is monitored continuously by the sensor, which is the
  sweep — over the scanned area, not over a proposed trajectory, and in one horizontal plane.
- **Pilz D10 covers** — "PNOZmulti 2 can be used to monitor safety functions safely … safety
  requirements up to PL e of EN ISO 13849-1 and SIL CL 3 of EN IEC 62061", configuration built in
  PNOZmulti Configurator and loaded to a program memory in the base unit,
  <https://www.pilz.com/en-US/products/small-controllers/configurable-control-systems/pnozmulti-2-controllers>.
- **Pilz D4 no** — the controller has no geometry of its own. It evaluates the logic; the sensing
  comes from whatever safety sensors are wired to it.
- **NeMo D1/D2** — NeMo Guardrails is "an open-source library for applying programmable safety and
  security controls to LLM and agentic applications", with input and output rails and tool-call
  rails that check a proposed tool call before execution
  (<https://github.com/NVIDIA-NeMo/Guardrails>). The boundary it guards is text and tool arguments;
  a joint setpoint has no representation in it. **D2 covers** in the sense that it is model-agnostic
  and retraining-free, which is the same property LOKAHI needs for a VLA policy — and that is the
  only dimension on which the two can be compared at all.
- **NeMo D7/D8 no** — rails are configuration files and the library runs in-process in your
  application, which is the opposite of fail-closed in LOKAHI's sense: nothing in the architecture
  refuses to start on an unsigned policy, and the record it produces is application logging, not a
  hash chain.
- **D8 is "no" for every third-party row, and that is deliberate.** D8 asks for a record that is
  tamper-evident and verifiable offline. A rosbag, a ROS log, a diagnostic buffer and a simulation
  data log are none of those things: each is a file written by the same process it describes, with
  no chain and no signature, so a deleted entry is invisible. These systems emit logs, diagnostics
  or recorded data; none of them produces evidence of the kind `Ledger.append` produces, and that is
  the only reason LOKAHI's D8 is "partial" rather than "no".
- **SICK and Pilz D7/D9 are marked †, not verified** — the configuration workflow is documented
  (Safety Designer for the scanner and Flexi Soft, PNOZmulti Configurator for the Pilz controller,
  loaded to a program memory module), and restart interlock after a protective-field or safety-logic
  trip is standard behaviour of this device class, but neither the data sheet nor the product page
  I read states the checksum/verification semantics or the interlock function explicitly. §5 items 9
  and 10. Those two cells are almost certainly "covers"; they are not "covers" on evidence I hold.

- **Licences** — MoveIt 2 BSD-3-Clause
  (<https://raw.githubusercontent.com/moveit/moveit2/main/LICENSE.txt>); FCL BSD-3-Clause
  (<https://raw.githubusercontent.com/flexible-collision-library/fcl/master/LICENSE>); ros2_control
  Apache-2.0 (<https://raw.githubusercontent.com/ros-controls/ros2_control/master/LICENSE>);
  safe-control-gym MIT (<https://raw.githubusercontent.com/learnsyslab/safe-control-gym/main/LICENSE>);
  Isaac Lab BSD-3-Clause (<https://raw.githubusercontent.com/isaac-sim/IsaacLab/main/LICENSE>);
  Isaac Sim's repository code Apache-2.0 "with additional components licensed under other terms"
  including the Omniverse Kit SDK
  (<https://raw.githubusercontent.com/isaac-sim/IsaacSim/main/LICENSE>); NeMo Guardrails Apache-2.0
  (<https://raw.githubusercontent.com/NVIDIA-NeMo/Guardrails/develop/LICENSE.md>); LOKAHI
  Apache-2.0 (`LICENSE`).

### The rows in one line each

- **MoveIt 2 + FCL** (BSD-3-Clause) — motion planning with FCL collision checking against a
  planning scene, and MoveIt Servo for realtime jogging and teleoperation with singularity and
  collision velocity scaling. Uncapped software in the robot-control class. It is a strong D4 and
  the wrong tool for D1: it answers "is this path colliding", not "is this chunk stale and does it
  cross a keep-out".
- **ros2_control + joint_trajectory_controller**, rolling documentation (Apache-2.0) — the ROS 2 control framework, with
  per-joint limiters in the controller manager and a trajectory controller that interpolates and
  aborts on goal tolerance. Uncapped software in the robot-control class. It is where a real
  integrator's limits *are* enforced today, which is why it is the row LOKAHI most needs to work
  with rather than replace.
- **safe-control-gym** (MIT) — a benchmark suite for safe learning-based control: PyBullet CartPole
  and Quadrotor with CasADi symbolic dynamics, disturbance injection, MPC baselines (optional
  `acados`) and `cbf` / `mpsc` safety filters. Research and simulation only; it evaluates
  controllers, it does not guard an actuator.
- **NVIDIA Isaac Sim / Isaac Lab** (Apache-2.0 repository code plus NVIDIA-licensed dependencies;
  Isaac Lab BSD-3-Clause) — a robotics simulator and RL training stack. It gives you contact
  sensing, physics collision and simulation-time joint behaviour; it gives you no runtime guard
  between a policy and a real drive, and no safety claim. Simulation class.
- **SICK microScan3 + Flexi Soft** (commercial hardware) — a certified Type 3, PL d, SIL 2 safety
  laser scanner wired into a certified PL e, Cat 4, SIL 3 configurable safety controller. Safety-rated
  class. This is the object that stops the machine, and it is the one LOKAHI is *not*.
- **Pilz PNOZmulti 2** (commercial hardware) — a configurable safety controller, PL e / SIL CL 3,
  programmed in PNOZmulti Configurator and run from a program memory module in the base unit.
  Safety-rated class; it evaluates safety logic, and it needs sensors to mean anything.
- **NVIDIA NeMo Guardrails** (Apache-2.0) — programmable rails over LLM input, output and tool
  calls. **Outside the class entirely**: it is a good system with a real purpose, and in a robotics
  deployment it guards what the agent *says and calls*, not what the arm *does*. Placing it here is
  the point — "guardrail" in the LLM sense and "gate" in the actuator sense are different products,
  and a buyer who conflates them has bought neither.

---

## 3. Where LOKAHI is genuinely behind

Stated first and without softening:

1. **Everything safety-rated.** Not one LOKAHI row covers D10, and D10 is the only dimension a
   machine builder's declaration of conformity can rest on. SICK's data sheet carries a category
   (Cat 4), a PFHd (1.07 × 10⁻⁹) and a PL e / SIL 3 claim; LOKAHI sells a Python object. If the
   requirement is "the safety function must reach PL d", the answer is a safety controller, not this
   repository — and [LOKAHI_VLA.md](LOKAHI_VLA.md) says so in its own words.
2. **Certified diagnostic coverage.** LOKAHI has no DCavg, no self-test of its own logic, no
   plausibility check between two channels. It cannot detect that its own checker has stopped
   checking. A safety controller's diagnostic coverage is a number on a data sheet; here it is
   absent by construction, because there is only one channel.
3. **Real-robot validation.** Every LOKAHI number is simulation: the synthetic arm in
   [BENCHMARKS.md](BENCHMARKS.md) and Safety-Gymnasium. The best of them — 0 of 120 adversarial
   executions reaching the actuator, against 94 of 120 ungated, with 0% false clamp on benign
   chunks — is a statement about a synthetic arm with boxes and primitives, on a machine that runs
   nine times slower than a laptop. It is not a cell, and it is not a VLA policy. SICK's and Pilz's
   numbers are declared safety parameters read off a data sheet and a product page, not measured by
   this project.
4. **Dynamics-aware safety.** safe-control-gym ships CBF and model-predictive safety filters that
   use a dynamics model to keep the system inside a safe set. LOKAHI checks the chunk it was handed
   and nothing beyond it: no dynamics model, no invariant set, no forward prediction. On a fast robot with a slow policy, a
   per-step filter can be correct about every step and still be too late, which is exactly the gap
   CBF-style shielding is designed to close.
5. **Motion-mesh collision checking.** MoveIt checks mesh geometry of the robot against a planning
   scene with FCL, and can check continuous motion with Bullet's CCD. LOKAHI checks a swept point
   with one scalar tool radius against boxes and four analytic primitives; a mesh keep-out is the
   only mesh path and it needs an optional extra (`pip install "lokahi[vla-geometry]"`), and a mesh
   the gate cannot load is a hard error. For "do not hit the fixture", MoveIt is the more capable
   checker today.
6. **Integration and operations.** ros2_control is where commands are actually enforced on real
   hardware; MoveIt and Isaac have real ecosystems next to them. LOKAHI's hold driver is planned
   (`v1.5.0`, see [STANDARDS_MATRIX.md](STANDARDS_MATRIX.md) §C), its receipts and verify CLI are
   planned (`v1.4.0`), its ledger is in memory, and there is no OPC UA or Modbus write in the tree.

What LOKAHI covers that no other row does — stated as scope, not as superiority: a **policy-agnostic
per-chunk decision** (D1 with D2) combined with **temporal integrity of the chunk** (D3), **a
signed, closed configuration that refuses rather than defaults** (D7), and **a hash-chained record
of the refusal** (D8), all at the same decision point. MoveIt has geometry without chunk semantics;
ros2_control has limits without configuration integrity; the safe-RL shields have prediction
without a record; the safety devices have certification without any model of a *policy*. Whether
that combination is worth buying is a question about the cell, not about the table.

And where the table's "covers" is thin, it is thin on purpose: D4 is static geometry, D5 has no
velocity enforcement, D9's reset is a string, D8's ledger is a list. §Limits of what this version
knows in [LOKAHI_VLA.md](LOKAHI_VLA.md) is the authoritative list and it is longer than this
paragraph.

---

## 4. What a buyer still has to obtain from elsewhere

Nothing below can be supplied by LOKAHI, by MoveIt, by ros2_control, by a simulator or by an LLM
guardrail. Each is a separate purchase, a separate signature or a separate test in the cell.

**A risk assessment.** ISO 12100:2010 is the general-principles standard for risk assessment and
risk reduction (<https://www.iso.org/standard/51528.html>); its output — the hazards, the required
performance level or SIL per safety function — is the input every other item on this list needs and
none of them can produce. LOKAHI's manifest holds limits; it does not decide what the limits should
be, and [STANDARDS_MATRIX.md](STANDARDS_MATRIX.md) says the same in every "Gap" cell.

**A protective stop on independent hardware.** Stop category 0, 1 or 2 as the application requires
(IEC 60204-1; a vendor summary of the three categories is at
<https://www.se.com/us/en/faqs/FA122781/>), implemented by a safety-rated device and safety
controller that remove drive power on their own path, with a measured stopping time and distance.
The SICK microScan3 plus Flexi Soft and the Pilz PNOZmulti 2 are examples of the class; either one
is what actually stops the machine, and LOKAHI's `E_STOP` is a latch that asks the consumer to
hold.

**Validation in the cell.** ISO 10218-2:2025 is the robot-system integration standard
(<https://www.austrian-standards.at/en/shop/iso-10218-2-2025-2025-02-05~p4050538>); the validation
it and ISO/TS 15066 require — measured forces and pressures at the contact points, stopping
performance, the actual workcell geometry — is physical measurement in the actual cell. LOKAHI's
benchmarks are simulation on a synthetic arm and cannot be substituted for it.

**CE marking and the declaration of conformity.** The manufacturer placing the machine on the
market performs the conformity assessment and issues the declaration under the machinery
legislation — Directive 2006/42/EC today, replaced by Regulation (EU) 2023/1230 as of 20 January
2027, which also covers safety components
(<https://eur-lex.europa.eu/EN/legal-content/summary/machinery-safety-requirements.html>). LOKAHI
is a component supplier; it produces no assessment and no mark, and no row in this document
implies one.

**In short:** the safety controller, the scanner or light curtain, the stopping-time measurement,
the risk assessment, the cell validation and the CE mark are the buyer's, and they are the majority
of the work. LOKAHI's contribution is that a specific class of bad command — a stale, malformed,
out-of-envelope, wall-crossing action chunk from a learned policy — gets refused and recorded
before it reaches the actuator, and that the refusal can be shown to an auditor afterwards.

---

## 5. Claims I could not verify

Each of these is marked with a dagger in the matrix or deliberately omitted from it. None is
asserted above.

1. **Isaac Sim / Isaac Lab joint-limit enforcement (D5†).** I confirmed contact sensors exist in
   Isaac Lab, but I did not read a page in the Isaac Sim or PhysX documentation that states how
   joint limits are enforced in simulation. The cell is marked partial on the strength of the
   physics solver's role, which is an inference, not a citation. Treat it as unverified.
2. **MoveIt Servo's behaviour on an imminent collision.** The documentation says velocities are
   "scaled down"; whether the scale can reach a full stop, and whether that stop latches, is not
   stated on the page I read. I have written "scales velocities down" and nothing stronger.
3. **Whether ros2_control's `JointLimiter` only clamps.** The documentation names the limiter and
   lists the limits it enforces, but its "Description of the Limiter Algorithms" section reads
   "tba" (<https://control.ros.org/rolling/doc/ros2_control/hardware_interface/doc/joint_limiting.html>).
   The word "clamp" in this document is my reading of a limiter that saturates commands, and it is
   not a quotation.
4. **JTC abort semantics.** I read the parameter descriptions for the tolerance and
   `decelerate_on_cancel`, not the controller source. Whether a tolerance violation aborts the
   action, holds position, or merely reports is not verified from code.
5. **safe-control-gym's CBF and MPSC filters, in detail.** I verified the directories exist and the
   repository describes safety filters; I did not read the filter implementations, so I make no
   claim about which constraint formulations or QP solvers they use, nor whether they run outside
   PyBullet.
6. **Whether any of the software systems claim functional safety.** I found no PL or SIL claim for
   MoveIt, ros2_control, safe-control-gym, Isaac Sim, Isaac Lab or NeMo Guardrails on the pages I
   read. That is an absence in the documentation I fetched, not a proof of absence, and it is why
   D10 is "no" rather than "not applicable" for those rows.
7. **Pilz PNOZmulti 2's architecture category and PFHd.** The Pilz page states PL e and SIL CL 3;
   it does not state a category or a PFHd on the page I read. I did not quote one.
8. **SICK response times.** No response-time or stopping-distance figure is quoted anywhere in
   this document, because I did not fetch a source for one.
9. **SICK and Pilz configuration verification.** Both vendors document a software configuration
   workflow; neither page I read states whether the downloaded configuration is checksummed or
   verified by the device in a way comparable to LOKAHI's manifest digest. The D7† cells rest on
   that gap.
10. **SICK and Pilz restart interlock.** I did not fetch a page stating the reset/restart-interlock
    function for these devices. The D9† cells are marked as standard device-class behaviour, not as
    a citation.
11. **NeMo Guardrails' behaviour when a rail itself fails.** Not verified, and not needed for the
    claim made here, which is only that its boundary is text and tool calls rather than actuator
    setpoints.
