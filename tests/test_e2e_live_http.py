# -*- coding: utf-8 -*-
"""End-to-end live HTTP integration test for LOKAHI Streamable HTTP Gateway.

Spins up a real ThreadingHTTPServer, issues live HTTP POST requests,
and verifies the R1-06 Approval Protocol end-to-end:
1. Benign tool call passes fast-path with _meta.approval_status = "not_requested"
2. Irreversible tool call escalates with input_required = True & _meta.approval_status = "pending"
3. Out-of-band approval ingestion over HTTP (/approve)
4. Quorum satisfaction allows execution with _meta.approval_status = "approved"
5. Unregistered/forbidden tool returns _meta.approval_status = "refused" without input_required
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lokahi import Manifest, Signer, compute_action_digest
from lokahi_consent import ConsentFramework, Ed25519Signer, Ed25519Verifier, Threshold, Approval
from lokahi_notify import build_link
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway
from lokahi_http import make_handler, ThreadingHTTPServer

KEY = b"k" * 32
LINK_KEY = b"L" * 32
TOKEN = "test-bearer-token"


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestLiveHttpGateway(unittest.TestCase):
    def setUp(self):
        self.port = _get_free_port()
        self.signer = Signer(KEY)
        self.manifest_signer = Ed25519Signer.generate()
        self.manifest = Manifest(
            node="live-node",
            informational=["data_reading", "valve_control"],
            forbidden_targets=["unlisted_field"],
            jurisdiction="IN-DL",
            residency=["IN-DL"],
            exceptions={"read_meter": 10},
            expires="2030-01-01T00:00:00Z",
            steward="steward-1",
            tools={
                "read_meter": {
                    "class": "read",
                    "stores_in": ["IN-DL"],
                    "args": {"meter_id": "data_reading"},
                },
                "cut_valve": {
                    "class": "irreversible",
                    "stores_in": ["IN-DL"],
                    "args": {"valve_id": "valve_control"},
                },
            },
            approval_window_seconds=60,
            consent_threshold={"m": 2, "n": 3, "group": "board"},
        )
        self.manifest.sign(self.signer)

        # Generate threshold keys
        self.validators, self.private_keys = {}, {}
        for i in range(3):
            op = f"v:{i}"
            signer = Ed25519Signer.generate()
            self.private_keys[op] = signer
            self.validators[op] = Ed25519Verifier.from_public_bytes(signer.public_bytes())

        self.consent_framework = ConsentFramework(
            self.manifest,
            self.validators,
            threshold=Threshold(2, 3),
            groups={"board": ["v:0", "v:1", "v:2"]},
            operator_roles={"v:0": "steward", "v:1": "steward", "v:2": "steward"},
        )
        self.store = ApprovalStore()
        self.gateway = LokahiMCPGateway(
            self.manifest,
            self.signer,
            approvers={},
            approval_store=self.store,
            consent_framework=self.consent_framework,
            operator_roles={"v:0": "steward", "v:1": "steward", "v:2": "steward"},
        )

        handler_cls = make_handler(
            self.gateway,
            bearer_tokens=[TOKEN],
            approval_link_key=LINK_KEY,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler_cls)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        time.sleep(0.1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2.0)

    def _post(self, path: str, payload: dict, token: str = TOKEN) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            return e.code, body

    def test_end_to_end_mcp_flow(self):
        # 1. Benign tool call passes immediately
        status, resp = self._post("/mcp", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_meter", "arguments": {"meter_id": "M-101"}},
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp.get("allowed"))
        self.assertNotIn("error", resp)
        receipt = resp["lokahi_receipt"]
        self.assertEqual(receipt["_meta"]["approval_status"], "not_requested")
        self.assertNotIn("input_required", receipt)

        # 2. Irreversible tool call escalates (R1-06 pending + input_required)
        status, resp = self._post("/mcp", {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "cut_valve", "arguments": {"valve_id": "V-99"}},
        })
        self.assertEqual(status, 200)
        self.assertIn("error", resp)
        err_data = resp["error"]["data"]
        self.assertTrue(err_data.get("input_required"))
        self.assertEqual(err_data["_meta"]["approval_status"], "pending")
        action_digest = err_data["action_digest"]
        self.assertTrue(bool(action_digest))

        # 3. Ingest first steward approval via HTTP /approve
        token_v0 = build_link("", action_digest, "v:0", int(time.time()) + 3600, LINK_KEY).split("t=")[1]
        app_v0 = Approval("v:0", "portal", "yes", action_digest, "2030-01-01T00:00:00Z", "n-0").sign(self.private_keys["v:0"])
        status, resp = self._post("/approve", {
            "token": token_v0,
            "approval": app_v0.to_dict(),
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp.get("accepted"))

        # Re-call: 1 of 2 votes -> still pending
        status, resp = self._post("/mcp", {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "cut_valve", "arguments": {"valve_id": "V-99"}},
        })
        self.assertEqual(status, 200)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["data"]["_meta"]["approval_status"], "pending")
        self.assertEqual(resp["error"]["data"]["quorum_met"], 1)

        # 4. Ingest second steward approval via HTTP /approve
        token_v1 = build_link("", action_digest, "v:1", int(time.time()) + 3600, LINK_KEY).split("t=")[1]
        app_v1 = Approval("v:1", "portal", "yes", action_digest, "2030-01-01T00:00:00Z", "n-1").sign(self.private_keys["v:1"])
        status, resp = self._post("/approve", {
            "token": token_v1,
            "approval": app_v1.to_dict(),
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp.get("accepted"))

        # Re-call: 2 of 2 votes reached -> APPROVED!
        status, resp = self._post("/mcp", {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "cut_valve", "arguments": {"valve_id": "V-99"}},
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp.get("allowed"))
        self.assertNotIn("error", resp)
        receipt = resp["lokahi_receipt"]
        self.assertEqual(receipt["_meta"]["approval_status"], "approved")
        self.assertEqual(receipt["quorum_met"], 2)

        # 5. Out-of-manifest forbidden tool call -> Refused (not pending, no input_required)
        status, resp = self._post("/mcp", {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "evil_wipe_data", "arguments": {}},
        })
        self.assertEqual(status, 200)
        self.assertIn("error", resp)
        err_data = resp["error"]["data"]
        self.assertEqual(err_data["_meta"]["approval_status"], "refused")
        self.assertNotIn("input_required", err_data)


if __name__ == "__main__":
    unittest.main()
