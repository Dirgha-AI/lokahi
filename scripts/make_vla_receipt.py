"""Build a signed rollout receipt, plus a tampered copy, for the receipt CI job.

`lokahi-vla-verify` is the only program an auditor runs, and its whole contract
is the exit code: 0 intact, 1 tampered, 2 cannot evaluate. A CI job that asserts
those numbers needs a receipt the gate actually produced, so this script drives
`LokahiVLAGate` over a fixed episode and writes both artefacts into one
directory:

    receipt.json    the intact receipt, signed by a freshly generated key
    tampered.json   the same document with its ledger root mutated

It prints the signing public key on stdout so the job can pin it.

Run it against an installed wheel, not the source tree, so the entry point the
job exercises is the one `pip install lokahi` ships:

    python scripts/make_vla_receipt.py out
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lokahi import CANON_JCS, digest
from lokahi.lokahi_consent import Ed25519Signer
from lokahi.vla.rollout_receipt import RolloutReceipt
from lokahi.vla.vla_gate import LokahiVLAGate

MS = 1_000_000
T0 = 1_000_000_000_000
WRENCH = {"force_n": 0.0, "torque_nm": 0.0}

MANIFEST = {
    "node": "robot:ci-receipt",
    "physical_envelope": {"max_contact_force_n": 10.0, "max_joint_torque_nm": 45.0},
    "physical_vla": {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        "control_period_ms": 20.0,
        "workspace_aabb": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
        "kinematic_limits": {"max_pos_delta_m": 0.25},
    },
}

# Three steps: two inside the 0.25 m per-step limit, one that jumps 0.40 m and is
# therefore clamped. A receipt of only clean passes would not exercise the
# clamped verdict in the chain.
PLAN = ((0.10, (0.12,)), (0.14, (0.16,)), (0.14, (0.20, 0.60)))


def _state(x):
    return {"ee_pos": [x, 0.10, 0.10], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0, 0.0]}


def _chunk(*xs):
    return [
        {"ee_pos": [x, 0.10, 0.10], "ee_quat": [1.0, 0.0, 0.0, 0.0], "duration_ms": 20.0}
        for x in xs
    ]


def _verified(_document):
    """The document below is this script's own fixture; signature binding is
    covered by tests/test_vla_gate.py and is not what this job measures."""
    return True


def build(signer):
    gate = LokahiVLAGate.from_manifest(MANIFEST, verifier=_verified)
    receipt = RolloutReceipt(
        "policy:ci-receipt", digest(CANON_JCS, MANIFEST), "sync", signer=signer
    )
    now = T0
    for robot_x, points in PLAN:
        now += 20 * MS
        obs = now - 5 * MS
        decision = gate.evaluate_step(obs, now, _state(robot_x), _chunk(*points), wrench=WRENCH)
        receipt.record_step(decision, obs, now)
    return receipt.to_dict()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("outdir", help="directory to write receipt.json and tampered.json into")
    args = parser.parse_args(argv)

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    document = build(Ed25519Signer.generate())
    (out / "receipt.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

    tampered = json.loads(json.dumps(document))
    root = tampered["ledger_root"]
    tampered["ledger_root"] = ("1" if root[0] == "0" else "0") + root[1:]
    (out / "tampered.json").write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    print(document["signature"]["public_key"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
