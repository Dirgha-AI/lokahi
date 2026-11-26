"""The `physical_vla` manifest section: schema, validation, fail-closed load.

Design rule for this module: it adds only what `physical_envelope` lacks. A
manifest that repeats a force or workspace limit here is refused, because two
sources of truth for one physical bound is the failure LOKAHI exists to prevent.

Every validation failure raises `VLAManifestError`. There is no permissive
default: a gate that cannot read its bounds must not guess them.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

SECTION = "physical_vla"

EXECUTION_MODES = ("sync", "lazy_async", "continuous_async")

# Violation classes a policy may name, and the actions it may map them to.
POLICY_ACTIONS = ("CLAMP", "SOFT_STOP", "E_STOP")
POLICY_CLASSES = (
    "stale_action",
    "clock_skew",
    "structural",
    "watchdog",
    "workspace_keep_in",
    "workspace_keep_out",
    "kinematic",
    "wrench",
)

DEFAULT_POLICY = {
    "stale_action": "SOFT_STOP",
    "clock_skew": "SOFT_STOP",
    "structural": "SOFT_STOP",
    "watchdog": "SOFT_STOP",
    "workspace_keep_in": "E_STOP",
    "workspace_keep_out": "E_STOP",
    "kinematic": "CLAMP",
    "wrench": "SOFT_STOP",
}

KNOWN_KEYS = {
    "max_action_age_ms",
    "watchdog_timeout_ms",
    "max_gate_eval_us",
    "control_period_ms",
    "execution_mode",
    "workspace_aabb",
    "keep_out_aabbs",
    "tool_radius_m",
    "kinematic_limits",
    "policy_on_violation",
}

KNOWN_KINEMATIC_KEYS = {
    "max_pos_delta_m",
    "max_rot_delta_rad",
    "max_joint_vel_rad_s",
    "max_joint_acc_rad_s2",
    "joint_limits",
}

# Limits that must stay in `physical_envelope` and must not be restated here.
ENVELOPE_ONLY = {
    "max_force_n",
    "max_torque_nm",
    "max_contact_force_n",
    "max_joint_torque_nm",
    "workspace_bounds_m",
    "workspace_aabb_m",
    "max_cartesian_velocity_ms",
}


class VLAManifestError(ValueError):
    """A `physical_vla` section that cannot be trusted."""


def _require_finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VLAManifestError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise VLAManifestError(f"{name} must be finite")
    return number


def _require_positive(name: str, value: Any) -> float:
    number = _require_finite(name, value)
    if number <= 0.0:
        raise VLAManifestError(f"{name} must be greater than zero")
    return number


def _parse_aabb(name: str, value: Any) -> tuple[float, float, float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise VLAManifestError(f"{name} must be a six-element list")
    if len(value) != 6:
        raise VLAManifestError(f"{name} must have exactly 6 elements, got {len(value)}")
    numbers = tuple(_require_finite(f"{name}[{i}]", v) for i, v in enumerate(value))
    if not (numbers[0] < numbers[3] and numbers[1] < numbers[4] and numbers[2] < numbers[5]):
        raise VLAManifestError(f"{name} must satisfy min < max on every axis")
    return numbers  # type: ignore[return-value]


@dataclass(frozen=True)
class PhysicalVLAConfig:
    """Validated `physical_vla` bounds. Immutable: a limit cannot drift mid-episode."""

    max_action_age_ms: float
    watchdog_timeout_ms: float
    max_gate_eval_us: float
    control_period_ms: float
    execution_mode: str
    workspace_aabb: tuple[float, float, float, float, float, float] | None = None
    keep_out_aabbs: tuple[tuple[float, float, float, float, float, float], ...] = ()
    tool_radius_m: float = 0.0
    max_pos_delta_m: float | None = None
    max_rot_delta_rad: float | None = None
    max_joint_vel_rad_s: float | None = None
    max_joint_acc_rad_s2: float | None = None
    joint_limits: tuple[tuple[float, float], ...] = ()
    policy_on_violation: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_POLICY))
    # Read from `physical_envelope`, not from this section.
    max_force_n: float | None = None
    max_torque_nm: float | None = None

    def policy_for(self, violation_class: str) -> str:
        return self.policy_on_violation.get(violation_class, "SOFT_STOP")


def _parse_section(raw: Mapping[str, Any]) -> dict[str, Any]:
    duplicated = set(raw) & ENVELOPE_ONLY
    if duplicated:
        raise VLAManifestError(
            f"{SECTION} may not restate envelope limits: {sorted(duplicated)}; "
            "they belong in physical_envelope"
        )
    unknown = set(raw) - KNOWN_KEYS
    if unknown:
        raise VLAManifestError(f"unknown keys in {SECTION}: {sorted(unknown)}")

    policy = dict(DEFAULT_POLICY)
    supplied = raw.get("policy_on_violation", {})
    if not isinstance(supplied, Mapping):
        raise VLAManifestError("policy_on_violation must be an object")
    for key, action in supplied.items():
        if key not in POLICY_CLASSES:
            raise VLAManifestError(f"policy_on_violation names unknown class: {key}")
        if action not in POLICY_ACTIONS:
            raise VLAManifestError(f"policy_on_violation[{key}] must be one of {POLICY_ACTIONS}")
        policy[key] = action

    limits = raw.get("kinematic_limits", {})
    if not isinstance(limits, Mapping):
        raise VLAManifestError("kinematic_limits must be an object")
    unknown_limits = set(limits) - KNOWN_KINEMATIC_KEYS
    if unknown_limits:
        raise VLAManifestError(f"unknown kinematic_limits keys: {sorted(unknown_limits)}")

    joint_limits: list[tuple[float, float]] = []
    for pair in limits.get("joint_limits", []) or []:
        if not isinstance(pair, Sequence) or len(pair) != 2:
            raise VLAManifestError("joint_limits entries must be [low, high]")
        low = _require_finite("joint_limits.low", pair[0])
        high = _require_finite("joint_limits.high", pair[1])
        if not low < high:
            raise VLAManifestError("joint_limits entries must satisfy low < high")
        joint_limits.append((low, high))

    mode = raw.get("execution_mode", "sync")
    if mode not in EXECUTION_MODES:
        raise VLAManifestError(f"execution_mode must be one of {EXECUTION_MODES}")

    keep_out = raw.get("keep_out_aabbs", []) or []
    if not isinstance(keep_out, Sequence) or isinstance(keep_out, (str, bytes)):
        raise VLAManifestError("keep_out_aabbs must be a list of six-element boxes")

    return {
        "max_action_age_ms": _require_positive("max_action_age_ms", raw.get("max_action_age_ms")),
        "watchdog_timeout_ms": _require_positive(
            "watchdog_timeout_ms", raw.get("watchdog_timeout_ms")
        ),
        "max_gate_eval_us": _require_positive("max_gate_eval_us", raw.get("max_gate_eval_us", 5000)),
        "control_period_ms": _require_positive("control_period_ms", raw.get("control_period_ms", 20)),
        "execution_mode": mode,
        "workspace_aabb": (
            _parse_aabb("workspace_aabb", raw["workspace_aabb"]) if "workspace_aabb" in raw else None
        ),
        "keep_out_aabbs": tuple(_parse_aabb(f"keep_out_aabbs[{i}]", box) for i, box in enumerate(keep_out)),
        "tool_radius_m": _require_finite("tool_radius_m", raw.get("tool_radius_m", 0.0)),
        "max_pos_delta_m": (
            _require_positive("max_pos_delta_m", limits["max_pos_delta_m"])
            if "max_pos_delta_m" in limits
            else None
        ),
        "max_rot_delta_rad": (
            _require_positive("max_rot_delta_rad", limits["max_rot_delta_rad"])
            if "max_rot_delta_rad" in limits
            else None
        ),
        "max_joint_vel_rad_s": (
            _require_positive("max_joint_vel_rad_s", limits["max_joint_vel_rad_s"])
            if "max_joint_vel_rad_s" in limits
            else None
        ),
        "max_joint_acc_rad_s2": (
            _require_positive("max_joint_acc_rad_s2", limits["max_joint_acc_rad_s2"])
            if "max_joint_acc_rad_s2" in limits
            else None
        ),
        "joint_limits": tuple(joint_limits),
        "policy_on_violation": policy,
    }


def load_physical_vla(
    manifest: Mapping[str, Any],
    verifier: Callable[[Mapping[str, Any]], bool] | None = None,
) -> PhysicalVLAConfig:
    """Read and validate the section from an already-parsed manifest document.

    `verifier` is the manifest signature check. It is called first and a False
    return is fatal: bounds from an unsigned manifest are not bounds. The caller
    supplies it so this module stays free of signing policy, which lives in
    `lokahi_sign` and `lokahi_consent`.
    """
    if not isinstance(manifest, Mapping):
        raise VLAManifestError("manifest must be an object")
    if verifier is not None and not verifier(manifest):
        raise VLAManifestError("unverifiable_manifest")
    raw = manifest.get(SECTION)
    if not isinstance(raw, Mapping):
        raise VLAManifestError(f"manifest has no {SECTION} section")

    config = _parse_section(raw)

    envelope = manifest.get("physical_envelope") or {}
    if not isinstance(envelope, Mapping):
        raise VLAManifestError("physical_envelope must be an object")
    force = envelope.get("max_contact_force_n", envelope.get("max_force_n"))
    torque = envelope.get("max_joint_torque_nm", envelope.get("max_torque_nm"))
    config["max_force_n"] = _require_positive("max_contact_force_n", force) if force is not None else None
    config["max_torque_nm"] = _require_positive("max_joint_torque_nm", torque) if torque is not None else None

    return PhysicalVLAConfig(**config)


def load_physical_vla_file(
    path: str,
    verifier: Callable[[Mapping[str, Any]], bool] | None = None,
) -> PhysicalVLAConfig:
    """Load from a manifest file. Parse failure is fatal, never a skip."""
    with open(path, "r", encoding="utf-8") as handle:
        try:
            document = json.load(handle)
        except json.JSONDecodeError as exc:
            raise VLAManifestError(f"manifest is not valid JSON: {exc}") from exc
    return load_physical_vla(document, verifier=verifier)
