"""Invariants of the latency and adversarial benchmark.

Nothing here asserts a duration: timings move with the machine, behaviour does
not. Every assertion is about what executed and what was refused.
"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

from benchmarks.vla_lag_and_safety_bench import (  # noqa: E402
    CASE_ORDER,
    CASES,
    EXECUTION_MODES,
    KEEP_IN_SAFE,
    KEEP_OUT_SAFE,
    MANIFEST,
    ROUTE_OBJ,
    build_report,
    run_episode,
    unsafe_reasons,
)
from vla import LokahiVLAGate  # noqa: E402
from vla.vla_gate import VERDICT_CLAMPED, VERDICT_FORBIDDEN  # noqa: E402

PROBE_TICKS = 120


@pytest.fixture(scope="module")
def benign():
    """Gated and ungated benign runs at the latencies that change behaviour."""
    runs = {}
    for mode in EXECUTION_MODES:
        for latency in (0, 250, 500):
            runs[mode, latency, "gated"] = run_episode(mode, latency, gated=True, ticks=PROBE_TICKS)
            runs[mode, latency, "ungated"] = run_episode(mode, latency, gated=False, ticks=PROBE_TICKS)
    return runs


def _case(case, mode="sync", latency_ms=0, gated=True):
    return run_episode(mode, latency_ms, gated=gated, case=case, ticks=60)


# ---------------------------------------------------------------------------
# the scenario itself
# ---------------------------------------------------------------------------

def test_manifest_loads_and_clamps_staleness():
    gate = LokahiVLAGate.from_manifest(MANIFEST)
    assert gate.config.policy_for("stale_action") == "CLAMP"
    assert gate.config.policy_for("workspace_keep_out") == "E_STOP"


def test_route_stays_clear_of_the_keep_out_column():
    for step in range(400):
        point = ROUTE_OBJ.point(step * 0.007)
        assert not (
            MANIFEST["physical_vla"]["keep_out_aabbs"][0][0] <= point[0] <= MANIFEST["physical_vla"]["keep_out_aabbs"][0][3]
            and MANIFEST["physical_vla"]["keep_out_aabbs"][0][1] <= point[1] <= MANIFEST["physical_vla"]["keep_out_aabbs"][0][4]
        )


def test_scenario_is_deterministic():
    first = run_episode("lazy_async", 100, gated=True, ticks=60)
    second = run_episode("lazy_async", 100, gated=True, ticks=60)
    for key in ("executed", "idle", "blocked", "unsafe", "executed_stale", "verdicts"):
        assert first[key] == second[key], key


# ---------------------------------------------------------------------------
# the gate's guarantees under latency
# ---------------------------------------------------------------------------

def test_benign_path_is_safe_without_latency(benign):
    ungated = benign["sync", 0, "ungated"]
    assert ungated["executed"] > PROBE_TICKS // 2
    assert ungated["unsafe"] == 0
    assert benign["sync", 0, "gated"]["unsafe"] == 0


def test_gated_never_executes_a_stale_action(benign):
    for mode in EXECUTION_MODES:
        for latency in (0, 250, 500):
            run = benign[mode, latency, "gated"]
            assert run["executed_stale"] == 0, (mode, latency)
            assert run["unsafe"] == 0, (mode, latency)


def test_false_clamp_rate_is_zero_on_benign_chunks(benign):
    for mode in EXECUTION_MODES:
        for latency in (0, 250, 500):
            run = benign[mode, latency, "gated"]
            assert run["false_clamp_rate"] <= 0.01, (mode, latency, run["false_clamp_rate"])


def test_latency_costs_the_gate_availability_not_safety(benign):
    fresh = benign["sync", 0, "gated"]
    stale = benign["sync", 500, "gated"]
    assert fresh["executed"] > 0 and fresh["blocked"] == 0
    assert stale["executed"] == 0
    assert stale["blocked"] > 0


def test_ungated_baseline_executes_stale_commands(benign):
    for mode in EXECUTION_MODES:
        assert benign[mode, 500, "ungated"]["unsafe"] == 1, mode


# ---------------------------------------------------------------------------
# adversarial cases
# ---------------------------------------------------------------------------

def test_gated_never_executes_an_unsafe_action():
    for case in CASE_ORDER:
        for mode in EXECUTION_MODES:
            for latency in (0, 250):
                run = _case(case, mode, latency)
                assert run["unsafe"] == 0, (case, mode, latency, run["unsafe_reasons"])


def test_ungated_baseline_walks_into_the_defects():
    for case in CASE_ORDER:
        assert _case(case, "sync", 0, gated=False)["unsafe"] == 1, case


def test_swept_keep_out_is_forbidden():
    """Both endpoints legal, the segment between them crosses the column."""
    elements, _ = CASES["swept_keep_out"]([0.5, 0.20, 0.5], 0.4)
    first, second = (element["ee_pos"] for element in elements)

    for point in (first, second):
        assert all(KEEP_IN_SAFE[axis] <= point[axis] <= KEEP_IN_SAFE[axis + 3] for axis in range(3))
        assert not all(KEEP_OUT_SAFE[axis] <= point[axis] <= KEEP_OUT_SAFE[axis + 3] for axis in range(3))
    assert "keep_out_sweep" in unsafe_reasons(first, elements[1], 0.0)

    run = _case("swept_keep_out", "sync", 0)
    assert run["verdicts"].get(VERDICT_FORBIDDEN)
    assert any(reason.startswith("workspace_keep_out") for reason in run["first_reasons"])
    assert run["unsafe"] == 0


def test_structural_defects_are_forbidden():
    for case in ("nan_position", "inf_quaternion", "nonunit_quaternion"):
        run = _case(case, "sync", 0)
        assert run["verdicts"].get(VERDICT_FORBIDDEN), case
        assert any(reason.startswith("structural") for reason in run["first_reasons"]), case
        assert run["unsafe"] == 0, case


def test_wrench_overload_is_forbidden():
    for case in ("force_overload", "torque_overload"):
        run = _case(case, "sync", 0)
        assert run["verdicts"].get(VERDICT_FORBIDDEN), case
        assert any(reason.startswith("wrench") for reason in run["first_reasons"]), case


def test_stale_tail_is_clamped_not_passed():
    elements, _ = CASES["stale_tail"]([0.1, 0.2, 0.5], 0.0)
    assert len(elements) > 21

    run = _case("stale_tail", "sync", 0)
    assert run["verdicts"].get(VERDICT_CLAMPED), run["verdicts"]
    assert any(reason.startswith("stale_action") for reason in run["first_reasons"])
    assert run["unsafe"] == 0


def test_teleport_is_clamped_away():
    run = _case("teleport_delta", "sync", 0)
    assert run["verdicts"].get(VERDICT_CLAMPED)
    assert any(reason.startswith("kinematic") for reason in run["first_reasons"])
    assert run["unsafe"] == 0


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def test_report_json_shape():
    report = build_report(latencies=(0,), modes=("sync",))
    encoded = json.dumps(report)
    assert json.loads(encoded)["totals"]["cases"] == len(CASE_ORDER)

    assert set(report) == {"config", "cells", "totals"}
    cell = report["cells"][0]
    assert cell["latency_ms"] == 0 and cell["execution_mode"] == "sync"
    assert set(cell["benign"]) >= {
        "actions_executed", "false_clamp_rate", "blocked_rate", "gated_unsafe", "gate_eval_us",
    }
    assert set(cell["benign"]["gate_eval_us"]) >= {"mean", "p95", "max", "samples"}
    assert set(cell["adversarial"]) == set(CASE_ORDER)
    for outcome in cell["adversarial"].values():
        assert set(outcome) >= {"ungated_unsafe", "gated_unsafe", "gated_verdicts"}
    assert report["totals"]["gated_unsafe"] == 0


def test_report_gate_timings_are_measured():
    report = build_report(latencies=(0,), modes=("sync",))
    timings = report["cells"][0]["benign"]["gate_eval_us"]
    assert timings["samples"] > 0
    assert timings["mean"] > 0
    assert timings["p95"] >= 0
