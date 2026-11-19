# LOKAHI — Task Board

Living list. IDs match `ROADMAP.md`. Work top-down within a phase.
Rule: every task lands with an acceptance test that fails before it.

---

## NOW — R0 close-out (v1.0.1)

- [x] Audit remediation C1–C4, H1–H3, M1–M8
- [x] Regression suite `tests/test_audit_regressions.py` (21 tests)
- [x] `pyproject.toml` valid; `pip install --dry-run .` passes
- [x] Ed25519 CLI e2e and HMAC CLI e2e tested
- [x] `.gitignore` build artifacts
- [ ] README: mark SLM tier experimental; limit safety claims to tested behaviour
- [ ] README: add the audit note and link `AUDIT_REPORT.md`
- [ ] CI workflow: Python 3.10/3.11/3.12, Rust build+test, packaging smoke, CLI e2e
- [ ] Tag `v1.0.1` (local only; push awaits confirmation)

---

## R1 — Parity (v1.1) — 12 weeks

### W1 Protocol & transport
- [ ] R1-01 Streamable HTTP transport (stateless, MCP 2026-07-28) + keep stdio
- [ ] R1-02 OAuth 2.1 resource server: RFC 9728 PRM, PKCE, RFC 8707 resource indicators
- [ ] R1-03 RFC 9207 `iss` validation; CIMD client registration
- [ ] R1-04 Per-client consent for static client IDs (confused-deputy defence)
- [ ] R1-05 Token exchange (RFC 8693); never pass client tokens upstream
- [ ] R1-06 `_meta.approval_status` + `input_required` interrupt handling
- [ ] R1-07 Publish gateway conformance suite (40+ checks) + CI integration
- [ ] R1-28 Tool-description pinning: schema hash in manifest; drift → refuse

### W2 Identity & authority
- [ ] R1-08 OIDC-authenticated approvers; WebAuthn/passkey confirm per decision
- [ ] R1-09 RBAC roles (steward/operator/auditor) + per-tool authority grants
- [ ] R1-10 Tool ACLs per consumer and team
- [ ] R1-11 Group quorum ("any 2 of the water board") resolved from IdP

### W3 Enforcement kernel
- [ ] R1-12 Rust host binary with embedded WASM kernel; p99 < 1 ms
- [ ] R1-13 cargo-fuzz harness + 24h clean run; differential tests vs Python oracle
- [ ] R1-14 Property tests: consent never relaxes a safety refusal

### W6 Evidence
- [ ] R1-15 `lokahi evidence <digest>`: offline-verifiable bundle (JSON + PDF)
- [ ] R1-15b Standalone verifier tool (no LOKAHI install required)
- [ ] R1-16 S3 Object Lock exporter

### W7 Observability
- [ ] R1-17 OTel traces + metrics + structured logs; OCSF schema
- [ ] R1-18 SIEM exporters: Splunk HEC, Sentinel, Elastic, generic webhook
- [ ] R1-19 Prometheus `/metrics`, `/healthz`, `/readyz`

### W8 Experience
- [ ] R1-20 Approval inbox: email signed link, Slack/Teams app, passkey confirm
- [ ] R1-21 Console MVP: pending approvals, decision detail, ledger browse, policy view
- [ ] R1-22 Docs site + quickstart (< 5 min to first decision)

### W9 Integrations & ecosystem
- [ ] R1-23 OPA (Rego) bundle export and Cedar policy export
- [ ] R1-24 Arniko adapter with signed provenance
- [ ] R1-25 Python SDK + TypeScript SDK
- [ ] R1-26 Dockerfile, compose, Helm chart, GitHub Action
- [ ] R1-27 Sector policy packs: utility, hospital, editorial

### W10 Assurance
- [ ] R1-29 60-point enterprise RFP checklist, self-assessed honestly
- [ ] Book external crypto review #1

### R1 exit gate
- [ ] p99 < 1 ms (Rust host), p99 API < 25 ms, published with reproduction script
- [ ] 10,000-decision soak: zero crashes, zero chain breaks
- [ ] Conformance suite green (stdio + HTTP)
- [ ] 200+ tests
- [ ] One external user completes quickstart on a clean machine

---

## R1-in-parallel — Design partner (do not wait)

- [ ] 10 named conversations: 4 utilities, 3 SIs, 2 hospitals, 1 robotics integrator
- [ ] 1 signed pilot LOI
- [ ] Utility deployment sketch (air-gapped, 3 stewards, valve + crew dispatch)
- [ ] Hospital deployment sketch (care-plan change, 2-of-3 clinical board)

---

## R2 — Operate (v1.2) — 14 weeks

### W6 Evidence & ledger
- [ ] Merkle tree ledger with inclusion proofs
- [ ] Witness cosigning (2 independent witnesses)
- [ ] Optional Rekor/Trillian backend
- [ ] Key rotation with historical verifiability (key fingerprint in chain)
- [ ] Bounded revocation propagation

### W7 Ops
- [ ] Multi-tenancy: per-tenant keys, policies, ledgers, quotas
- [ ] HA: stateless decision service + shared ledger; writer election/partitioning
- [ ] Backup/restore runbook + restore test in CI
- [ ] Distributed rate limiting (Redis)
- [ ] Chaos/fault-injection suite (kill WASM, drop ledger, sever IdP, slow consent)
- [ ] Published load test (decisions/s/core, tail latency)

### W8 Experience
- [ ] Policy studio: author, validate, dry-run, diff, version
- [ ] Simulation/replay over historical ledger
- [ ] Escalation routing + on-call webhook integration
- [ ] Approval SLA dashboard

### W9 Integrations
- [ ] Registry / virtual servers (curated tool catalogues)
- [ ] Credential injection: Vault, AWS/GCP/Azure secret managers, env
- [ ] Go SDK, Terraform provider
- [ ] SIEM/SOAR playbooks

### W10 Assurance
- [ ] SOC 2 Type I readiness, then audit
- [ ] External crypto review #1 completed and published
- [ ] Pen test #1, findings remediated
- [ ] Red-team corpus v2 (MCP-specific attacks)

### R2 exit gate
- [ ] 100k-decision soak clean
- [ ] Two independent deployments (one air-gapped)
- [ ] SOC 2 Type I report
- [ ] Benchmark + pen-test summary published

---

## R3 — Differentiate (v2.0) — 16 weeks

### W4 Consent crypto
- [ ] RFC 9591 FROST two-round threshold signatures + DKG
- [ ] Group key per quorum; gate never sees individual signatures
- [ ] Disjointness attestation graph (registration, key custody, infra, geography)
- [ ] Sybil collapse for shared custody/ASN/org
- [ ] Auditable emergency quorum substitution

### W5 Physical safety
- [ ] ROS 2 action-server interlock node (Rust/C++)
- [ ] ISO 10218-1:2025 / TS 15066 mapping (force, pressure, speed-and-separation)
- [ ] PLC-safe hold driver (OPC UA / Modbus)
- [ ] Safety case template: hazard → envelope → interlock → evidence
- [ ] One integrator pilot

### W5/W8 Teleoperation
- [ ] WebXR console: live pose stream, action log, re-arm with passkey
- [ ] Dual-control re-arm (two disjoint humans)

### W10 Assurance
- [ ] External crypto review #2 (FROST + attestation)
- [ ] Publish red-team corpus + results independently
- [ ] IETF draft for consent-over-tool-calls; ISO liaison note
- [ ] Academic evaluation paper

### R3 exit gate
- [ ] FROST in production path with published threat model
- [ ] Robotics pilot with ISO mapping signed off
- [ ] Teleop session demonstrated end-to-end with an auditor present

---

## R4 — Lead (v2.1) — 16 weeks

- [ ] Conformance certification programme + badge
- [ ] Managed/cloud option (open core stays complete)
- [ ] Signed public manifest registry
- [ ] SI channel kit (deck, deployment playbook, training)
- [ ] Annual transparency report

---

## Explicitly out of scope (until R4)

- Prompt/content filtering — refer to LlamaFirewall / Guardrails
- Model hosting or routing
- General workflow orchestration
- Consumer apps, prosumer plans
- Certified machine safety hardware (we interlock, we do not certify)

---

## Completed (history)

- v0.1.2 core, v0.1.3 trust-boundary hardening, v0.1.4 decisions tier (upstream)
- v1.0.0 cascade: WASM pre-flight, SLM tier, Ed25519 consent, fail-secure + WebXR
- v1.0.1 audit remediation: 4 critical, 3 high, 8 medium, 21 regression tests
