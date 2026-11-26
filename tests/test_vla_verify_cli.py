"""`lokahi-vla-verify` exit codes.

The verifier is the only thing an auditor runs, so the contract is the exit
code: 0 intact, 1 tampered, 2 cannot evaluate. Anything else is a bug that
reads as "probably fine" in a shell script.
"""
import json
import os
import subprocess
import sys

import pytest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, TOOLS)

from lokahi import CANON_JCS, Signer, digest
from lokahi_consent import Ed25519Signer

from vla.rollout_receipt import RolloutReceipt
from vla.vla_gate import LokahiVLAGate

MS = 1_000_000
T0 = 1_000_000_000_000

BOOTSTRAP = (
    "import sys; sys.path.insert(0, {tools!r}); "
    "from vla.rollout_receipt import main; raise SystemExit(main(sys.argv[1:]))"
).format(tools=TOOLS)


def manifest():
    return {
        "node": "robot:test",
        "physical_envelope": {"max_contact_force_n": 10.0, "max_joint_torque_nm": 45.0},
        "physical_vla": {
            "max_action_age_ms": 150.0,
            "watchdog_timeout_ms": 500.0,
            "control_period_ms": 20.0,
            "workspace_aabb": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
            "kinematic_limits": {"max_pos_delta_m": 0.25},
        },
    }


def state(x):
    return {"ee_pos": [x, 0.10, 0.10], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}


def chunk(*xs):
    return [
        {"ee_pos": [x, 0.10, 0.10], "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": 20.0}
        for x in xs
    ]


def build_document(signer):
    gate = LokahiVLAGate.from_manifest(manifest())
    receipt = RolloutReceipt("policy:cli", digest(CANON_JCS, manifest()), "sync", signer=signer)
    now = T0
    plan = ((0.10, (0.12,)), (0.14, (0.16,)), (0.14, (0.20, 0.60)))
    for robot_x, points in plan:
        now += 20 * MS
        obs = now - 5 * MS
        decision = gate.evaluate_step(obs, now, state(robot_x), chunk(*points))
        receipt.record_step(decision, obs_ts_ns=obs, now_ts_ns=now)
    return receipt.to_dict()


def write(tmp_path, document, name="receipt.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP, *[str(a) for a in args]],
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture(scope="module")
def ed_signer():
    return Ed25519Signer.generate()


def test_clean_receipt_exits_zero(tmp_path, ed_signer):
    document = build_document(ed_signer)
    path = write(tmp_path, document)
    result = run_cli(path, "--pubkey", document["signature"]["public_key"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECEIPT VERIFIED" in result.stdout


def test_clean_receipt_exits_zero_without_planned_key(tmp_path, ed_signer):
    document = build_document(ed_signer)
    path = write(tmp_path, document)
    result = run_cli(path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_tampered_ledger_root_exits_one(tmp_path, ed_signer):
    document = build_document(ed_signer)
    document["ledger_root"] = ("1" if document["ledger_root"][0] == "0" else "0") + document["ledger_root"][1:]
    path = write(tmp_path, document)
    result = run_cli(path, "--pubkey", document["signature"]["public_key"])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED VERIFICATION" in result.stdout
    assert "ledger_root" in result.stderr


def test_tampered_step_verdict_exits_one(tmp_path, ed_signer):
    document = build_document(ed_signer)
    document["entries"][0]["verdict"] = "FORBIDDEN"
    path = write(tmp_path, document)
    result = run_cli(path, "--pubkey", document["signature"]["public_key"])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger_chain" in result.stderr


def test_receipt_signed_by_another_key_exits_one(tmp_path):
    trusted = Ed25519Signer.generate()
    rogue = Ed25519Signer.generate()
    document = build_document(rogue)
    path = write(tmp_path, document)
    result = run_cli(path, "--pubkey", trusted.public_bytes().hex())
    assert result.returncode == 1, result.stdout + result.stderr
    assert "signature" in result.stderr


def test_hmac_receipt_needs_the_key_to_exit_zero(tmp_path):
    document = build_document(Signer(b"k" * 32))
    path = write(tmp_path, document)

    missing = run_cli(path)
    assert missing.returncode == 2, missing.stdout + missing.stderr
    assert "UNVERIFIABLE" in missing.stderr

    wrong = run_cli(path, "--key", "n" * 32)
    assert wrong.returncode == 1, wrong.stdout + wrong.stderr

    telemetry = tmp_path / "hmac.key"
    telemetry.write_text("k" * 32, encoding="utf-8")
    right = run_cli(path, "--key-file", telemetry)
    assert right.returncode == 0, right.stdout + right.stderr


def test_unreadable_inputs_exit_two(tmp_path):
    missing = run_cli(tmp_path / "absent.json")
    assert missing.returncode == 2

    junk = tmp_path / "junk.json"
    junk.write_text("{not json", encoding="utf-8")
    assert run_cli(junk).returncode == 2

    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "something-else"}), encoding="utf-8")
    result = run_cli(other)
    assert result.returncode == 2
    assert "UNVERIFIABLE" in result.stderr


def test_json_report_is_machine_readable(tmp_path, ed_signer):
    document = build_document(ed_signer)
    path = write(tmp_path, document)
    result = run_cli(path, "--pubkey", document["signature"]["public_key"], "--json")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["status"] == "verified"
    assert all(check["passed"] for check in report["checks"])
