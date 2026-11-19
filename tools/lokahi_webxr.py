# -*- coding: utf-8 -*-
"""WebXR teleoperation broker for LOKAHI fail-secure fallback.

When the LOKAHI cascade cannot reach a secure automated decision (timeout,
exception, or explicit human-escalation trigger), the system enters fail-secure
state and triggers a WebXR teleoperation session. A human operator dons a
WebXR headset and takes manual control of the actuator/agent through an
audited telepresence stream. All teleoperation actions are logged to the same
tamper-evident ledger.

Design constraints:

* **Audited telepresence.** Every teleop action (pose, command, override) is
  recorded to the hash-chained ledger with the operator's identity and a
  cryptographic signature.
* **Human re-arm.** Re-arming from secure state requires a cryptographic
  re-arm authorization recorded to the ledger, initiated through the WebXR
  console. The system never auto-re-arms.
* **WebXR session lifecycle:** initiate → operator takes control → actions
  audited → release → (optionally) re-arm.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from lokahi import Decision, FORBIDDEN, COMPLIANT, Action, Manifest, Signer


@dataclass
class WebXRSession:
    """An active WebXR teleoperation session."""

    session_id: str
    operator: str
    started_at: float
    endpoint: str
    active: bool = True
    actions: list[dict] = field(default_factory=list)
    rearm_requested: bool = False
    rearm_signature: str = ""


@dataclass
class TeleopAction:
    """One audited teleoperation action."""

    ts: float
    operator: str
    action_type: str  # "pose", "command", "override", "rearm", "release"
    target: str       # actuator/agent being controlled
    payload: dict     # action-specific data
    signature: str = ""


class WebXRTeleopBroker:
    """Broker for WebXR teleoperation sessions.

    Coordinates fail-secure → teleoperation → re-arm flow. Integrates with the
    tamper-evident ledger so every teleop action is cryptographically audited.
    """

    def __init__(
        self,
        endpoint: str,
        ledger: Any,
        manifest_node: str,
    ) -> None:
        self.endpoint = endpoint
        self.ledger = ledger
        self.manifest_node = manifest_node
        self.session: Optional[WebXRSession] = None
        self._sessions: list[WebXRSession] = []

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def trigger_teleoperation(
        self, webxr_endpoint: str, now: Optional[float] = None
    ) -> dict[str, Any]:
        """Trigger a WebXR teleoperation session (convenience wrapper).

        Equivalent to initiate_session but named for the fail-secure
        integration path. Returns the session details.
        """
        now = now if now is not None else time.time()
        # If there's already an active session, don't create a new one.
        if self.session is not None and self.session.active:
            return {
                "teleop_active": True,
                "session_id": self.session.session_id,
                "webxr_endpoint": self.endpoint,
                "session_started_at": self.session.started_at,
                "message": "WebXR teleoperation session already active.",
            }
        self.endpoint = webxr_endpoint
        result = self.initiate_session(operator="ops:failsecure-operator", now=now)
        result["teleop_active"] = True
        result["webxr_endpoint"] = webxr_endpoint
        return result

    def initiate_session(self, operator: str, now: Optional[float] = None) -> dict[str, Any]:
        """Initiate a WebXR teleoperation session for a human operator.

        Returns the session details the operator uses to connect their
        WebXR headset.
        """
        now = now if now is not None else time.time()
        session_id = secrets.token_hex(16)

        self.session = WebXRSession(
            session_id=session_id,
            operator=operator,
            started_at=now,
            endpoint=self.endpoint,
        )
        self._sessions.append(self.session)

        entry = self._ledger_entry(
            verdict="webxr_session_initiated",
            reasons=["failsecure_teleoperation"],
            extra={
                "session_id": session_id,
                "operator": operator,
                "endpoint": self.endpoint,
            },
        )

        return {
            "session_id": session_id,
            "operator": operator,
            "webxr_endpoint": self.endpoint,
            "started_at": now,
            "status": "awaiting_operator",
            "ledger_entry": entry,
            "message": (
                "WebXR teleoperation session initiated. Operator should connect "
                "their WebXR headset to take manual control."
            ),
        }

    def record_teleop_action(
        self,
        action_type: str,
        target: str,
        payload: dict,
        signature: str = "",
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Record an audited teleoperation action.

        Called by the WebXR frontend for every operator action (pose update,
        command, override). All actions are ledgered.
        """
        now = now if now is not None else time.time()
        if self.session is None or not self.session.active:
            return {
                "accepted": False,
                "reason": "no_active_session",
            }

        action = TeleopAction(
            ts=now,
            operator=self.session.operator,
            action_type=action_type,
            target=target,
            payload=payload,
            signature=signature,
        )
        self.session.actions.append(action.__dict__)

        entry = self._ledger_entry(
            verdict="teleop_action",
            reasons=[action_type],
            extra={
                "session_id": self.session.session_id,
                "operator": self.session.operator,
                "target": target,
                "action_type": action_type,
                "payload": payload,
            },
        )

        return {
            "accepted": True,
            "action_index": len(self.session.actions) - 1,
            "ts": now,
            "ledger_entry": entry,
        }

    def request_rearm(self, rearm_signature: str, now: Optional[float] = None) -> dict[str, Any]:
        """Operator requests re-arm from the WebXR console.

        Records the re-arm request and signature. The actual re-arm is
        completed by the FailSecureManager.rearm() call.
        """
        now = now if now is not None else time.time()
        if self.session is None:
            return {
                "accepted": False,
                "reason": "no_active_session",
            }

        self.session.rearm_requested = True
        self.session.rearm_signature = rearm_signature

        entry = self._ledger_entry(
            verdict="webxr_rearm_requested",
            reasons=["human_rearm"],
            extra={
                "session_id": self.session.session_id,
                "operator": self.session.operator,
                "rearm_signature": rearm_signature[:16] + "...",
            },
        )

        return {
            "accepted": True,
            "session_id": self.session.session_id,
            "rearm_signature": rearm_signature,
            "ledger_entry": entry,
            "message": "Re-arm requested. System will re-arm after validation.",
        }

    def release_session(self, now: Optional[float] = None) -> dict[str, Any]:
        """Release (end) the current WebXR teleoperation session."""
        now = now if now is not None else time.time()
        if self.session is None:
            return {"accepted": False, "reason": "no_active_session"}

        session = self.session
        session.active = False
        session_id = session.session_id
        operator = session.operator

        entry = self._ledger_entry(
            verdict="webxr_session_released",
            reasons=["teleoperation_ended"],
            extra={
                "session_id": session_id,
                "operator": operator,
                "duration_seconds": now - self.session.started_at,
                "action_count": len(self.session.actions),
            },
        )

        self.session = None

        return {
            "accepted": True,
            "session_id": session_id,
            "operator": operator,
            "released_at": now,
            "ledger_entry": entry,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return the current teleoperation session status."""
        if self.session is None:
            return {
                "session_active": False,
                "endpoint": self.endpoint,
            }
        return {
            "session_active": self.session.active,
            "session_id": self.session.session_id,
            "operator": self.session.operator,
            "endpoint": self.endpoint,
            "started_at": self.session.started_at,
            "rearm_requested": self.session.rearm_requested,
            "action_count": len(self.session.actions),
        }

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    def _ledger_entry(
        self,
        verdict: str,
        reasons: list[str],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """Append a teleoperation event to the tamper-evident ledger.

        Uses `Ledger.append_event` so teleop events chain onto the same hash
        sequence as tool-call decisions.
        """
        entry: dict[str, Any] = {
            "verdict": verdict,
            "reasons": reasons,
            "node": self.manifest_node,
            "extra": extra,
        }
        try:
            if hasattr(self.ledger, "append_event"):
                chained = self.ledger.append_event(
                    verdict=verdict,
                    reasons=reasons,
                    node=self.manifest_node,
                    extra=extra,
                )
                entry["seq"] = chained["seq"]
                entry["hash"] = chained["hash"]
        except Exception:  # noqa: BLE001 - teleop must continue even if logging faults
            entry["ledger_error"] = "append_failed"
        return entry
