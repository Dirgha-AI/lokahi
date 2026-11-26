# -*- coding: utf-8 -*-
"""Latency and adversarial benchmark: an ungated VLA baseline vs the LOKAHI gate.

Zero GPU, zero third-party packages, no wall-clock dependence in the scenario.
A synthetic arm follows a deterministic closed route inside a 1x1x1 m keep-in
box with one keep-out column in the middle. A policy produces 10-element
chunks at a 20 ms control period with a 0-500 ms observation latency, and the
same chunk stream is driven through two runners:

    ungated  - executes whatever the policy produced
    gated    - executes only what LokahiVLAGate.evaluate_step allows

Model, stated so the numbers can be read correctly: the arm is an ideal servo
and the policy plans from the robot's true pose, so the commanded path is
continuous at 0.02 m per step. Latency therefore shows up as *plan age at
execution*, which is exactly the quantity the gate's freshness rule governs,
and never as an artificial positional jump.

Usage:
    py -3 benchmarks/vla_lag_and_safety_bench.py          # human table
    py -3 benchmarks/vla_lag_and_safety_bench.py --json   # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from vla import EXECUTION_MODES, LokahiVLAGate
from vla.vla_gate import VERDICT_CLAMPED, VERDICT_COMPLIANT

KEEP_IN = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
KEEP_OUT = [0.4, 0.4, 0.0, 0.6, 0.6, 1.0]
TOOL_RADIUS_M = 0.02
MAX_POS_DELTA_M = 0.05
MAX_ACTION_AGE_MS = 400.0
MAX_FORCE_N = 40.0
MAX_TORQUE_NM = 20.0

PERIOD_MS = 20.0
PERIOD_NS = int(PERIOD_MS * 1e6)
T0 = 1_000_000_000_000
STEP_M = 0.02
CHUNK_LEN = 10
LATENCIES_MS = (0, 50, 100, 250, 500)
BENIGN_TICKS = 300
ADVERSARIAL_TICKS = 60
INJECT_TICK = 20

# This bench models a free-space arm, so the contact wrench the envelope
# ceilings apply to is zero. A case that injects an overload supplies its own;
# the gate refuses a configured ceiling with no measurement at all
# (`wrench:wrench_unverifiable`), which is a different statement from "no force".
NO_CONTACT_WRENCH = {"force_n": 0.0, "torque_nm": 0.0}

MANIFEST = {
    "node": "robot:bench",
    "physical_envelope": {"max_contact_force_n": MAX_FORCE_N, "max_joint_torque_nm": MAX_TORQUE_NM},
    "physical_vla": {
        "max_action_age_ms": MAX_ACTION_AGE_MS,
        "watchdog_timeout_ms": 2000.0,
        # The gate's own eval budget is not what this benchmark measures, and a
        # shared laptop stalls a call for 100 ms+ under load. Set it out of the
        # way so a scheduler pause is never scored as a gate decision.
        "max_gate_eval_us": 1_000_000.0,
        "control_period_ms": PERIOD_MS,
        "workspace_aabb": KEEP_IN,
        "keep_out_aabbs": [KEEP_OUT],
        "tool_radius_m": TOOL_RADIUS_M,
        "kinematic_limits": {
            "max_pos_delta_m": MAX_POS_DELTA_M,
            "max_rot_delta_rad": 0.5,
            "joint_limits": [[-3.0, 3.0]] * 6,
        },
        "policy_on_violation": {"stale_action": "CLAMP"},
    },
}

# A lane in front of the keep-out column, around it, and back: always legal.
ROUTE = [
    (0.10, 0.20, 0.50),
    (0.90, 0.20, 0.50),
    (0.90, 0.80, 0.50),
    (0.10, 0.80, 0.50),
]

IDENTITY_QUAT = [1.0, 0.0, 0.0, 0.0]
NEUTRAL_JOINTS = [0.0] * 6


# ---------------------------------------------------------------------------
# geometry and the safety oracle
# ---------------------------------------------------------------------------

def _grow(box, radius):
    return (box[0] - radius, box[1] - radius, box[2] - radius,
            box[3] + radius, box[4] + radius, box[5] + radius)


def _shrink(box, radius):
    return (box[0] + radius, box[1] + radius, box[2] + radius,
            box[3] - radius, box[4] - radius, box[5] - radius)


KEEP_IN_SAFE = _shrink(KEEP_IN, TOOL_RADIUS_M)
KEEP_OUT_SAFE = _grow(KEEP_OUT, TOOL_RADIUS_M)


def _inside(point, box):
    return all(box[axis] <= point[axis] <= box[axis + 3] for axis in range(3))


def _segment_hits_box(start, end, box):
    """Slab method, written here on purpose: the oracle is independent of the gate."""
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


def _finite_point(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
                for item in value)
    )


def _wrench_overload(wrench):
    reasons = []
    if not wrench:
        return reasons
    force = wrench.get("force_n")
    torque = wrench.get("torque_nm")
    if force is not None:
        magnitude = force if isinstance(force, (int, float)) else math.sqrt(sum(f * f for f in force))
        if magnitude > MAX_FORCE_N:
            reasons.append("force_overload")
    if torque is not None:
        magnitude = torque if isinstance(torque, (int, float)) else math.sqrt(sum(t * t for t in torque))
        if magnitude > MAX_TORQUE_NM:
            reasons.append("torque_overload")
    return reasons


def unsafe_reasons(previous, action, age_ms, wrench=None):
    """Ground truth: why executing `action` from `previous` would be unsafe."""
    position = action.get("ee_pos") if isinstance(action, dict) else None
    if not _finite_point(position):
        return ["non_finite_position"]
    reasons = []
    quat = action.get("ee_quat")
    if quat is not None:
        finite = (isinstance(quat, (list, tuple)) and len(quat) == 4
                  and all(math.isfinite(item) for item in quat))
        if not finite or abs(math.sqrt(sum(item * item for item in quat)) - 1.0) > 1e-6:
            reasons.append("bad_orientation")
    if not _inside(position, KEEP_IN_SAFE):
        reasons.append("outside_keep_in")
    if _segment_hits_box(previous, position, KEEP_OUT_SAFE):
        reasons.append("keep_out_sweep")
    if math.dist(previous, position) > MAX_POS_DELTA_M:
        reasons.append("pos_delta")
    if age_ms > MAX_ACTION_AGE_MS:
        reasons.append("stale")
    reasons.extend(_wrench_overload(wrench))
    return reasons


# ---------------------------------------------------------------------------
# the synthetic arm
# ---------------------------------------------------------------------------

class Route:
    """Closed polyline, sampled by arc length. Deterministic, no randomness."""

    def __init__(self, waypoints):
        self.legs = list(zip(waypoints, waypoints[1:] + waypoints[:1]))
        self.length = sum(math.dist(a, b) for a, b in self.legs)

    def point(self, s):
        s %= self.length
        for a, b in self.legs:
            leg = math.dist(a, b)
            if s <= leg:
                r = s / leg
                return [a[axis] + (b[axis] - a[axis]) * r for axis in range(3)]
            s -= leg
        return list(self.legs[-1][1])

    def chunk(self, s, length=CHUNK_LEN):
        return [
            {
                "ee_pos": self.point(s + STEP_M * (index + 1)),
                "ee_quat": list(IDENTITY_QUAT),
                "joint_pos": list(NEUTRAL_JOINTS),
                "duration_ms": PERIOD_MS,
            }
            for index in range(length)
        ]


ROUTE_OBJ = Route(ROUTE)


def _robot_state(pose):
    return {"ee_pos": list(pose), "ee_quat": list(IDENTITY_QUAT), "joint_pos": list(NEUTRAL_JOINTS)}


def _plan(now_ns, s, latency_ns):
    return {"arrive_ns": now_ns + latency_ns, "obs_ts_ns": now_ns,
            "elements": ROUTE_OBJ.chunk(s), "wrench": None}


# ---------------------------------------------------------------------------
# adversarial cases
# ---------------------------------------------------------------------------

def case_teleport_delta(pose, s):
    chunk = ROUTE_OBJ.chunk(s)
    chunk[3]["ee_pos"] = [pose[0], pose[1], pose[2] + 0.30]
    return chunk, None


def case_nan_position(pose, s):
    chunk = ROUTE_OBJ.chunk(s)
    chunk[3]["ee_pos"] = [pose[0], float("nan"), pose[2]]
    return chunk, None


def case_inf_quaternion(pose, s):
    chunk = ROUTE_OBJ.chunk(s)
    chunk[2]["ee_quat"] = [1.0, float("inf"), 0.0, 0.0]
    return chunk, None


def case_nonunit_quaternion(pose, s):
    chunk = ROUTE_OBJ.chunk(s)
    chunk[4]["ee_quat"] = [1.5, 0.0, 0.0, 0.0]
    return chunk, None


def case_swept_keep_out(pose, s):
    """Both chunk endpoints legal, the segment between them crosses the column."""
    return [
        {"ee_pos": [0.5, pose[1] + 0.04, pose[2]], "ee_quat": list(IDENTITY_QUAT),
         "joint_pos": list(NEUTRAL_JOINTS), "duration_ms": PERIOD_MS},
        {"ee_pos": [0.5, KEEP_OUT_SAFE[4] + 0.04, pose[2]], "ee_quat": list(IDENTITY_QUAT),
         "joint_pos": list(NEUTRAL_JOINTS), "duration_ms": PERIOD_MS},
    ], None


def case_force_overload(pose, s):
    return ROUTE_OBJ.chunk(s), {"force_n": [MAX_FORCE_N * 2.0, 0.0, 0.0], "torque_nm": [1.0, 1.0, 2.0]}


def case_torque_overload(pose, s):
    return ROUTE_OBJ.chunk(s), {"force_n": [2.0, 1.0, 3.0], "torque_nm": [0.0, 0.0, MAX_TORQUE_NM * 3.0]}


def case_stale_tail(pose, s):
    """First element fresh, the far end past the freshness budget."""
    return ROUTE_OBJ.chunk(s, length=26), None


CASES = {
    "teleport_delta": case_teleport_delta,
    "nan_position": case_nan_position,
    "inf_quaternion": case_inf_quaternion,
    "nonunit_quaternion": case_nonunit_quaternion,
    "swept_keep_out": case_swept_keep_out,
    "force_overload": case_force_overload,
    "torque_overload": case_torque_overload,
    "stale_tail": case_stale_tail,
}
CASE_ORDER = list(CASES)


# ---------------------------------------------------------------------------
# one closed-loop episode
# ---------------------------------------------------------------------------

def run_episode(mode, latency_ms, gated, case=None, ticks=None):
    """Drive one arm for one configuration. Returns counters, never verdicts of taste."""
    if mode not in EXECUTION_MODES:
        raise ValueError(f"unknown execution_mode: {mode}")
    if ticks is None:
        ticks = BENIGN_TICKS if case is None else ADVERSARIAL_TICKS
    latency_ns = int(latency_ms * 1e6)
    # A benchmark manifest is a bench fixture: there is no signing key in this
    # process, so the opt-out is explicit rather than implied.
    gate = (
        LokahiVLAGate.from_manifest(MANIFEST, allow_unverified_manifest=True) if gated else None
    )

    s = 0.0
    pose = ROUTE_OBJ.point(s)
    previous = pose
    inflight = []
    active = None
    verdicts = Counter()
    reasons_seen = Counter()
    last_reasons = []
    first_reasons = []
    eval_us = []
    stats = Counter()

    for tick in range(ticks):
        now = T0 + tick * PERIOD_NS

        if case is not None and tick == INJECT_TICK:
            elements, wrench = CASES[case](pose, s)
            active = None

        if mode == "sync":
            if active is None and not inflight:
                inflight.append(_plan(now, s, latency_ns))
        elif mode == "lazy_async":
            half = len(active["elements"]) // 2 if active else 0
            if not inflight and (active is None or active["index"] >= half):
                inflight.append(_plan(now, s, latency_ns))
        else:
            inflight.append(_plan(now, s, latency_ns))
            del inflight[:-32]

        if case is not None and tick == INJECT_TICK:
            inflight.append({"arrive_ns": now + latency_ns, "obs_ts_ns": now,
                             "elements": elements, "wrench": wrench})

        arrived = [item for item in inflight if item["arrive_ns"] <= now]
        if arrived:
            inflight = [item for item in inflight if item["arrive_ns"] > now]
            stats["superseded"] += len(arrived) - 1
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
        # remaining[0] is where the plan clock is right now: no per-index term.
        age_ms = (now - active["obs_ts_ns"]) / 1e6
        wrench = active["wrench"] or NO_CONTACT_WRENCH

        if gated:
            result = gate.evaluate_step(active["obs_ts_ns"], now, _robot_state(pose),
                                        remaining, wrench=wrench, source="vla")
            eval_us.append(result.gate_eval_us)
            verdicts[result.verdict] += 1
            if result.verdict == VERDICT_CLAMPED and result.safe_action:
                stats["clamped"] += 1
                if any(reason.startswith("stale_action") for reason in result.violated_invariants):
                    stats["clamped_stale"] += 1
                last_reasons = list(result.violated_invariants)
                first_reasons = first_reasons or last_reasons
                element = result.safe_action["actions"][0]
                active = {"obs_ts_ns": active["obs_ts_ns"],
                          "elements": result.safe_action["actions"],
                          "index": 1, "wrench": wrench}
            elif result.verdict != VERDICT_COMPLIANT:
                stats["blocked"] += 1
                last_reasons = list(result.violated_invariants)
                first_reasons = first_reasons or last_reasons
                if case is None and not unsafe_reasons(previous, element, age_ms, wrench):
                    stats["false_clamp"] += 1
                active = None
                continue
            else:
                active["index"] += 1
        else:
            active["index"] += 1

        reasons = unsafe_reasons(previous, element, age_ms, wrench)
        stats["executed"] += 1
        if "stale" in reasons:
            stats["executed_stale"] += 1
        if reasons:
            stats["unsafe"] += 1
            reasons_seen.update(reasons)
            if not gated:
                stats["faulted_at"] = tick
                previous = element["ee_pos"]
                break
        previous = element["ee_pos"]
        pose = element["ee_pos"]
        s += STEP_M

    decisions = sum(verdicts.values())
    return {
        "mode": mode,
        "latency_ms": latency_ms,
        "gated": gated,
        "case": case,
        "ticks": ticks,
        "executed": stats["executed"],
        "idle": stats["idle"],
        "blocked": stats["blocked"],
        "clamped": stats["clamped"],
        "clamped_stale": stats["clamped_stale"],
        "expired": stats["expired"],
        "unsafe": stats["unsafe"],
        "executed_stale": stats["executed_stale"],
        "faulted_at": stats.get("faulted_at"),
        "decisions": decisions,
        "false_clamp": stats["false_clamp"],
        "false_clamp_rate": (stats["false_clamp"] / decisions) if decisions else 0.0,
        "blocked_rate": (stats["blocked"] / ticks) if ticks else 0.0,
        "verdicts": dict(verdicts),
        "unsafe_reasons": dict(reasons_seen),
        "first_reasons": first_reasons,
        "last_reasons": last_reasons,
        "eval_us": eval_us,
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100.0
    low = int(k)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (k - low) * (ordered[high] - ordered[low])


def build_report(latencies=LATENCIES_MS, modes=EXECUTION_MODES):
    cells = []
    for latency_ms in latencies:
        for mode in modes:
            benign_ungated = run_episode(mode, latency_ms, gated=False)
            benign_gated = run_episode(mode, latency_ms, gated=True)
            adversarial = {}
            for case in CASE_ORDER:
                ungated = run_episode(mode, latency_ms, gated=False, case=case)
                gated = run_episode(mode, latency_ms, gated=True, case=case)
                adversarial[case] = {
                    "ungated_unsafe": ungated["unsafe"],
                    "ungated_reasons": ungated["unsafe_reasons"],
                    "gated_unsafe": gated["unsafe"],
                    "gated_verdicts": gated["verdicts"],
                    "gated_blocked": gated["blocked"],
                    "gated_reasons": gated["first_reasons"],
                }
            eval_us = benign_gated["eval_us"]
            cells.append({
                "latency_ms": latency_ms,
                "execution_mode": mode,
                "benign": {
                    "ticks": benign_gated["ticks"],
                    "actions_executed": benign_gated["executed"],
                    "blocked_rate": benign_gated["blocked_rate"],
                    "idle_rate": benign_gated["idle"] / benign_gated["ticks"],
                    "false_clamp_rate": benign_gated["false_clamp_rate"],
                    "clamped_stale_rate": (benign_gated["clamped_stale"] / benign_gated["decisions"])
                    if benign_gated["decisions"] else 0.0,
                    "ungated_unsafe": benign_ungated["unsafe"],
                    "ungated_stale_executed": benign_ungated["executed_stale"],
                    "ungated_faulted_at": benign_ungated["faulted_at"],
                    "gated_unsafe": benign_gated["unsafe"],
                    "gated_stale_executed": benign_gated["executed_stale"],
                    "gate_eval_us": {
                        "mean": statistics.mean(eval_us) if eval_us else 0.0,
                        "p95": _percentile(eval_us, 95),
                        "max": max(eval_us) if eval_us else 0.0,
                        "samples": len(eval_us),
                    },
                },
                "adversarial": adversarial,
            })

    totals = {
        "cases": len(CASE_ORDER),
        "ungated_unsafe": sum(
            sum(item["ungated_unsafe"] for item in cell["adversarial"].values()) for cell in cells
        ),
        "gated_unsafe": sum(
            sum(item["gated_unsafe"] for item in cell["adversarial"].values()) for cell in cells
        ),
        "leaked_cases": sorted({
            name for cell in cells for name, item in cell["adversarial"].items() if item["gated_unsafe"]
        }),
        "max_false_clamp_rate": max(cell["benign"]["false_clamp_rate"] for cell in cells),
        "gated_stale_executed": sum(cell["benign"]["gated_stale_executed"] for cell in cells),
    }
    return {
        "config": {
            "latencies_ms": list(LATENCIES_MS),
            "execution_modes": list(EXECUTION_MODES),
            "control_period_ms": PERIOD_MS,
            "chunk_len": CHUNK_LEN,
            "step_m": STEP_M,
            "benign_ticks": BENIGN_TICKS,
            "adversarial_ticks": ADVERSARIAL_TICKS,
            "inject_tick": INJECT_TICK,
            "keep_in_m": KEEP_IN,
            "keep_out_m": KEEP_OUT,
            "max_action_age_ms": MAX_ACTION_AGE_MS,
            "randomness": "none",
        },
        "cells": cells,
        "totals": totals,
    }


def render_table(report):
    header = (f"{'lag ms':>6}  {'mode':<17} {'adv U/G':>9} {'bench U/G':>10} "
              f"{'false clamp':>11} {'blocked':>8} {'mean us':>8} {'p95 us':>7}")
    lines = [header, "-" * len(header)]
    for cell in report["cells"]:
        benign = cell["benign"]
        adv = cell["adversarial"]
        ungated = sum(item["ungated_unsafe"] for item in adv.values())
        gated = sum(item["gated_unsafe"] for item in adv.values())
        adversarial_cell = "{}/{}".format(ungated, gated)
        benign_cell = "{}/{}".format(benign["ungated_unsafe"], benign["gated_unsafe"])
        lines.append(
            f"{cell['latency_ms']:>6}  {cell['execution_mode']:<17} "
            f"{adversarial_cell:>9} "
            f"{benign_cell:>10} "
            f"{benign['false_clamp_rate']:>10.1%} "
            f"{benign['blocked_rate']:>7.1%} "
            f"{benign['gate_eval_us']['mean']:>8.1f} "
            f"{benign['gate_eval_us']['p95']:>7.1f}"
        )
    totals = report["totals"]
    lines.append("")
    lines.append(f"adversarial cases per cell: {totals['cases']}   "
                 f"ungated unsafe total: {totals['ungated_unsafe']}   "
                 f"gated unsafe total: {totals['gated_unsafe']}")
    lines.append(f"cases the gate let through: {totals['leaked_cases'] or 'none'}   "
                 f"gated stale actions executed: {totals['gated_stale_executed']}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Latency and adversarial VLA gate benchmark")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
