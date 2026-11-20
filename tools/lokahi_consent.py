# -*- coding: utf-8 -*-
"""Multi-party threshold consent framework for LOKAHI.

Implements disjoint multi-operator consent where an agent or physical actuator
cannot act without cryptographic signatures from multiple independent validators,
and where silence from any required party acts as an automatic veto.

Design:

* **Ed25519 public-key signatures.** Each validator holds an independent Ed25519
  key pair. The gate holds only the validator *public* keys (never private keys),
  so no single party — including the gate operator — can forge approvals.
* **m-of-n threshold.** A per-tool `(m, n)` threshold is declared in the signed
  manifest (default 2-of-3). An action requires `m` distinct, valid validator
  signatures on the exact action digest before it may proceed.
* **Silence is veto (Ahimsa Timeout).** If the consent window closes with fewer
  than `m` signatures, the call is refused with `non_response` — never allowed.
* **Dissent veto.** Two or more distinct validators voting "no" freeze execution
  immediately (`suspended_by_ring`), overriding any number of "yes" votes.
* **Conflict-of-interest.** The agent, its operator, and any emergency
  declarant are excluded from the consent ring. Each person counts once however
  many agents they run.

Standard library only, plus `cryptography` for Ed25519. HMAC-SHA256 (v0.1.4)
remains supported for backward compatibility.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from lokahi import (
    COMPLIANT,
    ESCALATE,
    FORBIDDEN,
    SUSPENSION_QUORUM,
    Action,
    Approval,
    Consent,
    Decision,
    Manifest,
    Signer,
    canonical,
    compute_action_digest,
)


# ---------------------------------------------------------------------------
# Ed25519 signer (public-key signatures)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - cryptography is a soft dependency
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature

    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    Ed25519PrivateKey = None  # type: ignore
    Ed25519PublicKey = None  # type: ignore
    InvalidSignature = Exception  # type: ignore
    _CRYPTOGRAPHY_AVAILABLE = False


class Ed25519Signer:
    """Ed25519 public-key signer/verifier.

    Holds a private key for signing and exposes the public key for verification.
    The verifier never needs the private key, so the gate can verify validator
    signatures without holding any validator's private key.
    """

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key()

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        """Generate a fresh Ed25519 key pair."""
        if not _CRYPTOGRAPHY_AVAILABLE:
            raise ImportError(
                "the 'cryptography' package is required for Ed25519 keys; "
                "install it or use HMAC-SHA256 Signer instead"
            )
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, key_bytes: bytes) -> "Ed25519Signer":
        if not _CRYPTOGRAPHY_AVAILABLE:
            raise ImportError("the 'cryptography' package is required for Ed25519 keys")
        return cls(Ed25519PrivateKey.from_private_bytes(key_bytes))

    @classmethod
    def from_public_bytes(cls, key_bytes: bytes) -> "Ed25519Verifier":
        """Create a verification-only object from public key bytes."""
        if not _CRYPTOGRAPHY_AVAILABLE:
            raise ImportError("the 'cryptography' package is required for Ed25519 keys")
        return Ed25519Verifier(Ed25519PublicKey.from_public_bytes(key_bytes))

    def private_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 private key seed (for key share storage)."""
        return self._private_key.private_bytes_raw()

    def public_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 public key (for distribution to the gate)."""
        return self._public_key.public_bytes_raw()

    def sign(self, payload: bytes) -> str:
        signature = self._private_key.sign(payload)
        return signature.hex()

    def verify(self, payload: bytes, signature: str) -> bool:
        """Verify a signature using this signer's public key."""
        try:
            sig_bytes = bytes.fromhex(signature)
        except ValueError:
            return False
        try:
            self._public_key.verify(sig_bytes, payload)
            return True
        except InvalidSignature:
            return False
        except Exception:
            return False


class Ed25519Verifier:
    """Verification-only Ed25519 key. Holds a public key, never a private key."""

    def __init__(self, public_key: Ed25519PublicKey) -> None:
        self._public_key = public_key

    @classmethod
    def from_public_bytes(cls, key_bytes: bytes) -> "Ed25519Verifier":
        if not _CRYPTOGRAPHY_AVAILABLE:
            raise ImportError("the 'cryptography' package is required for Ed25519 keys")
        return cls(Ed25519PublicKey.from_public_bytes(key_bytes))

    def public_bytes(self) -> bytes:
        return self._public_key.public_bytes_raw()

    def verify(self, payload: bytes, signature: str) -> bool:
        try:
            sig_bytes = bytes.fromhex(signature)
        except ValueError:
            return False
        try:
            self._public_key.verify(sig_bytes, payload)
            return True
        except InvalidSignature:
            return False
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Threshold consent
# ---------------------------------------------------------------------------

@dataclass
class Threshold:
    """m-of-n consent threshold.

    ``m`` distinct validators must sign "yes" within the window; ``n`` is the
    total number of validators. Default 2-of-3.
    """

    m: int = 2
    n: int = 3

    def __post_init__(self) -> None:
        if self.m < 1:
            raise ValueError("threshold m must be >= 1")
        if self.n < self.m:
            raise ValueError("threshold n must be >= m")


@dataclass
class ValidatorKey:
    """One validator's public key and metadata."""

    operator: str
    public_key_hex: str
    verifier: Ed25519Verifier  # verification-only; no private key


@dataclass
class ConsentResult:
    """Outcome of a consent check."""

    consent_met: bool        # m distinct "yes" signatures received
    vetoed: bool             # silence veto or dissent veto triggered
    reason: str              # e.g. "quorum_met", "non_response", "suspended_by_ring"
    yes_operators: list[str] # distinct operators who signed "yes"
    no_operators: list[str]  # distinct operators who signed "no"
    quorum_required: int     # m
    quorum_met: int          # distinct "yes" count
    details: dict = field(default_factory=dict)


class ConsentFramework:
    """Multi-party threshold consent framework.

    Manages validator keys, threshold configuration, and consent checking for
    one manifest. Used by the MCP gate to decide whether an irreversible or
    population-affecting action has enough disjoint consent to proceed.
    """

    def __init__(
        self,
        manifest: Manifest,
        validator_keys: dict[str, Ed25519Verifier],
        threshold: Optional[Threshold] = None,
        suspension_quorum: int = SUSPENSION_QUORUM,
        groups: Optional[dict[str, list[str]]] = None,
        operator_roles: Optional[dict[str, str]] = None,
    ) -> None:
        if not validator_keys:
            raise ValueError("at least one validator key is required")

        self.manifest = manifest
        self.threshold = threshold or Threshold()
        self.operator_roles = dict(operator_roles or {})
        if any(
            not isinstance(identity, str)
            or not identity
            or not isinstance(role, str)
            or role not in {"operator", "auditor", "steward"}
            for identity, role in self.operator_roles.items()
        ):
            raise ValueError(
                "operator_roles must map non-empty identities to operator, auditor, or steward"
            )
        # Dissent threshold. Defaults to the engine's SUSPENSION_QUORUM so the
        # two implementations cannot drift.
        self.suspension_quorum = int(suspension_quorum)
        if self.suspension_quorum < 1:
            raise ValueError("suspension_quorum must be >= 1")
        # Optional named group: when the manifest names one, only its members
        # count toward quorum. This keeps a large validator set from being
        # diluted by people outside the body that owns the decision.
        threshold_cfg = (
            manifest.consent_threshold
            if isinstance(manifest.consent_threshold, dict)
            else {}
        )
        self.group_name = threshold_cfg.get("group")
        self.group_members: Optional[set[str]] = None
        if self.group_name:
            if not isinstance(groups, dict) or self.group_name not in groups:
                raise ValueError(f"manifest names consent group {self.group_name!r} but no membership was supplied")
            members = groups[self.group_name]
            if (
                not isinstance(members, list)
                or not all(isinstance(member, str) and member for member in members)
                or len(members) != len(set(members))
            ):
                raise ValueError(f"consent group {self.group_name!r} must contain unique non-empty operator ids")
            self.group_members = set(members)
            unknown = self.group_members - set(validator_keys)
            if unknown:
                raise ValueError(
                    f"consent group {self.group_name!r} contains operators without validator keys: "
                    f"{sorted(unknown)}"
                )
            if len(self.group_members) != self.threshold.n:
                raise ValueError(
                    f"consent group {self.group_name!r} has {len(self.group_members)} members, "
                    f"but manifest threshold n={self.threshold.n}"
                )
        if len(validator_keys) < self.threshold.n:
            raise ValueError(
                f"need at least {self.threshold.n} validator keys, got {len(validator_keys)}"
            )

        # validator_keys maps operator id -> Ed25519Verifier (public key only)
        self.validators: dict[str, ValidatorKey] = {}
        for operator, verifier in validator_keys.items():
            self.validators[operator] = ValidatorKey(
                operator=operator,
                public_key_hex=verifier.public_bytes().hex(),
                verifier=verifier,
            )

    # ------------------------------------------------------------------
    # Threshold from manifest
    # ------------------------------------------------------------------

    @classmethod
    def from_manifest(
        cls,
        manifest: Manifest,
        validator_keys: dict[str, Ed25519Verifier],
        suspension_quorum: int = SUSPENSION_QUORUM,
        groups: Optional[dict[str, list[str]]] = None,
        threshold: Optional[Threshold] = None,
        operator_roles: Optional[dict[str, str]] = None,
    ) -> "ConsentFramework":
        """Build from a manifest, reading per-tool or global consent threshold."""
        configured_threshold = threshold
        tools = manifest.tools or {}
        # Look for a default threshold in the manifest, or per-tool overrides.
        # Stored under manifest-level "consent_threshold" or per-tool "consent_threshold".
        ct = tools.get("_consent_threshold") if isinstance(tools, dict) else None
        if ct is None:
            ct = manifest.to_dict().get("consent_threshold")
        if ct is not None:
            manifest_threshold = Threshold(m=int(ct.get("m", 2)), n=int(ct.get("n", 3)))
            if configured_threshold is not None and configured_threshold != manifest_threshold:
                raise ValueError(
                    "validator-key threshold does not match the signed manifest threshold"
                )
            configured_threshold = manifest_threshold
        return cls(
            manifest,
            validator_keys,
            threshold=configured_threshold,
            suspension_quorum=suspension_quorum,
            groups=groups,
            operator_roles=operator_roles,
        )

    # ------------------------------------------------------------------
    # Consent checking
    # ------------------------------------------------------------------

    def check_consent(
        self,
        action: Action,
        approvals: list[Approval],
        now: Optional[float] = None,
        timed_out: bool = False,
        requester_operator: Optional[str] = None,
        declaring_party: Optional[str] = None,
        round_nonce: str = "",
    ) -> ConsentResult:
        """Check whether an action has threshold consent.

        Parameters
        ----------
        action:
            The proposed action. Its reversibility and population_affecting
            flags determine whether consent is needed at all.
        approvals:
            Signed Approval records collected out of band.
        now:
            Current time (seconds since epoch). Defaults to time.time().
        timed_out:
            Whether the consent window has closed.
        requester_operator:
            Operator running the agent (excluded from consent ring).
        declaring_party:
            Emergency declarant (excluded from consent ring).
        round_nonce:
            Nonce binding the consent to this exact action round. Must match
            the nonce used when computing the approvals' action_digest.

        Returns
        -------
        ConsentResult
        """
        now = now if now is not None else time.time()
        threshold = self.threshold
        expected_digest = compute_action_digest(self.manifest, action, round_nonce)

        # Filter approvals to valid ones: correct digest, unexpired, registered
        # verifier, signature verifies, nonce not reused.
        valid_yes: dict[str, Approval] = {}  # operator -> approval (first yes wins)
        valid_no: dict[str, Approval] = {}   # operator -> approval (first no wins)
        seen_nonces: set[str] = set()

        for ap in approvals:
            if ap.action_digest != expected_digest:
                continue
            if ap.decision not in ("yes", "no"):
                continue
            if ap.nonce in seen_nonces:
                continue
            if ap.expired(now):
                continue

            if self.group_members is not None and ap.operator not in self.group_members:
                continue  # outside the consent group named by the manifest
            if self.operator_roles.get(ap.operator) in {"operator", "auditor"}:
                continue  # role policy: only stewards (or unassigned legacy keys) may approve
            if self.operator_roles and ap.operator not in self.operator_roles:
                continue  # once role assignments are enabled, unassigned keys cannot count
            verifier = self.validators.get(ap.operator)
            if verifier is None:
                continue  # unregistered operator
            # Conflict-of-interest: operator cannot approve the agent's calls.
            if requester_operator and ap.operator == requester_operator:
                continue
            if declaring_party and ap.operator == declaring_party:
                continue
            # Verify the signature over the canonical approval body.
            if not verifier.verifier.verify(canonical(ap.body()), ap.signature):
                continue

            seen_nonces.add(ap.nonce)
            if ap.decision == "yes":
                if ap.operator not in valid_yes:
                    valid_yes[ap.operator] = ap
            else:
                if ap.operator not in valid_no:
                    valid_no[ap.operator] = ap

        yes_operators = sorted(valid_yes.keys())
        no_operators = sorted(valid_no.keys())
        quorum_met = len(yes_operators)

        # Dissent veto (Rule 13): two or more distinct "no" votes freeze immediately.
        if len(no_operators) >= self.suspension_quorum:
            return ConsentResult(
                consent_met=False,
                vetoed=True,
                reason="suspended_by_ring",
                yes_operators=yes_operators,
                no_operators=no_operators,
                quorum_required=threshold.m,
                quorum_met=quorum_met,
                details={
                    "dissent_count": len(no_operators),
                    "dissent_operators": no_operators,
                },
            )

        # Ahimsa timeout (Rule 14): silence is veto.
        if timed_out and quorum_met < threshold.m:
            return ConsentResult(
                consent_met=False,
                vetoed=True,
                reason="non_response",
                yes_operators=yes_operators,
                no_operators=no_operators,
                quorum_required=threshold.m,
                quorum_met=quorum_met,
                details={
                    "window_closed": True,
                    "missing_signatures": threshold.m - quorum_met,
                },
            )

        # Quorum met.
        if quorum_met >= threshold.m:
            return ConsentResult(
                consent_met=True,
                vetoed=False,
                reason="quorum_met",
                yes_operators=yes_operators,
                no_operators=no_operators,
                quorum_required=threshold.m,
                quorum_met=quorum_met,
            )

        # Not enough yes votes yet, window still open -> escalate (await more consent).
        return ConsentResult(
            consent_met=False,
            vetoed=False,
            reason="insufficient_disjoint_consent",
            yes_operators=yes_operators,
            no_operators=no_operators,
            quorum_required=threshold.m,
            quorum_met=quorum_met,
            details={
                "window_closes_at": None,  # set by caller if known
            },
        )

    # ------------------------------------------------------------------
    # Helper: does this action need consent?
    # ------------------------------------------------------------------

    @staticmethod
    def needs_consent(action: Action) -> bool:
        """True if the action requires multi-party consent (irreversible or
        population-affecting). Reversible, non-population actions are fast-path
        and need no consent."""
        return (not action.reversible) or action.population_affecting
