# -*- coding: utf-8 -*-
"""Approval notifications and signed approval links (R1-20).

The gate cannot approve on a human's behalf, and it must not be able to. So the
inbox does two separate things:

1. **Notify** the right operator that an action is waiting, with the action
   digest, the tool, and an expiry.
2. **Authenticate the request**, not the answer: the link is an HMAC-signed
   token bound to ``(digest, operator, expiry)``. It proves the gateway asked
   *this* operator about *this* action. It does not sign the approval.

The operator's own Ed25519 key still signs the ``Approval`` (as with
``lokahi_approve.py``), and the gateway verifies that signature at decision time
exactly as before. A compromised mailbox can therefore read the request but
cannot approve it.

Channels: console, generic webhook, Slack, SMTP email. All are best effort; a
notification failure never changes a verdict.

Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import smtplib
import time
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Optional

LINK_PREFIX = "lokahi-approval/1"


class LinkError(ValueError):
    """An approval link is malformed, expired, or forged."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))


def _mac(key: bytes, digest: str, operator: str, expires_at: int) -> str:
    message = f"{LINK_PREFIX}|{digest}|{operator}|{expires_at}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def build_link(
    base_url: str,
    digest: str,
    operator: str,
    expires_at: int,
    key: bytes,
) -> str:
    """Build a signed approval link for one operator and one action."""
    if not key or len(key) < 16:
        raise LinkError("link key must be at least 16 bytes")
    token = _b64(
        json.dumps(
            {"d": digest, "o": operator, "e": int(expires_at), "m": _mac(key, digest, operator, int(expires_at))},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return f"{base_url.rstrip('/')}/approve?t={token}"


def parse_link(base_url: str, token: str, key: bytes, now: Optional[float] = None) -> dict:
    """Validate a link token and return ``{digest, operator, expires_at}``."""
    try:
        payload = json.loads(_unb64(token))
    except (ValueError, json.JSONDecodeError) as exc:
        raise LinkError("malformed token") from exc
    digest = payload.get("d", "")
    operator = payload.get("o", "")
    expires_at = int(payload.get("e", 0))
    mac = payload.get("m", "")
    if not (isinstance(digest, str) and isinstance(operator, str) and digest and operator):
        raise LinkError("malformed token")
    expected = _mac(key, digest, operator, expires_at)
    if not hmac.compare_digest(expected, str(mac)):
        raise LinkError("bad link signature")
    current = now if now is not None else time.time()
    if current > expires_at:
        raise LinkError("link expired")
    return {"digest": digest, "operator": operator, "expires_at": expires_at}


# ---------------------------------------------------------------------------
# Notices and channels
# ---------------------------------------------------------------------------

@dataclass
class Notice:
    digest: str
    tool: str
    operator: str
    expires_at: int
    node: str = ""
    reasons: list[str] = field(default_factory=list)
    link: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "lokahi.approval_requested",
            "node": self.node,
            "tool": self.tool,
            "operator": self.operator,
            "action_digest": self.digest,
            "expires_at": self.expires_at,
            "reasons": list(self.reasons),
            "link": self.link,
        }

    def text(self) -> str:
        return (
            f"LOKAHI approval requested\n"
            f"  node:     {self.node}\n"
            f"  tool:     {self.tool}\n"
            f"  operator: {self.operator}\n"
            f"  digest:   {self.digest}\n"
            f"  reasons:  {', '.join(self.reasons)}\n"
            f"  expires:  {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.expires_at))}\n"
            f"  open:     {self.link}\n"
            f"\n"
            f"Approving requires your own key. The link authenticates the request, not the answer.\n"
            f"  python tools/lokahi_approve.py --key-file <your.key> --operator {self.operator} \\\n"
            f"      --node <device> --digest {self.digest} --decision yes --approvals approvals.jsonl\n"
        )


class Channel:
    name = "channel"

    def send(self, notice: Notice) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleChannel(Channel):
    name = "console"

    def __init__(self, stream=None) -> None:
        self.stream = stream

    def send(self, notice: Notice) -> None:
        import sys

        (self.stream or sys.stderr).write(notice.text())

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.flush()
            except Exception:  # noqa: BLE001
                pass


class WebhookChannel(Channel):
    name = "webhook"

    def __init__(self, url: str, headers: Optional[dict[str, str]] = None, timeout: float = 3.0) -> None:
        self.url = url
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.timeout = timeout

    def send(self, notice: Notice) -> None:
        request = urllib.request.Request(
            self.url, data=json.dumps(notice.as_dict()).encode("utf-8"),
            method="POST", headers=self.headers,
        )
        with urllib.request.urlopen(request, timeout=self.timeout):
            pass


class SlackChannel(Channel):
    name = "slack"

    def __init__(self, webhook_url: str, timeout: float = 3.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, notice: Notice) -> None:
        text = (
            f"*LOKAHI approval requested* — `{notice.tool}` on `{notice.node}`\n"
            f"operator: `{notice.operator}`\n"
            f"digest: `{notice.digest}`\n"
            f"expires: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(notice.expires_at))}\n"
            f"<{notice.link}|Open approval request>"
        )
        request = urllib.request.Request(
            self.webhook_url,
            data=json.dumps({"text": text}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout):
            pass


class EmailChannel(Channel):
    name = "email"

    def __init__(self, host: str, port: int = 587, username: Optional[str] = None,
                 password: Optional[str] = None, sender: str = "lokahi@localhost",
                 use_tls: bool = True, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.use_tls = use_tls
        self.timeout = timeout

    def send(self, notice: Notice) -> None:  # pragma: no cover - needs a mail server
        message = EmailMessage()
        message["Subject"] = f"LOKAHI approval requested: {notice.tool}"
        message["From"] = self.sender
        message["To"] = notice.operator
        message.set_content(notice.text())
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
            if self.use_tls:
                client.starttls()
            if self.username and self.password:
                client.login(self.username, self.password)
            client.send_message(message)


# ---------------------------------------------------------------------------
# Notifier (fan-out per operator)
# ---------------------------------------------------------------------------

class Notifier:
    """Routes a notice to the channels configured for one operator."""

    def __init__(self, base_url: str, key: bytes, operators: dict[str, list[Channel]],
                 ttl_seconds: int = 3600) -> None:
        if not key or len(key) < 16:
            raise LinkError("link key must be at least 16 bytes")
        self.base_url = base_url
        self.key = key
        self.operators = operators
        self.ttl_seconds = int(ttl_seconds)
        self.stats = {"sent": 0, "failed": 0}

    @classmethod
    def from_config(cls, config: dict) -> "Notifier":
        key_text = config.get("link_key") or os.environ.get("LOKAHI_APPROVAL_LINK_KEY", "")
        if not key_text:
            raise LinkError("notifier needs a link_key (or LOKAHI_APPROVAL_LINK_KEY)")
        operators: dict[str, list[Channel]] = {}
        for operator, spec in (config.get("operators") or {}).items():
            channels: list[Channel] = []
            if isinstance(spec.get("email"), str) and config.get("smtp"):
                smtp = config["smtp"]
                channels.append(EmailChannel(
                    host=smtp["host"], port=int(smtp.get("port", 587)),
                    username=smtp.get("username"), password=smtp.get("password"),
                    sender=smtp.get("sender", "lokahi@localhost"),
                    use_tls=bool(smtp.get("use_tls", True)),
                ))
            if isinstance(spec.get("slack_webhook"), str):
                channels.append(SlackChannel(spec["slack_webhook"]))
            if isinstance(spec.get("webhook"), str):
                channels.append(WebhookChannel(spec["webhook"]))
            if not channels:
                channels.append(ConsoleChannel())
            operators[operator] = channels
        return cls(
            base_url=config.get("base_url", "http://127.0.0.1:8787"),
            key=key_text.encode("utf-8"),
            operators=operators,
            ttl_seconds=int(config.get("ttl_seconds", 3600)),
        )

    def notify_pending(self, digest: str, tool: str, node: str, reasons: list[str],
                       now: Optional[float] = None) -> list[Notice]:
        current = now if now is not None else time.time()
        expires_at = int(current + self.ttl_seconds)
        notices = []
        for operator, channels in self.operators.items():
            link = build_link(self.base_url, digest, operator, expires_at, self.key)
            notice = Notice(
                digest=digest, tool=tool, operator=operator, expires_at=expires_at,
                node=node, reasons=list(reasons), link=link,
            )
            for channel in channels:
                try:
                    channel.send(notice)
                    self.stats["sent"] += 1
                except Exception:  # noqa: BLE001 - notification must never block
                    self.stats["failed"] += 1
            notices.append(notice)
        return notices
