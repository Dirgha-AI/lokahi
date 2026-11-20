# LOKAHI Production Architecture: Multi-Layered Cascading Enforcement Runtime

LOKAHI provides boundary-enforcement and disjoint-consent infrastructure for autonomous agentic collectives. This document details the technical architecture of the multi-layered cascading runtime (v1.0.0), which re-architects LOKAHI from a single Python decision filter into a cascaded enforcement pipeline where lightweight deterministic checks run first in a WebAssembly sandbox, escalating to SLM semantic judges only when needed, gated by multi-party threshold consent, and failing securely to human WebXR teleoperation.

## Architectural Foundation

The reference implementation (`tools/lokahi.py`) provides an executable specification: zero-dependency RFC 8785 canonical JSON formatting, deterministic evaluation of 15 ordered safety rules, vote binding, signed out-of-band approvals, and an append-only, SHA-256 hash-chained decision ledger. The MCP decision filter (`tools/mcp_stdio_proxy.py`) has these properties:

- It refuses agent-supplied control metadata.
- It takes all facts from a signed default-deny tool policy.
- It owns approval timeouts.
- It requires a verification key.

## Cascading Architecture

LOKAHI v1.0.0 enforces a four-layer cascade plus a fail-secure fallback:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DESIGN TIME — ahimsa-ai (AIA v1.1)                                          │
│  • Dual-axis scoring (Himsa risk / Sarvodaya benefit)                        │
│  • Five red-line refusals                                                    │
│  • Compiles approved project → signed boundary manifest                      │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ signed manifest
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — WASM DETERMINISTIC PRE-FLIGHT (sub-ms budget)                     │
│  Powered by arniko scanner adapters + native deterministic checks            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │ Regex       │  │ Schema      │  │ Rate limit  │  │ Arniko LLM      │   │
│  │ allowlists  │  │ validation  │  │ (token      │  │ scanners:       │   │
│  │ + blocklists│  │ (manifest   │  │ bucket per  │  │ DeepTeam/Garak/ │   │
│  │             │  │ derived)    │  │ agent/tool) │  │ Promptfoo/      │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │ LlmGuard/…    │   │
│         │                │                │         └────────┬────────┘   │
│         └────────────────┴────────────────┴─────────────────┘            │
│                           WASM sandbox (Wasmtime/Wasmer)                   │
│                           • no network, read-only manifest, no FS write   │
│                           • fast-path ALLOW / hard REFUSE                 │
│                           • sub-millisecond budget (MAX 500 µs)           │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ pass → escalate
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — SLM SEMANTIC JUDGES (intent / nuance / injection)                 │
│  • Small language model runs only when Layer 1 + LOKAHI 15 rules pass       │
│  • Input: tool name, class (from signed manifest — never agent), args        │
│  • Output: ALLOW / ESCALATE / REFUSE + charter reason code + rationale      │
│  • Combination: stricter of LOKAHI verdict and SLM verdict wins              │
│  • SLM failure/unknown → ESCALATE (fail closed)                              │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ needs consent →
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — MULTI-PARTY THRESHOLD CONSENT                                     │
│  • m-of-n Ed25519 signatures (independent keys; FROST planned)               │
│  • m-of-n independent validators sign exact action digest                    │
│  • Per-tool (m, n) configurable in signed manifest (default 2-of-3)          │
│  • Silence from ANY required party = automatic veto                          │
│  • Two dissenting validators freeze execution immediately                    │
│  • Agent cannot self-approve; operator conflict-of-interest blocked          │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ quorum met →
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — EXECUTION + RESPONSE INSPECTION                                   │
│  • MCP client relays allowed call to tool                                    │
│  • Tool RESPONSE inspected for indirect prompt injection                     │
│  • Tamper-evident hash-chained ledger records every decision                 │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ edge failure / timeout / exception             │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FAIL-SECURE STATE                                                           │
│  • Drop execution privileges immediately                                       │
│  • Revert to secure default (no action)                                        │
│  • Fallback to human WebXR teleoperation for manual oversight                │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Layer 1: WASM Deterministic Pre-Flight

**Goal:** Block obvious threats within sub-millisecond budgets, before any SLM is invoked.

### Components

**WASM Host (Rust + Wasmtime/Wasmer)**
- Host module written in Rust, compiled to WASM, executed via Wasmtime with Wasi-capability restrictions (no network, no filesystem beyond read-only manifest, no environment access).
- Each deterministic check is a pure function: input = `(tool, args, manifest_context)`, output = `(ALLOW / REFUSE / ESCALATE + reason)`.
- Sandboxed so a malicious or buggy check cannot corrupt the host process.

**Deterministic Checks**
| Check | What it does | Budget |
|-------|-------------|--------|
| Regex allowlist / blocklist | Tool name and argument values must match allowlisted patterns; blocklist for known injection strings | < 100 µs |
| Schema validation | Args validated against the signed manifest's per-tool JSON Schema | < 150 µs |
| Manifest integrity | Signature (HMAC v1 / Ed25519 v2) and expiry pre-check | < 150 µs |
| Rate limiting | Token bucket per (agent, tool) — burst + sustained caps | < 50 µs |
| Arniko scanner findings | DeepTeam, Garak, Promptfoo, LlmGuard, Rebuff, Purple Llama | < 50 µs |

**Fast-Path Decision**
- If any check returns REFUSE → hard refuse immediately, record in ledger, no SLM call.
- If all checks pass → escalate to Layer 2.
- Unknown tool or argument not in manifest → REFUSE.

### Deliverable
- `tools/wasm_preflight/` — Rust crate (`wasm32-unknown-unknown` target), 18 KB release WASM module.
- `tools/wasm_bridge.py` — Python Wasmtime bridge.
- CLI: `--preflight-wasm <path>` and `--require-preflight`.

## Layer 2: SLM Semantic Judges

**Goal:** Evaluate intent, semantic nuance, and complex prompt injections — only when Layer 1 passes.

**Design**
- Small language model (0.5B–1B params, quantized, CPU/edge) — much lighter than a frontier LLM.
- Input: tool name, tool class (read/write/irreversible) from the *signed manifest* (never from the agent), argument values, action digest.
- Output: `ALLOW` / `ESCALATE` / `REFUSE` + reason code (from `docs/decisions/CHARTER.md`) + one-line rationale.
- **Combination rule:** The stricter of LOKAHI's deterministic decision and the SLM's verdict wins. SLM can turn ALLOW into ESCALATE or REFUSE, but never the reverse.

**Order of operations (fixed):**
1. LOKAHI 15-rule engine decides first. If REFUSE or ESCALATE, stands — SLM not asked.
2. Charter rules in plain code (timed-out approval → REFUSE; data residency violation → REFUSE; irreversible → needs people).
3. SLM judge runs last; its answer is combined by taking the stricter verdict.
4. If SLM fails or returns unknown → ESCALATE (fail closed).

### Deliverable
- `tools/decisions/slm_judge.py` — `SLMJudge` class (ONNX/GGML/MLX backends + pure-Python fallback).
- `tools/decisions/charter.py` — plain-code charter rules (`hard_rules()`).
- `tools/decisions/tier.py` — `combine()` (stricter-of-two).
- CLI: `--slm-model <path>` and `--slm-timeout <seconds>`.

## Layer 3: Multi-Party Threshold Consent

**Goal:** No agent or physical actuator acts without cryptographic signatures from multiple independent validators. Silence = veto.

**Cryptography**
- **m-of-n Ed25519 signatures** — each validator holds an independent Ed25519 key pair and signs the action digest individually; the gate holds only validator *public* keys and counts how many distinct valid signatures it received. No single party can forge an approval, but this is **not** cryptographic threshold signing: the gate sees every individual signature and there is no key splitting. RFC 9591 (FROST) two-round threshold signing remains planned.
- Per-tool `(m, n)` threshold in the signed manifest (default 2-of-3).
- Action digest = exact tool + argument values; signatures bound to that exact call.

**Silence = veto (ahimsa timeout)**
- Consent window has a deadline; fewer than `m` signatures before close → **REFUSED** (`non_response`), never allowed.
- Two dissenting validators freeze execution immediately regardless of others.

**Conflict-of-interest protections**
- The agent cannot approve its own calls.
- The operator running the agent cannot approve the agent's calls.
- Each person counts once, however many agents they run.
- Emergency declarant cannot vote on their own scope.

### Deliverable
- `tools/lokahi_consent.py` — `ConsentFramework` class.
- `tools/lokahi_sign.py` — Ed25519 key share generation (`keygen --threshold m --n n`).
- `tools/lokahi_approve.py` — threshold signing from each validator's device.
- CLI: `--validator-keys <path>` and `--require-consent-framework`.

## Layer 4: Execution + Response Inspection

Allowed calls are relayed to the tool server via the MCP client. Tool responses are inspected for indirect prompt injection / hidden data. Every decision is recorded in the tamper-evident hash-chained ledger.

## Fail-Secure State & WebXR Teleoperation

**Goal:** Edge failures never result in unauthorized action. Default to secure state; escalate to human oversight.

**Fail-secure behavior:**
| Failure mode | Action |
|-------------|--------|
| WASM sandbox crash / panic | Drop privileges, refuse call, log to ledger |
| SLM judge timeout / crash / unknown output | ESCALATE → require human consent or refuse |
| Consent window expires without quorum | REFUSE (`non_response`) — silence is veto |
| Manifest unsigned / expired / unverifiable | REFUSE — fail closed |
| Validator network unavailable | Cannot gather signatures → REFUSE |
| Uncaught exception anywhere | Drop execution privileges → secure default |

**WebXR teleoperation fallback**
When the pipeline cannot reach a secure automated decision:
1. **Drop execution privileges** — the agent/actuator is locked; no further tool calls may be issued.
2. **Trigger WebXR teleoperation session** — a human operator dons a WebXR headset and takes manual control of the actuator/agent through a live, audited telepresence stream.
3. All teleoperation actions are logged to the same tamper-evident ledger for post-hoc audit.
4. The system remains in secure state until a human explicitly re-arms it through the WebXR console with a cryptographic re-arm signature.

### Deliverable
- `tools/lokahi_failsecure.py` — `FailSecureManager` class.
- `tools/lokahi_webxr.py` — `WebXRTeleopBroker` class.
- CLI: `--webxr-endpoint <url>`.

## Component Stack & Layered Decomposition

| Layer | Production Component | Standard / Foundation | Implementation |
|-------|---------------------|----------------------|----------------|
| Canonical Serialization | RFC 8785 (JCS) | `rfc8785` (Python), `canonical-json` (Rust) | Deterministic byte ordering |
| Cryptographic Signatures | Ed25519 | `cryptography` (Python) | Asymmetric envelope signing |
| Node Identity & Attestation | W3C DID, SPIFFE/SPIRE | `did:key`, `did:web`, SPIFFE x509 SVIDs | Workload & enclave manifest binding |
| Disjoint Consent | Ed25519 (RFC 8032) | `cryptography` Ed25519 sign/verify | m-of-n independent validator signatures |
| Policy Engine | Deterministic Wasm sandbox | Wasmtime | 15-rule normative decision kernel |
| Interception & Gateway | Model Context Protocol (MCP) | MCP JSON-RPC 2.0 | Stdio gateway adapter |
| Transparency Log | Hash-chained ledger | SHA-256 | Append-only manifest & decision receipts |
| Telemetry & Observability | OpenTelemetry, OCSF | OpenTelemetry SDK | Distributed trace spans & audit events |
| Kernel Containment | Linux Namespaces / cgroups | eBPF (Tetragon), Firecracker, gVisor | Runtime syscall & socket boundary |
| Fail-Secure State | Privilege drop + WebXR | WebXR Device API | Human teleoperation fallback |

## Core Architectural Challenges & Mitigations

### 1. Attested Disjointness vs. Asserted Independence
Operator identity is represented as an input string. In production, cryptographic and institutional proof of disjointness is required:
- **Disjointness Graph:** Gateways construct and score a dependency graph spanning legal registration, root-key custody, infrastructure providers, and geographic jurisdiction.
- **Sybil Resistance:** Quorums require verified distinct physical operators.

### 2. Split-View Attack Mitigation
A compromised agent could present permissive manifests to local peers while serving restrictive manifests to auditors:
- **Witness-Cosigned Transparency Log:** Manifest hashes committed to an append-only transparency log prior to activation.
- **Consistency Proofs:** Independent witness nodes cosign the log head.

### 3. High-Assurance Revocation Propagation
Expiry timestamps alone are insufficient for real-time risk mitigation:
- **Short TTL Ephemeral Grants:** Fast-path tool invocations utilize short-lived capability tickets.
- **Gossip Revocation Bus:** Revocation vectors broadcast across peer gateways within a bounded sync window.

### 4. Liveness vs. Safety Invariants
If a steward becomes unresponsive, the system must not stall vital operations nor silently bypass safeguards:
- **Fail-Closed Default (The Ahimsa Timeout):** When a required consent quorum fails to respond within the window, Rule 14 triggers deterministic refusal. Silence is never acquiescence.
- **Audited Emergency Escalations:** Quorum-approved emergency overrides require cryptographic recording with strict rate-limiting.

### 5. Decoupling Synchronous Execution from Asynchronous Human Deliberation
Tool calls execute within 50–200 ms. Human stewards cannot deliberate at machine speed:
- **Fast-Path Verification:** Compliant actions matching pre-signed boundary manifests execute locally in sub-millisecond time.
- **Asynchronous Human Queuing:** Actions requiring multi-party human consent enter an asynchronous durable queue governed by explicit SLOs.

## Deployment Topologies

### Local Edge Node (Air-Gapped / Workstation)
A standalone self-contained binary with embedded SQLite storage, Ed25519 keys in OS native credential stores, local MCP proxy, and complete operational autonomy.

### Federated Multi-Node Cluster
Dedicated gateway instances protecting service boundaries, shared witness log synchronizing manifest registrations, P2P threshold signing rings, hardware-backed key isolation.

### Global Scaled Infrastructure
Regionally partitioned transparency logs with cross-realm Merkle consistency proofs, horizontally autoscaled stateless inspection gateways, deterministic cross-border compliance routing.

## Protocol Implementation Roadmap

1. **Wasm Decision Kernel:** Compile the 15-rule normative engine into a standalone WASM module with fuzzing and formal verification.
2. **Threshold Ring (FROST) Integration:** Replace independent Ed25519 signatures with RFC 9591 two-round FROST threshold signatures so an aggregating gate never sees individual signatures and a colluding minority cannot reconstruct key shares.
3. **Transparency Witness Federation:** Deploy immutable Rekor-compatible log instances with Merkle tree consistency checks.
4. **External Cryptographic Audit:** Formal threat modeling and independent penetration testing.
