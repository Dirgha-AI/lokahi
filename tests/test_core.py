# -*- coding: utf-8 -*-
"""Unit tests for the LOKAHI v0.1.2 reference core (32 tests).

Run: python3 tests/test_core.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
import lokahi
from lokahi import Action, Consent, Ledger, Manifest, Signer, canonical, evaluate

KEY = b"prototype-key-not-for-production"
OTHER = b"a-different-key-entirely-000000"
NOW = 1_800_000_000.0  # 2027-01-15, well before the manifest expiry


def make_manifest(signer: Signer, **over) -> Manifest:
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


def make_action(**over) -> Action:
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


class TestManifest(unittest.TestCase):
    def test_canonical_is_order_independent(self):
        a = {"b": 1, "a": [2, 3]}
        b = {"a": [2, 3], "b": 1}
        self.assertEqual(canonical(a), canonical(b))

    def test_sign_verify_roundtrip(self):
        s = Signer(KEY)
        m = make_manifest(s)
        self.assertTrue(m.verify(s))

    def test_tampered_manifest_fails(self):
        s = Signer(KEY)
        m = make_manifest(s)
        m.informational.append("protest_history")
        self.assertFalse(m.verify(s))

    def test_wrong_key_fails(self):
        m = make_manifest(Signer(KEY))
        self.assertFalse(m.verify(Signer(OTHER)))

    def test_short_key_rejected(self):
        with self.assertRaises(ValueError):
            Signer(b"short")

    def test_expiry_parsed(self):
        s = Signer(KEY)
        self.assertFalse(make_manifest(s).expired(NOW))
        self.assertTrue(make_manifest(s, expires="2020-01-01T00:00:00Z").expired(NOW))

    def test_unreadable_or_naive_expiry_is_expired(self):
        m1 = Manifest("n", [], [], "IN-DL", ["IN"], {}, "not-a-date", "s")
        m2 = Manifest("n", [], [], "IN-DL", ["IN"], {}, "2028-03-01T00:00:00", "s")  # missing timezone
        self.assertTrue(m1.expired(NOW))
        self.assertTrue(m2.expired(NOW))

    def test_from_dict_roundtrip_and_missing_fields(self):
        s = Signer(KEY)
        m = make_manifest(s)
        d = m.to_dict()
        d["signature"] = m.signature
        m2 = Manifest.from_dict(d)
        self.assertTrue(m2.verify(s))
        with self.assertRaises(ValueError):
            Manifest.from_dict({"node": "incomplete"})


class TestEvaluation(unittest.TestCase):
    def setUp(self):
        self.signer = Signer(KEY)
        self.manifest = make_manifest(self.signer)

    def run_eval(self, action=None, consents=(), **kw):
        return evaluate(self.manifest, action or make_action(), consents,
                        signer=self.signer, now=NOW, **kw)

    def test_within_manifest_is_compliant(self):
        d = self.run_eval()
        self.assertTrue(d.allowed)
        self.assertEqual(d.reasons, ["within_manifest"])

    def test_unsigned_manifest_denies(self):
        m = make_manifest(self.signer)
        m.signature = ""
        d = evaluate(m, make_action(), signer=self.signer, now=NOW)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["unsigned_manifest"])

    def test_unverifiable_manifest_denies(self):
        m = make_manifest(Signer(OTHER))
        d = evaluate(m, make_action(), signer=self.signer, now=NOW)
        self.assertEqual(d.reasons, ["unverifiable_manifest"])

    def test_expired_manifest_denies(self):
        m = make_manifest(self.signer, expires="2020-01-01T00:00:00Z")
        d = evaluate(m, make_action(), signer=self.signer, now=NOW)
        self.assertEqual(d.reasons, ["expired_manifest"])

    def test_forbidden_target_denies(self):
        d = self.run_eval(make_action(features=["protest_history"]))
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertIn("forbidden_target:protest_history", d.reasons)

    def test_unlisted_feature_denies(self):
        d = self.run_eval(make_action(features=["usage_liters", "phone_number"]))
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertIn("feature_out_of_manifest:phone_number", d.reasons)

    def test_jurisdiction_mismatch_denies(self):
        d = self.run_eval(make_action(jurisdiction="US-NY"))
        self.assertEqual(d.reasons, ["jurisdiction_mismatch"])

    def test_residency_violation_denies(self):
        d = self.run_eval(make_action(stores_in=["IN", "US"]))
        self.assertIn("residency_violation:US", d.reasons)

    def test_emergency_exception_rate_cap_exceeded_denies(self):
        d = self.run_eval(make_action(declared_by="org:mayor", exceptions_used=3))
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["exception_rate_exceeded"])

    def test_emergency_within_cap_still_requires_disjoint_quorum(self):
        d = self.run_eval(make_action(declared_by="org:mayor", exceptions_used=1))
        self.assertEqual(d.verdict, lokahi.ESCALATE)
        self.assertEqual(d.reasons, ["insufficient_disjoint_consent"])

    def test_irreversible_without_quorum_escalates(self):
        d = self.run_eval(make_action(reversible=False))
        self.assertEqual(d.verdict, lokahi.ESCALATE)
        self.assertEqual(d.quorum_required, 3)
        self.assertEqual(d.quorum_met, 0)

    def test_irreversible_with_three_operators_is_compliant(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertTrue(d.allowed)
        self.assertEqual(d.quorum_met, 3)

    def test_agents_do_not_count_as_operators(self):
        consents = [Consent("alice", "node-a", agents=12, decision="yes")]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.ESCALATE)
        self.assertEqual(d.quorum_met, 1)

    def test_population_affecting_needs_quorum(self):
        d = self.run_eval(make_action(reversible=True, population_affecting=True))
        self.assertEqual(d.verdict, lokahi.ESCALATE)

    def test_requester_node_cannot_vote(self):
        consents = [
            Consent("alice", "agent:allocator-2", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["requester_in_ring"])

    def test_requester_operator_cannot_vote(self):
        consents = [
            Consent("org:water-ops", "node-x", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["requester_operator_in_ring"])

    def test_requester_cannot_cast_no_vote_in_ring(self):
        consents = [
            Consent("org:water-ops", "node-x", decision="no"),
            Consent("bob", "node-b", decision="no"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["requester_operator_in_ring"])

    def test_declaring_party_cannot_vote(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False, declared_by="alice"), consents)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["declaring_party_in_ring"])

    def test_suspension_quorum_dissent_freezes_action(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
            Consent("dave", "node-d", decision="no"),
            Consent("eve", "node-e", decision="no"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["suspended_by_ring"])

    def test_empty_operator_or_node_ignored(self):
        consents = [
            Consent("", "node-a", decision="yes"),
            Consent("   ", "node-b", decision="yes"),
            Consent("alice", "", decision="yes"),
            Consent("bob", "node-c", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.ESCALATE)
        self.assertEqual(d.quorum_met, 1)

    def test_equivocating_operator_cannot_count_as_yes(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("alice", "node-a2", decision="no"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.ESCALATE)
        self.assertEqual(d.quorum_met, 2)

    def test_timeout_denies(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
        ]
        d = self.run_eval(make_action(reversible=False), consents, timed_out=True)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["non_response"])


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.signer = Signer(KEY)
        self.manifest = make_manifest(self.signer)

    def test_chain_verifies_and_exports_json(self):
        ledger = Ledger()
        for f in (["usage_liters"], ["protest_history"], ["phone_number"]):
            a = make_action(features=f)
            d = evaluate(self.manifest, a, signer=self.signer, now=NOW)
            ledger.append(d, a, self.manifest)
        self.assertEqual(ledger.verify(), (True, 3))
        self.assertIn("egz-17-water-board", ledger.to_json())

    def test_tamper_and_reordering_detected(self):
        ledger = Ledger()
        for f in (["usage_liters"], ["protest_history"], ["phone_number"]):
            a = make_action(features=f)
            d = evaluate(self.manifest, a, signer=self.signer, now=NOW)
            ledger.append(d, a, self.manifest)
        ledger.entries[1]["verdict"] = "compliant"
        self.assertEqual(ledger.verify(), (False, 1))

        # Reordering test
        ledger2 = Ledger()
        for f in (["usage_liters"], ["protest_history"]):
            a = make_action(features=f)
            d = evaluate(self.manifest, a, signer=self.signer, now=NOW)
            ledger2.append(d, a, self.manifest)
        ledger2.entries[0], ledger2.entries[1] = ledger2.entries[1], ledger2.entries[0]
        self.assertEqual(ledger2.verify(), (False, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
