# -*- coding: utf-8 -*-
"""The LOKAHI VLA gate against the shields a user would actually weigh it against.

The corpus is not redefined here. Every arm is driven through the route, the
latency sweep, the execution modes and the eight adversarial cases produced by
`benchmarks/vla_lag_and_safety_bench.py`, which is imported and used as the
generator. What changes between arms is only the thing that stands between the
policy's chunk and the actuator:

    ungated               - the raw policy; nothing between chunk and actuator
    clamp_only            - a fixed displacement delta and a fixed plan age
    swept_geometry_only   - clamp_only plus a swept keep-out test
    lokahi                - LokahiVLAGate, the nine-check gate

Units, held identical for every arm:

    adversarial executions reaching the actuator
        cells of the 120-cell adversarial sweep (3 execution modes x 5
        latencies x 8 cases) in which at least one action the ground-truth
        oracle calls unsafe was executed. 120 is a cell count, not an action
        count; `unsafe_executions` reports the action count beside it.
    over-refusals on benign chunks
        decisions in the benign sweep that hard-refused a step the oracle
        allows. `safe_steps_modified` counts clamps and rewrites of an allowed
        step, so a rate limiter cannot hide behind the narrower column.
    decisions per second
        wall clock around the arm's decision call only, on one device. These
        are this-device numbers under a machine that stalls, not a latency
        claim, and they are not comparable across devices.

Usage:
    py -3 benchmarks/alternatives_bench.py          # human table
    py -3 benchmarks/alternatives_bench.py --json   # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

from benchmarks import vla_lag_and_safety_bench as bench  # noqa: E402
from vla import LokahiVLAGate  # noqa: E402
from vla.vla_gate import VERDICT_CLAMPED, VERDICT_COMPLIANT  # noqa: E402

PERIOD_NS = bench.PERIOD_NS
MAX_POS_DELTA_M = bench.MAX_POS_DELTA_M
MAX_ACTION_AGE_MS = bench.MAX_ACTION_AGE_MS
TOOL_RADIUS_M = bench.TOOL_RADIUS_M
KEEP_IN = bench.KEEP_IN
KEEP_OUT = bench.KEEP_OUT


# ---------------------------------------------------------------------------
# what a shield can guarantee, one key per property a reviewer would ask about
# ---------------------------------------------------------------------------

GUARANTEES = (
    ("structural_finite", "rejects NaN/Inf and malformed positions"),
    ("orientation_unit", "rejects a non-unit quaternion"),
    ("freshness", "ages the step being executed, against max_action_age_ms"),
    ("keep_in", "tests the whole swept path against the workspace box"),
    ("keep_out_swept", "tests every swept segment, not only its endpoints"),
    ("displacement", "bounds per-step displacement"),
    ("wrench", "refuses force/torque beyond the physical envelope"),
    ("watchdog", "refuses a step after a stalled pipeline resumes"),
    ("latch", "a fault persists until a named human re-arms"),
    ("budget", "refuses when its own evaluation overruns"),
    ("receipt", "leaves a per-episode record that can be verified afterwards"),
)
GUARANTEE_KEYS = tuple(key for key, _ in GUARANTEES)


@dataclass(frozen=True)
class Verdict:
    """What an arm decided about the step that is about to execute."""

    kind: str                       # "pass" | "clamped" | "refused"
    execute: tuple | None           # actions to stream from this step on
    reasons: tuple = ()


def _no_guarantees(**overrides) -> dict[str, bool]:
    declared = dict.fromkeys(GUARANTEE_KEYS, False)
    declared.update(overrides)
    return declared


class Shield:
    """One arm. `decide` receives exactly what the gate receives, nothing else."""

    name = ""
    guarantees: Mapping[str, bool] = {}

    @property
    def lacks(self) -> tuple[str, ...]:
        return tuple(key for key in GUARANTEE_KEYS if not self.guarantees.get(key))

    def decide(self, obs_ts_ns, now_ts_ns, robot_state, chunk, wrench=None) -> Verdict:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# arms 1-3: what an integrator writes when they write it themselves
# ---------------------------------------------------------------------------

class Ungated(Shield):
    """The raw policy. Not a shield; the baseline that names the exposure."""

    name = "ungated"
    guarantees = _no_guarantees()

    def decide(self, obs_ts_ns, now_ts_ns, robot_state, chunk, wrench=None) -> Verdict:
        return Verdict("pass", (chunk[0],))


def _step_toward(pose, target, limit):
    """The step the rate limiter actually issues: at most `limit` metres along the chord."""
    delta = math.dist(pose, target)
    if not delta:
        return list(pose)
    # A step issued at exactly the limit lands one ulp above it once the square
    # roots are summed again, and a strict `>` limit test then reads it as a
    # violation. Keep a 1e-9 relative margin (sub-picometre here) so the emitted
    # step satisfies the bound it claims.
    scale = limit * (1.0 - 1e-9) / delta
    return [pose[axis] + (target[axis] - pose[axis]) * scale for axis in range(3)]


class ClampOnly(Shield):
    """A fixed displacement delta plus a fixed plan age. The common in-house filter.

    No latch, no watchdog, no sweep, no structural check, no wrench, no receipt.
    Written the way it is usually written: a duration filter on the plan and a
    rate limit on the step. IEEE-754 does the rest -- a NaN displacement is
    never greater than the delta, so a NaN step passes.
    """

    name = "clamp_only"
    guarantees = _no_guarantees(freshness=True, displacement=True)

    def decide(self, obs_ts_ns, now_ts_ns, robot_state, chunk, wrench=None) -> Verdict:
        age_ms = (now_ts_ns - obs_ts_ns) / 1e6
        if age_ms > MAX_ACTION_AGE_MS:
            return Verdict("refused", None, (f"plan_age:{age_ms:.1f}ms",))
        head = chunk[0]
        pose = robot_state["ee_pos"]
        delta = math.dist(pose, head["ee_pos"])
        if delta > MAX_POS_DELTA_M:
            clamped = dict(head)
            clamped["ee_pos"] = _step_toward(pose, head["ee_pos"], MAX_POS_DELTA_M)
            return Verdict("clamped", (clamped,), (f"rate_clamped:{delta:.4f}m",))
        return Verdict("pass", (head,))


def _grow_box(box, radius):
    return (box[0] - radius, box[1] - radius, box[2] - radius,
            box[3] + radius, box[4] + radius, box[5] + radius)


def _segment_hits_box(start, end, box) -> bool:
    """Slab method over the closed segment. Written here, not taken from the gate."""
    t_enter, t_exit = 0.0, 1.0
    for axis in range(3):
        origin, delta = start[axis], end[axis] - start[axis]
        low, high = box[axis], box[axis + 3]
        if abs(delta) < 1e-12:
            if origin < low or origin > high:
                return False
            continue
        first, second = (low - origin) / delta, (high - origin) / delta
        if first > second:
            first, second = second, first
        t_enter, t_exit = max(t_enter, first), min(t_exit, second)
        if t_enter > t_exit:
            return False
    return True


class SweptGeometryOnly(ClampOnly):
    """ClampOnly plus a swept keep-out test. Still no latch, watchdog or budget."""

    name = "swept_geometry_only"
    guarantees = _no_guarantees(freshness=True, displacement=True, keep_out_swept=True)

    def decide(self, obs_ts_ns, now_ts_ns, robot_state, chunk, wrench=None) -> Verdict:
        obstacle = _grow_box(KEEP_OUT, TOOL_RADIUS_M)
        points = [robot_state["ee_pos"]] + [element["ee_pos"] for element in chunk]
        for start, end in zip(points, points[1:]):
            if _segment_hits_box(start, end, obstacle):
                return Verdict("refused", None, ("keep_out_sweep",))
        return super().decide(obs_ts_ns, now_ts_ns, robot_state, chunk, wrench)


# ---------------------------------------------------------------------------
# arm 4: the gate
# ---------------------------------------------------------------------------

class Lokahi(Shield):
    """LokahiVLAGate behind the same `decide` seam as the alternatives."""

    name = "lokahi"
    guarantees = _no_guarantees(**dict.fromkeys(GUARANTEE_KEYS, True))

    def __init__(self) -> None:
        # The bench's own manifest, loaded where no signing key exists: the
        # opt-out is explicit rather than implied by a missing argument.
        self.gate = LokahiVLAGate.from_manifest(
            bench.MANIFEST, allow_unverified_manifest=True
        )

    def decide(self, obs_ts_ns, now_ts_ns, robot_state, chunk, wrench=None) -> Verdict:
        result = self.gate.evaluate_step(
            obs_ts_ns, now_ts_ns, robot_state, chunk,
            wrench=bench.NO_CONTACT_WRENCH if wrench is None else wrench,
            source="vla",
        )
        if result.verdict == VERDICT_COMPLIANT:
            return Verdict("pass", (chunk[0],))
        if result.verdict == VERDICT_CLAMPED and result.safe_action:
            return Verdict(
                "clamped", tuple(result.safe_action["actions"]),
                tuple(result.violated_invariants),
            )
        return Verdict("refused", None, tuple(result.violated_invariants))


# One instance per episode: an arm's latch must not leak between episodes.
ARMS = {
    Ungated.name: Ungated,
    ClampOnly.name: ClampOnly,
    SweptGeometryOnly.name: SweptGeometryOnly,
    Lokahi.name: Lokahi,
}
ARM_ORDER = list(ARMS)


# ---------------------------------------------------------------------------
# one closed-loop episode
# ---------------------------------------------------------------------------

def run_episode(arm: Shield, mode: str, latency_ms: float, case=None, ticks=None) -> dict:
    """Drive one arm for one cell of the corpus.

    The plan/in-flight/arrival bookkeeping is the benchmark's own contract and is
    reproduced here unchanged so every arm is scored on the same schedule. The
    arm sees `(obs_ts_ns, now_ns, robot_state, remaining_chunk, wrench)`.
    """
    if mode not in bench.EXECUTION_MODES:
        raise ValueError(f"unknown execution_mode: {mode}")
    if ticks is None:
        ticks = bench.BENIGN_TICKS if case is None else bench.ADVERSARIAL_TICKS
    latency_ns = int(latency_ms * 1e6)

    s = 0.0
    pose = bench.ROUTE_OBJ.point(s)
    previous = list(pose)
    inflight: list[dict[str, Any]] = []
    active = None
    stats = Counter()
    verdicts = Counter()
    reasons_seen = Counter()
    eval_ns: list[int] = []

    for tick in range(ticks):
        now = bench.T0 + tick * PERIOD_NS

        if case is not None and tick == bench.INJECT_TICK:
            elements, wrench = bench.CASES[case](pose, s)
            active = None

        if mode == "sync":
            if active is None and not inflight:
                inflight.append(bench._plan(now, s, latency_ns))
        elif mode == "lazy_async":
            half = len(active["elements"]) // 2 if active else 0
            if not inflight and (active is None or active["index"] >= half):
                inflight.append(bench._plan(now, s, latency_ns))
        else:
            inflight.append(bench._plan(now, s, latency_ns))
            del inflight[:-32]

        if case is not None and tick == bench.INJECT_TICK:
            inflight.append({"arrive_ns": now + latency_ns, "obs_ts_ns": now,
                             "elements": elements, "wrench": wrench})

        arrived = [item for item in inflight if item["arrive_ns"] <= now]
        if arrived:
            inflight = [item for item in inflight if item["arrive_ns"] > now]
            newest = arrived[-1]
            active = {"obs_ts_ns": newest["obs_ts_ns"], "elements": newest["elements"],
                      "index": 0, "wrench": newest["wrench"]}

        if mode == "continuous_async" and active is not None and stats["executed"]:
            due = int((now - active["obs_ts_ns"]) // PERIOD_NS)
            if due >= len(active["elements"]):
                stats["expired"] += 1
                active = None

        if active is None or active["index"] >= len(active["elements"]):
            stats["idle"] += 1
            active = None
            continue

        element = active["elements"][active["index"]]
        remaining = active["elements"][active["index"]:]
        age_ms = (now - active["obs_ts_ns"]) / 1e6
        wrench = active["wrench"]

        started = time.perf_counter_ns()
        decision = arm.decide(active["obs_ts_ns"], now, bench._robot_state(pose), remaining, wrench)
        eval_ns.append(time.perf_counter_ns() - started)
        verdicts[decision.kind] += 1

        step_was_safe = not bench.unsafe_reasons(previous, element, age_ms, wrench)

        if decision.execute is None:
            stats["refused"] += 1
            if case is None and step_was_safe:
                stats["over_refusal"] += 1
            active = None
            continue

        if decision.kind == "clamped":
            issued = decision.execute[0]
            same_step = (issued.get("ee_pos") == element.get("ee_pos")
                         and issued.get("ee_quat") == element.get("ee_quat"))
            if same_step:
                # The arm kept the step it was given and shortened the stream.
                stats["truncated"] += 1
            else:
                stats["modified"] += 1
                if case is None and step_was_safe:
                    stats["safe_step_modified"] += 1
            active = {"obs_ts_ns": active["obs_ts_ns"], "elements": list(decision.execute),
                      "index": 1, "wrench": wrench}
        else:
            active["index"] += 1

        element = decision.execute[0]
        reasons = bench.unsafe_reasons(previous, element, age_ms, wrench)
        stats["executed"] += 1
        if reasons:
            stats["unsafe"] += 1
            reasons_seen.update(reasons)
        previous = element["ee_pos"] if element.get("ee_pos") is not None else previous
        pose = element["ee_pos"]
        s += bench.STEP_M

    return {
        "arm": arm.name,
        "mode": mode,
        "latency_ms": latency_ms,
        "case": case,
        "ticks": ticks,
        "executed": stats["executed"],
        "refused": stats["refused"],
        "modified": stats["modified"],
        "truncated": stats["truncated"],
        "idle": stats["idle"],
        "expired": stats["expired"],
        "unsafe": stats["unsafe"],
        "over_refusal": stats["over_refusal"],
        "safe_step_modified": stats["safe_step_modified"],
        "decisions": sum(verdicts.values()),
        "verdicts": dict(verdicts),
        "unsafe_reasons": dict(reasons_seen),
        "eval_ns": eval_ns,
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def build_report(latencies=bench.LATENCIES_MS, modes=bench.EXECUTION_MODES,
                 cases=bench.CASE_ORDER, arms=ARM_ORDER, cross_check=True):
    benign_cells = []
    adversarial_cells = []
    for arm_name in arms:
        for latency_ms in latencies:
            for mode in modes:
                benign_cells.append(run_episode(ARMS[arm_name](), mode, latency_ms))
                for case in cases:
                    adversarial_cells.append(
                        run_episode(ARMS[arm_name](), mode, latency_ms, case=case)
                    )

    report_arms = []
    for arm_name in arms:
        benign = [cell for cell in benign_cells
                  if cell["arm"] == arm_name and cell["latency_ms"] in latencies]
        adversarial = [cell for cell in adversarial_cells if cell["arm"] == arm_name]
        decisions = sum(cell["decisions"] for cell in benign + adversarial)
        eval_ns = [value for cell in benign + adversarial for value in cell["eval_ns"]]
        elapsed_s = sum(eval_ns) / 1e9
        by_case = {}
        for case in cases:
            cells = [cell for cell in adversarial if cell["case"] == case]
            by_case[case] = {
                "cells": len(cells),
                "leaked_cells": sum(1 for cell in cells if cell["unsafe"]),
                "unsafe_executions": sum(cell["unsafe"] for cell in cells),
            }
        report_arms.append({
            "arm": arm_name,
            "lacks": list(_instance(arm_name).lacks),
            "guarantees": dict(_instance(arm_name).guarantees),
            "adversarial": {
                "cells": len(adversarial),
                "leaked_cells": sum(1 for cell in adversarial if cell["unsafe"]),
                "unsafe_executions": sum(cell["unsafe"] for cell in adversarial),
                "by_case": by_case,
            },
            "benign": {
                "cells": len(benign),
                "decisions": sum(cell["decisions"] for cell in benign),
                "over_refusals": sum(cell["over_refusal"] for cell in benign),
                "safe_steps_modified": sum(cell["safe_step_modified"] for cell in benign),
                "truncated_tails": sum(cell["truncated"] for cell in benign),
                "unsafe_executions": sum(cell["unsafe"] for cell in benign),
                "refusals": sum(cell["refused"] for cell in benign),
                "cells_with_any_intervention": sum(
                    1 for cell in benign
                    if cell["refused"] or cell["modified"] or cell["truncated"]
                ),
            },
            "throughput": {
                "decisions": decisions,
                "elapsed_s": elapsed_s,
                "decisions_per_second": (decisions / elapsed_s) if elapsed_s else 0.0,
                "mean_us": statistics.mean(eval_ns) / 1000.0 if eval_ns else 0.0,
                "median_us": statistics.median(eval_ns) / 1000.0 if eval_ns else 0.0,
                "p95_us": bench._percentile(eval_ns, 95) / 1000.0,
                "max_us": max(eval_ns) / 1000.0 if eval_ns else 0.0,
            },
        })

    report = {
        "config": {
            "corpus": "benchmarks/vla_lag_and_safety_bench.py (imported, not copied)",
            "latencies_ms": list(latencies),
            "execution_modes": list(modes),
            "cases": list(cases),
            "arms": list(arms),
            "adversarial_cells": len(latencies) * len(modes) * len(cases),
            "benign_cells": len(latencies) * len(modes),
            "max_pos_delta_m": MAX_POS_DELTA_M,
            "max_action_age_ms": MAX_ACTION_AGE_MS,
            "keep_out_m": KEEP_OUT,
            "tool_radius_m": TOOL_RADIUS_M,
            "randomness": "none",
        },
        "arms": report_arms,
    }

    if cross_check:
        report["cross_check"] = _cross_check(arms, latencies, modes, cases)
    return report


def _instance(arm_name):
    return ARMS[arm_name]()


def _cross_check(arms, latencies, modes, cases):
    """Prove the two arms that exist twice agree with the benchmark's own runner.

    `ungated` and `lokahi` are also expressible as `gated=False` / `gated=True`
    in the source benchmark. If the seam here changed the schedule, those two
    arms would stop reproducing the corpus's published counts, so compare the
    per-cell leak sets rather than trusting them.
    """
    checked = {}
    for arm_name, gated in (("ungated", False), ("lokahi", True)):
        if arm_name not in arms:
            continue
        ours, theirs = [], []
        for latency_ms in latencies:
            for mode in modes:
                for case in cases:
                    cell = run_episode(ARMS[arm_name](), mode, latency_ms, case=case)
                    ours.append(bool(cell["unsafe"]))
                    reference = bench.run_episode(mode, latency_ms, gated=gated, case=case)
                    theirs.append(bool(reference["unsafe"]))
        checked[arm_name] = {
            "cells": len(ours),
            "reference_leaked_cells": sum(theirs),
            "matches_reference": ours == theirs,
        }
    return checked


def render_table(report):
    header = (f"{'arm':<20} {'adv leaked':>12} {'adv unsafe':>11} "
              f"{'over-ref':>9} {'safe step':>9} {'dec/s':>9} {'mean us':>8}  lacks")
    lines = [header, "-" * len(header)]
    for arm in report["arms"]:
        adv = arm["adversarial"]
        benign = arm["benign"]
        throughput = arm["throughput"]
        lacks = ",".join(key for key in arm["lacks"]) or "none"
        lines.append(
            f"{arm['arm']:<20} "
            f"{adv['leaked_cells']:>5}/{adv['cells']:<6} "
            f"{adv['unsafe_executions']:>11} "
            f"{benign['over_refusals']:>9} "
            f"{benign['safe_steps_modified']:>9} "
            f"{throughput['decisions_per_second']:>9.0f} "
            f"{throughput['mean_us']:>8.1f}  {lacks}"
        )
    config = report["config"]
    lines.append("")
    lines.append(f"corpus: {config['adversarial_cells']} adversarial cells "
                 f"({len(config['execution_modes'])} modes x {len(config['latencies_ms'])} latencies "
                 f"x {len(config['cases'])} cases), {config['benign_cells']} benign cells, "
                 f"no randomness")
    lines.append("dec/s and mean us are this device's wall clock, not a latency claim")
    for arm in report["arms"]:
        throughput = arm["throughput"]
        lines.append(f"  {arm['arm']:<20} median {throughput['median_us']:>7.1f} us   "
                     f"p95 {throughput['p95_us']:>8.1f} us   max {throughput['max_us']:>9.1f} us   "
                     f"{throughput['decisions']} decisions")
        leaked = [name for name, item in arm["adversarial"]["by_case"].items() if item["leaked_cells"]]
        lines.append(f"  {'':<20} cases that reached the actuator: {', '.join(leaked) or 'none'}")
    check = report.get("cross_check") or {}
    for arm_name, item in check.items():
        lines.append(f"cross-check {arm_name}: {item['reference_leaked_cells']}/{item['cells']} leaked "
                     f"in the source benchmark, matches={item['matches_reference']}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Alternative shields on the adversarial corpus")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--no-cross-check", action="store_true",
                        help="skip re-running the corpus through the source benchmark")
    args = parser.parse_args(argv)
    report = build_report(cross_check=not args.no_cross_check)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
