# -*- coding: utf-8 -*-
"""Streamable HTTP transport for the LOKAHI gateway.

Implements the server side of the MCP Streamable HTTP shape:

* one endpoint (default ``/mcp``) accepting JSON-RPC over ``POST``
* responds with ``application/json`` by default, or a single-message
  ``text/event-stream`` when the client sends ``Accept: text/event-stream``
* stateless: no session identifier is issued or required, matching the
  2026-07-28 revision's removal of protocol-level sessions
* ``GET``/``DELETE`` on the endpoint return 405 (this server does not initiate
  streams and holds no session to delete)

Not yet implemented, and deliberately refused rather than faked:

* OAuth 2.1 resource-server behaviour (RFC 9728 protected-resource metadata,
  PKCE, RFC 8707 resource indicators). Until that lands (R1-02) callers present
  a bearer token from an allowlist.
* Batch requests and server-initiated notifications.
* TLS termination: run behind a reverse proxy, or configure the caller's proxy.

The gateway is not thread-safe internally, so decisions are serialized by a lock
while health and metrics endpoints stay responsive. Horizontal scaling is an R2
item; this server is for a single node.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

DEFAULT_PATH = "/mcp"
MAX_BODY_BYTES = 1 * 1024 * 1024


class HttpTransportError(RuntimeError):
    """The transport was configured in a way that cannot be served safely."""


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def make_handler(gateway, *, path: str = DEFAULT_PATH, bearer_tokens: Optional[list[str]] = None):
    """Build a request handler class bound to one gateway.

    `bearer_tokens`, when set, is the allowlist of accepted ``Authorization:
    Bearer`` values. This is a stop-gap until OAuth 2.1 resource-server support
    (R1-02); an empty list means the transport must not be exposed publicly.
    """
    tokens = set(bearer_tokens or [])
    decision_lock = threading.Lock()
    started_at = time.time()
    stats = {"requests": 0, "decisions": 0, "refusals": 0, "errors": 0, "auth_failures": 0}
    stats_lock = threading.Lock()

    def bump(key: str) -> None:
        with stats_lock:
            stats[key] = stats.get(key, 0) + 1

    class Handler(BaseHTTPRequestHandler):
        server_version = "LOKAHI/1.1"
        protocol_version = "HTTP/1.1"

        # -- helpers ---------------------------------------------------
        def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:  # client hung up
                pass

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, _json_bytes(obj))

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter default
            if os.environ.get("LOKAHI_HTTP_VERBOSE"):
                super().log_message(fmt, *args)

        def _authorized(self) -> bool:
            if not tokens:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            return header[len("Bearer "):].strip() in tokens

        # -- routes ----------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path in ("/healthz", "/health"):
                self._json(200, {"status": "ok", "uptime_s": int(time.time() - started_at)})
                return
            if self.path in ("/readyz", "/ready"):
                ready = True
                if hasattr(gateway, "failsecure") and gateway.failsecure is not None:
                    ready = gateway.failsecure.has_privilege()
                body = {"ready": ready}
                self._json(200 if ready else 503, body)
                return
            if self.path == "/metrics":
                with stats_lock:
                    snapshot = dict(stats)
                lines = [f"lokahi_{k}_total {v}" for k, v in sorted(snapshot.items())]
                lines.append(f"lokahi_uptime_seconds {int(time.time() - started_at)}")
                self._send(200, ("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4")
                return
            if self.path == path:
                self._json(405, {"error": "method_not_allowed",
                                 "detail": "this server does not initiate streams"})
                return
            self._json(404, {"error": "not_found"})

        def do_DELETE(self) -> None:  # noqa: N802
            if self.path == path:
                self._json(405, {"error": "method_not_allowed",
                                 "detail": "stateless server; no session to delete"})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != path:
                self._json(404, {"error": "not_found"})
                return
            bump("requests")

            if not self._authorized():
                bump("auth_failures")
                self._json(401, {"error": "unauthorized"})
                return

            length_header = self.headers.get("Content-Length")
            if length_header is None:
                self._json(411, {"error": "length_required"})
                return
            try:
                length = int(length_header)
            except ValueError:
                self._json(400, {"error": "bad_length"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._json(413, {"error": "body_too_large", "limit": MAX_BODY_BYTES})
                return

            raw = self.rfile.read(length)
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._json(400, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "parse error"}})
                return

            if isinstance(message, list):
                # Batch is deliberately unsupported rather than half-supported.
                self._json(400, {"error": "batch_not_supported",
                                 "detail": "send one JSON-RPC message per request"})
                return

            try:
                with decision_lock:
                    result = gateway.intercept(message)
            except Exception as exc:  # noqa: BLE001 - the gateway already handles its own errors
                bump("errors")
                self._json(500, {"error": "gateway_failure", "detail": type(exc).__name__})
                return

            if isinstance(result, dict) and result.get("allowed") is False:
                bump("refusals")
            else:
                bump("decisions")

            accept = self.headers.get("Accept", "")
            if "text/event-stream" in accept:
                payload = _json_bytes(result)
                stream = b"event: message\ndata: " + payload + b"\n\n"
                self._send(200, stream, "text/event-stream")
            else:
                self._send(200, _json_bytes(result))

    return Handler


def build_server(
    gateway,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    path: str = DEFAULT_PATH,
    bearer_tokens: Optional[list[str]] = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) a Streamable HTTP server for `gateway`."""
    if not isinstance(port, int) or not (0 <= port < 65536):
        raise HttpTransportError(f"invalid port: {port!r}")
    handler = make_handler(gateway, path=path, bearer_tokens=bearer_tokens)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(gateway, host: str, port: int, *, path: str = DEFAULT_PATH,
          bearer_tokens: Optional[list[str]] = None) -> None:
    """Run until interrupted."""
    server = build_server(gateway, host, port, path=path, bearer_tokens=bearer_tokens)
    try:
        print(f"lokahi: listening on http://{host}:{port}{path}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def parse_listen(value: str) -> tuple[str, int]:
    """Parse ``HOST:PORT`` (IPv6 in brackets allowed)."""
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        if end == -1 or not value[end + 1:].startswith(":"):
            raise HttpTransportError(f"invalid listen address: {value!r}")
        host, port_text = value[1:end], value[end + 2:]
    else:
        if ":" not in value:
            raise HttpTransportError("listen address must be HOST:PORT")
        host, port_text = value.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise HttpTransportError(f"invalid port in {value!r}") from exc
    return host or "127.0.0.1", port
