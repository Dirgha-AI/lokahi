# Safe-Control-Gym Comparator (D-4)

**Status: not measured. The package did not install, so there are no numbers in
this document and none in the repository. Nothing here is a result.**

`HANDOVER.md` files this as defect D-4: the Safety-Gymnasium table has an
ungated arm and a gated arm, and no third column showing what a filter built for
this problem does with the same driver. safe-control-gym (utiasDSL, MIT) is the
suite the table was meant to borrow that column from — it ships MPSC, CBF and
neural-CBF safety filters as first-class objects and reports constraint
evaluations in `info` on every step, which is the quantity both shields claim to
move.

This document records what was attempted on the Windows review machine, the exact
failure, what exists in the repository anyway, and what a reader has to run to
produce the table. None of it is an argument that LOKAHI is better or worse than
a control-barrier-function shield: that claim needs a table, and the table needs
a run.

## 1. What was attempted

Both attempts used the interpreter the review machine documents for Python work
(`python` there is a Microsoft Store stub; `timeout` is Windows `timeout.exe`).

```text
py -3 -m venv D:/tmp/scg                        # Python 3.13.0, venv created
D:/tmp/scg/Scripts/python.exe -m pip install safe-control-gym
```

```text
ERROR: Could not find a version that satisfies the requirement safe-control-gym (from versions: none)
ERROR: No matching distribution found for safe-control-gym
```

There is no `safe-control-gym` distribution on PyPI — the project publishes
source only. The next attempt was therefore the project's own documented
installation route, so that the record distinguishes "no such package name" from
"this environment cannot build it":

```text
D:/tmp/scg/Scripts/python.exe -m pip install "git+https://github.com/utiasDSL/safe-control-gym.git"
```

It cloned and resolved `safe-control-gym==2.0.0` at commit
`6b5391d014f36fdfa0f9d22d92c77387e5274308`, built the package wheel
(`Successfully built safe-control-gym`), and then failed building two of its
dependencies — both C extensions, both with no wheel for CPython 3.13 on
`win_amd64`, so both were built from source against this machine's toolchain:

```text
  CloneTreeCreator.cpp
  C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Tools\MSVC\14.29.30133\include\cstring(12): fatal error C1083: Cannot open include file: 'string.h': No such file or directory
  error: Command '[ ... cl.exe ... ]' returned non-zero exit status 2.
  ERROR: Failed building wheel for pybullet
```

```text
  C:\Users\ABC\AppData\Local\Programs\Python\Python313\include\pyconfig.h(59): fatal error C1083: Cannot open include file: 'io.h': No such file or directory
  error: Command '[ ... cl.exe ... ]' returned non-zero exit status 2.
  ERROR: Failed building wheel for pycddlib
Successfully built safe-control-gym
Failed to build pybullet pycddlib
ERROR: ERROR: Failed to build installable wheels for some pyproject.toml based projects (pybullet, pycddlib)
```

The C++ compiler present (`VS 2019 Build Tools`, MSVC 14.29) has no Windows SDK
headers: `string.h` and `io.h` are Windows SDK files, not MSVC files. That is an
incomplete build toolchain on this machine, not a defect in the suite, and it is
not something a benchmark runner may paper over.

Final state of the environment: nothing installed.

```text
$ D:/tmp/scg/Scripts/python.exe -m pip list
Package Version
------- -------
pip     24.2
$ D:/tmp/scg/Scripts/python.exe -c "import safe_control_gym"
ModuleNotFoundError: No module named 'safe_control_gym'
```

Nothing was vendored, patched, stubbed or downgraded to force a run, and no
number below is estimated, transferred from another machine, or reconstructed
from the suite's published figures. The run would measure this gate on this
driver; numbers from elsewhere would measure something else.

## 2. What exists anyway

### `benchmarks/safe_control_gym_runner.py`

The comparator, written against the suite's source (read from the clone above,
not from memory), mirroring `benchmarks/safety_gymnasium_runner.py` in structure
and report format. Three arms share one driver and one environment per task:

| arm | what executes |
| --- | --- |
| `ungated` | the driver's action, unchanged |
| `cbf` | the action certified by the suite's own CBF filter through `certify_action(state, action, info)`, or a held step when its QP returns infeasible |
| `gated` | the action the LOKAHI gate allows: a refused step is replaced by a hold, and the next step is a command that undoes the last one |

The cost column is the suite's own per-step `info["constraint_violation"]`,
summed over an episode. Both shields see the same driver, the same seed, and the
same reset state, so a difference between the columns is attributable to the
shield.

The task is `cartpole` stabilisation, because that is the one task the suite
implements a CBF for (`safe_control_gym/safety_filters/cbf/cbf.py` raises
`NotImplementedError` for anything but the cartpole). Its state constraint here
is a cart position bound of ±2 m and a pole angle bound of ±0.5 rad.

**The asymmetry is stated up front in the runner and matters more than the
table:** the pole angle is a joint-space quantity, and the gate polices the swept
segment of a point in space. It has no rule for an angle. So the gate is handed
the cart's position on its rail — the part of the constraint it can express — and
the CBF is handed the state box it was built for. On this task a working CBF
therefore has a job the gate cannot do at all, and the runner's report separates
the steps charged for the cart leaving the rail from the steps charged for the
pole falling, rather than hiding one inside the other.

That asymmetry is also why the fair comparison — position constraints both
shields can see — is MPSC on the quadrotor, which this document cannot run
either, for the same reason. Cartpole was chosen for the first run because CBF
is the filter D-4 names and cartpole is where the suite has one.

### `tests/test_safe_control_gym_runner.py`

20 tests: 18 pass with no third-party package installed, 2 skip with a reason
because they need the real suite.

The 18 cover the four-value `(obs, reward, done, info)` contract and the refusal
of any other shape, the constraint-flag requirement, the rail oracle, each arm's
bookkeeping (a refusal holds rather than skipping the environment step; a CBF
correction counts as an intervention; an infeasible QP holds the step), the
report's measures and byte-identical determinism for fixed seeds, and `main()`
exiting 2 with `cannot measure:` when the suite is absent.

The 2 that skip assert that the real cartpole reports a constraint flag and that
all three arms run. They assert that the arms ran, not which shield won.

```text
$ py -3 -m pytest tests/test_safe_control_gym_runner.py -q
18 passed, 2 skipped
```

## 3. What to run to get the numbers

The blocker is a build toolchain, so either repair it or run somewhere Linux:

**A machine with a complete C toolchain** (Windows: the Visual Studio Build Tools
must include the *Windows 10/11 SDK*, not only the MSVC compiler; or any Linux
machine, which the suite targets first):

```bash
py -3 -m venv D:/tmp/scg
D:/tmp/scg/Scripts/python.exe -m pip install "git+https://github.com/utiasDSL/safe-control-gym.git"
D:/tmp/scg/Scripts/python.exe benchmarks/safe_control_gym_runner.py --seeds 0 1 2
```

```bash
D:/tmp/scg/Scripts/python.exe benchmarks/safe_control_gym_runner.py --json > scg.json
```

The runner exits 2 with `cannot measure:` and a reason rather than reporting a
zero when anything it needs is missing — the suite, the constraint flag, a
working CBF filter, or a movable cart.

The table has one block per task, three arm rows, and two delta rows:

```text
task          arm         rew/ep   cost/ep  cost steps  interv  refus  false
cartpole      ungated      ...      ...        ...          -      -      -
cartpole      cbf          ...      ...        ...        ...    ...      -
cartpole      gated        ...      ...        ...        ...    ...    ...
              cbf delta                       ...   (+/-% episodic cost)
              gate delta                      ...   (+/-% episodic cost)
```

`interv` and `refus` are per-step rates. `false` is the gate's refusals on steps
whose *predicted* cart position was still inside the rail, judged by an oracle in
the runner written from the configured bounds and independent of both shields;
it is blank for the CBF arm, because scoring a CBF's pole correction against a
positional oracle would report a working filter as an over-refusing one.

## 4. What a reader may conclude from this document

- The LOKAHI gate has not been compared against a CBF or an MPSC shield, on any
  task, at any seed. D-4 stays open.
- The comparator exists and is tested as far as a package-free machine allows.
  Its gate-side and CBF-side bookkeeping are verified against stubs; its
  suite-side adapter has never executed.
- The freeze defect the Safety-Gymnasium run found (a gate that refuses every
  illegal one-step move holds a robot inside a hazard while the environment keeps
  charging it) is not answered by this lane. A CBF answers a different question
  — it synthesises a certified input rather than refusing one — so a comparison
  would show the shape of the fix, not make it.

Nothing in this document is a safety claim, and a comparison table would not be
one either. LOKAHI is not a safety-rated control function; it is not certified to
PL d, SIL, or AgPL, and no benchmark against a simulator filter changes that.
See "What this is not" in `docs/LOKAHI_VLA.md`.

## 5. Open items

| item | state |
| --- | --- |
| safe-control-gym installable on this machine | no — build toolchain incomplete for `pybullet`, `pycddlib` |
| cartpole, three arms, seeds 0 1 2 | written, never run |
| quadrotor + MPSC, the comparison both shields can see | not written; needs the suite installed first |
| CBF column in `docs/BENCHMARKS.md` | absent, and it must not be filled without a run |
