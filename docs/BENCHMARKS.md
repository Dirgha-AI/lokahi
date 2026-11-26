# Benchmarks

Two harnesses, both reproducible, both reporting what they actually measured.

Everything here is simulation. Nothing here was run on hardware, and no result
here supports a claim of certification (see the non-claims section of
[LOKAHI_VLA.md](LOKAHI_VLA.md)).

---

## 1. Latency and adversarial chunks

`benchmarks/vla_lag_and_safety_bench.py`

```bash
py -3 benchmarks/vla_lag_and_safety_bench.py           # table
py -3 benchmarks/vla_lag_and_safety_bench.py --json    # machine-readable
```

A synthetic arm inside a 1 m keep-in box with one keep-out box, driven through
latency injection at 0, 50, 100, 250 and 500 ms under each of the three execution
modes. Eight adversarial cases per cell: teleport delta, NaN position, Inf
quaternion, non-unit quaternion, a chunk that penetrates the keep-out box with
both endpoints legal, force overload, torque overload, and a chunk whose tail is
stale while its head is fresh.

| Measure | Ungated | LOKAHI gated |
|---|---|---|
| Unsafe actions executed, across 120 adversarial executions | 94 | **0** |
| Adversarial cases that leaked through | 8 of 8 | **0 of 8** |
| False-clamp rate on benign chunks | — | **0.0%** |
| Stale actions executed | — | **0** |

**What this does not prove.** The arm is synthetic, the geometry is boxes, and
the timings from this device are noise: it runs roughly nine times slower than a
normal machine with 100 ms interpreter stalls, so the microsecond columns vary by
an order of magnitude between identical near-zero-latency cells. Do not cite a
latency number from here. The correctness columns do not depend on the clock.

---

## 2. Constraint cost on Safety-Gymnasium

`benchmarks/safety_gymnasium_runner.py`

Safety-Gymnasium (PKU-Alignment, Apache-2.0, NeurIPS 2023 Datasets and
Benchmarks) is the published suite for safe reinforcement learning. Its
`env.step` returns `(observation, reward, cost, terminated, truncated, info)`,
where `cost` is that step's constraint violation — the quantity a runtime gate
claims to reduce.

Both arms run one seeded driver over the same environment seeds, so any
difference is caused by the gate. Three seeds per task, 1000-step horizon,
`safety-gymnasium` 1.0.0 on CPython 3.10.22.

```bash
# seeds are listed, not counted: `--seeds 3` means seed 3 alone.
py -3 benchmarks/safety_gymnasium_runner.py --seeds 0 1 2
# the keep-out shape is selectable, for the D-1 comparison:
py -3 benchmarks/safety_gymnasium_runner.py --seeds 0 1 2 --keep-out circle   # default
py -3 benchmarks/safety_gymnasium_runner.py --seeds 0 1 2 --keep-out box
```

### Arm A: keep-outs as bounding boxes (`--keep-out box`)

| Task | Arm | Reward / ep | Cost / ep | Charged steps | Intervention | False clamp |
|---|---|---|---|---|---|---|
| SafetyPointGoal1-v0 | ungated | −1.260 | 38.3 | 115 | — | — |
| SafetyPointGoal1-v0 | gated | −2.331 | **79.3** | 251 | 50.6% | 43 |
| SafetyPointButton1-v0 | ungated | −4.440 | 19.3 | 58 | — | — |
| SafetyPointButton1-v0 | gated | −1.025 | **227.3** | 682 | 20.9% | 10 |
| SafetyCarGoal1-v0 | ungated | 2.547 | 92.7 | 278 | — | — |
| SafetyCarGoal1-v0 | gated | −0.072 | **45.3** | 136 | 61.9% | 467 |

### Arm B: keep-outs as discs (`--keep-out circle`, the default)

| Task | Arm | Reward / ep | Cost / ep | Charged steps | Intervention | False clamp |
|---|---|---|---|---|---|---|
| SafetyPointGoal1-v0 | ungated | −1.260 | 38.3 | 115 | — | — |
| SafetyPointGoal1-v0 | gated | 0.069 | **83.7** | 251 | 32.3% | **0** |
| SafetyPointButton1-v0 | ungated | −4.440 | 19.3 | 58 | — | — |
| SafetyPointButton1-v0 | gated | −1.269 | **156.0** | 468 | 3.1% | **0** |
| SafetyCarGoal1-v0 | ungated | 2.547 | 92.7 | 278 | — | — |
| SafetyCarGoal1-v0 | gated | −0.383 | **154.0** | 462 | 63.8% | **0** |

Same seeds, same driver, same horizon. The only difference between the arms is
the geometry the gate was given.

### Reading this honestly

**The disc removes over-refusal completely and does not fix the cost.**

Over-refusal goes to zero on all three tasks: 43 → 0, 10 → 0 and 467 → 0. Every
refusal the gate now issues is a step the environment would itself have charged
for. That is the D-1 acceptance criterion met on its first half — the gate no
longer stops the robot for no reason, and it is now measuring the world as the
world is.

Cost does not follow. SafetyCar goes from 92.7 ungated to 154.0 gated
(interventions on 63.8% of steps), SafetyPoint 38.3 → 83.7, SafetyButton 19.3 →
156.0. Reward moves the other way on Point (−1.260 → 0.069) and slightly down on
the other two, so the arm is not simply frozen everywhere.

The cause is visible in the shape of the numbers. A refusal is a one-step
judgement: the gate rejects the step that would end inside a disc, the driver
re-plans from where it is, that step is refused too, and the robot stands still
while the environment charges it for standing inside the hazard. 462 of
SafetyCar's 3,000 gated steps were charged while only 278 of the ungated arm's
were — the shield converted a moving violation into a stationary one. Precision
without a recovery path is a robot that stops in the worst place.

That is exactly what the escape-planning work targets: when every forward step
is refused, the gate has to hand back a setpoint that increases clearance rather
than a hold.

### What changed as a direct result

An earlier version of the gate scored 319 cost per episode on SafetyPoint
against 38 ungated. The cause was found in simulation and fixed in
`tools/vla/vla_gate.py`: the gate refused any segment starting inside a keep-out
box, so a robot that drifted into a hazard could never be commanded out, and the
environment charged it for every step it remained. The gate now measures how
badly a constraint is already violated and, when the robot starts in violation,
accepts a chunk whose end pose is no worse. Six tests cover it.

### What changed as a direct result

An earlier version of the gate scored 319 cost per episode on SafetyPoint
against 38 ungated. The cause was found in simulation and fixed in
`tools/vla/vla_gate.py`: the gate refused any segment starting inside a keep-out
box, so a robot that drifted into a hazard could never be commanded out, and the
environment charged it for every step it remained. The gate now measures how
badly a constraint is already violated and, when the robot starts in violation,
accepts a chunk whose end pose is no worse. Six tests cover it.

### What this does not prove

- Three tasks, three seeds, and a scripted waypoint driver. This measures the
  gate, not any learned policy, and it is not comparable to a published SafeRL
  baseline table.
- The gate models obstacles as static geometry. Disc geometry is now modelled
  exactly and the over-refusal column is zero, so that mismatch is closed.
  Moving hazards are still never given to the gate; SafetyButton's charge is
  still mostly from obstacles it cannot see.
- No task shows a clean cost reduction at preserved reward. **A published claim
  of "LOKAHI reduces constraint violations" is not supported by this table yet.**

---

## What would make these publishable

1. ~~Model obstacles as their real geometry — discs and cylinders, not bounding
   boxes — and re-run.~~ **Done**, see arm B: false clamps 43/10/467 → 0/0/0.
   The cost did not follow, which is the finding.
2. Give the gate a recovery path when every forward step is refused. Without it a
   precise refusal freezes the robot inside the hazard it refused to enter, which
   is where the remaining cost comes from.
3. Give the gate the moving hazards, or declare them out of scope explicitly and
   report SafetyButton as unmeasurable rather than bad.
4. An ungated arm that violates on purpose with enough headroom to measure a
   difference: the current driver avoids hazards too well on Point and Button for
   the comparison to have room.
5. Only then compare against a published safety-filter baseline, and only across
   the same seeds and horizon.
