# -*- coding: utf-8 -*-
"""Tests for R1 features: tool pinning (R1-28) and evidence packs (R1-15).

Run: python3 tests/test_r1_features.py
"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lokahi import Approval, Manifest, Signer  # noqa: E402
from lokahi_consent import (  # noqa: E402
    ConsentFramework,
    Ed25519Signer,
    Ed25519Verifier,
    Threshold,
)
from lokahi_evidence import build_evidence, verify_evidence  # noqa: E402
from lokahi_toolpin import (  # noqa: E402
    build_pin_set,
    check_catalogue,
    check_manifest,
    policy_digest,
    tool_schema_digest,
    verify_pin_set,
)
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway  # noqa: E402

KEY = b"r1-features-manifest-key-00000000"
NOW = 1_800_000_000.0
TOOLS = [
    {
        "name": "read_meter",
        "description": "Read a water meter",
        "inputSchema": {
            "type": "object",
            "properties": {"meter_id": {"type": "string"}},
            "required": ["meter_id"],
        },
    },
    {
        "name": "email_author",
        "description": "Email a submission author",
        "inputSchema": {
            "type": "object",
            "properties": {"meter_id": {"type": "string"}},
        },
    },
]


def _manifest(signer, tools=None, threshold=None):
    m = Manifest(
        node="r1-node",
        informational=["meter_id"],
        forbidden_targets=[],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 1, "window_hours": 24},
        expires="2030-01-01T00:00:00Z",
        steward="did:web:r1.example/steward",
        consent_threshold=threshold,
        tools=tools
        or {
            "read_meter": {"class": "read", "args": {"meter_id": "meter_id"}},
            "email_author": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
        },
    )
    return m.sign(signer)


def _call(tool, args, msg_id=1):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }


# ---------------------------------------------------------------------------
# R1-28 tool pinning
# ---------------------------------------------------------------------------

class TestToolPinDigests(unittest.TestCase):
    def test_schema_digest_ignores_volatile_fields(self):
        a = dict(TOOLS[0])
        b = dict(TOOLS[0], _meta={"trace": "x"}, annotations={"readOnlyHint": True})
        self.assertEqual(tool_schema_digest(a), tool_schema_digest(b))

    def test_schema_digest_changes_with_description(self):
        a = dict(TOOLS[0])
        b = dict(TOOLS[0], description="Read a meter and send results to an external host")
        self.assertNotEqual(tool_schema_digest(a), tool_schema_digest(b))

    def test_policy_digest_is_key_order_independent(self):
        p1 = {"class": "write", "args": {"b": None, "a": "x"}, "stores_in": ["US", "IN"]}
        p2 = {"stores_in": ["IN", "US"], "args": {"a": "x", "b": None}, "class": "write"}
        self.assertEqual(policy_digest(p1), policy_digest(p2))


class TestToolPinning(unittest.TestCase):
    def setUp(self):
        self.signer = Signer(KEY)
        self.manifest = _manifest(self.signer)
        self.pins = build_pin_set(TOOLS, self.manifest, "org:security", self.signer, now=NOW)

    def test_pin_set_roundtrip(self):
        self.assertTrue(verify_pin_set(self.pins, self.signer))
        self.assertEqual(set(self.pins["tools"]), {"read_meter", "email_author"})

    def test_tampered_pin_set_rejected(self):
        bad = copy.deepcopy(self.pins)
        bad["tools"]["read_meter"]["class"] = "write"
        self.assertFalse(verify_pin_set(bad, self.signer))

    def test_no_drift_when_unchanged(self):
        self.assertEqual(check_catalogue(self.pins, TOOLS), [])
        self.assertEqual(check_manifest(self.pins, self.manifest), [])

    def test_description_change_is_schema_drift(self):
        changed = [dict(TOOLS[0], description="Read a meter and exfiltrate"), TOOLS[1]]
        events = check_catalogue(self.pins, changed)
        self.assertEqual([(e.tool, e.kind) for e in events], [("read_meter", "schema_changed")])

    def test_removed_tool_is_drift(self):
        events = check_catalogue(self.pins, [TOOLS[1]])
        self.assertIn(("read_meter", "removed"), [(e.tool, e.kind) for e in events])

    def test_added_tool_is_drift(self):
        extra = TOOLS + [{"name": "wipe_disk", "description": "", "inputSchema": {}}]
        events = check_catalogue(self.pins, extra)
        self.assertIn(("wipe_disk", "added"), [(e.tool, e.kind) for e in events])

    def test_manifest_policy_change_is_drift(self):
        changed = _manifest(
            self.signer,
            tools={
                "read_meter": {"class": "write", "args": {"meter_id": "meter_id"}},
                "email_author": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
            },
        )
        events = check_manifest(self.pins, changed)
        self.assertEqual([(e.tool, e.kind) for e in events], [("read_meter", "policy_changed")])

    def test_gateway_allows_pinned_tool(self):
        gw = LokahiMCPGateway(
            self.manifest, self.signer, approvers={}, approval_store=ApprovalStore(),
            tool_pins=self.pins,
        )
        result = gw.intercept(_call("read_meter", {"meter_id": "M-1"}), now=NOW)
        self.assertTrue(result.get("allowed"), result)

    def test_gateway_refuses_unpinned_tool(self):
        gw = LokahiMCPGateway(
            self.manifest, self.signer, approvers={}, approval_store=ApprovalStore(),
            tool_pins=self.pins,
        )
        result = gw.intercept(_call("drop_everything", {}), now=NOW)
        self.assertFalse(result.get("allowed"))
        self.assertIn("tool_not_pinned:drop_everything", result["error"]["data"]["reasons"])

    def test_gateway_refuses_manifest_drift(self):
        drifted = _manifest(
            self.signer,
            tools={
                "read_meter": {"class": "read", "args": {"meter_id": "meter_id"}},
                "email_author": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
                "new_tool": {"class": "irreversible", "args": {}},
            },
        )
        gw = LokahiMCPGateway(
            drifted, self.signer, approvers={}, approval_store=ApprovalStore(),
            tool_pins=self.pins,
        )
        result = gw.intercept(_call("read_meter", {"meter_id": "M-1"}), now=NOW)
        self.assertFalse(result.get("allowed"))
        self.assertIn("tool_policy_drift:new_tool", result["error"]["data"]["reasons"])


# ---------------------------------------------------------------------------
# R1-15 evidence packs
# ---------------------------------------------------------------------------

class TestEvidencePacks(unittest.TestCase):
    def setUp(self):
        self.signer = Signer(KEY)
        self.manifest = _manifest(self.signer, threshold={"m": 2, "n": 3})
        self.verifiers, self.seeds = {}, {}
        for i in range(3):
            s = Ed25519Signer.generate()
            self.seeds[f"v:{i}"] = s.private_bytes().hex()
            self.verifiers[f"v:{i}"] = Ed25519Verifier.from_public_bytes(s.public_bytes())
        self.cf = ConsentFramework(self.manifest, self.verifiers, threshold=Threshold(2, 3))
        self.store = ApprovalStore()
        self.gw = LokahiMCPGateway(
            self.manifest, self.signer, approvers={}, approval_store=self.store,
            consent_framework=self.cf,
        )

    def _approved_decision(self):
        call = _call("email_author", {"meter_id": "M-1"})
        first = self.gw.intercept(call, now=NOW)
        digest = first["error"]["data"]["action_digest"]
        used = []
        for i in [0, 1]:
            op = f"v:{i}"
            sv = Ed25519Signer.from_private_bytes(bytes.fromhex(self.seeds[op]))
            ap = Approval(op, f"d{i}", "yes", digest, "2030-01-01T00:00:00Z", f"n-{op}").sign(sv)
            self.store.add(ap)
            used.append(ap)
        result = self.gw.intercept(call, now=NOW)
        self.assertTrue(result.get("allowed"), result)
        return digest, used

    def test_ledger_records_action_digest(self):
        digest, _ = self._approved_decision()
        self.assertEqual(self.gw.ledger.entries[-1].get("action_digest"), digest)

    def test_evidence_verifies(self):
        digest, used = self._approved_decision()
        seq = len(self.gw.ledger.entries) - 1
        bundle = build_evidence(
            self.gw.ledger.entries, seq, self.manifest, approvals=used, pack_signer=self.signer
        )
        report = verify_evidence(
            bundle,
            manifest_key=KEY.decode(),
            validators={op: self.verifiers[op].public_bytes().hex() for op in self.verifiers},
            pack_key=KEY.decode(),
        )
        self.assertTrue(report.ok, report.render())
        names = [c["check"] for c in report.checks]
        for required in ("decision hash", "chain link", "manifest signature",
                         "approval binding", "approval signatures", "pack signature"):
            self.assertIn(required, names)

    def test_evidence_detects_tampered_decision(self):
        _, used = self._approved_decision()
        seq = len(self.gw.ledger.entries) - 1
        bundle = build_evidence(self.gw.ledger.entries, seq, self.manifest, approvals=used)
        tampered = copy.deepcopy(bundle)
        tampered["decision"]["verdict"] = "forbidden"
        report = verify_evidence(tampered, manifest_key=KEY.decode())
        self.assertFalse(report.ok)
        failed = [c["check"] for c in report.checks if not c["passed"]]
        self.assertIn("decision hash", failed)

    def test_evidence_detects_swapped_manifest(self):
        _, used = self._approved_decision()
        seq = len(self.gw.ledger.entries) - 1
        bundle = build_evidence(self.gw.ledger.entries, seq, self.manifest, approvals=used)
        other = _manifest(Signer(b"a-different-manifest-key-000000"))
        bundle["manifest"] = other.to_dict() | {"signature": other.signature}
        report = verify_evidence(bundle, manifest_key=KEY.decode())
        self.assertFalse(report.ok)
        failed = [c["check"] for c in report.checks if not c["passed"]]
        self.assertIn("manifest signature", failed)

    def test_evidence_detects_approval_signed_by_wrong_key(self):
        digest, used = self._approved_decision()
        seq = len(self.gw.ledger.entries) - 1
        bundle = build_evidence(self.gw.ledger.entries, seq, self.manifest, approvals=used)
        forged = [("v:0", "00" * 32), ("v:1", self.verifiers["v:0"].public_bytes().hex())]
        report = verify_evidence(
            bundle, manifest_key=KEY.decode(), validators=dict(forged)
        )
        self.assertFalse(report.ok)
        failed = [c["check"] for c in report.checks if not c["passed"]]
        self.assertIn("approval signatures", failed)

    def test_evidence_detects_broken_chain_link(self):
        _, used = self._approved_decision()
        seq = len(self.gw.ledger.entries) - 1
        bundle = build_evidence(self.gw.ledger.entries, seq, self.manifest, approvals=used)
        bundle["chain"]["prev_entry"]["verdict"] = "compliant"
        report = verify_evidence(bundle, manifest_key=KEY.decode())
        self.assertFalse(report.ok)
        failed = [c["check"] for c in report.checks if not c["passed"]]
        self.assertIn("chain link", failed)

    def test_evidence_without_keys_fails_closed(self):
        _, used = self._approved_decision()
        seq = len(self.gw.ledger.entries) - 1
        bundle = build_evidence(self.gw.ledger.entries, seq, self.manifest, approvals=used)
        report = verify_evidence(bundle)  # no manifest key
        self.assertFalse(report.ok)

    def test_cli_build_and_verify(self):
        import subprocess
        import tempfile

        _, _ = self._approved_decision()
        with tempfile.TemporaryDirectory() as d:
            ledger = os.path.join(d, "ledger.jsonl")
            with open(ledger, "w", encoding="utf-8") as f:
                for e in self.gw.ledger.entries:
                    f.write(json.dumps(e, sort_keys=True) + "\n")
            manifest_path = os.path.join(d, "manifest.json")
            signed = self.manifest.to_dict() | {"signature": self.manifest.signature}
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(signed, f)
            key_path = os.path.join(d, "manifest.key")
            with open(key_path, "w", encoding="ascii") as f:
                f.write(KEY.decode())
            pack = os.path.join(d, "pack.json")

            r = subprocess.run(
                [sys.executable, os.path.join("tools", "lokahi_evidence.py"), "build",
                 "--ledger", ledger, "--seq", str(len(self.gw.ledger.entries) - 1),
                 "--manifest", manifest_path, "--out", pack],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(r.returncode, 0, r.stderr)

            r = subprocess.run(
                [sys.executable, os.path.join("tools", "lokahi_evidence.py"), "verify", pack,
                 "--manifest-key-file", key_path],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("EVIDENCE VERIFIED", r.stdout)


if __name__ == "__main__":
    unittest.main()
