# Adversarial audit — LOKAHI VLA gate, 2026-11-27

Scope: `tools/vla/physical_manifest.py`, `vla_gate.py`, `geometry.py`, `replanning.py`,
`kinematics.py`, `rollout_receipt.py`, `adapters/*.py`, at `feat/v1.3.1-truth`
(`99f218d`). Read-only: no source file was modified, nothing committed. The only file
this audit added to the repository is this one.

## Scope note before anything else

The task names `tools/vla/recovery.py` and an `escape_path`. **Neither exists.** There is
no `recovery.py` in the tree, and `grep -rn "escape_path"` over `--include=*.py
--include=*.md` returns nothing. What the design calls recovery is two things:

- `_recovery_hint` (`vla_gate.py:713-737`) — a prose-ish direction dict, it proposes no pose;
- the "recovery" branch of the geometry sweep (`vla_gate.py:349-372`, `385-414`) — when the
  arm already starts inside a violation, the chunk is judged on its *end pose alone*.

Finding F5 below attacks that second thing, which is where an `escape_path` would have lived.
The replanned stop (`vla_gate.py:561-599`) is the only other code that emits a pose the gate
did not sweep, and findings F7/F8 cover it.

## Method and what could not be measured

Repro scripts live outside the repository at `C:\Users\ABC\.capy\work\audit\` (`proofs.py`,
`proofs3.py`, `proofs4.py`, `mini.py`, `mini2.py`; a `proofs2.py` stopped at its R5 block
after R2, and those experiments were rerun in `proofs4.py`). They import the tree through the
same alias the wheel uses (`package-dir = {"lokahi" = "tools"}`), pass `py -3`, and change
nothing on disk. Every output quoted below is pasted verbatim from those runs.

I did **not** run the repository test suite, per the "run only your own tests" rule, so I
cannot say whether any existing test already pins the behaviour reported here. Timing
numbers below are from this machine (the one that runs ~9x slow with 100 ms stalls); they are
used only to establish that a budget overrun happens, never as a latency claim.

Two attacks I ran and **discarded as findings** because the code held — recorded so nobody
re-walks them:

- *Budget bypass (brief item c).* My first read of `evaluate_step` suggested the COMPLIANT
  return at `vla_gate.py:513-515` skipped the budget check. It does not: the budget block at
  `601-616` lives in `_result`, which every path goes through. A simulated 50 ms gate returns
  `FORBIDDEN` with `budget_exceeded:50000us>2000us` even with zero violations, and an E_STOP
  survives a 50 ms overrun (`e_stop_latched` + `budget_exceeded`, state stays `E_STOP`).
- *Receipt tamper.* Editing `gate_eval_us` inside a signed entry is caught:
  `ledger_chain: entry 0 hash does not recompute`. Dropping entries, flipping the root and
  re-signing with another key are all caught by the pinned-key comparison at
  `rollout_receipt.py:523-530`.

Everything else below reproduced.

## Summary

| ID | Severity | File:line | What |
|---|---|---|---|
| F1 | high | `vla_gate.py:301, 322-330, 643-648` | `duration_ms` is never validated: NaN or negative on an early element hides a stale tail, and a NaN on the last legal element yields a "deceleration" profile with no motion and NaN velocity |
| F2 | high | `vla_gate.py:306-311` | The missing-`ee_pos` guard omits `keep_out_shapes`, so a shapes-only manifest passes a setpoint at a keep-out centre |
| F3 | high | `vla_gate.py:443, 457-459` | Joint limits and joint velocity are skipped whenever `robot_state` omits `joint_pos`, even though the chunk carries the joints |
| F4 | high | `vla_gate.py:497-507, 667-672` | A NaN or missing wrench silently skips a configured force/torque ceiling |
| F5 | high | `vla_gate.py:366-372, 385-394, 405-414` | The recovery rule compares end poses only: a chunk may travel 2 m further outside the workspace, or go deeper into a keep-out mid-chunk, and still be COMPLIANT |
| F6 | high | `physical_manifest.py:277-327, 332-344`; `vla_gate.py:215` | No verifier by default, and a bounds-free manifest builds a gate that checks nothing: a chunk 70 m outside returns COMPLIANT |
| F7 | medium | `vla_gate.py:561-599` | The replanned stop is emitted inside `safe_action` with no sweep: 12 of its 127 waypoints sit inside a keep-out box |
| F8 | medium | `vla_gate.py:601-616` | A budget overrun rewrites the verdict to FORBIDDEN but keeps `replanned_stop` as the payload, so the documented "hold at the current pose" is not what a consumer receives |
| F9 | medium | `physical_manifest.py:40-53, 233-238`; `vla_gate.py:684-710` | A manifest can remap an E_STOP class to `CLAMP`, which the gate turns into a non-latching SOFT_STOP; the next clean chunk is ARMED with no `human_reset` |
| F10 | medium | `vla_gate.py:238-243, 514` | `SOFT_STOP → CLAMPED, allowed=True` is reachable and is not in the documented state machine |
| F11 | medium | `geometry.py:227-237` (`vla_gate.py:405`) | A mesh keep-out makes every `evaluate_step` raise, so the documented COAL sweep path is unreachable |
| F12 | medium | `rollout_receipt.py:171-204, 224-280, 370-386` | Receipt provenance is a builder claim: arbitrary `manifest_hash`, arbitrary verdict strings, stub decisions, and `gate_eval_us=0.0` all verify |
| F13 | medium | `physical_manifest.py:304`; `vla_gate.py:115-120, 384` | A negative `tool_radius_m` is accepted and widens the keep-in region: a setpoint outside the declared workspace is COMPLIANT |
| F14 | low | `vla_gate.py:426, 434-440` | The rotation bound is skipped for the first setpoint whenever `robot_state` has no `ee_quat` |
| F15 | low | `physical_manifest.py:292` | `max_gate_eval_us` has no upper bound and no relation to `control_period_ms` |
| F16 | low | `docs/LOKAHI_VLA.md:15, 68-78, 83-88, 182, 193, 247, 253, 254, 262` | Documentation that the code no longer matches (three claims are stale in the *unsafe* direction to read, three in the safe direction) |

---

## F1 — `duration_ms` is unvalidated, and freshness is the only thing standing between a stale chunk and the actuator

**Severity: high.**
`vla_gate.py:301` coerces `duration_ms` with `float(element.get("duration_ms", control_period_ms))`
inside the structural `try`, but nothing checks that it is finite or positive. `vla_gate.py:322-330`
then accumulates it into every later element's age, and `643-648` divides by it to derive velocity.

Trigger:

```python
g = gate()                       # max_action_age_ms=150
r = g.evaluate_step(obs_ts_ns=0, now_ts_ns=100_000_000, robot_state=STATE0,
                    action_chunk=[{"ee_pos": [0.51, 0.5, 0.5], "duration_ms": float("nan")},
                                  {"ee_pos": [0.52, 0.5, 0.5], "duration_ms": 20.0}])
```

Output (control, `duration_ms=1000` on element 0, then `NaN`, then `-1e9`):

```
  control: e0 dur 1000 ms, tail age 1100 ms > 150: verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['stale_action:1:1100.0ms>150.0ms']
  e0 dur NaN, tail age therefore NaN: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  e0 dur -1e9, tail age negative: verdict=COMPLIANT state=ARMED allowed=True violations=[]
```

**Consequence:** a policy (or a malformed adapter row) can make the tail of an arbitrarily stale
chunk execute by putting `NaN` or a negative number in one earlier `duration_ms`, which is exactly
the slow-inference failure the gate exists to catch. The published adversarial corpus has a
stale-tail case (`docs/BENCHMARKS.md:24-25`) but no malformed-`duration_ms` case, so the 0-of-8
result does not cover it.

The same missing check corrupts the stop profile. With `clamp_strategy: replan`, a NaN on the last
legal element divides into `_chunk_velocity`, `stop_duration` collapses to 0 because
`max(0.0, nan)` keeps the 0.0, and the gate returns a **successful** `replanned_stop`:

```
verdict: CLAMPED allowed: True mode: replanned_stop
stop_profile: [{'t_ms': 0.0, 'ee_pos': [0.2, 0.5, 0.5], 'ee_vel': [nan, nan, nan]}]
```

**Consequence:** the arm is told it has a deceleration profile that contains one waypoint at rest
with NaN velocity — an empty stop dressed as a planned one.

**Suggested check that proves the fix:** a structural test that walks `[float("nan"),
float("inf"), -1.0, 0.0]` through `duration_ms` for the last, first and middle element and asserts
`verdict == FORBIDDEN` with `structural:chunk[i]:duration_ms` for each; plus one test asserting no
`replanned_stop` payload can contain a non-finite `ee_vel`. Regex for the code: `duration_ms` must go
through `_require_positive`-style validation before it enters `parsed`.

## F2 — the fail-closed guard for a missing `ee_pos` forgets shape keep-outs

**Severity: high.**
`vla_gate.py:306-311` refuses a `robot_state` without `ee_pos` only when one of
`workspace_aabb`, `keep_out_aabbs`, `max_pos_delta_m`, `max_rot_delta_rad` is configured. The
geometry sweep at `403-420` also consumes `keep_out_shapes`, which is not in that list, so a
manifest that names only shapes and a caller that forgets `ee_pos` produce a gate that checks
nothing at all.

Trigger:

```python
g = LokahiVLAGate.from_manifest({"physical_vla": {
    "max_action_age_ms": 150.0, "watchdog_timeout_ms": 500.0,
    "max_gate_eval_us": 2000.0, "control_period_ms": 20.0,
    "keep_out_shapes": [{"kind": "circle", "centre": [0.5, 0.5], "radius": 0.3}]}})
r = g.evaluate_step(obs_ts_ns=0, now_ts_ns=1_000_000,
                    robot_state={"joint_pos": [0.0] * 6},
                    action_chunk=[{"ee_pos": [0.5, 0.5, 0.5], "duration_ms": 20.0}])
```

Output:

```
  setpoint at the centre of a circle keep-out, robot_state without ee_pos: verdict=COMPLIANT state=ARMED allowed=True violations=[]
```

**Consequence:** the one input shape the code already recognised as fail-open (missing current
pose) still returns COMPLIANT when the hazard is a shaped keep-out, which is the configuration the
benchmarks now prefer.

**Suggested check that proves the fix:** parametrise the existing missing-`ee_pos` test over every
geometry source — `workspace_aabb`, `keep_out_aabbs`, `keep_out_shapes`, `max_pos_delta_m`,
`max_rot_delta_rad` — and require `structural:robot_state_missing_ee_pos` for each. The stronger
fix (refuse without `ee_pos` unconditionally) would make the whole table moot.

## F3 — joint limits and joint velocity are skipped when the *current* joint vector is missing, although the chunk carries its own joints

**Severity: high.**
`vla_gate.py:443` guards the per-element joint-limit check with `config.joint_limits and joint_now is not None`,
and `457-459` guards the velocity/acceleration block the same way. Neither check needs the current
joint vector: joint limits are a per-element predicate, and only acceleration needs a previous
velocity.

Trigger:

```python
joint_gate = gate(kinematic_limits={"joint_limits": [[-1.0, 1.0]] * 6,
                                    "max_joint_vel_rad_s": 1.0})
r = joint_gate.evaluate_step(obs_ts_ns=0, now_ts_ns=1_000_000, robot_state={"ee_pos": [0.5, 0.5, 0.5]},
                             action_chunk=[{"ee_pos": [0.51, 0.5, 0.5], "joint_pos": [9.0] * 6,
                                            "duration_ms": 20.0}])
```

Output:

```
  joints [9]*6 against limits [-1,1], robot_state has no joint_pos: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  same chunk, robot_state carries joint_pos (control): verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['kinematic:joint_limit:0:0:9.000', ..., 'kinematic:joint_vel:0:450.000rad/s>1.0']
```

**Consequence:** the ROS 2 adapter's own `joint_state_to_robot_state` (`ros2_adapter.py:104-126`)
carries joint positions through, so this only bites a deployment that feeds a Cartesian-only
state — precisely the deployment that most needs the joint check, since it is not describing the
joints any other way.

**Suggested check that proves the fix:** keep the joint-limit loop outside the `joint_now` guard
and add a test where `robot_state` has only `ee_pos` and the chunk commands `[9]*6` against
`[-1, 1]`, expecting `kinematic:joint_limit`. Velocity can stay guarded but must emit a
`structural:robot_state_missing_joint_pos` refusal instead of silence.

## F4 — a NaN or absent wrench disables a configured force ceiling

**Severity: high.**
`vla_gate.py:497` (`if wrench:`) and `667-672` (`_scalar_or_norm` returns `None` for a non-finite
value) both route to "no check ran", and the result is COMPLIANT. The manifest can hold a force
ceiling from `physical_envelope` (`physical_manifest.py:354-357`) with no requirement that the
caller ever supply a measurement.

Trigger:

```python
g = gate()   # physical_envelope.max_contact_force_n = 140
g.evaluate_step(..., wrench={"force_n": 500.0})        # control
g.evaluate_step(..., wrench={"force_n": float("nan")})
g.evaluate_step(..., wrench=None)
```

Output:

```
  force 500 N, envelope ceiling 140 N (control): verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['wrench:force:500.00N>140.0N']
  force NaN, envelope ceiling 140 N: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  no wrench at all, envelope ceiling 140 N: verdict=COMPLIANT state=ARMED allowed=True violations=[]
```

**Consequence:** a sensor that drops out (NaN) or an adapter whose `wrench_from_obs` returns
`None` — the default in `gym_wrapper.py:316-321` — reads as "force fine" rather than "force
unknown", so a contact violation during a sensor dropout is invisible.

**Suggested check that proves the fix:** when `config.max_force_n` is set, treat
`wrench is None` and any non-finite component as a `wrench:unmeasured` violation (SOFT_STOP at
minimum); test each of the three inputs above and expect a refusal for all three.

## F5 — the recovery rule compares end poses, so "leaving" is not enforced

**Severity: high.** This is the attack `AUDIT-BRIEF.md` item (a) names, and it works.

`vla_gate.py:366-372` (keep-in) and `385-394` (keep-out AABB, `405-414` shaped) compute a distance
at the start and at the *last* waypoint only. Intermediate waypoints are never compared, and the
segment sweep is skipped entirely in this branch (`395` is reached only when `start_depth == 0`).

Triggers and output:

```python
# keep-out, start 0.05 m inside, mid 0.10 m inside (deeper), end 0.05 m inside
g.evaluate_step(..., robot_state={"ee_pos": [0.55, 0.5, 0.52]},
    action_chunk=[{"ee_pos": [0.70, 0.5, 0.52], "duration_ms": 20.0},
                  {"ee_pos": [0.55, 0.5, 0.51], "duration_ms": 20.0}])
# keep-in, start 0.5 m outside the workspace, mid 2.0 m outside, end 0.4 m outside
g.evaluate_step(..., robot_state={"ee_pos": [1.5, 0.5, 0.5]},
    action_chunk=[{"ee_pos": [3.0, 0.5, 0.5], "duration_ms": 20.0},
                  {"ee_pos": [1.4, 0.5, 0.5], "duration_ms": 20.0}])
```

```
  keep-out: start depth 0.05, mid 0.10 (deeper), end 0.05: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  margins={'oldest_action_age_ms': 21.0, 'keep_in_margin_m': 0.41999999999999993, 'keep_out_recovery_0_m': 0.0}
  keep-in: start 0.5 m outside, mid 2.0 m outside, end 0.4 m outside: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  margins={'oldest_action_age_ms': 22.0, 'keep_in_recovery_m': 0.10000000000000009}
```

**Consequence:** once the arm is inside a hazard, a policy can drive it 1.5 m further out of the
workspace, or deeper into the obstacle, one chunk at a time and keep the gate's approval, because
each chunk ends no worse than it started. The docstring's "monotonically leaving" (`geometry.py:206`)
and the document's "monotone-recovery rule" (`docs/LOKAHI_VLA.md:262`) are not what the code
implements.

**Suggested check that proves the fix:** bound the excursion — reject when any intermediate
waypoint's depth exceeds `start_depth + epsilon` (keep-out) or `start_violation + epsilon`
(keep-in), and add a test that feeds the two chunks above expecting
`recovery_required:keep_out:excursion` / `recovery_required:keep_in:excursion`. `margins`
should carry the maximum excursion alongside the endpoint gain so the two cannot be confused.

## F6 — the manifest is unverified by default, and a manifest with no bounds builds a gate that checks nothing

**Severity: high.**
`physical_manifest.py:332` declares `verifier: Callable | None = None`; `vla_gate.py:215` forwards
it unchanged. Every in-tree call site (`benchmarks/vla_lag_and_safety_bench.py:302`,
`benchmarks/safety_gymnasium_runner.py:551`, the `gym_wrapper` docstring example at line 70) passes
no verifier. The loader's own docstring says "bounds from an unsigned manifest are not bounds",
and `docs/LOKAHI_VLA.md:15` says the section "never falls back to a default. ... There is no 'gate
with no bounds'".

Trigger:

```python
g = LokahiVLAGate.from_manifest({"physical_vla": {"max_action_age_ms": 150.0,
                                                  "watchdog_timeout_ms": 500.0}})
r = g.evaluate_step(obs_ts_ns=0, now_ts_ns=1_000_000, robot_state=STATE0,
                    action_chunk=[{"ee_pos": [50.0, 50.0, 50.0], "duration_ms": 20.0}])
```

Output:

```
  no verifier passed; built gate: workspace_aabb=None keep_out_aabbs=() max_pos_delta_m=None max_force_n=None
  chunk 70 m outside any workspace: verdict=COMPLIANT state=ARMED allowed=True violations=[]
```

**Consequence:** the two properties the document says matter most — one source of truth and fail
closed on configuration — both depend on the caller remembering a keyword argument, and a manifest
that simply omits `workspace_aabb`, `keep_out_*`, `kinematic_limits` and `physical_envelope`
produces a gate that can only ever check freshness and the watchdog. `max_gate_eval_us`,
`control_period_ms` and `tool_radius_m` are also silently defaulted
(`physical_manifest.py:292-304`), so "never falls back to a default" is false for three keys.

**Suggested check that proves the fix:** make `verifier` required (or fail when a manifest arrives
dict-shaped without one), and refuse a section that configures no geometric bound at all; tests:
(a) `from_manifest(doc)` with no verifier raises `VLAManifestError: unverified_manifest`; (b) a
manifest with no bounds at all raises `VLAManifestError: no_bounds`, and neither gate can be
constructed.

## F7 — the replanned stop is handed out unswept, and it can cross a keep-out the chunk never touched

**Severity: medium.**
`vla_gate.py:561-599` builds the profile from the last legal pose and the chunk's own velocity
(`_chunk_velocity`, `643-648`), then puts it in `safe_action["stop_profile"]`. Nothing sweeps those
waypoints against the workspace or the keep-outs; `docs/LOKAHI_VLA.md:213` asserts the safety with
a promise about the *next* call ("gated again on the next `evaluate_step`"), which is a property of
the consumer, not of this output.

Trigger (workspace `x∈[0,10]`, keep-out box `[2,0,0,3,1,1]`, `max_pos_delta_m=0.1`,
`clamp_strategy=replan`, `max_acceleration=2.0`, `max_jerk=100.0`): current `x=0`, chunk
`0.1, 0.2, 0.35` m.

Output:

```
  chunk ending 0.15 m past the delta limit: verdict=CLAMPED state=CLAMPED allowed=True violations=['kinematic:pos_delta:2:0.1500m>0.1m']
  safe_action mode=replanned_stop prefix=2 profile=127 waypoints
  waypoints inside keep-out box [2.0, 0.0, 0.0, 3.0, 1.0, 1.0]: 12
  last waypoint=[6.5, 0.5, 0.5]
```

**Consequence:** the legal prefix arrives at 5 m/s, and the accompanying "safe" deceleration drives
through a hazard box and 6.5 m from where the arm started, with `allowed=True` and no violation
recorded — a consumer that executes `safe_action` as documented (`docs:141-142`,
`controller.hold(result.safe_action)`) executes it.

**Suggested check that proves the fix:** sweep the profile with the same geometry path used for
chunks before attaching it, and if any waypoint hits a keep-out or leaves the keep-in, replace the
payload with a hold and record `replanned_stop:unsafe:{segment}`. Test: the trigger above must
come back `FORBIDDEN`/`safe_mechanical_hold`, or with a profile whose every waypoint is clear.

## F8 — a budget overrun keeps the motion payload, which contradicts the documented hold

**Severity: medium.**
`vla_gate.py:601-616` rewrites `verdict` to `FORBIDDEN` and `state` to `SOFT_STOP` on overrun, but
replaces `safe_action` only `if safe_action is None`. A clamped-and-replanned step already has one.

Trigger: same chunk as F7, with the gate's own clock simulated at 50 ms against a 20 ms budget
(`max_gate_eval_us=20000`).

Output:

```
  same chunk, gate took 50 ms (budget 20 ms): verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['kinematic:pos_delta:2:0.1500m>0.1m', 'budget_exceeded:50000us>20000us']
  safe_action mode=replanned_stop keys=['actions', 'mode', 'note', 'stop_profile'] has_hold_pose=False
```

**Consequence:** `docs/LOKAHI_VLA.md:61` says overrun yields "a stop", and the recovery contract for
SOFT_STOP is a hold at the current pose; a consumer implementing that contract instead receives a
motion profile, so the refusal state and the payload disagree exactly when the gate was too slow to
be trusted. Worth noting for sizing: on this machine a 127-waypoint replan took ~3.8 ms on its own,
against the 2 ms default in `docs:123` — the default budget cannot cover the replan path here, so
this is the common case for `clamp_strategy: replan`, not a corner.

**Suggested check that proves the fix:** on overrun, force `safe_action` to the hold form
(`mode="safe_mechanical_hold"`, `hold_pose`, `hold_joints`) whatever it was, and keep the discarded
profile under a separate key for diagnosis; test asserts `verdict=FORBIDDEN`, `state=SOFT_STOP`,
`safe_action["mode"] == "safe_mechanical_hold"` and `"stop_profile" not in safe_action`.

## F9 — a manifest can turn the absorbing E_STOP class into a per-step refusal

**Severity: medium.**
`physical_manifest.py:40-53` defaults `workspace_keep_out`/`workspace_keep_in`/`structural` to
`E_STOP`, but `233-238` lets any manifest remap any class to any action in `POLICY_ACTIONS`. When
`workspace_keep_out` is mapped to `CLAMP`, `_decide` (`684-710`) finds `clamped_index is None`
(only `stale_action` and `kinematic` contribute one) and returns `FORBIDDEN` with **`SOFT_STOP`** —
not the E_STOP the class is supposed to own.

Trigger:

```python
g = gate(policy_on_violation={"workspace_keep_out": "CLAMP"},
         keep_out_aabbs=[[0.6, 0.2, 0.0, 0.8, 0.4, 1.2]])
g.evaluate_step(..., action_chunk=chunk([[0.7, 0.3, 0.5]]))    # into the box
g.evaluate_step(..., action_chunk=chunk([[0.51, 0.5, 0.5]]))   # clean, no human_reset
```

Output:

```
  chunk driven into the keep-out box: verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['workspace_keep_out:0:segment_0', 'kinematic:pos_delta:0:0.2828m>0.05m']
  next clean chunk, no human_reset called: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  gate.state=ARMED latch_reason=None
```

**Consequence:** the interlock for a keep-out penetration becomes a one-step refusal that a
well-formed next chunk clears silently — the absorbing behaviour the document promises for contact
with a hazard is now a manifest field away from being switched off, and the manifest has no
mechanism (see F6) that must be verified before it does so.

**Suggested check that proves the fix:** clamp the manifest's freedom — permit only escalation for
`workspace_keep_in`/`workspace_keep_out`/`structural` (refuse any mapping whose action is softer
than the default), and add a test asserting `VLAManifestError` for
`{"workspace_keep_out": "CLAMP"}`.

## F10 — `SOFT_STOP → CLAMPED, allowed=True` exists and is undocumented

**Severity: medium.**
`_latch` (`vla_gate.py:238-243`) refuses to overwrite an `E_STOP` but happily moves any other state,
so a step after a soft stop that trips a kinematic clamp sets `CLAMPED` and returns `allowed=True`.
The state diagram (`docs:68-78`) has `CLAMPED` edges only from `ARMED`.

Trigger: step 1 stale (SOFT_STOP), step 2 fresh with a 0.07 m delta at index 2.

Output:

```
  step 1: stale chunk: verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['stale_action:0:10000.0ms>150.0ms']
  step 2: fresh chunk, delta 0.07 at index 2: verdict=CLAMPED state=CLAMPED allowed=True violations=['kinematic:pos_delta:2:0.0700m>0.05m']
  safe_action mode=clamped_chunk actions=2
```

**Consequence:** an auditor reasoning from the state table concludes a soft-stopped gate cannot
return to executing in one step; it can, and the first evidence is a prefix the arm never had a hold
before. If that is intended (and for a kinematic clamp it may be), the table and the `interlock`
value (`pass`) need to say so.

**Suggested check that proves the fix:** either add the edge to the table and pin it with a test
(`SOFT_STOP → CLAMPED` must still carry the previous `state` in `margins` or violations), or make
`_latch` treat `SOFT_STOP` like `E_STOP` for step-local precedence and pin that instead.

## F11 — a mesh keep-out raises out of every `evaluate_step`

**Severity: medium.**
`vla_gate.py:405` calls `geometry.inside_depth` for every shape unconditionally. For a mesh,
`geometry.py:227-237` raises `UnsupportedShape` by design ("mesh recovery depth is not defined").
So the COAL sweep that `docs:182` and `193` document as the mesh path is unreachable: the gate
dies before it.

Trigger: manifest with one mesh keep-out and a benign chunk 0.5 m clear of it.

Output:

```
  raised UnsupportedShape: a mesh keep-out needs COAL: pip install 'lokahi[vla-geometry]'
```

**Consequence:** it is fail-closed (no motion), but it is a runtime crash on the first step, from a
manifest the loader accepted, and it means a mesh keep-out is functionally unsupported in the gate
despite being documented as supported with an optional extra. The message is also wrong on this
machine — the failure is not a missing extra, it is `inside_depth` refusing mesh by design.

**Suggested check that proves the fix:** decide the semantics and pin one: either
`_parse_shape` refuses `kind: mesh` (with a test that the manifest cannot even load), or the gate
treats "already inside a mesh" by testing the start point against the mesh via COAL and refusing it
with a violation instead of raising. A test should assert `evaluate_step` never raises for any
manifest that `load_physical_vla` accepted — a fuzz over `SHAPE_KINDS` is enough.

## F12 — receipt provenance is a claim, not evidence, and the verifier accepts contradictions

**Severity: medium.**
`rollout_receipt.py:171-204` accepts `manifest_hash`, `policy_id` and `execution_mode` as strings and
never compares them to anything that exists. `224-280` accepts any object with a `verdict`
attribute, defaults `gate_eval_us` to `0.0` when the attribute is absent, and never checks that
`allowed` agrees with `verdict`. `370-386` recomputes the aggregate from those entries and adds no
vocabulary check.

Trigger: a receipt with a made-up `manifest_hash`, a stub decision object, a second stub claiming
`verdict="FORBIDDEN"` with `allowed=True`, and an invented verdict string recorded from another
stub, then `verify_receipt(document, key=key)`.

Output:

```
  verify_receipt(ok=True, status=verified); checks failed=[]
  manifest_hash in a signed, verified receipt: abababab...ab (nothing in the tree holds this digest)
  latency_percentiles.gate_eval_us: {'count': 2, 'p50': 0.0, 'p95': 0.0, 'p99': 0.0}
  step 1 says verdict=FORBIDDEN allowed=True (verifier does not cross-check)
  edit gate_eval_us inside an entry: ok=False status=tampered reason=ledger_chain: entry 0 hash does not recompute
  verdict in receipt='DEFERRED_TO_OPERATOR'
  verify_receipt: ok=True status=verified failed=[]
  verdict_counts claimed: {'CLAMPED': 0, 'COMPLIANT': 0, 'DEFERRED_TO_OPERATOR': 1, 'FORBIDDEN': 0}
```

**Consequence:** a receipt proves its entries were not edited *after signing* and that the summary
matches them; it does not prove the gate produced them, that the manifest it names ever existed, or
that the latency it reports was measured — a zero-percentile latency block is a signature over a
fabricated measurement. The document's framing ("the evidence artifact for one episode",
`rollout_receipt.py:3-24`) invites more trust than the checks support.

**Suggested check that proves the fix:** (a) reject any step whose `verdict` is not in `VERDICTS`;
(b) reject a step whose `allowed` disagrees with its verdict; (c) make `gate_eval_us` mandatory
(no `getattr` default) and refuse a receipt whose latency block includes a step with none; (d) add
a `manifest_document_hash`-style field and have `verify_receipt` accept the manifest and recompute
the digest, so the hash is checkable rather than declared; (e) print a `provenance: declared`
check in the report so `lokahi-vla-verify` says out loud what it did not verify.

## F13 — a negative `tool_radius_m` widens the keep-in region

**Severity: medium.**
`physical_manifest.py:304` validates `tool_radius_m` with `_require_finite` only; `0.06` and `-0.2`
are equally acceptable. `vla_gate.py:115-120` then shrinks the keep-in box by that radius (a
negative radius *grows* it) and `384` grows keep-out boxes by it (a negative radius can invert
them into `min > max`, which the slab test then answers at random).

Trigger: `tool_radius_m=-0.2`, workspace `[0,1]³`, `max_pos_delta_m=1.2`, setpoint at `x = 1.15`.

Output:

```
  accepted tool_radius_m=-0.2
  setpoint 0.15 m outside the declared workspace: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  same setpoint, tool_radius_m=0 (control): verdict=FORBIDDEN state=E_STOP allowed=False violations=['workspace_keep_in:vertices_outside']
```

**Consequence:** the one number the document says "still describes the tool"
(`docs:190-192`) can move the workspace boundary outward, so the effective keep-in no longer matches
the signed manifest it was read from.

**Suggested check that proves the fix:** require `tool_radius_m >= 0` (and, better, `< half the
smallest keep-in extent`); tests: negative radius raises `VLAManifestError`, and the setpoint above
is refused with either a manifest error or `workspace_keep_in:vertices_outside`.

## F14 — the rotation bound is skipped for the first setpoint when `robot_state` has no `ee_quat`

**Severity: low.**
`vla_gate.py:426` and `434-440`: `previous_quat` starts from `robot_state["ee_quat"]`, so with no
current orientation the first setpoint's rotation is never measured. Later setpoints are, because
`previous_quat` then comes from the chunk itself.

Trigger: robot_state with only `ee_pos`; one setpoint rotated 90°, `max_rot_delta_rad=0.05`.

Output:

```
  90 deg rotation in the first setpoint, robot_state has no ee_quat: verdict=COMPLIANT state=ARMED allowed=True violations=[]
  same setpoint, robot_state carries ee_quat (control): verdict=FORBIDDEN state=SOFT_STOP allowed=False violations=['kinematic:rot_delta:0:1.5708rad>0.05rad']
```

**Consequence:** the first command after any state without orientation can spin the tool arbitrarily,
which for a tool modelled as a sphere (F5 in the document's own limits list) is invisible to every
other check.

**Suggested check that proves the fix:** refuse a `robot_state` without `ee_quat` when
`max_rot_delta_rad` is configured (same shape as the `ee_pos` guard), and add this trigger as a
test expecting `structural:robot_state_missing_ee_quat`.

## F15 — `max_gate_eval_us` is unbounded and unrelated to the control period

**Severity: low.**
`physical_manifest.py:292` defaults it to `5000` and validates only positivity; a manifest can set
`1e9` and the budget check can never fire.

Output:

```
  accepted max_gate_eval_us=1e+09 us with control_period_ms=20
```

**Consequence:** the budget invariant, the last check in the ordering, is the one invariant the same
manifest can neutralise — and it is the only check that bounds how late the gate may be before its
answer is stale.

**Suggested check that proves the fix:** refuse `max_gate_eval_us > control_period_ms * 1000`
(one control period in microseconds) or warn loudly; a test over `(control_period_ms=20,
max_gate_eval_us=1e9)` expecting `VLAManifestError`.

---

## F16 — documentation that the code does not support

**Severity: low**, except two entries that would mislead a safety reader.

Stale in the **safe** direction (code is stricter than the document — will confuse an auditor into
believing a hole exists):

- `docs/LOKAHI_VLA.md:253` — "A `robot_state` without `ee_pos` disables checks 6 and 7 silently …
  returns `COMPLIANT`". The code added `structural:robot_state_missing_ee_pos`
  (`vla_gate.py:306-315`) and refuses. Verified: `verdict=FORBIDDEN … violations=['structural:robot_state_missing_ee_pos']`.
- `docs/LOKAHI_VLA.md:254` — "`max_joint_vel_rad_s` and `max_joint_acc_rad_s2` … never evaluated".
  They are evaluated (`vla_gate.py:457-494`). Verified: `kinematic:joint_vel:0:10000.000rad/s>1.0`.
- `docs/LOKAHI_VLA.md:247` — "Receipts and a `lokahi-vla-verify` CLI are **planned for v1.4.0** …
  and are not in this tree". `tools/vla/rollout_receipt.py` (629 lines, with `main()`) is in this
  tree.

Stale in the **unsafe** direction (document promises more than the code does):

- `docs/LOKAHI_VLA.md:15` — "never falls back to a default … There is no 'gate with no bounds'". Three
  keys default (`:292-304`) and a bounds-free section loads (F6). Verified: 70 m outside → COMPLIANT.
- `docs/LOKAHI_VLA.md:262` and `geometry.py:206` — "monotone-recovery rule", "monotonically leaving
  it". Not enforced (F5). Verified: 2 m excursion → COMPLIANT.
- `docs/LOKAHI_VLA.md:61` — overrun "rewrites the result to `FORBIDDEN` / `SOFT_STOP`". True for the
  verdict, false for the payload (F8). Verified: `mode=replanned_stop` under FORBIDDEN.
- `docs/LOKAHI_VLA.md:68-78` — the state diagram has no `SOFT_STOP → CLAMPED` edge (F10).
- `docs/LOKAHI_VLA.md:182, 193` — mesh keep-outs are documented as swept through COAL and as
  "a hard error naming the extra" only when COAL is absent. In fact every step raises, COAL or not
  (F11).

Claims I checked that **hold**: the 120-execution / 0-gated / 94-ungated figures and the 0% false
clamp (README `291`), the Safety-Gymnasium table and its "does not flatter us" paragraph
(`docs/BENCHMARKS.md:69-99`), the CLAMPED-recorded-as-compliant disclosure (`docs:244`), the
E_STOP-is-a-hold-by-design disclosure (`docs:102`), and `to_decision()` returning `compliant` for a
clamp (verified: `verdict='compliant'` for a `CLAMPED` step). No document in the tree quotes a
latency number.

---

## Verdict

I would not put this module between a policy and an actuator today, and the reason is specific
rather than total: the gate's *decision logic* is sound — the budget refuses rather than allows, an
E_STOP survives a slow step, clamps to index 0 are downgraded, tampered receipts are caught, and
uncheckable geometry raises instead of passing — but five of its inputs can be shaped to make a
check quietly not run while the result still says COMPLIANT, and the module's own documentation
asserts fail-closure on exactly those paths. A gate that verifies everything it is given is worth
having; a gate that cannot tell "I measured this and it is fine" from "nobody gave me this" is not,
because the failure is silent and the record will say the arm was cleared. Everything above is
reproduced against this tree, nothing here needs hardware to demonstrate, and none of it depends on
an attacker with repo access: a NaN from a sensor, an adapter that forgets one key, or a manifest
copied without its signature is enough.

The three things that must change first:

1. **Validate or refuse every input the decision depends on.** `duration_ms` finite and positive
   (F1); a missing `ee_pos`, `joint_pos`, `ee_quat` or `wrench` is a refusal whenever the manifest
   configures the bound that needs it (F2, F3, F4, F14). This alone removes every silent-skip path I
   found.
2. **Replace the end-pose recovery rule with a bounded-excursion rule, and sweep the replanned stop
   before it becomes a payload** (F5, F7, F8). The current rule approves the exact motion that makes
   a hazard worse, and the stop profile is the only unswept motion the gate emits.
3. **Stop treating the manifest as advisory:** verify it by default (F6), bound what it may weaken —
   no softening of an E_STOP class (F9), no non-negative-validated radius (F13), no unbounded gate
   budget (F15) — and make the receipt say which of its claims are declared rather than verified
   (F12). Until then "one source of truth per physical limit" describes the schema, not the trust
   boundary.
