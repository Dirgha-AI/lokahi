# HANDOVER — LOKAHI v1.4.0 draft

Written for the next agent to pick this up without asking anyone what happened.

**Branch:** `feat/v1.3.1-truth` · **Tree:** clean · **Suite:** 459 passed, 7 skipped
(one load-sensitive adapter test failed once in a full run and passes 3/3 alone — see D-5)

Read this file before reading code. Then read
[`docs/LOKAHI_VLA.md`](docs/LOKAHI_VLA.md) for the design and
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) for what the numbers do and do not show.

---

## 1. State in one screen

| Area | State |
|---|---|
| Canonical digests | ✅ RFC 8785 version 2, versioned, backwards compatible |
| VLA manifest section | ✅ signed, validated, fail-closed |
| Gate (9 checks, 5 states) | ✅ 56 tests, incl. recovery from a violated state |
| Signed rollout receipts + verifier | ✅ 26 tests, tamper detection |
| Adapters (gym / LeRobot / ROS 2) | ✅ 35 tests, no runtime dependency added |
| Adversarial + latency benchmark | ✅ 0 of 120 adversarial executions reach the actuator |
| Safety-Gymnasium benchmark | ⚠️ measured, published with caveats, **one task improves, two regress** |
| Local CI workflow (Gitea/Forgejo) | ✅ ported, never executed |
| Documentation | ✅ architecture, standards matrix, benchmarks, non-claims |

**The honest one-liner:** the module works and is well tested; the *evidence that
it helps* is mixed and the reasons are known. Do not publish a claim of reduced
constraint violations until D-1 to D-4 are closed.

---

## 2. Verify it yourself in five commands

```bash
cd "/d/Projects/Public/release/lokahi"          # or the clone path
py -3 -m pytest -q                               # 459 passed, 7 skipped, ~3 min
py -3 -m pytest tests/test_vla_gate.py -q        # 56 gate tests, ~1 s
py -3 benchmarks/vla_lag_and_safety_bench.py     # adversarial + latency table
py -3 -m pip wheel . --no-deps --no-build-isolation -w /d/tmp/w   # packaging
```

Benchmark against the published suite needs its own interpreter; it is not a
runtime dependency:

```bash
py -3 -m venv /d/tmp/sg310
/d/tmp/sg310/Scripts/python.exe -m pip install safety-gymnasium
/d/tmp/sg310/Scripts/python.exe benchmarks/safety_gymnasium_runner.py --seeds 0 1 2
```

Traps on this device, all discovered the hard way:

- `python` is a Windows Store stub. Use `py -3`.
- `timeout N <cmd>` is Windows `timeout.exe` and silently does nothing. It does
  not wrap commands; a 127 exit is that, not a missing binary.
- The box runs ~9× slower than normal with 100 ms interpreter stalls. Any timing
  number from it is noise; correctness numbers are fine.
- `gitea.exe` starts, migrates its database and creates users, but never binds a
  port here. See D-6.

---

## 3. The bug: what it is, and exactly how I will solve it

The benchmark found a real design defect. It is worth understanding, because the
fix pattern generalises.

### D-1 · Over-refusal: the gate models obstacles as boxes, the world uses discs

**Symptom.** On SafetyCarGoal1 the gate cuts cost 92.7 → 45.3 per episode (−51%)
but 467 of its 619 refusals were steps the environment would not have charged
for. That is a shield that stops the robot for no reason.

**Cause, established.** `physical_manifest` accepts keep-outs as axis-aligned
boxes only. Safety-Gymnasium charges on a hazard's *disc*. A box circumscribing a
disc is up to 27% larger on the diagonal, so poses that are legally clear are
refused.

**Fix, in this order.**

1. Add an explicit optional `shape` to keep-outs in the manifest:
   `{"shape": "circle", "centre": [x, y], "radius": r}` alongside the existing box
   form, and `{"shape": "cylinder", "centre": [x, y, z], "radius": r, "height": h}`
   for 3D. Default stays `box`, so no existing manifest changes meaning.
2. In `vla_gate`, implement the swept test for the new shapes: point-to-segment
   distance in the plane for a circle, and the same plus a z-slab test for a
   cylinder. Keep the existing slab test for boxes; do not replace it.
3. Re-run `--seeds 0 1 2` (`--seeds` lists seeds, it does not count them). The acceptance criterion is not "cost went down": it is
   **the false-clamp count falls while the cost reduction survives**. If the
   false clamps fall but the cost rises, the change was wrong.

**Why this fixes it rather than hides it.** Over-refusal comes from representing
the world more coarsely than the world is. Adding the shape the world actually
has removes the error at its source. Loosening thresholds would have hidden the
same error and shown better numbers.

### D-2 · Moving hazards are not modelled at all

**Symptom.** SafetyButton regresses to 227 cost per episode. Its hazards move.

**Cause.** The gate's keep-outs are static, fixed at manifest load. A hazard that
moves after load is invisible to it.

**Fix.** Two acceptable answers; pick one deliberately.

- *Support them:* add an optional per-step `dynamic_keep_outs` argument to
  `evaluate_step`, validated against a manifest-declared maximum count and
  maximum speed so a caller cannot smuggle in a 1000-obstacle denial-of-service.
  The receipt must record that the set came from the caller, not the manifest.
- *Declare them out of scope:* state in the manifest schema and in
  `docs/BENCHMARKS.md` that dynamic obstacles are not gated, and report
  SafetyButton as unmeasurable rather than as a regression.

I recommend supporting them, because a warehouse or a farm is full of moving
things and "static obstacles only" is not a product.

### D-3 · The benchmark has no headroom

**Symptom.** After D-1, the ungated arm barely violates on Point (cost 0 in one
run) and Button. A shield cannot improve a number that is already near zero.

**Cause.** The driver avoids hazards competently; only a third of its targets aim
at them.

**Fix.** Make the *ungated* arm violate on purpose, so the comparison has room:
raise the hazard-aim fraction and record it in the result JSON, or drive with a
trained unsafe policy. Then, and only then, compare against a published SafeRL or
safety-filter baseline on identical seeds and horizon.

### D-4 · No baseline comparison yet

**Symptom.** The table has two arms and no third column.

**Fix.** `safe-control-gym` (MIT) ships MPSC, CBF and neural CBF as first-class
filters and puts constraint evaluations in the info dict. That is the benchmark
built to compare filters, and LOKAHI belongs in that column. Do it after D-1, or
the comparison measures our geometry bug.

### D-5 · One load-sensitive test

`tests/test_vla_adapters.py::test_gym_four_element_env_terminated_maps_done`
failed once in a full run, passes 3/3 alone. Something in it is timing-dependent,
probably the gate's own `max_gate_eval_us` budget under load. Fix the cause: pin a
generous budget in the fixture, or assert on the verdict rather than a derived
field. Do not add a retry.

### D-6 · Gitea will not serve on this machine (environment, not code)

Binary installed at `D:\Projects\Public\release\gitea-local\gitea.exe` (28.4.0,
sha256 verified), `custom/conf/app.ini` on SQLite, database migrated (39 tables),
admin row created. `gitea web` produces no output and never binds 3000; the CLI
subcommands hang for minutes and never complete. Not investigated to root cause;
the plausible explanations are antivirus scanning a 123 MB binary on every start
and the machine's general I/O latency.

**Meanwhile:** local tracking is a bare mirror, `git push gitea
feat/v1.3.1-truth`. `gitea-vps` remote is configured for
`ssh://git@31.97.239.223:2222/salik/lokahi.git`. The CI workflow for a working
instance is already committed (`.gitea/`, `.forgejo/`).

### D-7 · Known gaps that are decisions, not bugs

- `execution_mode` is validated and stored but changes no behaviour: all three
  modes gate one chunk per call.
- `obs_to_action_ms` measures observation to decision, not to actuator emission.
- Manifest, approval and action digests stay canonical version 1 by design;
  moving them would invalidate every issued signature.
- ROS 2 adapters are fixture-tested only, never against `rclpy`.

---

## 4. To-do list

Ordered. Each has an acceptance criterion, because "improve the benchmark" is not
a task.

| # | Task | Acceptance |
|---|---|---|
| 1 | Circle and cylinder keep-out shapes (`physical_manifest` + `vla_gate`) | false-clamp count falls and the cost reduction survives on Car |
| 2 | Per-step dynamic keep-outs with manifest-declared caps | Button cost falls; receipt records the caller as the source |
| 3 | Bench driver with real violation headroom | ungated arm has non-trivial cost on all three tasks |
| 4 | Per-obstacle attribution in the harness | every charged step names the hazard that caused it |
| 5 | `safe-control-gym` filter comparison | LOKAHI in the MPSC/CBF column, same seeds and horizon |
| 6 | Fix the load-sensitive adapter test | 3 consecutive full-suite runs, no flakes |
| 7 | Implement or remove `execution_mode` | either behaviour differs per mode and is tested, or the field is gone |
| 8 | `safe_vla_bench` STL metrics (needs GPU) | safety rate, SBU and VSI reported on LIBERO |
| 9 | Tag `v1.4.0` | after 1–4 land and the suite is green three times |
| 10 | One external safety engineer reads the standards matrix | written review on file |

---

## 5. Instructions for the reviewing agent

**Trust nothing you have not run.** Every number in this repository was produced
by a command that is written down next to it. If a number has no command, treat
it as a claim, not a measurement.

**Check these five things specifically.**

1. **The recovery rule.** `vla_gate.py`, the keep-in and keep-out branches. Ask:
   can a compromised or hallucinating policy use `recovery_required` as a licence
   to walk *through* a hazard by ending each chunk slightly closer to legal on the
   far side? If yes, that is a real hole and it needs a maximum excursion or a
   monotone descent over the episode, not the chunk.
2. **The fail-closed paths.** A gate that returns COMPLIANT when its inputs are
   unusable is worse than no gate. Three were found and fixed during development;
   look for a fourth. Try: missing `ee_pos`, empty chunk, unreadable quaternion,
   `joint_pos` length mismatch against `joint_limits`, a manifest whose
   `physical_envelope` lacks force limits while the caller passes a wrench.
3. **The receipt.** `rollout_receipt` recomputes every summary field at
   verification time. Try to make a tampered receipt verify: flip a latency,
   drop a trailing ledger entry, resign with a different key, swap the manifest
   hash, present a version 1 chain as version 2.
4. **The packaging claim.** `pip wheel` and confirm `lokahi/vla/` and
   `lokahi/vla/adapters/` are both inside. One bug of exactly this kind was found
   late: the adapters subpackage was missing from `[tool.setuptools] packages`
   and a pip install would have shipped the gate without them.
5. **The non-claims.** `docs/LOKAHI_VLA.md` says LOKAHI is not a safety-rated
   control function and cannot be PL d under ISO 13849-1. Check that nothing
   elsewhere contradicts it. A README sentence that sounds like certification is
   a serious defect.

**Do not do these.** Do not tune a benchmark until it shows a gain. Do not weaken
a test to make a suite green. Do not add a runtime dependency: zero runtime
dependencies is the product claim that makes the thing deployable air-gapped.
Do not merge canonical version 2 into manifest signing.

---

## 6. What this is not

Not certified. Not a safety-rated control function. Not PL d, not SIL, not AgPL.
No hardware has ever run this. The standards matrix labels every clause number it
could not verify against a licensed copy, and every row where we emit an artifact
but not a certification.

---

## 7. Where everything lives

| Path | What |
|---|---|
| `tools/vla/vla_gate.py` | the nine checks, five states, recovery rule |
| `tools/vla/physical_manifest.py` | `physical_vla` schema and validation |
| `tools/vla/rollout_receipt.py` | signed receipts and `lokahi-vla-verify` |
| `tools/vla/adapters/` | gym, LeRobot, recorded ROS 2 |
| `benchmarks/vla_lag_and_safety_bench.py` | adversarial + latency table |
| `benchmarks/safety_gymnasium_runner.py` | constraint cost on the published suite |
| `docs/LOKAHI_VLA.md` | architecture, check order, non-claims |
| `docs/STANDARDS_MATRIX.md` | control → clause → evidence → gap |
| `docs/BENCHMARKS.md` | both tables with their caveats |
| `docs/CI_LOCAL.md` | Gitea vs Forgejo runners |
| `CHANGELOG.md` | v1.4.0 entry, including what it does not do |
| `ROADMAP.md` | release train and the V-series task board |
