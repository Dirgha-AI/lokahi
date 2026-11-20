# -*- coding: utf-8 -*-
"""Tests for R1 features: tool pinning (R1-28) and evidence packs (R1-15).

Run: python3 tests/test_r1_features.py
"""
import importlib.util
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


# ---------------------------------------------------------------------------
# R1-01 Streamable HTTP transport
# ---------------------------------------------------------------------------

class TestHttpTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading

        from lokahi_http import build_server

        cls.signer = Signer(KEY)
        cls.manifest = _manifest(cls.signer)
        cls.gw = LokahiMCPGateway(
            cls.manifest, cls.signer, approvers={}, approval_store=ApprovalStore()
        )
        cls.server = build_server(cls.gw, "127.0.0.1", 0, bearer_tokens=["tok-1"])
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, body, token="tok-1", accept="application/json"):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json", "Accept": accept}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request("POST", "/mcp", json.dumps(body), headers)
        response = conn.getresponse()
        payload = response.read().decode("utf-8")
        conn.close()
        return response.status, payload

    def _get(self, path):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        payload = response.read().decode("utf-8")
        conn.close()
        return response.status, payload

    def test_allowed_call_over_http(self):
        status, body = self._post(_call("read_meter", {"meter_id": "M-1"}))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["allowed"])

    def test_refusal_is_a_decision_not_a_transport_error(self):
        status, body = self._post(
            _call("read_meter", {"meter_id": "M-1", "protest_history": True})
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["allowed"])
        reasons = payload["error"]["data"]["reasons"]
        self.assertTrue(any("protest_history" in r for r in reasons), reasons)

    def test_bearer_auth_required(self):
        self.assertEqual(self._post(_call("read_meter", {"meter_id": "M-1"}), token=None)[0], 401)
        self.assertEqual(self._post(_call("read_meter", {"meter_id": "M-1"}), token="nope")[0], 401)

    def test_sse_accept_header(self):
        status, body = self._post(
            _call("read_meter", {"meter_id": "M-1"}), accept="text/event-stream"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith("event: message\ndata: "))

    def test_batch_rejected(self):
        status, _ = self._post([_call("read_meter", {"meter_id": "M-1"})])
        self.assertEqual(status, 400)

    def test_health_readiness_metrics(self):
        self.assertEqual(self._get("/healthz")[0], 200)
        status, body = self._get("/readyz")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ready"])
        status, body = self._get("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("lokahi_requests_total", body)

    def test_parse_error(self):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST", "/mcp", b"{not json",
            {"Content-Type": "application/json", "Authorization": "Bearer tok-1"},
        )
        response = conn.getresponse()
        body = json.loads(response.read().decode())
        conn.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(body["error"]["code"], -32700)


class TestHttpFailSecure(unittest.TestCase):
    def test_locked_gateway_refuses_over_http_and_reports_not_ready(self):
        import http.client
        import threading

        from lokahi_http import build_server

        signer = Signer(KEY)
        manifest = _manifest(signer)
        gw = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=ApprovalStore())
        server = build_server(gw, "127.0.0.1", 0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            gw.failsecure.enter_secure_state("test_lock", now=NOW)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/mcp", json.dumps(_call("read_meter", {"meter_id": "M-1"})),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            payload = json.loads(response.read().decode())
            conn.close()
            self.assertFalse(payload["allowed"])
            self.assertTrue(
                any(r.startswith("secure_state_active") for r in payload["error"]["data"]["reasons"])
            )

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/readyz")
            response = conn.getresponse()
            body = json.loads(response.read().decode())
            conn.close()
            self.assertEqual(response.status, 503)
            self.assertFalse(body["ready"])
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# R1-02 OAuth 2.1 resource server
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(claims, *, key, alg="RS256", kid="kid-1"):
    import json as _json
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    signing_input = (_b64(_json.dumps(header).encode()) + "." + _b64(_json.dumps(claims).encode())).encode()
    if alg == "HS256":
        import hashlib
        import hmac
        signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    elif alg == "RS256":
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    elif alg == "ES256":
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:
        signature = b""
    return signing_input.decode() + "." + _b64(signature)


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography not installed")
class TestOAuthVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import time

        from cryptography.hazmat.primitives.asymmetric import rsa

        cls.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.now = int(time.time())
        cls.issuer = "https://idp.example/realms/water"
        cls.audience = "https://lokahi.example/mcp"

    def _claims(self, **over):
        base = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": "user:alice",
            "client_id": "team:water-ops",
            "exp": self.now + 300,
            "nbf": self.now - 5,
        }
        base.update(over)
        return base

    def _verifier(self, **kw):
        from lokahi_oauth import JWTVerifier

        return JWTVerifier(issuers=[self.issuer], audience=self.audience, **kw)

    def test_valid_rs256_token(self):
        token = _make_jwt(self._claims(), key=self.rsa_key)
        # Inject the public key directly to avoid a network JWKS fetch.
        v = self._verifier(algorithms=["RS256"])
        v.jwks = type("J", (), {"get": staticmethod(lambda kid: self.rsa_key.public_key())})()
        claims = v.verify_token(token)
        self.assertEqual(claims.subject, "user:alice")
        self.assertEqual(claims.client_id, "team:water-ops")

    def test_expired_token(self):
        token = _make_jwt(self._claims(exp=self.now - 60), key=self.rsa_key)
        v = self._verifier(algorithms=["RS256"])
        v.jwks = type("J", (), {"get": staticmethod(lambda kid: self.rsa_key.public_key())})()
        from lokahi_oauth import OAuthError
        with self.assertRaises(OAuthError) as ctx:
            v.verify_token(token)
        self.assertEqual(ctx.exception.reason, "token_expired")

    def test_wrong_audience(self):
        token = _make_jwt(self._claims(aud="https://other.example"), key=self.rsa_key)
        v = self._verifier(algorithms=["RS256"])
        v.jwks = type("J", (), {"get": staticmethod(lambda kid: self.rsa_key.public_key())})()
        from lokahi_oauth import OAuthError
        with self.assertRaises(OAuthError) as ctx:
            v.verify_token(token)
        self.assertEqual(ctx.exception.reason, "bad_audience")

    def test_wrong_issuer(self):
        token = _make_jwt(self._claims(iss="https://evil.example"), key=self.rsa_key)
        v = self._verifier(algorithms=["RS256"])
        v.jwks = type("J", (), {"get": staticmethod(lambda kid: self.rsa_key.public_key())})()
        from lokahi_oauth import OAuthError
        with self.assertRaises(OAuthError) as ctx:
            v.verify_token(token)
        self.assertEqual(ctx.exception.reason, "bad_issuer")

    def test_alg_none_rejected(self):
        token = _make_jwt(self._claims(), key=b"", alg="none")
        v = self._verifier(algorithms=["RS256"])
        from lokahi_oauth import OAuthError
        with self.assertRaises(OAuthError) as ctx:
            v.verify_token(token)
        self.assertEqual(ctx.exception.reason, "algorithm_not_allowed")

    def test_algorithm_not_in_allowlist(self):
        secret = b"shared-secret-0123456789"
        token = _make_jwt(self._claims(), key=secret, alg="HS256")
        v = self._verifier(algorithms=["RS256"])
        v.jwks = type("J", (), {"get": staticmethod(lambda kid: self.rsa_key.public_key())})()
        from lokahi_oauth import OAuthError
        with self.assertRaises(OAuthError) as ctx:
            v.verify_token(token)
        self.assertEqual(ctx.exception.reason, "algorithm_not_allowed")

    def test_hs256_roundtrip(self):
        secret = b"shared-secret-0123456789"
        token = _make_jwt(self._claims(), key=secret, alg="HS256")
        v = self._verifier(algorithms=["HS256"], hs256_secret=secret)
        self.assertEqual(v.verify_token(token).subject, "user:alice")

    def test_tampered_signature(self):
        token = _make_jwt(self._claims(), key=self.rsa_key)
        head, payload, sig = token.split(".")
        tampered = head + "." + payload + "." + ("A" * len(sig))
        v = self._verifier(algorithms=["RS256"])
        v.jwks = type("J", (), {"get": staticmethod(lambda kid: self.rsa_key.public_key())})()
        from lokahi_oauth import OAuthError
        with self.assertRaises(OAuthError):
            v.verify_token(tampered)


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography not installed")
class TestOAuthOverHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        import time

        from cryptography.hazmat.primitives.asymmetric import rsa
        from lokahi_http import build_server
        from lokahi_oauth import JWTVerifier, ResourceMetadata

        cls.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.now = int(time.time())
        cls.issuer = "https://idp.example/realms/water"
        cls.audience = "https://lokahi.example/mcp"
        verifier = JWTVerifier(issuers=[cls.issuer], audience=cls.audience, algorithms=["RS256"])
        verifier.jwks = type("J", (), {"get": staticmethod(lambda kid: cls.rsa_key.public_key())})()

        signer = Signer(KEY)
        cls.gw = LokahiMCPGateway(_manifest(signer), signer, approvers={}, approval_store=ApprovalStore())
        cls.server = build_server(
            cls.gw, "127.0.0.1", 0,
            oauth_verifier=verifier,
            resource_metadata=ResourceMetadata(
                resource=cls.audience, authorization_servers=[cls.issuer]
            ),
        )
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _claims(self, **over):
        base = {"iss": self.issuer, "aud": self.audience, "sub": "user:alice",
                "exp": self.now + 300, "nbf": self.now - 5}
        base.update(over)
        return base

    def _post(self, body, token=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request("POST", "/mcp", json.dumps(body), headers)
        response = conn.getresponse()
        payload = response.read().decode()
        headers_out = dict(response.getheaders())
        conn.close()
        return response.status, payload, headers_out

    def test_metadata_endpoint(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/.well-known/oauth-protected-resource")
        response = conn.getresponse()
        doc = json.loads(response.read().decode())
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(doc["resource"], self.audience)
        self.assertEqual(doc["authorization_servers"], [self.issuer])

    def test_missing_token_is_401_with_challenge(self):
        status, _, headers = self._post(_call("read_meter", {"meter_id": "M-1"}))
        self.assertEqual(status, 401)
        self.assertIn("WWW-Authenticate", headers)
        self.assertIn("resource_metadata=", headers["WWW-Authenticate"])

    def test_valid_token_allows(self):
        token = _make_jwt(self._claims(), key=self.rsa_key)
        status, body, _ = self._post(_call("read_meter", {"meter_id": "M-1"}), token=token)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["allowed"])

    def test_expired_token_rejected(self):
        token = _make_jwt(self._claims(exp=self.now - 60), key=self.rsa_key)
        status, body, _ = self._post(_call("read_meter", {"meter_id": "M-1"}), token=token)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["reason"], "token_expired")

    def test_consumer_identity_reaches_ledger(self):
        token = _make_jwt(self._claims(client_id="team:water-ops"), key=self.rsa_key)
        self._post(_call("read_meter", {"meter_id": "M-1"}), token=token)
        self.assertEqual(self.gw.ledger.entries[-1]["requester"], "consumer:team:water-ops")


# ---------------------------------------------------------------------------
# R1-10 per-consumer tool ACL
# ---------------------------------------------------------------------------

class TestToolACL(unittest.TestCase):
    def test_exact_and_wildcard(self):
        from lokahi_acl import ToolACL
        acl = ToolACL.from_dict({
            "default": "deny",
            "consumers": {
                "team:a": {"allow": ["read_meter"]},
                "team:b": {"allow": ["read_*"]},
                "team:c": {"allow": ["read_meter", "email_author"], "deny": ["email_*"]},
            },
        })
        self.assertTrue(acl.allows("team:a", "read_meter"))
        self.assertFalse(acl.allows("team:a", "email_author"))
        self.assertTrue(acl.allows("team:b", "read_meter"))
        self.assertFalse(acl.allows("team:c", "email_author"))
        self.assertFalse(acl.allows("team:unknown", "read_meter"))

    def test_default_allow(self):
        from lokahi_acl import ToolACL
        acl = ToolACL.from_dict({"default": "allow", "consumers": {"team:a": {"deny": ["read_meter"]}}})
        self.assertTrue(acl.allows("team:z", "read_meter"))
        self.assertFalse(acl.allows("team:a", "read_meter"))

    def test_gateway_denies_by_consumer(self):
        from lokahi_acl import ToolACL
        signer = Signer(KEY)
        acl = ToolACL.from_dict({"default": "deny", "consumers": {"team:a": {"allow": ["read_meter"]}}})
        gw = LokahiMCPGateway(_manifest(signer), signer, approvers={}, approval_store=ApprovalStore(),
                              tool_acl=acl)
        ok = gw.intercept(_call("read_meter", {"meter_id": "M-1"}), now=NOW, consumer="team:a")
        self.assertTrue(ok.get("allowed"), ok)
        denied = gw.intercept(_call("email_author", {"meter_id": "M-1"}), now=NOW, consumer="team:a")
        self.assertFalse(denied.get("allowed"))
        self.assertIn("tool_acl_denied:team:a:email_author", denied["error"]["data"]["reasons"])




# ---------------------------------------------------------------------------
# R1-06 / R1-09 / R1-11: approval status, roles, consent groups
# ---------------------------------------------------------------------------

class TestApprovalStatusProtocol(unittest.TestCase):
    def setUp(self):
        self.signer = Signer(KEY)
        self.gm = LokahiMCPGateway(_manifest(self.signer), self.signer,
                                   approvers={}, approval_store=ApprovalStore())

    def test_escalation_carries_pending_status(self):
        result = self.gm.intercept(_call("email_author", {"meter_id": "M-1"}), now=NOW)
        self.assertFalse(result["allowed"])
        data = result["error"]["data"]
        self.assertTrue(data.get("input_required"))
        self.assertEqual(data["_meta"]["approval_status"], "pending")
        self.assertEqual(data["_meta"]["action_digest"], data["action_digest"])

    def test_allow_has_no_pending_metadata(self):
        result = self.gm.intercept(_call("read_meter", {"meter_id": "M-1"}), now=NOW)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["lokahi_receipt"]["_meta"]["approval_status"], "not_requested")
        self.assertNotIn("input_required", result["lokahi_receipt"])

    def test_timed_out_consent_is_not_input_required(self):
        self.gm.approval_window_seconds = 1
        first = self.gm.intercept(_call("email_author", {"meter_id": "M-1"}), now=NOW)
        self.assertTrue(first["error"]["data"]["input_required"])
        timed_out = self.gm.intercept(
            _call("email_author", {"meter_id": "M-1"}, 2), now=NOW + 2
        )
        self.assertFalse(timed_out["allowed"])
        self.assertEqual(timed_out["error"]["data"]["_meta"]["approval_status"], "timed_out")
        self.assertNotIn("input_required", timed_out["error"]["data"])

    def test_safety_refusal_is_refused_not_pending(self):
        # Unknown tools are hard refusals; no action may be resumed by approval.
        refused = self.gm.intercept(_call("not_in_policy", {}), now=NOW)
        self.assertFalse(refused["allowed"])
        self.assertEqual(refused["error"]["data"]["_meta"]["approval_status"], "refused")
        self.assertNotIn("input_required", refused["error"]["data"])


class TestRoles(unittest.TestCase):
    def setUp(self):
        from lokahi_acl import ToolACL

        self.acl = ToolACL.from_dict({
            "default": "deny",
            "consumers": {
                "team:ops": {"role": "operator", "allow": ["read_meter"]},
                "audit:alice": {"role": "auditor"},
                "board:bob": {"role": "steward", "allow": ["read_meter"]},
            },
        })

    def test_auditor_cannot_invoke(self):
        allowed, reason = self.acl.may_invoke("audit:alice", "read_meter")
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("role_cannot_invoke:auditor"))

    def test_operator_can_invoke_listed_tool(self):
        self.assertEqual(self.acl.may_invoke("team:ops", "read_meter"), (True, ""))

    def test_steward_role_still_subject_to_tool_list(self):
        allowed, reason = self.acl.may_invoke("board:bob", "email_author")
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("tool_acl_denied"))

    def test_gateway_enforces_auditor_role(self):
        signer = Signer(KEY)
        gw = LokahiMCPGateway(_manifest(signer), signer, approvers={}, approval_store=ApprovalStore(),
                              tool_acl=self.acl)
        result = gw.intercept(_call("read_meter", {"meter_id": "M-1"}), now=NOW,
                              consumer="audit:alice")
        self.assertFalse(result["allowed"])
        self.assertTrue(any("role_cannot_invoke" in r for r in result["error"]["data"]["reasons"]))

    def test_auditor_cannot_approve(self):
        allowed, reason = self.acl.may_approve("audit:alice")
        self.assertFalse(allowed)
        self.assertEqual(reason, "role_cannot_approve:auditor:audit:alice")
        self.assertEqual(self.acl.may_approve("team:ops"),
                         (False, "role_cannot_approve:operator:team:ops"))
        self.assertTrue(self.acl.may_approve("board:bob")[0])

    def test_role_mode_rejects_unassigned_approver(self):
        allowed, reason = self.acl.may_approve("unknown:person")
        self.assertFalse(allowed)
        self.assertEqual(reason, "role_unassigned_cannot_approve:unknown:person")

    def test_empty_exact_identity_does_not_inherit_wildcard_role(self):
        from lokahi_acl import ToolACL

        acl = ToolACL.from_dict({
            "default": "deny",
            "consumers": {"*": {"allow": ["read_meter"]}, "team:explicit": {}},
        })
        self.assertIsNone(acl.role_of("team:explicit"))


class TestConsentGroups(unittest.TestCase):
    def test_threshold_keygen_records_group_membership(self):
        import tempfile
        import lokahi_sign

        with tempfile.TemporaryDirectory() as temp:
            rc = lokahi_sign.main([
                "keygen", "--threshold", "2", "--n", "3", "--group", "water-board",
                "--out-dir", temp,
            ])
            self.assertEqual(rc, 0)
            with open(os.path.join(temp, "approvers.json"), encoding="utf-8") as stream:
                config = json.load(stream)
            self.assertEqual(config["groups"]["water-board"], sorted(config["validators"]))

    def test_only_group_members_count(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold={"m": 2, "n": 3, "group": "water-board"})
        verifiers, seeds = {}, {}
        for i in range(4):
            s = Ed25519Signer.generate()
            seeds[f"v:{i}"] = s.private_bytes().hex()
            verifiers[f"v:{i}"] = Ed25519Verifier.from_public_bytes(s.public_bytes())
        groups = {"water-board": ["v:0", "v:1", "v:2"]}  # v:3 is outside
        cf = ConsentFramework(manifest, verifiers, threshold=Threshold(2, 3), groups=groups)
        store = ApprovalStore()
        gw = LokahiMCPGateway(manifest, signer, approvers={}, approval_store=store,
                             consent_framework=cf)
        first = gw.intercept(_call("email_author", {"meter_id": "M-1"}), now=NOW)
        digest = first["error"]["data"]["action_digest"]

        # One member and one outsider: only the member counts.
        for op in ("v:2", "v:3"):
            s = Ed25519Signer.from_private_bytes(bytes.fromhex(seeds[op]))
            store.add(Approval(op, "d", "yes", digest, "2030-01-01T00:00:00Z", f"n-{op}").sign(s))
        still = gw.intercept(_call("email_author", {"meter_id": "M-1"}, 2), now=NOW)
        # v:2 is in the group, v:3 is not -> only one counts.
        self.assertEqual(still["error"]["data"]["quorum_met"], 1)

        # A second member tips it.
        s = Ed25519Signer.from_private_bytes(bytes.fromhex(seeds["v:1"]))
        store.add(Approval("v:1", "d", "yes", digest, "2030-01-01T00:00:00Z", "n-v1").sign(s))
        final = gw.intercept(_call("email_author", {"meter_id": "M-1"}, 3), now=NOW)
        self.assertTrue(final.get("allowed"), final)
        self.assertEqual(final["lokahi_receipt"]["_meta"]["approval_status"], "approved")

    def test_manifest_group_without_membership_is_rejected(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold={"m": 2, "n": 3, "group": "water-board"})
        with self.assertRaises(ValueError):
            ConsentFramework(manifest, {"v:0": Ed25519Verifier.from_public_bytes(
                Ed25519Signer.generate().public_bytes())}, threshold=Threshold(2, 3))

    def test_group_must_match_manifest_n_and_registered_keys(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold={"m": 2, "n": 3, "group": "water-board"})
        verifiers = {}
        for i in range(4):
            verifiers[f"v:{i}"] = Ed25519Verifier.from_public_bytes(
                Ed25519Signer.generate().public_bytes()
            )
        with self.assertRaisesRegex(ValueError, "threshold n"):
            ConsentFramework(
                manifest, verifiers, threshold=Threshold(2, 3),
                groups={"water-board": ["v:0", "v:1"]},
            )
        with self.assertRaisesRegex(ValueError, "without validator keys"):
            ConsentFramework(
                manifest, verifiers, threshold=Threshold(2, 3),
                groups={"water-board": ["v:0", "v:1", "missing"]},
            )

    def test_from_manifest_uses_validator_config_threshold_when_manifest_omits_it(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold=None)
        verifiers = {}
        for i in range(2):
            verifiers[f"v:{i}"] = Ed25519Verifier.from_public_bytes(
                Ed25519Signer.generate().public_bytes()
            )
        framework = ConsentFramework.from_manifest(
            manifest, verifiers, threshold=Threshold(1, 2)
        )
        self.assertEqual((framework.threshold.m, framework.threshold.n), (1, 2))

    def test_from_manifest_rejects_threshold_conflicting_with_signed_policy(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold={"m": 2, "n": 3})
        verifiers = {}
        for i in range(3):
            verifiers[f"v:{i}"] = Ed25519Verifier.from_public_bytes(
                Ed25519Signer.generate().public_bytes()
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            ConsentFramework.from_manifest(
                manifest, verifiers, threshold=Threshold(1, 3)
            )

    def test_role_policy_is_applied_to_signed_approvals(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold={"m": 2, "n": 2})
        validators, private = {}, {}
        for operator in ("v:steward", "v:auditor"):
            key = Ed25519Signer.generate()
            private[operator] = key
            validators[operator] = Ed25519Verifier.from_public_bytes(key.public_bytes())
        framework = ConsentFramework(
            manifest,
            validators,
            threshold=Threshold(2, 2),
            operator_roles={"v:steward": "steward", "v:auditor": "auditor"},
        )
        from lokahi import Action, compute_action_digest

        action = Action("agent:x", "email_author", ["meter_id"], "IN-DL", reversible=False)
        digest = compute_action_digest(manifest, action)
        approvals = [
            Approval(op, "device", "yes", digest, "2030-01-01T00:00:00Z", op).sign(key)
            for op, key in private.items()
        ]
        result = framework.check_consent(action, approvals, now=NOW)
        self.assertFalse(result.consent_met)
        self.assertEqual(result.yes_operators, ["v:steward"])
        self.assertEqual(result.quorum_met, 1)

    def test_unassigned_validator_does_not_count_in_role_mode(self):
        signer = Signer(KEY)
        manifest = _manifest(signer, threshold={"m": 2, "n": 2})
        private, validators = {}, {}
        for operator in ("v:steward", "v:unassigned"):
            key = Ed25519Signer.generate()
            private[operator] = key
            validators[operator] = Ed25519Verifier.from_public_bytes(key.public_bytes())
        framework = ConsentFramework(
            manifest, validators, threshold=Threshold(2, 2),
            operator_roles={"v:steward": "steward"},
        )
        from lokahi import Action, compute_action_digest

        action = Action("agent:x", "email_author", ["meter_id"], "IN-DL", reversible=False)
        digest = compute_action_digest(manifest, action)
        approvals = [
            Approval("v:unassigned", "device", "yes", digest, "2030-01-01T00:00:00Z", "n").sign(
                private["v:unassigned"]
            )
        ]
        result = framework.check_consent(action, approvals, now=NOW)
        self.assertFalse(result.consent_met)
        self.assertEqual(result.quorum_met, 0)


if __name__ == "__main__":
    unittest.main()
