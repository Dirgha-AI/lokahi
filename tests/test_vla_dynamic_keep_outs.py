"""Per-step keep-outs: the manifest's caps, the gate's sweep, the receipt.

D-2 in HANDOVER.md: keep-outs fixed at manifest load cannot see a hazard that
moves, and refusing every illegal step inside a static hazard leaves the robot
frozen in it while the environment keeps charging. `evaluate_step` therefore
takes an optional per-step set, bounded by two manifest caps, and the receipt
records that the set came from the caller rather than the manifest.
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from lokahi import CANON_JCS, GENESIS, canonical_jcs, digest
from lokahi_consent import Ed25519Signer

from vla.physical_manifest import VLAManifestError, load_physical_vla
from vla.rollout_receipt import RolloutReceipt, main, verify_receipt
from vla.vla_gate import ARMED, E_STOP, SOFT_STOP, VERDICT_COMPLIANT, LokahiVLAGate

BOX = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
T0 = 1_000_000_000_000
MS = 1_000_000

DISC = {"kind": "circle", "centre": [0.5, 0.5], "radius": 0.2}


def manifest(**overrides):
    section = {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        "control_period_ms": 20.0,
        # Generous budget so this machine's scheduling noise cannot flip a
        # verdict; the budget check has its own test in test_vla_gate.py.
        "max_gate_eval_us": 10_000_000.0,
        "workspace_aabb": BOX,
        "keep_out_aabbs": [],
        "kinematic_limits": {"max_pos_delta_m": 0.25},
    }
    section.update(overrides)
    return {
        "node": "robot:test",
        "physical_envelope": {"max_contact_force_n": 10.0, "max_joint_torque_nm": 45.0},
        "physical_vla": section,
    }


MEASURED_WRENCH = {"force_n": 1.0, "torque_nm": 1.0}


def verified(_document):
    """This file's manifests are its own fixture, so the verified path is the one under test."""
    return True


class _MeasuredGate(LokahiVLAGate):
    """The fixture's adapter measures the wrench the manifest's ceilings apply to.

    The gate refuses a configured force or torque ceiling it has no measurement
    for (`wrench:wrench_unverifiable`), so every step in a deployment with those
    ceilings carries a wrench. Saying that once here keeps each test about the
    check it names; tests that are about the wrench pass their own.
    """

    def evaluate_step(self, obs_ts_ns, now_ts_ns, robot_state, action_chunk,
                      wrench=MEASURED_WRENCH, **kwargs):
        return super().evaluate_step(
            obs_ts_ns, now_ts_ns, robot_state, action_chunk, wrench=wrench, **kwargs
        )


def gate(**overrides):
    return _MeasuredGate.from_manifest(manifest(**overrides), verifier=verified)


def state(x=0.5, y=0.5, z=0.5):
    return {"ee_pos": [x, y, z], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}


def chunk(*points, duration_ms=20.0):
    return [
        {"ee_pos": list(point), "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": duration_ms}
        for point in points
    ]


# ---------------------------------------------------------------------------
# the caps: off by default, and refused rather than ignored
# ---------------------------------------------------------------------------

def test_dynamic_keep_outs_are_off_by_default():
    config = load_physical_vla(manifest())
    assert config.max_dynamic_keep_outs == 0
    assert config.max_dynamic_radius_m is None
    assert config.policy_for("dynamic_keep_out") == "E_STOP"


def test_the_caps_load_from_the_manifest():
    config = load_physical_vla(manifest(max_dynamic_keep_outs=3, max_dynamic_radius_m=0.25))
    assert config.max_dynamic_keep_outs == 3
    assert config.max_dynamic_radius_m == pytest.approx(0.25)


@pytest.mark.parametrize("bad", [-1, 1.5, True, "2", None])
def test_a_bad_count_cap_is_fatal(bad):
    with pytest.raises(VLAManifestError, match="max_dynamic_keep_outs"):
        load_physical_vla(manifest(max_dynamic_keep_outs=bad))


@pytest.mark.parametrize("bad", [0, -0.1, "0.2", float("inf")])
def test_a_bad_radius_cap_is_fatal(bad):
    with pytest.raises(VLAManifestError, match="max_dynamic_radius_m"):
        load_physical_vla(manifest(max_dynamic_radius_m=bad))


def test_a_supplied_set_is_refused_when_the_feature_is_off():
    result = gate().evaluate_step(
        T0, T0, state(0.1, 0.5, 0.5), chunk([0.2, 0.5, 0.5]), dynamic_keep_outs=[DISC]
    )
    assert result.verdict == "FORBIDDEN"
    assert result.state == E_STOP
    assert result.violated_invariants == ["dynamic_keep_out:disabled:count=1"]
    assert result.keep_out_source == "caller"
    assert result.dynamic_keep_out_count == 1


def test_more_shapes_than_the_cap_is_refused():
    result = gate(max_dynamic_keep_outs=2).evaluate_step(
        T0, T0, state(0.1, 0.5, 0.5), chunk([0.2, 0.5, 0.5]),
        dynamic_keep_outs=[DISC, DISC, DISC],
    )
    assert result.state == E_STOP
    assert result.violated_invariants == ["dynamic_keep_out:count:3>2"]


def test_a_shape_above_the_radius_cap_is_refused():
    result = gate(max_dynamic_keep_outs=4, max_dynamic_radius_m=0.1).evaluate_step(
        T0, T0, state(0.1, 0.5, 0.5), chunk([0.2, 0.5, 0.5]), dynamic_keep_outs=[DISC]
    )
    assert result.state == E_STOP
    assert result.violated_invariants == ["dynamic_keep_out:radius:0:0.2000m>0.1m"]


@pytest.mark.parametrize("bad", [
    {"kind": "torus", "centre": [0.5, 0.5], "radius": 0.2},
    {"kind": "circle", "centre": [0.5, 0.5], "radius": 0.0},
    "disc",
])
def test_a_malformed_dynamic_shape_is_refused_not_ignored(bad):
    result = gate(max_dynamic_keep_outs=1).evaluate_step(
        T0, T0, state(0.1, 0.5, 0.5), chunk([0.2, 0.5, 0.5]), dynamic_keep_outs=[bad]
    )
    assert result.state == E_STOP
    assert result.allowed is False
    assert all(reason.startswith("dynamic_keep_out:shape:0:") for reason in result.violated_invariants)
    assert result.violated_invariants


def test_a_non_list_dynamic_set_is_refused():
    result = gate(max_dynamic_keep_outs=1).evaluate_step(
        T0, T0, state(0.1, 0.5, 0.5), chunk([0.2, 0.5, 0.5]), dynamic_keep_outs="disc"
    )
    assert result.state == E_STOP
    assert result.violated_invariants == ["dynamic_keep_out:not_a_shape_list"]


# ---------------------------------------------------------------------------
# the sweep: a disc that moves into the path is caught on that step
# ---------------------------------------------------------------------------

def test_a_disc_that_moves_into_the_path_is_caught_on_that_step():
    g = gate(max_dynamic_keep_outs=4)
    # Step 1: the disc sits at the far end of the workspace, clear of the path.
    far = {"kind": "circle", "centre": [0.9, 0.5], "radius": 0.1}
    first = g.evaluate_step(
        T0, T0, state(0.2, 0.5, 0.5), chunk([0.4, 0.5, 0.5]), dynamic_keep_outs=[far]
    )
    assert first.verdict == VERDICT_COMPLIANT
    assert first.keep_out_source == "caller"
    assert first.dynamic_keep_out_count == 1

    # Step 2: the same disc is now on the segment the arm is about to sweep.
    near = {"kind": "circle", "centre": [0.6, 0.5], "radius": 0.1}
    second = g.evaluate_step(
        T0 + 20 * MS, T0 + 20 * MS, state(0.4, 0.5, 0.5), chunk([0.5, 0.5, 0.5]),
        dynamic_keep_outs=[near],
    )
    assert second.verdict == "FORBIDDEN"
    assert second.state == E_STOP
    assert second.violated_invariants == ["workspace_keep_out:dynamic_0:segment_0"]


def test_the_same_step_without_the_set_is_armed():
    """The disc is what refuses the step, not the manifest's own bounds."""
    result = gate().evaluate_step(T0, T0, state(0.35, 0.5, 0.5), chunk([0.5, 0.5, 0.5]))
    assert result.verdict == VERDICT_COMPLIANT
    assert result.keep_out_source == "manifest"
    assert result.dynamic_keep_out_count == 0


def test_recovery_out_of_a_moving_disc_is_allowed():
    result = gate(max_dynamic_keep_outs=1, tool_radius_m=0.0).evaluate_step(
        T0, T0, state(0.5, 0.55, 0.5),
        chunk([0.5, 0.55, 0.5], [0.5, 0.5, 0.5], [0.5, 0.3, 0.5]),
        dynamic_keep_outs=[DISC],
    )
    assert result.verdict == VERDICT_COMPLIANT
    assert result.state == ARMED
    assert result.margins["keep_out_recovery_dynamic_0_m"] == pytest.approx(0.15, abs=1e-6)


def test_a_chunk_that_digs_deeper_into_a_moving_disc_is_refused():
    result = gate(max_dynamic_keep_outs=1, tool_radius_m=0.0).evaluate_step(
        T0, T0, state(0.5, 0.68, 0.5), chunk([0.5, 0.68, 0.5], [0.5, 0.5, 0.5]),
        dynamic_keep_outs=[DISC],
    )
    assert result.state == SOFT_STOP
    assert result.violated_invariants == ["recovery_required:keep_out:dynamic_0:depth=0.0200m"]


# ---------------------------------------------------------------------------
# the receipt: where the set came from, and a tampered source
# ---------------------------------------------------------------------------

def provenance_episode(signer):
    g = gate(max_dynamic_keep_outs=2)
    receipt = RolloutReceipt(
        "policy:moving-hazard",
        digest(CANON_JCS, manifest(max_dynamic_keep_outs=2)),
        "sync",
        episode_id="episode-moving",
        node="robot:test",
        signer=signer,
    )
    steps = [
        (state(0.1, 0.5, 0.5), chunk([0.2, 0.5, 0.5]), None),
        (state(0.2, 0.5, 0.5), chunk([0.3, 0.5, 0.5]),
         [{"kind": "circle", "centre": [0.8, 0.5], "radius": 0.2}]),
    ]
    now = T0
    for robot, actions, dynamic in steps:
        now += 20 * MS
        obs = now - 5 * MS
        decision = g.evaluate_step(obs, now, robot, actions, dynamic_keep_outs=dynamic)
        receipt.record_step(decision, obs_ts_ns=obs, now_ts_ns=now)
    return receipt


@pytest.fixture(scope="module")
def signer():
    return Ed25519Signer.generate()


def test_the_receipt_records_the_source_and_the_count(signer):
    document = provenance_episode(signer).to_dict()
    extras = [entry["extra"] for entry in document["entries"]]
    assert extras[0]["keep_out_source"] == "manifest"
    assert extras[0]["dynamic_keep_outs"] == 0
    assert extras[1]["keep_out_source"] == "caller"
    assert extras[1]["dynamic_keep_outs"] == 1
    report = verify_receipt(document, pubkey=document["signature"]["public_key"])
    assert report.ok, report.render()
    assert "keep_out_provenance" not in report.failed()


def reseal(document, signer):
    """Rebuild the chain and re-sign, as a builder editing its own record would."""
    entries = document["entries"]
    previous = GENESIS
    for index, entry in enumerate(entries):
        entry["seq"] = index
        entry["prev"] = previous
        body = {key: value for key, value in entry.items() if key != "hash"}
        entry["hash"] = digest(CANON_JCS, body)
        previous = entry["hash"]
    document["ledger_root"] = previous
    body = {key: value for key, value in document.items() if key != "signature"}
    document["signature"] = {
        "algorithm": "ed25519",
        "value": signer.sign(canonical_jcs(body)),
        "public_key": signer.public_bytes().hex(),
        "key_id": hashlib.sha256(signer.public_bytes()).hexdigest()[:32],
    }
    return document


def test_an_edited_source_breaks_the_chain(signer, tmp_path):
    document = provenance_episode(signer).to_dict()
    document["entries"][1]["extra"]["keep_out_source"] = "manifest"
    report = verify_receipt(document, pubkey=document["signature"]["public_key"])
    assert not report.ok
    assert "ledger_chain" in report.failed()

    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main([str(path)]) == 1


def test_a_rechained_and_resigned_source_is_caught_by_the_provenance_check(signer):
    """With the chain and signature rebuilt, only the field's own meaning fails."""
    document = provenance_episode(signer).to_dict()
    document["entries"][1]["extra"]["keep_out_source"] = "manifest"
    tampered = reseal(document, signer)
    report = verify_receipt(tampered, pubkey=tampered["signature"]["public_key"])
    assert not report.ok
    assert "ledger_chain" not in report.failed()
    assert "keep_out_provenance" in report.failed()
    assert "claims the manifest" in report.reason
