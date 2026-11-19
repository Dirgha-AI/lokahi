# LOKAHI v1.0.0 Audit Report

**Date:** 2026-11-19
**Scope:** all work introduced in the v1.0.0 cascading-runtime branch
**Verdict:** not release-ready. Four critical defects reproduced.

## Critical

### C1 — Consent framework bypasses the 15-rule engine
**File:** `tools/mcp_stdio_proxy.py`, `_intercept_inner`
**Cause:** `evaluate()` is called only `if consent_result is None`. When a
`ConsentFramework` is configured, every irreversible or population-affecting
call skips Rules 4–8 and the physical envelope.

**Reproduction:**
```
manifest: jurisdiction IN-DL, residency [IN]
tool:     class=irreversible, jurisdiction=US, stores_in=[US]
plain gateway          -> FORBIDDEN jurisdiction_mismatch
consent gateway 1-of-3 -> ALLOWED   quorum_met

manifest: physical_envelope max_contact_force_n=6.1
tool:     class=irreversible, physical=true
args:     contact_force_n=999, cartesian_velocity_ms=50
plain gateway          -> FORBIDDEN physical_force_exceeded, ...
consent gateway 1-of-3 -> ALLOWED   quorum_met, actuator_interlock=pass
```

**Impact:** a single colluding validator can authorise a crush force 164x the
signed envelope. This contradicts the design thesis and the threat model.

**Fix:** always run `evaluate()`; merge so rules `FORBIDDEN` always wins and
consent can only promote `ESCALATE(insufficient_disjoint_consent)`.

### C2 — Privilege drop is not enforced
**File:** `tools/mcp_stdio_proxy.py`, `_intercept_inner`
**Cause:** `FailSecureManager.has_privilege()` is never consulted in the request
path. `enter_secure_state()` revokes the token, but `intercept()` ignores it.

**Reproduction:**
```
allowed = True
enter_secure_state(...) -> has_privilege() == False
allowed = True            <-- still permitted
```

### C3 — Documented Ed25519 CLI flow fails
**File:** `tools/mcp_stdio_proxy.py`, `main()`
**Cause:** the manifest verifier is constructed as an HMAC `Signer` from the key
file. An Ed25519-signed manifest therefore fails verification and the gateway
refuses to start (`unverifiable_manifest`).

### C4 — Package cannot be built
**File:** `pyproject.toml`
**Cause:** invalid TOML (multi-line inline table under `optional-dependencies`,
trailing commas), non-standard `[tool.setuptools.entry-points]` table, and
`tools/wasm_preflight/` has no `__init__.py` so its `package-data` never applies.

## High

### H1 — 4 KB manifest cap
`wasm_bridge.py` refuses any manifest over 4096 bytes with
`manifest_too_large`; a 40-tool manifest (8.9 KB) causes every call to be
refused. Fail-closed, but deployment-blocking.

### H2 — Fixed WASM memory offsets, latent truncation
The bridge writes to linear-memory offsets 0 and 4096 without an allocator.
Currently safe only because the linker placed the data segment at ~1.06 MB;
nothing guarantees that. The 8 KB result buffer truncates mid-JSON if reasons
grow, which would raise in `json.loads` and trip fail-secure.

### H3 — Duplicate dissent threshold
`lokahi_consent.py` hardcodes `>= 2` instead of reusing `SUSPENSION_QUORUM`.

## Medium / Low

- M1 dummy-manifest ledger appends in fail-secure and WebXR
- M2 ledger file append without lock/flush
- M3 unbounded rate-limiter bucket map
- M4 `predict_proba_one` returns fabricated distribution
- M5 dead code: `ThresholdApprovalStore`, `fail_secure_boundary`, `PreflightVerdict`
- M6 Layer 1 sees `{}` when arguments are not a dict
- M7 nine stale `v0.1.4` banners
- M8 309 MB `tools/wasm_preflight/target/` untracked and not ignored

## Test-suite blind spots

| Gap | Defect that shipped |
|---|---|
| No CLI Ed25519 test | C3 |
| No "consent must not bypass rules" test | C1 |
| No post-lock request test | C2 |
| No package build test | C4 |
| No large-manifest test | H1 |
| Rust tests never call `run_preflight` | H2 |

## Documentation overstatements

- `ARCHITECTURE.md` and `CHANGELOG.md` describe FROST threshold signatures; the
  implementation is independent Ed25519 signatures verified individually.
- `WEBXR_FALLBACK.md` and `THREAT_MODEL.md` describe privilege revocation as
  enforced; C2 shows it is not.
- `FUZZING_PLAN.md` describes targets that are not yet written.
