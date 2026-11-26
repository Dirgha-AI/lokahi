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
py -3 benchmarks/safety_gymnasium_runner.py --seeds 3
```

| Task | Arm | Reward / ep | Cost / ep | Charged steps | Intervention | False clamp |
|---|---|---|---|---|---|---|
| SafetyPointGoal1-v0 | ungated | −1.260 | 38.3 | 115 | — | — |
| SafetyPointGoal1-v0 | gated | −2.331 | **79.3** | 238 | 50.6% | 43 |
| SafetyPointButton1-v0 | ungated | −4.440 | 19.3 | 58 | — | — |
| SafetyPointButton1-v0 | gated | −1.025 | **227.3** | 682 | 20.9% | 10 |
| SafetyCarGoal1-v0 | ungated | 2.547 | 92.7 | 278 | — | — |
| SafetyCarGoal1-v0 | gated | −0.072 | **45.3** | 136 | 61.9% | 467 |

### Reading this honestly

**One task improves, two regress, and the improvement is not clean.**

SafetyCar is the only reduction: cost per episode 92.7 → 45.3, a 51% cut. It is
bought at a real price. The gated car earns −0.072 reward where the ungated car
earns 2.547, so it made less progress, and **467 of its 619 refusals were steps
the environment would not have charged for** — over-refusal, not precision. The
cause is named: the harness hands the gate a hazard's bounding *square* while the
suite charges on the *disc*, and a one-step prediction is conservative for a
coasting vehicle.

SafetyPoint regressed from 38.3 to 79.3 cost per episode at only 50.6%
interventions, so the gate is not preventing entry there. SafetyButton regressed
badly, and its explanation is structural rather than mysterious: that task's
moving hazards are never given to the gate, so most of its charge is from
obstacles the gate cannot see.

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
- The gate models obstacles as static axis-aligned boxes. Safety-Gymnasium has
  moving hazards and disc geometry. Both mismatches are visible in the
  over-refusal column and neither is fixed.
- No task shows a clean cost reduction at preserved reward. **A published claim
  of "LOKAHI reduces constraint violations" is not supported by this table yet.**

---

## What would make these publishable

1. Model obstacles as their real geometry — discs and cylinders, not bounding
   boxes — and re-run. That directly attacks the false-clamp count.
2. Give the gate the moving hazards, or declare them out of scope explicitly and
   report SafetyButton as unmeasurable rather than bad.
3. An ungated arm that violates on purpose with enough headroom to measure a
   difference: the current driver avoids hazards too well on Point and Button for
   the comparison to have room.
4. Only then compare against a published safety-filter baseline, and only across
   the same seeds and horizon.
