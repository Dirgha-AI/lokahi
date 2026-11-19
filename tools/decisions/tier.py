# -*- coding: utf-8 -*-
"""The experimental decisions tier: LOKAHI first, then charter rules, then the model.

Order (docs/decisions/CHARTER.md):

  1. LOKAHI (DDM-00). The deterministic gate decides first. If it refuses or
     escalates, that is final and the model is not consulted.
  2. Hard charter rules in plain code: DDM-02 (approval timed out), DDM-05
     (data outside the allowed residency), DDM-01 (irreversible needs a person).
  3. The model runs last. It can keep a decision or make it stricter
     (ALLOW -> ESCALATE -> REFUSE). It can never make one looser.

When a manifest is in play, the tool class comes from the signed manifest's
tool policy, never from the agent. Standard library only.
"""
from __future__ import annotations

from .charter import (
    ALLOW,
    ESCALATE,
    REFUSE,
    RULE_TEXT,
    SEVERITY,
    hard_rules,
    normalize,
)
from .model import DEFAULT_MODEL_PATH, load_model

# Data features the model was trained on (schema enum). Manifest feature names
# that match one of these are passed on; others are not model inputs.
MODEL_DATA_FEATURES = {
    "none", "public_info", "aggregate_stats", "personal_name", "contact", "health", "minor",
    "biometric", "precise_location", "financial", "government_id", "credentials",
}


def system_decision(row: dict, model, approved_by_quorum: bool = False) -> dict:
    """Hard charter rules, then the model; the stricter decision wins.

    `approved_by_quorum` is True only when LOKAHI allowed an irreversible call
    because enough distinct people signed yes. DDM-01 is then already met, but
    the model can still escalate for another reason, or refuse.
    """
    hard = hard_rules(row)
    if approved_by_quorum and hard == (ESCALATE, "DDM-01"):
        hard = None
    d, code, conf = model.predict([row])[0]
    model_said = d
    if approved_by_quorum and (d, code) == (ESCALATE, "DDM-01"):
        d, code = ALLOW, "DDM-00"
    source = "model"
    if hard is not None and SEVERITY[hard[0]] >= SEVERITY[d]:
        d, code, source = hard[0], hard[1], "charter_rule"
    if source == "model":
        why = f"{RULE_TEXT[code]} (model {model.version}, confidence {conf:.2f})"
    else:
        why = f"{RULE_TEXT[code]} (rule checked in code; model said {model_said})"
    return {"decision": d, "reason_code": code, "rationale": why, "model_version": model.version, "source": source}


class DecisionTier:
    """Reviews calls that LOKAHI allowed and may only make them stricter.

    Pass an instance (or True) as `experimental_decisions=` to
    `LokahiMCPGateway`. `model` is anything with `.version` and
    `.predict(rows, cautious=True) -> [(decision, reason_code, confidence)]`;
    by default the bundled JSON model is loaded.
    """

    def __init__(self, model=None, model_path: str = DEFAULT_MODEL_PATH):
        self.model = model if model is not None else load_model(model_path)

    def row_for_call(self, tool_name: str, arguments: dict, policy: dict, features: list[str], quorum_met: bool,
                     purpose: str = "") -> dict:
        """Build the model input from the signed manifest's policy, not from the agent.

        `purpose` is LOKAHI's own record of the call (`mcp:<tool>`), not a claim by the agent.
        """
        feats = sorted(f for f in features if f in MODEL_DATA_FEATURES) or ["none"]
        return normalize({
            "tool_name": tool_name,
            "tool_class": policy["class"],
            "arguments": arguments,
            "data_features": feats,
            # LOKAHI has already enforced residency and storage for this call (DDM-00).
            "destination": {"jurisdiction": "LOCAL", "allowed_residency": ["LOCAL"]},
            "requester_context": {"role": "agent", "purpose": purpose, "approval_status": "approved" if quorum_met else "not_requested"},
        })

    def review(self, tool_name: str, arguments: dict, policy: dict, features: list[str], lokahi_reasons: list[str],
               purpose: str = "") -> dict:
        """Decide one call LOKAHI allowed. Any failure escalates (fail closed)."""
        try:
            quorum = "quorum_met" in lokahi_reasons
            row = self.row_for_call(tool_name, arguments, policy, features, quorum, purpose)
            out = system_decision(row, self.model, approved_by_quorum=quorum)
            if out["decision"] not in SEVERITY:
                raise ValueError(f"model returned unknown decision {out['decision']!r}")
        except Exception as exc:  # noqa: BLE001 - an experimental layer must not open the gate
            out = {"decision": ESCALATE, "reason_code": "DDM-09",
                   "rationale": f"{RULE_TEXT['DDM-09']} (decisions tier failed: {type(exc).__name__})",
                   "model_version": getattr(self.model, "version", "unknown"), "source": "tier_error"}
        out["lokahi_reasons"] = list(lokahi_reasons)
        return out


def decide(call: dict, model=None) -> dict:
    """Decide one call with no manifest: hard charter rules, then the model.

    Without a manifest, the tool class is whatever the caller states; a missing
    or unknown class counts as irreversible. With a manifest, use
    `LokahiMCPGateway(..., experimental_decisions=True)` instead, so LOKAHI
    decides first and the tool class comes from the signed policy.
    """
    return system_decision(normalize(call), model if model is not None else load_model())


def combine(lokahi_verdict: str, tier_decision: str) -> str:
    """Final verdict in LOKAHI terms: the stricter of LOKAHI's and the tier's."""
    as_label = {"compliant": ALLOW, "escalate": ESCALATE, "forbidden": REFUSE}[lokahi_verdict]
    if tier_decision not in SEVERITY:
        tier_decision = ESCALATE  # an unknown answer is never a yes
    final = tier_decision if SEVERITY[tier_decision] > SEVERITY[as_label] else as_label
    return {ALLOW: "compliant", ESCALATE: "escalate", REFUSE: "forbidden"}[final]
