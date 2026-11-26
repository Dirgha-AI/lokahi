"""The rubric scores by running LOKAHI, and these tests hold it to that.

Four properties are asserted here, because a reviewer needs them before any
number in the rubric can be believed:

1. the weights are a partition of 100;
2. a dimension that could not be measured cannot move the total;
3. scoring is deterministic, including through a JSON round trip;
4. moving a threshold is visible in the evidence string, so a verdict change is
   never silent.
"""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rubric  # noqa: E402

REQUIRED_KEYS = {"metric", "value", "threshold", "passed", "measured", "weight", "evidence"}


@pytest.fixture(scope="module")
def measurements():
    """One quick measurement run, shared: two latency cells, every other probe."""
    return rubric.measure(quick=True)


@pytest.fixture(scope="module")
def result(measurements):
    return rubric.score(measurements)


# ---------------------------------------------------------------------------
# the rubric is a partition of 100
# ---------------------------------------------------------------------------

def test_weights_sum_to_one_hundred():
    assert sum(rubric.WEIGHTS.values()) == 100


def test_every_weighted_dimension_is_also_described():
    assert set(rubric.WEIGHTS) == set(rubric.THRESHOLDS)
    assert set(rubric.WEIGHTS) == set(rubric.DIRECTIONS)
    assert set(rubric.WEIGHTS) == set(rubric.DIMENSIONS)


def test_every_dimension_carries_the_required_fields(result):
    for name, dimension in result["dimensions"].items():
        assert REQUIRED_KEYS <= set(dimension), name
        assert dimension["weight"] == rubric.WEIGHTS[name]
        assert isinstance(dimension["measured"], bool)
        assert dimension["direction"] in ("min", "max")


def test_the_required_dimensions_are_all_present():
    required = {
        "over_refusal_rate",
        "constraint_violation_reduction",
        "staleness_handling",
        "gate_latency_budget",
        "determinism_and_replay",
        "auditability",
        "dependency_footprint",
        "fail_closed_coverage",
        "dynamic_obstacle_support",
        "recovery_capability",
        "packaging_completeness",
    }
    assert required <= set(rubric.WEIGHTS)


# ---------------------------------------------------------------------------
# an unmeasured dimension cannot contribute
# ---------------------------------------------------------------------------

def test_the_total_is_a_partial_score_over_measured_weight(result):
    dimensions = result["dimensions"]
    measured = [d for d in dimensions.values() if d["measured"]]
    assert result["measured_weight"] == sum(d["weight"] for d in measured)
    assert result["measured_weight"] + result["unmeasured_weight"] == 100
    assert result["partial"] is True
    assert result["total"] == pytest.approx(
        100.0 * result["earned_weight"] / result["measured_weight"]
    )
    assert result["total"] <= 100.0


def test_the_unmeasured_dimension_is_reported_with_a_reason(result):
    unmeasured = result["unmeasured"]
    assert unmeasured == ["hardware_and_rated_controller"]
    dimension = result["dimensions"][unmeasured[0]]
    assert dimension["measured"] is False
    assert dimension["passed"] is False
    assert "not measured" in dimension["evidence"]
    assert result["unmeasured_weight"] == rubric.WEIGHTS[unmeasured[0]]


def test_a_perfect_unmeasured_dimension_cannot_raise_the_total(measurements, result):
    """Claiming the hardware dimension is fine changes nothing: it is not scored."""
    optimistic = copy.deepcopy(measurements)
    optimistic["hardware"]["robot_attached"] = True
    rescored = rubric.score(optimistic)
    assert rescored["total"] == result["total"]
    assert rescored["earned_weight"] == result["earned_weight"]


def test_counting_the_unmeasured_weight_as_passed_would_inflate_the_total(result):
    inflated = 100.0 * (result["earned_weight"] + result["unmeasured_weight"]) / 100.0
    assert inflated > result["total"]


# ---------------------------------------------------------------------------
# scoring is deterministic
# ---------------------------------------------------------------------------

def test_scoring_the_same_measurements_twice_is_identical(measurements):
    assert rubric.score(measurements) == rubric.score(measurements)


def test_scoring_survives_a_json_round_trip(measurements):
    """A reviewer can store the measurements and re-score them months later."""
    stored = json.loads(json.dumps(measurements))
    assert rubric.score(stored) == rubric.score(measurements)


def test_replay_fingerprints_are_identical_across_runs():
    first = rubric.replay_fingerprint()
    second = rubric.replay_fingerprint()
    assert first == second
    assert first["verdicts"] == ["COMPLIANT", "CLAMPED", "FORBIDDEN", "FORBIDDEN", "FORBIDDEN", "FORBIDDEN"]


def test_measurement_reruns_agree_on_every_dimension_but_the_clock(measurements):
    """A second measurement run moves only the dimension that reads a clock."""
    again = rubric.measure(quick=True)
    first_score = rubric.score(measurements)
    second_score = rubric.score(again)
    volatile = {"gate_latency_budget"}
    for name in rubric.WEIGHTS:
        if name in volatile:
            continue
        assert (
            first_score["dimensions"][name] == second_score["dimensions"][name]
        ), f"{name} changed between two identical measurement runs"


# ---------------------------------------------------------------------------
# a moved threshold is visible, never silent
# ---------------------------------------------------------------------------

def test_lowering_a_threshold_is_named_in_the_evidence(measurements, result):
    baseline = result["dimensions"]["over_refusal_rate"]
    assert baseline["passed"] is True

    tightened = rubric.score(measurements, thresholds={"over_refusal_rate": -1.0})
    moved = tightened["dimensions"]["over_refusal_rate"]

    assert moved["value"] == baseline["value"]
    assert moved["passed"] is False
    assert moved["threshold"] == -1.0
    assert "threshold -1.0000" in moved["evidence"]
    assert f"threshold {baseline['threshold']:.4f}" not in moved["evidence"]
    assert tightened["total"] < result["total"]


def test_loosening_a_threshold_turns_the_verdict_and_says_why(measurements, result):
    failed = result["failed"]
    assert "dynamic_obstacle_support" in failed, "the dynamic-obstacle gap must be visible"

    loosened = rubric.score(measurements, thresholds={"dynamic_obstacle_support": -1.0})
    moved = loosened["dimensions"]["dynamic_obstacle_support"]
    assert moved["passed"] is True
    assert moved["value"] == 0.0
    assert "threshold -1.00" in moved["evidence"]
    assert loosened["total"] > result["total"]


def test_threshold_overrides_do_not_leak_into_the_module_table(measurements):
    rubric.score(measurements, thresholds={"over_refusal_rate": -1.0})
    assert rubric.THRESHOLDS["over_refusal_rate"] == 0.01


# ---------------------------------------------------------------------------
# the numbers come from the real code, not from this test file
# ---------------------------------------------------------------------------

def test_the_adversarial_dimension_matches_the_harness_counters(measurements, result):
    adversarial = measurements["adversarial"]
    assert adversarial["executions"] == adversarial["cases"] * adversarial["cells"]
    assert adversarial["ungated_unsafe"] > 0
    expected = 1.0 - adversarial["gated_unsafe"] / adversarial["ungated_unsafe"]
    assert result["dimensions"]["constraint_violation_reduction"]["value"] == pytest.approx(expected)


def test_staleness_is_measured_against_a_scenario_that_actually_goes_stale(measurements, result):
    staleness = measurements["staleness"]
    assert staleness["ungated_stale_executed"] > 0, "the scenario must produce staleness to judge it"
    assert staleness["gated_stale_executed"] == 0
    dimension = result["dimensions"]["staleness_handling"]
    assert dimension["measured"] is True
    assert dimension["passed"] is True


def test_auditability_probes_a_signed_receipt_and_a_tampered_one(measurements, result):
    audit = measurements["auditability"]
    assert audit["signature_algorithm"] in ("ed25519", "hmac-sha256")
    assert audit["total"] == 7
    assert audit["passed"] == 7, audit["failures"]
    assert result["dimensions"]["auditability"]["passed"] is True


def test_fail_closed_counts_real_refusals(measurements, result):
    fail_closed = measurements["fail_closed"]
    assert fail_closed["total"] == 14
    assert fail_closed["passed"] == 14, fail_closed["failures"]
    observed = fail_closed["observations"]["undeclared_force_ceiling_with_100n_wrench"]
    assert observed in ("COMPLIANT", "FORBIDDEN")


def test_dependency_footprint_reads_the_package_metadata(measurements):
    dependencies = measurements["dependencies"]
    assert dependencies["probe_error"] is None
    assert dependencies["declared_runtime_dependencies"] == []
    assert dependencies["third_party_at_import"] == []


def test_packaging_covers_every_source_subpackage(measurements):
    packaging = measurements["packaging"]
    assert packaging["passed"] == packaging["total"], packaging["failures"]
    assert set(packaging["discovered_packages"]) <= set(packaging["declared_packages"])


# ---------------------------------------------------------------------------
# the rendering
# ---------------------------------------------------------------------------

def test_format_rubric_prints_the_partial_total_and_the_unmeasured_reason(result):
    rendered = rubric.format_rubric(result)
    assert "Partial total" in rendered
    assert "UNMEASURED" in rendered
    assert "not extrapolated" in rendered
    assert "hardware_and_rated_controller" in rendered
    assert "not a certification" in rendered
    for name in rubric.WEIGHTS:
        assert name in rendered
