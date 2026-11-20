# -*- coding: utf-8 -*-
"""Tests for the multi-party threshold consent framework (Stage 3).

Run: python3 tests/test_threshold_consent.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

try:
    from lokahi_consent import ConsentFramework, Ed25519Signer, Ed25519Verifier, Threshold
    from lokahi import Action, Approval, Manifest, Signer, compute_action_digest

    _CONSENT_AVAILABLE = True
except ImportError:
    _CONSENT_AVAILABLE = False

KEY = b"prototype-key-not-for-production"
NOW = 1_800_000_000.0  # 2027-01-15


def _make_validator_keys(n: int = 3) -> dict[str, Ed25519Verifier]:
    """Generate n Ed25519 validator key pairs, return public verifiers."""
    keys = {}
    for i in range(n):
        signer = Ed25519Signer.generate()
        keys[f"validator:{i}"] = Ed25519Verifier.from_public_bytes(signer.public_bytes())
    return keys


def _sign_digest(private_key_hex: str, digest: str) -> str:
    """Sign a digest hex string with an Ed25519 private key (hex-encoded seed)."""
    signer = Ed25519Signer.from_private_bytes(bytes.fromhex(private_key_hex))
    return signer.sign(digest.encode("utf-8"))


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
        tool="cutoff_water_valve",
        features=[],
        jurisdiction="IN-DL",
        requester_operator="org:water-ops",
        purpose="emergency shutoff",
        reversible=False,  # irreversible -> needs consent
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


@unittest.skipUnless(_CONSENT_AVAILABLE, "lokahi_consent not available")
class TestThreshold(unittest.TestCase):
    """Threshold configuration and manifest integration."""

    def test_default_threshold_is_2_of_3(self):
        t = Threshold()
        self.assertEqual(t.m, 2)
        self.assertEqual(t.n, 3)

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            Threshold(m=0, n=3)
        with self.assertRaises(ValueError):
            Threshold(m=4, n=3)

    def test_from_manifest_reads_consent_threshold(self):
        s = Signer(KEY)
        m = _make_manifest(s, consent_threshold={"m": 1, "n": 2})
        validator_keys = _make_validator_keys(2)
        cf = ConsentFramework.from_manifest(m, validator_keys)
        self.assertEqual(cf.threshold.m, 1)
        self.assertEqual(cf.threshold.n, 2)

    def test_from_manifest_defaults_to_2_of_3(self):
        s = Signer(KEY)
        m = _make_manifest(s)
        validator_keys = _make_validator_keys(3)
        cf = ConsentFramework.from_manifest(m, validator_keys)
        self.assertEqual(cf.threshold.m, 2)
        self.assertEqual(cf.threshold.n, 3)

    def test_needs_consent_irreversible(self):
        a = _make_action(reversible=False)
        self.assertTrue(ConsentFramework.needs_consent(a))

    def test_needs_consent_population_affecting(self):
        a = _make_action(reversible=True, population_affecting=True)
        self.assertTrue(ConsentFramework.needs_consent(a))

    def test_no_consent_for_reversible(self):
        a = _make_action(reversible=True, population_affecting=False)
        self.assertFalse(ConsentFramework.needs_consent(a))


@unittest.skipUnless(_CONSENT_AVAILABLE, "lokahi_consent not available")
class TestConsentChecking(unittest.TestCase):
    """Consent check outcomes: quorum, veto-on-silence, dissent."""

    def setUp(self):
        self.s = Signer(KEY)
        self.m = _make_manifest(self.s, consent_threshold={"m": 2, "n": 3})
        self.validator_keys = _make_validator_keys(3)
        # Also keep private keys for signing
        self.private_keys = {}
        for i in range(3):
            signer = Ed25519Signer.generate()
            self.private_keys[f"validator:{i}"] = signer.private_bytes().hex()
            self.validator_keys[f"validator:{i}"] = Ed25519Verifier.from_public_bytes(
                signer.public_bytes()
            )
        self.cf = ConsentFramework.from_manifest(self.m, self.validator_keys)
        self.action = _make_action()
        self.digest = compute_action_digest(self.m, self.action, "test-nonce")
        self.round_nonce = "test-nonce"

    def _make_approval(self, operator: str, decision: str, private_key_hex: str) -> Approval:
        return Approval(
            operator, "device-1", decision, self.digest,
            "2028-03-01T00:00:00Z", "nonce-" + operator,
        ).sign(Ed25519Signer.from_private_bytes(bytes.fromhex(private_key_hex)))

    def test_two_yes_meets_quorum(self):
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        a2 = self._make_approval("validator:1", "yes", self.private_keys["validator:1"])
        result = self.cf.check_consent(
            self.action, [a1, a2], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertTrue(result.consent_met)
        self.assertFalse(result.vetoed)
        self.assertEqual(result.reason, "quorum_met")
        self.assertEqual(result.quorum_required, 2)
        self.assertEqual(result.quorum_met, 2)

    def test_one_yes_insufficient(self):
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        result = self.cf.check_consent(
            self.action, [a1], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertFalse(result.consent_met)
        self.assertFalse(result.vetoed)
        self.assertEqual(result.reason, "insufficient_disjoint_consent")
        self.assertEqual(result.quorum_met, 1)

    def test_silence_is_veto(self):
        """Window closes with only 1 yes (need 2) -> non_response veto."""
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        result = self.cf.check_consent(
            self.action, [a1], now=NOW, timed_out=True, round_nonce=self.round_nonce
        )
        self.assertFalse(result.consent_met)
        self.assertTrue(result.vetoed)
        self.assertEqual(result.reason, "non_response")
        self.assertEqual(result.quorum_met, 1)

    def test_two_no_votes_veto(self):
        """Two distinct validators voting no -> suspended_by_ring veto."""
        a1 = self._make_approval("validator:0", "no", self.private_keys["validator:0"])
        a2 = self._make_approval("validator:1", "no", self.private_keys["validator:1"])
        a3 = self._make_approval("validator:2", "yes", self.private_keys["validator:2"])
        result = self.cf.check_consent(
            self.action, [a1, a2, a3], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertFalse(result.consent_met)
        self.assertTrue(result.vetoed)
        self.assertEqual(result.reason, "suspended_by_ring")
        self.assertEqual(result.no_operators, ["validator:0", "validator:1"])

    def test_unregistered_operator_ignored(self):
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        # validator:99 is not registered
        a2 = Approval(
            "validator:99", "device-1", "yes", self.digest,
            "2028-03-01T00:00:00Z", "nonce-validator:99",
        ).sign(Ed25519Signer.from_private_bytes(bytes.fromhex(self.private_keys["validator:0"])))
        result = self.cf.check_consent(
            self.action, [a1, a2], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertEqual(result.quorum_met, 1)
        self.assertFalse(result.consent_met)

    def test_expired_approval_ignored(self):
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        # Make it expired
        a1.expires = "2000-01-01T00:00:00Z"
        result = self.cf.check_consent(
            self.action, [a1], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertEqual(result.quorum_met, 0)
        self.assertFalse(result.consent_met)

    def test_wrong_digest_ignored(self):
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        # Tamper the digest
        a1.action_digest = "tampered-digest"
        result = self.cf.check_consent(
            self.action, [a1], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertEqual(result.quorum_met, 0)
        self.assertFalse(result.consent_met)

    def test_reused_nonce_ignored(self):
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        a2 = Approval(
            "validator:0", "device-2", "yes", self.digest,
            "2028-03-01T00:00:00Z", a1.nonce,  # same nonce
        ).sign(Ed25519Signer.from_private_bytes(bytes.fromhex(self.private_keys["validator:0"])))
        result = self.cf.check_consent(
            self.action, [a1, a2], now=NOW, timed_out=False, round_nonce=self.round_nonce
        )
        self.assertEqual(result.quorum_met, 1)
        self.assertFalse(result.consent_met)

    def test_conflict_of_interest_requester_excluded(self):
        """The requesting operator cannot approve its own call."""
        # validator:0 is the requester operator
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        a2 = self._make_approval("validator:1", "yes", self.private_keys["validator:1"])
        result = self.cf.check_consent(
            self.action, [a1, a2], now=NOW, timed_out=False,
            requester_operator="validator:0", round_nonce=self.round_nonce,
        )
        # validator:0's yes should be excluded -> only 1 yes -> insufficient
        self.assertEqual(result.quorum_met, 1)
        self.assertFalse(result.consent_met)

    def test_declaring_party_excluded(self):
        """Emergency declarant cannot vote on their own scope."""
        a1 = self._make_approval("validator:0", "yes", self.private_keys["validator:0"])
        a2 = self._make_approval("validator:1", "yes", self.private_keys["validator:1"])
        result = self.cf.check_consent(
            self.action, [a1, a2], now=NOW, timed_out=False,
            declaring_party="validator:0", round_nonce=self.round_nonce,
        )
        self.assertEqual(result.quorum_met, 1)
        self.assertFalse(result.consent_met)


@unittest.skipUnless(_CONSENT_AVAILABLE, "lokahi_consent not available")
class TestEd25519Signer(unittest.TestCase):
    """Ed25519 public-key signatures."""

    def test_sign_verify_roundtrip(self):
        signer = Ed25519Signer.generate()
        payload = b"hello world"
        signature = signer.sign(payload)
        self.assertTrue(signer.verify(payload, signature))

    def test_wrong_signature_rejected(self):
        signer = Ed25519Signer.generate()
        self.assertFalse(signer.verify(b"hello", "deadbeef" * 8))

    def test_public_key_only_cannot_sign(self):
        """A verifier (public key only) cannot sign — it has no private key."""
        private_signer = Ed25519Signer.generate()
        verifier = Ed25519Verifier.from_public_bytes(private_signer.public_bytes())
        # Verifier has no sign() method
        self.assertFalse(hasattr(verifier, "sign"))
        # But it can verify
        payload = b"test"
        signature = private_signer.sign(payload)
        self.assertTrue(verifier.verify(payload, signature))

    def test_different_keys_different_signatures(self):
        s1 = Ed25519Signer.generate()
        s2 = Ed25519Signer.generate()
        payload = b"same payload"
        sig1 = s1.sign(payload)
        sig2 = s2.sign(payload)
        self.assertNotEqual(sig1, sig2)
        # Each verifier only accepts its own signature
        self.assertTrue(s1.verify(payload, sig1))
        self.assertFalse(s1.verify(payload, sig2))


if __name__ == "__main__":
    unittest.main()
