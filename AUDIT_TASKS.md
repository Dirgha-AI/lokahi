# LOKAHI Audit Remediation — Task List

Source: audit of v1.0.0 work-in-progress (see `AUDIT_REPORT.md`).
Rule: every fix lands with a regression test that fails before the fix.
No push without user confirmation.

---

## CRITICAL

### C1 — Consent framework bypasses the 15-rule engine — DONE
- [x] Restructure `_intercept_inner`: always call `evaluate()`
- [x] Merge rule: rules `FORBIDDEN` always wins; consent may only promote
      `ESCALATE(insufficient_disjoint_consent)` -> `COMPLIANT`
- [x] Consent veto (`non_response`, `suspended_by_ring`) cannot be overridden by rules `COMPLIANT`
- [x] Regression test: residency-violating irreversible call + full quorum -> `FORBIDDEN`
- [x] Regression test: jurisdiction-mismatched irreversible call + full quorum -> `FORBIDDEN`
- [x] Regression test: physical envelope breach + full quorum -> `FORBIDDEN` + `safe_mechanical_hold`
- [x] Regression test: valid irreversible call + quorum -> still `COMPLIANT`
- [x] Regression test: `suspended_by_ring` with quorum present -> `FORBIDDEN`

### C2 — Privilege drop not enforced — DONE
- [x] `_intercept_inner`: first check `failsecure.has_privilege()` -> deny `secure_state_active`
- [x] Regression test: call after `enter_secure_state()` is denied
- [x] Regression test: call after `rearm()` is allowed again
- [x] Regression test: cascade exception locks subsequent calls

### C3 — Ed25519 CLI flow broken — DONE
- [x] `main()`: load manifest verifier via Ed25519-aware loader (not HMAC `Signer`)
- [x] Preserve HMAC backward compatibility
- [x] Prefer `<key>.pub` so the gateway never needs the private key
- [x] Regression test: full CLI flow `keygen --ed25519` -> `manifest` -> gateway starts
- [x] Regression test: HMAC CLI flow still works

### C4 — Package cannot be built — DONE
- [x] Rewrite `pyproject.toml` as valid TOML
- [x] Move console scripts to `[project.scripts]`
- [x] Verify `python -m pip install --dry-run .` succeeds
- [x] Add `.gitignore` entry for `tools/wasm_preflight/target/`

---

## HIGH

### H1 — 4 KB manifest cap — DONE
- [x] Raise bridge caps and match WASM memory layout (or pass manifest hash)
      Defaults are now 64 KiB request / 4 MiB manifest, configurable per bridge.
- [x] Regression test: 40-tool manifest (~9 KB) -> legitimate call `ALLOW`
- [x] Regression test: oversized input still fails closed with clear reason

### H2 — Fixed WASM memory offsets / result truncation — DONE
- [x] Export `lokahi_alloc(len) -> ptr` / `lokahi_free(ptr, len)` from the Rust crate
- [x] Bridge allocates per call; no hardcoded offsets
- [x] Result buffer allocated per call and freed after read; no truncation
- [x] Regression test: large manifest round-trips intact
- [x] Regression test: 6,000 sequential calls, memory plateaus (no leak)

### H3 — Duplicate dissent logic — DONE
- [x] Single source of truth for `SUSPENSION_QUORUM`
- [x] `ConsentFramework` takes `suspension_quorum` (default from the engine constant)
- [x] Regression test: dissent threshold consistent with engine

---

## MEDIUM

- [x] M1 — Replace dummy-manifest ledger appends in `lokahi_failsecure.py`
- [x] M1 — Replace dummy-manifest ledger appends in `lokahi_webxr.py`
- [x] M1 — Regression test: fail-secure ledger entry carries real node id and valid chain
- [x] M2 — Ledger file append: lock + flush (+ optional fsync); documented single-writer constraint
- [x] M3 — Cap rate-limiter bucket map size (4096, fail closed on saturation)
- [x] M4 — `SLMJudge.predict_proba_one` delegates to the Python model or raises
- [x] M5 — Removed dead code (`ThresholdApprovalStore`, `fail_secure_boundary`, `PreflightVerdict`)
- [x] M6 — Layer 1 receives the same payload as the final decision
- [x] M7 — Version string sweep: removed stale `v0.1.4` banners
- [x] M8 — `.gitignore` `tools/wasm_preflight/target/`

---

## DOCUMENTATION CORRECTIONS

- [ ] `ARCHITECTURE.md`: state "independent Ed25519 signatures" not "threshold signatures (FROST)"
- [ ] `WEBXR_FALLBACK.md`: only claim enforcement after C2 fix verified
- [ ] `THREAT_MODEL.md`: only claim enforcement after C2/C1 fix verified
- [ ] `FUZZING_PLAN.md`: label targets as not-yet-implemented
- [ ] `CHANGELOG.md`: correct the v1.0.0 claims to match shipped behaviour
- [ ] `AUDIT_REPORT.md`: keep as the record of record

---

## Verification gate (before any push)

- [x] `python3 tests/test_core.py` — pass
- [x] `python3 tests/test_decisions.py` — pass
- [x] `python3 tests/test_threshold_consent.py` — pass
- [x] `python3 tests/test_failsecure.py` — pass
- [x] `python3 tests/test_audit_regressions.py` — 21 tests, pass
- [x] `python3 tools/mcp_stdio_proxy.py --self-test` — 10/10
- [x] `cargo test --release` in `tools/wasm_preflight` — 22 pass
- [x] `cargo build --target wasm32-unknown-unknown --release` — pass
- [x] `pip install --dry-run .` — succeeds
- [x] CLI Ed25519 e2e — succeeds
- [x] `git status` contains no build artifacts

## Outstanding (not code defects)

- [ ] Commit and push (awaiting user confirmation)
- [ ] Independent external review (see `docs/EXTERNAL_REVIEW_CHECKLIST.md`)
- [ ] Write the fuzz targets described in `docs/FUZZING_PLAN.md`
- [ ] Replace independent Ed25519 signatures with FROST threshold signing
- [ ] Disjointness attestation graph
