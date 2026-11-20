# -*- coding: utf-8 -*-
"""LOKAHI v1.0.0 — MCP JSON-RPC 2.0 cascading enforcement gateway.

Evaluates each `tools/call` request against a signed LOKAHI manifest before it
reaches a tool server, and appends every decision to a hash-chained ledger.

Security model (v1.0.0):
  * The agent's request is data, never control. A request carrying LOKAHI
    control metadata (`_lokahi`: consents, timeouts, reversibility,
    jurisdiction, storage) is refused instead of trusted.
  * Facts come from the signed manifest's per-tool policy: which arguments a
    tool may take and which data feature each reveals, whether the tool is
    irreversible, and where it stores data. Unknown tools and arguments are
    denied.
  * Approvals arrive out of band as signed records bound to the exact call
    (tool name plus argument values). The gateway's clock decides when an
    approval window closes; an unanswered request is refused (Rule 14).
  * A manifest verification key is mandatory. Without one the CLI refuses to
    start and the library class refuses to construct.

Not yet: this is a decision filter, not a forwarding proxy. It does not launch
or relay to the tool server and does not inspect tool results.

Experimental (off by default): `--decisions experimental` (or
`experimental_decisions=True` in Python) adds a small learned model after
LOKAHI's own decision. It only reviews calls LOKAHI allowed and can only make
them stricter. See tools/decisions/ and docs/decisions/CHARTER.md.

Usage:
    python tools/mcp_stdio_proxy.py --self-test
    python tools/mcp_stdio_proxy.py --manifest manifest.json --key-file manifest.key \
        --approvers approvers.json --approvals approvals.jsonl --ledger .lokahi-ledger.jsonl \
        [--decisions experimental]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lokahi import (  # noqa: E402
    COMPLIANT,
    ESCALATE,
    FORBIDDEN,
    Action,
    Approval,
    Consent,
    Decision,
    Ledger,
    Manifest,
    Signer,
    accept_approvals,
    canonical,
    compute_action_digest,
    evaluate,
    load_key,
    manifest_status,
    validate_tool_policy,
)

# Engine verdicts that arise purely from its fixed three-vote quorum. When a
# manifest declares its own threshold, the consent framework owns the quorum
# count and these verdicts defer to it. Every other engine refusal is absolute.
_CONSENT_ONLY_REASONS = frozenset({"insufficient_disjoint_consent", "non_response"})


def merge_threshold_consent(
    decision: Decision, consent_result: Any, hold_mode: str = "pass"
) -> Decision:
    """Merge the normative engine's decision with the threshold-consent result.

    Invariants, in order:

    1. An engine refusal for any reason other than its own quorum count is
       absolute. Consent can never relax a manifest, feature, jurisdiction,
       residency, exception-cap, physical-envelope or conflict-of-interest
       refusal.
    2. Engine verdicts caused only by its fixed three-vote quorum defer to the
       manifest's threshold, because the framework owns the quorum count.
    3. A framework veto freezes anything the engine allowed or escalated.
    4. A framework escalation is stricter than engine compliance.

    `consent_result` may be None, in which case the engine decision is returned
    unchanged.
    """
    if consent_result is None:
        return decision

    if consent_result.vetoed:
        framework_verdict, framework_reasons = FORBIDDEN, [consent_result.reason]
    elif consent_result.consent_met:
        framework_verdict, framework_reasons = COMPLIANT, ["quorum_met"]
    else:
        framework_verdict, framework_reasons = ESCALATE, ["insufficient_disjoint_consent"]

    def as_framework(verdict: str, reasons: list[str]) -> Decision:
        return Decision(
            verdict,
            reasons,
            consent_result.quorum_required,
            consent_result.quorum_met,
            actuator_interlock="pass" if verdict == COMPLIANT else hold_mode,
        )

    engine_is_consent_only = set(decision.reasons) <= _CONSENT_ONLY_REASONS

    # 1. Safety refusals are absolute.
    if decision.verdict == FORBIDDEN and not engine_is_consent_only:
        return decision

    # 2. Consent-only engine verdicts defer to the manifest threshold.
    if engine_is_consent_only:
        return as_framework(framework_verdict, framework_reasons)

    # 3. A framework veto freezes an engine allow/escalation.
    if framework_verdict == FORBIDDEN:
        return as_framework(FORBIDDEN, framework_reasons)

    # 4. A framework escalation is stricter than engine compliance.
    if framework_verdict == ESCALATE and decision.verdict == COMPLIANT:
        return as_framework(ESCALATE, framework_reasons)

    # 5. The engine's verdict stands; report the manifest's quorum numbers.
    if decision.verdict == COMPLIANT:
        return Decision(
            COMPLIANT,
            decision.reasons,
            consent_result.quorum_required,
            consent_result.quorum_met,
            actuator_interlock="pass",
        )
    return decision

# Layer 1: WASM deterministic pre-flight bridge.
try:  # noqa: E402
    from wasm_bridge import PreflightVerdict, WasmtimePreflight, WasmtimeUnavailable  # noqa: E402

    _WASM_BRIDGE_AVAILABLE = True
except ImportError:  # pragma: no cover
    WasmtimePreflight = None  # type: ignore
    PreflightVerdict = None  # type: ignore
    WasmtimeUnavailable = ImportError  # type: ignore
    _WASM_BRIDGE_AVAILABLE = False

# Layer 2: SLM semantic judge (imported lazily to avoid importing the decisions
# tier at module import time — see test_importing_the_gate_does_not_import_the_tier).
_SLM_JUDGE_AVAILABLE = None  # type: ignore[assignment]


def _import_slm_judge():
    """Import SLMJudge lazily, returning (SLMJudge, SLMJudgeUnavailable) or (None, None)."""
    global _SLM_JUDGE_AVAILABLE
    if _SLM_JUDGE_AVAILABLE is not None:
        return _SLM_JUDGE_AVAILABLE
    try:  # noqa: E402
        from decisions.slm_judge import SLMJudge, SLMUnavailable as SLMJudgeUnavailable

        _SLM_JUDGE_AVAILABLE = (SLMJudge, SLMJudgeUnavailable, True)
    except ImportError:  # pragma: no cover
        _SLM_JUDGE_AVAILABLE = (None, ImportError, False)
    return _SLM_JUDGE_AVAILABLE

# Layer 3: Multi-party threshold consent (imported lazily).
_CONSENT_FRAMEWORK_AVAILABLE = None  # type: ignore[assignment]


def _import_consent_framework():
    """Import ConsentFramework lazily."""
    global _CONSENT_FRAMEWORK_AVAILABLE
    if _CONSENT_FRAMEWORK_AVAILABLE is not None:
        return _CONSENT_FRAMEWORK_AVAILABLE
    try:  # noqa: E402
        from lokahi_consent import (
            ConsentFramework,
            Ed25519Signer,
            Ed25519Verifier,
            Threshold,
        )

        _CONSENT_FRAMEWORK_AVAILABLE = (
            ConsentFramework,
            Ed25519Verifier,
            Ed25519Signer,
            Threshold,
            True,
        )
    except ImportError:  # pragma: no cover
        _CONSENT_FRAMEWORK_AVAILABLE = (None, None, None, None, False)
    return _CONSENT_FRAMEWORK_AVAILABLE

# Layer 4: Fail-secure state & WebXR teleoperation (imported lazily).
_FAILSECURE_AVAILABLE = None  # type: ignore[assignment]


def _import_failsecure():
    """Import FailSecureManager and WebXRTeleopBroker lazily."""
    global _FAILSECURE_AVAILABLE
    if _FAILSECURE_AVAILABLE is not None:
        return _FAILSECURE_AVAILABLE
    try:  # noqa: E402
        from lokahi_failsecure import FailSecureManager  # noqa: E402
        from lokahi_webxr import WebXRTeleopBroker  # noqa: E402

        _FAILSECURE_AVAILABLE = (FailSecureManager, WebXRTeleopBroker, True)
    except ImportError:  # pragma: no cover
        _FAILSECURE_AVAILABLE = (None, None, False)
    return _FAILSECURE_AVAILABLE


# Per-consumer tool ACL, imported lazily.
_ACL_AVAILABLE = None  # type: ignore[assignment]


def _import_acl():
    global _ACL_AVAILABLE
    if _ACL_AVAILABLE is not None:
        return _ACL_AVAILABLE
    try:  # noqa: E402
        from lokahi_acl import ToolACL  # noqa: E402

        _ACL_AVAILABLE = (ToolACL, True)
    except ImportError:  # pragma: no cover
        _ACL_AVAILABLE = (None, False)
    return _ACL_AVAILABLE


# Tool pinning (rug-pull defence), imported lazily.
_TOOLPIN_AVAILABLE = None  # type: ignore[assignment]


def _import_toolpin():
    """Import the tool-pinning helpers lazily."""
    global _TOOLPIN_AVAILABLE
    if _TOOLPIN_AVAILABLE is not None:
        return _TOOLPIN_AVAILABLE
    try:  # noqa: E402
        from lokahi_toolpin import check_manifest, verify_pin_set  # noqa: E402

        _TOOLPIN_AVAILABLE = (check_manifest, verify_pin_set, True)
    except ImportError:  # pragma: no cover
        _TOOLPIN_AVAILABLE = (None, None, False)
    return _TOOLPIN_AVAILABLE

# JSON-RPC 2.0 LOKAHI error codes
JSONRPC_ERR_FORBIDDEN = -32001
JSONRPC_ERR_ESCALATE = -32002
JSONRPC_ERR_PARSE = -32700

DEFAULT_APPROVAL_WINDOW_SECONDS = 900
CONTROL_METADATA_KEY = "_lokahi"

# One lock per process for ledger file appends. Cross-process safety is the
# deployment's responsibility: run a single gateway writer per ledger file
# (documented in docs/ARCHITECTURE.md).
_LEDGER_FILE_LOCK = threading.Lock()
# Set LOKAHI_LEDGER_FSYNC=0 to skip fsync on very high-volume deployments.
_LEDGER_FSYNC = os.environ.get("LOKAHI_LEDGER_FSYNC", "1") != "0"


def append_ledger_line(path: str, entry: dict) -> None:
    """Append one ledger entry as a JSONL line, flush it, and fsync by default."""
    line = json.dumps(entry, sort_keys=True) + "\n"
    with _LEDGER_FILE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            if _LEDGER_FSYNC:
                os.fsync(f.fileno())

PHYSICAL_PARAM_KEYS = {
    "contact_force_n",
    "damping_kd",
    "joint_torque_nm",
    "tendon_tension_n",
    "cartesian_velocity_ms",
    "max_decel_ms2",
    "waypoint_m",
}


class GatewayConfigError(ValueError):
    """The gateway was configured in a way that cannot be made safe."""


class ApprovalStore:
    """Signed approvals, read from a JSONL file the agent cannot write to.

    Approvals can also be added in memory (tests, embedding applications).
    Malformed lines are skipped; they never count as approval.
    """

    def __init__(self, path: str | None = None, approvals: list[Approval] | None = None) -> None:
        self.path = path
        self._memory: list[Approval] = list(approvals or [])

    def add(self, approval: Approval) -> None:
        self._memory.append(approval)

    def load(self) -> list[Approval]:
        items = list(self._memory)
        if self.path and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(Approval.from_dict(json.loads(line)))
                    except (ValueError, TypeError):
                        continue
        return items


def call_nonce(tool_name: str, arguments: dict) -> str:
    """Bind approvals to the exact call: the tool name plus its argument values."""
    return "args:" + hashlib.sha256(canonical({"tool": tool_name, "arguments": arguments})).hexdigest()


DECISIONS_MODES = ("off", "experimental")


def _make_decision_tier(option: Any, tier_model: Any = None) -> Any:
    """Turn `experimental_decisions=` into a tier, or None (the default: off).

    Accepted: None, False or "off" (off); True or "experimental" (bundled JSON
    model or SLM judge if provided); or an object with a `review()` method,
    such as `decisions.DecisionTier(model=...)`.
    """
    if option is None or option is False or option == "off":
        return None
    if option is True or option == "experimental":
        from decisions import DecisionTier  # imported only when the tier is on

        return DecisionTier(model=tier_model)
    if callable(getattr(option, "review", None)):
        return option
    raise GatewayConfigError(f"experimental_decisions must be one of {DECISIONS_MODES}, True/False, or a tier object")


class LokahiMCPGateway:
    """Cascading MCP JSON-RPC 2.0 enforcement gateway (v1.0.0).

    Layers, in order: fail-secure gate -> WASM pre-flight -> manifest and 15
    rules -> threshold consent merge -> optional decisions tier.
    """

    def __init__(
        self,
        manifest: Manifest,
        signer: Signer | None,
        approvers: dict[str, Signer] | None = None,
        approval_store: ApprovalStore | None = None,
        default_requester: str = "agent:mcp-client",
        default_operator: str = "org:local-operator",
        ledger_path: str | None = None,
        approval_window_seconds: int | None = None,
        experimental_decisions: Any = None,
        preflight: Optional[WasmtimePreflight] = None,
        require_preflight: bool = False,
        slm_judge: Optional[SLMJudge] = None,
        consent_framework: Any = None,
        failsecure_endpoint: Optional[str] = None,
        tool_pins: Optional[dict] = None,
        require_pins: bool = False,
        tool_acl: Any = None,
    ) -> None:
        if signer is None:
            raise GatewayConfigError("a manifest verification key is required; refusing to start without one")
        # Tool pinning: refuse calls to tools whose policy changed after review.
        _check_manifest, _verify_pins, _toolpin_available = _import_toolpin()
        self.tool_pins = tool_pins
        self._pin_events: list = []
        if tool_pins is not None:
            if not _toolpin_available:
                raise GatewayConfigError(
                    "tool pins were provided but lokahi_toolpin could not be imported"
                )
            try:
                self._pin_events = _check_manifest(tool_pins, manifest)
            except Exception as exc:  # noqa: BLE001 - an unreadable pin set must not open the gate
                self._pin_events = [type("_E", (), {"tool": "<pinset>", "kind": "unreadable", "detail": str(exc)})()]
        elif require_pins:
            raise GatewayConfigError("require_pins=True but no tool pin set was provided")
        self.tool_acl = tool_acl
        # Layer 1: WASM deterministic pre-flight.
        if preflight is not None:
            self.preflight = preflight
        elif require_preflight and not _WASM_BRIDGE_AVAILABLE:
            raise GatewayConfigError(
                "require_preflight=True but WasmtimePreflight could not be imported; refusing to start"
            )
        else:
            self.preflight = None
        self.require_preflight = require_preflight
        # Layer 2: SLM semantic judge.
        _SLMJudge, _SLMJudgeUnavailable, _slm_available = _import_slm_judge()
        if slm_judge is not None:
            self.slm_judge = slm_judge
        elif _slm_available:
            self.slm_judge = None
        else:
            self.slm_judge = None
        # Layer 3: Multi-party threshold consent.
        _ConsentFramework, _Ed25519Verifier, _Ed25519Signer, _Threshold, _cf_available = _import_consent_framework()
        if consent_framework is not None:
            self.consent_framework = consent_framework
        elif _cf_available:
            self.consent_framework = None  # can be set later via set_consent_framework()
        else:
            self.consent_framework = None
        self._cf_available = _cf_available
        # Experimental learned decisions tier. Off unless the caller asks for it.
        tier_model = slm_judge if slm_judge is not None else None
        self.decision_tier = _make_decision_tier(experimental_decisions, tier_model=tier_model)
        # Ledger is created here so fail-secure + WebXR can share it.
        self.ledger = Ledger()
        self.ledger_path = ledger_path
        # Layer 4: Fail-secure state & WebXR teleoperation (share the ledger).
        _FailSecureManager, _WebXRTeleopBroker, _fs_available = _import_failsecure()
        if _fs_available:
            self.failsecure = _FailSecureManager(self.ledger, manifest.node)
            if failsecure_endpoint:
                self.webxr = _WebXRTeleopBroker(
                    endpoint=failsecure_endpoint,
                    ledger=self.ledger,
                    manifest_node=manifest.node,
                )
            else:
                self.webxr = None
        else:
            self.failsecure = None
            self.webxr = None
        self._fs_available = _fs_available
        self.manifest = manifest
        self.signer = signer
        self.approvers = dict(approvers or {})
        self.approval_store = approval_store if approval_store is not None else ApprovalStore()
        self.default_requester = default_requester
        self.default_operator = default_operator
        window = approval_window_seconds if approval_window_seconds is not None else manifest.approval_window_seconds
        self.approval_window_seconds = int(window) if window is not None else DEFAULT_APPROVAL_WINDOW_SECONDS
        self.pending: dict[str, float] = {}  # action digest -> when its approval window opened
        self._hold = "safe_mechanical_hold" if manifest.physical_envelope is not None else "pass"
        self._policy_error: str | None = None
        if manifest.tools is None:
            self._policy_error = "no_tool_policy"
        else:
            try:
                validate_tool_policy(manifest.tools)
            except ValueError:
                self._policy_error = "invalid_tool_policy"

    # ------------------------------------------------------------------
    def _record(
        self,
        rpc_msg: dict[str, Any],
        decision: Decision,
        action: Action,
        now: float,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        msg_id = rpc_msg.get("id") if isinstance(rpc_msg, dict) else None
        evidence_extra = {}
        if extra and isinstance(extra.get("action_digest"), str):
            evidence_extra["action_digest"] = extra["action_digest"]
        entry = self.ledger.append(decision, action, self.manifest, ts=now, extra=evidence_extra)
        if self.ledger_path:
            append_ledger_line(self.ledger_path, entry)
        data: dict[str, Any] = {
            "verdict": decision.verdict,
            "reasons": decision.reasons,
            "quorum_required": decision.quorum_required,
            "quorum_met": decision.quorum_met,
            "actuator_interlock": decision.actuator_interlock,
            "ledger_seq": entry["seq"],
            "ledger_hash": entry["hash"],
        }
        if extra:
            data.update(extra)
        if decision.verdict == COMPLIANT:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "allowed": True,
                "lokahi_receipt": data,
                "forwarded_request": rpc_msg,
            }
        err_code = JSONRPC_ERR_FORBIDDEN if decision.verdict == FORBIDDEN else JSONRPC_ERR_ESCALATE
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "allowed": False,
            "error": {
                "code": err_code,
                "message": f"LOKAHI_{decision.verdict.upper()}: {','.join(decision.reasons)}",
                "data": data,
            },
        }

    def _deny(self, rpc_msg: dict[str, Any], action: Action, now: float, reasons: list[str]) -> dict[str, Any]:
        return self._record(rpc_msg, Decision(FORBIDDEN, reasons, actuator_interlock=self._hold), action, now)

    # ------------------------------------------------------------------
    def intercept(
        self,
        rpc_msg: dict[str, Any],
        now: float | None = None,
        consumer: str | None = None,
    ) -> dict[str, Any]:
        """Decide one JSON-RPC 2.0 request. Non-`tools/call` methods pass through.

        All layer logic is wrapped in a fail-secure exception boundary: any
        exception from WASM pre-flight, SLM judge, threshold consent, or the
        15-rule engine routes to secure state and may trigger WebXR
        teleoperation for human manual oversight.
        """
        method = rpc_msg.get("method", "") if isinstance(rpc_msg, dict) else ""
        if method != "tools/call":
            return {"allowed": True, "passthrough": True, "request": rpc_msg}

        now = now if now is not None else time.time()

        try:
            return self._intercept_inner(rpc_msg, now, consumer)
        except Exception as exc:  # noqa: BLE001 - fail closed on any cascade exception
            if self.failsecure is not None:
                decision = self.failsecure.handle_exception(exc, "cascade")
                # Trigger WebXR teleoperation for human manual oversight.
                if self.webxr is not None and not self.webxr.session:
                    try:
                        self.webxr.initiate_session(
                            operator=self.default_operator, now=now
                        )
                    except Exception:  # noqa: BLE001 - teleop trigger must not mask the original error
                        pass
                return self._record(rpc_msg, decision, Action(
                    requester=self.default_requester,
                    tool="unknown",
                    features=[],
                    jurisdiction=self.manifest.jurisdiction,
                    requester_operator=self.default_operator,
                    purpose="failsecure_transition",
                ), now)
            # No fail-secure manager: re-raise (caller handles).
            raise

    # ------------------------------------------------------------------
    def _intercept_inner(
        self, rpc_msg: dict[str, Any], now: float, consumer: str | None = None
    ) -> dict[str, Any]:
        """Inner intercept logic: all cascade layers. May raise on failure."""
        params = rpc_msg.get("params")
        params = params if isinstance(params, dict) else {}
        raw_name = params.get("name")
        tool_name = raw_name if isinstance(raw_name, str) and raw_name else "unknown_tool"
        arguments = params.get("arguments", {})
        requester = f"consumer:{consumer}" if consumer else self.default_requester
        base = Action(
            requester=requester,
            tool=tool_name,
            features=[],
            jurisdiction=self.manifest.jurisdiction,
            requester_operator=self.default_operator,
            purpose=f"mcp:{tool_name}",
        )

        # ------------------------------------------------------------------
        # Fail-secure gate: once privileges are revoked nothing may proceed
        # until a human re-arms. This check comes before every other layer so
        # a locked gateway cannot be used to probe or execute.
        # ------------------------------------------------------------------
        if self.failsecure is not None and not self.failsecure.has_privilege():
            return self._record(
                rpc_msg,
                Decision(
                    FORBIDDEN,
                    [f"secure_state_active:{self.failsecure.state.reason or 'locked'}"],
                    actuator_interlock="safe_mechanical_hold",
                ),
                base,
                now,
            )

        # ------------------------------------------------------------------
        # Per-consumer tool ACL: the manifest says what a tool is, the ACL says
        # who may reach it. Both must permit the call.
        # ------------------------------------------------------------------
        if self.tool_acl is not None:
            if not self.tool_acl.allows(consumer or self.default_requester, tool_name):
                return self._deny(
                    rpc_msg, base, now,
                    [f"tool_acl_denied:{consumer or self.default_requester}:{tool_name}"],
                )

        # ------------------------------------------------------------------
        # Tool pinning: a tool added to or changed in the manifest after the
        # catalogue was reviewed is refused; unknown tools are refused when a
        # pin set is in force.
        # ------------------------------------------------------------------
        if self.tool_pins is not None:
            if self._pin_events:
                return self._record(
                    rpc_msg,
                    Decision(
                        FORBIDDEN,
                        [f"tool_policy_drift:{e.tool}" for e in self._pin_events],
                        actuator_interlock="safe_mechanical_hold",
                    ),
                    base,
                    now,
                )
            if tool_name not in self.tool_pins.get("tools", {}):
                return self._deny(rpc_msg, base, now, [f"tool_not_pinned:{tool_name}"])

        # Malformed payloads are refused before any layer sees them, so Layer 1
        # and the final decision always evaluate the same bytes.
        if not isinstance(arguments, dict):
            return self._deny(rpc_msg, base, now, ["malformed_arguments"])

        # ------------------------------------------------------------------
        # Layer 1: WASM deterministic pre-flight (sub-millisecond budget).
        # Runs before any manifest or rule evaluation. Blocks obvious threats
        # (regex blocklist, schema violations, rate limits, arniko findings)
        # without touching the SLM or the 15-rule engine.
        # ------------------------------------------------------------------
        if self.preflight is not None:
            # Manifest.to_dict() excludes the signature field (by design: the
            # signature is computed over content without itself). Include it
            # explicitly for the WASM manifest integrity check.
            manifest_dict = self.manifest.to_dict()
            if self.manifest.signature:
                manifest_dict["signature"] = self.manifest.signature
            layer1_input = {
                "tool": tool_name,
                "arguments": arguments,
                "agent": self.default_requester,
                "arniko_findings": None,
            }
            layer1_verdict = self.preflight.check(layer1_input, manifest_dict)
            if layer1_verdict.refused:
                return self._record(
                    rpc_msg,
                    Decision(
                        FORBIDDEN,
                        ["layer1_preflight:" + r for r in layer1_verdict.reasons],
                        actuator_interlock=self._hold,
                    ),
                    base,
                    now,
                    extra={"layer1_budget_us": layer1_verdict.budget_us},
                )
        elif self.require_preflight:
            return self._record(
                rpc_msg,
                Decision(
                    FORBIDDEN,
                    ["layer1_preflight_unavailable"],
                    actuator_interlock=self._hold,
                ),
                base,
                now,
            )

        # Rules 1-3 first: nothing can rescue a bad manifest.
        status = manifest_status(self.manifest, self.signer, now, self._hold)
        if status is not None:
            return self._record(rpc_msg, status, base, now)

        # The agent may not send its own approvals, timeouts or facts.
        if CONTROL_METADATA_KEY in params or CONTROL_METADATA_KEY in arguments:
            return self._deny(rpc_msg, base, now, ["agent_supplied_control_metadata"])

        if self._policy_error:
            return self._deny(rpc_msg, base, now, [self._policy_error])
        policy = self.manifest.tools.get(tool_name)
        if policy is None:
            return self._deny(rpc_msg, base, now, [f"tool_not_in_manifest:{tool_name}"])

        allowed_args = policy["args"]
        unknown = sorted(a for a in arguments if a not in allowed_args)
        if unknown:
            barred = set(self.manifest.forbidden_targets)
            reasons = [f"forbidden_target:{a}" for a in unknown if a in barred]
            reasons += [f"argument_out_of_policy:{a}" for a in unknown if a not in barred]
            return self._deny(rpc_msg, base, now, reasons)

        features = sorted({allowed_args[a] for a in arguments if allowed_args[a]})
        physical_params = None
        if policy.get("physical"):
            physical_params = {k: arguments[k] for k in sorted(PHYSICAL_PARAM_KEYS) if k in arguments} or None

        action = Action(
            requester=requester,
            tool=tool_name,
            features=features,
            jurisdiction=policy.get("jurisdiction", self.manifest.jurisdiction),
            requester_operator=self.default_operator,
            purpose=f"mcp:{tool_name}",
            reversible=policy["class"] != "irreversible",
            population_affecting=bool(policy.get("population_affecting", False)),
            stores_in=list(policy.get("stores_in", [])),
            physical_params=physical_params,
        )
        nonce = call_nonce(tool_name, arguments)
        digest = compute_action_digest(self.manifest, action, nonce)

        consent_result = None
        consents = []
        timed_out = False
        closes = None
        if (not action.reversible) or action.population_affecting:
            opened = self.pending.setdefault(digest, now)
            closes = opened + self.approval_window_seconds
            timed_out = now >= closes

            if self.consent_framework is not None:
                # Layer 3: multi-party threshold consent (Ed25519, veto-on-silence).
                consent_result = self.consent_framework.check_consent(
                    action=action,
                    approvals=self.approval_store.load(),
                    now=now,
                    timed_out=timed_out,
                    requester_operator=self.default_operator,
                    declaring_party=action.declared_by,
                    round_nonce=nonce,
                )
                # Feed the threshold signatures into the normative engine as
                # bound votes so its conflict-of-interest and dissent rules see
                # the same ring. The framework result is merged after.
                consents = [
                    Consent(op, "threshold-consent", decision="yes", bound_action_digest=digest)
                    for op in consent_result.yes_operators
                ]
            else:
                # Legacy HMAC-based consent (v0.1.4).
                consents = accept_approvals(
                    self.approval_store.load(), self.approvers, digest, now
                )

        # The 15-rule engine ALWAYS runs. Consent cannot bypass a safety rule;
        # merge_threshold_consent() enforces the precedence.
        decision = evaluate(
            self.manifest,
            action,
            consents,
            signer=self.signer,
            now=now,
            timed_out=timed_out,
            ledger=self.ledger,
            round_nonce=nonce,
        )
        decision = merge_threshold_consent(decision, consent_result, self._hold)

        if decision.verdict != ESCALATE:
            self.pending.pop(digest, None)  # a refusal or approval closes this round

        extra: dict[str, Any] = {"action_digest": digest}
        if decision.verdict == ESCALATE and closes is not None:
            extra["approval_window_closes"] = closes
        if consent_result is not None:
            extra["threshold_consent"] = {
                "consent_met": consent_result.consent_met,
                "vetoed": consent_result.vetoed,
                "reason": consent_result.reason,
                "yes_operators": consent_result.yes_operators,
                "no_operators": consent_result.no_operators,
            }
        if self.decision_tier is not None and decision.verdict == COMPLIANT:
            decision, extra["decisions"] = self._apply_decision_tier(decision, tool_name, arguments, policy, action)
        return self._record(rpc_msg, decision, action, now, extra)

    def _apply_decision_tier(
        self, decision: Decision, tool_name: str, arguments: dict, policy: dict, action: Action
    ) -> tuple[Decision, dict[str, Any]]:
        """Let the experimental tier review a call LOKAHI allowed. It can only make it stricter."""
        from decisions import combine  # imported only when the tier is on

        review = self.decision_tier.review(
            tool_name, arguments, policy, list(action.features), list(decision.reasons), purpose=action.purpose
        )
        verdict = combine(decision.verdict, review.get("decision"))
        if verdict == COMPLIANT:
            return decision, review
        stricter = Decision(
            verdict,
            [f"decisions_tier:{review.get('reason_code', 'DDM-09')}"],
            quorum_required=decision.quorum_required,
            quorum_met=decision.quorum_met,
            actuator_interlock=self._hold,  # as LOKAHI does for its own escalations and refusals
        )
        return stricter, review


# ----------------------------------------------------------------------
# self-test
# ----------------------------------------------------------------------

SELF_TEST_TOOLS = {
    "read_meter": {"class": "read", "args": {"meter_id": "meter_id", "usage_liters": "usage_liters"}},
    "cutoff_water_valve": {"class": "irreversible", "args": {"meter_id": "meter_id"}},
    "move_cartesian": {
        "class": "write",
        "physical": True,
        "args": {
            "ee_force_torque": "ee_force_torque",
            "contact_force_n": None,
            "cartesian_velocity_ms": None,
            "waypoint_m": None,
        },
    },
}


def _call(msg_id: int, tool: str, arguments: dict, params_extra: dict | None = None) -> dict:
    params: dict[str, Any] = {"name": tool, "arguments": arguments}
    if params_extra:
        params.update(params_extra)
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}


def _approve(store: ApprovalStore, keys: dict[str, Signer], digest: str, decision: str = "yes", sign_with: Signer | None = None) -> None:
    for i, (operator, key) in enumerate(sorted(keys.items())):
        store.add(
            Approval(operator, f"device-{i}", decision, digest, "2027-02-01T00:00:00Z", f"{digest[:12]}-{operator}").sign(
                sign_with or key
            )
        )


def run_self_test() -> int:
    signer = Signer(b"mcp-gateway-selftest-manifest-key-01")
    approver_keys = {
        "steward:delhi-jal": Signer(b"approver-key-delhi-jal-000000000"),
        "steward:ombudsman": Signer(b"approver-key-ombudsman-00000000"),
        "steward:ward-council": Signer(b"approver-key-ward-council-00000"),
    }
    attacker = Signer(b"attacker-key-pretending-approver")
    now = 1_800_000_000.0
    manifest = Manifest(
        node="mcp:municipal-grid-gateway",
        informational=["usage_liters", "meter_id", "ee_force_torque"],
        forbidden_targets=["protest_history", "demographic_stratification_proxy"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 2, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:dirgha.example/mcp/grid",
        physical_envelope={
            "max_contact_force_n": 6.1,
            "max_cartesian_velocity_ms": 0.5,
            "max_decel_ms2": 1.0,
            "workspace_bounds_m": [0.0, 2.0, -1.0, 1.0, 0.2, 2.4],
        },
        tools=SELF_TEST_TOOLS,
        approval_window_seconds=900,
    ).sign(signer)

    store = ApprovalStore()
    gw = LokahiMCPGateway(manifest, signer, approvers=approver_keys, approval_store=store)
    results: list[tuple[str, dict, bool, str]] = []

    def check(label: str, res: dict, exp_allowed: bool, exp_reason: str) -> None:
        results.append((label, res, exp_allowed, exp_reason))

    check("1. Benign read_meter call", gw.intercept(_call(1, "read_meter", {"meter_id": "M-104", "usage_liters": True}), now=now), True, "within_manifest")
    check("2. Barred target smuggled in as an extra argument", gw.intercept(_call(2, "read_meter", {"meter_id": "M-104", "protest_history": True}), now=now), False, "forbidden_target:protest_history")
    check("3. Irreversible valve cutoff, no approvals yet -> ESCALATE", gw.intercept(_call(3, "cutoff_water_valve", {"meter_id": "M-104"}), now=now), False, "insufficient_disjoint_consent")
    check("4. Same cutoff after the approval window closes -> FORBIDDEN", gw.intercept(_call(4, "cutoff_water_valve", {"meter_id": "M-104"}), now=now + 900), False, "non_response")

    first = gw.intercept(_call(5, "cutoff_water_valve", {"meter_id": "M-105"}), now=now)
    digest_105 = first["error"]["data"]["action_digest"]
    _approve(store, approver_keys, digest_105)
    check("5. Three signed out-of-band approvals -> COMPLIANT", gw.intercept(_call(5, "cutoff_water_valve", {"meter_id": "M-105"}), now=now + 60), True, "quorum_met")

    check(
        "6. Robot move breaks the stopping-distance barrier",
        gw.intercept(_call(6, "move_cartesian", {"ee_force_torque": True, "contact_force_n": 5.0, "cartesian_velocity_ms": 0.45, "waypoint_m": [1.95, 0.0, 1.0]}), now=now),
        False,
        "physical_stopping_distance_exceeded",
    )
    self_approval = {CONTROL_METADATA_KEY: {"consents": [{"operator": op, "node": "n", "decision": "yes"} for op in approver_keys], "timed_out": False}}
    check("7. Agent tries to approve itself -> FORBIDDEN", gw.intercept(_call(7, "cutoff_water_valve", {"meter_id": "M-106"}, self_approval), now=now), False, "agent_supplied_control_metadata")
    check("8. Tool not in the manifest -> FORBIDDEN", gw.intercept(_call(8, "drop_all_records", {}), now=now), False, "tool_not_in_manifest:drop_all_records")

    first = gw.intercept(_call(9, "cutoff_water_valve", {"meter_id": "M-107"}), now=now)
    _approve(store, approver_keys, first["error"]["data"]["action_digest"], sign_with=attacker)
    check("9. Approvals forged with the wrong key are ignored", gw.intercept(_call(9, "cutoff_water_valve", {"meter_id": "M-107"}), now=now + 60), False, "insufficient_disjoint_consent")
    check("10. Approvals for valve M-105 replayed for M-108 are ignored", gw.intercept(_call(10, "cutoff_water_valve", {"meter_id": "M-108"}), now=now + 60), False, "insufficient_disjoint_consent")

    print("=" * 92)
    print(f"LOKAHI v1.0.0 - MCP JSON-RPC 2.0 CASCADING GATEWAY SELF-TEST ({len(results)} SCENARIOS)")
    print("=" * 92)
    passed = 0
    for label, res, exp_allowed, exp_reason in results:
        allowed = bool(res.get("allowed"))
        reasons = res["lokahi_receipt"]["reasons"] if allowed else res["error"]["data"]["reasons"]
        ok = allowed == exp_allowed and any(r.startswith(exp_reason) for r in reasons)
        passed += ok
        print(f"{'[PASS]' if ok else '[FAIL]'} {label:<60} | Allowed={str(allowed):<5} | {reasons[0]}")

    chain_ok, chain_len = gw.ledger.verify()
    print("-" * 92)
    print(f"MCP Gateway Results: {passed}/{len(results)} passed | Hash-Chained Ledger: {chain_len} entries (valid={chain_ok})")
    print("=" * 92)
    return 0 if (passed == len(results) and chain_ok) else 1


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def load_approvers(path: str) -> dict[str, Signer]:
    """approvers.json maps operator id -> key file path (relative paths resolve next to the JSON)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("approvers file must map operator ids to key file paths")
    base = os.path.dirname(os.path.abspath(path))
    approvers: dict[str, Signer] = {}
    for operator, key_path in data.items():
        resolved = key_path if os.path.isabs(key_path) else os.path.join(base, key_path)
        approvers[str(operator)] = Signer(load_key(resolved))
    return approvers


ED25519_PREFIX = "ed25519:"


def _verifier_from_text(text: str):
    """Build a manifest verifier from key text.

    ``ed25519:<64 hex>`` yields an Ed25519 verifier (the matching ``.pub`` file
    is preferred by :func:`load_manifest_verifier`); anything else is treated
    as an HMAC-SHA256 secret, preserving v0.1.x behaviour.
    """
    text = text.strip()
    if text.startswith(ED25519_PREFIX):
        _, _, _signer_cls, _, available = _import_consent_framework()
        if not available:
            raise ValueError(
                "the manifest key is Ed25519 but the 'cryptography' package is not installed"
            )
        seed = bytes.fromhex(text[len(ED25519_PREFIX):])
        if len(seed) != 32:
            raise ValueError("an Ed25519 private key must be 32 bytes")
        return _signer_cls.from_private_bytes(seed)
    return Signer(text.encode("utf-8"))


def load_manifest_verifier(key_file: str):
    """Load the manifest verification key, Ed25519-aware.

    Prefers the public-key file written next to an Ed25519 private key
    (``<key>.pub``) so the gateway never needs the private key. Falls back to
    deriving the public key from the private seed, then to HMAC-SHA256.
    """
    with open(key_file, encoding="ascii") as f:
        text = f.read().strip()
    if text.startswith(ED25519_PREFIX):
        _, verifier_cls, _, _, available = _import_consent_framework()
        pub_path = key_file + ".pub"
        if available and os.path.exists(pub_path):
            with open(pub_path, encoding="ascii") as f:
                pub_hex = f.read().strip()
            return verifier_cls.from_public_bytes(bytes.fromhex(pub_hex))
    return _verifier_from_text(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOKAHI v1.0.0 MCP JSON-RPC 2.0 cascading enforcement gateway")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in gateway self-test")
    parser.add_argument("--manifest", help="Path to a signed LOKAHI manifest JSON with a tools policy")
    parser.add_argument("--key-file", help="Manifest verification key file (or set LOKAHI_MANIFEST_KEY)")
    parser.add_argument("--approvers", help="JSON mapping approver ids to their key files")
    parser.add_argument("--approvals", help="JSONL file of signed approvals (written by tools/lokahi_approve.py)")
    parser.add_argument("--ledger", help="Optional path to append JSONL ledger entries")
    parser.add_argument("--requester", default="agent:mcp-client", help="Identity of the agent being gated")
    parser.add_argument("--operator", default="org:local-operator", help="Who runs the agent (cannot approve its own calls)")
    parser.add_argument(
        "--decisions",
        choices=DECISIONS_MODES,
        default="off",
        help="Experimental learned decisions tier (default off). 'experimental' lets a small model make calls LOKAHI allowed stricter, never looser",
    )
    parser.add_argument(
        "--preflight-wasm",
        help="Path to the Layer 1 WASM pre-flight module (preflight.wasm). Enables sub-millisecond deterministic checks before the 15-rule engine.",
    )
    parser.add_argument(
        "--require-preflight",
        action="store_true",
        default=False,
        help="Require the WASM pre-flight module; refuse to start if it cannot be loaded (fail closed).",
    )
    parser.add_argument(
        "--slm-model",
        help="Path to a small language model (ONNX/GGML/MLX) for Layer 2 semantic judgment. Falls back to the bundled pure-Python model if omitted.",
    )
    parser.add_argument(
        "--slm-timeout",
        type=float,
        default=5.0,
        help="SLM inference timeout in seconds. Exceeded → ESCALATE (fail closed).",
    )
    parser.add_argument(
        "--validator-keys",
        help="Path to approvers.json from `lokahi_sign.py keygen --threshold m --n n`. Enables Ed25519 threshold consent.",
    )
    parser.add_argument(
        "--require-consent-framework",
        action="store_true",
        default=False,
        help="Require the threshold consent framework; refuse to start if it cannot be loaded (fail closed).",
    )
    parser.add_argument(
        "--webxr-endpoint",
        help="WebXR teleoperation endpoint URL for fail-secure human manual oversight (e.g. wss://webxr.example.com/teleop).",
    )
    parser.add_argument(
        "--tool-pins",
        help="Signed tool pin set (from `lokahi_toolpin.py pin`). Refuses tools whose policy changed after review.",
    )
    parser.add_argument(
        "--require-pins",
        action="store_true",
        default=False,
        help="Require a tool pin set; refuse to start without one (fail closed).",
    )
    parser.add_argument(
        "--http-listen",
        help="Serve MCP Streamable HTTP on HOST:PORT instead of stdio (e.g. 127.0.0.1:8787).",
    )
    parser.add_argument(
        "--http-token",
        help="Comma-separated bearer token allowlist for HTTP. Static stop-gap; prefer --oauth-*. Or set LOKAHI_HTTP_TOKEN.",
    )
    parser.add_argument("--oauth-issuer", help="Comma-separated accepted token issuers (iss).")
    parser.add_argument("--oauth-audience", help="Required token audience; normally the gateway resource URI.")
    parser.add_argument("--oauth-jwks", help="JWKS URL for RS256/ES256 token verification.")
    parser.add_argument("--oauth-alg", default="RS256,ES256", help="Comma-separated algorithm allowlist.")
    parser.add_argument("--oauth-hs256-secret", help="Shared secret for HS256 tokens (local testing only). Or set LOKAHI_OAUTH_HS256_SECRET.")
    parser.add_argument("--oauth-resource", help="Canonical resource URI published in RFC 9728 metadata.")
    parser.add_argument("--tool-acl", help="JSON file mapping consumers to allowed tools (R1-10).")
    args = parser.parse_args(argv)

    if args.self_test or not args.manifest:
        return run_self_test()

    def refuse(reason: str) -> int:
        print(f"lokahi: refusing to start: {reason}", file=sys.stderr)
        return 2

    key = None
    try:
        if args.key_file:
            signer = load_manifest_verifier(args.key_file)
        elif os.environ.get("LOKAHI_MANIFEST_KEY"):
            signer = _verifier_from_text(os.environ["LOKAHI_MANIFEST_KEY"])
        else:
            return refuse("no manifest verification key (use --key-file or LOKAHI_MANIFEST_KEY)")
        with open(args.manifest, encoding="utf-8") as f:
            manifest = Manifest.from_dict(json.load(f))
        approvers = load_approvers(args.approvers) if args.approvers else {}
    except (OSError, ValueError) as exc:
        return refuse(str(exc))

    problem = manifest_status(manifest, signer)
    if problem is not None:
        return refuse(problem.reasons[0])
    if manifest.tools is None:
        return refuse("manifest has no tools policy; every tool is denied without one")
    try:
        validate_tool_policy(manifest.tools)
    except ValueError as exc:
        return refuse(f"invalid tools policy: {exc}")

    try:
        decision_tier = _make_decision_tier(args.decisions)
    except (OSError, ValueError) as exc:
        return refuse(f"cannot load the experimental decisions model: {exc}")

    # Layer 1: initialize WASM pre-flight if a path was provided.
    preflight = None
    if args.preflight_wasm:
        try:
            preflight = WasmtimePreflight(args.preflight_wasm)
        except WasmtimeUnavailable as exc:
            if args.require_preflight:
                return refuse(f"cannot load WASM pre-flight module: {exc}")
            print(f"lokahi: warning: WASM pre-flight unavailable: {exc}", file=sys.stderr)
    elif args.require_preflight:
        return refuse("--require-preflight set but no --preflight-wasm provided")

    # Layer 2: initialize SLM semantic judge if a model path was provided.
    _SLMJudge, _SLMJudgeUnavailable, _slm_available = _import_slm_judge()
    slm_judge = None
    if args.slm_model:
        if not _slm_available:
            print("lokahi: warning: SLM judge backend not available", file=sys.stderr)
        else:
            try:
                slm_judge = _SLMJudge(
                    model_path=args.slm_model,
                    inference_timeout_seconds=args.slm_timeout,
                )
            except _SLMJudgeUnavailable as exc:
                print(f"lokahi: warning: SLM judge unavailable: {exc}", file=sys.stderr)

    # Layer 3: initialize threshold consent framework if validator keys were provided.
    _ConsentFramework, _Ed25519Verifier, _Ed25519Signer, _Threshold, _cf_available = _import_consent_framework()
    consent_framework = None
    if args.validator_keys:
        if not _cf_available:
            if args.require_consent_framework:
                return refuse("threshold consent framework not available (cryptography package missing)")
            print("lokahi: warning: threshold consent framework not available", file=sys.stderr)
        else:
            try:
                with open(args.validator_keys, encoding="utf-8") as f:
                    vk_data = json.load(f)
                threshold_cfg = vk_data.get("threshold", {"m": 2, "n": 3})
                validators = vk_data.get("validators", {})
                validator_verifiers = {}
                for operator, pub_hex in validators.items():
                    verifier = _Ed25519Verifier.from_public_bytes(bytes.fromhex(pub_hex))
                    validator_verifiers[operator] = verifier
                threshold = _Threshold(m=int(threshold_cfg["m"]), n=int(threshold_cfg["n"]))
                consent_framework = _ConsentFramework.from_manifest(
                    manifest, validator_verifiers, threshold=threshold
                )
            except (OSError, ValueError, KeyError) as exc:
                if args.require_consent_framework:
                    return refuse(f"cannot load threshold consent framework: {exc}")
                print(f"lokahi: warning: cannot load threshold consent framework: {exc}", file=sys.stderr)
    elif args.require_consent_framework:
        return refuse("--require-consent-framework set but no --validator-keys provided")

    # Per-consumer tool ACL.
    tool_acl = None
    if args.tool_acl:
        _ToolACL, _acl_available = _import_acl()
        if not _acl_available:
            return refuse("tool ACL support unavailable")
        try:
            tool_acl = _ToolACL.load(args.tool_acl)
        except (OSError, ValueError) as exc:
            return refuse(f"cannot load tool ACL: {exc}")

    # Tool pin set (rug-pull defence).
    tool_pins = None
    if args.tool_pins:
        try:
            with open(args.tool_pins, encoding="utf-8") as f:
                tool_pins = json.load(f)
        except (OSError, ValueError) as exc:
            return refuse(f"cannot read tool pin set: {exc}")

    gw = LokahiMCPGateway(
        manifest,
        signer,
        approvers=approvers,
        approval_store=ApprovalStore(args.approvals),
        default_requester=args.requester,
        default_operator=args.operator,
        ledger_path=args.ledger,
        experimental_decisions=decision_tier,
        preflight=preflight,
        require_preflight=args.require_preflight,
        slm_judge=slm_judge,
        consent_framework=consent_framework,
        failsecure_endpoint=args.webxr_endpoint,
        tool_pins=tool_pins,
        require_pins=args.require_pins,
        tool_acl=tool_acl,
    )
    # Streamable HTTP transport (R1-01), when requested.
    if args.http_listen:
        try:
            from lokahi_http import HttpTransportError, parse_listen, serve
        except ImportError as exc:  # pragma: no cover
            return refuse(f"HTTP transport unavailable: {exc}")
        try:
            host, port = parse_listen(args.http_listen)
        except HttpTransportError as exc:
            return refuse(str(exc))

        raw_tokens = args.http_token or os.environ.get("LOKAHI_HTTP_TOKEN") or ""
        tokens = [t.strip() for t in raw_tokens.split(",") if t.strip()]

        oauth_verifier = None
        resource_metadata = None
        issuers = [i.strip() for i in (args.oauth_issuer or "").split(",") if i.strip()]
        if issuers or args.oauth_audience or args.oauth_jwks:
            if not (issuers and args.oauth_audience):
                return refuse("OAuth requires both --oauth-issuer and --oauth-audience")
            resource = args.oauth_resource or f"http://{host}:{port}"
            secret = args.oauth_hs256_secret or os.environ.get("LOKAHI_OAUTH_HS256_SECRET")
            algorithms = [a.strip() for a in (args.oauth_alg or "").split(",") if a.strip()]
            try:
                from lokahi_oauth import JWTVerifier, ResourceMetadata

                oauth_verifier = JWTVerifier(
                    issuers=issuers,
                    audience=args.oauth_audience,
                    algorithms=algorithms or None,
                    jwks_url=args.oauth_jwks,
                    hs256_secret=secret.encode("utf-8") if secret else None,
                )
                resource_metadata = ResourceMetadata(
                    resource=resource,
                    authorization_servers=list(issuers),
                )
            except (ImportError, ValueError) as exc:
                return refuse(f"cannot configure OAuth: {exc}")

        loopback = host in ("127.0.0.1", "localhost", "::1")
        if not loopback and not tokens and oauth_verifier is None:
            return refuse(
                "refusing to listen on a non-loopback address without --http-token "
                "or OAuth configuration"
            )
        serve(
            gw,
            host,
            port,
            bearer_tokens=tokens,
            oauth_verifier=oauth_verifier,
            resource_metadata=resource_metadata,
        )
        return 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out: dict[str, Any] = {"jsonrpc": "2.0", "id": None, "allowed": False, "error": {"code": JSONRPC_ERR_PARSE, "message": "parse error"}}
        else:
            out = gw.intercept(msg)
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
