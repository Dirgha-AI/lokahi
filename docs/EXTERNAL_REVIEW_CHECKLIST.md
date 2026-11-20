# LOKAHI External Review Checklist

Items for independent security and cryptographic review before production deployment of LOKAHI v1.0.0.

## 1. Cryptographic Review

### 1.1 Ed25519 Threshold Signatures
- [ ] **Key generation:** Each validator generates an independent Ed25519 key pair. No key material is shared.
- [ ] **Public key distribution:** The gate holds only validator public keys. No validator private key ever enters the gate.
- [ ] **Signature verification:** All approvals are verified against the correct validator's public key using `Ed25519Verifier.verify()`.
- [ ] **Threshold enforcement:** m-of-n distinct valid signatures required. m and n are configured per manifest.
- [ ] **Action digest binding:** Signatures are bound to the exact action digest (tool name + argument values), computed via `compute_action_digest()`.
- [ ] **Key file format:** Ed25519 private keys use `ed25519:` prefix + 64 hex chars (32-byte seed). HMAC keys are raw hex with no prefix.
- [ ] **Key storage:** Private keys are written with owner-only permissions (0o600 on POSIX).
- [ ] **Key rotation:** No key rotation protocol specified yet. Review needed for how manifest signing keys and validator keys are rotated.

### 1.2 HMAC-SHA256 Backward Compatibility
- [ ] v0.1.4 HMAC-SHA256 manifests still verify correctly.
- [ ] Mixed deployments (some validators Ed25519, some HMAC) are handled safely — HMAC never escalates to Ed25519 silently.

### 1.3 Canonical Serialization
- [ ] RFC 8785 JCS-compatible canonical JSON: sorted keys, whitespace stripped, UTF-8 encoded.
- [ ] Optional fields omitted from canonical form when unset (backward compatibility with v0.1.x signatures).

## 2. Consent Framework Review

### 2.1 Threshold Enforcement
- [ ] m-of-n distinct validators required for consent.
- [ ] Default threshold is 2-of-3, configurable per manifest via `consent_threshold`.
- [ ] Per-tool thresholds supported.

### 2.2 Veto-on-Silence
- [ ] Consent window has a deadline (`approval_window_seconds`).
- [ ] Fewer than `m` signatures before close → `non_response` REFUSE.
- [ ] Silence is never interpreted as consent.

### 2.3 Dissent Veto
- [ ] Two or more distinct validators voting "no" → `suspended_by_ring` freeze.
- [ ] Dissent freeze cannot be masked by conflicted votes.

### 2.4 Conflict-of-Interest
- [ ] Agent cannot approve its own calls (`agent_supplied_control_metadata` refused).
- [ ] Operator running the agent cannot approve the agent's calls (Rule 11).
- [ ] Emergency declarant cannot vote on their own scope (Rule 12).
- [ ] Each person counts once, however many agents they run.
- [ ] Equivocating operators (submitting conflicting votes) are disqualified from "yes" quorum but their dissent still counts.

### 2.5 Approval Validation
- [ ] Approval must match exact action digest.
- [ ] Approval must not be expired.
- [ ] Approval nonce must not be reused.
- [ ] Approver must be registered in the validator set.
- [ ] Signature must verify with the approver's public key.

## 3. Fail-Secure Review

### 3.1 Exception Handling
- [ ] All cascade layers wrapped in try/except (`intercept()` → `_intercept_inner()`).
- [ ] Any exception → `failsecure.handle_exception()` → secure state.
- [ ] Secure state revokes execution privilege token immediately.
- [ ] Secure state cannot be exited without human re-arm.

### 3.2 WebXR Teleoperation
- [ ] WebXR session triggered automatically on fail-secure entry.
- [ ] All teleop actions recorded to tamper-evident ledger.
- [ ] Re-arm requires cryptographic re-arm signature.
- [ ] System never auto-re-arms.
- [ ] Session release records duration and action count.

### 3.3 Privilege Token Lifecycle
- [ ] Token issued on construction.
- [ ] Token revoked on secure state entry.
- [ ] New token issued on re-arm.
- [ ] `has_privilege()` returns False when token is None or state is active.

## 4. Code Quality Review

### 4.1 Memory Safety (Rust WASM Crate)
- [ ] No `unsafe` blocks outside of WASM memory buffer access (which is required for the C ABI).
- [ ] No integer overflows in budget calculations.
- [ ] No buffer overflows in result serialization (8 KiB static buffer, length-checked).
- [ ] No panics on malformed input (all JSON parse errors handled).
- [ ] No division by zero.
- [ ] No unbounded recursion.

### 4.2 Python Code Quality
- [ ] Type hints on all public functions.
- [ ] Docstrings on all public classes and methods.
- [ ] Error handling for all I/O operations.
- [ ] No bare `except:` clauses (use `except Exception` with comments).
- [ ] Logging for security-relevant events.

### 4.3 Testing Coverage
- [ ] Unit tests for all new code (Rust + Python).
- [ ] Existing tests (test_core.py, test_decisions.py) still pass.
- [ ] End-to-end cascade tests verified.
- [ ] Edge cases tested: expired manifests, forged signatures, reused nonces, unregistered operators.

## 5. Architecture Review

### 5.1 Layer Separation
- [ ] Layer 1 (WASM) runs before any manifest or rule evaluation.
- [ ] Layer 2 (SLM) runs only when Layer 1 + 15 rules pass.
- [ ] Layer 3 (consent) runs only when Layer 2 allows.
- [ ] Layer 4 (execution) runs only when consent is met.
- [ ] Fail-secure wraps all layers.

### 5.2 Fail-Closed Defaults
- [ ] Unsigned manifest → REFUSE.
- [ ] Unverifiable manifest → REFUSE.
- [ ] Expired manifest → REFUSE.
- [ ] Unknown tool → REFUSE.
- [ ] Unknown argument → REFUSE.
- [ ] Missing consent quorum → ESCALATE / REFUSE.
- [ ] Consent timeout → REFUSE.
- [ ] Dissent veto → REFUSE.
- [ ] Any exception → secure state (REFUSE + lock).

### 5.3 Stricter-Only Guarantee
- [ ] SLM can turn ALLOW → ESCALATE / REFUSE, never the reverse.
- [ ] LOKAHI REFUSE / ESCALATE cannot be overridden by SLM.
- [ ] Charter rules can only make decisions stricter.

## 6. Known Limitations (Documented)

1. **Ed25519 key shares are not yet threshold-split** (RFC 9591 FROST planned). Each validator holds a full key pair; threshold is enforced in software.
2. **Disjointness is asserted, not attested.** Validator identity is a string; no cryptographic proof of distinct personhood.
3. **Approval windows reset on restart.** In-progress consent windows are lost on gateway restart.
4. **No emergency override through the gate.** Emergency declarations are engine-only.
5. **SLM training data is synthetic.** Labels not yet human-reviewed (DDM-13 not met).

## 7. Review Sign-Off

| Review Area | Reviewer | Date | Status | Notes |
|-------------|----------|------|--------|-------|
| Cryptographic signatures (Ed25519) | | | | |
| Threshold consent framework | | | | |
| Fail-secure + WebXR | | | | |
| Rust WASM memory safety | | | | |
| Python code quality | | | | |
| Architecture & fail-closed defaults | | | | |

## 8. Recommended Follow-Up Actions

1. Implement RFC 9591 FROST two-round threshold signatures (replace per-validator key pairs with true threshold key shares).
2. Add disjointness attestation graph (legal registration, key custody, infrastructure ownership).
3. Add key rotation protocol with historical auditability.
4. Add emergency override path through the gate (with strict rate-limiting).
5. Complete human review of SLM training labels (DDM-13).
6. Conduct independent penetration testing.
