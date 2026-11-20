# Enterprise RFP checklist — self-assessed

Sixty questions a security or procurement team will ask. Answered honestly, with
the evidence a reviewer can check and the roadmap item for anything missing.

**Legend:** Met · Partial · Not met · Planned (phase)

Counts: **Met 24 · Partial 14 · Not met 14 · Planned 8** (as of v1.1.0).

---

## A. Protocol and transport

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| A1 | MCP stdio transport | Met | `tools/mcp_stdio_proxy.py` |
| A2 | MCP Streamable HTTP (stateless, 2026-07-28 shape) | Partial | `tools/lokahi_http.py`; JSON + single-message SSE; no server-initiated streams |
| A3 | No protocol sessions / session-id not used as auth | Met | stateless by design |
| A4 | RFC 9728 protected-resource metadata | Met | `/.well-known/oauth-protected-resource` |
| A5 | OAuth 2.1 bearer validation (PKCE is AS-side) | Partial | `lokahi_oauth.py`; resource-server side only |
| A6 | RFC 8707 resource indicators (`aud`) enforcement | Met | `JWTVerifier` audience check |
| A7 | RFC 9207 `iss` response validation | Not met | client-side; R1-02b |
| A8 | Client ID Metadata Documents | Not met | R1-02b |
| A9 | No token passthrough | Met | gateway never forwards tokens or requests |
| A10 | Token exchange (RFC 8693) for upstream credentials | Not met | R1-05 |

## B. Identity and access

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| B1 | Approver identity from an IdP | Partial | JWT `sub`/`client_id` accepted; no interactive OIDC login for approvers |
| B2 | WebAuthn / passkey confirmation per decision | Not met | R1-20b; approvals currently require a signing key |
| B3 | RBAC (steward / operator / auditor) | Partial | local role mapping enforces tool invocation and approval/quorum restrictions; no IdP role/group sync |
| B4 | Per-consumer tool ACL | Met | `lokahi_acl.py`, `--tool-acl` |
| B5 | Group quorum resolved from IdP groups | Partial | named local groups constrain quorum; IdP group resolution remains planned |
| B6 | Agent cannot approve itself | Met | `agent_supplied_control_metadata` |
| B7 | Operator cannot approve its own agent | Met | Rule 11 + consent exclusion |
| B8 | Emergency declarant cannot vote | Met | Rule 12 + consent exclusion |
| B9 | One person = one vote regardless of agents | Met | `distinct_operators` |
| B10 | Credential injection / vault (agent holds no secret) | Not met | R2 |

## C. Consent and authority

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| C1 | Per-manifest m-of-n threshold | Met | `consent_threshold` |
| C2 | Approval bound to exact tool + argument digest | Met | `compute_action_digest` |
| C3 | Approval expiry | Met | `Approval.expires` |
| C4 | Nonce replay protection | Met | consent ring dedupe |
| C5 | Timeout is a refusal | Met | `non_response` |
| C6 | Two dissenters freeze the action | Met | `suspended_by_ring` |
| C7 | Consent cannot relax a safety refusal | Met | `merge_threshold_consent` + property tests |
| C8 | Cryptographic threshold signatures (FROST) | Not met | independent ED25519 signatures; R3 |
| C9 | Disjointness attestation (proof keys are distinct people) | Not met | R3 |
| C10 | Emergency quorum substitution | Not met | R3 (auditable, no silence-as-consent) |

## D. Policy and supply chain

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| D1 | Default-deny tool policy | Met | `tool_not_in_manifest`, `argument_out_of_policy` |
| D2 | Forbidden data targets refused before consent | Met | Rule 4 |
| D3 | Residency enforcement | Met | Rule 7 |
| D4 | Jurisdiction enforcement | Met | Rule 6 |
| D5 | Physical envelope (force, torque, velocity, workspace) | Met | `_check_physical_envelope` |
| D6 | Stopping-distance barrier | Met | CBK check in `lokahi.py` |
| D7 | Tool schema pinning / rug-pull detection | Met | `lokahi_toolpin.py` |
| D8 | Server registry with signed provenance | Not met | R2 |
| D9 | OPA / Cedar interop | Not met | R1-23 |

## E. Evidence and audit

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| E1 | Every decision recorded, including refusals | Met | `Ledger.append` in `_record` |
| E2 | Tamper-evident hash chain | Met | `Ledger.verify` |
| E3 | Entry links to its predecessor | Met | `prev` field |
| E4 | Offline-verifiable evidence pack | Met | `lokahi_evidence.py` |
| E5 | Standalone verifier (no LOKAHI install) | Met | `lokahi_evidence.py verify` |
| E6 | Merkle inclusion proofs | Not met | R2 |
| E7 | Witness cosigning / transparency log | Not met | R2 |
| E8 | SIEM export (Splunk, Elastic, webhook, OTLP) | Met | `lokahi_telemetry.py` |
| E9 | Retention / archival to immutable storage | Partial | S3 Object Lock exporter planned R2 |

## F. Operations

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| F1 | Health / readiness endpoints | Met | `/healthz`, `/readyz` |
| F2 | Prometheus metrics | Met | `/metrics` |
| F3 | OTLP traces | Partial | OTLP **logs** per decision; spans planned |
| F4 | Multi-tenancy | Not met | R2 |
| F5 | High availability / horizontal scale | Not met | R2 (single writer today) |
| F6 | Backup and restore | Partial | ledger is append-only JSONL; runbook pending |
| F7 | Distributed rate limiting | Not met | R2 (WASM-local buckets today) |
| F8 | Chaos / fault-injection suite | Not met | R2 |
| F9 | Container image, compose, Helm, GH Action | Met | `Dockerfile.lokahi`, `docker-compose.lokahi.yml`, `deploy/helm/lokahi`, `.github/actions/lokahi-verify` |
| F10 | Air-gapped deployment | Met | no network required; WASM built locally |

## G. Security and assurance

| # | Requirement | Status | Evidence / plan |
|---|---|---|---|
| G1 | External cryptographic review | Not met | booked in R1; required before any production safety claim |
| G2 | Penetration test | Not met | R2 |
| G3 | SOC 2 Type I | Not met | R2 |
| G4 | Fuzzing of the decision kernel | Partial | cargo-fuzz targets + 2k-case Rust property tests |
| G5 | Property tests for safety invariants | Met | `tests/test_properties.py` |
| G6 | Published conformance suite | Met | `tools/conformance.py` (33 checks) |
| G7 | Documented threat model | Met | `docs/THREAT_MODEL.md` |
| G8 | Dependency audit | Met | `docs/DEPENDENCY_AUDIT.md` |
| G9 | Vulnerability disclosure policy | Partial | upstream `SECURITY.md`; no CVE process yet |
| G10 | Support SLA | Not met | commercial |

---

## How to use this document in a review

1. Anything marked **Met** has a file and a test you can run.
2. Anything **Partial** states exactly which half exists. Do not accept a
   vendor demo that shows the other half without a date.
3. Anything **Not met** is a commitment gap. If your deployment depends on it,
   it is a blocker, not a nice-to-have.

The three that matter most for a first production deployment:

- **G1** external crypto review (no safety claim without it)
- **B2** passkey approvals (a signing key on a laptop is not a human check)
- **F5** HA (one process is not a control plane)

None of those are code-complete in v1.1.0.
