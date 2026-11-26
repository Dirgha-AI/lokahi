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
# truncate: hand back the legal prefix of the chunk. replan: hand back that
# prefix plus a jerk-limited stop profile, so the arm decelerates instead of
# dropping the remainder on the floor.
CLAMP_STRATEGIES = ("truncate", "replan")
KNOWN_REPLAN_KEYS = {"max_acceleration_m_s2", "max_jerk_m_s3", "max_velocity_m_s"}

# Violation classes a policy may name, and the actions it may map them to.
POLICY_ACTIONS = ("CLAMP", "SOFT_STOP", "E_STOP")
POLICY_CLASSES = (
    "recovery",
    "stale_action",
    "clock_skew",
    "structural",
    "watchdog",
    "workspace_keep_in",
    "workspace_keep_out",
    "dynamic_keep_out",
    "kinematic",
    "wrench",
)

DEFAULT_POLICY = {
    # A refusal to leave a violated region is not an emergency: the robot is
    # already inside it, and latching a stop there leaves it there. Soft-stop
    # keeps the arm held while the next chunk is allowed to improve things.
    "recovery": "SOFT_STOP",
    "stale_action": "SOFT_STOP",
    "clock_skew": "SOFT_STOP",
    "structural": "SOFT_STOP",
    "watchdog": "SOFT_STOP",
    "workspace_keep_in": "E_STOP",
    "workspace_keep_out": "E_STOP",
    # A caller-supplied keep-out the manifest did not authorise is refused at
    # the same severity as any other obstacle conflict: an unvalidated set is
    # either a mistake or an attempt to shape the gate's own view of the world.
    "dynamic_keep_out": "E_STOP",
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
    "keep_out_shapes",
    "max_dynamic_keep_outs",
    "max_dynamic_radius_m",
    "tool_radius_m",
    "kinematic_limits",
    "policy_on_violation",
    "clamp_strategy",
    "replan_limits",
    "recovery_planning",
    "recovery_step_m",
    "recovery_max_steps",
}

SHAPE_KINDS = ("circle", "cylinder", "sphere", "capsule", "mesh")

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


def _require_count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VLAManifestError(f"{name} must be an integer count")
    if value < 0:
        raise VLAManifestError(f"{name} must not be negative")
    return value


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
    """Validated `physical_vla` bounds. Immutable: a limit cannot drift mid-episode.

    The escape hatch: `recovery_planning` adds an escape planner beside a
    keep-out refusal. Refusing every illegal one-step move from a pose that is
    already inside a hazard leaves the robot stationary inside it, and the
    outside world keeps charging it — the refusal is right, but on its own it
    is a trap. With this on, a refused chunk that names a keep-out still gets
    the same FORBIDDEN verdict, the same state and the same interlock, plus a
    `safe_action` of mode `escape` and the poses to leave by. Nothing about the
    refusal is softened, and the escape poses are subject to the same gate
    before they are executed. It is off by default because a deployment that
    cannot act on a `setpoints` payload should not be handed one.
    """

    max_action_age_ms: float
    watchdog_timeout_ms: float
    max_gate_eval_us: float
    control_period_ms: float
    execution_mode: str
    clamp_strategy: str = "truncate"
    replan_limits: dict[str, float] = field(default_factory=dict)
    recovery_planning: bool = False
    recovery_step_m: float = 0.02
    recovery_max_steps: int = 50
    workspace_aabb: tuple[float, float, float, float, float, float] | None = None
    keep_out_aabbs: tuple[tuple[float, float, float, float, float, float], ...] = ()
    # Normalised primitives: a box circumscribing a disc is up to 27% larger on
    # the diagonal, which refuses poses that are legally clear; a simulator
    # measured 467 such refusals on one task before shapes existed.
    keep_out_shapes: tuple[dict[str, Any], ...] = ()
    # Per-step keep-outs a caller may supply. Zero count means the feature is
    # off: a gate that never agreed to accept a set refuses one rather than
    # quietly ignoring it, because a silently dropped obstacle is a blind spot.
    max_dynamic_keep_outs: int = 0
    max_dynamic_radius_m: float | None = None
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
    # Whether a verifier accepted the document these bounds were read from.
    # `LokahiVLAGate` refuses a gate built from an unverified one unless the
    # caller opts out explicitly: a gate that enforces an unsigned, unverified
    # manifest reports COMPLIANT for input nobody vouched for.
    manifest_verified: bool = False

    def policy_for(self, violation_class: str) -> str:
        return self.policy_on_violation.get(violation_class, "SOFT_STOP")


def parse_keep_out_shape(name: str, value: Any) -> dict[str, Any]:
    """Normalise one keep-out primitive. Shapes the gate cannot check are fatal."""
    if not isinstance(value, Mapping):
        raise VLAManifestError(f"{name} must be an object")
    kind = value.get("kind", value.get("shape"))
    if kind not in SHAPE_KINDS:
        raise VLAManifestError(f"{name}.shape must be one of {SHAPE_KINDS}")
    allowed = {"shape", "kind", "centre", "start", "end", "radius", "height",
               "vertices", "triangles"}
    unknown = set(value) - allowed
    if unknown:
        raise VLAManifestError(f"{name} has unknown keys: {sorted(unknown)}")
    shape: dict[str, Any] = {"kind": kind}

    def point(key: str, size: int) -> tuple[float, ...]:
        raw_point = value.get(key)
        if not isinstance(raw_point, Sequence) or isinstance(raw_point, (str, bytes)):
            raise VLAManifestError(f"{name}.{key} must be a list of {size} numbers")
        if len(raw_point) != size:
            raise VLAManifestError(f"{name}.{key} must have {size} elements")
        return tuple(_require_finite(f"{name}.{key}[{i}]", v) for i, v in enumerate(raw_point))

    if kind == "mesh":
        vertices = value.get("vertices")
        triangles = value.get("triangles")
        if not isinstance(vertices, Sequence) or len(vertices) < 3:
            raise VLAManifestError(f"{name} is a mesh and needs at least three vertices")
        if not isinstance(triangles, Sequence) or not triangles:
            raise VLAManifestError(f"{name} is a mesh and needs triangles")
        parsed_vertices = []
        for i, vertex in enumerate(vertices):
            if not isinstance(vertex, Sequence) or len(vertex) != 3:
                raise VLAManifestError(f"{name}.vertices[{i}] must be three numbers")
            parsed_vertices.append(
                tuple(_require_finite(f"{name}.vertices[{i}][{axis}]", value_of)
                      for axis, value_of in enumerate(vertex))
            )
        parsed_triangles = []
        for i, triangle in enumerate(triangles):
            if not isinstance(triangle, Sequence) or len(triangle) != 3:
                raise VLAManifestError(f"{name}.triangles[{i}] must have three indices")
            indices = tuple(int(float(index)) for index in triangle)
            if any(index < 0 or index >= len(parsed_vertices) for index in indices):
                raise VLAManifestError(f"{name}.triangles[{i}] indexes a vertex that does not exist")
            parsed_triangles.append(indices)
        shape["vertices"] = tuple(parsed_vertices)
        shape["triangles"] = tuple(parsed_triangles)
        return shape

    shape["radius"] = _require_positive(f"{name}.radius", value.get("radius"))
    if kind == "capsule":
        shape["start"] = point("start", 3)
        shape["end"] = point("end", 3)
        return shape
    shape["centre"] = point("centre", 2 if kind == "circle" else 3)
    if kind == "cylinder":
        if "height" not in value:
            raise VLAManifestError(f"{name} is a cylinder and needs a height")
        shape["height"] = _require_positive(f"{name}.height", value.get("height"))
    return shape


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

    clamp_strategy = raw.get("clamp_strategy", "truncate")
    if clamp_strategy not in CLAMP_STRATEGIES:
        raise VLAManifestError(f"clamp_strategy must be one of {CLAMP_STRATEGIES}")

    replan = raw.get("replan_limits") or {}
    if not isinstance(replan, Mapping):
        raise VLAManifestError("replan_limits must be an object")
    unknown_replan = set(replan) - KNOWN_REPLAN_KEYS
    if unknown_replan:
        raise VLAManifestError(f"unknown replan_limits keys: {sorted(unknown_replan)}")
    replan_limits = {
        key: _require_positive(f"replan_limits.{key}", value)
        for key, value in replan.items()
    }
    if clamp_strategy == "replan" and "max_acceleration_m_s2" not in replan_limits:
        raise VLAManifestError(
            "clamp_strategy replan needs replan_limits.max_acceleration_m_s2"
        )

    keep_out = raw.get("keep_out_aabbs", []) or []
    if not isinstance(keep_out, Sequence) or isinstance(keep_out, (str, bytes)):
        raise VLAManifestError("keep_out_aabbs must be a list of six-element boxes")
    shapes = raw.get("keep_out_shapes", []) or []
    if not isinstance(shapes, Sequence) or isinstance(shapes, (str, bytes)):
        raise VLAManifestError("keep_out_shapes must be a list of shape objects")

    recovery_planning = raw.get("recovery_planning", False)
    if not isinstance(recovery_planning, bool):
        raise VLAManifestError("recovery_planning must be true or false")
    recovery_max_steps = raw.get("recovery_max_steps", 50)
    if isinstance(recovery_max_steps, bool) or not isinstance(recovery_max_steps, int):
        raise VLAManifestError("recovery_max_steps must be an integer")
    if recovery_max_steps <= 0:
        raise VLAManifestError("recovery_max_steps must be greater than zero")

    return {
        "max_action_age_ms": _require_positive("max_action_age_ms", raw.get("max_action_age_ms")),
        "watchdog_timeout_ms": _require_positive(
            "watchdog_timeout_ms", raw.get("watchdog_timeout_ms")
        ),
        "max_gate_eval_us": _require_positive("max_gate_eval_us", raw.get("max_gate_eval_us", 5000)),
        "control_period_ms": _require_positive("control_period_ms", raw.get("control_period_ms", 20)),
        "execution_mode": mode,
        "clamp_strategy": clamp_strategy,
        "replan_limits": replan_limits,
        "recovery_planning": recovery_planning,
        "recovery_step_m": _require_positive("recovery_step_m", raw.get("recovery_step_m", 0.02)),
        "recovery_max_steps": recovery_max_steps,
        "workspace_aabb": (
            _parse_aabb("workspace_aabb", raw["workspace_aabb"]) if "workspace_aabb" in raw else None
        ),
        "keep_out_aabbs": tuple(_parse_aabb(f"keep_out_aabbs[{i}]", box) for i, box in enumerate(keep_out)),
        "keep_out_shapes": tuple(
            parse_keep_out_shape(f"keep_out_shapes[{i}]", shape) for i, shape in enumerate(shapes)
        ),
        "max_dynamic_keep_outs": _require_count(
            "max_dynamic_keep_outs", raw.get("max_dynamic_keep_outs", 0)
        ),
        "max_dynamic_radius_m": (
            _require_positive("max_dynamic_radius_m", raw["max_dynamic_radius_m"])
            if "max_dynamic_radius_m" in raw
            else None
        ),
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

    Omitting the verifier does not verify anything; it records
    `manifest_verified=False` on the config, and `LokahiVLAGate` then refuses to
    use the bounds unless its caller passes `allow_unverified_manifest=True`.
    The loader itself stays permissive so a test can inspect a section without
    owning a signing key, but nothing that acts on the bounds is.
    """
    if not isinstance(manifest, Mapping):
        raise VLAManifestError("manifest must be an object")
    if verifier is not None and not verifier(manifest):
        raise VLAManifestError("unverifiable_manifest")
    raw = manifest.get(SECTION)
    if not isinstance(raw, Mapping):
        raise VLAManifestError(f"manifest has no {SECTION} section")

    config = _parse_section(raw)
    config["manifest_verified"] = verifier is not None

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
