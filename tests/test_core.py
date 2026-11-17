# -*- coding: utf-8 -*-
"""Unit tests for LOKAHI v0.1.3, Ghostproof, and Kinetic Ahimsa.

Run: py tests/test_core.py
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
import ghostproof
import lokahi
import mcp_stdio_proxy
import run_wargame_workshop
from ghostproof import DelegationTask, audit_hesitation_seams, parse_llm_logprobs, route_delegation
from lokahi import (
    Action,
    Approval,
    Consent,
    Ledger,
    Manifest,
    Signer,
    accept_approvals,
    bind_consents,
    canonical,
    compute_action_digest,
    evaluate,
)

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
        # The test harness is the trusted channel that collected these votes,
        # so it binds them to the action under test (v0.1.3 requires binding).
        action = action or make_action()
        bound = bind_consents(consents, self.manifest, action)
        return evaluate(self.manifest, action, bound, signer=self.signer, now=NOW, **kw)

    def test_missing_verification_key_denies(self):
        """v0.1.3: no key means the signature cannot be checked, so the manifest is unverifiable."""
        d = evaluate(self.manifest, make_action(), signer=None, now=NOW)
        self.assertEqual(d.verdict, lokahi.FORBIDDEN)
        self.assertEqual(d.reasons, ["unverifiable_manifest"])

    def test_unbound_consents_do_not_count_by_default(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = evaluate(self.manifest, make_action(reversible=False), consents, signer=self.signer, now=NOW)
        self.assertEqual(d.verdict, lokahi.ESCALATE)
        self.assertEqual(d.quorum_met, 0)

    def test_unbound_consents_opt_out_is_explicit(self):
        consents = [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ]
        d = evaluate(self.manifest, make_action(reversible=False), consents, signer=self.signer, now=NOW,
                     require_bound_consent=False)
        self.assertTrue(d.allowed)

    def test_v01_manifest_canonical_form_unchanged(self):
        """Manifests without v0.1.3 fields keep the same signed bytes, so old signatures still verify."""
        d = self.manifest.to_dict()
        self.assertNotIn("tools", d)
        self.assertNotIn("approval_window_seconds", d)
        self.assertEqual(
            sorted(d),
            sorted(["node", "informational", "forbidden_targets", "jurisdiction", "residency",
                    "exceptions", "expires", "steward"]),
        )

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
        """Verify the kinematic Stopping-Distance Control Barrier Function (v^2 / 2*a_max)."""
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
        """Verify MCP JSON-RPC 2.0 `tools/call` interception and ledger receipt."""
        m = make_manifest(self.signer, tools={
            "read_meter": {"class": "read", "args": {"usage_liters": "usage_liters"}},
            "profile_citizen": {"class": "read", "args": {}},
        })
        gw = mcp_stdio_proxy.LokahiMCPGateway(m, signer=self.signer)
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
        self.assertIn("forbidden_target:protest_history", res_bad["error"]["data"]["reasons"])

    def test_wargame_workshop_five_rounds_pass(self):
        """Verify all 5 American Emperor x LOKAHI Wargame Workshop rounds execute and pass."""
        with redirect_stdout(io.StringIO()):
            rc = run_wargame_workshop.run_wargame(selected_round=None, interactive=False)
        self.assertEqual(rc, 0)


GATEWAY_TOOLS = {
    "read_meter": {"class": "read", "args": {"meter_id": "meter_id", "usage_liters": "usage_liters"}},
    "cutoff_valve": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
    "upload_report": {"class": "write", "stores_in": ["US"], "args": {"usage_liters": "usage_liters"}},
}
APPROVER_KEYS = {
    "alice": b"approver-alice-key-0000000000000",
    "bob": b"approver-bob-key-00000000000000",
    "carol": b"approver-carol-key-0000000000000",
}
WINDOW = 600
APPROVAL_EXPIRY = "2027-02-01T00:00:00Z"  # after NOW


def rpc(tool, arguments, msg_id=1, **params_extra):
    params = {"name": tool, "arguments": arguments}
    params.update(params_extra)
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}


def reasons_of(res):
    return res["lokahi_receipt"]["reasons"] if res["allowed"] else res["error"]["data"]["reasons"]


class TestGateway(unittest.TestCase):
    """v0.1.3 trust boundary: the agent's request is data, never control."""

    def setUp(self):
        self.signer = Signer(KEY)
        self.manifest = make_manifest(self.signer, tools=GATEWAY_TOOLS, approval_window_seconds=WINDOW)
        self.approvers = {op: Signer(k) for op, k in APPROVER_KEYS.items()}
        self.store = mcp_stdio_proxy.ApprovalStore()
        self.gw = mcp_stdio_proxy.LokahiMCPGateway(
            self.manifest,
            self.signer,
            approvers=self.approvers,
            approval_store=self.store,
            default_requester="agent:allocator-2",
            default_operator="org:water-ops",
        )

    def approve(self, digest, operators=("alice", "bob", "carol"), decision="yes", key=None,
                expires=APPROVAL_EXPIRY, tag="r1"):
        for op in operators:
            signer = key or self.approvers[op]
            self.store.add(Approval(op, f"device-{op}", decision, digest, expires, f"{tag}-{op}").sign(signer))

    def open_round(self, arguments, now=NOW):
        res = self.gw.intercept(rpc("cutoff_valve", arguments), now=now)
        self.assertEqual(res["error"]["data"]["verdict"], lokahi.ESCALATE)
        return res["error"]["data"]["action_digest"]

    def test_benign_read_is_compliant(self):
        res = self.gw.intercept(rpc("read_meter", {"meter_id": "M-1", "usage_liters": True}), now=NOW)
        self.assertTrue(res["allowed"])
        self.assertEqual(reasons_of(res), ["within_manifest"])

    def test_agent_supplied_approvals_are_refused(self):
        forged = {"consents": [{"operator": op, "node": "n", "decision": "yes"} for op in APPROVER_KEYS]}
        res = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-1"}, _lokahi=forged), now=NOW)
        self.assertFalse(res["allowed"])
        self.assertEqual(reasons_of(res), ["agent_supplied_control_metadata"])

    def test_agent_supplied_facts_inside_arguments_are_refused(self):
        res = self.gw.intercept(
            rpc("cutoff_valve", {"meter_id": "M-1", "_lokahi": {"reversible": True, "timed_out": False}}), now=NOW
        )
        self.assertEqual(reasons_of(res), ["agent_supplied_control_metadata"])

    def test_unknown_tool_is_denied(self):
        res = self.gw.intercept(rpc("drop_all_records", {}), now=NOW)
        self.assertEqual(reasons_of(res), ["tool_not_in_manifest:drop_all_records"])

    def test_unknown_argument_is_denied(self):
        res = self.gw.intercept(rpc("read_meter", {"meter_id": "M-1", "phone_number": "x"}), now=NOW)
        self.assertEqual(reasons_of(res), ["argument_out_of_policy:phone_number"])

    def test_forbidden_argument_names_the_forbidden_target(self):
        res = self.gw.intercept(rpc("read_meter", {"meter_id": "M-1", "protest_history": True}), now=NOW)
        self.assertEqual(reasons_of(res), ["forbidden_target:protest_history"])

    def test_storage_location_comes_from_policy_not_the_agent(self):
        res = self.gw.intercept(rpc("upload_report", {"usage_liters": 12}), now=NOW)
        self.assertFalse(res["allowed"])
        self.assertEqual(reasons_of(res), ["residency_violation:US"])

    def test_gateway_clock_owns_the_timeout(self):
        self.open_round({"meter_id": "M-1"}, now=NOW)
        retry = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-1"}), now=NOW + WINDOW - 1)
        self.assertEqual(retry["error"]["data"]["verdict"], lokahi.ESCALATE)
        self.assertEqual(retry["error"]["data"]["approval_window_closes"], NOW + WINDOW)
        late = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-1"}), now=NOW + WINDOW)
        self.assertEqual(reasons_of(late), ["non_response"])

    def test_signed_out_of_band_approvals_unlock_irreversible_call(self):
        digest = self.open_round({"meter_id": "M-2"})
        self.approve(digest)
        res = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-2"}), now=NOW + 60)
        self.assertTrue(res["allowed"])
        self.assertEqual(reasons_of(res), ["quorum_met"])

    def test_forged_expired_and_replayed_approvals_do_not_count(self):
        digest = self.open_round({"meter_id": "M-3"})
        self.approve(digest, key=Signer(b"attacker-key-pretending-approver"), tag="forged")
        self.approve(digest, expires="2026-12-01T00:00:00Z", tag="expired")
        other = self.open_round({"meter_id": "M-4"})
        self.approve(other, tag="other-valve")
        res = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-3"}), now=NOW + 60)
        self.assertEqual(res["error"]["data"]["verdict"], lokahi.ESCALATE)
        self.assertEqual(res["error"]["data"]["quorum_met"], 0)

    def test_approval_is_bound_to_the_exact_arguments(self):
        digest = self.open_round({"meter_id": "M-5"})
        self.approve(digest)
        swapped = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-6"}), now=NOW + 60)
        self.assertEqual(swapped["error"]["data"]["verdict"], lokahi.ESCALATE)

    def test_duplicate_nonce_counts_once(self):
        digest = self.open_round({"meter_id": "M-7"})
        for op in ("alice", "bob"):
            ap = Approval(op, f"device-{op}", "yes", digest, APPROVAL_EXPIRY, "same-nonce").sign(self.approvers[op])
            self.store.add(ap)
        consents = accept_approvals(self.store.load(), self.approvers, digest, NOW)
        self.assertEqual(len(consents), 1)

    def test_operator_running_the_agent_cannot_approve(self):
        runner_key = Signer(b"approver-water-ops-key-000000000")
        self.approvers["org:water-ops"] = runner_key
        self.gw.approvers["org:water-ops"] = runner_key  # the operator is also a registered approver
        digest = self.open_round({"meter_id": "M-8"})
        self.approve(digest, operators=("org:water-ops", "alice", "bob"))
        res = self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-8"}), now=NOW + 60)
        self.assertEqual(reasons_of(res), ["requester_operator_in_ring"])

    def test_gateway_keeps_its_own_copy_of_approvers(self):
        """Approvers registered after start-up are not trusted until the gateway is reconfigured."""
        self.approvers["mallory"] = Signer(b"approver-mallory-key-00000000000")
        self.assertNotIn("mallory", self.gw.approvers)

    def test_approve_cli_writes_an_approval_the_gateway_accepts(self):
        import lokahi_approve

        digest = self.open_round({"meter_id": "M-9"})
        with tempfile.TemporaryDirectory() as tmp:
            approvals_path = os.path.join(tmp, "approvals.jsonl")
            for op, k in APPROVER_KEYS.items():
                key_path = os.path.join(tmp, f"{op}.key")
                with open(key_path, "w", encoding="utf-8") as f:
                    f.write(k.decode())
                with redirect_stdout(io.StringIO()):
                    rc = lokahi_approve.main([
                        "--key-file", key_path, "--operator", op, "--node", f"device-{op}",
                        "--digest", digest, "--decision", "yes", "--approvals", approvals_path,
                    ])
                self.assertEqual(rc, 0)
            store = mcp_stdio_proxy.ApprovalStore(approvals_path)
            # The CLI stamps a real expiry (now + 1 h), so check against the real clock.
            consents = accept_approvals(store.load(), self.approvers, digest)
            self.assertEqual(sorted(c.operator for c in consents), sorted(APPROVER_KEYS))

    def test_sign_cli_signs_the_example_manifest_for_the_gateway(self):
        """The README how-to: keygen, sign examples/editorial-desk.manifest.json, gate a call."""
        import lokahi_sign

        example = os.path.join(os.path.dirname(__file__), "..", "examples", "editorial-desk.manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            key_path = os.path.join(tmp, "manifest.key")
            signed_path = os.path.join(tmp, "manifest.json")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(lokahi_sign.main(["keygen", "--out", key_path]), 0)
                self.assertEqual(lokahi_sign.main(["manifest", "--key-file", key_path, "--in", example, "--out", signed_path]), 0)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(lokahi_sign.main(["keygen", "--out", key_path]), 2)  # never overwrites silently
            with open(signed_path, encoding="utf-8") as f:
                manifest = Manifest.from_dict(json.load(f))
            signer = Signer(lokahi.load_key(key_path))
            self.assertTrue(manifest.verify(signer))
            gw = mcp_stdio_proxy.LokahiMCPGateway(manifest, signer)
            ok = gw.intercept(rpc("summarize_submission", {"submission_id": "S-1"}), now=NOW)
            self.assertTrue(ok["allowed"])
            upload = gw.intercept(rpc("send_to_cloud_model", {"manuscript_text": "..."}), now=NOW)
            self.assertEqual(reasons_of(upload), ["residency_violation:US"])

    def test_gateway_refuses_to_start_without_a_key(self):
        with self.assertRaises(mcp_stdio_proxy.GatewayConfigError):
            mcp_stdio_proxy.LokahiMCPGateway(self.manifest, None)

    def test_manifest_without_tool_policy_denies_every_call(self):
        gw = mcp_stdio_proxy.LokahiMCPGateway(make_manifest(self.signer), self.signer)
        res = gw.intercept(rpc("read_meter", {"usage_liters": True}), now=NOW)
        self.assertEqual(reasons_of(res), ["no_tool_policy"])

    def test_wrongly_signed_manifest_is_denied_before_anything_else(self):
        forged = make_manifest(Signer(OTHER), tools=GATEWAY_TOOLS)
        gw = mcp_stdio_proxy.LokahiMCPGateway(forged, self.signer)
        res = gw.intercept(rpc("read_meter", {"usage_liters": True}, _lokahi={"timed_out": False}), now=NOW)
        self.assertEqual(reasons_of(res), ["unverifiable_manifest"])

    def test_every_decision_is_in_the_ledger(self):
        self.gw.intercept(rpc("read_meter", {"meter_id": "M-1"}), now=NOW)
        self.gw.intercept(rpc("drop_all_records", {}), now=NOW)
        self.gw.intercept(rpc("cutoff_valve", {"meter_id": "M-1"}, _lokahi={}), now=NOW)
        ok, count = self.gw.ledger.verify()
        self.assertTrue(ok)
        self.assertEqual(count, 3)
        self.assertEqual([e["verdict"] for e in self.gw.ledger.entries], ["compliant", "forbidden", "forbidden"])

    def test_cli_refuses_to_start_without_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "manifest.json")
            d = self.manifest.to_dict()
            d["signature"] = self.manifest.signature
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f)
            saved = os.environ.pop("LOKAHI_MANIFEST_KEY", None)
            try:
                with redirect_stderr(io.StringIO()) as err:
                    rc = mcp_stdio_proxy.main(["--manifest", path])
            finally:
                if saved is not None:
                    os.environ["LOKAHI_MANIFEST_KEY"] = saved
            self.assertEqual(rc, 2)
            self.assertIn("refusing to start", err.getvalue())

    def test_cli_end_to_end_with_key_approvers_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            def write(name, text):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
                return p

            d = self.manifest.to_dict()
            d["signature"] = self.manifest.signature
            manifest_path = write("manifest.json", json.dumps(d))
            key_path = write("manifest.key", KEY.decode())
            for op, k in APPROVER_KEYS.items():
                write(f"{op}.key", k.decode())
            approvers_path = write("approvers.json", json.dumps({op: f"{op}.key" for op in APPROVER_KEYS}))
            ledger_path = os.path.join(tmp, "ledger.jsonl")
            lines = "\n".join([
                json.dumps(rpc("read_meter", {"meter_id": "M-1"}, msg_id=1)),
                json.dumps(rpc("drop_all_records", {}, msg_id=2)),
                "not json",
            ]) + "\n"
            saved_stdin = sys.stdin
            sys.stdin = io.StringIO(lines)
            try:
                with redirect_stdout(io.StringIO()) as out:
                    rc = mcp_stdio_proxy.main([
                        "--manifest", manifest_path, "--key-file", key_path,
                        "--approvers", approvers_path, "--approvals", os.path.join(tmp, "approvals.jsonl"),
                        "--ledger", ledger_path,
                    ])
            finally:
                sys.stdin = saved_stdin
            self.assertEqual(rc, 0)
            results = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
            self.assertEqual([r["allowed"] for r in results], [True, False, False])
            self.assertEqual(results[2]["error"]["code"], mcp_stdio_proxy.JSONRPC_ERR_PARSE)
            with open(ledger_path, encoding="utf-8") as f:
                self.assertEqual(len(f.read().splitlines()), 2)


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
