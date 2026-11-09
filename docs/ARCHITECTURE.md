# LOKAHI Production Architecture: Scaling from Reference Gateway to Distributed Multi-Node Enforcement

LOKAHI provides boundary-enforcement and disjoint-consent infrastructure for autonomous agentic collectives. This document details the technical architecture, security guarantees, and engineering roadmap for transitioning the executable reference implementation into a distributed, multi-node enforcement system.

## Architectural Foundation

The reference implementation (`tools/lokahi.py`, v0.1.2) provides an executable, formally verifiable specification: zero-dependency RFC 8785 canonical JSON formatting, deterministic evaluation of 15 ordered safety rules, and an append-only, SHA-256 hash-chained decision ledger.

Moving from single-process gateway execution to distributed collective enforcement requires six production-grade cryptographic and systems guarantees:

1. **Verifiable Identity:** Each participating node cryptographically attests identity using sovereign keys held in secure enclaves (TPM / HSM / Apple Secure Enclave), with key-rotation protocols that preserve historical auditability.
2. **Unforgeable Disjoint Consent:** Multilateral authorization relies on threshold signatures where no subset of colluding operators or compromised coordinators can reconstruct key shares or fabricate consent.
3. **Consensus & State Agreement:** Every network participant observes an identical, witness-cosigned capability manifest for any given node at any point in time, guaranteeing immediate detection of split-view attacks.
4. **Bounded Revocation Propagation:** Revocations propagate deterministically across all active gateways within a bounded maximum time delta ($T_{\text{max}}$).
5. **Strict Decision Budgets:** Sub-millisecond evaluation latency on the fast-path (machine evaluation of signed capability bounds), ensuring zero pipeline stalls during high-frequency MCP tool dispatch. Human deliberation is strictly asynchronous and decoupled from real-time execution pipelines.
6. **Cryptographic Non-Repudiation:** Every access grant, override, escalation, and denial produces an immutable, exportable, witness-attested cryptographic audit receipt.

## Component Stack & Layered Decomposition

To maximize security assurance and minimize novel attack surfaces, LOKAHI follows an 80/20 design rule: approximately 80% of the architecture leverages battle-tested, audited open standards and cryptographic primitives, while the custom 20% strictly defines the domain protocol semantics (manifest schemas, decision ordering, disjointness graphs, and ledger proofs).

| Layer | Production Component | Standard / Foundation | Protocol Implementation |
|---|---|---|---|
| **Canonical Serialization** | RFC 8785 (JCS) | `rfc8785` (Python), `canonical-json` (Rust) | Deterministic byte ordering |
| **Cryptographic Signatures** | Ed25519 / Ed448 | `libsodium`, `ed25519-dalek` | Asymmetric envelope signing |
| **Node Identity & Attestation** | W3C DID, SPIFFE/SPIRE | `did:key`, `did:web`, SPIFFE x509 SVIDs | Workload & enclave manifest binding |
| **Disjoint Threshold Consent** | IETF RFC 9591 (FROST) | Schnorr threshold signatures & DKG | Multi-operator disjoint ring policy |
| **Policy Engine** | Deterministic Wasm sandbox | Wasmtime, OPA/Rego, AWS Cedar | 15-rule normative decision kernel |
| **Interception & Gateway** | Model Context Protocol (MCP) | MCP JSON-RPC 2.0, Envoy `ext_authz` | Stdio/SSE gateway adapter |
| **Transparency Log** | Merkle log with witnesses | Sigstore Rekor, Trillian, Tessera | Append-only manifest & decision receipts |
| **Inter-Node Transport** | DIDComm v2 / A2A / gRPC | Decentralized identity messaging | Asynchronous consent request/response |
| **State Storage** | Embedded SQLite / PostgreSQL | WAL-mode SQLite, encrypted at rest | Schema & hash-chained ledger |
| **Telemetry & Observability** | OpenTelemetry, OCSF | OpenTelemetry SDK / OTLP exporter | Distributed trace spans & audit events |
| **Kernel Containment** | Linux Namespaces / cgroups | eBPF (Tetragon), Firecracker, gVisor | Runtime syscall & socket boundary |

## Core Architectural Challenges & Mitigations

### 1. Attested Disjointness vs. Asserted Independence
In basic implementations, operator identity is represented as an input string. In production environments, sovereign safety requires cryptographic and institutional proof of disjointness:
- **Disjointness Graph:** Gateways construct and score a dependency graph spanning legal registration, root-key custody, infrastructure providers (AWS, GCP, bare metal), and geographic jurisdiction.
- **Sybil Resistance:** Quorums require verified distinct physical operators. If two signing keys originate from identical ASN clusters, shared parent organizations, or co-located Kubernetes clusters, they collapse to a single vote under Rule 11.

### 2. Split-View Attack Mitigation
A compromised or malicious agent could attempt to present permissive boundary manifests to local peers while serving restrictive manifests to oversight auditors.
- **Witness-Cosigned Transparency Log:** Manifest hashes must be committed to an append-only transparency log prior to activation.
- **Consistency Proofs:** Independent witness nodes cosign the log head. Any divergence between what two peers observe produces an irrefutable cryptographic proof of misbehavior.

### 3. High-Assurance Revocation Propagation
Expiry timestamps alone are insufficient for real-time risk mitigation. When an agent exhibits drift or an operator revokes consent:
- **Short TTL Ephemeral Grants:** Fast-path tool invocations utilize short-lived capability tickets (e.g., 60 seconds).
- **Gossip Revocation Bus:** Revocation vectors broadcast across peer gateways over an authenticated gossip protocol. A revoked ticket is permanently blocked across all nodes within the network sync window.

### 4. Liveness vs. Safety Invariants
If an external steward or human reviewer becomes unresponsive, the system must not stall vital operations arbitrarily, nor may it silently bypass safeguards:
- **Fail-Closed Default (The Ahimsa Timeout):** When a required consent quorum fails to respond within the designated execution window, Rule 14 triggers deterministic refusal. Silence is never interpreted as acquiescence.
- **Audited Emergency Escalations:** Quorum-approved emergency overrides require cryptographic recording with strict rate-limiting (Rule 6) and post-incident forensic isolation.

### 5. Decoupling Synchronous Execution from Asynchronous Human Deliberation
Tool calls in production LLM pipelines execute within 50–200 ms. Human stewards cannot deliberate at machine speed:
- **Fast-Path Verification:** Compliant actions matching pre-signed, active boundary manifests execute locally in sub-millisecond time.
- **Asynchronous Human Queuing:** Actions requiring multi-party human consent enter an asynchronous durable queue governed by explicit service-level objectives (SLOs), cleanly decoupling interactive agent operations from operator oversight.

## Deployment Topologies

### Local Edge Node (Air-Gapped / Workstation)
A standalone self-contained binary:
- Embedded SQLite storage with write-ahead logging.
- Ed25519 signing keys stored in OS native credential stores (Windows DPAPI, macOS Keychain, Linux Secret Service).
- Local MCP proxy filtering tool calls between client host and language model.
- Complete operational autonomy with zero internet dependencies.

### Federated Multi-Node Cluster
For multi-agent organizations and institutional deployments:
- Dedicated gateway instances protecting service boundaries.
- Shared witness log synchronizing manifest registrations.
- P2P threshold signing rings coordinating inter-agent handoffs.
- Hardware-backed key isolation across disjoint cloud and bare-metal environments.

### Global Scaled Infrastructure
For cross-jurisdictional Digital Public Infrastructure (DPI):
- Regionally partitioned transparency logs with cross-realm Merkle consistency proofs.
- Horizontally autoscaled stateless inspection gateways caching validated manifests at edge locations.
- Deterministic cross-border compliance routing validating data residency and sovereign jurisdiction rules.

## Protocol Implementation Roadmap

1. **Wasm Decision Kernel:** Compile the 15-rule normative engine into a standalone, memory-safe WebAssembly module with comprehensive fuzzing and formal verification.
2. **Threshold Ring (FROST) Integration:** Transition the consent evaluation layer from HMAC reference tokens to RFC 9591 Schnorr threshold signatures.
3. **Transparency Witness Federation:** Deploy immutable Rekor-compatible log instances with verifiable Merkle tree consistency checks.
4. **External Cryptographic Audit:** Conduct formal threat modeling and independent penetration testing across gateway protocol surfaces.
