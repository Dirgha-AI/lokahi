# From toy to system: what LOKAHI actually needs

Written 28 October 2026, after `tools/lokahi.py` was working. This is the engineering answer to "is this real".

## Verdict on what exists

`tools/lokahi.py` (v0.1.2) is a specification made executable. About 250 lines, no dependencies, 32 unit tests and a 50-scenario deterministic benchmark harness. It proves that the decision order can be written down unambiguously and that the ledger detects an edit.

It is not a system. It has:

- no transport (manifests are Python objects, not messages)
- no real signatures (HMAC means the verifier holds the signing key)
- no persistence (a list in memory)
- no concurrency (one decision at a time, synchronously)
- no discovery (you already know the other node's manifest)
- no revocation (expiry only)
- no threat model, no audit, no adversarial testing

The README states that list before any claim. That is the point of writing it down.

## What "real" means here

Six properties, none of which the toy has:

1. **Verifiable identity.** A node proves who it is with a key nobody else holds, and can rotate that key without invalidating history.
2. **Unforgeable consent.** Three signatures that cannot be assembled by two parties, and cannot be collected by a coordinator who sees the shares.
3. **Agreement.** Every party sees the same manifest for the same node at the same time, or the difference is detectable.
4. **Revocation that propagates.** A revoked manifest stops working everywhere within a bounded time.
5. **A decision budget.** Milliseconds on the compliant path, because tool calls cannot wait. Human deliberation is asynchronous and never on that path.
6. **Evidence.** Every decision is attributable, exportable, and checkable by a third party after the fact.

## The stack, layer by layer

The rule for every layer: adopt the audited open-source component, write only the part that does not exist.

| Layer | What production uses | Open source to build on | What we write |
|---|---|---|---|
| Canonical form | JCS, RFC 8785 | `rfc8785` (Python), `canonical-json` (Rust), `json-canonicalize` | nothing |
| Signatures | Ed25519 | `libsodium`, `ed25519-dalek`, `@noble/ed25519` | nothing |
| Identity | `did:key`, `did:web`, SPIFFE | SPIRE/SPIFFE, `didkit` | the manifest binding |
| Threshold consent | FROST (t-of-n Schnorr, RFC 9591), DKG | Zcash Foundation `frost`, `tss-lib` | the ring policy |
| Policy evaluation | deterministic rules, Wasm sandbox | OPA/Rego, Cedar, `wasmtime` | the boundary rule set |
| Tool interception | MCP gateway, ext_authz | MCP, Envoy `ext_authz`, OPA sidecar | the gateway adapter |
| Transparency | Merkle log with witnesses | Sigstore Rekor, Trillian, Tessera | manifest entry format |
| Messaging | DIDComm v2, A2A, gRPC | `didcomm-rust`, A2A SDK | consent request/response |
| Storage | Postgres, SQLite at the edge | Postgres, SQLite | schema |
| Telemetry | OpenTelemetry, OCSF | OTel SDK | decision spans |
| Sandboxing (later) | Firecracker, gVisor, eBPF | Tetragon, Cilium | policy hook |

Roughly 80% of the system is existing, audited software. The 20% we write is the protocol: manifest semantics, decision order, ring policy, and the ledger entry format. That ratio is the argument for feasibility.

## The five hard problems

These are what separate a demo from a system.

**1. Disjointness cannot be asserted.** My code takes "three operators with disjoint control" as a field. In reality you need evidence: distinct legal entities, distinct key custody, no shared administrator, ideally distinct jurisdictions. Real designs build a disjointness graph from attested facts (registration, key custody, infrastructure ownership) and score it. This is the central research question of the project, it is partly legal rather than technical, and the design is stronger for naming it as the open problem rather than claiming it solved.

**2. Split view.** A malicious node can show one manifest to one peer and a different one to another. The fix is a transparency log with witness cosigning, the pattern Certificate Transparency and Go's checksum database already use: each node publishes its manifest's hash to a log, and independent witnesses cosign the log head. Without this, manifests are promises.

**3. Revocation.** Expiry is not revocation. A revoked key must stop working at nodes that already cached it. Short TTLs plus a log plus gossip is the standard answer; the bound on propagation delay has to be stated, because it is a security parameter.

**4. Liveness against safety.** An unreachable steward blocks action. At three nodes that is tolerable; at three hundred it is an outage. Quorum substitution must not become a way to manufacture consent, which is why it belongs in the spec with its own audit trail rather than in an operator's judgement.

**5. Human latency.** A ring of humans cannot answer in 150 milliseconds. The latency target applies only to the compliant path, which is machine-decided. Escalation is asynchronous and must be designed as a queue with SLAs, not as a wait.

## Local, then global

**Local (one node, one machine).** A single binary: embedded SQLite, Ed25519 keys in the OS keystore, the MCP gateway in front of the agent's tools, the ledger as an append-only file with periodic checkpoints. This is what a person runs on a laptop or a lab server. It must work with no network, because a boundary that depends on the internet is not a boundary.

**Lab (three to ten nodes).** One gateway per node, a shared transparency log, manifests fetched and cached with a TTL, consent requests over DIDComm or A2A, FROST shares held by operators on separate machines. This is the shape of a three-to-ten node pilot across independent operators.

**Global (thousands of nodes).** The log becomes the bottleneck and the attack surface. Partition by jurisdiction and community, with cross-log consistency proofs; cache manifests at the edge with short TTLs; keep the decision path stateless so gateways scale horizontally; make the ring a matter of presence, not directory. Two things break first: revocation propagation and witness coordination. Both are operational problems with known patterns from certificate infrastructure, and both are out of scope for the reference implementation.

## Effort, honestly

| Work | Person-months | Confidence |
|---|---|---|
| Spec, conformance suite, test vectors | 3 | high |
| Manifest plus identity plus rotation | 2 | high |
| Decision engine hardened (property tests, fuzzing, Wasm) | 3 | medium |
| FROST ring and DKG integration | 4 | medium |
| Transparency log plus witnesses | 3 | medium |
| MCP gateway integration | 2 | high |
| Three-node pilot operations | 3 | medium |
| Security review | 1 | high |

About 21 person-months for a credible reference implementation, or roughly 12 months for one person working with agents on the parts that parallelise, if the ring ships with attestations rather than FROST.

A production system with an audit and an operations story is three to four engineers plus a cryptographer for a year.

## Design corrections

1. **Arniko is not the interception point.** It is a scanner. The interceptor is an MCP gateway that calls the same rule engine.
2. **The latency target applies to the compliant path only.** Escalation is queued, with an SLA rather than a p95.
3. **Disjointness is the research question.** Name it as unsolved rather than implying it is solved.
4. **Keep the ratio visible.** Roughly 80% of the stack is audited open source; the protocol layer, the pilot, and an audit of the composition are the new work.

The reference implementation specifies and demonstrates the part that does not exist, on top of parts that do.
