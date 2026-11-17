# -*- coding: utf-8 -*-
"""Sign a LOKAHI approval for one action digest (v0.1.3 reference).

When a call needs approval, the gateway's response includes `action_digest`.
Each approver signs that digest with their own key and appends the record to
the approvals file the gateway reads. The agent never writes that file.

v0.1.3 signs with the HMAC-SHA256 stand-in, so the gateway holds each
approver's key too. Public-key (Ed25519) approvals are the next milestone.

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
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lokahi import Approval, Signer, load_key  # noqa: E402


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
    approval = Approval(a.operator, a.node, a.decision, a.digest, expires, secrets.token_hex(16))
    approval.sign(Signer(load_key(a.key_file)))
    with open(a.approvals, "a", encoding="utf-8") as f:
        f.write(json.dumps(approval.to_dict(), sort_keys=True) + "\n")
    print(f"signed {a.decision!r} from {a.operator} for {a.digest[:16]}... (expires {expires})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
