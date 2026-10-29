# -*- coding: utf-8 -*-
"""Interactive 11-Scenario Walkthrough Demo for LOKAHI Protocol v0.1.2.

Run: python3 demo.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))

from lokahi import Action, Consent, Ledger, Manifest, Signer, evaluate

KEY = b"demo-authority-key-do-not-leak"
NOW = 1_800_000_000.0  # 2027-01-15


def run_demo():
    signer = Signer(KEY)
    manifest = Manifest(
        node="egz-17-water-board",
        informational=["service_address", "usage_liters", "meter_id"],
        forbidden_targets=["residency_status", "protest_history"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 3, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:egz17.example/steward",
    ).sign(signer)

    print("LOKAHI PROTOCOL v0.1.2 — RUNTIME GATEWAY DEMO (15 NORMATIVE RULES)")
    print(f"Protected Node: {manifest.node} (Jurisdiction: {manifest.jurisdiction})")
    print("-" * 72)

    scenarios = [
        ("within manifest (fast path)", Action("agent:allocator", "read", ["usage_liters"], "IN-DL"), (), False),
        ("asks for a forbidden target", Action("agent:allocator", "read", ["protest_history"], "IN-DL"), (), False),
        ("asks for an unlisted feature", Action("agent:allocator", "read", ["phone_number"], "IN-DL"), (), False),
        ("acts in another jurisdiction", Action("agent:allocator", "read", ["usage_liters"], "US-NY"), (), False),
        ("stores data outside residency", Action("agent:allocator", "read", ["usage_liters"], "IN-DL", stores_in=["US"]), (), False),
        ("emergency exception cap exceeded", Action("agent:allocator", "cut_valve", ["usage_liters"], "IN-DL", declared_by="org:mayor", exceptions_used=3), (), False),
        ("irreversible, no consent", Action("agent:allocator", "cut_valve", ["usage_liters"], "IN-DL", reversible=False), (), False),
        ("irreversible, three operators", Action("agent:allocator", "cut_valve", ["usage_liters"], "IN-DL", reversible=False), [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="yes"),
            Consent("carol", "node-c", decision="yes"),
        ], False),
        ("twelve agents, one operator", Action("agent:allocator", "cut_valve", ["usage_liters"], "IN-DL", reversible=False), [
            Consent("alice", "node-a", agents=12, decision="yes"),
        ], False),
        ("2-of-3 dissenting operators freeze", Action("agent:allocator", "cut_valve", ["usage_liters"], "IN-DL", reversible=False), [
            Consent("alice", "node-a", decision="yes"),
            Consent("bob", "node-b", decision="no"),
            Consent("carol", "node-c", decision="no"),
        ], False),
        ("timeout, silence counts as no", Action("agent:allocator", "cut_valve", ["usage_liters"], "IN-DL", reversible=False), (), True),
    ]

    ledger = Ledger()
    for name, action, consents, timed_out in scenarios:
        dec = evaluate(manifest, action, consents, signer=signer, now=NOW, timed_out=timed_out)
        ledger.append(dec, action, manifest)
        print(f"{name:<36} -> {dec.verdict.upper():<9} {dec.reasons}")

    print("-" * 72)
    ok, count = ledger.verify()
    print(f"ledger: {count} entries, chain valid = {ok}")

    # Demonstrate tamper-evidence
    ledger.entries[2]["verdict"] = "compliant-tampered"
    tamper_ok, bad_idx = ledger.verify()
    print(f"after modifying entry 2: chain valid = ({tamper_ok}, index {bad_idx})")


if __name__ == "__main__":
    run_demo()
