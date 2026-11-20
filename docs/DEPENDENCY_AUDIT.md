# LOKAHI Dependency Audit

Inventory of all dependencies used by LOKAHI v1.0.0, their purpose, license, and security posture.

## Python Dependencies

| Package | Purpose | License | Version | Security Notes |
|---------|---------|---------|---------|----------------|
| `cryptography` | Ed25519 threshold signatures (Layer 3) | BSD/Apache-2.0 | >=42.0 | Well-audited, FIPS 140-2 module available. Used only for Ed25519 sign/verify. |
| `wasmtime` | WebAssembly runtime (Layer 1 bridge) | Apache-2.0 | >=15.0 | Sandboxed WASM execution with Wasi capability restrictions. No network, no FS write. |
| `onnxruntime` | SLM inference backend (Layer 2, optional) | MIT | >=1.17 | Optional, only when `--slm-model` with ONNX model is used. |
| `llama-cpp-python` | GGML/llama.cpp backend (Layer 2, optional) | MIT | >=0.2 | Optional, only when `--slm-model` with GGML model is used. |
| `mlx-lm` | MLX backend (Layer 2, optional, Apple Silicon) | MIT | >=0.5 | Optional, only when `--slm-model` with MLX model is used. |

### Zero-Dependency Core

The core LOKAHI engine (`tools/lokahi.py`) has **zero dependencies** — standard library only. This is a deliberate design choice to minimize the trusted computing base for the 15-rule decision engine, hash-chained ledger, and manifest verification.

## Rust Dependencies (WASM Crate)

| Crate | Purpose | License | Version | Security Notes |
|-------|---------|---------|---------|----------------|
| `serde` / `serde_json` | JSON serialization | MIT/Apache-2.0 | 1.x | Well-audited, used only for JSON parse/serialize. |
| `regex` | Compiled regex allowlist/blocklist | MIT/Apache-2.0 | 1.x | Uses RE2-like backtracking-limited engine. No ReDoS risk. |

## Dependency Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| `cryptography` supply chain | Low | Well-maintained, widely audited, FIPS 140-2 option. Pin exact versions in production. |
| `wasmtime` WASM escape | Low | Wasmtime is mature, sandboxed with Wasi restrictions. WASM module has no network/FS write access. |
| Optional SLM backends | Medium | Only loaded when explicitly configured. Model files are versioned and pinned. No self-training. |
| `regex` ReDoS | Low | Uses bounded backtracking engine. Patterns are pre-compiled and vetted. |

## Dependency Management

- **Pin exact versions** in production deployments (e.g., `cryptography==42.0.0`).
- **Vendor dependencies** for air-gapped deployments.
- **Regular dependency updates** via `cargo update` / `pip install --upgrade`.
- **Vulnerability scanning** via `cargo audit` and `pip-audit` or equivalent.

## Dependency-Free Guarantee

The following components have **zero dependencies** and can run in fully air-gapped environments:

- `tools/lokahi.py` — 15-rule decision engine, manifest validation, consent evaluation, hash-chained ledger
- `tools/lokahi_sign.py` (HMAC mode) — Key generation and manifest signing
- `tools/lokahi_approve.py` (HMAC mode) — Approval signing
- `tools/mcp_stdio_proxy.py` — MCP gateway (without optional WASM/SLM/consent components)

Dependencies are only introduced for:
- Ed25519 signatures (`cryptography`)
- WASM execution (`wasmtime`)
- SLM model inference (optional backends)
