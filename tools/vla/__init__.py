"""VLA runtime safety for LOKAHI (v1.4.0).

`physical_manifest` extends a signed LOKAHI manifest with the limits a
Vision-Language-Action policy needs and the core engine does not already carry:
freshness, watchdog, keep-out geometry, and per-violation policy.

Force, torque and workspace bounds are *not* restated here. They live in the
manifest's existing `physical_envelope` and are referenced by name, so one
physical limit has exactly one source of truth (see `physical_manifest`).
"""
from .physical_manifest import (
    EXECUTION_MODES,
    PhysicalVLAConfig,
    VLAManifestError,
    load_physical_vla,
)
from .vla_gate import LokahiVLAGate, VLADecisionResult

__all__ = [
    "EXECUTION_MODES",
    "LokahiVLAGate",
    "PhysicalVLAConfig",
    "VLADecisionResult",
    "VLAManifestError",
    "load_physical_vla",
]
