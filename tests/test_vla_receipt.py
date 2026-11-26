"""Rollout receipts: build them from a real gate, then try to forge them.

Every test here is a claim an auditor would make about an episode: the record
is intact, the summary matches the record, and the signature names a key the
verifier actually trusts.
"""
import copy
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from lokahi import CANON_JCS, GENESIS, Signer, canonical_jcs, digest
from lokahi_consent import Ed25519Signer
from lokahi_evidence import EVIDENCE_FORMAT, verify_evidence

from vla.rollout_receipt import (
    ALGORITHM_ED25519,
    ALGORITHM_HMAC,
    RECEIPT_FORMAT,
    ReceiptError,
    RolloutReceipt,
    percentile,
    verify_receipt,
)
from vla.vla_gate import (
    VERDICT_CLAMPED,
    VERDICT_COMPLIANT,
    VERDICT_FORBIDDEN,
    LokahiVLAGate,
)

MS = 1_000_000
T0 = 1_000_000_000_000


def manifest():
    return {
        "node": "robot:test",
        "physical_envelope": {"max_contact_force_n": 10.0, "max_joint_torque_nm": 45.0},
        "physical_vla": {
            "max_action_age_ms": 150.0,
            "watchdog_timeout_ms": 500.0,
            "control_period_ms": 20.0,
            "execution_mode": "sync",
            "workspace_aabb": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
            "keep_out_aabbs": [],
            "kinematic_limits": {"max_pos_delta_m": 0.25},
        },
    }


def state(x=0.14, y=0.10, z=0.10):
    return {"ee_pos": [x, y, z], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}


def chunk(*points, duration_ms=20.0):
    return [
        {"ee_pos": list(point), "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": duration_ms}
        for point in points
    ]


def episode_plan():
    """Five real gate decisions: two pass, one clamps, one forbids, one recovers."""
    return [
        (state(0.10, 0.10, 0.10), chunk((0.12, 0.10, 0.10), (0.14, 0.10, 0.10))),
        (state(0.14, 0.10, 0.10), chunk((0.16, 0.10, 0.10))),
        # A violation at index 1: the prefix stays legal, so the gate clamps.
        (state(0.14, 0.10, 0.10), chunk((0.20, 0.10, 0.10), (0.60, 0.10, 0.10))),
        (state(0.14, 0.10, 0.10), []),
        (state(0.14, 0.10, 0.10), chunk((0.16, 0.10, 0.10))),
    ]


def build_receipt(signer=None, *, policy_id="policy:toy-lift", episode_id="episode-001"):
    gate = LokahiVLAGate.from_manifest(manifest())
    receipt = RolloutReceipt(
        policy_id,
        digest(CANON_JCS, manifest()),
        "sync",
        episode_id=episode_id,
        node="robot:test",
        signer=signer,
    )
    now = T0
    for index, (robot, actions) in enumerate(episode_plan()):
        now += 20 * MS
        obs = now - (5 + index) * MS
        decision = gate.evaluate_step(obs, now, robot, actions)
        receipt.record_step(decision, obs_ts_ns=obs, now_ts_ns=now, source="vla", context={"tick": index})
    return receipt, gate


@pytest.fixture(scope="module")
def ed_signer():
    return Ed25519Signer.generate()


@pytest.fixture()
def signed(ed_signer):
    receipt, _ = build_receipt(signer=ed_signer)
    return receipt.to_dict()


def pubkey_of(document):
    return document["signature"]["public_key"]


def flip(text):
    return ("1" if text[0] == "0" else "0") + text[1:]


# ---------------------------------------------------------------------------
# the aggregate itself
# ---------------------------------------------------------------------------

def test_percentile_is_nearest_rank():
    values = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert percentile(values, 50) == 3.0
    assert percentile(values, 95) == 5.0
    assert percentile(values, 99) == 5.0
    assert percentile(values, 100) == 5.0
    assert percentile([1.0], 50) == 1.0
    assert percentile([], 95) is None


def test_receipt_records_the_episode_it_ran(signed):
    verdicts = [entry["verdict"] for entry in signed["entries"]]
    assert verdicts == [
        VERDICT_COMPLIANT,
        VERDICT_COMPLIANT,
        VERDICT_CLAMPED,
        VERDICT_FORBIDDEN,
        VERDICT_COMPLIANT,
    ]
    assert signed["format"] == RECEIPT_FORMAT
    assert signed["policy_id"] == "policy:toy-lift"
    assert signed["episode_id"] == "episode-001"
    assert signed["execution_mode"] == "sync"
    assert signed["canon"] == CANON_JCS
    assert signed["step_count"] == 5
    assert signed["intervention_count"] == 2
    assert signed["verdict_counts"] == {
        VERDICT_COMPLIANT: 3,
        VERDICT_CLAMPED: 1,
        VERDICT_FORBIDDEN: 1,
    }
    assert signed["ledger_root"] == signed["entries"][-1]["hash"]
    assert signed["latency_percentiles"]["obs_to_action_ms"] == {
        "count": 5,
        "p50": 7.0,
        "p95": 9.0,
        "p99": 9.0,
    }
    assert signed["latency_percentiles"]["gate_eval_us"]["count"] == 5


def test_clean_receipt_verifies(signed):
    report = verify_receipt(signed, pubkey=pubkey_of(signed))
    assert report.ok, report.render()
    assert report.failed() == []
    assert json.loads(json.dumps(signed)) == signed


def test_clean_receipt_verifies_against_its_embedded_key(signed):
    report = verify_receipt(signed)
    assert report.ok, report.render()
    detail = {check["check"]: check["detail"] for check in report.checks}
    assert "pinned: no" in detail["signature"]


def test_empty_episode_is_verifiable_and_says_nothing(ed_signer):
    receipt = RolloutReceipt("policy:toy-lift", "a" * 64, "lazy_async", signer=ed_signer)
    document = receipt.to_dict()
    assert document["step_count"] == 0
    assert document["ledger_root"] == GENESIS
    assert document["latency_percentiles"]["obs_to_action_ms"] == {
        "count": 0,
        "p50": None,
        "p95": None,
        "p99": None,
    }
    assert verify_receipt(document, pubkey=pubkey_of(document)).ok


def test_receipt_constructor_refuses_untrustworthy_labels(ed_signer):
    with pytest.raises(ReceiptError, match="policy_id"):
        RolloutReceipt("  ", "a" * 64, "sync", signer=ed_signer)
    with pytest.raises(ReceiptError, match="manifest_hash"):
        RolloutReceipt("policy:x", "not-a-digest", "sync", signer=ed_signer)
    with pytest.raises(ReceiptError, match="execution_mode"):
        RolloutReceipt("policy:x", "a" * 64, "eventually", signer=ed_signer)


def test_record_step_refuses_a_non_decision(ed_signer):
    receipt = RolloutReceipt("policy:toy-lift", "a" * 64, "sync", signer=ed_signer)
    with pytest.raises(ReceiptError, match="verdict"):
        receipt.record_step(object(), obs_ts_ns=0, now_ts_ns=0)


# ---------------------------------------------------------------------------
# tampering
# ---------------------------------------------------------------------------

def test_edited_step_verdict_is_detected(signed):
    document = copy.deepcopy(signed)
    document["entries"][0]["verdict"] = VERDICT_FORBIDDEN
    report = verify_receipt(document, pubkey=pubkey_of(document))
    assert report.status == "tampered"
    failed = set(report.failed())
    assert {"ledger_chain", "verdict_counts", "intervention_count"} <= failed


def test_edited_step_latency_is_detected(signed):
    document = copy.deepcopy(signed)
    document["entries"][2]["extra"]["obs_to_action_ms"] += 0.1
    report = verify_receipt(document, pubkey=pubkey_of(document))
    assert report.status == "tampered"
    failed = set(report.failed())
    assert {"ledger_chain", "latency_percentiles"} <= failed


def test_edited_summary_percentile_is_detected(signed):
    document = copy.deepcopy(signed)
    document["latency_percentiles"]["obs_to_action_ms"]["p95"] = 42.0
    report = verify_receipt(document, pubkey=pubkey_of(document))
    assert report.status == "tampered"
    assert "latency_percentiles" in report.failed()


def test_edited_ledger_root_is_detected(signed):
    document = copy.deepcopy(signed)
    document["ledger_root"] = flip(document["ledger_root"])
    report = verify_receipt(document, pubkey=pubkey_of(document))
    assert report.status == "tampered"
    assert "ledger_root" in report.failed()


def test_edited_manifest_hash_is_detected(signed):
    document = copy.deepcopy(signed)
    document["manifest_hash"] = flip(document["manifest_hash"])
    report = verify_receipt(document, pubkey=pubkey_of(document))
    assert report.status == "tampered"
    assert "signature" in report.failed()


def test_dropping_a_step_is_detected(signed):
    document = copy.deepcopy(signed)
    document["entries"] = document["entries"][:-1]
    report = verify_receipt(document, pubkey=pubkey_of(document))
    assert report.status == "tampered"
    assert {"ledger_root", "step_count"} <= set(report.failed())


def test_receipt_resigned_with_another_key_is_rejected(signed):
    original_pub = pubkey_of(signed)
    other = Ed25519Signer.generate()
    body = {key: value for key, value in signed.items() if key != "signature"}
    forged = copy.deepcopy(body)
    forged["signature"] = {
        "algorithm": ALGORITHM_ED25519,
        "value": other.sign(canonical_jcs(body)),
        "public_key": other.public_bytes().hex(),
        "key_id": hashlib.sha256(other.public_bytes()).hexdigest()[:32],
    }
    assert forged["signature"]["key_id"] != signed["signature"]["key_id"]

    report = verify_receipt(forged, pubkey=original_pub)
    assert report.status == "tampered"
    assert report.failed() == ["signature"]

    # The embedded key only proves integrity; identity needs the pinned key.
    # This is exactly why `pubkey` exists and why the CLI accepts --pubkey.
    assert verify_receipt(forged).ok


def test_hmac_receipt_needs_the_shared_key():
    signer = Signer(b"k" * 32)
    receipt, _ = build_receipt(signer=signer)
    document = receipt.to_dict()
    assert document["signature"]["algorithm"] == ALGORITHM_HMAC
    assert "public_key" not in document["signature"]

    assert verify_receipt(document, key="k" * 32).ok
    assert verify_receipt(document, key="n" * 32).status == "tampered"
    # A key too short to be a key is not a verdict on the receipt.
    assert verify_receipt(document, key="short").status == "unverifiable"

    without_key = verify_receipt(document)
    assert without_key.status == "unverifiable"
    assert without_key.reason.startswith("signature")


def test_receipt_without_a_signer_is_refused_when_only_hmac_is_possible(monkeypatch):
    import vla.rollout_receipt as module

    monkeypatch.setattr(module, "_default_signer", lambda: None)
    receipt, _ = build_receipt(signer=None)
    with pytest.raises(ReceiptError, match="no signer available"):
        receipt.to_dict()


# ---------------------------------------------------------------------------
# it is the engine's chain, not a receipt-shaped copy
# ---------------------------------------------------------------------------

def test_entries_verify_with_the_existing_evidence_verifier(signed):
    entries = signed["entries"]
    for index, entry in enumerate(entries):
        bundle = {
            "format": EVIDENCE_FORMAT,
            "decision": entry,
            "chain": {
                "seq": entry["seq"],
                "prev_hash": entry["prev"],
                "prev_entry": entries[index - 1] if index else None,
                "algorithm": "sha256(canonical-version(entry without hash))",
            },
            "manifest": {"node": "robot:test"},
            "action_digest": None,
            "approvals": [],
        }
        report = verify_evidence(bundle)
        checks = {check["check"]: check["passed"] for check in report.checks}
        assert checks["decision hash"], f"entry {index}"
        assert checks["chain link"], f"entry {index}"


def test_entries_declare_the_receipts_canonical_version(signed):
    assert all(entry["canon"] == signed["canon"] for entry in signed["entries"])
    assert all(entry["kind"] == "event" for entry in signed["entries"])
