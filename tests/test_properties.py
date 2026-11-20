# -*- coding: utf-8 -*-
"""Property tests for the consent/engine merge and manifest canonical form.

No third-party test libraries: a fixed seed makes these reproducible, and the
invariants are the ones the audit found violated in v1.0.0.

Run: python3 tests/test_properties.py
"""
import os
import random
import sys
import unittest
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from lokahi import COMPLIANT, ESCALATE, FORBIDDEN, Decision, Manifest, Signer  # noqa: E402
from mcp_stdio_proxy import merge_threshold_consent  # noqa: E402

SEED = 20261119
KEY = b"property-test-key-00000000000000"

SAFETY_REASONS = [
    "forbidden_target:protest_history",
    "feature_out_of_manifest:secret",
    "jurisdiction_mismatch",
    "residency_violation:US",
    "physical_force_exceeded:999.0N>6.1N",
    "requester_in_ring",
    "requester_operator_in_ring",
    "declaring_party_in_ring",
    "exception_rate_exceeded",
    "unsigned_manifest",
    "unverifiable_manifest",
    "expired_manifest",
    "tool_not_in_manifest:x",
    "argument_out_of_policy:x",
]
CONSENT_REASONS = ["insufficient_disjoint_consent", "non_response"]


@dataclass
class FakeConsent:
    vetoed: bool
    consent_met: bool
    reason: str
    quorum_required: int
    quorum_met: int


def random_decision(rng: random.Random) -> Decision:
    kind = rng.choice(["safety_forbidden", "consent_forbidden", "escalate", "compliant"])
    if kind == "safety_forbidden":
        reasons = rng.sample(SAFETY_REASONS, rng.randint(1, 3))
        return Decision(FORBIDDEN, reasons, actuator_interlock="safe_mechanical_hold")
    if kind == "consent_forbidden":
        return Decision(FORBIDDEN, [rng.choice(CONSENT_REASONS)], actuator_interlock="pass")
    if kind == "escalate":
        return Decision(ESCALATE, ["insufficient_disjoint_consent"], 2, rng.randint(0, 1))
    return Decision(COMPLIANT, ["within_manifest"], actuator_interlock="pass")


def random_consent(rng: random.Random) -> FakeConsent:
    m = rng.randint(1, 3)
    met = rng.randint(0, m)
    kind = rng.choice(["met", "veto_silence", "veto_dissent", "pending"])
    if kind == "met":
        return FakeConsent(False, True, "quorum_met", m, max(met, m))
    if kind == "veto_silence":
        return FakeConsent(True, False, "non_response", m, met)
    if kind == "veto_dissent":
        return FakeConsent(True, False, "suspended_by_ring", m, met)
    return FakeConsent(False, False, "insufficient_disjoint_consent", m, met)


class TestMergeProperties(unittest.TestCase):
    def test_none_consent_is_identity(self):
        rng = random.Random(SEED)
        for _ in range(500):
            decision = random_decision(rng)
            merged = merge_threshold_consent(decision, None)
            self.assertEqual(merged.verdict, decision.verdict)
            self.assertEqual(merged.reasons, decision.reasons)

    def test_safety_refusals_are_absolute(self):
        """No consent outcome may relax a safety refusal (the v1.0.0 C1 bug)."""
        rng = random.Random(SEED + 1)
        for _ in range(2000):
            decision = Decision(
                FORBIDDEN, [rng.choice(SAFETY_REASONS)], actuator_interlock="safe_mechanical_hold"
            )
            consent = random_consent(rng)
            merged = merge_threshold_consent(decision, consent)
            self.assertEqual(merged.verdict, FORBIDDEN, (decision, consent))
            self.assertEqual(merged.reasons, decision.reasons, (decision, consent))

    def test_veto_never_yields_compliant(self):
        rng = random.Random(SEED + 2)
        for _ in range(2000):
            decision = random_decision(rng)
            consent = FakeConsent(True, False, rng.choice(["non_response", "suspended_by_ring"]), 2, 1)
            merged = merge_threshold_consent(decision, consent)
            self.assertNotEqual(merged.verdict, COMPLIANT, (decision, consent))

    def test_consent_met_promotes_only_consent_only_escalation(self):
        rng = random.Random(SEED + 3)
        for _ in range(2000):
            decision = random_decision(rng)
            consent = FakeConsent(False, True, "quorum_met", 2, 2)
            merged = merge_threshold_consent(decision, consent)
            if decision.verdict == FORBIDDEN and set(decision.reasons) <= set(CONSENT_REASONS):
                self.assertEqual(merged.verdict, COMPLIANT, decision)
            elif decision.verdict == FORBIDDEN:
                self.assertEqual(merged.verdict, FORBIDDEN, decision)
            else:
                self.assertEqual(merged.verdict, COMPLIANT, decision)
            if merged.verdict == COMPLIANT:
                self.assertEqual(merged.quorum_required, 2)
                self.assertEqual(merged.quorum_met, 2)

    def test_non_consent_verdicts_never_loosen(self):
        """For any engine verdict not caused by its own quorum, severity holds."""
        severity = {COMPLIANT: 0, ESCALATE: 1, FORBIDDEN: 2}
        rng = random.Random(SEED + 4)
        for _ in range(2000):
            decision = random_decision(rng)
            if set(decision.reasons) <= set(CONSENT_REASONS):
                continue
            consent = random_consent(rng)
            merged = merge_threshold_consent(decision, consent)
            self.assertGreaterEqual(
                severity[merged.verdict],
                severity[decision.verdict],
                (decision, consent, merged),
            )

    def test_consent_only_verdicts_follow_the_framework(self):
        """Quorum-only verdicts resolve to the threshold, with one exception."""
        rng = random.Random(SEED + 8)
        for _ in range(2000):
            decision = random_decision(rng)
            if not (set(decision.reasons) <= set(CONSENT_REASONS)):
                continue
            consent = random_consent(rng)
            merged = merge_threshold_consent(decision, consent)
            if consent.vetoed:
                self.assertEqual(merged.verdict, FORBIDDEN, (decision, consent))
            elif consent.consent_met:
                self.assertEqual(merged.verdict, COMPLIANT, (decision, consent))
                self.assertEqual(merged.quorum_required, consent.quorum_required)
                self.assertEqual(merged.quorum_met, consent.quorum_met)
            else:
                expected = FORBIDDEN if decision.verdict == FORBIDDEN else ESCALATE
                self.assertEqual(merged.verdict, expected, (decision, consent))

    def test_interlock_is_hold_for_any_refusal(self):
        rng = random.Random(SEED + 5)
        for _ in range(1000):
            decision = random_decision(rng)
            consent = random_consent(rng)
            merged = merge_threshold_consent(decision, consent, hold_mode="safe_mechanical_hold")
            if merged.verdict == FORBIDDEN:
                self.assertEqual(merged.actuator_interlock, "safe_mechanical_hold", merged)


class TestManifestCanonicalProperties(unittest.TestCase):
    def _manifest(self, **over):
        m = Manifest(
            node="p-node",
            informational=["a", "b"],
            forbidden_targets=["x"],
            jurisdiction="IN-DL",
            residency=["IN"],
            exceptions={"safety_critical": 1, "window_hours": 24},
            expires="2030-01-01T00:00:00Z",
            steward="did:web:p",
        )
        for k, v in over.items():
            setattr(m, k, v)
        return m

    def test_sign_verify_roundtrip_is_random_order_stable(self):
        rng = random.Random(SEED + 6)
        signer = Signer(KEY)
        for _ in range(200):
            features = sorted({f"f{rng.randint(0, 9)}" for _ in range(rng.randint(1, 5))})
            targets = sorted({f"t{rng.randint(0, 5)}" for _ in range(rng.randint(0, 3))})
            m = self._manifest(informational=features, forbidden_targets=targets).sign(signer)
            self.assertTrue(m.verify(signer))
            rng.shuffle(m.informational)
            rng.shuffle(m.forbidden_targets)
            self.assertTrue(m.verify(signer), "order must not change the signed bytes")

    def test_optional_fields_are_only_included_when_set(self):
        m = self._manifest()
        base = m.to_dict()
        self.assertNotIn("tools", base)
        self.assertNotIn("consent_threshold", base)
        self.assertNotIn("approval_window_seconds", base)

        m.tools = {"read": {"class": "read", "args": {}}}
        m.consent_threshold = {"m": 2, "n": 3}
        m.approval_window_seconds = 900
        extended = m.to_dict()
        for key in ("tools", "consent_threshold", "approval_window_seconds"):
            self.assertIn(key, extended)

    def test_tampering_any_field_breaks_verification(self):
        signer = Signer(KEY)
        rng = random.Random(SEED + 7)
        for _ in range(200):
            m = self._manifest().sign(signer)
            field = rng.choice(["node", "jurisdiction", "steward", "expires"])
            setattr(m, field, str(getattr(m, field)) + "x")
            self.assertFalse(m.verify(signer), field)


if __name__ == "__main__":
    unittest.main()
