"""What each alternative arm is, and is not.

Nothing here asserts a duration or reproduces a table cell: it asserts the shape
of every arm, so a change to an arm's checks fails here before it can move a
number in docs/ALTERNATIVES.md. The two arms that also exist in the source
benchmark are additionally checked against it, which is what keeps this driver
on the same corpus.
"""
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

from benchmarks import vla_lag_and_safety_bench as bench  # noqa: E402
import benchmarks.alternatives_bench as alt  # noqa: E402

NOW = bench.T0 + 100 * bench.PERIOD_NS


def _chunk(points, quat=None):
    return [
        {"ee_pos": list(point), "ee_quat": list(quat or bench.IDENTITY_QUAT),
         "joint_pos": [0.0] * 6, "duration_ms": bench.PERIOD_MS}
        for point in points
    ]


def _route_at(x=0.20, y=0.20, z=0.50):
    return [x, y, z]


def _decide(arm, chunk, pose=None, now=NOW, obs=None, wrench=None):
    pose = pose or _route_at(0.20)
    return arm.decide(obs if obs is not None else now, now,
                      bench._robot_state(pose), chunk, wrench)


# ---------------------------------------------------------------------------
# the four arms
# ---------------------------------------------------------------------------

def test_the_table_has_the_four_arms_in_order():
    assert alt.ARM_ORDER == ["ungated", "clamp_only", "swept_geometry_only", "lokahi"]
    assert set(alt.ARMS) == set(alt.ARM_ORDER)


def test_ungated_is_the_policy_and_declares_nothing():
    arm = alt.Ungated()
    assert arm.lacks == alt.GUARANTEE_KEYS
    chunk = _chunk([_route_at(0.22)])
    verdict = _decide(arm, chunk)
    assert verdict.kind == "pass"
    assert verdict.execute[0] is chunk[0]


def test_clamp_only_declares_exactly_rate_and_age():
    arm = alt.ClampOnly()
    assert arm.guarantees["displacement"] is True
    assert arm.guarantees["freshness"] is True
    assert set(k for k in alt.GUARANTEE_KEYS if arm.guarantees[k]) == {"displacement", "freshness"}
    for missing in ("latch", "watchdog", "keep_out_swept", "keep_in",
                    "structural_finite", "orientation_unit", "wrench", "budget", "receipt"):
        assert missing in arm.lacks


def test_swept_geometry_only_is_clamp_only_plus_the_sweep():
    base = alt.ClampOnly()
    swept = alt.SweptGeometryOnly()
    widened = set(k for k in alt.GUARANTEE_KEYS if swept.guarantees[k]) - \
        set(k for k in alt.GUARANTEE_KEYS if base.guarantees[k])
    assert widened == {"keep_out_swept"}
    assert isinstance(swept, alt.ClampOnly)
    for missing in ("latch", "watchdog", "budget", "receipt", "keep_in"):
        assert missing in swept.lacks


def test_lokahi_declares_every_guarantee():
    arm = alt.Lokahi()
    assert arm.lacks == ()
    assert all(arm.guarantees[key] for key in alt.GUARANTEE_KEYS)


def test_a_declaration_cannot_be_true_without_a_check():
    """A guarantee an arm does not declare is a check its source does not make."""
    import inspect

    sources = {
        name: inspect.getsource(alt.ARMS[name]) for name in ("clamp_only", "swept_geometry_only")
    }
    code_tokens = {
        "structural_finite": ("isfinite",),
        "latch": ("self.state", "latch_reason"),
        "watchdog": ("last_step_ns",),
        "wrench": ("force_n", "torque_nm"),
        "budget": ("perf_counter_ns",),
    }
    for name, source in sources.items():
        arm = alt.ARMS[name]()
        for guarantee, tokens in code_tokens.items():
            if not arm.guarantees[guarantee]:
                assert not any(token in source for token in tokens), (name, guarantee)


# ---------------------------------------------------------------------------
# no latch
# ---------------------------------------------------------------------------

def test_clamp_only_has_no_latch():
    arm = alt.ClampOnly()
    assert not hasattr(arm, "state") and not hasattr(arm, "latch_reason")
    violating = _chunk([_route_at(0.20, 0.20, 0.90)])
    first = _decide(arm, violating)
    assert first.kind == "clamped"
    clean = _chunk([_route_at(0.22)])
    assert _decide(arm, clean).kind == "pass"


def test_swept_geometry_only_has_no_latch():
    arm = alt.SweptGeometryOnly()
    crossing = _chunk([_route_at(0.50, 0.40, 0.50)])
    assert _decide(arm, crossing, pose=_route_at(0.50, 0.36, 0.50)).kind == "refused"
    clean = _chunk([_route_at(0.52, 0.36, 0.50)])
    assert _decide(arm, clean, pose=_route_at(0.50, 0.36, 0.50)).kind == "pass"


def test_lokahi_latches_and_only_a_human_clears_it():
    arm = alt.Lokahi()
    crossing = _chunk([_route_at(0.50, 0.40, 0.50)])
    pose = _route_at(0.50, 0.36, 0.50)
    assert _decide(arm, crossing, pose=pose).kind == "refused"

    clean = _chunk([_route_at(0.52, 0.36, 0.50)])
    latched = _decide(arm, clean, pose=pose)
    assert latched.kind == "refused"
    assert any("e_stop_latched" in reason for reason in latched.reasons)

    arm.gate.human_reset("operator:test", now_ts_ns=NOW + 1)
    assert _decide(arm, clean, pose=pose).kind == "pass"


def test_each_episode_gets_its_own_lokahi_instance():
    """The driver builds one arm per episode, so a latch cannot cross cells."""
    first = alt.Lokahi()
    _decide(first, _chunk([_route_at(0.50, 0.40, 0.50)]), pose=_route_at(0.50, 0.36, 0.50))
    second = alt.Lokahi()
    assert _decide(second, _chunk([_route_at(0.22)])).kind == "pass"


# ---------------------------------------------------------------------------
# no watchdog, no structural check, no wrench, no budget or receipt
# ---------------------------------------------------------------------------

def test_clamp_only_has_no_watchdog():
    arm = alt.ClampOnly()
    clean = _chunk([_route_at(0.22)])
    assert _decide(arm, clean, now=NOW).kind == "pass"
    late = NOW + int(10_000 * 1e6)
    assert _decide(arm, clean, now=late, obs=late).kind == "pass"
    assert "watchdog" in arm.lacks


def test_lokahi_refuses_the_same_gap():
    arm = alt.Lokahi()
    clean = _chunk([_route_at(0.22)])
    assert _decide(arm, clean, now=NOW).kind == "pass"
    late = NOW + int(10_000 * 1e6)
    verdict = _decide(arm, clean, now=late, obs=late)
    assert verdict.kind == "refused"
    assert any("watchdog" in reason for reason in verdict.reasons)


def test_clamp_only_lets_a_nan_reach_the_actuator():
    arm = alt.ClampOnly()
    chunk = _chunk([_route_at(0.22)])
    chunk[0]["ee_pos"] = [0.22, float("nan"), 0.50]
    verdict = _decide(arm, chunk)
    assert verdict.kind == "pass"
    assert math.isnan(verdict.execute[0]["ee_pos"][1])
    assert bench.unsafe_reasons(bench.ROUTE_OBJ.point(0.0), verdict.execute[0], 0.0)


def test_clamp_only_lets_a_non_unit_quaternion_reach_the_actuator():
    arm = alt.ClampOnly()
    chunk = _chunk([_route_at(0.22)], quat=[1.5, 0.0, 0.0, 0.0])
    verdict = _decide(arm, chunk)
    assert verdict.kind == "pass"
    assert "bad_orientation" in bench.unsafe_reasons(
        bench.ROUTE_OBJ.point(0.0), verdict.execute[0], 0.0
    )


def test_lokahi_refuses_both():
    for position, quat in (([0.22, float("nan"), 0.50], None), ([0.22], [1.5, 0.0, 0.0, 0.0])):
        arm = alt.Lokahi()
        chunk = _chunk([_route_at(*position)], quat=quat)
        verdict = _decide(arm, chunk)
        assert verdict.kind == "refused"
        assert verdict.reasons


def test_clamp_only_has_no_wrench_limit():
    arm = alt.ClampOnly()
    chunk = _chunk([_route_at(0.22)])
    wrench = {"force_n": [bench.MAX_FORCE_N * 2.0, 0.0, 0.0], "torque_nm": [1.0, 1.0, 2.0]}
    assert _decide(arm, chunk, wrench=wrench).kind == "pass"

    gate = alt.Lokahi()
    assert _decide(gate, chunk, wrench=wrench).kind == "refused"


def test_neither_alternative_carries_a_budget_or_a_receipt():
    for arm_name in ("clamp_only", "swept_geometry_only"):
        assert "budget" in alt.ARMS[arm_name].guarantees
        assert alt.ARMS[arm_name].guarantees["budget"] is False
        assert alt.ARMS[arm_name].guarantees["receipt"] is False


# ---------------------------------------------------------------------------
# the one check the geometry arm really adds
# ---------------------------------------------------------------------------

def test_the_swept_test_sees_a_segment_the_rate_limit_cannot():
    """Endpoint legal, step inside the delta, path crosses the column."""
    pose = _route_at(0.50, 0.36, 0.50)
    chunk = _chunk([_route_at(0.50, 0.40, 0.50)])
    assert math.dist(pose, chunk[0]["ee_pos"]) <= bench.MAX_POS_DELTA_M
    assert "keep_out_sweep" in bench.unsafe_reasons(pose, chunk[0], 0.0)

    assert _decide(alt.ClampOnly(), chunk, pose=pose).kind == "pass"
    assert _decide(alt.SweptGeometryOnly(), chunk, pose=pose).kind == "refused"


def test_the_swept_test_still_misses_a_nan_when_the_slab_returns_early():
    """Why the geometry arm catches NaN only sometimes: with the first axis
    constant and outside the box, the slab test returns before it reaches the
    NaN axis, so the accidental refusal disappears."""
    obstacle = alt._grow_box(bench.KEEP_OUT, bench.TOOL_RADIUS_M)
    nan_point = [0.90, float("nan"), 0.50]
    assert alt._segment_hits_box([0.90, 0.20, 0.50], nan_point, obstacle) is False
    arm = alt.SweptGeometryOnly()
    verdict = _decide(arm, _chunk([nan_point]), pose=[0.90, 0.20, 0.50])
    assert verdict.kind == "pass"
    assert "non_finite_position" in bench.unsafe_reasons([0.90, 0.20, 0.50], verdict.execute[0], 0.0)


# ---------------------------------------------------------------------------
# the rate limit honours the limit it claims
# ---------------------------------------------------------------------------

def test_clamp_only_bounds_every_step_it_issues():
    arm = alt.ClampOnly()
    for target in ([0.20, 0.20, 0.95], [0.60, 0.60, 0.50], [3.0, -2.0, 0.5]):
        chunk = _chunk([target])
        verdict = _decide(arm, chunk, pose=[0.20, 0.20, 0.50])
        assert verdict.kind == "clamped"
        assert math.dist([0.20, 0.20, 0.50], verdict.execute[0]["ee_pos"]) <= bench.MAX_POS_DELTA_M


def test_clamp_only_still_refuses_a_stale_step():
    arm = alt.ClampOnly()
    chunk = _chunk([_route_at(0.22)])
    late = NOW + int((bench.MAX_ACTION_AGE_MS + 50) * 1e6)
    assert _decide(arm, chunk, now=late, obs=NOW).kind == "refused"


# ---------------------------------------------------------------------------
# the driver is on the same corpus as the source benchmark
# ---------------------------------------------------------------------------

def test_the_driver_reproduces_the_source_benchmark():
    for arm_name, gated in (("ungated", False), ("lokahi", True)):
        for mode, latency, case in (("sync", 0, "teleport_delta"),
                                    ("lazy_async", 100, "swept_keep_out"),
                                    ("sync", 100, "force_overload")):
            ours = alt.run_episode(alt.ARMS[arm_name](), mode, latency, case=case)
            theirs = bench.run_episode(mode, latency, gated=gated, case=case)
            assert bool(ours["unsafe"]) == bool(theirs["unsafe"]), (arm_name, mode, latency, case)


def test_the_report_covers_every_arm_on_the_same_cells():
    report = alt.build_report(latencies=(0,), modes=("sync",),
                              cases=("nan_position", "force_overload"), cross_check=True)
    assert report["config"]["adversarial_cells"] == 2
    for arm in report["arms"]:
        assert arm["adversarial"]["cells"] == 2
        assert arm["benign"]["cells"] == 1
        assert arm["throughput"]["decisions"] > 0
        assert 0.0 <= arm["benign"]["over_refusals"] <= arm["benign"]["decisions"]
    assert report["cross_check"]["ungated"]["matches_reference"] is True
    assert report["cross_check"]["lokahi"]["matches_reference"] is True


def test_the_table_separates_the_arms_on_a_two_case_slice():
    report = alt.build_report(latencies=(0,), modes=("sync",),
                              cases=("nan_position", "force_overload"), cross_check=False)
    leaked = {arm["arm"]: arm["adversarial"]["leaked_cells"] for arm in report["arms"]}
    assert leaked["ungated"] == 2
    assert leaked["clamp_only"] == 2
    assert leaked["lokahi"] == 0
    for arm in report["arms"]:
        assert arm["benign"]["over_refusals"] == 0
