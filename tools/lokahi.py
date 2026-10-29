# -*- coding: utf-8 -*-
"""LOKAHI v0.1.2 reference core.

Three pieces, in the order a request moves through them:

    manifest  who a node is, and what it permits
    evaluate  the pre-flight decision on one proposed action (15 ordered rules)
    ledger    an append-only, tamper-evident record of that decision

Design constraints, stated because they are the design:

1. Fails closed. A manifest that cannot be read, verified or dated is a denial.
2. Enumerates permission. A feature not listed is not permitted.
3. Counts operators, not agents. A quorum of twelve agents run by one operator
   is one signature.
4. Silence denies. A pending signature that times out is a no.
5. Operators cannot approve or sit on their own requests, regardless of which
   node executes them, and emergency declarants cannot vote on their own scope.
6. Suspension quorum: two or more dissenting operators freeze the action.
7. Rate-capped emergency exceptions: emergency declarations exceeding the
   manifest's safety_critical window cap fail closed.

The signature back end here is HMAC-SHA256, so one machine can play both sides
in the demo with zero external dependencies. The specification requires Ed25519:
a verifier must be able to check a manifest without holding the signer's key.
The interface is isolated in `Signer`, and swapping the back end changes this
class only.
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
    """HMAC-SHA256 stand-in for Ed25519.

    Production note: HMAC requires the verifier to hold the signing key, which
    defeats the purpose across independent nodes. Replace with Ed25519 in M1.
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
    """Accept 2028-03-01T00:00:00Z or an ISO string with offset. Reject junk."""
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

    REQUIRED = ("node", "informational", "forbidden_targets", "jurisdiction",
                "residency", "exceptions", "expires", "steward")

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "informational": sorted(self.informational),
            "forbidden_targets": sorted(self.forbidden_targets),
            "jurisdiction": self.jurisdiction,
            "residency": sorted(self.residency),
            "exceptions": dict(sorted(self.exceptions.items())),
            "expires": self.expires,
            "steward": self.steward,
        }

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

    @property
    def allowed(self) -> bool:
        return self.verdict == COMPLIANT


# --------------------------------------------------------------------------
# evaluation (15 normative rules)
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


def evaluate(manifest: Manifest,
             action: Action,
             consents: Iterable[Consent] = (),
             signer: Signer | None = None,
             now: float | None = None,
             timed_out: bool = False) -> Decision:
    """Decide one proposed action against one node's manifest across 15 ordered rules.

    Order is normative. The cheapest and most certain denials come first so that
    a forged, expired, or out-of-scope manifest can never be rescued by a later rule.
    """
    consents_list = list(consents)

    # Rule 1: signature empty -> forbidden (unsigned_manifest)
    if not manifest.signature:
        return Decision(FORBIDDEN, ["unsigned_manifest"])

    # Rule 2: signature does not verify -> forbidden (unverifiable_manifest)
    if signer is not None and not manifest.verify(signer):
        return Decision(FORBIDDEN, ["unverifiable_manifest"])

    # Rule 3: expires in the past or unreadable -> forbidden (expired_manifest)
    if manifest.expired(now):
        return Decision(FORBIDDEN, ["expired_manifest"])

    # Rule 4: any requested feature in forbidden_targets -> forbidden (forbidden_target:<name>)
    permitted = set(manifest.informational)
    forbidden = set(manifest.forbidden_targets)
    requested = set(action.features)
    if requested & forbidden:
        return Decision(FORBIDDEN, [f"forbidden_target:{t}" for t in sorted(requested & forbidden)])

    # Rule 5: any requested feature absent from informational -> forbidden (feature_out_of_manifest:<name>)
    unknown = requested - permitted
    if unknown:
        return Decision(FORBIDDEN, [f"feature_out_of_manifest:{t}" for t in sorted(unknown)])

    # Rule 6: action jurisdiction differs from manifest jurisdiction -> forbidden (jurisdiction_mismatch)
    if action.jurisdiction != manifest.jurisdiction:
        return Decision(FORBIDDEN, ["jurisdiction_mismatch"])

    # Rule 7: action stores outside residency -> forbidden (residency_violation:<name>)
    outside = set(action.stores_in) - set(manifest.residency)
    if outside:
        return Decision(FORBIDDEN, [f"residency_violation:{x}" for x in sorted(outside)])

    # Rule 8: emergency declaration exceeds manifest rate-cap -> forbidden (exception_rate_exceeded)
    if action.declared_by is not None:
        cap = int(manifest.exceptions.get("safety_critical", 0))
        if action.exceptions_used >= cap:
            return Decision(FORBIDDEN, ["exception_rate_exceeded"])

    # Rule 9: fast path (reversible, not population-affecting, not an emergency override) -> compliant (within_manifest)
    needs_quorum = (not action.reversible) or action.population_affecting or (action.declared_by is not None)
    if not needs_quorum:
        return Decision(COMPLIANT, ["within_manifest"])

    # Rules 10-12: Conflict-of-interest exclusion (checked across all submitted ring participants)
    active_participants = [
        c for c in consents_list
        if isinstance(c.operator, str) and c.operator.strip()
        and isinstance(c.node, str) and c.node.strip()
    ]
    yes_ring = distinct_operators(active_participants, decision_filter="yes")

    # Rule 10: requesting node in ring -> forbidden (requester_in_ring)
    if any(c.node == action.requester for c in active_participants):
        return Decision(FORBIDDEN, ["requester_in_ring"], QUORUM, len(yes_ring))

    # Rule 11: requesting operator in ring -> forbidden (requester_operator_in_ring)
    if action.requester_operator and any(c.operator == action.requester_operator for c in active_participants):
        return Decision(FORBIDDEN, ["requester_operator_in_ring"], QUORUM, len(yes_ring))

    # Rule 12: emergency-declaring party in ring -> forbidden (declaring_party_in_ring)
    if action.declared_by and any(c.operator == action.declared_by for c in active_participants):
        return Decision(FORBIDDEN, ["declaring_party_in_ring"], QUORUM, len(yes_ring))

    # Rule 13: two or more distinct operators vote "no" -> forbidden (suspended_by_ring)
    no_votes = distinct_operators(active_participants, decision_filter="no")
    if len(no_votes) >= SUSPENSION_QUORUM:
        return Decision(FORBIDDEN, ["suspended_by_ring"], QUORUM, 0)

    # Rule 14: timeout with fewer than three distinct operators -> forbidden (non_response)
    if timed_out and len(yes_ring) < QUORUM:
        return Decision(FORBIDDEN, ["non_response"], QUORUM, len(yes_ring))

    # Rule 15: fewer than three distinct operators -> escalate (insufficient_disjoint_consent); else compliant (quorum_met)
    if len(yes_ring) < QUORUM:
        return Decision(ESCALATE, ["insufficient_disjoint_consent"], QUORUM, len(yes_ring))

    return Decision(COMPLIANT, ["quorum_met"], QUORUM, len(yes_ring))


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

    def append(self, decision: Decision, action: Action, manifest: Manifest) -> dict:
        body = {
            "seq": len(self.entries),
            "ts": int(time.time()),
            "node": manifest.node,
            "requester": action.requester,
            "tool": action.tool,
            "verdict": decision.verdict,
            "reasons": decision.reasons,
            "quorum_required": decision.quorum_required,
            "quorum_met": decision.quorum_met,
            "prev": self._head(),
        }
        body["hash"] = hashlib.sha256(canonical(body)).hexdigest()
        self.entries.append(body)
        return body

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
