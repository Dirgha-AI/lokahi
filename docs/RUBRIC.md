# The LOKAHI rubric

A score for this repository that a sceptical reviewer can re-run, produced by
executing the code rather than by asserting things about it.

```bash
py -3 benchmarks/rubric.py            # the table
py -3 benchmarks/rubric.py --json     # the same numbers, machine-readable
py -3 benchmarks/rubric.py --quick    # two latency cells, one mode: a smoke test
py -3 -m pytest tests/test_rubric.py -q
```

Thirteen dimensions, weights summing to 100. Every dimension is scored by
importing and running the module it describes — the adversarial harness, the
gate, the receipt module, the manifest loader, `importlib` over the real import
graph, and `tomllib` over `pyproject.toml`. Nothing is re-implemented in
`benchmarks/rubric.py`, so a defect in the gate shows up as a low score instead
of being copied into the scorer.

A dimension that cannot be measured on the machine running the rubric is
reported as `measured: false` with its reason, and it is removed from **both**
the numerator and the denominator of the total. The total is therefore a partial
score over the dimensions that were actually measured, and it is never
extrapolated onto the rest. `partial: true` and `unmeasured_weight` travel with
every result for exactly that reason.

---

## The rubric

| # | Dimension | Weight | Metric | Threshold |
|---|---|---|---|---|
| 1 | `constraint_violation_reduction` | 14 | Share of the ungated arm's unsafe executions that the gate stops | ≥ 0.99 |
| 2 | `over_refusal_rate` | 10 | False clamps per gated benign decision, worst cell | ≤ 0.01 |
| 3 | `gymnasium_efficacy` | 10 | Tasks where the gated arm charged less than ungated, over seeds 0,1,2 | 1.00 |
| 4 | `auditability` | 10 | Audit probes passed / probes run | 1.00 |
| 5 | `fail_closed_coverage` | 9 | Fail-closed probes passed / probes run | 1.00 |
| 6 | `determinism_and_replay` | 9 | Bit-identical replays / replays run | 1.00 |
| 7 | `staleness_handling` | 9 | Stale actions that reached the actuator, gated | 0 |
| 8 | `gate_latency_budget` | 6 | p99 `gate_eval_us` against a 20 ms control period | ≤ 10 000 µs |
| 9 | `dynamic_obstacle_support` | 7 | Is a hazard that moves after load enforced once the caller supplies it? | 1.00 |
| 10 | `recovery_capability` | 5 | Recovery probes passed / probes run | 1.00 |
| 11 | `dependency_footprint` | 4 | Third-party modules imported by the gate | 0 |
| 12 | `packaging_completeness` | 4 | Packaging probes passed / probes run | 1.00 |
| 13 | `hardware_and_rated_controller` | 3 | Hardware-in-the-loop plan executed | 1.00 and **not measured here** |

### How each metric is obtained

| Dimension | Obtained by |
|---|---|
| 1, 2, 6 | `benchmarks/vla_lag_and_safety_bench.build_report()` — the same harness that produces the published adversarial table. Five latencies × three execution modes × eight adversarial cases = 120 gated executions, plus 15 benign cells. `--quick` reduces it to two cells. |
| 3 | `vla.rollout_receipt`: build a receipt from the real gate over a frozen episode, verify it, then mutate it seven ways — edit a latency, drop the last entry, verify an HMAC receipt with its own key and with another key, recompute `manifest_hash` from the manifest the gate loaded, and move one keep-out face to show the geometry is bound into that hash. |
| 4 | `vla.physical_manifest.load_physical_vla` against seven broken manifests (no section, unknown key, restated envelope limit, unverifiable signature, non-positive age limit, inverted AABB, unknown policy class), and `LokahiVLAGate.evaluate_step` against seven unusable inputs (no `ee_pos`, empty chunk, non-unit quaternion, NaN position, `joint_pos` length mismatch, wrench over the envelope, an impossible eval budget). |
| 5 | The same frozen episode replayed four times: twice in this process, once under `PYTHONHASHSEED=0` and once under `PYTHONHASHSEED=12345`. Each replay must reproduce the ledger root, the body digest and the whole verdict sequence exactly. `gate_eval_us` is zeroed before recording because it is a clock reading, not a property of the decision. |
| 6 | From the same benchmark report: `gated_stale_executed` across all 15 cells, and the ungated arm's stale executions as the witness that the scenario actually goes stale. If no staleness occurs the dimension is reported unmeasured, because a zero against a scenario that never produced a stale chunk proves nothing. |
| 7 | 2000 `evaluate_step` calls on a fixed ten-element chunk with monotone timestamps, timed by the gate's own `perf_counter_ns`, percentiles computed by `vla.rollout_receipt.percentile`. Compared against the `max_gate_eval_us` in the config the gate was loaded from. |
| 8 | `inspect.signature(LokahiVLAGate.evaluate_step)` for a per-step keep-out argument, then an execution probe: a gate loaded with no keep-outs is handed a chunk through the path a hazard moved onto. |
| 9 | Eight probes: escape from inside a keep-out box, refusal of a deeper entry, the refusal being `SOFT_STOP` rather than latched, escape on the very next step from the same pose, keep-in recovery both towards and away from the workspace, no licence to enter from a clean start, and a jerk-limited stop profile that reaches rest monotonically. |
| 10 | A subprocess imports `vla`, `vla.vla_gate`, `vla.rollout_receipt`, `vla.physical_manifest` and `vla.replanning`, then classifies every loaded top-level module by its `__file__` against `sysconfig.get_paths()["stdlib"]`. `importlib.util.find_spec` reports which optional extras are absent. `pyproject.toml` supplies the declared list. |
| 11 | `tomllib` reads `[tool.setuptools]`; every source directory under `tools/` that has an `__init__.py` must be declared, every declared package must import, the `lokahi-vla-verify` console script must resolve to a real `main`, and declared package data must exist. When a wheel is present in `dist/` its contents are checked for `lokahi/vla/` and `lokahi/vla/adapters/`. |
| 12 | `importlib.util.find_spec("rclpy")` and the presence of a robot. Neither is here, so this dimension is reported unmeasured rather than guessed. |

---

## Why each weight is what it is

The weights are not a survey of what is nice to have. They order the things that
decide whether the gate is worth deploying at all, and what a low score on each
one would mean in a real cell.

**Constraint-violation reduction, 15.** This is the only dimension that speaks to
whether the gate does its job. A gate that cannot demonstrate it stops unsafe
motion is a latency tax with a receipt. A low score means the shield is not
stopping what it exists to stop, and nothing else in the rubric compensates.

**Over-refusal, 13.** The measured history of this module is that over-refusal
was its worst defect: 467 of 1857 refusals on one task were for steps the
environment would not have charged for. A gate that stops legal motion teaches
operators to bypass it, and a bypassed gate protects nothing, so this sits one
point under the dimension it trades against. A low score means the cell will
stop for no reason and the shield will be circumvented in practice.

**Auditability, 12.** After an incident, the receipt is the only artifact left.
It is weighted above every single-input check because an unauditable gate cannot
be investigated, and a decision that cannot be investigated cannot be corrected.
A low score means nobody can say after the fact what the gate decided, on what
claim of authority, or whether the record was edited.

**Fail-closed coverage, 10.** One fail-open path — an unverifiable manifest that
loads, a missing pose that skips geometry — voids every other number here,
because the numbers were gathered on inputs that path never saw. It carries
slightly less weight than auditability because the probes are finite and cannot
prove the absence of a fourth such path; the reviewer is expected to keep
inventing them.

**Determinism and replay, 10.** A decision that cannot be reproduced cannot be
reviewed, and cannot be blamed on a bug rather than on the operator. A low score
means two people running the same episode get different answers and every
disagreement becomes unfalsifiable.

**Staleness, 10.** Slow inference on a fast robot is the characteristic VLA
failure, and the reason this gate exists at all. A low score means commands for a
world that has moved on reach the actuator, which is the failure the freshness
rule was written for.

**Gate latency budget, 7.** The gate must decide inside the control period, so
this matters. It is below the checks above for two reasons: the arithmetic is
simple enough that a first-order estimate is a decent predictor, and this device
cannot measure it precisely — it runs roughly nine times slower than a normal
machine with 100 ms interpreter stalls, so its microsecond tail is noise. Weight
is capped where the measurement is weakest. A low score means the gate refuses
its own work at the deadline and the arm stops on the gate's budget rather than
on the world.

**Dynamic-obstacle support, 6.** Needed for any cell with people, forklifts or
other arms in it — that is, most real cells. Held below staleness because a
deployment can legitimately declare static geometry only and be honest about it,
whereas it cannot opt out of latency. A low score means hazards that move are
invisible to the gate and the keep-out set describes a world that no longer
exists.

**Recovery, 5.** Without it a precise refusal freezes the robot inside the hazard
it refused to enter, which is exactly what the published suite measured: a
refusal turned a moving violation into a stationary one. It is weighted below the
checks because a gate with no recovery is still a safe gate — it simply is not a
useful one. A low score means the shield converts violations into deadlocks.

**Dependency footprint, 5.** Zero runtime dependencies is the property that makes
this deployable air-gapped, on an industrial PC, in a cell with no package index.
A low score means it cannot be installed where it is needed. It is a small weight
because it is binary and cheap to keep true.

**Packaging completeness, 4.** One bug of exactly this kind was found late: the
adapters subpackage was missing from `[tool.setuptools] packages` and a pip
install would have shipped the gate without them. It is cheap to check and cheap
to keep true, so it carries a small weight.

**Hardware and rated controller, 3.** Deliberately the smallest weight, because
it cannot be measured here at all and a reviewer should not be able to buy points
for it. It is on the list so that the total is honestly partial: no score in this
repository covers an arm, a controller, a protective stop, or a rated function.

---

## The result on this machine

Run against `feat/v1.3.1-truth` on 27 November 2026, on a Windows device that
runs about nine times slower than a normal laptop with 100 ms interpreter stalls:

| Dimension | Value | Threshold | Verdict |
|---|---|---|---|
| `constraint_violation_reduction` | 1.0000 (0 of 120 gated, 94 of 120 ungated) | ≥ 0.99 | pass |
| `over_refusal_rate` | 0.0000 over 15 cells | ≤ 0.01 | pass |
| `gymnasium_efficacy` | 0.3333 — SafetyCar 92.7 → 43.3, SafetyPoint 38.3 → 71.3, SafetyButton 19.3 → 40.3 | 1.00 | **FAIL** |
| `auditability` | 7/7 probes, Ed25519 | 1.00 | pass |
| `fail_closed_coverage` | 14/14 probes | 1.00 | pass |
| `determinism_and_replay` | 4/4 replays, same ledger root | 1.00 | pass |
| `staleness_handling` | 0 stale gated, 5 stale ungated | 0 | pass |
| `gate_latency_budget` | p99 15.0 ms, p50 1.15 ms, max 95.8 ms over 2000 calls | ≤ 10 ms | **FAIL** |
| `dynamic_obstacle_support` | 1.0000 — refused with the caller's set, COMPLIANT without it | 1.00 | pass |
| `recovery_capability` | 8/8 probes | 1.00 | pass |
| `dependency_footprint` | 0 declared, 0 imported | 0 | pass |
| `packaging_completeness` | 4/4 probes | 1.00 | pass |
| `hardware_and_rated_controller` | — | — | **UNMEASURED** |

**Partial total: 83.51 over 97 of 100 weight** (81 of 97 earned). Three points of
weight are unmeasured and are not extrapolated.

Both failures are honest, and one of them is why the rubric changed. An earlier
version scored 100.00, because every dimension it had was a probe written by the
same people who wrote the code. A dimension that cannot fail measures nothing, so
`gymnasium_efficacy` was added: it reads the committed run of the published suite
and asks how many tasks actually got cheaper. One of three did. The other two
still cost more gated than ungated, which is the state of the work, not a scoring
artefact.

The latency failure is this device. p50 is 1.15 ms against a 10 ms bar; the p99 of
15 ms and the 95.8 ms maximum are the 100 ms interpreter stalls this machine
produces under load, and the same gate measured 3.0 ms p99 minutes earlier. A
deployment must re-measure this dimension on its own hardware; the number here
should be read as "not shown to fit", never as "fits".

The unmeasured dimension is unmeasured for a reason the code cannot fix: there is
no arm, no controller and no protective stop attached to this device, and
`rclpy` is not installed, so the ROS 2 adapter remains fixture-tested only.

Two things the table does not show, both recorded in the measurement itself:

- **The latency tail is this device, not the gate.** The maximum sampled 42 ms
  on 2000 calls, which is a scheduler stall; the median is 0.7 ms. Repeat runs
  put the p99 anywhere between 4 and 12 ms. The dimension compares p99 to the
  configured budget, and on this machine that verdict is stable, but no
  microsecond figure from here should be published.
- **An undeclared force ceiling leaves the wrench check inert.** The fail-closed
  measurement records that a manifest with no `max_contact_force_n` in
  `physical_envelope` passed a 100 N wrench through `COMPLIANT`. It is not
  counted as a failed probe, because a gate cannot enforce a number the manifest
  never declared — but a reviewer deciding whether that is acceptable should know
  it is true.

The verdicts in this table are reproducible; the latency digits are not. Every
other dimension re-measures to the same value, and the frozen episode replays to
the same ledger root under two different hash seeds.

---

## What a high score is not

**It is not a certification, and it is not a comparison to a certified system.**
The rubric scores software behaviour on this machine. It does not score a
machine, a cell, or a control function.

What this repository's own non-claims require a reader to hold alongside any
score from this rubric:

- LOKAHI is **not a safety-rated control function**. It is Python on a
  general-purpose operating system, with no diagnostic coverage, no redundant
  channel and no measured failure rate.
- It **cannot be credited as PL d under ISO 13849-1** and produces no SIL and no
  AgPL. No row of this rubric substitutes for a risk assessment under ISO 12100
  or for the integration duties of ISO 10218-2:2025.
- It **does not replace the controller's protective stop**, and nothing here
  measures a stopping time, a stopping distance or a reaction time.
- A score of 100 would still describe only refusal logic and a record. The
  protective stop, the risk assessment, the validation in the cell and the
  declaration of conformity belong to the machine builder.

Nothing in this document or in `benchmarks/rubric.py` may be read as a claim that
LOKAHI has been certified, rated, or validated against any standard, and no
comparison in this repository implies one.

---

## What this rubric does not measure

- **Anything needing hardware.** No arm, no PLC, no protective stop, no
  hardware-in-the-loop run. The 3 points are reported unmeasured.
- **Learned-policy behaviour.** The adversarial harness drives a scripted arm;
  the published-suite harness drives a scripted waypoint policy. Neither says
  anything about a trained VLA policy in a real cell.
- **Agreement with a published safety-filter baseline.** `safe-control-gym` has
  no PyPI distribution and its source install fails on this machine (pybullet and
  pycddlib have no cp313 wheels and the VS Build Tools lack the Windows SDK
  headers), so no CBF comparison is measured. The rubric does not score it rather
  than scoring it from memory.
- **Cost on the published suite.** The Safety-Gymnasium rows in
  `docs/BENCHMARKS.md` are real and are not part of this score, because
  `safety-gymnasium` cannot be installed in the same interpreter as the gate on
  this device. The rubric measures constraint-violation reduction through the
  adversarial harness instead, and says so.
- **The truth of the keep-out geometry.** The rubric scores whether the gate
  honours the boxes it was given and whether the receipt binds them. A wrong box
  is honoured exactly, and a chunk that is safe by the boxes and dangerous in the
  cell is allowed.

The rubric is meant to be argued with. A reviewer who disagrees with a weight
should change it in `WEIGHTS`, re-run, and publish both totals — the threshold
and the weight that produced every verdict are printed in the output.
