# -*- coding: utf-8 -*-
"""Telemetry and SIEM export for LOKAHI decisions.

Every decision can be emitted to one or more sinks:

* **OTLP/HTTP** (JSON) — ``/v1/logs`` log records carrying the decision as
  attributes, so it lands in any OpenTelemetry collector.
* **Webhook** — a plain JSON POST, for a SIEM, SOAR playbook or an internal bus.
* **Splunk HEC** — ``/services/collector/event`` with ``Authorization: Splunk``.
* **Elastic** — ``_bulk`` NDJSON with an API key.

Delivery rules, deliberately:

* **Fail-open for telemetry, never for decisions.** An export failure is counted
  and swallowed; it must not change a verdict.
* **Asynchronous.** One worker thread and a bounded queue; if the queue is full,
  events are dropped and counted rather than blocking the request path.
* **No secrets in payloads.** The event carries identities, tool names, verdicts
  and reasons — never argument values.
* **Flush on demand.** ``flush(timeout)`` for tests and shutdown.

Standard library only.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_QUEUE_SIZE = 2048
DEFAULT_TIMEOUT = 3.0


@dataclass
class DecisionEvent:
    ts: float
    node: str
    tool: str
    verdict: str
    reasons: list[str]
    requester: str
    quorum_required: int = 0
    quorum_met: int = 0
    action_digest: str = ""
    ledger_seq: int = -1
    ledger_hash: str = ""
    actuator_interlock: str = ""
    consumer: str = ""
    layer: str = "gateway"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "node": self.node,
            "tool": self.tool,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "requester": self.requester,
            "quorum_required": self.quorum_required,
            "quorum_met": self.quorum_met,
            "action_digest": self.action_digest,
            "ledger_seq": self.ledger_seq,
            "ledger_hash": self.ledger_hash,
            "actuator_interlock": self.actuator_interlock,
            "consumer": self.consumer,
            "layer": self.layer,
        }


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------

class Sink:
    name = "sink"

    def send(self, event: DecisionEvent) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def _http_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


class WebhookSink(Sink):
    """Generic JSON webhook."""

    name = "webhook"

    def __init__(self, url: str, headers: Optional[dict[str, str]] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.timeout = timeout

    def send(self, event: DecisionEvent) -> None:
        _http_post(self.url, json.dumps(event.as_dict()).encode("utf-8"), self.headers, self.timeout)


class SplunkHECSink(Sink):
    """Splunk HTTP Event Collector."""

    name = "splunk_hec"

    def __init__(self, url: str, token: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url.rstrip("/")
        if not self.url.endswith("/services/collector/event"):
            self.url += "/services/collector/event"
        self.token = token
        self.timeout = timeout

    def send(self, event: DecisionEvent) -> None:
        body = json.dumps({"event": event.as_dict(), "sourcetype": "lokahi:decision"}).encode()
        _http_post(
            self.url,
            body,
            {"Content-Type": "application/json", "Authorization": f"Splunk {self.token}"},
            self.timeout,
        )


class ElasticSink(Sink):
    """Elasticsearch ``_bulk`` indexer."""

    name = "elastic"

    def __init__(self, url: str, api_key: Optional[str] = None, index: str = "lokahi-decisions",
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.index = index
        self.timeout = timeout

    def send(self, event: DecisionEvent) -> None:
        lines = [
            json.dumps({"index": {"_index": self.index}}),
            json.dumps(event.as_dict()),
        ]
        body = ("\n".join(lines) + "\n").encode("utf-8")
        headers = {"Content-Type": "application/x-ndjson"}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        _http_post(f"{self.url}/_bulk", body, headers, self.timeout)


class OTLPSink(Sink):
    """OTLP/HTTP JSON log exporter.

    Emits one log record per decision under a ``lokahi.decision`` scope. This is
    the shape a collector accepts; attribute values are strings and ints only.
    """

    name = "otlp"

    def __init__(self, endpoint: str, service_name: str = "lokahi",
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/v1/logs"):
            self.endpoint += "/v1/logs"
        self.service_name = service_name
        self.timeout = timeout

    def _payload(self, event: DecisionEvent) -> dict[str, Any]:
        attributes = [
            {"key": "lokahi.tool", "value": {"stringValue": event.tool}},
            {"key": "lokahi.verdict", "value": {"stringValue": event.verdict}},
            {"key": "lokahi.reasons", "value": {"stringValue": ",".join(event.reasons)}},
            {"key": "lokahi.requester", "value": {"stringValue": event.requester}},
            {"key": "lokahi.consumer", "value": {"stringValue": event.consumer}},
            {"key": "lokahi.quorum_required", "value": {"intValue": int(event.quorum_required)}},
            {"key": "lokahi.quorum_met", "value": {"intValue": int(event.quorum_met)}},
            {"key": "lokahi.ledger_seq", "value": {"intValue": int(event.ledger_seq)}},
            {"key": "lokahi.ledger_hash", "value": {"stringValue": event.ledger_hash}},
            {"key": "lokahi.action_digest", "value": {"stringValue": event.action_digest}},
        ]
        if event.actuator_interlock:
            attributes.append(
                {"key": "lokahi.actuator_interlock",
                 "value": {"stringValue": event.actuator_interlock}}
            )
        return {
            "resourceLogs": [{
                "resource": {"attributes": [
                    {"key": "service.name", "value": {"stringValue": self.service_name}},
                    {"key": "lokahi.node", "value": {"stringValue": event.node}},
                ]},
                "scopeLogs": [{
                    "scope": {"name": "lokahi.decision", "version": "1"},
                    "logRecords": [{
                        "timeUnixNano": str(int(event.ts * 1_000_000_000)),
                        "severityText": "INFO" if event.verdict == "compliant" else "WARN",
                        "body": {"stringValue": f"{event.tool} -> {event.verdict}"},
                        "attributes": attributes,
                    }],
                }],
            }]
        }

    def send(self, event: DecisionEvent) -> None:
        body = json.dumps(self._payload(event)).encode("utf-8")
        _http_post(self.endpoint, body, {"Content-Type": "application/json"}, self.timeout)


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------

@dataclass
class TelemetryStats:
    queued: int = 0
    sent: int = 0
    failed: int = 0
    dropped: int = 0


class Telemetry:
    """Bounded, asynchronous fan-out to sinks. Never blocks a decision."""

    def __init__(self, sinks: Optional[list[Sink]] = None,
                 queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self.sinks = list(sinks or [])
        self.stats = TelemetryStats()
        self._queue: "queue.Queue[DecisionEvent]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if self.sinks:
            self._thread = threading.Thread(target=self._run, name="lokahi-telemetry", daemon=True)
            self._thread.start()

    # -- lifecycle -----------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._deliver(event)
            self._queue.task_done()

    def _deliver(self, event: DecisionEvent) -> None:
        for sink in self.sinks:
            try:
                sink.send(event)
                self.stats.sent += 1
            except Exception:  # noqa: BLE001 - telemetry must never raise
                self.stats.failed += 1

    def emit(self, event: DecisionEvent) -> None:
        if not self.sinks:
            return
        try:
            self._queue.put_nowait(event)
            self.stats.queued += 1
        except queue.Full:
            self.stats.dropped += 1

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for queued events to be delivered. Returns False on timeout."""
        if not self.sinks:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty():
                return True
            time.sleep(0.02)
        return self._queue.empty()

    def close(self) -> None:
        self.flush()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    # -- construction ---------------------------------------------------
    @classmethod
    def from_env(cls) -> Optional["Telemetry"]:
        """Build from environment variables. Returns None when unconfigured."""
        sinks: list[Sink] = []
        if url := os.environ.get("LOKAHI_OTEL_ENDPOINT"):
            sinks.append(OTLPSink(url, service_name=os.environ.get("LOKAHI_SERVICE_NAME", "lokahi")))
        if url := os.environ.get("LOKAHI_SIEM_WEBHOOK"):
            sinks.append(WebhookSink(url))
        splunk_url = os.environ.get("LOKAHI_SPLUNK_HEC_URL")
        splunk_token = os.environ.get("LOKAHI_SPLUNK_HEC_TOKEN")
        if splunk_url and splunk_token:
            sinks.append(SplunkHECSink(splunk_url, splunk_token))
        elastic_url = os.environ.get("LOKAHI_ELASTIC_URL")
        if elastic_url:
            sinks.append(ElasticSink(elastic_url, api_key=os.environ.get("LOKAHI_ELASTIC_API_KEY")))
        if not sinks:
            return None
        return cls(sinks)
