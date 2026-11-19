# LOKAHI Threat Model

Attack surfaces, threat actors, and mitigations for each layer of the LOKAHI multi-layered cascading enforcement runtime (v1.0.0).

## Threat Actors

| Actor | Capabilities | Motivation |
|-------|-------------|------------|
| **Compromised Agent** | Can issue arbitrary tool calls; may attempt to bypass the gate | Execute unauthorized actions, exfiltrate data |
| **Malicious Validator** | Holds a validator key share; may collude | Forge approvals, block legitimate actions |
| **Compromised Gateway** | Runs the LOKAHI MCP proxy; sees all requests | Alter decisions, log selectively, drop calls |
| **Network Attacker** | Can intercept, delay, or drop messages between components | Denial of service, replay attacks |
| **Physical Attacker** | Physical access to actuators/robots | Cause physical harm, property damage |

## Threats by Layer

### Layer 1 — WASM Deterministic Pre-Flight

| Threat | Severity | Mitigation |
|--------|----------|------------|
| **WASM sandbox escape** | Critical | Wasmtime with Wasi capability restrictions (no network, no FS write, read-only manifest only). WASM module is single-threaded, no system clock access. |
| **Regex evasion** (obfuscated injection strings) | High | Blocklist covers known patterns; schema validation rejects unknown arguments; rate limiting prevents brute-force evasion attempts. |
| **Schema bypass** (unknown arguments) | High | Default-deny tool policy: unknown tools and arguments are refused. Only manifest-declared args are accepted. |
| **Manifest forgery** | Critical | Signature verified (HMAC v1 / Ed25519 v2). WASM does structural checks; host performs full cryptographic verification. |
| **Rate-limit exhaustion** | Medium | Token bucket per (agent, tool); bursts capped; sustained rate limited. |
| **Budget exceeded** (DoS) | Medium | Pre-flight refuses if any check exceeds 500 µs budget. Fail closed, never fail open. |

### Layer 2 — SLM Semantic Judges

| Threat | Severity | Mitigation |
|--------|----------|------------|
| **Prompt injection through SLM** | High | SLM only reviews calls LOKAHI already allowed. SLM output is combined by stricter-of-two — it can only escalate/refuse, never allow. |
| **SLM model poisoning** | High | Model weights are versioned and pinned. No self-training at runtime (DDM-13). Retraining only from human-reviewed labels. |
| **SLM timeout / crash** | Medium | Hard timeout (default 5s). Timeout → ESCALATE. Unknown output → ESCALATE. |
| **Semantic evasion** (nuanced injection bypasses SLM) | Medium | Layer 1 blocks obvious injections first. Charter rules (DDM-01/02/05) checked in code before SLM. |

### Layer 3 — Multi-Party Threshold Consent

| Threat | Severity | Mitigation |
|--------|----------|------------|
| **Validator key compromise** | Critical | Each validator holds independent Ed25519 key. Gate holds only public keys. Threshold m-of-n requires multiple independent compromises. |
| **Gate forging approvals** | Critical | Gate never holds validator private keys. Cannot forge Ed25519 signatures. |
| **Replay attacks** | Medium | Approvals bound to exact action digest (tool + args). Nonce prevents reuse. Expiry window limits validity. |
| **Silence-as-consent** | Critical | Veto-on-silence: timeout → `non_response` REFUSE, never allowed. |
| **Dissent masked by conflicted votes** | High | Two dissenting validators → `suspended_by_ring` freeze, cannot be masked by conflicted votes. |
| **Self-approval** | Critical | Agent cannot approve its own calls. Operator cannot approve agent's calls. Emergency declarant cannot vote on own scope. |

### Layer 4 — Execution + Response Inspection

| Threat | Severity | Mitigation |
|--------|----------|------------|
| **Indirect prompt injection in tool response** | High | Tool responses inspected for hidden instructions. LOKAHI treats tool results as data, not control. |
| **Ledger tampering** | Critical | SHA-256 hash chain: each entry commits to previous. Any edit breaks the chain from that index onward. |

### Fail-Secure State & WebXR

| Threat | Severity | Mitigation |
|--------|----------|------------|
| **Failure bypass** (exception swallowed) | Critical | All cascade layers wrapped in try/except. Any exception → secure state + WebXR trigger. Never fail open. |
| **Unauthorized re-arm** | Critical | Re-arm requires cryptographic signature recorded to ledger. |
| **WebXR session hijack** | High | Operator identity bound to session. Actions signed by operator key. |
| **Privilege token theft** | Medium | Token revoked on secure state entry. New token issued on re-arm. |

## Risk Matrix

| Threat | Likelihood | Impact | Risk Level |
|--------|-----------|--------|------------|
| WASM sandbox escape | Low | Critical | High |
| Validator key compromise | Low | Critical | High |
| Gate forging approvals | Low | Critical | High |
| Prompt injection evasion | Medium | High | High |
| Silence-as-consent bypass | Low | Critical | High |
| Ledger tampering | Low | Critical | High |
| SLM model poisoning | Medium | High | High |
| Replay attacks | Medium | Medium | Medium |
| Self-approval | Low | Critical | High |
| Failure bypass | Low | Critical | High |

## Residual Risks (Known Limitations)

1. **Ed25519 key shares are not yet threshold-split** (RFC 9591 FROST planned). Each validator holds a full key pair; threshold is enforced in software, not cryptography.
2. **Disjointness is asserted, not attested.** Validator identity is a string; no cryptographic proof that two validators are distinct people.
3. **Approval windows reset on restart.** A restart opens a new consent window; it never approves anything, but in-progress windows are lost.
4. **No emergency override through the gate.** Emergency declarations are engine-only; the MCP filter doesn't accept them through the consent path.
5. **SLM model training data is synthetic.** Labels have not been human-reviewed (DDM-13 not yet met).

## Monitoring & Detection

| Signal | Source | Action |
|--------|--------|--------|
| Secure state entries | FailSecureManager.status() | Investigate cause, verify ledger |
| WebXR session activations | WebXRTeleopBroker.status() | Confirm human operator engagement |
| Ledger chain breaks | Ledger.verify() | Immediate incident response |
| Rate-limit refusals | WASM pre-flight reasons | Investigate potential brute-force |
| Consent timeouts | `non_response` reasons | Review steward availability |
| Dissent vetoes | `suspended_by_ring` reasons | Review operator conflict |
