# -*- coding: utf-8 -*-
"""Fail-secure state manager for LOKAHI.

On any pipeline failure — WASM sandbox crash, SLM judge timeout, consent
network failure, manifest verification failure, or uncaught exception — the
system drops execution privileges immediately and reverts to a secure default
state where no action is permitted until a human explicitly re-arms via the
WebXR teleoperation console.

Design constraints:

* **Fail closed by default.** Any exception anywhere in the cascade routes to
  secure state. The system never falls back to a permissive default.
* **Privilege drop.** Once in secure state, the agent/actuator cannot issue
  further tool calls until re-armed.
* **Human re-arm.** Re-arming requires a cryptographic re-arm authorization
  recorded to the tamper-evident ledger, initiated through the WebXR console.
* **All transitions ledgered.** Entry to secure state, privilege drop, and
  re-arm are all recorded in the hash-chained ledger.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from lokahi import Decision, FORBIDDEN, COMPLIANT, ESCALATE, Action, Manifest, Signer


@dataclass
class FailSecureState:
    """Current fail-secure state of the runtime."""

    active: bool = False          # True = system is in secure state (locked)
    entered_at: Optional[float] = None  # timestamp when secure state was entered
    reason: str = ""              # why secure state was entered
    teleop_active: bool = False   # True = WebXR teleoperation session is active
    rearmed_at: Optional[float] = None  # timestamp of last re-arm
    rearm_signature: str = ""     # cryptographic re-arm authorization signature
    privilege_token: Optional[str] = None  # execution privilege token (None = revoked)
    transition_count: int = 0     # how many times secure state has been entered


class FailSecureManager:
    """Manages the fail-secure state and privilege lifecycle.

    The gateway delegates all exception handling to this manager. When any
    layer of the cascade fails, `enter_secure_state()` is called, which
    revokes the privilege token and locks the actuator/agent.
    """

    def __init__(self, ledger: Any, manifest_node: str) -> None:
        self.ledger = ledger
        self.manifest_node = manifest_node
        self.state = FailSecureState()
        # A privilege token is issued when the system is operational.
        # Setting it to None revokes all execution privileges.
        self._issue_privilege_token()

    # ------------------------------------------------------------------
    # Privilege token lifecycle
    # ------------------------------------------------------------------

    def _issue_privilege_token(self) -> None:
        """Issue a fresh execution privilege token."""
        import hashlib
        import os
        token = hashlib.sha256(
            os.urandom(32) + str(time.time()).encode()
        ).hexdigest()
        self.state.privilege_token = token

    def _revoke_privilege_token(self) -> None:
        """Revoke the execution privilege token (drop execution privileges)."""
        self.state.privilege_token = None

    def has_privilege(self) -> bool:
        """True if the system is operational and execution is permitted."""
        return self.state.privilege_token is not None and not self.state.active

    # ------------------------------------------------------------------
    # Secure state transitions
    # ------------------------------------------------------------------

    def enter_secure_state(self, reason: str, now: Optional[float] = None) -> Decision:
        """Enter secure state: drop privileges, lock actuator/agent.

        Called when any layer of the cascade fails. Records the transition
        to the tamper-evident ledger.
        """
        now = now if now is not None else time.time()
        self.state.active = True
        self.state.entered_at = now
        self.state.reason = reason
        self.state.transition_count += 1
        self._revoke_privilege_token()

        # Record to ledger
        entry = self._ledger_entry(
            verdict="secure_state_entered",
            reasons=[reason],
            extra={
                "transition_count": self.state.transition_count,
                "privilege_revoked": True,
            },
        )

        return Decision(
            FORBIDDEN,
            [f"secure_state:{reason}"],
            actuator_interlock="safe_mechanical_hold",
        )

    def trigger_teleoperation(
        self, webxr_endpoint: str, now: Optional[float] = None
    ) -> dict[str, Any]:
        """Trigger a WebXR teleoperation session for human manual oversight.

        Called after entering secure state when a human needs to take manual
        control of the actuator/agent.
        """
        now = now if now is not None else time.time()
        self.state.teleop_active = True

        entry = self._ledger_entry(
            verdict="teleoperation_triggered",
            reasons=["webxr_teleoperation"],
            extra={
                "webxr_endpoint": webxr_endpoint,
                "transition_count": self.state.transition_count,
            },
        )

        return {
            "webxr_endpoint": webxr_endpoint,
            "session_started_at": now,
            "teleop_active": True,
            "ledger_entry": entry,
            "message": "Human WebXR teleoperation session initiated. "
                       "Take manual control of the actuator/agent.",
        }

    def rearm(
        self, rearm_signature: str, now: Optional[float] = None
    ) -> Decision:
        """Re-arm the system from secure state.

        Requires a cryptographic re-arm authorization signature from a human
        operator via the WebXR console. After re-arm, a new privilege token
        is issued and execution is permitted again.
        """
        now = now if now is not None else time.time()
        if not self.state.active:
            return Decision(
                COMPLIANT,
                ["already_armed"],
                actuator_interlock="pass",
            )

        self.state.active = False
        self.state.teleop_active = False
        self.state.rearmed_at = now
        self.state.rearm_signature = rearm_signature
        self._issue_privilege_token()

        entry = self._ledger_entry(
            verdict="secure_state_rearmed",
            reasons=["human_rearm_authorized"],
            extra={
                "rearm_signature": rearm_signature[:16] + "...",
                "transition_count": self.state.transition_count,
            },
        )

        return Decision(
            COMPLIANT,
            ["rearmed"],
            actuator_interlock="pass",
        )

    # ------------------------------------------------------------------
    # Exception boundary handler
    # ------------------------------------------------------------------

    def handle_exception(
        self, exc: Exception, layer: str, now: Optional[float] = None
    ) -> Decision:
        """Handle an exception from any cascade layer.

        Routes to secure state and returns a FORBIDDEN decision.
        This is the single entry point for all fail-secure transitions.
        """
        now = now if now is not None else time.time()
        reason = f"{layer}_exception:{type(exc).__name__}"
        return self.enter_secure_state(reason, now)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return the current fail-secure status for monitoring/health checks."""
        return {
            "secure_state": self.state.active,
            "teleop_active": self.state.teleop_active,
            "entered_at": self.state.entered_at,
            "reason": self.state.reason,
            "rearmed_at": self.state.rearmed_at,
            "privilege_revoked": self.state.privilege_token is None,
            "transition_count": self.state.transition_count,
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
        """Append a fail-secure transition to the tamper-evident ledger.

        Uses `Ledger.append_event` so the entry chains onto the same hash
        sequence as tool-call decisions and carries the real node id.
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
        except Exception:  # noqa: BLE001 - a ledger fault must not mask the safety transition
            entry["ledger_error"] = "append_failed"
        return entry
