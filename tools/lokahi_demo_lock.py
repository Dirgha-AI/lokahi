# -*- coding: utf-8 -*-
"""Demonstrate the fail-secure path: allowed, locked, re-armed.

Usage:
    python tools/lokahi_demo_lock.py

Uses a throwaway manifest and key; no network, no files written.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ != "lokahi" and HERE not in sys.path:  # source checkout only
    sys.path.insert(0, HERE)

from lokahi import Manifest, Signer  # noqa: E402
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway  # noqa: E402

NOW = 1_800_000_000.0


def main() -> int:
    signer = Signer(b"demo-lock-key-000000000000000000")
    manifest = Manifest(
        node="demo-node",
        informational=["meter_id"],
        forbidden_targets=[],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 1, "window_hours": 24},
        expires="2030-01-01T00:00:00Z",
        steward="did:web:demo.example/steward",
        tools={"read_meter": {"class": "read", "args": {"meter_id": "meter_id"}}},
    ).sign(signer)

    gateway = LokahiMCPGateway(
        manifest, signer, approvers={}, approval_store=ApprovalStore()
    )
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "read_meter", "arguments": {"meter_id": "M-104"}},
    }

    first = gateway.intercept(call, now=NOW)
    print(f"1. before lock : allowed={first.get('allowed')}")

    gateway.failsecure.enter_secure_state("demo_cascade_failure", now=NOW)
    second = gateway.intercept(call, now=NOW)
    print(f"2. after lock  : allowed={second.get('allowed')} "
          f"reasons={second['error']['data']['reasons']}")

    gateway.failsecure.rearm("human-rearm-signature", now=NOW)
    third = gateway.intercept(call, now=NOW)
    print(f"3. after rearm : allowed={third.get('allowed')}")

    ok, length = gateway.ledger.verify()
    print(f"ledger: {length} entries, chain valid={ok}")
    return 0 if (first.get("allowed") and not second.get("allowed")
                 and third.get("allowed") and ok) else 1


if __name__ == "__main__":
    sys.exit(main())
