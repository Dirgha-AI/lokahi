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


def make_handler(
    gateway,
    *,
    path: str = DEFAULT_PATH,
    bearer_tokens: Optional[list[str]] = None,
    oauth_verifier=None,
    resource_metadata=None,
    approval_link_key: Optional[bytes] = None,
):
    """Build a request handler class bound to one gateway.

    `oauth_verifier`, when supplied, validates bearer JWTs (R1-02) and supplies
    the consumer identity for tool ACLs. `resource_metadata` is a
    `ResourceMetadata` used both for `/.well-known/oauth-protected-resource` and
    for the `WWW-Authenticate` challenge on 401.

    `bearer_tokens`, when set, is a static allowlist used only when no OAuth
    verifier is configured.
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
        def _send(
            self,
            code: int,
            body: bytes,
            content_type: str = "application/json",
            extra_headers: Optional[dict] = None,
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:  # client hung up
                pass

        def _json(self, code: int, obj: Any, extra_headers: Optional[dict] = None) -> None:
            self._send(code, _json_bytes(obj), extra_headers=extra_headers)

        def _handle_approve(self) -> None:
            import json as _json

            if approval_link_key is None:
                self._json(404, {"error": "approvals_not_configured"})
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._json(413, {"error": "body_too_large"})
                return
            try:
                payload = _json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError("body must be a JSON object")
                token = payload["token"]
                approval = payload["approval"]
            except (ValueError, KeyError, TypeError):
                self._json(400, {"error": "expected {token, approval}"})
                return
            if not isinstance(token, str) or not isinstance(approval, dict):
                self._json(400, {"error": "token must be a string and approval must be an object"})
                return

            try:
                from lokahi_notify import parse_link

                parsed = parse_link("", token, approval_link_key)
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"error": "bad_link", "detail": str(exc)})
                return

            if approval.get("operator") != parsed["operator"]:
                self._json(400, {"error": "operator_mismatch"})
                return
            if approval.get("action_digest") != parsed["digest"]:
                self._json(400, {"error": "digest_mismatch"})
                return
            if approval.get("decision") not in ("yes", "no"):
                self._json(400, {"error": "decision_must_be_yes_or_no"})
                return

            try:
                from lokahi import Approval

                record = Approval.from_dict(approval)
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": "malformed_approval", "detail": str(exc)})
                return

            authorize_approval = getattr(gateway, "authorize_approval", None)
            if callable(authorize_approval):
                allowed, reason = authorize_approval(record.operator)
                if not allowed:
                    bump("auth_failures")
                    self._json(403, {"error": "approval_forbidden", "reason": reason})
                    return
            else:
                acl = getattr(gateway, "tool_acl", None)
                if acl is not None and hasattr(acl, "may_approve"):
                    allowed, reason = acl.may_approve(record.operator)
                    if not allowed:
                        bump("auth_failures")
                        self._json(403, {"error": "approval_forbidden", "reason": reason})
                        return

            store = getattr(gateway, "approval_store", None)
            if store is None or not hasattr(store, "add"):
                self._json(501, {"error": "approval_store_unavailable"})
                return
            store.add(record)
            bump("decisions")
            self._json(200, {"accepted": True, "operator": record.operator,
                             "action_digest": record.action_digest,
                             "decision": record.decision})

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
            if resource_metadata is not None and self.path in (
                "/.well-known/oauth-protected-resource",
                f"/.well-known/oauth-protected-resource{path}",
            ):
                self._json(200, resource_metadata.as_dict())
                return
            if self.path.startswith("/approve"):
                if approval_link_key is None:
                    self._json(404, {"error": "approvals_not_configured"})
                    return
                from urllib.parse import parse_qs, urlparse

                token = (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
                try:
                    from lokahi_notify import parse_link

                    parsed = parse_link("", token, approval_link_key)
                except Exception as exc:  # noqa: BLE001
                    self._json(400, {"error": "bad_link", "detail": str(exc)})
                    return
                page = (
                    "<!doctype html><meta charset=utf-8>"
                    "<title>LOKAHI approval</title>"
                    "<body style=\'font-family:system-ui;max-width:44rem;margin:3rem auto\'>"
                    "<h1>Approval requested</h1>"
                    f"<p><b>Operator:</b> {parsed['operator']}<br>"
                    f"<b>Action digest:</b> <code>{parsed['digest']}</code></p>"
                    "<p>This page authenticates the <i>request</i>. Approving requires your own key:</p>"
                    f"<pre>python tools/lokahi_approve.py --key-file your.key "
                    f"--operator {parsed['operator']} --node your-device "
                    f"--digest {parsed['digest']} --decision yes "
                    f"--approvals approvals.jsonl</pre>"
                    "<p>Or POST JSON to this path: "
                    "<code>{\"token\": \"...\", \"approval\": {...}}</code></p>"
                    "</body>"
                )
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
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
            if self.path.startswith("/approve"):
                self._handle_approve()
                return
            if self.path != path:
                self._json(404, {"error": "not_found"})
                return
            bump("requests")

            consumer = None
            if oauth_verifier is not None:
                try:
                    claims = oauth_verifier.verify(self.headers.get("Authorization", ""))
                    consumer = claims.client_id
                except Exception as exc:  # noqa: BLE001 - any failure is a 401
                    bump("auth_failures")
                    headers = {}
                    if resource_metadata is not None:
                        headers["WWW-Authenticate"] = resource_metadata.www_authenticate()
                    reason = getattr(exc, "reason", "unauthorized")
                    self._json(
                        401,
                        {"error": "unauthorized", "reason": reason},
                        extra_headers=headers,
                    )
                    return
            elif not self._authorized():
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
                    if consumer is None:
                        result = gateway.intercept(message)
                    else:
                        result = gateway.intercept(message, consumer=consumer)
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
    oauth_verifier=None,
    resource_metadata=None,
    approval_link_key: Optional[bytes] = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) a Streamable HTTP server for `gateway`."""
    if not isinstance(port, int) or not (0 <= port < 65536):
        raise HttpTransportError(f"invalid port: {port!r}")
    handler = make_handler(
        gateway,
        path=path,
        bearer_tokens=bearer_tokens,
        oauth_verifier=oauth_verifier,
        resource_metadata=resource_metadata,
        approval_link_key=approval_link_key,
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(
    gateway,
    host: str,
    port: int,
    *,
    path: str = DEFAULT_PATH,
    bearer_tokens: Optional[list[str]] = None,
    oauth_verifier=None,
    resource_metadata=None,
    approval_link_key: Optional[bytes] = None,
) -> None:
    """Run until interrupted."""
    server = build_server(
        gateway,
        host,
        port,
        path=path,
        bearer_tokens=bearer_tokens,
        oauth_verifier=oauth_verifier,
        resource_metadata=resource_metadata,
        approval_link_key=approval_link_key,
    )
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
