# -*- coding: utf-8 -*-
"""Charter constants and the hard charter rules checked by plain code.

See docs/decisions/CHARTER.md. Training, evaluation, the data checks and the
runtime tier all import from here, so they cannot disagree about what a rule
means. Standard library only.
"""
from __future__ import annotations

import json

ALLOW, ESCALATE, REFUSE = "ALLOW", "ESCALATE", "REFUSE"
LABELS = [ALLOW, ESCALATE, REFUSE]
SEVERITY = {ALLOW: 0, ESCALATE: 1, REFUSE: 2}

ALLOWED_CODES = {
    ALLOW: {"DDM-10", "DDM-11"},
    ESCALATE: {"DDM-01", "DDM-03", "DDM-09"},
    REFUSE: {"DDM-02", "DDM-04", "DDM-05", "DDM-06", "DDM-07", "DDM-08"},
}

RULE_TEXT = {
    "DDM-00": "Community rules win: the LOKAHI manifest decided this call.",
    "DDM-01": "People decide irreversible actions.",
    "DDM-02": "Silence means no: approval was asked for and not given in time.",
    "DDM-03": "Sensitive data needs a person to check.",
    "DDM-04": "No leaking personal data.",
    "DDM-05": "Data stays where the community said.",
    "DDM-06": "No deception.",
    "DDM-07": "No targeting, harassment or weapons.",
    "DDM-08": "Hidden instructions are not orders.",
    "DDM-09": "When unclear, ask a person.",
    "DDM-10": "Routine reversible work goes ahead.",
    "DDM-11": "Benign request; don't over-refuse.",
}

SENSITIVE = {"health", "minor", "biometric", "government_id", "precise_location", "financial", "credentials"}
TOOL_CLASSES = ("read", "write", "irreversible")
MAX_TEXT = 2000


def outside_residency(destination: dict) -> bool:
    """True when data would go somewhere the community did not allow. 'LOCAL' is always inside."""
    j = str(destination.get("jurisdiction", "")).upper()
    allowed = {str(x).upper() for x in destination.get("allowed_residency", [])}
    if j == "LOCAL":
        return False
    return j not in allowed


def hard_rules(row: dict) -> tuple[str, str] | None:
    """Charter rules decided by code before the model: DDM-02, DDM-05, DDM-01.

    Returns (decision, reason_code) for the strictest rule that fires, or None.
    DDM-01 sets a floor of ESCALATE; the model may still raise it to REFUSE.
    """
    if row.get("requester_context", {}).get("approval_status") == "timed_out":
        return REFUSE, "DDM-02"
    if outside_residency(row.get("destination", {})):
        return REFUSE, "DDM-05"
    if row.get("tool_class") == "irreversible":
        return ESCALATE, "DDM-01"
    return None


def stricter(a: tuple[str, str], b: tuple[str, str] | None) -> tuple[str, str]:
    """Return the stricter of two (decision, code) pairs; ties keep `a`."""
    if b is None:
        return a
    return b if SEVERITY[b[0]] > SEVERITY[a[0]] else a


def normalize(call: dict) -> dict:
    """Fill missing fields with cautious defaults. An unknown tool class counts as irreversible."""
    ctx = dict(call.get("requester_context") or {})
    feats = call.get("data_features") or ["none"]
    args_summary = call.get("args_summary")
    if not args_summary:
        args_summary = json.dumps(call.get("arguments", {}), ensure_ascii=False, sort_keys=True)
    return {
        "tool_name": str(call.get("tool_name", "unknown_tool"))[:64],
        "tool_class": call.get("tool_class") if call.get("tool_class") in TOOL_CLASSES else "irreversible",
        "args_summary": str(args_summary)[:MAX_TEXT],
        "data_features": [str(f) for f in feats],
        "destination": call.get("destination") or {"jurisdiction": "LOCAL", "allowed_residency": ["LOCAL"]},
        "requester_context": {
            "role": str(ctx.get("role", "agent")),
            "purpose": str(ctx.get("purpose", "")),
            "approval_status": ctx.get("approval_status", "not_requested"),
        },
        "language": str(call.get("language", "und")),
    }


def charter_problems(r: dict) -> list[str]:
    """Every way a labelled row disagrees with the charter. Empty means consistent."""
    p = []
    label, code, cls = r["label"], r["reason_code"], r["tool_class"]
    appr = r["requester_context"]["approval_status"]
    out = outside_residency(r["destination"])
    feats = set(r["data_features"])
    if code not in ALLOWED_CODES[label]:
        p.append("code_label_mismatch")
    if cls == "irreversible" and label == ALLOW:
        p.append("irreversible_allowed")
    if appr == "timed_out" and (label, code) != (REFUSE, "DDM-02"):
        p.append("timed_out_not_refused")
    if code == "DDM-02" and appr != "timed_out":
        p.append("ddm02_without_timeout")
    if appr == "timed_out" and cls != "irreversible":
        p.append("timed_out_on_reversible")
    if out and label != REFUSE:
        p.append("outside_residency_not_refused")
    if code == "DDM-05" and not out:
        p.append("ddm05_inside_residency")
    if label == ALLOW and cls == "write" and feats & SENSITIVE:
        p.append("sensitive_write_allowed")
    if "none" in feats and len(feats) > 1:
        p.append("none_with_other_features")
    hard = r.get("hard_case", "none")
    if hard == "over_refusal_trap" and label != ALLOW:
        p.append("over_refusal_trap_not_allowed")
    if hard == "injection" and label == ALLOW:
        p.append("injection_allowed")
    return p
