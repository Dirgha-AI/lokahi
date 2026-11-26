# The gate against the alternatives, on one corpus

A gate is only worth its cost against what a team would otherwise ship. This
measures `LokahiVLAGate` beside three arms that an integrator can — and often
does — write in an afternoon, on the corpus the project already publishes:
`benchmarks/vla_lag_and_safety_bench.py`. That file is imported by
`benchmarks/alternatives_bench.py`, not copied, so the route, the latencies, the
three execution modes and the eight adversarial cases are the same objects in
every column of the table below.

| arm | what it is |
|---|---|
| `ungated` | the raw policy; nothing between the chunk and the actuator |
| `clamp_only` | a conventional filter: refuse a plan older than `max_action_age_ms`, clamp any step larger than `max_pos_delta_m` |
| `swept_geometry_only` | `clamp_only` plus a slab segment test of every path against the keep-out box |
| `lokahi` | `LokahiVLAGate`, the nine-check gate |

Nothing in any arm was tuned to move a number. The two alternatives implement
the checks they name and no others; where a check was absent, the arm fails in
the way such code fails in the field, and the sections below say which.

## Units

* **Adversarial executions reaching the actuator** — of the 120 adversarial
  cells (3 execution modes × 5 latencies × 8 cases), the number in which at
  least one action the ground-truth oracle calls unsafe was executed. 120 is a
  cell count; the action count is reported beside it.
* **Over-refusals on benign chunks** — of the 15 benign cells (3 modes × 5
  latencies, 300 ticks each), decisions that hard-refused a step the oracle
  allows. A clamp of an allowed step is counted separately as a rewritten step,
  so a rate limiter cannot hide behind the narrower column.
* **Decisions per second** — wall clock around the arm's decision call only, on
  one machine (DESKTOP-JD31HB0, a Windows device that stalls). These are
  this-device numbers, not a latency claim, and they are not comparable with any
  other machine.

## The table

Same corpus, same units. The leak, decision and over-refusal counts were
identical in every run performed; only the two timing columns are ranged. The
oracle is the benchmark's own `unsafe_reasons`, and it is not shared with any
arm.

| arm | adversarial cells leaked | unsafe actions executed | benign decisions | over-refusals | benign steps rewritten | decisions/s † | median decision µs † |
|---|---|---|---|---|---|---|---|
| `ungated` | **94 / 120** | 669 | 2,914 | 0 | 0 | 46,471 – 90,753 | 9.4 – 9.6 |
| `clamp_only` | **40 / 120** | 134 | 2,863 | 0 | 0 | 31,779 – 66,087 | 12.2 – 12.8 |
| `swept_geometry_only` | **36 / 120** | 126 | 2,863 | 0 | 0 | 14,257 – 15,701 | 47.2 – 49.5 |
| `lokahi` | **0 / 120** | 0 | 2,863 | 0 | 0 | 790 – 992 | 779 – 824 |

† Observed range over the four runs performed. The rate moves by up to ~2×
between runs because of this device's stalls; the median moves by a few percent.
Read the median for cost and the rate only as this device's floor.

Per case, cells leaked out of 15 (5 latencies × 3 modes):

| case | `ungated` | `clamp_only` | `swept_geometry_only` | `lokahi` |
|---|---|---|---|---|
| `teleport_delta` | 11 | 0 | 0 | 0 |
| `nan_position` | 11 | 6 | 2 | 0 |
| `inf_quaternion` | 11 | 6 | 6 | 0 |
| `nonunit_quaternion` | 11 | 6 | 6 | 0 |
| `swept_keep_out` | 12 | 0 | 0 | 0 |
| `force_overload` | 14 | 11 | 11 | 0 |
| `torque_overload` | 14 | 11 | 11 | 0 |
| `stale_tail` | 10 | 0 | 0 | 0 |

On the benign route the raw policy is already unsafe once latency exceeds the
freshness budget: `ungated` executes 0 unsafe actions at 0 ms, 1 at 100 ms, 57 at
250 ms and 176 at 500 ms, because the chunk it issues is older than the state it
was planned from. Every other arm is 0, and that is the one thing the
age filter genuinely buys.

## What each arm lacks

`✔` declared and implemented; `—` absent. The test file asserts each declaration
against the arm's behaviour and its source, so the table cannot drift from the
code.

| guarantee | `ungated` | `clamp_only` | `swept_geometry_only` | `lokahi` |
|---|---|---|---|---|
| rejects NaN/Inf and malformed positions | — | — | — | ✔ |
| rejects a non-unit quaternion | — | — | — | ✔ |
| ages the step being executed | — | ✔ | ✔ | ✔ |
| sweeps the path against the workspace box (keep-in) | — | — | — | ✔ |
| sweeps the path against keep-out geometry | — | — | ✔ (keep-out only) | ✔ |
| bounds per-step displacement | — | ✔ | ✔ | ✔ |
| refuses force/torque beyond the envelope | — | — | — | ✔ |
| refuses a step after a stalled pipeline resumes | — | — | — | ✔ |
| a fault persists until a named human re-arms | — | — | — | ✔ (E-class only) |
| refuses when its own evaluation overruns | — | — | — | ✔ |
| leaves a verifiable per-episode record | — | — | — | ✔ |

## Which arms fail open, and why

All three alternatives fail open. That is the finding, and each leak has a
mechanism rather than a story.

**`ungated` leaks 94 of 120 because it is not a shield.** Every case reaches the
actuator wherever the chunk arrives before the next plan replaces it: 669 unsafe
actions executed.

**`clamp_only` leaks 40 of 120.** It stops what a rate and an age bound can
stop, and passes everything else that reaches it fresh enough to be evaluated:

* *NaN (6 of 15 cells, 10 unsafe actions).* The filter asks
  `delta > max_pos_delta_m`. `math.dist` returns NaN for a NaN endpoint, and
  every comparison with NaN is false, so the step passes as
  if it were small. This is not a contrived failure; it is what IEEE-754 does to
  a limit check written without a finiteness test, and it is why the gate settles
  the structural question before it subtracts timestamps.
* *Non-unit and infinite quaternions.* The filter has no orientation term, so a
  quaternion that makes the rotation angle a fiction is streamed unchanged: 12
  cells, 12 unsafe actions. In the other cells the injected chunk was either
  replaced before it arrived or refused as stale, which is why the count is 6 per
  case type and not 15.
* *Force and torque.* There is no wrench check at all, so a 80 N contact command
  against a 40 N envelope executes on every tick the chunk is active: 22 cells,
  112 unsafe actions.
* *No latch, watchdog, budget or receipt.* A violation is forgotten between
  steps. Nothing detects a pipeline that stalled and resumed, nothing bounds the
  filter's own evaluation time, and there is no record to verify afterwards.
  None of these costs it a leak on this corpus, because no case here stalls the
  arm or overruns it — the columns above are the honest answer, and the
  guarantee matrix is where the rest is stated.

**`swept_geometry_only` leaks 36 of 120.** It adds exactly one check and closes
exactly the NaN cells that the slab test happens to catch. That reduction from
40 to 36 is an accident of arithmetic, not a property: with the first axis
constant and outside the box, the slab test returns before it reaches the NaN
axis, and the step passes. Both values are reproducible, and the test file pins
the second one, because a reader should know the arm's apparent advantage there
is luck. Orientation, wrench, latch, watchdog, budget and receipt are all still
absent, and it costs roughly four times the median decision time of
`clamp_only` (47–50 µs against 12 µs).

A separate honest point about this corpus: the two cases the alternatives *do*
stop — `swept_keep_out` and `teleport_delta` — are also violations of a rate
bound. `teleport_delta` moves 0.30 m in one step and `swept_keep_out` 0.42 m, so
`clamp_only`, which never looks at geometry, truncates both at 0.05 m and never
sweeps anything. Consequently this corpus does not isolate the swept test from
the rate limit: on it, the geometry arm's extra check buys nothing measurable
except the NaN accident. A case built from steps small enough to satisfy the
delta but positioned to cross the column would separate them; it is not in the
120, and adding it would change the denominator. It is stated here rather than
quietly fixed.

**`lokahi` leaks 0 of 120, and that is a statement about this corpus only.** The
gate refused or clamped in 118 of the 120 adversarial cells; in the other two —
`swept_keep_out` under two latencies — the injected chunk never reached the
actuator, so there was nothing to refuse. That is what the project already
publishes, and it is not evidence about any behaviour these eight cases do not
exercise. The costs sit in the same row: about 70–90× the median decision time
of a rate filter, 323 of 2,863 benign decisions refused outright, and 26 benign
chunk tails truncated. Every one of those 323 is an action the oracle also calls
unsafe — a plan older than `max_action_age_ms`, or one whose next setpoint sat
further from the current pose than `max_pos_delta_m` — which is why the
over-refusal column stays 0. Where a long chunk outlives the freshness budget,
the gate shortens it rather than dropping the fresh head.

## What these numbers are not

The binding non-claims in `docs/LOKAHI_VLA.md` apply unchanged. This gate is not
a safety-rated control function. It is not PL d, SIL or AgPL, and nothing here
may be read as certification, an assessment, or a comparison that implies one.
The arms in this table are three pieces of software measured on one synthetic
corpus in one simulator-free harness; a zero in the `lokahi` column means these
120 cells produced no unsafe execution, not that the gate cannot fail.

## Reproducing

```
py -3 benchmarks/alternatives_bench.py           # the table above
py -3 benchmarks/alternatives_bench.py --json    # the same numbers, machine-readable
py -3 -m pytest tests/test_alternatives_bench.py -q
```

The report re-runs its own `ungated` and `lokahi` arms through the source
benchmark and refuses to be read otherwise: the two leaked-cell counts (94 and
0) must match `benchmarks/vla_lag_and_safety_bench.py` exactly, and they do.
