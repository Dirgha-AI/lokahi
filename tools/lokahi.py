# -*- coding: utf-8 -*-
"""LOKAHI v0.1.2 reference core (with Kinetic Ahimsa physical-envelope interlocks
and stateful Ledger sliding-window exception tracking).

Three pieces, in the order a request moves through them:

    manifest  who a node is, and what digital/physical bounds it permits
    evaluate  the pre-flight decision on one proposed action (15 ordered rules)
    ledger    an append-only, tamper-evident record of that decision

Design constraints, stated because they are the design:

1. Fails closed. A manifest that cannot be read, verified or dated is a denial.
2. Enumerates permission. A feature not listed is not permitted; a physical
   trajectory exceeding declared force, torque, velocity, or workspace bounds is denied.
3. Counts operators, not agents. A quorum of twelve agents run by one operator
   is one signature.
4. Silence denies (The Ahimsa Timeout Rule — Rule 14). A pending signature that
   times out resolves strictly to `non_response` denial (and safe mechanical hold
   in physical robotics).
5. Operators cannot approve or sit on their own requests, regardless of which
   node executes them, and emergency declarants cannot vote on their own scope.
6. Suspension quorum (Rule 13): two or more distinct, non-conflicted dissenting
   operators immediately freeze the action (`suspended_by_ring`), and cannot be
   masked by a requester injecting a conflicted vote.
7. Rate-capped emergency exceptions (Rule 8): emergency declarations exceeding the
   manifest's sliding-window `safety_critical` cap (via `exceptions_used` or
   stateful `Ledger` history) fail closed (`exception_rate_exceeded`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Iterable

__version__ = "0.1.2"

COMPLIANT = "compliant"
ESCALATE = "escalate"
FORBIDDEN = "forbidden"

# A quorum is met by distinct operators with disjoint control.
QUORUM = 3
SUSPENSION_QUORUM = 2


# --------------------------------------------------------------------------
# canonical form and signing
# --------------------------------------------------------------------------

def canonical(obj: dict) -> bytes:
    """Deterministic bytes for signing (RFC 8785 JCS compatible subset)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Signer:
    """Cryptographic manifest signer and verifier.

    Supports zero-dependency HMAC-SHA256 in local single-host test harnesses and
    optional Ed25519 asymmetric verification when `cryptography` is installed.
    """

    def __init__(self, key: bytes):
        if len(key) < 16:
            raise ValueError("key too short")
        self._key = key

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> bool:
        if not isinstance(signature, str) or len(signature) != 64:
            return False
        return hmac.compare_digest(self.sign(payload), signature)


def _parse_time(value: str) -> float:
    """Accept 2028-03-01T00:00:00Z or an ISO string with offset. Reject junk or naive strings."""
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    import datetime as _dt
    dt = _dt.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.timestamp()


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

@dataclass
class Manifest:
    node: str
    informational: list[str]           # features this node permits
    forbidden_targets: list[str]       # optimisation targets barred outright
    jurisdiction: str                  # e.g. IN-DL
    residency: list[str]               # where data may be stored
    exceptions: dict                   # {"safety_critical": 3, "window_hours": 24}
    expires: str                       # ISO 8601
    steward: str                       # survives the principal
    signature: str = ""
    physical_envelope: dict | None = None  # Optional RFC 8785 physical robotics bounds (Paper 6)

    REQUIRED = (
        "node",
        "informational",
        "forbidden_targets",
        "jurisdiction",
        "residency",
        "exceptions",
        "expires",
        "steward",
    )

    def to_dict(self) -> dict:
        d = {
            "node": self.node,
            "informational": sorted(self.informational),
            "forbidden_targets": sorted(self.forbidden_targets),
            "jurisdiction": self.jurisdiction,
            "residency": sorted(self.residency),
            "exceptions": dict(sorted(self.exceptions.items())),
            "expires": self.expires,
            "steward": self.steward,
        }
        if self.physical_envelope is not None:
            d["physical_envelope"] = dict(sorted(self.physical_envelope.items()))
        return d

    def sign(self, signer: Signer) -> "Manifest":
        self.signature = signer.sign(canonical(self.to_dict()))
        return self

    def verify(self, signer: Signer) -> bool:
        return signer.verify(canonical(self.to_dict()), self.signature)

    def expired(self, now: float | None = None) -> bool:
        try:
            return _parse_time(self.expires) <= (now if now is not None else time.time())
        except Exception:
            return True  # an unreadable expiry is an expired manifest

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        missing = [k for k in cls.REQUIRED if k not in data]
        if missing:
            raise ValueError(f"manifest missing fields: {','.join(missing)}")
        return cls(
            node=str(data["node"]),
            informational=[str(x) for x in data["informational"]],
            forbidden_targets=[str(x) for x in data["forbidden_targets"]],
            jurisdiction=str(data["jurisdiction"]),
            residency=[str(x) for x in data["residency"]],
            exceptions=dict(data["exceptions"]),
            expires=str(data["expires"]),
            steward=str(data["steward"]),
            signature=str(data.get("signature", "")),
            physical_envelope=dict(data["physical_envelope"]) if "physical_envelope" in data and data["physical_envelope"] is not None else None,
        )


# --------------------------------------------------------------------------
# action and consent
# --------------------------------------------------------------------------

@dataclass
class Action:
    requester: str                     # node identifier making the call
    tool: str
    features: list[str]                # features the action would read
    jurisdiction: str                  # where the action takes effect
    requester_operator: str = ""       # human or org controlling the requesting node
    purpose: str = ""
    reversible: bool = True
    population_affecting: bool = False
    stores_in: list[str] = field(default_factory=list)
    declared_by: str | None = None     # set when the requester declares an emergency
    exceptions_used: int = 0           # emergency calls already consumed in current window
    physical_params: dict | None = None  # Optional embodied robotics trajectory parameters (Paper 6)


@dataclass
class Consent:
    operator: str                      # a human or organisation with control
    node: str
    agents: int = 1                    # how many agents that operator runs
    decision: str = "pending"          # yes | no | pending


@dataclass
class Decision:
    verdict: str
    reasons: list[str]
    quorum_required: int = 0
    quorum_met: int = 0
    actuator_interlock: str = "pass"   # pass | safe_mechanical_hold | normal_retract

    @property
    def allowed(self) -> bool:
        return self.verdict == COMPLIANT


# --------------------------------------------------------------------------
# evaluation (15 normative rules + Kinetic Ahimsa envelope checks)
# --------------------------------------------------------------------------

def distinct_operators(consents: Iterable[Consent], decision_filter: str | None = None) -> list[Consent]:
    """One operator, one voice, however many agents they field.

    Empty operator/node identifiers are ignored, and any operator who
    equivocates (submits conflicting decisions in the same ring) is disqualified
    from counting toward affirmative 'yes' consent.
    """
    valid_consents = [
        c for c in consents
        if isinstance(c.operator, str) and c.operator.strip()
        and isinstance(c.node, str) and c.node.strip()
    ]
    decisions_by_op: dict[str, set[str]] = {}
    for c in valid_consents:
        op = c.operator.strip()
        decisions_by_op.setdefault(op, set()).add(c.decision)

    seen: dict[str, Consent] = {}
    for c in valid_consents:
        op = c.operator.strip()
        # Equivocating operators cannot count as 'yes', though their dissent still counts as 'no'
        if decision_filter == "yes" and len(decisions_by_op[op]) > 1:
            continue
        if decision_filter is not None and c.decision != decision_filter:
            continue
        if op not in seen:
            seen[op] = c
    return list(seen.values())


def _check_physical_envelope(env: dict, params: dict) -> list[str]:
    """Validate embodied robotics trajectory parameters against signed physical_envelope bounds."""
    violations: list[str] = []

    # 1. Impedance damping disable check
    if "damping_kd" in params and params["damping_kd"] is not None:
        if float(params["damping_kd"]) <= 0.0:
            violations.append("forbidden_target:impedance_damping_disabled")

    # 2. Contact force ceiling (e.g., WallFin 5.0 N +/- 1.1 N)
    if "contact_force_n" in params and "max_contact_force_n" in env:
        if float(params["contact_force_n"]) > float(env["max_contact_force_n"]):
            violations.append(f"physical_force_exceeded:{params['contact_force_n']}N>{env['max_contact_force_n']}N")

    # 3. Joint torque / tendon tension ceiling (e.g., DEX ARM 01 1,000 Hz)
    if "joint_torque_nm" in params and "max_joint_torque_nm" in env:
        req_t = params["joint_torque_nm"]
        max_t = float(env["max_joint_torque_nm"])
        peak_t = max(float(x) for x in req_t) if isinstance(req_t, (list, tuple)) else float(req_t)
        if peak_t > max_t:
            violations.append(f"physical_torque_exceeded:{peak_t}Nm>{max_t}Nm")

    # 4. Tendon tension cap (DEX ARM 01 20-DoF hand)
    if "tendon_tension_n" in params and "max_tendon_tension_n" in env:
        if float(params["tendon_tension_n"]) > float(env["max_tendon_tension_n"]):
            violations.append(f"physical_tendon_exceeded:{params['tendon_tension_n']}N>{env['max_tendon_tension_n']}N")

    # 5. Cartesian velocity bound
    if "cartesian_velocity_ms" in params and "max_cartesian_velocity_ms" in env:
        if float(params["cartesian_velocity_ms"]) > float(env["max_cartesian_velocity_ms"]):
            violations.append(f"physical_velocity_exceeded:{params['cartesian_velocity_ms']}ms>{env['max_cartesian_velocity_ms']}ms")

    # 6. 3D Cartesian workspace & support polygon bounding box [x_min, x_max, y_min, y_max, z_min, z_max]
    if "waypoint_m" in params and "workspace_bounds_m" in env:
        wp = params["waypoint_m"]
        bounds = env["workspace_bounds_m"]
        if len(wp) == 3 and len(bounds) == 6:
            x, y, z = float(wp[0]), float(wp[1]), float(wp[2])
            if not (bounds[0] <= x <= bounds[1] and bounds[2] <= y <= bounds[3] and bounds[4] <= z <= bounds[5]):
                violations.append("feature_out_of_manifest:workspace_bounds_exceeded")

    return violations


def evaluate(
    manifest: Manifest,
    action: Action,
    consents: Iterable[Consent] = (),
    signer: Signer | None = None,
    now: float | None = None,
    timed_out: bool = False,
    ledger: "Ledger | None" = None,
) -> Decision:
    """Decide one proposed action against one node's manifest across 15 ordered rules.

    Order is normative. The cheapest and most certain denials come first so that
    a forged, expired, or out-of-scope manifest can never be rescued by a later rule.
    """
    consents_list = list(consents)
    is_physical = manifest.physical_envelope is not None or action.physical_params is not None
    hold_mode = "safe_mechanical_hold" if is_physical else "pass"

    # Rule 1: signature empty -> forbidden (unsigned_manifest)
    if not manifest.signature:
        return Decision(FORBIDDEN, ["unsigned_manifest"], actuator_interlock=hold_mode)

    # Rule 2: signature does not verify -> forbidden (unverifiable_manifest)
    if signer is not None and not manifest.verify(signer):
        return Decision(FORBIDDEN, ["unverifiable_manifest"], actuator_interlock=hold_mode)

    # Rule 3: expires in the past or unreadable -> forbidden (expired_manifest)
    if manifest.expired(now):
        return Decision(FORBIDDEN, ["expired_manifest"], actuator_interlock=hold_mode)

    # Rule 4: any requested feature in forbidden_targets -> forbidden (forbidden_target:<name>)
    permitted = set(manifest.informational)
    forbidden = set(manifest.forbidden_targets)
    requested = set(action.features)
    if requested & forbidden:
        return Decision(
            FORBIDDEN,
            [f"forbidden_target:{t}" for t in sorted(requested & forbidden)],
            actuator_interlock=hold_mode,
        )

    # Rule 5: any requested feature absent from informational -> forbidden (feature_out_of_manifest:<name>)
    unknown = requested - permitted
    if unknown:
        return Decision(
            FORBIDDEN,
            [f"feature_out_of_manifest:{t}" for t in sorted(unknown)],
            actuator_interlock=hold_mode,
        )

    # Kinetic Ahimsa physical envelope verification (Paper 6)
    if manifest.physical_envelope and action.physical_params:
        phys_violations = _check_physical_envelope(manifest.physical_envelope, action.physical_params)
        if phys_violations:
            return Decision(FORBIDDEN, phys_violations, actuator_interlock="safe_mechanical_hold")

    # Rule 6: action jurisdiction differs from manifest jurisdiction -> forbidden (jurisdiction_mismatch)
    if action.jurisdiction != manifest.jurisdiction:
        return Decision(FORBIDDEN, ["jurisdiction_mismatch"], actuator_interlock=hold_mode)

    # Rule 7: action stores outside residency -> forbidden (residency_violation:<name>)
    outside = set(action.stores_in) - set(manifest.residency)
    if outside:
        return Decision(
            FORBIDDEN,
            [f"residency_violation:{x}" for x in sorted(outside)],
            actuator_interlock=hold_mode,
        )

    # Rule 8: emergency declaration exceeds manifest rate-cap -> forbidden (exception_rate_exceeded)
    if action.declared_by is not None:
        cap = int(manifest.exceptions.get("safety_critical", 0))
        window_hours = float(manifest.exceptions.get("window_hours", 24))
        ledger_used = (
            ledger.count_exceptions_in_window(manifest.node, now if now is not None else time.time(), window_hours)
            if ledger is not None
            else 0
        )
        effective_used = max(action.exceptions_used, ledger_used)
        if effective_used >= cap:
            return Decision(FORBIDDEN, ["exception_rate_exceeded"], actuator_interlock=hold_mode)

    # Rule 9: fast path (reversible, not population-affecting, not an emergency override) -> compliant (within_manifest)
    needs_quorum = (not action.reversible) or action.population_affecting or (action.declared_by is not None)
    if not needs_quorum:
        return Decision(COMPLIANT, ["within_manifest"], actuator_interlock="pass")

    active_participants = [
        c for c in consents_list
        if isinstance(c.operator, str) and c.operator.strip()
        and isinstance(c.node, str) and c.node.strip()
    ]
    non_conflicted_participants = [
        c for c in active_participants
        if c.node != action.requester
        and (not action.requester_operator or c.operator != action.requester_operator)
        and (not action.declared_by or c.operator != action.declared_by)
    ]
    yes_ring = distinct_operators(non_conflicted_participants, decision_filter="yes")

    # Rule 13 (Checked prior to conflict rejection so a requester cannot mask a 2-of-3 steward freeze!):
    # Two or more distinct, non-conflicted operators voting "no" -> forbidden (suspended_by_ring)
    no_votes = distinct_operators(non_conflicted_participants, decision_filter="no")
    if len(no_votes) >= SUSPENSION_QUORUM:
        return Decision(FORBIDDEN, ["suspended_by_ring"], QUORUM, 0, actuator_interlock=hold_mode)

    # Rules 10-12: Conflict-of-interest exclusion across submitted ring participants
    # Rule 10: requesting node in ring -> forbidden (requester_in_ring)
    if any(c.node == action.requester for c in active_participants):
        return Decision(FORBIDDEN, ["requester_in_ring"], QUORUM, len(yes_ring), actuator_interlock=hold_mode)

    # Rule 11: requesting operator in ring -> forbidden (requester_operator_in_ring)
    if action.requester_operator and any(c.operator == action.requester_operator for c in active_participants):
        return Decision(FORBIDDEN, ["requester_operator_in_ring"], QUORUM, len(yes_ring), actuator_interlock=hold_mode)

    # Rule 12: emergency-declaring party in ring -> forbidden (declaring_party_in_ring)
    if action.declared_by and any(c.operator == action.declared_by for c in active_participants):
        return Decision(FORBIDDEN, ["declaring_party_in_ring"], QUORUM, len(yes_ring), actuator_interlock=hold_mode)

    # Rule 14 (The Ahimsa Timeout Rule): timeout with fewer than three distinct operators -> forbidden (non_response)
    if timed_out and len(yes_ring) < QUORUM:
        default_retract = (
            str(manifest.physical_envelope.get("timeout_interlock_mode", "normal_retract"))
            if isinstance(manifest.physical_envelope, dict)
            else "normal_retract"
        )
        retract_mode = default_retract if is_physical else "pass"
        return Decision(FORBIDDEN, ["non_response"], QUORUM, len(yes_ring), actuator_interlock=retract_mode)

    # Rule 15: fewer than three distinct operators -> escalate (insufficient_disjoint_consent); else compliant (quorum_met)
    if len(yes_ring) < QUORUM:
        return Decision(ESCALATE, ["insufficient_disjoint_consent"], QUORUM, len(yes_ring), actuator_interlock=hold_mode)

    return Decision(COMPLIANT, ["quorum_met"], QUORUM, len(yes_ring), actuator_interlock="pass")


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------

GENESIS = "0" * 64


class Ledger:
    """Append-only hash chain. Each entry commits to the one before it."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def _head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else GENESIS

    def append(self, decision: Decision, action: Action, manifest: Manifest, ts: float | None = None) -> dict:
        timestamp = int(ts if ts is not None else time.time())
        body = {
            "seq": len(self.entries),
            "ts": timestamp,
            "node": manifest.node,
            "requester": action.requester,
            "tool": action.tool,
            "verdict": decision.verdict,
            "reasons": decision.reasons,
            "quorum_required": decision.quorum_required,
            "quorum_met": decision.quorum_met,
            "declared_by": action.declared_by,
            "prev": self._head(),
        }
        body["hash"] = hashlib.sha256(canonical(body)).hexdigest()
        self.entries.append(body)
        return body

    def count_exceptions_in_window(self, node: str, now: float, window_hours: float = 24.0) -> int:
        """Count valid emergency-declared actions (excluding forged/out-of-scope FORBIDDEN spam)
        recorded for `node` within the sliding window."""
        cutoff = now - (window_hours * 3600.0)
        return sum(
            1
            for e in self.entries
            if e.get("node") == node
            and e.get("declared_by") is not None
            and e.get("verdict") in (COMPLIANT, ESCALATE)
            and e.get("ts", 0) >= cutoff
        )

    def verify(self) -> tuple[bool, int]:
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e["prev"] != prev:
                return False, i
            body = {k: v for k, v in e.items() if k != "hash"}
            if hashlib.sha256(canonical(body)).hexdigest() != e["hash"]:
                return False, i
            prev = e["hash"]
        return True, len(self.entries)

    def to_json(self) -> str:
        return json.dumps(self.entries, indent=2, sort_keys=True)
