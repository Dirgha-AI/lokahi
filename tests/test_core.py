# -*- coding: utf-8 -*-
"""Unit tests for LOKAHI v0.1.2, Ghostproof (Paper 5), and Kinetic Ahimsa (Paper 6).

Run: py tests/test_core.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
import ghostproof
import lokahi
import mcp_stdio_proxy
import run_wargame_workshop
from ghostproof import DelegationTask, audit_hesitation_seams, parse_llm_logprobs, route_delegation
from lokahi import Action, Consent, Ledger, Manifest, Signer, canonical, compute_action_digest, evaluate

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

    def test_stateful_ledger_sliding_window_exception_cap(self):
        ledger = Ledger()
        for i in range(3):
            act = make_action(declared_by="org:mayor", exceptions_used=i)
            dec = self.run_eval(act, ledger=ledger)
            ledger.append(dec, act, self.manifest, ts=NOW - 3600)
        # 4th call within 24h window with exceptions_used=0 is still caught by stateful ledger!
        d4 = self.run_eval(make_action(declared_by="org:mayor", exceptions_used=0), ledger=ledger)
        self.assertEqual(d4.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d4.reasons, ["exception_rate_exceeded"])

    def test_forged_forbidden_calls_do_not_exhaust_exception_budget(self):
        """Verify Rule 8 DoS fix: rejected/forbidden spam with declared_by set cannot exhaust the node's emergency budget."""
        ledger = Ledger()
        for _ in range(5):
            bad_act = make_action(declared_by="org:attacker", jurisdiction="US-NY", exceptions_used=0)
            bad_dec = self.run_eval(bad_act, ledger=ledger)
            self.assertEqual(bad_dec.verdict, lokahi.FORBIDDEN)
            ledger.append(bad_dec, bad_act, self.manifest, ts=NOW - 600)
        # Valid emergency declaration still succeeds into ESCALATE because FORBIDDEN spam did not consume slots
        valid_emerg = self.run_eval(make_action(declared_by="org:mayor", exceptions_used=0), ledger=ledger)
        self.assertEqual(valid_emerg.verdict, lokahi.ESCALATE)
        self.assertEqual(valid_emerg.reasons, ["insufficient_disjoint_consent"])

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

    def test_requester_cannot_mask_two_independent_no_votes(self):
        """Verify D-02 fix: two non-conflicted 'no' votes trigger suspended_by_ring even if requester injects a vote."""
        consents = [
            Consent("org:water-ops", "node-x", decision="yes"),
            Consent("dave", "node-d", decision="no"),
            Consent("eve", "node-e", decision="no"),
        ]
        d = self.run_eval(make_action(reversible=False), consents)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["suspended_by_ring"])

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

    def test_kinetic_ahimsa_physical_envelope_interlock(self):
        phys_m = make_manifest(
            self.signer,
            physical_envelope={
                "max_contact_force_n": 6.1,
                "max_joint_torque_nm": 45.0,
                "workspace_bounds_m": [0.0, 2.0, -1.0, 1.0, 0.2, 2.4],
            },
        )
        ok_act = make_action(physical_params={"contact_force_n": 5.0, "joint_torque_nm": 20.0, "waypoint_m": [1.0, 0.0, 1.0]})
        d_ok = evaluate(phys_m, ok_act, signer=self.signer, now=NOW)
        self.assertTrue(d_ok.allowed)

        over_force = make_action(physical_params={"contact_force_n": 18.0, "joint_torque_nm": 20.0, "waypoint_m": [1.0, 0.0, 1.0]})
        d_over = evaluate(phys_m, over_force, signer=self.signer, now=NOW)
        self.assertEqual(d_over.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d_over.actuator_interlock, "safe_mechanical_hold")

    def test_kinematic_stopping_distance_cbf_interlock(self):
        """Verify Paper 6 Kinematic Stopping-Distance Control Barrier Function (v^2 / 2*a_max)."""
        phys_m = make_manifest(
            self.signer,
            physical_envelope={
                "max_contact_force_n": 6.1,
                "max_cartesian_velocity_ms": 0.5,
                "max_decel_ms2": 1.0,
                "workspace_bounds_m": [0.0, 2.0, -1.0, 1.0, 0.2, 2.4],
            },
        )
        # At v=0.45 m/s and a_max=1.0 m/s^2, d_stop = 0.10125 m; waypoint x=1.95 has only 0.05 m margin to x_max=2.0
        overshoot_act = make_action(
            physical_params={"contact_force_n": 5.0, "cartesian_velocity_ms": 0.45, "waypoint_m": [1.95, 0.0, 1.0]}
        )
        d_cbf = evaluate(phys_m, overshoot_act, signer=self.signer, now=NOW)
        self.assertEqual(d_cbf.verdict, lokahi.FORBIDDEN)
        self.assertTrue(any(r.startswith("physical_stopping_distance_exceeded") for r in d_cbf.reasons))

    def test_replay_bound_action_digest_rejects_mismatched_consent(self):
        """Verify Consent votes bound to a different action digest are rejected from quorum."""
        act1 = make_action(tool="cutoff_valve_A", reversible=False)
        act2 = make_action(tool="cutoff_valve_B", reversible=False)
        digest_act1 = compute_action_digest(self.manifest, act1)
        replayed_consents = [
            Consent("alice", "node-a", decision="yes", bound_action_digest=digest_act1),
            Consent("bob", "node-b", decision="yes", bound_action_digest=digest_act1),
            Consent("carol", "node-c", decision="yes", bound_action_digest=digest_act1),
        ]
        # Valid for act1, rejected as replay for act2
        self.assertTrue(self.run_eval(act1, replayed_consents).allowed)
        d_replay = self.run_eval(act2, replayed_consents)
        self.assertEqual(d_replay.verdict, lokahi.ESCALATE)
        self.assertEqual(d_replay.quorum_met, 0)

    def test_mcp_stdio_proxy_jsonrpc_interceptor(self):
        """Verify real-world MCP JSON-RPC 2.0 `tools/call` interception and ledger receipt."""
        gw = mcp_stdio_proxy.LokahiMCPGateway(self.manifest, signer=self.signer)
        ok_rpc = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "read_meter", "arguments": {"usage_liters": True}},
        }
        res_ok = gw.intercept(ok_rpc, now=NOW)
        self.assertTrue(res_ok["allowed"])
        self.assertEqual(res_ok["lokahi_receipt"]["verdict"], "compliant")

        bad_rpc = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "profile_citizen", "arguments": {"protest_history": True}},
        }
        res_bad = gw.intercept(bad_rpc, now=NOW)
        self.assertFalse(res_bad["allowed"])
        self.assertEqual(res_bad["error"]["code"], mcp_stdio_proxy.JSONRPC_ERR_FORBIDDEN)

    def test_wargame_workshop_five_rounds_pass(self):
        """Verify all 5 American Emperor x LOKAHI Wargame Workshop rounds execute and pass."""
        rc = run_wargame_workshop.run_wargame(selected_round=None, interactive=False)
        self.assertEqual(rc, 0)


class TestGhostproof(unittest.TestCase):
    def test_three_axis_router_fast_path_and_escalation(self):
        routine = DelegationTask("T1", "Infra", reversibility=1.0, empirical_human_lift=-0.01, normative_legitimacy=0)
        d1 = route_delegation(routine)
        self.assertEqual(d1.route, ghostproof.AUTONOMOUS_FAST_PATH)
        self.assertFalse(d1.interrupts_human)

        high_stakes = DelegationTask(
            "T2",
            "Clinical",
            reversibility=0.0,
            empirical_human_lift=0.12,
            normative_legitimacy=1,
            token_distributions=[[0.42, 0.39, 0.19]],
        )
        d2 = route_delegation(high_stakes)
        self.assertEqual(d2.route, ghostproof.SEAM_EXPOSED_DELIBERATION)
        self.assertTrue(d2.hesitation.flagged)
        self.assertTrue(d2.hesitation.requires_tradeoff_endorsement)

    def test_hesitation_seam_entropy_and_margin(self):
        seam = audit_hesitation_seams([[0.45, 0.43, 0.12]])
        self.assertTrue(seam.flagged)
        self.assertLess(seam.min_top2_margin, 0.15)

    def test_parse_openai_or_ollama_llm_logprobs(self):
        """Verify Ghostproof parses real OpenAI/vLLM/Ollama top_logprobs payloads into hesitation seams."""
        raw_logprobs = [
            {
                "token": "Approve",
                "top_logprobs": [
                    {"token": "Approve", "logprob": -0.82},
                    {"token": "Defer", "logprob": -0.91},
                    {"token": "Reject", "logprob": -1.85},
                ],
            }
        ]
        dists = parse_llm_logprobs(raw_logprobs)
        self.assertEqual(len(dists), 1)
        seam = audit_hesitation_seams(raw_logprobs)
        self.assertTrue(seam.flagged)
        self.assertLess(seam.min_top2_margin, 0.10)

    def test_somatic_sparring_node_and_brier_calibration(self):
        sparring = DelegationTask(
            "T3",
            "Research",
            reversibility=1.0,
            empirical_human_lift=-0.02,
            normative_legitimacy=0,
            is_sparring_node=True,
            crux_pod_size=3,
        )
        d_spar = route_delegation(sparring)
        self.assertEqual(d_spar.route, ghostproof.DEVELOPMENTAL_SPARRING_NODE)
        self.assertTrue(d_spar.requires_blind_precommitment)
        self.assertEqual(d_spar.crux_pod_size, 3)

        judgments = [
            ghostproof.PreRegisteredJudgment("T2", ["alice", "bob", "carol"], "Branch B", "Hypothesis 1", 0.85, 1),
            ghostproof.PreRegisteredJudgment("T3", ["alice", "bob", "carol"], "Branch A", "Hypothesis 2", 0.20, 0),
        ]
        cal = ghostproof.evaluate_brier_calibration(judgments)
        self.assertEqual(cal["count"], 2)
        self.assertTrue(cal["well_calibrated"])
        self.assertLess(cal["brier_score"], 0.05)


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

        ledger2 = Ledger()
        for f in (["usage_liters"], ["protest_history"]):
            a = make_action(features=f)
            d = evaluate(self.manifest, a, signer=self.signer, now=NOW)
            ledger2.append(d, a, self.manifest)
        ledger2.entries[0], ledger2.entries[1] = ledger2.entries[1], ledger2.entries[0]
        self.assertEqual(ledger2.verify(), (False, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
