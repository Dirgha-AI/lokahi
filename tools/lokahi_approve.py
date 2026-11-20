# -*- coding: utf-8 -*-
"""Sign a LOKAHI approval for one action digest (v1.0).

When a call needs approval, the gateway's response includes `action_digest`.
Each approver signs that digest with their own key and appends the record to
the approvals file the gateway reads. The agent never writes that file.

v1.0 supports Ed25519 public-key signatures (recommended): the gate verifies
each approval with the validator's public key, never holding any validator's
private key. HMAC-SHA256 (v0.1.4) remains supported for backward compatibility.

    # Ed25519 (v1.0)
    python tools/lokahi_approve.py --key-file validator:0.key --operator validator:0 \
        --node laptop-1 --digest <action_digest> --decision yes \
        --approvals approvals.jsonl

    # HMAC-SHA256 (v0.1.4, backward compatible)
    python tools/lokahi_approve.py --key-file editor.key --operator editor:fiction \
        --node laptop-1 --digest <action_digest> --decision yes \
        --approvals approvals.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ != "lokahi" and HERE not in sys.path:  # source checkout only
    sys.path.insert(0, HERE)

from lokahi import Approval, Signer, load_key  # noqa: E402

# Ed25519 (soft dependency via `cryptography`)
try:  # noqa: E402
    from lokahi_consent import Ed25519Signer  # noqa: E402

    _ED25519_AVAILABLE = True
except ImportError:  # pragma: no cover
    Ed25519Signer = None  # type: ignore
    _ED25519_AVAILABLE = False


def _load_signer(key_file: str) -> Signer:
    """Load a Signer from a key file, auto-detecting HMAC vs Ed25519.

    Ed25519 key files start with the ``ed25519:`` prefix followed by the
    32-byte private key seed as 64 hex chars. HMAC-SHA256 keys are raw hex
    with no prefix. Falls back to HMAC-SHA256 if Ed25519 is unavailable.
    """
    key_bytes = load_key(key_file)
    text = key_bytes.decode("ascii", errors="replace").strip()
    if _ED25519_AVAILABLE and text.startswith("ed25519:"):
        hex_part = text[len("ed25519:"):]
        try:
            seed = bytes.fromhex(hex_part)
            if len(seed) == 32:
                return Ed25519Signer.from_private_bytes(seed)
        except ValueError:
            pass
    return Signer(key_bytes)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sign a LOKAHI approval for one action digest")
    p.add_argument("--key-file", required=True, help="The approver's own key file")
    p.add_argument("--operator", required=True, help="Approver id, as listed in approvers.json")
    p.add_argument("--node", required=True, help="Device or node the approver signs from")
    p.add_argument("--digest", required=True, help="action_digest from the gateway's response")
    p.add_argument("--decision", choices=["yes", "no"], required=True)
    p.add_argument("--expires-in", type=int, default=3600, help="Seconds until this approval expires")
    p.add_argument("--approvals", required=True, help="Approvals JSONL file to append to")
    a = p.parse_args(argv)

    expires = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=a.expires_in)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    signer = _load_signer(a.key_file)
    approval = Approval(a.operator, a.node, a.decision, a.digest, expires, secrets.token_hex(16))
    approval.sign(signer)

    # Record the algorithm used.
    approval_dict = approval.to_dict()
    if _ED25519_AVAILABLE and isinstance(signer, Ed25519Signer):
        approval_dict["signature_algorithm"] = "ed25519"
    else:
        approval_dict["signature_algorithm"] = "hmac-sha256"

    with open(a.approvals, "a", encoding="utf-8") as f:
        f.write(json.dumps(approval_dict, sort_keys=True) + "\n")
    algo = "ed25519" if (_ED25519_AVAILABLE and isinstance(signer, Ed25519Signer)) else "hmac-sha256"
    print(
        f"signed {a.decision!r} from {a.operator} for {a.digest[:16]}... "
        f"(expires {expires}, algo={algo})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
