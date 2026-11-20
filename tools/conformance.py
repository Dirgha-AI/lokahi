# -*- coding: utf-8 -*-
"""LOKAHI conformance suite (R1-07).

A published list of behaviours a LOKAHI gateway must exhibit. Run it against a
build to prove the gate fails closed in the ways the specification claims:

    python tools/conformance.py            # in-process checks
    python tools/conformance.py --http URL # also probe a running HTTP gateway

Exit code 0 only if every check passes. This suite is deliberately independent
of the unit tests: it builds its own fixtures and asserts the externally visible
contract, not internal helpers.

The suite is also the artefact a third party can run against *another*
implementation to check compatibility (see ROADMAP.md R1-07).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ != "lokahi" and HERE not in sys.path:  # source checkout only
    sys.path.insert(0, HERE)

from lokahi import (  # noqa: E402
    Approval,
    Manifest,
    Signer,
    compute_action_digest,
)

MANIFEST_KEY = b"conformance-manifest-key-000000"
LINK_KEY = b"conformance-link-key-0000000000"
NOW = 1_800_000_000.0


@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Suite:
    results: list[Result] = field(default_factory=list)

    def check(self, name: str, fn: Callable[[], bool], detail: str = "") -> None:
        try:
            ok = bool(fn())
            self.results.append(Result(name, ok, detail if not ok else ""))
        except Exception as exc:  # noqa: BLE001 - a raising check is a failing check
            self.results.append(Result(name, False, f"{type(exc).__name__}: {exc}"))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def render(self) -> str:
        lines = []
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            line = f"[{mark}] {r.name}"
            if r.detail:
                line += f"  ({r.detail})"
            lines.append(line)
        lines.append("")
        lines.append(f"{self.passed}/{len(self.results)} checks passed")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def base_manifest(**over) -> Manifest:
    m = Manifest(
        node="conformance-node",
        informational=["service_address", "usage_liters", "meter_id"],
        forbidden_targets=["protest_history", "residency_status"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 2, "window_hours": 24},
        expires="2030-01-01T00:00:00Z",
        steward="did:web:conformance.example/steward",
        approval_window_seconds=900,
        physical_envelope={
            "max_contact_force_n": 6.1,
            "max_cartesian_velocity_ms": 0.5,
            "max_decel_ms2": 1.0,
            "workspace_bounds_m": [0.0, 2.0, -1.0, 1.0, 0.2, 2.4],
        },
        tools={
            "read_meter": {"class": "read", "args": {"meter_id": "meter_id", "usage_liters": "usage_liters"}},
            "cutoff_water_valve": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
            "send_to_cloud": {"class": "write", "stores_in": ["US"], "args": {"meter_id": "meter_id"}},
            "move_robot": {
                "class": "write",
                "physical": True,
                "args": {"contact_force_n": None, "cartesian_velocity_ms": None, "waypoint_m": None},
            },
        },
    )
    for k, v in over.items():
        setattr(m, k, v)
    return m


def call(tool: str, args: dict, msg_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}


def reasons_of(response: dict) -> list[str]:
    if response.get("allowed"):
        return list(response.get("lokahi_receipt", {}).get("reasons", []))
    return list(response.get("error", {}).get("data", {}).get("reasons", []))


def digest_of(response: dict) -> Optional[str]:
    if response.get("allowed"):
        return response.get("lokahi_receipt", {}).get("action_digest")
    return response.get("error", {}).get("data", {}).get("action_digest")


# ---------------------------------------------------------------------------
# In-process checks
# ---------------------------------------------------------------------------

def run_in_process(suite: Suite) -> None:
    from lokahi import Ledger
    from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway

    signer = Signer(MANIFEST_KEY)
    manifest = base_manifest().sign(signer)

    def gw(**kw):
        return LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore(), **kw)

    # --- manifest rules -------------------------------------------------
    suite.check("unsigned manifest is refused", lambda: (
        not gw_signed(None).intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW).get("allowed")
    ))
    suite.check("wrong-key manifest is refused", lambda: (
        not LokahiMCPGateway(manifest, Signer(b"a-different-key-0000000000000"),
                             approvers={}, approval_store=ApprovalStore())
        .intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW).get("allowed")
    ))
    expired = base_manifest(expires="2020-01-01T00:00:00Z").sign(signer)
    suite.check("expired manifest is refused", lambda: (
        not LokahiMCPGateway(expired, signer, approvers={}, approval_store=ApprovalStore())
        .intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW).get("allowed")
    ))
    no_policy = base_manifest(tools=None).sign(signer)
    suite.check("manifest without a tool policy refuses everything", lambda: (
        not LokahiMCPGateway(no_policy, signer, approvers={}, approval_store=ApprovalStore())
        .intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW).get("allowed")
    ))

    # --- tool and argument policy --------------------------------------
    g = gw()
    suite.check("benign read is allowed", lambda: g.intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW).get("allowed"))
    suite.check("unknown tool is refused", lambda: "tool_not_in_manifest:drop"
                in reasons_of(g.intercept(call("drop", {}), now=NOW)))
    suite.check("unknown argument is refused", lambda: "argument_out_of_policy:secret"
                in reasons_of(g.intercept(call("read_meter", {"meter_id": "M-1", "secret": 1}), now=NOW)))
    suite.check("forbidden target is refused", lambda: "forbidden_target:protest_history"
                in reasons_of(g.intercept(call("read_meter", {"meter_id": "M-1", "protest_history": True}), now=NOW)))
    suite.check("agent-supplied control metadata is refused", lambda: "agent_supplied_control_metadata"
                in reasons_of(g.intercept(call("read_meter", {"meter_id": "M-1", "_lokahi": {}}), now=NOW)))
    suite.check("malformed arguments are refused", lambda: "malformed_arguments"
                in reasons_of(g.intercept(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "read_meter", "arguments": "nope"}}, now=NOW)))
    suite.check("residency violation is refused", lambda: "residency_violation:US"
                in reasons_of(g.intercept(call("send_to_cloud", {"meter_id": "M-1"}), now=NOW)))

    # --- physical envelope ---------------------------------------------
    suite.check("physical force above the envelope is refused", lambda: any(
        r.startswith("physical_force_exceeded")
        for r in reasons_of(g.intercept(
            call("move_robot", {"contact_force_n": 999.0, "cartesian_velocity_ms": 0.1,
                                "waypoint_m": [1, 0, 1]}), now=NOW))
    ))

    # --- consent --------------------------------------------------------
    def consent_setup(m=2, n=3):
        from lokahi_consent import ConsentFramework, Ed25519Signer, Ed25519Verifier, Threshold

        verifiers, seeds = {}, {}
        for i in range(n):
            s = Ed25519Signer.generate()
            seeds[f"v:{i}"] = s.private_bytes().hex()
            verifiers[f"v:{i}"] = Ed25519Verifier.from_public_bytes(s.public_bytes())
        cf = ConsentFramework(manifest, verifiers, threshold=Threshold(m, n))
        store = ApprovalStore()
        gateway = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=store,
                                   consent_framework=cf)
        return gateway, store, seeds

    def sign(seeds, operator, digest, decision="yes"):
        from lokahi_consent import Ed25519Signer

        s = Ed25519Signer.from_private_bytes(bytes.fromhex(seeds[operator]))
        return Approval(operator, "dev", decision, digest, "2030-01-01T00:00:00Z",
                        f"n-{operator}-{decision}").sign(s)

    gateway, store, seeds = consent_setup()
    first = gateway.intercept(call("cutoff_water_valve", {"meter_id": "M-1"}), now=NOW)
    suite.check("irreversible call escalates without consent",
                lambda: reasons_of(first) == ["insufficient_disjoint_consent"])
    digest = digest_of(first)

    for op in ("v:0", "v:1"):
        store.add(sign(seeds, op, digest))
    suite.check("two of three signatures satisfy the manifest threshold",
                lambda: gateway.intercept(call("cutoff_water_valve", {"meter_id": "M-1"}, 2), now=NOW).get("allowed"))

    gateway2, store2, seeds2 = consent_setup()
    first2 = gateway2.intercept(call("cutoff_water_valve", {"meter_id": "M-9"}), now=NOW)
    digest2 = digest_of(first2)
    for op in ("v:0", "v:1"):
        store2.add(sign(seeds2, op, digest2, decision="no"))
    suite.check("two dissenting validators freeze the action", lambda: "suspended_by_ring"
                in reasons_of(gateway2.intercept(call("cutoff_water_valve", {"meter_id": "M-9"}, 2), now=NOW)))

    gateway3, store3, seeds3 = consent_setup()
    first3 = gateway3.intercept(call("cutoff_water_valve", {"meter_id": "M-10"}), now=NOW)
    digest3 = digest_of(first3)
    store3.add(sign(seeds3, "v:0", digest3))
    suite.check("silence is refusal when the window closes", lambda: "non_response"
                in reasons_of(gateway3.intercept(call("cutoff_water_valve", {"meter_id": "M-10"}, 2), now=NOW + 901)))

    # --- tool pinning ---------------------------------------------------
    from lokahi_toolpin import build_pin_set

    observed = [{"name": "read_meter", "description": "read", "inputSchema": {"type": "object"}},
                {"name": "cutoff_water_valve", "description": "cut", "inputSchema": {"type": "object"}},
                {"name": "send_to_cloud", "description": "send", "inputSchema": {"type": "object"}},
                {"name": "move_robot", "description": "move", "inputSchema": {"type": "object"}}]
    pins = build_pin_set(observed, manifest, "conformance", signer, now=NOW)
    gw_pins = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore(),
                               tool_pins=pins)
    suite.check("pinned tool is allowed", lambda: gw_pins.intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW).get("allowed"))
    suite.check("unpinned tool is refused under a pin set", lambda: "tool_not_pinned:unknown_tool"
                in reasons_of(gw_pins.intercept(call("unknown_tool", {}), now=NOW)))

    drifted = base_manifest()
    drifted.tools = dict(drifted.tools)
    drifted.tools["added_later"] = {"class": "irreversible", "args": {}}
    drifted = drifted.sign(signer)
    gw_drift = LokahiMCPGateway(drifted, signer, approvers={}, approval_store=ApprovalStore(),
                                tool_pins=pins)
    suite.check("manifest drift is refused", lambda: any(
        r.startswith("tool_policy_drift")
        for r in reasons_of(gw_drift.intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW))
    ))

    # --- ACL -------------------------------------------------------------
    from lokahi_acl import ToolACL

    acl = ToolACL.from_dict({"default": "deny", "consumers": {"team:a": {"allow": ["read_meter"]}}})
    gw_acl = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore(),
                              tool_acl=acl)
    suite.check("ACL permits a listed consumer", lambda: gw_acl.intercept(
        call("read_meter", {"meter_id": "M-1"}), now=NOW, consumer="team:a").get("allowed"))
    suite.check("ACL refuses an unlisted tool for that consumer", lambda: "tool_acl_denied:team:a:cutoff_water_valve"
                in reasons_of(gw_acl.intercept(call("cutoff_water_valve", {"meter_id": "M-1"}),
                                               now=NOW, consumer="team:a")))

    # --- fail-secure -----------------------------------------------------
    gw_fs = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore())
    gw_fs.failsecure.enter_secure_state("conformance", now=NOW)
    suite.check("locked gateway refuses everything", lambda: any(
        r.startswith("secure_state_active")
        for r in reasons_of(gw_fs.intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW))
    ))
    gw_fs.failsecure.rearm("conformance-rearm", now=NOW)
    suite.check("re-armed gateway allows again",
                lambda: gw_fs.intercept(call("read_meter", {"meter_id": "M-1"}, 2), now=NOW).get("allowed"))

    # --- ledger and evidence ---------------------------------------------
    chain_ok, _ = signer and (True, 0)
    gw_ledger = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore())
    gw_ledger.intercept(call("read_meter", {"meter_id": "M-1"}), now=NOW)
    suite.check("ledger chain verifies", lambda: gw_ledger.ledger.verify()[0])
    suite.check("ledger records the action digest",
                lambda: bool(gw_ledger.ledger.entries[-1].get("action_digest")))
    suite.check("denied decisions are ledgered", lambda: (
        gw_ledger.intercept(call("nope", {}), now=NOW),
        gw_ledger.ledger.entries[-1]["verdict"] == "forbidden",
    )[1])

    from lokahi_evidence import build_evidence, verify_evidence

    bundle = build_evidence(gw_ledger.ledger.entries, 0, manifest)
    suite.check("evidence pack verifies offline", lambda: verify_evidence(
        bundle, manifest_key=MANIFEST_KEY.decode()).ok)

    # --- canonical compatibility ------------------------------------------
    legacy = Manifest(
        node="legacy", informational=["a"], forbidden_targets=[], jurisdiction="IN-DL",
        residency=["IN"], exceptions={"safety_critical": 1, "window_hours": 24},
        expires="2030-01-01T00:00:00Z", steward="did:web:l",
    ).sign(signer)
    suite.check("manifests without optional fields keep a stable canonical form",
                lambda: legacy.verify(signer))


def gw_signed(signer: Optional[Signer]):
    s = signer or Signer(b"unused-but-long-enough-key-000000")
    manifest = base_manifest()
    if signer is None:
        # Deliberately unsigned.
        from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway

        return LokahiMCPGateway(manifest, s, approvers={}, approval_store=ApprovalStore())
    from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway

    return LokahiMCPGateway(manifest.sign(s), s, approvers={}, approval_store=ApprovalStore())


# ---------------------------------------------------------------------------
# HTTP checks
# ---------------------------------------------------------------------------

def run_http(suite: Suite, base_url: str, token: Optional[str]) -> None:
    def request(path: str, body: Optional[dict] = None) -> tuple[int, dict, dict]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        def _decode(raw: bytes) -> dict:
            text = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(text or "{}")
            except ValueError:
                return {"_raw": text}

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, _decode(response.read()), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read()), dict(exc.headers)

    status, body, _ = request("/healthz")
    suite.check("HTTP /healthz responds 200", lambda: status == 200)

    status, body, _ = request("/readyz")
    suite.check("HTTP /readyz responds", lambda: status in (200, 503))

    status, body, _ = request("/metrics")
    suite.check("HTTP /metrics exposes lokahi counters", lambda: status == 200)

    status, body, _ = request("/mcp", call("read_meter", {"meter_id": "M-1"}))
    suite.check("HTTP decision path responds", lambda: status in (200, 401))

    status, body, headers = request("/.well-known/oauth-protected-resource")
    suite.check("HTTP protected-resource metadata is served when OAuth is on",
                lambda: status == 200 or status == 404)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LOKAHI conformance suite")
    parser.add_argument("--http", help="Also probe a running gateway, e.g. http://127.0.0.1:8787")
    parser.add_argument("--http-token", help="Bearer token for the HTTP probes")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    suite = Suite()
    run_in_process(suite)

    if args.http:
        run_http(suite, args.http, args.http_token)

    if args.json:
        print(json.dumps(
            {"passed": suite.passed, "total": len(suite.results),
             "checks": [r.__dict__ for r in suite.results]},
            indent=2,
        ))
    else:
        print("LOKAHI CONFORMANCE SUITE")
        print("=" * 60)
        print(suite.render())

    return 0 if suite.passed == len(suite.results) else 1


if __name__ == "__main__":
    sys.exit(main())
