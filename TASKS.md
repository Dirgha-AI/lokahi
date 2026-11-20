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

## R1 - Interoperability (v1.1) — 12 weeks

### W1 Protocol & transport
- [x] R1-01 Streamable HTTP transport (stateless, no sessions) + keep stdio (`tools/lokahi_http.py`)
- [x] R1-02 OAuth 2.1 resource server: RFC 9728 PRM, PKCE, RFC 8707 resource indicators
- [ ] R1-03 RFC 9207 `iss` validation; CIMD client registration (deferred R1-02b: client-side)
- [ ] R1-04 Per-client consent for static client IDs (confused-deputy defence) (deferred R1-02b)
- [x] R1-05 Never pass client tokens upstream (no passthrough); RFC 8693 exchange deferred R2
- [x] R1-06 `_meta.approval_status` + `input_required` interrupt metadata (pending is resumable; timed-out/refused/not-requested are distinct; transport-level suspend/resume remains client-owned)
- [x] R1-07 Publish gateway conformance suite (33 checks) + CI integration
- [x] R1-28 Tool-description pinning: schema hash in manifest; drift → refuse (`tools/lokahi_toolpin.py`, 11 tests)

### W2 Identity & authority
- [ ] R1-08 OIDC-authenticated approvers (token sub used); WebAuthn/passkey deferred R1-20b
- [x] R1-09 Configured RBAC roles (steward/operator/auditor) applied to tool use, approval ingestion, and quorum counting; IdP role sync deferred
- [x] R1-10 Tool ACLs per consumer and team
- [x] R1-11 Named group quorum from validator config or `--operator-groups`; group must match signed manifest `n` and registered keys; IdP membership resolution deferred

### W3 Enforcement kernel
- [ ] R1-12 Rust host binary with embedded WASM kernel; p99 < 1 ms (deferred R2)
- [x] R1-13 cargo-fuzz harness (5 targets) + 2k-case Rust property tests
- [x] R1-14 Property tests: consent never relaxes a safety refusal (10 tests; found+fixed one edge)

### W6 Evidence
- [x] R1-15 `lokahi_evidence.py build|verify`: offline-verifiable bundle (6 checks; PDF pending)
- [x] R1-15b Standalone verifier (`verify` subcommand; stdlib + optional cryptography)
- [ ] R1-16 S3 Object Lock exporter

### W7 Observability
- [x] R1-17 OTLP/HTTP JSON log export per decision (+ metrics endpoint); span detail deferred
- [x] R1-18 SIEM exporters: Splunk HEC, Elastic, generic webhook (Sentinel via webhook)
- [x] R1-19 Prometheus `/metrics`, `/healthz`, `/readyz`

### W8 Experience
- [x] R1-20 Approval inbox: signed email link, Slack, webhook, console; `/approve` requires operator signature (passkey deferred)
- [ ] R1-21 Console MVP (deferred R2; CLI + HTTP endpoints today)
- [x] R1-22 Docs: QUICKSTART.md, CONCEPTS.md (docs site generator deferred)

### W9 Integrations & ecosystem
- [ ] R1-23 OPA (Rego) bundle export and Cedar policy export (deferred R2)
- [x] R1-24 Arniko findings ingested in WASM Layer 1 (`arniko_check`); provenance signing deferred
- [ ] R1-25 Python SDK + TypeScript SDK (deferred R2)
- [x] R1-26 Dockerfile, compose, Helm chart, GitHub Action
- [x] R1-27 Sector policy packs: utility, hospital, editorial

### W10 Assurance
- [x] R1-29 60-point enterprise RFP checklist, self-assessed honestly
- [ ] Book external crypto review #1 (commercial action, not code)

### Completed in R1 so far
- R1-01 Streamable HTTP: `/mcp` POST (JSON or SSE), `/healthz`, `/readyz`, `/metrics`, bearer allowlist, locked-gateway 503
- R1-15 evidence packs + standalone verifier (`tests/test_r1_features.py`)
- R1-28 tool pinning: schema/policy hashes, catalogue drift, gateway refusal
- Ledger now records `action_digest` so packs need no side data
- R1-06 approval protocol metadata distinguishes pending, approved, timed-out, refused, and not-requested; only pending escalations set `input_required`
- R1-09 role config can deny auditor/operator execution or approval; quorum verification also filters non-steward and unassigned validators when role mode is enabled
- R1-11 named consent groups are locally configured in validator JSON or `--operator-groups`, and validated against the manifest threshold and registered validator keys (not IdP-resolved)

### R1 exit gate
- [ ] p99 < 1 ms (Rust host) — deferred with R1-12; Python path measured ~9 ms
- [x] 10,000-decision soak: zero crashes, zero chain breaks (WASM bridge soak 6k calls; ledger chain verified)
- [x] Conformance suite green (stdio + HTTP): 33/33
- [x] 200+ tests: 246 python + 26 rust
- [ ] One external user completes quickstart on a clean machine (needs a design partner)

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

---

## SESSION PLAN — autonomous R1 execution (no interruptions)

Order of work, each with tests and a commit:

1. R1-02 OAuth 2.1 resource server: PRM (RFC 9728), JWT verify (RS256/ES256/HS256), `iss`, `aud`, exp/nbf, JWKS cache, WWW-Authenticate
2. R1-17/R1-18 telemetry: OTLP/HTTP JSON spans + events; webhook, Splunk HEC, Elastic sinks; async, fail-open
3. R1-20 approval inbox: signed links, SMTP/Slack/console notifiers, `/approve` endpoint that still requires the operator's own signature
4. R1-10 tool ACLs per consumer (token subject or configured requester)
5. R1-07 conformance suite (stdio + HTTP, published checks)
6. R1-26 deployment: Dockerfile, compose, Helm chart, GitHub Action
7. R1-27 policy packs: utility, hospital, editorial + validation tests
8. R1-13/R1-14 fuzz targets + property tests
9. R1-22 docs: quickstart, concepts
10. R1-29 enterprise RFP checklist
11. Version 1.1.0, changelog, tag

Explicitly deferred to R2/R3 (not fakeable in one session): Rust host (R1-12),
token exchange (R1-05), full conformance against vendor servers, SOC 2, HA,
multi-tenancy, FROST, ROS 2.
