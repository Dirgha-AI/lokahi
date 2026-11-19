# LOKAHI — Roadmap to Open-Source SOTA

**Vision:** LOKAHI is the reference open-source enforcement runtime for authority
over consequential agent actions: deterministic pre-flight, multi-party disjoint
consent, physical safety envelopes, tamper-evident evidence, and fail-secure
human handoff — deployable air-gapped, integrable with any MCP host, and good
enough that a CISO, a safety engineer, and an auditor all accept it.

**Non-negotiable differentiators** (the reason to exist, not the reason to buy):
1. Disjoint m-of-n consent with dissent freeze and conflict-of-interest exclusion.
2. Kinetic Ahimsa physical envelopes with safe mechanical hold.
3. Design-time assessment → runtime manifest compiler (+ ahimsa-ai).
4. Fail-secure lock + human teleoperation handoff.
5. Auditor-grade evidence pack, offline-verifiable.

**Parity is table stakes.** Everything else in this roadmap exists so we can
pass an enterprise RFP *and* keep the five differentiators.

---

## 1. Competitive parity matrix

Legend: ✅ ships · ◐ partial · ❌ absent · ★ LOKAHI-unique

| Capability | Permit | Docker AG | SecureAuth | TrueFoundry | EnforceAuth | ContextForge | Kong | agentgw | **LOKAHI target** |
|---|---|---|---|---|---|---|---|---|---|
| stdio MCP | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ now |
| Streamable HTTP / stateless MCP 2026-07-28 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **R1** |
| OAuth 2.1 resource server (RFC 9728 PRM, PKCE, RFC 8707) | ✅ | ◐ | ✅ | ◐ | ✅ | ✅ | ✅ | ◐ | **R1** |
| iss validation (RFC 9207), CIMD client registration | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ✅ | ◐ | **R1** |
| No token passthrough / token exchange | ◐ | ◐ | ◐ | ◐ | ✅ | ◐ | ✅ | ◐ | **R1** |
| Per-client consent (confused deputy) | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | **R1** |
| Tool ACL per consumer / team | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | **R1** |
| Registry / virtual servers | ◐ | ✅ | ❌ | ✅ | ❌ | ✅ | ◐ | ◐ | **R2** |
| Tool-description pinning / rug-pull defence | ◐ | ✅ | ◐ | ◐ | ◐ | ✅ | ◐ | ◐ | **R1** |
| Credential injection / vault (agent holds no upstream secret) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **R1** |
| Human-in-the-loop approval | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ now |
| Argument-bound approval | ✅ | ◐ | ◐ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ now |
| Timeout always rejects | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ now |
| **Disjoint m-of-n quorum** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| **Dissent freeze** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| **Physical envelope + safe hold** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| **Fail-secure lock + teleop handoff** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| Policy-as-code interop (OPA / Cedar / CEL) | ◐ | ✅ Cedar | ◐ | ◐ | ✅ OPA | ✅ OPA | ◐ | ✅ CEL | **R1** |
| OTel traces + metrics | ◐ | ✅ | ◐ | ✅ | ◐ | ✅ | ✅ | ✅ | **R1** |
| SIEM export (Splunk/Sentinel/Elastic) | ◐ | ◐ | ◐ | ✅ | ◐ | ✅ | ✅ | ✅ | **R1** |
| Approval inbox (email / Slack / passkey) | ✅ | ✅ | ✅ | ✅ | ◐ | ❌ | ❌ | ❌ | **R1** |
| Admin console | ✅ | ✅ | ✅ | ✅ | ◐ | ✅ | ✅ | ◐ | **R1** |
| Escalation / on-call routing | ◐ | ◐ | ◐ | ◐ | ◐ | ❌ | ❌ | ❌ | **R2** |
| Immutable transparency log + witness | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **R2** |
| Multi-tenancy + HA | ✅ | ◐ | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | **R2** |
| Policy simulation / replay over history | ❌ | ❌ | ❌ | ❌ | ◐ | ❌ | ❌ | ❌ | **R2** |
| SDKs (Python / TypeScript / Go) | ◐ | ◐ | ✅ | ✅ | ◐ | ✅ | ✅ | ◐ | **R2** |
| Air-gapped deployment | ◐ | ◐ | ◐ | ❌ | ◐ | ✅ | ❌ | ◐ | ✅ now |
| Fuzz + formal verification of kernel | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **R1–R2** |
| FROST threshold signatures | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **R3** |
| Disjointness attestation graph | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **R3** |
| MCP gateway conformance suite | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **R1** ★ leadership |
| Evidence pack for auditors | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | **R1** ★ |
| Physics/robotics integration (ROS 2, ISO 10218) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **R3** ★ |

**Reading:** we already lead on four safety capabilities nobody sells. We are
behind on transport, identity, observability, operations and UX. Parity work is
R1–R2; depth work is R3; category leadership is R4.

---

## 2. Workstreams

| ID | Workstream | Owner type | Primary risk retired |
|---|---|---|---|
| W1 | Protocol & transport (MCP 2026-07-28, OAuth 2.1, HTTP) | Backend | Cannot front real MCP servers |
| W2 | Identity & authority (OIDC approvers, RBAC, tenants) | Backend | Approver identity is not real |
| W3 | Enforcement kernel (Rust host, WASM, fuzz, formal) | Systems | Latency and assurance |
| W4 | Consent crypto (FROST, DKG, rotation, attestation) | Crypto | "Threshold" is only counting |
| W5 | Physical safety (ROS 2, ISO 10218/15066, PLC interlock) | Embedded/safety | No path to robotics buyers |
| W6 | Evidence & ledger (transparency log, witness, evidence pack) | Backend | Auditor does not accept it |
| W7 | Observability & ops (OTel, SIEM, HA, scaling, chaos) | SRE | Cannot run in production |
| W8 | Experience (console, approval inbox, policy studio, docs) | Product/frontend | Nobody can use it |
| W9 | Integrations & ecosystem (OPA/Cedar, Arniko, SIEM, SDKs, Helm) | Backend/DevRel | Island product |
| W10 | Assurance & community (SOC 2, pen test, corpus, standards) | Security/DevRel | Nobody trusts it |

---

## 3. Phases

### R0 — Stabilise (weeks 0–2) · v1.0.1
Ship the audit remediation and stop the bleeding.

Exit criteria:
- C1–C4, H1–H3, M1–M8 closed with regression tests (done)
- README corrected; SLM tier marked experimental; safety claims limited to what is tested
- CI: Python matrix 3.10–3.12, Rust build+test, packaging smoke, CLI e2e
- Release v1.0.1 tagged

### R1 — Parity (weeks 3–14) · v1.1 "Enterprise-ready"
The release that makes us pass an RFP without apologising.

**W1 Protocol & transport**
- MCP Streamable HTTP transport (stateless, 2026-07-28), keep stdio
- OAuth 2.1 resource server: RFC 9728 PRM, PKCE, RFC 8707 resource indicators, RFC 9207 `iss` validation
- Client ID Metadata Documents; per-client consent for static client IDs
- No token passthrough; RFC 8693 token exchange for upstream
- MCP `_meta` approval-status convention; `input_required` interrupt support
- Gateway conformance test suite (publish as standalone package)

**W2 Identity & authority**
- OIDC-authenticated approvers; WebAuthn/passkey confirmation per decision
- RBAC: steward / operator / auditor roles; per-tool authority grants
- Tool ACLs per consumer and team
- Approver directory + group quorum (e.g. "any 2 of the water board")

**W3 Kernel**
- Rust host binary (`lokahi-gateway-rs`) with embedded WASM kernel: p99 < 1 ms decision
- Fuzz harness (cargo-fuzz) + differential tests vs Python oracle
- Property tests for merge precedence (consent never relaxes a safety refusal)

**W6 Evidence**
- `lokahi evidence <digest>` — offline-verifiable bundle (JSON + PDF + signatures + chain proof)
- Ledger export to S3 Object Lock (opt-in) and append-only file
- Auditor verification tool that needs no LOKAHI install

**W7 Observability**
- OTel traces, metrics, structured logs; OCSF event schema
- SIEM exporters: Splunk HEC, Azure Sentinel, Elastic, generic webhook
- Prometheus `/metrics`; health/readiness endpoints

**W8 Experience**
- Approval inbox: email (signed link), Slack/Teams app, passkey confirm
- Console MVP: pending approvals, decision detail, ledger browse, policy view
- Docs site: quickstart (<5 min), concepts, policy reference, threat model

**W9 Integrations**
- OPA bundle export (Rego) and Cedar policy export; import decision results
- Arniko adapter: findings feed Layer 1 with signed provenance
- Python SDK, TypeScript SDK, CLI
- Docker image, docker-compose, Helm chart, GitHub Action
- Three sector policy packs: utility, hospital, editorial

**W1/W3 supply chain**
- Tool-description pinning: hash each tool schema into the manifest; drift → refuse
- Server registry with signed metadata (in-toto/SLSA attestations, optional)

Exit criteria:
- Pass a self-authored 60-point enterprise RFP checklist (published)
- p99 < 1 ms in Rust host on a 2019 laptop; p99 API < 25 ms
- 10,000-decision soak, zero chain breaks, zero crashes
- MCP conformance suite green for stdio + HTTP
- 200+ tests

### R2 — Operate (weeks 15–28) · v1.2 "Production"
The release an SRE will run and an auditor will accept.

**W6 Evidence & ledger**
- Merkle tree ledger with inclusion proofs; witness cosigning (2 witnesses)
- Optional Rekor/Trillian backend
- Key rotation with historical verifiability (key fingerprint in chain)
- Revocation propagation with bounded TTL

**W7 Ops**
- Multi-tenancy: per-tenant keys, policies, ledgers, quotas
- HA: stateless decision service + Postgres/SQLite-WAL ledger; single-writer
  election or partitioned writers; backup/restore runbook
- Distributed rate limiting (Redis)
- Chaos/fault-injection suite (kill WASM, drop ledger, sever IdP, slow consent)
- Load test published: decisions/sec/core, tail latency

**W8 Experience**
- Policy studio: manifest authoring, validation, dry-run, diff, versioning
- Simulation/replay: "what would this policy have decided over last 90 days?"
- Escalation routing + on-call schedule integration (PagerDuty-style webhook)
- Approval SLA dashboard

**W9 Integrations**
- Registry/virtual servers (bundle tools into curated catalogues)
- Credential injection: Vault, AWS/GCP/Azure secret managers, env
- Go SDK; Terraform provider
- SIEM/SOAR playbooks

**W10 Assurance**
- SOC 2 Type I readiness → audit start
- External crypto review #1 (Ed25519 usage, digest binding, chain)
- Pen test #1
- Published red-team corpus v2 with MCP-specific attacks

Exit criteria:
- 100k-decision soak, no leak, no chain break
- Two independent deployments (one air-gapped) in production-like use
- SOC 2 Type I report
- Published benchmark + pen-test summary

### R3 — Differentiate (weeks 29–44) · v2.0 "Authority"
Turn the unique capabilities from claims into products.

**W4 Consent crypto**
- RFC 9591 FROST two-round threshold signatures + DKG
- Group key per quorum; gate never sees individual signatures
- Disjointness attestation graph: legal registration, key custody, infra, geography
- Sybil resistance: collapse quorums that share custody/ASN/org
- Emergency quorum substitution with auditable path (no silence-as-consent)

**W5 Physical safety**
- ROS 2 action-server interlock node (C++/Rust)
- ISO 10218-1:2025 / TS 15066 mapping: force, pressure, speed-and-separation
- PLC-safe output driver (OPC UA / Modbus) for mechanical hold
- Certified reference architecture with one integrator
- Safety case template: hazard → envelope → interlock → evidence

**W8/W5 Teleoperation**
- WebXR console: live teleop session, pose stream, action log, re-arm with passkey
- Dual-control re-arm (two humans, disjoint)

**W10 Assurance & standards**
- External crypto review #2 covering FROST + attestation
- Publish red-team corpus + results independently (CC-BY)
- Standards submission: IETF draft for consent-over-tool-calls; ISO liaison note
- Academic paper (ASTRA-aligned evaluation)

Exit criteria:
- FROST in production path; key compromise threat model published
- One robotics integrator pilot with ISO mapping signed off
- Teleop session demonstrated end-to-end with an auditor watching

### R4 — Lead (weeks 45–60) · v2.1 "Reference"
Become the thing other people implement.

- LOKAHI conformance certification programme (self-cert + published test suite)
- Managed/cloud option (optional; open core stays complete)
- Manifest registry with signed public policies
- SI channel kit: co-selling deck, deployment playbook, training
- Annual transparency report: decisions processed, refusals, incidents

---

## 4. Task index (by phase)

### R1 tasks (detailed)

| ID | Task | WS | Acceptance |
|---|---|---|---|
| R1-01 | Streamable HTTP transport | W1 | Stateless request/response; passes conformance suite |
| R1-02 | OAuth 2.1 resource server + PRM + PKCE + RFC 8707 | W1 | Token issued for resource; wrong audience rejected |
| R1-03 | RFC 9207 `iss` validation + CIMD registration | W1 | Mix-up attack test passes |
| R1-04 | Per-client consent (confused deputy) | W1 | Static-client-ID flow forces consent |
| R1-05 | Token exchange / no passthrough | W1 | Upstream token differs from client token |
| R1-06 | `_meta` approval-status + `input_required` | W1 | Client can distinguish pending from failure |
| R1-07 | Gateway conformance suite (publish) | W1 | 40+ checks, CI-integrated |
| R1-08 | OIDC approver identity + passkey confirm | W2 | Approval requires fresh WebAuthn |
| R1-09 | RBAC roles + per-tool authority grants | W2 | Auditor cannot approve; operator cannot self-approve |
| R1-10 | Tool ACL per consumer/team | W2 | Cross-team call denied |
| R1-11 | Group quorum ("any 2 of board") | W2 | Group membership resolved from IdP |
| R1-12 | Rust host + embedded WASM kernel | W3 | p99 < 1 ms; parity with Python |
| R1-13 | Fuzz harness + differential tests | W3 | 24h fuzz clean; oracle parity |
| R1-14 | Merge-precedence property tests | W3 | Consent never relaxes a safety refusal |
| R1-15 | Evidence pack CLI + verifier | W6 | Offline verify without LOKAHI installed |
| R1-16 | S3 Object Lock exporter | W6 | Object immutability verified |
| R1-17 | OTel traces/metrics/logs + OCSF | W7 | Trace shows all layers per decision |
| R1-18 | SIEM exporters (4 targets) | W7 | Events land in each target |
| R1-19 | Prometheus + health endpoints | W7 | Scrape works; liveness/readiness |
| R1-20 | Approval inbox: email + Slack + passkey | W8 | Decision in < 3 clicks; signed link |
| R1-21 | Console MVP | W8 | Pending, detail, ledger browse, policy view |
| R1-22 | Docs site + quickstart | W8 | New user decides a call in < 5 min |
| R1-23 | OPA + Cedar export | W9 | Policies evaluate identically for covered rules |
| R1-24 | Arniko adapter with signed provenance | W9 | Findings appear as Layer 1 refusals |
| R1-25 | Python + TypeScript SDKs | W9 | Idiomatic; typed; retries |
| R1-26 | Docker + compose + Helm + GH Action | W9 | One-command local stack |
| R1-27 | 3 sector policy packs | W9 | Utility/hospital/editorial examples + tests |
| R1-28 | Tool-description pinning + drift refusal | W1 | Changed schema → refuse with reason |
| R1-29 | 60-point enterprise RFP checklist | W10 | Published, self-assessed honestly |

### R2 tasks (summary)
- Merkle ledger + inclusion proofs; witness cosigning; Rekor backend (W6)
- Key rotation + revocation propagation (W6)
- Multi-tenancy, HA, backups, distributed rate limiting (W7)
- Chaos suite + published load test (W7)
- Policy studio, simulation/replay, escalation routing, SLA dashboard (W8)
- Registry/virtual servers, Vault/AWS/GCP secrets, Go SDK, Terraform (W9)
- SOC 2 Type I, crypto review #1, pen test #1, corpus v2 (W10)

### R3 tasks (summary)
- FROST + DKG + group keys (W4)
- Disjointness attestation + Sybil collapse (W4)
- ROS 2 interlock, ISO mapping, PLC hold driver, safety case template (W5)
- WebXR teleop console + dual-control re-arm (W5/W8)
- Crypto review #2, standards drafts, independent eval paper (W10)

### R4 tasks (summary)
- Certification programme + conformance badge
- Managed option, manifest registry, SI channel kit, transparency report

---

## 5. Benchmarks we will publish (and be judged on)

| Metric | Target (R1) | Target (R3) |
|---|---|---|
| Decision p50 / p95 / p99 (Rust host) | < 0.5 / 1 / 5 ms | < 0.3 / 0.8 / 2 ms |
| Gateway API p99 (HTTP, decision included) | < 25 ms | < 15 ms |
| Throughput per core | 5k decisions/s | 20k decisions/s |
| False-refusal rate, benign corpus | < 2% | < 1% |
| MCP attack detection (corpus v2) | ≥ 90% | ≥ 98% |
| Chain verification, 1M entries | < 5 s | < 2 s |
| Approval round-trip (human present) | < 60 s | < 30 s |
| Air-gapped install to first decision | < 10 min | < 5 min |

All published with hardware, commit hash, corpus hash, and reproduction script.
No benchmark without a reproduction command.

---

## 6. Definition of done per release

A release ships only when all hold:

1. Every task in the phase has an acceptance test that fails before and passes after.
2. Public docs updated; CHANGELOG entry states what is *not* done.
3. Threat model updated for any new surface.
4. Benchmarks re-run and published with reproduction script.
5. No open critical/high security finding.
6. Backwards compatibility statement (manifest format, ledger format, API).
7. Air-gapped install verified in CI (container, no network).
8. One external user has run the quickstart on a clean machine.

---

## 7. Resourcing and sequencing reality

| Phase | Duration | Minimum team | Notes |
|---|---|---|---|
| R0 | 2 weeks | 1 | done |
| R1 | 12 weeks | 2–3 | 1 backend (transport/identity), 1 systems (Rust/fuzz), 1 product/frontend (console/inbox/docs) |
| R2 | 14 weeks | 4 | +1 SRE for HA/ops/chaos |
| R3 | 16 weeks | 4–5 | +1 crypto, +1 embedded/safety (or a robotics partner) |
| R4 | 16 weeks | 5–6 | +DevRel, +security/compliance |

Two people can reach R1 but not R2. The R2 gate (SOC 2, HA, multi-tenancy) is
where a company either hires or stalls.

---

## 8. Risks to the roadmap

| Risk | Impact | Mitigation |
|---|---|---|
| MCP spec changes again | Rework in W1 | Transport-agnostic core; conformance suite tracks revisions |
| A platform ships quorum + physical | Differentiators eroded | Accelerate W5 (robotics) and W10 (standards) — platforms will not certify ISO or publish a corpus |
| No design partner by R1 exit | Roadmap becomes fiction | Run P0 design-partner hunt in parallel from week 1 |
| Crypto review finds FROST or digest issue | R3 slips | Commission review #1 in R1, #2 before FROST GA |
| Two-person bus factor | Existential | Document everything; bring a third engineer in R1 |
| Scope creep into "AI governance SaaS" | Loses focus | Governance stays design-time (AIA); we sell enforcement + evidence |

---

## 9. What "complete" means

The vision is complete when, without a LOKAHI salesperson in the room:

1. An auditor can verify any decision offline from an evidence pack.
2. A utility can deploy air-gapped and show a regulator the quorum, the envelope, and the hold.
3. A robotics integrator can cite LOKAHI in an ISO 10218 safety case.
4. A platform vendor implements the conformance suite because their customer demanded it.
5. A competitor's comparison page has to list "multi-party disjoint consent" and "physical envelope" as gaps.
