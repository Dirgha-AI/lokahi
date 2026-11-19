# -*- coding: utf-8 -*-
"""LOKAHI Layer 1 — Python bridge to the WASM deterministic pre-flight module.

Loads ``preflight.wasm`` (built from ``tools/wasm_preflight/``) via Wasmtime and
exposes a synchronous ``check(input_dict, manifest_dict) -> PreflightVerdict``
API. All checks run inside the WASM sandbox with a sub-millisecond budget.

Memory contract (v1.0.1): the module exports ``lokahi_alloc``/``lokahi_free``.
The bridge allocates request and manifest buffers through the module's own
allocator, so nothing is written at fixed offsets and the module's data segment
is never overlapped. The result buffer is allocated per call and freed after it
is read.

If the WASM runtime or module is unavailable, the bridge raises
``WasmtimeUnavailable`` and the caller should fall back to pure-Python
deterministic checks (fail-safe, never fail-open).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

try:
    import wasmtime

    _WASMTIME_AVAILABLE = True
except ImportError:  # pragma: no cover
    wasmtime = None  # type: ignore
    _WASMTIME_AVAILABLE = False


class WasmtimeUnavailable(RuntimeError):
    """Raised when the Wasmtime runtime or WASM module cannot be loaded."""


@dataclass
class PreflightVerdict:
    """Result of a Layer 1 WASM pre-flight check."""

    verdict: str  # "ALLOW" | "REFUSE" | "ESCALATE"
    reasons: list[str]
    layer: str  # always "layer1_wasm"
    budget_us: int

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"

    @property
    def refused(self) -> bool:
        return self.verdict == "REFUSE"


class WasmtimePreflight:
    """WASM-backed Layer 1 pre-flight checker.

    Parameters
    ----------
    wasm_path:
        Path to ``preflight.wasm``. Defaults to
        ``tools/wasm_preflight/target/wasm32-unknown-unknown/release/lokahi_wasm_preflight.wasm``
        relative to the calling process, or the ``LOKAHI_WASM_PREFLIGHT_PATH``
        environment variable.
    max_input_bytes, max_manifest_bytes:
        Upper bounds on request and manifest size. Exceeding them fails closed
        with a clear reason rather than attempting a huge allocation.
    """

    DEFAULT_MAX_INPUT_BYTES = 64 * 1024          # 64 KiB is ample for a tool call
    DEFAULT_MAX_MANIFEST_BYTES = 4 * 1024 * 1024  # 4 MiB for large tool policies

    def __init__(
        self,
        wasm_path: Optional[str] = None,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
        max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    ) -> None:
        if not _WASMTIME_AVAILABLE:
            raise WasmtimeUnavailable("wasmtime Python package is not installed")

        self.wasm_path = wasm_path or os.environ.get(
            "LOKAHI_WASM_PREFLIGHT_PATH",
            self._default_wasm_path(),
        )
        if not os.path.exists(self.wasm_path):
            raise WasmtimeUnavailable(f"WASM module not found: {self.wasm_path}")

        self.max_input_bytes = max_input_bytes
        self.max_manifest_bytes = max_manifest_bytes

        self._engine = wasmtime.Engine()
        self._module = wasmtime.Module.from_file(self._engine, self.wasm_path)
        self._store = wasmtime.Store(self._engine)
        self._instance = wasmtime.Instance(self._store, self._module, [])

        exports = self._instance.exports(self._store)
        required = ("memory", "preflight_check", "preflight_check_len", "lokahi_alloc", "lokahi_free")
        missing = [name for name in required if exports.get(name) is None]
        if missing:
            raise WasmtimeUnavailable(
                "WASM module is missing required exports: " + ", ".join(missing)
            )
        self._memory = exports["memory"]
        self._preflight_check = exports["preflight_check"]
        self._preflight_check_len = exports["preflight_check_len"]
        self._alloc = exports["lokahi_alloc"]
        self._free = exports["lokahi_free"]

    @staticmethod
    def _default_wasm_path() -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(
                here,
                "wasm_preflight",
                "target",
                "wasm32-unknown-unknown",
                "release",
                "lokahi_wasm_preflight.wasm",
            ),
            os.path.join(
                "tools",
                "wasm_preflight",
                "target",
                "wasm32-unknown-unknown",
                "release",
                "lokahi_wasm_preflight.wasm",
            ),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return candidates[0]

    # ------------------------------------------------------------------
    # Memory helpers (always through the module allocator)
    # ------------------------------------------------------------------

    def _write_allocated(self, data: bytes) -> tuple[int, int]:
        """Allocate a buffer inside the module, copy `data` into it, return (ptr, len)."""
        length = len(data)
        ptr = int(self._alloc(self._store, length))
        if ptr == 0:
            raise WasmtimeUnavailable(f"WASM allocator returned null for {length} bytes")
        self._memory.write(self._store, data, start=ptr)
        return ptr, length

    def _read_and_free(self, ptr: int, length: int) -> bytes:
        """Read `length` bytes from the module and free the buffer."""
        try:
            return bytes(self._memory.read(self._store, start=ptr, stop=ptr + length))
        finally:
            self._free(self._store, ptr, length)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, input_dict: dict[str, Any], manifest: dict[str, Any]) -> PreflightVerdict:
        """Run all Layer 1 WASM checks against one tool call.

        Parameters
        ----------
        input_dict:
            Preflight input: ``{"tool": str, "arguments": dict, "agent": str|None,
            "arniko_findings": dict|None}``.
        manifest:
            The signed LOKAHI boundary manifest (dict).

        Returns
        -------
        PreflightVerdict
        """
        start = time.perf_counter()

        input_json = json.dumps(input_dict, separators=(",", ":")).encode("utf-8")
        manifest_json = json.dumps(manifest, separators=(",", ":")).encode("utf-8")

        def _us() -> int:
            return int((time.perf_counter() - start) * 1_000_000)

        if len(input_json) > self.max_input_bytes:
            return PreflightVerdict("REFUSE", ["input_too_large"], "layer1_wasm", _us())
        if len(manifest_json) > self.max_manifest_bytes:
            return PreflightVerdict("REFUSE", ["manifest_too_large"], "layer1_wasm", _us())

        input_ptr = manifest_ptr = 0
        input_len = manifest_len = 0
        try:
            input_ptr, input_len = self._write_allocated(input_json)
            manifest_ptr, manifest_len = self._write_allocated(manifest_json)

            now_us = int(time.time() * 1_000_000)
            result_ptr = int(
                self._preflight_check(
                    self._store,
                    input_ptr,
                    input_len,
                    manifest_ptr,
                    manifest_len,
                    now_us,
                )
            )
            result_len = int(self._preflight_check_len(self._store))
            if result_ptr == 0 or result_len == 0:
                return PreflightVerdict("REFUSE", ["wasm_result_unavailable"], "layer1_wasm", _us())

            raw = self._read_and_free(result_ptr, result_len)
        finally:
            if input_ptr:
                self._free(self._store, input_ptr, input_len)
            if manifest_ptr:
                self._free(self._store, manifest_ptr, manifest_len)

        result_json = json.loads(raw.decode("utf-8"))
        elapsed_us = _us()

        return PreflightVerdict(
            verdict=str(result_json.get("verdict", "REFUSE")),
            reasons=list(result_json.get("reasons", [])),
            layer=str(result_json.get("layer", "layer1_wasm")),
            budget_us=max(elapsed_us, int(result_json.get("budget_us", 0))),
        )

    def health_check(self) -> bool:
        """Return True if the WASM module is loaded and responsive."""
        try:
            self._memory.data_ptr(self._store)
            self._memory.size(self._store)
            probe = int(self._alloc(self._store, 16))
            if probe == 0:
                return False
            self._free(self._store, probe, 16)
            return True
        except Exception:
            return False
