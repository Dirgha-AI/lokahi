# Brief for the auditing agent

You are picking up LOKAHI v1.4.0 draft to do the final audit and verification
before release. This file is your task. `HANDOVER.md` is the state of the world;
`docs/BENCHMARKS.md` is what the numbers do and do not show; `docs/LOKAHI_VLA.md`
is the design; `docs/STANDARDS_MATRIX.md` is the compliance mapping.

Read `HANDOVER.md` first. It is written for you specifically.

---

## What this repository is

A runtime safety gate for Vision-Language-Action robot policies, built on an
existing deterministic decision engine. It sits between a learned policy and an
actuator, decides whether each action chunk is legal, and emits a signed,
hash-chained receipt of every decision that a third party can verify offline.

It is **not** a certified safety function. Nothing here has run on hardware.

## Your job, in order

### 1. Establish that the tree is what it claims to be

```bash
py -3 -m pytest -q                                   # expect ~459 passed, ~7 skipped
git log --oneline -a | head -20
git status --porcelain                               # expect empty
```

If the suite is not green, stop and report the failures with their tracebacks.
Do not fix them yet — an auditing agent that edits what it audits cannot report
on it honestly.

### 2. Try to break the gate

The gate is the whole product. Read `tools/vla/vla_gate.py` and then attack it.
The five highest-value attacks, in order:

**a. The recovery hole.** When the robot is already in violation, the gate
accepts a chunk whose *end pose* is no worse. A policy that walks through a hazard
one chunk at a time could end each chunk slightly closer to legal than the last
while passing straight through the middle. Construct that sequence. If it works,
the rule needs a maximum excursion or an episode-level monotone descent, and that
is a high-severity finding.

**b. Fail-open paths.** A gate that answers COMPLIANT when its inputs are
unusable is worse than no gate. Three such paths were already found and fixed in
this release: a kinematic violation clamping to the whole chunk, a missing
`ee_pos` skipping geometry, and joint velocity limits validated but never
enforced. Look for a fourth. Try: empty chunk, `joint_pos` length mismatch,
`ee_quat` present on the chunk but absent from `robot_state`, a manifest with no
`physical_envelope`, a wrench given when the envelope has no force limit, a
`tool_radius_m` larger than half the workspace.

**c. The budget bypass.** `max_gate_eval_us` refuses on overrun and may only
escalate a state. Confirm it can never soften an E_STOP to SOFT_STOP, and that a
slow evaluation cannot return COMPLIANT.

**d. The latch.** E_STOP must be absorbing until a named human resets it.
`human_reset` takes an operator string and clears the latch. Confirm nothing else
clears it, and that an empty operator is refused.

**e. The receipt.** `rollout_receipt` recomputes every summary field from the
ledger entries at verification time. Try to make a tampered receipt verify: flip
a latency percentile, drop the last ledger entry, swap the manifest hash, resign
with a different key, present a canonical version 1 chain as version 2.

### 3. Verify the benchmark claims rather than reading them

```bash
py -3 benchmarks/vla_lag_and_safety_bench.py --json
```

Confirm from the JSON, not the prose: `gated_unsafe` is 0, `leaked_cases` is
empty, `max_false_clamp_rate` is 0.0. Then check that no document in the
repository claims a latency number, because the device these were measured on
runs roughly nine times slow with 100 ms stalls and its timing columns are noise.

For the Safety-Gymnasium table, the published result is deliberately mixed and
`docs/BENCHMARKS.md` says a claim of reduced constraint violations is not
supported. Verify that no other document contradicts that. A README sentence that
reads as a positive result would be a serious finding.

### 4. Verify packaging

```bash
py -3 -m pip wheel . --no-deps --no-build-isolation -w /d/tmp/audit
```

Open the wheel and confirm `lokahi/vla/__init__.py`,
`lokahi/vla/vla_gate.py` and `lokahi/vla/adapters/ros2_adapter.py` are all inside.
One packaging bug of exactly this shape was found late in this release: the
adapters subpackage was missing from `[tool.setuptools] packages`.

### 5. Audit the standards matrix

`docs/STANDARDS_MATRIX.md` maps controls to clauses with a gap column. Check every
row for two things: a clause number asserted without a licensed copy at hand
(these are marked with a symbol; confirm the marking is consistent), and a row
with no gap where a gap plainly exists. Overstating compliance is the most
damaging possible defect in this repository.

### 6. Report

Write your findings to `AUDIT-REPORT.md` at the repository root. One row per
finding:

| ID | Severity | File:line | What | Evidence | Suggested fix |

Severities: **critical** (a gate can be bypassed or fails open), **high** (a
published claim is false), **medium** (a defect with a workaround), **low**
(wording, ergonomics). Include the command output that demonstrates each finding.

End the report with an explicit verdict: is this release safe for others to
depend on, and what must change first.

---

## Rules

**Do not** tune a benchmark until it shows a gain. The mixed table is the honest
result; making it look better by changing the driver's hazard-aim rate is
falsification.

**Do not** weaken, skip or rename a test to make the suite pass. If a test is
wrong, say so and show why; fix the code under test, not the assertion.

**Do not** add a runtime dependency. Zero runtime dependencies is the product
claim that makes this deployable air-gapped and auditable offline. Anything you
need for evaluation goes in `[project.optional-dependencies]`.

**Do not** merge canonical version 2 into manifest signing. Every issued
manifest's signature would stop verifying.

**Do not** commit secrets, and do not push to a public remote.

## Environment traps, all found the hard way

- `python` on this machine is a Windows Store stub. Use `py -3`.
- `timeout N <command>` is Windows `timeout.exe` and silently runs nothing. A
  127 exit code usually means that, not a missing binary.
- The machine runs ~9× slower than normal with 100 ms interpreter stalls. Timing
  numbers from it are meaningless; correctness numbers are fine.
- Safety-Gymnasium is not installed in the repo interpreter. Use a separate venv;
  its tests skip cleanly without it.
- `gitea.exe` in `D:\Projects\Public\release\gitea-local\` migrates and creates
  users but never binds a port on this machine. Local tracking is a bare git
  mirror; see `HANDOVER.md` D-6.
