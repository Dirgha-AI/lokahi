# -*- coding: utf-8 -*-
"""Tests for approval notifications and the /approve endpoint (R1-20).

Run: python3 tests/test_approvals.py
"""
import io
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
from lokahi_http import build_server  # noqa: E402
from lokahi_notify import (  # noqa: E402
    ConsoleChannel,
    LinkError,
    Notifier,
    WebhookChannel,
    build_link,
    parse_link,
)
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway  # noqa: E402

KEY = b"approval-test-manifest-key-0000000"
LINK_KEY = b"approval-link-key-0123456789abcdef"
NOW = 1_800_000_000.0


def _manifest(signer: Signer) -> Manifest:
    return Manifest(
        node="approval-node",
        informational=["meter_id"],
        forbidden_targets=[],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 1, "window_hours": 24},
        expires="2030-01-01T00:00:00Z",
        steward="did:web:a",
        consent_threshold={"m": 2, "n": 3},
        tools={"email_author": {"class": "irreversible", "args": {"meter_id": "meter_id"}}},
    ).sign(signer)


def _call(msg_id=1):
    return {
        "jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
        "params": {"name": "email_author", "arguments": {"meter_id": "M-1"}},
    }


class TestLinkTokens(unittest.TestCase):
    def test_roundtrip(self):
        link = build_link("http://h:1", "dig", "v:0", 1_800_000_000 + 60, LINK_KEY)
        parsed = parse_link("", link.split("t=")[1], LINK_KEY, now=1_800_000_000)
        self.assertEqual(parsed["digest"], "dig")
        self.assertEqual(parsed["operator"], "v:0")

    def test_tampered_signature(self):
        link = build_link("http://h:1", "dig", "v:0", 1_800_000_060, LINK_KEY)
        token = link.split("t=")[1]
        with self.assertRaises(LinkError):
            parse_link("", token, b"wrong-key-0000000000000000000", now=1_800_000_000)

    def test_expired(self):
        link = build_link("http://h:1", "dig", "v:0", 1_800_000_060, LINK_KEY)
        with self.assertRaises(LinkError):
            parse_link("", link.split("t=")[1], LINK_KEY, now=1_800_000_061)

    def test_operator_is_bound(self):
        """Changing the operator in the token invalidates the MAC."""
        import base64

        link = build_link("http://h:1", "dig", "v:0", 1_800_000_060, LINK_KEY)
        raw = json.loads(base64.urlsafe_b64decode(link.split("t=")[1] + "=="))
        raw["o"] = "v:1"
        forged = base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()
        with self.assertRaises(LinkError):
            parse_link("", forged, LINK_KEY, now=1_800_000_000)

    def test_short_key_rejected(self):
        with self.assertRaises(LinkError):
            build_link("http://h:1", "dig", "v:0", 1_800_000_060, b"short")


class TestChannels(unittest.TestCase):
    def test_console(self):
        buffer = io.StringIO()
        notifier = Notifier("http://h:1", LINK_KEY, {"v:0": [ConsoleChannel(buffer)]})
        notices = notifier.notify_pending("dig", "email_author", "node", ["escalate"], now=NOW)
        self.assertEqual(len(notices), 1)
        text = buffer.getvalue()
        self.assertIn("LOKAHI approval requested", text)
        self.assertIn("dig", text)
        self.assertIn("/approve?t=", text)
        self.assertEqual(notifier.stats["sent"], 1)

    def test_failure_is_counted(self):
        notifier = Notifier("http://h:1", LINK_KEY, {"v:0": [WebhookChannel("http://127.0.0.1:9/x", timeout=0.2)]})
        notifier.notify_pending("dig", "t", "n", [], now=NOW)
        self.assertEqual(notifier.stats["failed"], 1)

    def test_one_notice_per_operator(self):
        buffer = io.StringIO()
        notifier = Notifier("http://h:1", LINK_KEY, {
            "v:0": [ConsoleChannel(buffer)],
            "v:1": [ConsoleChannel(buffer)],
        })
        notices = notifier.notify_pending("dig", "t", "n", [], now=NOW)
        self.assertEqual({n.operator for n in notices}, {"v:0", "v:1"})
        self.assertEqual(len({n.link for n in notices}), 2)


class TestApproveEndpoint(unittest.TestCase):
    def setUp(self):
        self.signer = Signer(KEY)
        self.manifest = _manifest(self.signer)
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
        self.server = build_server(self.gw, "127.0.0.1", 0, approval_link_key=LINK_KEY)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _post(self, path, body, token=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request("POST", path, json.dumps(body), headers)
        response = conn.getresponse()
        payload = response.read().decode()
        conn.close()
        return response.status, payload

    def _escalate(self):
        status, body = self._post("/mcp", _call())
        payload = json.loads(body)
        self.assertFalse(payload["allowed"])
        return payload["error"]["data"]["action_digest"]

    def _signed(self, operator, digest, decision="yes"):
        signer = Ed25519Signer.from_private_bytes(bytes.fromhex(self.seeds[operator]))
        record = Approval(
            operator, "web", decision, digest, "2030-01-01T00:00:00Z", f"n-{operator}-{decision}"
        ).sign(signer)
        return record.to_dict()

    def test_full_flow_escalate_then_approve_writes_satisfy_quorum(self):
        digest = self._escalate()
        for operator in ("v:0", "v:1"):
            token = build_link("", digest, operator, int(time.time()) + 3600, LINK_KEY).split("t=")[1]
            status, body = self._post(
                "/approve", {"token": token, "approval": self._signed(operator, digest)}
            )
            self.assertEqual(status, 200, body)
        status, body = self._post("/mcp", _call(2))
        payload = json.loads(body)
        self.assertTrue(payload["allowed"], payload)
        self.assertEqual(payload["lokahi_receipt"]["quorum_met"], 2)

    def test_operator_mismatch_rejected(self):
        digest = self._escalate()
        token = build_link("", digest, "v:0", int(time.time()) + 3600, LINK_KEY).split("t=")[1]
        status, body = self._post(
            "/approve", {"token": token, "approval": self._signed("v:1", digest)}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "operator_mismatch")

    def test_digest_mismatch_rejected(self):
        digest = self._escalate()
        token = build_link("", digest, "v:0", int(time.time()) + 3600, LINK_KEY).split("t=")[1]
        status, body = self._post(
            "/approve", {"token": token, "approval": self._signed("v:0", "0" * 64)}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "digest_mismatch")

    def test_bad_link_rejected(self):
        digest = self._escalate()
        token = build_link("", digest, "v:0", int(time.time()) + 3600, LINK_KEY).split("t=")[1]
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        status, body = self._post(
            "/approve", {"token": tampered, "approval": self._signed("v:0", digest)}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "bad_link")

    def test_get_approve_page_renders(self):
        import http.client
        digest = self._escalate()
        token = build_link("", digest, "v:0", int(time.time()) + 3600, LINK_KEY).split("t=")[1]
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/approve?t=" + token)
        response = conn.getresponse()
        body = response.read().decode()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertIn("Approval requested", body)
        self.assertIn(digest, body)


if __name__ == "__main__":
    unittest.main()
