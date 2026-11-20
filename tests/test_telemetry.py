# -*- coding: utf-8 -*-
"""Tests for telemetry and SIEM export (R1-17/R1-18).

Run: python3 tests/test_telemetry.py
"""
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

from lokahi import Manifest, Signer  # noqa: E402
from lokahi_telemetry import (  # noqa: E402
    DecisionEvent,
    ElasticSink,
    OTLPSink,
    SplunkHECSink,
    Telemetry,
    WebhookSink,
)
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway  # noqa: E402

KEY = b"telemetry-test-key-000000000000000"
NOW = 1_800_000_000.0


class Capture:
    """A tiny HTTP server that records the last request per path."""

    def __init__(self):
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with capture.lock:
                    capture.requests.append({
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    })
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def wait_for(self, count=1, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if len(self.requests) >= count:
                    return list(self.requests)
            time.sleep(0.01)
        with self.lock:
            return list(self.requests)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _event(**over) -> DecisionEvent:
    base = dict(
        ts=NOW, node="n", tool="read_meter", verdict="compliant",
        reasons=["within_manifest"], requester="consumer:team:a",
        quorum_required=0, quorum_met=0, action_digest="abc",
        ledger_seq=3, ledger_hash="f" * 64, actuator_interlock="pass",
    )
    base.update(over)
    return DecisionEvent(**base)


class TestSinks(unittest.TestCase):
    def setUp(self):
        self.capture = Capture()

    def tearDown(self):
        self.capture.close()

    def test_webhook(self):
        sink = WebhookSink(self.capture.url + "/hook")
        sink.send(_event())
        req = self.capture.wait_for(1)[0]
        self.assertEqual(req["path"], "/hook")
        self.assertEqual(json.loads(req["body"])["tool"], "read_meter")

    def test_splunk_hec(self):
        sink = SplunkHECSink(self.capture.url, "tok-123")
        sink.send(_event(verdict="forbidden"))
        req = self.capture.wait_for(1)[0]
        self.assertEqual(req["path"], "/services/collector/event")
        self.assertEqual(req["headers"]["Authorization"], "Splunk tok-123")
        self.assertEqual(json.loads(req["body"])["event"]["verdict"], "forbidden")

    def test_elastic_bulk(self):
        sink = ElasticSink(self.capture.url, api_key="key-1", index="lokahi-test")
        sink.send(_event())
        req = self.capture.wait_for(1)[0]
        self.assertEqual(req["path"], "/_bulk")
        self.assertEqual(req["headers"]["Authorization"], "ApiKey key-1")
        lines = [json.loads(l) for l in req["body"].decode().strip().split("\n")]
        self.assertEqual(lines[0]["index"]["_index"], "lokahi-test")
        self.assertEqual(lines[1]["tool"], "read_meter")

    def test_otlp_log_shape(self):
        sink = OTLPSink(self.capture.url)
        sink.send(_event(verdict="escalate", quorum_required=2, quorum_met=1))
        req = self.capture.wait_for(1)[0]
        self.assertEqual(req["path"], "/v1/logs")
        doc = json.loads(req["body"])
        record = doc["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        attrs = {a["key"]: list(a["value"].values())[0] for a in record["attributes"]}
        self.assertEqual(attrs["lokahi.verdict"], "escalate")
        self.assertEqual(attrs["lokahi.quorum_met"], 1)
        self.assertEqual(record["severityText"], "WARN")


class TestTelemetryFanout(unittest.TestCase):
    def setUp(self):
        self.capture = Capture()

    def tearDown(self):
        self.capture.close()

    def test_fanout_and_flush(self):
        telemetry = Telemetry([WebhookSink(self.capture.url + "/a"), WebhookSink(self.capture.url + "/b")])
        try:
            telemetry.emit(_event())
            self.assertTrue(telemetry.flush(3.0))
            self.assertEqual(len(self.capture.wait_for(2)), 2)
            self.assertEqual(telemetry.stats.sent, 2)
            self.assertEqual(telemetry.stats.failed, 0)
        finally:
            telemetry.close()

    def test_failure_is_counted_not_raised(self):
        telemetry = Telemetry([WebhookSink("http://127.0.0.1:9/none", timeout=0.2)])
        try:
            telemetry.emit(_event())
            self.assertTrue(telemetry.flush(3.0))
            self.assertEqual(telemetry.stats.failed, 1)
            self.assertEqual(telemetry.stats.sent, 0)
        finally:
            telemetry.close()

    def test_no_sinks_is_a_noop(self):
        telemetry = Telemetry([])
        telemetry.emit(_event())
        self.assertTrue(telemetry.flush(0.1))
        self.assertEqual(telemetry.stats.queued, 0)

    def test_queue_overflow_drops_without_blocking(self):
        telemetry = Telemetry([WebhookSink("http://127.0.0.1:9/x", timeout=0.2)], queue_size=2)
        try:
            for _ in range(50):
                telemetry.emit(_event())
            self.assertGreater(telemetry.stats.dropped, 0)
        finally:
            telemetry.close()


class TestGatewayEmission(unittest.TestCase):
    def test_decision_emits_event(self):
        capture = Capture()
        signer = Signer(KEY)
        manifest = Manifest(
            node="telemetry-node",
            informational=["meter_id"],
            forbidden_targets=[],
            jurisdiction="IN-DL",
            residency=["IN"],
            exceptions={"safety_critical": 1, "window_hours": 24},
            expires="2030-01-01T00:00:00Z",
            steward="did:web:t",
            tools={"read_meter": {"class": "read", "args": {"meter_id": "meter_id"}}},
        ).sign(signer)

        telemetry = Telemetry([WebhookSink(capture.url + "/decisions")])
        gw = LokahiMCPGateway(
            manifest, signer, approvers={}, approval_store=ApprovalStore(), telemetry=telemetry
        )
        try:
            result = gw.intercept(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "read_meter", "arguments": {"meter_id": "M-1"}}},
                now=NOW,
            )
            self.assertTrue(result.get("allowed"))
            self.assertTrue(telemetry.flush(3.0))
            reqs = capture.wait_for(1)
            self.assertTrue(reqs)
            payload = json.loads(reqs[0]["body"])
            self.assertEqual(payload["tool"], "read_meter")
            self.assertEqual(payload["verdict"], "compliant")
            self.assertEqual(payload["node"], "telemetry-node")
            self.assertEqual(payload["ledger_seq"], 0)
        finally:
            telemetry.close()
            capture.close()


class TestFromEnv(unittest.TestCase):
    def test_unconfigured_returns_none(self):
        saved = {k: os.environ.pop(k, None) for k in (
            "LOKAHI_OTEL_ENDPOINT", "LOKAHI_SIEM_WEBHOOK",
            "LOKAHI_SPLUNK_HEC_URL", "LOKAHI_ELASTIC_URL")}
        try:
            self.assertIsNone(Telemetry.from_env())
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_configured_builds_sinks(self):
        os.environ["LOKAHI_OTEL_ENDPOINT"] = "http://127.0.0.1:4318"
        try:
            t = Telemetry.from_env()
            self.assertIsNotNone(t)
            self.assertEqual([s.name for s in t.sinks], ["otlp"])
            t.close()
        finally:
            os.environ.pop("LOKAHI_OTEL_ENDPOINT", None)


if __name__ == "__main__":
    unittest.main()
