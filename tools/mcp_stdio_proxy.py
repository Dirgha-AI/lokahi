# -*- coding: utf-8 -*-
"""LOKAHI v0.1.2 — Model Context Protocol (MCP) JSON-RPC 2.0 Pre-Flight Gateway Proxy.

Wraps any MCP tool server (`tools/call`) with deterministic 15-rule LOKAHI boundary
verification, automatic feature/reversibility/kinematic extraction from raw JSON-RPC
arguments, and persistent hash-chained `.lokahi-ledger.jsonl` auditing.

Usage:
    # Run built-in 6-call JSON-RPC 2.0 gateway verification suite:
    py tools/mcp_stdio_proxy.py --self-test

    # Filter a stream of newline-delimited MCP JSON-RPC 2.0 messages over stdio:
    py tools/mcp_stdio_proxy.py --manifest manifest.json --ledger .lokahi-ledger.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lokahi import (
    COMPLIANT,
    ESCALATE,
    FORBIDDEN,
    Action,
    Consent,
    Decision,
    Ledger,
    Manifest,
    Signer,
    evaluate,
)

# JSON-RPC 2.0 LOKAHI error codes
JSONRPC_ERR_FORBIDDEN = -32001
JSONRPC_ERR_ESCALATE = -32002

IRREVERSIBLE_TOOL_PREFIXES = (
    "delete_",
    "drop_",
    "cutoff_",
    "suspend_",
    "terminate_",
    "disburse_",
    "transfer_",
    "override_",
    "execute_emergency_",
    "high_force_",
    "high_torque_",
)

PHYSICAL_PARAM_KEYS = {
    "contact_force_n",
    "damping_kd",
    "joint_torque_nm",
    "tendon_tension_n",
    "cartesian_velocity_ms",
    "max_decel_ms2",
    "waypoint_m",
}


class LokahiMCPGateway:
    """Deterministic MCP JSON-RPC 2.0 pre-flight interceptor."""

    def __init__(
        self,
        manifest: Manifest,
        signer: Signer | None = None,
        default_requester: str = "agent:mcp-client",
        default_operator: str = "org:local-operator",
        ledger_path: str | None = None,
    ) -> None:
        self.manifest = manifest
        self.signer = signer
        self.default_requester = default_requester
        self.default_operator = default_operator
        self.ledger = Ledger()
        self.ledger_path = ledger_path

    def extract_action_from_jsonrpc(self, rpc_msg: dict[str, Any]) -> tuple[Action, list[Consent], bool]:
        """Extract a LOKAHI Action, Consents, and timeout state from a raw MCP `tools/call` payload."""
        params = rpc_msg.get("params") or {}
        tool_name = str(params.get("name", "unknown_tool"))
        arguments = params.get("arguments") or {}
        meta = params.get("_lokahi") or arguments.get("_lokahi") or {}

        # Extract informational features: explicit list + argument field names (excluding meta/physical keys)
        explicit_features = meta.get("features") or arguments.get("features")
        if isinstance(explicit_features, list):
            features = [str(x) for x in explicit_features]
        else:
            features = [
                str(k)
                for k in arguments.keys()
                if not str(k).startswith("_") and str(k) not in PHYSICAL_PARAM_KEYS
            ]
        if not features:
            features = list(self.manifest.informational[:1]) if self.manifest.informational else ["telemetry"]

        # Infer reversibility from tool name prefix unless explicitly specified
        is_mutating_prefix = any(tool_name.lower().startswith(p) for p in IRREVERSIBLE_TOOL_PREFIXES)
        reversible = bool(meta.get("reversible", not is_mutating_prefix))
        population_affecting = bool(meta.get("population_affecting", False))

        # Extract physical robotics parameters if present in arguments or meta
        phys_params = meta.get("physical_params") or {}
        for pk in PHYSICAL_PARAM_KEYS:
            if pk in arguments and pk not in phys_params:
                phys_params[pk] = arguments[pk]

        action = Action(
            requester=str(meta.get("requester", self.default_requester)),
            tool=tool_name,
            features=features,
            jurisdiction=str(meta.get("jurisdiction", arguments.get("jurisdiction", self.manifest.jurisdiction))),
            requester_operator=str(meta.get("requester_operator", self.default_operator)),
            purpose=str(meta.get("purpose", f"mcp:{tool_name}")),
            reversible=reversible,
            population_affecting=population_affecting,
            stores_in=[str(x) for x in meta.get("stores_in", arguments.get("stores_in", []))],
            declared_by=meta.get("declared_by"),
            exceptions_used=int(meta.get("exceptions_used", 0)),
            physical_params=phys_params if phys_params else None,
        )

        raw_consents = meta.get("consents") or []
        consents = [
            Consent(
                operator=str(c.get("operator", "")),
                node=str(c.get("node", "")),
                agents=int(c.get("agents", 1)),
                decision=str(c.get("decision", "pending")),
                bound_action_digest=str(c.get("bound_action_digest", "")),
            )
            for c in raw_consents
        ]
        timed_out = bool(meta.get("timed_out", False))
        return action, consents, timed_out

    def intercept(self, rpc_msg: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """Intercept a JSON-RPC 2.0 request. Non-`tools/call` methods pass through."""
        method = rpc_msg.get("method", "")
        msg_id = rpc_msg.get("id")

        if method != "tools/call":
            return {"allowed": True, "passthrough": True, "request": rpc_msg}

        action, consents, timed_out = self.extract_action_from_jsonrpc(rpc_msg)
        decision: Decision = evaluate(
            self.manifest,
            action,
            consents=consents,
            signer=self.signer,
            now=now,
            timed_out=timed_out,
            ledger=self.ledger,
        )
        entry = self.ledger.append(decision, action, self.manifest, ts=now)

        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")

        if decision.verdict == COMPLIANT:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "allowed": True,
                "lokahi_receipt": {
                    "verdict": decision.verdict,
                    "reasons": decision.reasons,
                    "actuator_interlock": decision.actuator_interlock,
                    "ledger_seq": entry["seq"],
                    "ledger_hash": entry["hash"],
                },
                "forwarded_request": rpc_msg,
            }

        err_code = JSONRPC_ERR_FORBIDDEN if decision.verdict == FORBIDDEN else JSONRPC_ERR_ESCALATE
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "allowed": False,
            "error": {
                "code": err_code,
                "message": f"LOKAHI_{decision.verdict.upper()}: {','.join(decision.reasons)}",
                "data": {
                    "verdict": decision.verdict,
                    "reasons": decision.reasons,
                    "quorum_required": decision.quorum_required,
                    "quorum_met": decision.quorum_met,
                    "actuator_interlock": decision.actuator_interlock,
                    "ledger_seq": entry["seq"],
                    "ledger_hash": entry["hash"],
                },
            },
        }


def run_self_test() -> int:
    key = b"mcp-gateway-ed25519-secret-key-01"
    signer = Signer(key)
    now = 1_800_000_000.0
    manifest = Manifest(
        node="mcp:municipal-grid-gateway",
        informational=["usage_liters", "meter_id", "ee_force_torque"],
        forbidden_targets=["protest_history", "demographic_stratification_proxy"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 2, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:dirgha.example/mcp/grid",
        physical_envelope={
            "max_contact_force_n": 6.1,
            "max_cartesian_velocity_ms": 0.5,
            "max_decel_ms2": 1.0,
            "workspace_bounds_m": [0.0, 2.0, -1.0, 1.0, 0.2, 2.4],
        },
    ).sign(signer)

    gw = LokahiMCPGateway(manifest, signer=signer)

    test_calls = [
        (
            "1. Benign read_meter fast-path call",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_meter", "arguments": {"meter_id": "M-104", "usage_liters": True}}},
            True,
            "within_manifest",
        ),
        (
            "2. Barred optimization target in MCP arguments",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "score_citizen", "arguments": {"meter_id": "M-104", "protest_history": True}}},
            False,
            "forbidden_target:protest_history",
        ),
        (
            "3. Mutating cutoff_valve tool auto-detected as irreversible -> ESCALATE",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "cutoff_water_valve", "arguments": {"meter_id": "M-104"}}},
            False,
            "insufficient_disjoint_consent",
        ),
        (
            "4. Mutating cutoff_valve with Ahimsa Timeout (Rule 14) -> FORBIDDEN",
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "cutoff_water_valve", "arguments": {"meter_id": "M-104"}, "_lokahi": {"timed_out": True}}},
            False,
            "non_response",
        ),
        (
            "5. Mutating cutoff_valve with 3-of-3 disjoint steward ring -> COMPLIANT",
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "cutoff_water_valve",
                    "arguments": {"meter_id": "M-104"},
                    "_lokahi": {
                        "consents": [
                            {"operator": "steward:delhi-jal", "node": "n1", "decision": "yes"},
                            {"operator": "steward:ashoka-cdais", "node": "n2", "decision": "yes"},
                            {"operator": "steward:ombudsman", "node": "n3", "decision": "yes"},
                        ]
                    },
                },
            },
            True,
            "quorum_met",
        ),
        (
            "6. Robotics MCP call violating kinematic stopping-distance barrier -> FORBIDDEN",
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "move_cartesian",
                    "arguments": {
                        "ee_force_torque": True,
                        "contact_force_n": 5.0,
                        "cartesian_velocity_ms": 0.45,
                        "waypoint_m": [1.95, 0.0, 1.0],
                    },
                },
            },
            False,
            "physical_stopping_distance_exceeded",
        ),
    ]

    print("=" * 84)
    print("LOKAHI v0.1.2 — MCP JSON-RPC 2.0 PRE-FLIGHT PROXY SELF-TEST (6 SCENARIOS)")
    print("=" * 84)
    passed = 0
    for label, call, exp_allowed, exp_reason in test_calls:
        res = gw.intercept(call, now=now)
        actual_allowed = bool(res.get("allowed"))
        reasons = (
            res["lokahi_receipt"]["reasons"]
            if actual_allowed
            else res["error"]["data"]["reasons"]
        )
        ok = (actual_allowed == exp_allowed) and any(r.startswith(exp_reason) for r in reasons)
        if ok:
            passed += 1
        flag = "[PASS]" if ok else "[FAIL]"
        print(f"{flag} {label:<62} | Allowed={str(actual_allowed):<5} | {reasons[0]}")

    chain_ok, chain_len = gw.ledger.verify()
    print("-" * 84)
    print(f"MCP Proxy Results: {passed}/{len(test_calls)} passed | Hash-Chained Ledger: {chain_len} entries (valid={chain_ok})")
    print("=" * 84)
    return 0 if (passed == len(test_calls) and chain_ok) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="LOKAHI v0.1.2 MCP JSON-RPC 2.0 Pre-Flight Proxy")
    parser.add_argument("--self-test", action="store_true", help="Run built-in 6-scenario JSON-RPC 2.0 self-test")
    parser.add_argument("--manifest", help="Path to signed LOKAHI manifest JSON")
    parser.add_argument("--ledger", help="Optional path to append JSONL ledger entries")
    args = parser.parse_args()

    if args.self_test or not args.manifest:
        return run_self_test()

    with open(args.manifest, "r", encoding="utf-8") as f:
        m = Manifest.from_dict(json.load(f))
    gw = LokahiMCPGateway(m, ledger_path=args.ledger)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        sys.stdout.write(json.dumps(gw.intercept(msg)) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
