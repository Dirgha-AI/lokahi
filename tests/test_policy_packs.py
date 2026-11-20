# -*- coding: utf-8 -*-
"""Validate the shipped policy packs.

Run: python3 tests/test_policy_packs.py
"""
import glob
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lokahi import Manifest, Signer, validate_tool_policy  # noqa: E402
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway  # noqa: E402

KEY = b"policy-pack-test-key-00000000000"
NOW = 1_800_000_000.0

PACKS = sorted(glob.glob(os.path.join(ROOT, "policies", "*", "*.manifest.json")))


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def call(tool, args, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}


class TestPolicyPacks(unittest.TestCase):
    def test_packs_exist(self):
        self.assertGreaterEqual(len(PACKS), 3, f"found {PACKS}")

    def test_each_pack_is_valid_and_signable(self):
        signer = Signer(KEY)
        for path in PACKS:
            with self.subTest(pack=os.path.basename(path)):
                data = load(path)
                data.pop("signature", None)
                manifest = Manifest.from_dict(data)
                validate_tool_policy(manifest.tools)
                self.assertIsNotNone(manifest.consent_threshold)
                self.assertIn("m", manifest.consent_threshold)
                self.assertIn("n", manifest.consent_threshold)
                self.assertLessEqual(manifest.consent_threshold["m"], manifest.consent_threshold["n"])
                self.assertTrue(manifest.expired(now=NOW) is False)
                signed = manifest.sign(signer)
                self.assertTrue(signed.verify(signer))

    def test_flags_are_well_formed(self):
        """population_affecting and physical, when present, are booleans."""
        for path in PACKS:
            with self.subTest(pack=os.path.basename(path)):
                data = load(path)
                for name, policy in data["tools"].items():
                    for flag in ("population_affecting", "physical"):
                        if flag in policy:
                            self.assertIsInstance(policy[flag], bool, f"{name}.{flag}")

    def test_benign_reads_are_allowed_and_forbidden_fields_refused(self):
        signer = Signer(KEY)
        for path in PACKS:
            with self.subTest(pack=os.path.basename(path)):
                data = load(path)
                data.pop("signature", None)
                manifest = Manifest.from_dict(data).sign(signer)
                gw = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore())
                read_tool = next(n for n, p in manifest.tools.items() if p["class"] == "read")
                allowed_arg = next(iter(manifest.tools[read_tool]["args"]))
                result = gw.intercept(call(read_tool, {allowed_arg: "X-1"}), now=NOW)
                self.assertTrue(result.get("allowed"), (path, result))
                barred = manifest.forbidden_targets[0]
                refused = gw.intercept(
                    call(read_tool, {allowed_arg: "X-1", barred: True}), now=NOW
                )
                self.assertFalse(refused.get("allowed"))
                self.assertTrue(
                    any(barred in r for r in refused["error"]["data"]["reasons"]),
                    refused,
                )

    def test_irreversible_tool_escalates_without_consent(self):
        signer = Signer(KEY)
        for path in PACKS:
            with self.subTest(pack=os.path.basename(path)):
                data = load(path)
                data.pop("signature", None)
                manifest = Manifest.from_dict(data).sign(signer)
                gw = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore())
                name, policy = next(
                    (n, p) for n, p in manifest.tools.items() if p["class"] == "irreversible"
                )
                args = {next(iter(policy["args"])): "X-1"}
                result = gw.intercept(call(name, args), now=NOW)
                self.assertFalse(result.get("allowed"), (path, result))
                self.assertIn(
                    "insufficient_disjoint_consent",
                    result["error"]["data"]["reasons"],
                )


if __name__ == "__main__":
    unittest.main()
