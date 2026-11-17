# -*- coding: utf-8 -*-
"""LOKAHI v0.1.3 — MCP JSON-RPC 2.0 pre-flight decision filter.

Evaluates each `tools/call` request against a signed LOKAHI manifest before it
reaches a tool server, and appends every decision to a hash-chained ledger.

Security model (v0.1.3):
  * The agent's request is data, never control. A request carrying LOKAHI
    control metadata (`_lokahi`: consents, timeouts, reversibility,
    jurisdiction, storage) is refused instead of trusted.
  * Facts come from the signed manifest's per-tool policy: which arguments a
    tool may take and which data feature each reveals, whether the tool is
    irreversible, and where it stores data. Unknown tools and arguments are
    denied.
  * Approvals arrive out of band as signed records bound to the exact call
    (tool name plus argument values). The gateway's clock decides when an
    approval window closes; an unanswered request is refused (Rule 14).
  * A manifest verification key is mandatory. Without one the CLI refuses to
    start and the library class refuses to construct.

Not yet: this is a decision filter, not a forwarding proxy. It does not launch
or relay to the tool server and does not inspect tool results.

Usage:
    python tools/mcp_stdio_proxy.py --self-test
    python tools/mcp_stdio_proxy.py --manifest manifest.json --key-file manifest.key \
        --approvers approvers.json --approvals approvals.jsonl --ledger .lokahi-ledger.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lokahi import (  # noqa: E402
    COMPLIANT,
    ESCALATE,
    FORBIDDEN,
    Action,
    Approval,
    Decision,
    Ledger,
    Manifest,
    Signer,
    accept_approvals,
    canonical,
    compute_action_digest,
    evaluate,
    load_key,
    manifest_status,
    validate_tool_policy,
)

# JSON-RPC 2.0 LOKAHI error codes
JSONRPC_ERR_FORBIDDEN = -32001
JSONRPC_ERR_ESCALATE = -32002
JSONRPC_ERR_PARSE = -32700

DEFAULT_APPROVAL_WINDOW_SECONDS = 900
CONTROL_METADATA_KEY = "_lokahi"

PHYSICAL_PARAM_KEYS = {
    "contact_force_n",
    "damping_kd",
    "joint_torque_nm",
    "tendon_tension_n",
    "cartesian_velocity_ms",
    "max_decel_ms2",
    "waypoint_m",
}


class GatewayConfigError(ValueError):
    """The gateway was configured in a way that cannot be made safe."""


class ApprovalStore:
    """Signed approvals, read from a JSONL file the agent cannot write to.

    Approvals can also be added in memory (tests, embedding applications).
    Malformed lines are skipped; they never count as approval.
    """

    def __init__(self, path: str | None = None, approvals: list[Approval] | None = None) -> None:
        self.path = path
        self._memory: list[Approval] = list(approvals or [])

    def add(self, approval: Approval) -> None:
        self._memory.append(approval)

    def load(self) -> list[Approval]:
        items = list(self._memory)
        if self.path and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(Approval.from_dict(json.loads(line)))
                    except (ValueError, TypeError):
                        continue
        return items


def call_nonce(tool_name: str, arguments: dict) -> str:
    """Bind approvals to the exact call: the tool name plus its argument values."""
    return "args:" + hashlib.sha256(canonical({"tool": tool_name, "arguments": arguments})).hexdigest()


class LokahiMCPGateway:
    """Deterministic MCP JSON-RPC 2.0 pre-flight decision filter (v0.1.3)."""

    def __init__(
        self,
        manifest: Manifest,
        signer: Signer | None,
        approvers: dict[str, Signer] | None = None,
        approval_store: ApprovalStore | None = None,
        default_requester: str = "agent:mcp-client",
        default_operator: str = "org:local-operator",
        ledger_path: str | None = None,
        approval_window_seconds: int | None = None,
    ) -> None:
        if signer is None:
            raise GatewayConfigError("a manifest verification key is required; refusing to start without one")
        self.manifest = manifest
        self.signer = signer
        self.approvers = dict(approvers or {})
        self.approval_store = approval_store if approval_store is not None else ApprovalStore()
        self.default_requester = default_requester
        self.default_operator = default_operator
        self.ledger = Ledger()
        self.ledger_path = ledger_path
        window = approval_window_seconds if approval_window_seconds is not None else manifest.approval_window_seconds
        self.approval_window_seconds = int(window) if window is not None else DEFAULT_APPROVAL_WINDOW_SECONDS
        self.pending: dict[str, float] = {}  # action digest -> when its approval window opened
        self._hold = "safe_mechanical_hold" if manifest.physical_envelope is not None else "pass"
        self._policy_error: str | None = None
        if manifest.tools is None:
            self._policy_error = "no_tool_policy"
        else:
            try:
                validate_tool_policy(manifest.tools)
            except ValueError:
                self._policy_error = "invalid_tool_policy"

    # ------------------------------------------------------------------
    def _record(
        self,
        rpc_msg: dict[str, Any],
        decision: Decision,
        action: Action,
        now: float,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        msg_id = rpc_msg.get("id") if isinstance(rpc_msg, dict) else None
        entry = self.ledger.append(decision, action, self.manifest, ts=now)
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        data: dict[str, Any] = {
            "verdict": decision.verdict,
            "reasons": decision.reasons,
            "quorum_required": decision.quorum_required,
            "quorum_met": decision.quorum_met,
            "actuator_interlock": decision.actuator_interlock,
            "ledger_seq": entry["seq"],
            "ledger_hash": entry["hash"],
        }
        if extra:
            data.update(extra)
        if decision.verdict == COMPLIANT:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "allowed": True,
                "lokahi_receipt": data,
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
                "data": data,
            },
        }

    def _deny(self, rpc_msg: dict[str, Any], action: Action, now: float, reasons: list[str]) -> dict[str, Any]:
        return self._record(rpc_msg, Decision(FORBIDDEN, reasons, actuator_interlock=self._hold), action, now)

    # ------------------------------------------------------------------
    def intercept(self, rpc_msg: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """Decide one JSON-RPC 2.0 request. Non-`tools/call` methods pass through."""
        method = rpc_msg.get("method", "") if isinstance(rpc_msg, dict) else ""
        if method != "tools/call":
            return {"allowed": True, "passthrough": True, "request": rpc_msg}

        now = now if now is not None else time.time()
        params = rpc_msg.get("params")
        params = params if isinstance(params, dict) else {}
        raw_name = params.get("name")
        tool_name = raw_name if isinstance(raw_name, str) and raw_name else "unknown_tool"
        arguments = params.get("arguments", {})
        base = Action(
            requester=self.default_requester,
            tool=tool_name,
            features=[],
            jurisdiction=self.manifest.jurisdiction,
            requester_operator=self.default_operator,
            purpose=f"mcp:{tool_name}",
        )

        # Rules 1-3 first: nothing can rescue a bad manifest.
        status = manifest_status(self.manifest, self.signer, now, self._hold)
        if status is not None:
            return self._record(rpc_msg, status, base, now)

        if not isinstance(arguments, dict):
            return self._deny(rpc_msg, base, now, ["malformed_arguments"])

        # The agent may not send its own approvals, timeouts or facts.
        if CONTROL_METADATA_KEY in params or CONTROL_METADATA_KEY in arguments:
            return self._deny(rpc_msg, base, now, ["agent_supplied_control_metadata"])

        if self._policy_error:
            return self._deny(rpc_msg, base, now, [self._policy_error])
        policy = self.manifest.tools.get(tool_name)
        if policy is None:
            return self._deny(rpc_msg, base, now, [f"tool_not_in_manifest:{tool_name}"])

        allowed_args = policy["args"]
        unknown = sorted(a for a in arguments if a not in allowed_args)
        if unknown:
            barred = set(self.manifest.forbidden_targets)
            reasons = [f"forbidden_target:{a}" for a in unknown if a in barred]
            reasons += [f"argument_out_of_policy:{a}" for a in unknown if a not in barred]
            return self._deny(rpc_msg, base, now, reasons)

        features = sorted({allowed_args[a] for a in arguments if allowed_args[a]})
        physical_params = None
        if policy.get("physical"):
            physical_params = {k: arguments[k] for k in sorted(PHYSICAL_PARAM_KEYS) if k in arguments} or None

        action = Action(
            requester=self.default_requester,
            tool=tool_name,
            features=features,
            jurisdiction=policy.get("jurisdiction", self.manifest.jurisdiction),
            requester_operator=self.default_operator,
            purpose=f"mcp:{tool_name}",
            reversible=policy["class"] != "irreversible",
            population_affecting=bool(policy.get("population_affecting", False)),
            stores_in=list(policy.get("stores_in", [])),
            physical_params=physical_params,
        )
        nonce = call_nonce(tool_name, arguments)
        digest = compute_action_digest(self.manifest, action, nonce)

        consents = []
        timed_out = False
        closes = None
        if (not action.reversible) or action.population_affecting:
            opened = self.pending.setdefault(digest, now)
            closes = opened + self.approval_window_seconds
            timed_out = now >= closes
            consents = accept_approvals(self.approval_store.load(), self.approvers, digest, now)

        decision = evaluate(
            self.manifest,
            action,
            consents,
            signer=self.signer,
            now=now,
            timed_out=timed_out,
            ledger=self.ledger,
            round_nonce=nonce,
        )
        if decision.verdict != ESCALATE:
            self.pending.pop(digest, None)  # a refusal or approval closes this round

        extra: dict[str, Any] = {"action_digest": digest}
        if decision.verdict == ESCALATE and closes is not None:
            extra["approval_window_closes"] = closes
        return self._record(rpc_msg, decision, action, now, extra)


# ----------------------------------------------------------------------
# self-test
# ----------------------------------------------------------------------

SELF_TEST_TOOLS = {
    "read_meter": {"class": "read", "args": {"meter_id": "meter_id", "usage_liters": "usage_liters"}},
    "cutoff_water_valve": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
    "move_cartesian": {
        "class": "write",
        "physical": True,
        "args": {
            "ee_force_torque": "ee_force_torque",
            "contact_force_n": None,
            "cartesian_velocity_ms": None,
            "waypoint_m": None,
        },
    },
}


def _call(msg_id: int, tool: str, arguments: dict, params_extra: dict | None = None) -> dict:
    params: dict[str, Any] = {"name": tool, "arguments": arguments}
    if params_extra:
        params.update(params_extra)
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}


def _approve(store: ApprovalStore, keys: dict[str, Signer], digest: str, decision: str = "yes", sign_with: Signer | None = None) -> None:
    for i, (operator, key) in enumerate(sorted(keys.items())):
        store.add(
            Approval(operator, f"device-{i}", decision, digest, "2027-02-01T00:00:00Z", f"{digest[:12]}-{operator}").sign(
                sign_with or key
            )
        )


def run_self_test() -> int:
    signer = Signer(b"mcp-gateway-selftest-manifest-key-01")
    approver_keys = {
        "steward:delhi-jal": Signer(b"approver-key-delhi-jal-000000000"),
        "steward:ombudsman": Signer(b"approver-key-ombudsman-00000000"),
        "steward:ward-council": Signer(b"approver-key-ward-council-00000"),
    }
    attacker = Signer(b"attacker-key-pretending-approver")
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
        tools=SELF_TEST_TOOLS,
        approval_window_seconds=900,
    ).sign(signer)

    store = ApprovalStore()
    gw = LokahiMCPGateway(manifest, signer, approvers=approver_keys, approval_store=store)
    results: list[tuple[str, dict, bool, str]] = []

    def check(label: str, res: dict, exp_allowed: bool, exp_reason: str) -> None:
        results.append((label, res, exp_allowed, exp_reason))

    check("1. Benign read_meter call", gw.intercept(_call(1, "read_meter", {"meter_id": "M-104", "usage_liters": True}), now=now), True, "within_manifest")
    check("2. Barred target smuggled in as an extra argument", gw.intercept(_call(2, "read_meter", {"meter_id": "M-104", "protest_history": True}), now=now), False, "forbidden_target:protest_history")
    check("3. Irreversible valve cutoff, no approvals yet -> ESCALATE", gw.intercept(_call(3, "cutoff_water_valve", {"meter_id": "M-104"}), now=now), False, "insufficient_disjoint_consent")
    check("4. Same cutoff after the approval window closes -> FORBIDDEN", gw.intercept(_call(4, "cutoff_water_valve", {"meter_id": "M-104"}), now=now + 900), False, "non_response")

    first = gw.intercept(_call(5, "cutoff_water_valve", {"meter_id": "M-105"}), now=now)
    digest_105 = first["error"]["data"]["action_digest"]
    _approve(store, approver_keys, digest_105)
    check("5. Three signed out-of-band approvals -> COMPLIANT", gw.intercept(_call(5, "cutoff_water_valve", {"meter_id": "M-105"}), now=now + 60), True, "quorum_met")

    check(
        "6. Robot move breaks the stopping-distance barrier",
        gw.intercept(_call(6, "move_cartesian", {"ee_force_torque": True, "contact_force_n": 5.0, "cartesian_velocity_ms": 0.45, "waypoint_m": [1.95, 0.0, 1.0]}), now=now),
        False,
        "physical_stopping_distance_exceeded",
    )
    self_approval = {CONTROL_METADATA_KEY: {"consents": [{"operator": op, "node": "n", "decision": "yes"} for op in approver_keys], "timed_out": False}}
    check("7. Agent tries to approve itself -> FORBIDDEN", gw.intercept(_call(7, "cutoff_water_valve", {"meter_id": "M-106"}, self_approval), now=now), False, "agent_supplied_control_metadata")
    check("8. Tool not in the manifest -> FORBIDDEN", gw.intercept(_call(8, "drop_all_records", {}), now=now), False, "tool_not_in_manifest:drop_all_records")

    first = gw.intercept(_call(9, "cutoff_water_valve", {"meter_id": "M-107"}), now=now)
    _approve(store, approver_keys, first["error"]["data"]["action_digest"], sign_with=attacker)
    check("9. Approvals forged with the wrong key are ignored", gw.intercept(_call(9, "cutoff_water_valve", {"meter_id": "M-107"}), now=now + 60), False, "insufficient_disjoint_consent")
    check("10. Approvals for valve M-105 replayed for M-108 are ignored", gw.intercept(_call(10, "cutoff_water_valve", {"meter_id": "M-108"}), now=now + 60), False, "insufficient_disjoint_consent")

    print("=" * 92)
    print(f"LOKAHI v0.1.3 - MCP JSON-RPC 2.0 PRE-FLIGHT GATEWAY SELF-TEST ({len(results)} SCENARIOS)")
    print("=" * 92)
    passed = 0
    for label, res, exp_allowed, exp_reason in results:
        allowed = bool(res.get("allowed"))
        reasons = res["lokahi_receipt"]["reasons"] if allowed else res["error"]["data"]["reasons"]
        ok = allowed == exp_allowed and any(r.startswith(exp_reason) for r in reasons)
        passed += ok
        print(f"{'[PASS]' if ok else '[FAIL]'} {label:<60} | Allowed={str(allowed):<5} | {reasons[0]}")

    chain_ok, chain_len = gw.ledger.verify()
    print("-" * 92)
    print(f"MCP Gateway Results: {passed}/{len(results)} passed | Hash-Chained Ledger: {chain_len} entries (valid={chain_ok})")
    print("=" * 92)
    return 0 if (passed == len(results) and chain_ok) else 1


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def load_approvers(path: str) -> dict[str, Signer]:
    """approvers.json maps operator id -> key file path (relative paths resolve next to the JSON)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("approvers file must map operator ids to key file paths")
    base = os.path.dirname(os.path.abspath(path))
    approvers: dict[str, Signer] = {}
    for operator, key_path in data.items():
        resolved = key_path if os.path.isabs(key_path) else os.path.join(base, key_path)
        approvers[str(operator)] = Signer(load_key(resolved))
    return approvers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOKAHI v0.1.3 MCP JSON-RPC 2.0 pre-flight decision filter")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in gateway self-test")
    parser.add_argument("--manifest", help="Path to a signed LOKAHI manifest JSON with a tools policy")
    parser.add_argument("--key-file", help="Manifest verification key file (or set LOKAHI_MANIFEST_KEY)")
    parser.add_argument("--approvers", help="JSON mapping approver ids to their key files")
    parser.add_argument("--approvals", help="JSONL file of signed approvals (written by tools/lokahi_approve.py)")
    parser.add_argument("--ledger", help="Optional path to append JSONL ledger entries")
    parser.add_argument("--requester", default="agent:mcp-client", help="Identity of the agent being gated")
    parser.add_argument("--operator", default="org:local-operator", help="Who runs the agent (cannot approve its own calls)")
    args = parser.parse_args(argv)

    if args.self_test or not args.manifest:
        return run_self_test()

    def refuse(reason: str) -> int:
        print(f"lokahi: refusing to start: {reason}", file=sys.stderr)
        return 2

    key = None
    try:
        if args.key_file:
            key = load_key(args.key_file)
        elif os.environ.get("LOKAHI_MANIFEST_KEY"):
            key = os.environ["LOKAHI_MANIFEST_KEY"].encode("utf-8")
        if not key:
            return refuse("no manifest verification key (use --key-file or LOKAHI_MANIFEST_KEY)")
        signer = Signer(key)
        with open(args.manifest, encoding="utf-8") as f:
            manifest = Manifest.from_dict(json.load(f))
        approvers = load_approvers(args.approvers) if args.approvers else {}
    except (OSError, ValueError) as exc:
        return refuse(str(exc))

    problem = manifest_status(manifest, signer)
    if problem is not None:
        return refuse(problem.reasons[0])
    if manifest.tools is None:
        return refuse("manifest has no tools policy; v0.1.3 denies every tool without one")
    try:
        validate_tool_policy(manifest.tools)
    except ValueError as exc:
        return refuse(f"invalid tools policy: {exc}")

    gw = LokahiMCPGateway(
        manifest,
        signer,
        approvers=approvers,
        approval_store=ApprovalStore(args.approvals),
        default_requester=args.requester,
        default_operator=args.operator,
        ledger_path=args.ledger,
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out: dict[str, Any] = {"jsonrpc": "2.0", "id": None, "allowed": False, "error": {"code": JSONRPC_ERR_PARSE, "message": "parse error"}}
        else:
            out = gw.intercept(msg)
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
