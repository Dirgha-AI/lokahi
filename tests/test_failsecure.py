# -*- coding: utf-8 -*-
"""Tests for fail-secure state and WebXR teleoperation fallback (Stage 4).

Run: python3 tests/test_failsecure.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

try:
    from lokahi_failsecure import FailSecureManager, FailSecureState
    from lokahi_webxr import WebXRTeleopBroker, WebXRSession, TeleopAction
    from lokahi import Action, Decision, Manifest, Signer, Ledger, COMPLIANT, FORBIDDEN

    _STAGE4_AVAILABLE = True
except ImportError:
    _STAGE4_AVAILABLE = False

KEY = b"prototype-key-not-for-protection"
NOW = 1_800_000_000.0


def _make_manifest(signer: Signer, **over) -> Manifest:
    m = Manifest(
        node="egz-17-water-board",
        informational=["service_address", "usage_liters", "meter_id"],
        forbidden_targets=["residency_status", "protest_history"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 3, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:egz17.example/steward",
    )
    for k, v in over.items():
        setattr(m, k, v)
    return m.sign(signer)


def _make_action(**over) -> Action:
    a = Action(
        requester="agent:allocator-2",
        tool="read_meter",
        features=["usage_liters"],
        jurisdiction="IN-DL",
        requester_operator="org:water-ops",
        purpose="billing",
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


@unittest.skipUnless(_STAGE4_AVAILABLE, "lokahi_failsecure/lokahi_webxr not available")
class TestFailSecureState(unittest.TestCase):
    """Fail-secure state transitions and privilege lifecycle."""

    def setUp(self):
        self.s = Signer(KEY)
        self.m = _make_manifest(self.s)
        self.ledger = Ledger()
        self.fsm = FailSecureManager(self.ledger, self.m.node)

    def test_initial_state_operational(self):
        """Freshly constructed manager is operational with a privilege token."""
        self.assertTrue(self.fsm.has_privilege())
        self.assertFalse(self.fsm.state.active)
        self.assertIsNotNone(self.fsm.state.privilege_token)

    def test_enter_secure_state_revokes_privilege(self):
        """Entering secure state revokes the privilege token."""
        decision = self.fsm.enter_secure_state("test_exception", now=NOW)
        self.assertFalse(self.fsm.has_privilege())
        self.assertTrue(self.fsm.state.active)
        self.assertEqual(self.fsm.state.reason, "test_exception")
        self.assertEqual(self.fsm.state.entered_at, NOW)
        self.assertEqual(decision.verdict, FORBIDDEN)
        self.assertIn("secure_state:test_exception", decision.reasons)
        self.assertEqual(decision.actuator_interlock, "safe_mechanical_hold")

    def test_transition_count_increments(self):
        """Each secure-state entry increments the transition count."""
        self.fsm.enter_secure_state("reason1", now=NOW)
        self.assertEqual(self.fsm.state.transition_count, 1)
        self.fsm.enter_secure_state("reason2", now=NOW)
        self.assertEqual(self.fsm.state.transition_count, 2)

    def test_rearm_restores_privilege(self):
        """Re-arming issues a new privilege token and clears secure state."""
        self.fsm.enter_secure_state("test_exception", now=NOW)
        old_token = self.fsm.state.privilege_token  # should be None
        self.assertIsNone(old_token)

        decision = self.fsm.rearm("rearm-sig-abc123", now=NOW)
        self.assertTrue(self.fsm.has_privilege())
        self.assertFalse(self.fsm.state.active)
        self.assertFalse(self.fsm.state.teleop_active)
        self.assertEqual(self.fsm.state.rearmed_at, NOW)
        self.assertEqual(self.fsm.state.rearm_signature, "rearm-sig-abc123")
        self.assertEqual(decision.verdict, COMPLIANT)
        self.assertIn("rearmed", decision.reasons)
        # New token issued after re-arm
        self.assertIsNotNone(self.fsm.state.privilege_token)

    def test_rearm_when_already_armed_is_noop(self):
        """Re-arming an already-operational system returns compliant."""
        decision = self.fsm.rearm("sig", now=NOW)
        self.assertEqual(decision.verdict, COMPLIANT)
        self.assertIn("already_armed", decision.reasons)

    def test_status_returns_correct_state(self):
        """status() reflects the current fail-secure state."""
        status = self.fsm.status()
        self.assertFalse(status["secure_state"])
        self.assertFalse(status["teleop_active"])
        self.assertFalse(status["privilege_revoked"])

        self.fsm.enter_secure_state("test", now=NOW)
        status = self.fsm.status()
        self.assertTrue(status["secure_state"])
        self.assertTrue(status["privilege_revoked"])
        self.assertEqual(status["reason"], "test")
        self.assertEqual(status["transition_count"], 1)

    def test_ledger_records_transitions(self):
        """Secure state transitions are recorded in the tamper-evident ledger."""
        chain_ok_before, _ = self.ledger.verify()
        self.assertTrue(chain_ok_before)

        self.fsm.enter_secure_state("test_exception", now=NOW)
        chain_ok_after, _ = self.ledger.verify()
        self.assertTrue(chain_ok_after)

    def test_handle_exception_routes_to_secure_state(self):
        """handle_exception() enters secure state and returns FORBIDDEN."""
        exc = ValueError("WASM sandbox panic")
        decision = self.fsm.handle_exception(exc, "wasm_preflight", now=NOW)
        self.assertEqual(decision.verdict, FORBIDDEN)
        self.assertIn("secure_state:wasm_preflight_exception:ValueError", decision.reasons)
        self.assertTrue(self.fsm.state.active)
        self.assertFalse(self.fsm.has_privilege())


@unittest.skipUnless(_STAGE4_AVAILABLE, "lokahi_failsecure/lokahi_webxr not available")
class TestWebXRTeleopBroker(unittest.TestCase):
    """WebXR teleoperation session lifecycle and audited actions."""

    def setUp(self):
        self.ledger = Ledger()
        self.broker = WebXRTeleopBroker(
            endpoint="wss://webxr.example.com/teleop",
            ledger=self.ledger,
            manifest_node="egz-17-water-board",
        )

    def test_initiate_session(self):
        """Initiating a session returns connection details and ledger entry."""
        result = self.broker.initiate_session(operator="ops:alice", now=NOW)
        self.assertTrue(result["accepted"] if "accepted" in result else True)
        self.assertIn("session_id", result)
        self.assertEqual(result["operator"], "ops:alice")
        self.assertEqual(result["webxr_endpoint"], "wss://webxr.example.com/teleop")
        self.assertEqual(result["status"], "awaiting_operator")
        self.assertIsNotNone(result["ledger_entry"])
        self.assertIsNotNone(self.broker.session)
        self.assertEqual(self.broker.session.operator, "ops:alice")

    def test_record_teleop_action(self):
        """Teleop actions are recorded and ledgered."""
        self.broker.initiate_session(operator="ops:alice", now=NOW)
        result = self.broker.record_teleop_action(
            action_type="command",
            target="actuator:valve-01",
            payload={"command": "close", "force_n": 5.0},
            signature="sig-123",
            now=NOW,
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["action_index"], 0)
        self.assertEqual(len(self.broker.session.actions), 1)
        self.assertIsNotNone(result["ledger_entry"])

    def test_record_teleop_action_no_session(self):
        """Recording an action with no active session is rejected."""
        result = self.broker.record_teleop_action(
            action_type="command",
            target="actuator:valve-01",
            payload={},
            now=NOW,
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "no_active_session")

    def test_request_rearm(self):
        """Operator can request re-arm from the WebXR console."""
        self.broker.initiate_session(operator="ops:alice", now=NOW)
        result = self.broker.request_rearm("rearm-sig-xyz789", now=NOW)
        self.assertTrue(result["accepted"])
        self.assertTrue(self.broker.session.rearm_requested)
        self.assertEqual(self.broker.session.rearm_signature, "rearm-sig-xyz789")
        self.assertIsNotNone(result["ledger_entry"])

    def test_release_session(self):
        """Releasing a session ends it and records duration/action count."""
        self.broker.initiate_session(operator="ops:alice", now=NOW)
        self.broker.record_teleop_action(
            action_type="pose",
            target="actuator:arm-01",
            payload={"x": 1.0, "y": 2.0, "z": 3.0},
            now=NOW,
        )
        session = self.broker.session
        result = self.broker.release_session(now=NOW + 300)
        self.assertTrue(result["accepted"])
        self.assertFalse(session.active)
        self.assertIsNone(self.broker.session)
        extra = result["ledger_entry"]["extra"]
        self.assertEqual(extra["duration_seconds"], 300.0)
        self.assertEqual(extra["action_count"], 1)
        self.assertIsNotNone(result["ledger_entry"])

    def test_release_session_no_session(self):
        """Releasing with no active session is rejected."""
        result = self.broker.release_session(now=NOW)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "no_active_session")

    def test_status_no_session(self):
        """Status with no session returns session_active=False."""
        status = self.broker.status()
        self.assertFalse(status["session_active"])
        self.assertEqual(status["endpoint"], "wss://webxr.example.com/teleop")

    def test_status_with_session(self):
        """Status with an active session returns session details."""
        self.broker.initiate_session(operator="ops:bob", now=NOW)
        status = self.broker.status()
        self.assertTrue(status["session_active"])
        self.assertEqual(status["operator"], "ops:bob")
        self.assertEqual(status["action_count"], 0)


@unittest.skipUnless(_STAGE4_AVAILABLE, "lokahi_failsecure/lokahi_webxr not available")
class TestFailSecureIntegration(unittest.TestCase):
    """Integration: exception → secure state → WebXR teleop → re-arm."""

    def setUp(self):
        self.s = Signer(KEY)
        self.m = _make_manifest(self.s)
        self.ledger = Ledger()

    def test_exception_to_secure_state_to_teleop(self):
        """An exception enters secure state; WebXR teleop can be triggered."""
        fsm = FailSecureManager(self.ledger, self.m.node)
        broker = WebXRTeleopBroker(
            endpoint="wss://webxr.example.com/teleop",
            ledger=self.ledger,
            manifest_node=self.m.node,
        )

        # Exception enters secure state
        exc = RuntimeError("SLM judge timeout")
        decision = fsm.handle_exception(exc, "slm_judge", now=NOW)
        self.assertEqual(decision.verdict, FORBIDDEN)
        self.assertFalse(fsm.has_privilege())

        # Trigger WebXR teleoperation
        teleop = broker.trigger_teleoperation("wss://webxr.example.com/teleop", now=NOW)
        self.assertTrue(teleop["teleop_active"])
        self.assertEqual(teleop["webxr_endpoint"], "wss://webxr.example.com/teleop")

        # Verify ledger has both entries
        chain_ok, chain_len = self.ledger.verify()
        self.assertTrue(chain_ok)
        self.assertGreaterEqual(chain_len, 2)

    def test_full_lifecycle(self):
        """Full lifecycle: operational → exception → secure → teleop → re-arm."""
        fsm = FailSecureManager(self.ledger, self.m.node)
        self.assertTrue(fsm.has_privilege())

        # Exception
        exc = RuntimeError("consent network failure")
        fsm.handle_exception(exc, "consent_framework", now=NOW)
        self.assertFalse(fsm.has_privilege())
        self.assertTrue(fsm.state.active)

        # WebXR teleop
        broker = WebXRTeleopBroker(
            endpoint="wss://webxr.example.com/teleop",
            ledger=self.ledger,
            manifest_node=self.m.node,
        )
        broker.initiate_session(operator="ops:carol", now=NOW)
        broker.record_teleop_action(
            action_type="override",
            target="actuator:valve-01",
            payload={"command": "safe_hold"},
            now=NOW,
        )
        broker.request_rearm("rearm-sig-lifecycle", now=NOW)

        # Re-arm
        decision = fsm.rearm("rearm-sig-lifecycle", now=NOW)
        self.assertEqual(decision.verdict, COMPLIANT)
        self.assertTrue(fsm.has_privilege())
        self.assertFalse(fsm.state.active)

        # Ledger chain intact
        chain_ok, chain_len = self.ledger.verify()
        self.assertTrue(chain_ok)
        self.assertGreaterEqual(chain_len, 4)


if __name__ == "__main__":
    unittest.main()
