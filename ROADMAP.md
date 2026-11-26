# LOKAHI — Roadmap

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

## 0. Where the project actually is

This section is the answer to "what exists today", and it is the first thing a
buyer, an auditor or a new contributor reads. It replaces the version labels in
the phase sections below, which had drifted from what was released.

| Version | Date | Contents | Verification |
|---|---|---|---|
| v0.1.2 – v1.0.1 | Oct–Nov 2026 | Core kernel, fail-secure lock, evidence pack, cascading runtime | 200+ tests |
| v1.1.0 | 2026-11-20 | Parity release: Streamable HTTP transport, tool-description pinning, OAuth resource server | 267 tests |
| v1.2.0 | — | **Never tagged.** The features planned for it (RBAC, named consent groups, approval protocol) shipped under v1.3.0. Recorded here as skipped rather than silently renumbered. | — |
| v1.3.0 | 2026-11-23 | Enterprise RBAC, named consent groups, approval protocol | CI green, PyPI published |
| v1.3.1 | this release | "Truth": canonical versioning, honest changelog, status-tracked roadmap | see §6 |

**Three defects this release exists to fix.**

1. `canonical()` was labelled RFC 8785 and was not. It emitted `1.0` where JCS
   mandates `1`, `1e-07` where JCS mandates `1e-7`, and `-0.0` where JCS mandates
   `0`. Harmless for manifests made of strings and integers; fatal for a
   physical manifest made of floats, since a Rust or JavaScript verifier could
   not reproduce the digest and would report tampering on an untouched episode.
   v1.3.1 adds canonical version 2 (`canonical_jcs`) and records the version in
   every entry written with it. Version 1 chains verify unchanged.
2. `CHANGELOG.md` was not a sequence: v1.1.0 sat between two 2026-11-23 entries
   and v1.2.0 was missing, so no reader could order the releases.
3. This document promised R1 → v1.1, R2 → v1.2, R3 → v2.0 while v1.3.0 had
   already shipped R1 content, and carried no status column at all.

**How status is derived.** Every task below carries `done`, `partial` or `open`,
plus the file or test that justifies the marking. `done` means a named test
exists and passes at the tagged commit. `partial` means the code exists but the
acceptance criterion in the task description is not fully met. `open` means
nothing on disk satisfies it. No markings from memory or intention.

---

## 1. Competitive parity matrix

Legend: ✅ ships · ◐ partial · ❌ absent · ★ LOKAHI-unique

| Capability | Permit | Docker AG | SecureAuth | TrueFoundry | EnforceAuth | ContextForge | Kong | agentgw | **LOKAHI** |
|---|---|---|---|---|---|---|---|---|---|
| stdio MCP | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ now |
| Streamable HTTP / stateless MCP 2026-07-28 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ v1.1 |
| OAuth 2.1 resource server (RFC 9728 PRM, PKCE, RFC 8707) | ✅ | ◐ | ✅ | ◐ | ✅ | ✅ | ✅ | ◐ | ✅ v1.1 |
| iss validation (RFC 9207), CIMD client registration | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ✅ | ◐ | ◐ v1.1 |
| No token passthrough / token exchange | ◐ | ◐ | ✅ | ◐ | ✅ | ◐ | ✅ | ◐ | ❌ open |
| Per-client consent (confused deputy) | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ v1.3 |
| Tool ACL per consumer / team | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ v1.3 |
| Registry / virtual servers | ◐ | ✅ | ❌ | ✅ | ❌ | ✅ | ◐ | ◐ | ❌ deferred |
| Tool-description pinning / rug-pull defence | ◐ | ✅ | ◐ | ◐ | ◐ | ✅ | ◐ | ◐ | ✅ v1.1 |
| Credential injection / vault | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ open |
| Human-in-the-loop approval | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ now |
| Argument-bound approval | ✅ | ◐ | ◐ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ now |
| Timeout always rejects | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | ✅ now |
| **Disjoint m-of-n quorum** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| **Dissent freeze** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| **Physical envelope + safe hold** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| **Fail-secure lock + teleop handoff** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ★ now |
| Policy-as-code interop (OPA / Cedar / CEL) | ◐ | ✅ Cedar | ◐ | ◐ | ✅ OPA | ✅ OPA | ◐ | ✅ CEL | ❌ deferred |
| OTel traces + metrics | ◐ | ✅ | ◐ | ◐ | ◐ | ✅ | ◐ | ✅ | ✅ v1.1 |
| SIEM export (Splunk/Sentinel/Elastic) | ◐ | ◐ | ◐ | ✅ | ◐ | ✅ | ◐ | ✅ | ✅ v1.1 |
| Approval inbox (email / Slack) | ✅ | ✅ | ✅ | ✅ | ◐ | ❌ | ❌ | ◐ | ◐ v1.1 |
| Admin console | ✅ | ✅ | ✅ | ✅ | ◐ | ✅ | ✅ | ◐ | ❌ open |
| Escalation / on-call routing | ◐ | ◐ | ◐ | ◐ | ◐ | ❌ | ❌ | ◐ | ❌ v1.6 |
| Immutable transparency log + witness | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ◐ chain now, witnesses v1.6 |
| Multi-tenancy + HA | ✅ | ◐ | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | ❌ v1.6 |
| Policy simulation / replay over history | ❌ | ❌ | ❌ | ❌ | ◐ | ❌ | ❌ | ❌ | ❌ deferred |
| SDKs (Python / TypeScript / Go) | ◐ | ◐ | ✅ | ✅ | ◐ | ✅ | ✅ | ◐ | ❌ open |
| Air-gapped deployment | ◐ | ◐ | ◐ | ❌ | ◐ | ✅ | ❌ | ◐ | ✅ now |
| Fuzz + formal verification of kernel | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ◐ v1.1 |
| FROST threshold signatures | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ v2.0 |
| Disjointness attestation graph | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ v2.0 |
| MCP gateway conformance suite | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ v1.1 ★ leadership |
| Evidence pack for auditors | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ✅ v1.1 ★ |
| **VLA action-chunk gate with signed receipts** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **v1.4 ★ unique** |
| **Underwriting-grade incident evidence (PAIDS-shaped)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **v1.4.1 ★ unique** |
| **Safety-rated handoff (OPC UA / Modbus hold)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **v1.5 ★ unique** |

**Reading:** we lead on four safety capabilities nobody sells, and v1.3.1 adds
two claims we can now prove rather than assert (RFC 8785 reproducibility and a
verifiable status ledger). We are behind on transport edges, SDKs, console and
operations; that work is v1.6 at the earliest, because none of it is the reason
anyone buys.

---

## 2. Workstreams

| ID | Workstream | Owner type | Primary risk retired |
|---|---|---|---|
| W1 | Protocol & transport (MCP 2026-07-28, OAuth 2.1, HTTP) | Backend | Cannot front real MCP servers |
| W2 | Identity & authority (OIDC approvers, RBAC, tenants) | Backend | Approver identity is not real |
| W3 | Enforcement kernel (Rust host, WASM, fuzz, formal) | Systems | Latency and assurance |
| W4 | Consent crypto (FROST, DKG, rotation, attestation) | Crypto | "Threshold" is only counting |
| W5 | Physical safety (VLA gate, ROS 2, ISO 10218/18497, PLC hold) | Embedded/safety | No path to robotics buyers |
| W6 | Evidence & ledger (canonical versions, transparency log, witness) | Backend | Auditor does not accept it |
| W7 | Observability & ops (OTel, SIEM, HA, scaling, chaos) | SRE | Cannot run in production |
| W8 | Experience (console, approval inbox, policy studio, docs) | Product/frontend | Nobody can use it |
| W9 | Integrations & ecosystem (OPA/Cedar, Arniko, SIEM, SDKs, Helm) | Backend/DevRel | Island product |
| W10 | Assurance & community (SOC 2, pen test, corpus, standards) | Security/DevRel | Nobody trusts it |
| **W11** | **Underwriting evidence (incident schema, standards matrix, insurer pack)** | **Safety/standards** | **No buyer with a budget and a deadline** |

**W5 moves from R3 to the second release after this one.** The roadmap
previously scheduled robotics for weeks 29–44. Regulation (EU) 2023/1230 applies
on 20 January 2027, ISO 18497-4 has no reference implementation in software, and
the insurance market is underwriting physical AI now with no vendor to buy from.
A roadmap that reaches robotics in month seven is a roadmap for a market that
has already chosen its vendors. **W11 is new** and exists because it is the only
workstream whose output a buyer can price today.

---

## 3. The release train

Each release names the buyer it exists for. A release with no buyer is a
refactor, and refactors do not get their own tag.

| Version | Name | Contents | Buyer | Gate |
|---|---|---|---|---|
| v1.3.1 | Truth | Canonical v2, changelog repaired, roadmap statused, Arniko contract pinned | Diligence | §6, all eight |
| v1.4.0 | Instrument | `lokahi.vla`: manifest section, gate, receipts, `lokahi-vla-verify`, benchmark | Integrator engineering | §6 + determinism replay + tamper test |
| v1.4.1 | Evidence | Standards matrix (W11), PAIDS-shaped receipt schema, underwriter pack | Insurer / MGA | one underwriter names the fields they would price |
| v1.5.0 | Interlock | OPC UA / Modbus hold driver, ROS 2 node in Rust, dual-control re-arm, reference architecture | Integrator pilot | hardware-in-the-loop, one integrator |
| v1.6.0 | Production | R2 as written: Merkle proofs, witnesses, multi-tenancy, HA, chaos, SOC 2 Type I | Enterprise platform | §6 + 100k soak + two deployments |
| v2.0.0 | Authority | FROST + DKG, disjointness attestation, ISO liaison, conformance programme | Standards / SI channel | crypto review #2, one ISO mapping signed off |
| v2.1.0 | Reference | Certification badge, SI kit, transparency report | Ecosystem | — |

### v1.3.1 — Truth

- Canonical version 2: `canonical_jcs`, `_jcs_number`, `digest(version, obj)`.
  `Ledger(canon=...)` writes version 2 entries that declare `canon: 2`; version 1
  entries carry no such field and verify byte for byte as before.
- `lokahi_evidence._entry_hash` dispatches on the entry's declared version, so a
  version 2 chain verifies offline and an unknown version is a verification
  failure rather than a crash.
- Changelog reordered into a monotonic history with v1.2.0 recorded as skipped.
- This roadmap: status column with evidence, version ledger, W11, W5 pulled forward.
- **Knowingly not done:** signing, approval and action digests still use
  canonical version 1. They are Python-to-Python and changing them would
  invalidate every issued manifest for no benefit. They move to version 2 only
  if a non-Python verifier needs them, which is v1.4.0's problem, not this one.

### v1.4.0 — Instrument

`tools/vla/` exposed as `lokahi.vla`, built on the existing engine rather than
beside it:

- `physical_vla` manifest section carrying **only** what `physical_envelope`
  lacks: `max_action_age_ms`, `watchdog_timeout_ms`, `execution_mode`,
  `keep_out_aabbs`, `max_gate_eval_us`, per-rule `policy_on_violation`. Force,
  torque and workspace limits are **referenced** by name from
  `physical_envelope`, never restated, so one physical limit has one source of
  truth.
- Gate: structural checks, per-element chunk freshness (age is
  `chunk_ts + index * dt`, not one timestamp per chunk), polyline sweep against
  keep-in and keep-out AABBs, kinematic and wrench envelopes, and
  `max_gate_eval_us` that **refuses** on overrun — the same pattern the WASM
  preflight already uses with `budget_exceeded`.
- States project onto `FailSecureManager`. There is no second state machine.
- Receipts and `lokahi-vla-verify`, with `"canon": 2` in every receipt.
- Packaging: `"lokahi.vla"` added to `[tool.setuptools] packages`, the verify CLI
  added to `[project.scripts]`, adapters duck-typed and out of the import path so
  `dependencies = []` stays true.
- **Knowingly not done:** the ROS 2 adapter is not written until it can be tested
  against recorded message fixtures. Nothing on this machine can import `rclpy`,
  and untested duck-typed transport code is the kind that fails on the robot.

### v1.4.1 — Evidence (W11)

A document release, judged by whether a buyer can price it.

- Standards mapping matrix: every rule to the clause it discharges across
  ISO 10218-1/-2:2025, ISO/TS 15066, ISO 18497-4, ISO 25119, IEC 61511,
  IEC 62443-4-1, and AI Act Articles 12 and 17.
- Receipt schema aligned field-for-field with the PAIDS Layer A list, so an
  underwriter recognises it on sight.
- One worked reconstruction: a synthetic power-and-force-limited contact with a
  100 Hz force trace, the protective stop at 10–50 ms resolution, control-mode
  transition history, and a signed-at-source claim a third party can verify with
  a JSON parser and openssl.
- **Knowingly not done:** we do not claim this satisfies any certification.
  LOKAHI is not a safety-rated control function under ISO 13849-1 and cannot
  become one in CPython.

### v1.5.0 — Interlock

- Hold driver over OPC UA or Modbus that a PLC accepts.
- ROS 2 action-server interlock node in Rust, not Python.
- Dual-control re-arm (two humans, disjoint) with passkey.
- Reference architecture naming the safety-rated controller and LOKAHI's position
  outside it, feeding it.
- Gate is hardware-in-the-loop with one integrator, plus the architecture doc
  saying in plain words that this is not a protective stop and not PL d.

### v1.6.0 — Production

R2 as previously written, unchanged: Merkle inclusion proofs and witness
cosigning, key rotation with historical verifiability, revocation propagation,
multi-tenancy, HA with single-writer election, distributed rate limiting, chaos
suite, published load test, policy studio, simulation/replay, escalation routing,
SOC 2 Type I readiness, external crypto review #1, pen test #1, corpus v2.

---

## 4. Task index

Status legend: `done` (named test passes at the tagged commit) · `partial` (code
exists, acceptance criterion not fully met) · `open` (nothing on disk).

### R1 tasks

| ID | Task | WS | Status | Evidence |
|---|---|---|---|---|
| R1-01 | Streamable HTTP transport | W1 | done | `tools/lokahi_http.py`, `test_allowed_call_over_http` |
| R1-02 | OAuth 2.1 resource server + PRM + PKCE + RFC 8707 | W1 | done | `tools/lokahi_oauth.py`, `test_valid_rs256_token`, `test_wrong_audience` |
| R1-03 | RFC 9207 `iss` validation + CIMD registration | W1 | done | `test_wrong_issuer`; CIMD untested |
| R1-04 | Per-client consent (confused deputy) | W1 | done | v1.3.0 entry, `test_consumer_identity_reaches_ledger` |
| R1-05 | Token exchange / no passthrough | W1 | open | no exchange path exists |
| R1-06 | `_meta` approval-status + `input_required` | W1 | done | `test_escalation_carries_pending_status`, `test_timed_out_consent_is_not_input_required` |
| R1-07 | Gateway conformance suite (publish) | W1 | partial | `tools/conformance.py`; not packaged separately |
| R1-08 | OIDC approver identity + passkey confirm | W2 | partial | role mode enforced (`test_role_mode_rejects_unassigned_approver`); no WebAuthn |
| R1-09 | RBAC roles + per-tool authority grants | W2 | done | `test_auditor_cannot_invoke`, `test_steward_role_still_subject_to_tool_list` |
| R1-10 | Tool ACL per consumer/team | W2 | done | `test_exact_and_wildcard`, `test_gateway_denies_by_consumer` |
| R1-11 | Group quorum ("any 2 of board") | W2 | done | `test_group_must_match_manifest_n_and_registered_keys` |
| R1-12 | Rust host + embedded WASM kernel | W3 | partial | `crates/lokahi-gate` lives in Dirgha-AI/dirgha, not here; no p99 parity test in this repo |
| R1-13 | Fuzz harness + differential tests | W3 | partial | `docs/FUZZING_PLAN.md`, `tools/wasm_preflight/fuzz/`; no 24h clean run recorded |
| R1-14 | Merge-precedence property tests | W3 | done | `tests/test_properties.py` (10 tests) |
| R1-15 | Evidence pack CLI + verifier | W6 | done | `test_cli_build_and_verify`, `test_evidence_detects_tampered_decision` |
| R1-16 | S3 Object Lock exporter | W6 | open | no exporter |
| R1-17 | OTel traces/metrics/logs + OCSF | W7 | done | `tools/lokahi_telemetry.py`, `tests/test_telemetry.py` |
| R1-18 | SIEM exporters (4 targets) | W7 | partial | present in telemetry module; target-by-target landing untested |
| R1-19 | Prometheus + health endpoints | W7 | done | `test_health_readiness_metrics`, `test_locked_gateway_refuses_over_http_and_reports_not_ready` |
| R1-20 | Approval inbox: email + Slack + passkey | W8 | partial | `tools/lokahi_notify.py`; no passkey confirm |
| R1-21 | Console MVP | W8 | open | no console surface |
| R1-22 | Docs site + quickstart | W8 | partial | `docs/` complete as files; no built site |
| R1-23 | OPA + Cedar export | W9 | open | none |
| R1-24 | Arniko adapter with signed provenance | W9 | partial | `checks/arniko_check.rs`; direction of the contract is unpinned (see V-02) |
| R1-25 | Python + TypeScript SDKs | W9 | open | neither exists |
| R1-26 | Docker + compose + Helm + GH Action | W9 | partial | `Dockerfile.lokahi`, `docker-compose.lokahi.yml`, `deploy/helm/lokahi/`; no CI action |
| R1-27 | 3 sector policy packs | W9 | done | `tests/test_policy_packs.py` (5 tests) |
| R1-28 | Tool-description pinning + drift refusal | W1 | done | `tests/test_r1_features.py` pinning block |
| R1-29 | 60-point enterprise RFP checklist | W10 | partial | `docs/ENTERPRISE_RFP.md`; self-assessment not point-by-point |

Five `open`, ten `partial`, fourteen `done`. The `partial` rows are the honest
measure of what "enterprise-ready" meant when v1.1.0 shipped.

### V-series (new, this roadmap)

| ID | Task | WS | Target | Acceptance |
|---|---|---|---|---|
| V-01 | Canonical version 2 (RFC 8785 numbers) + versioned digest | W6 | v1.3.1 | `tests/test_canonical_jcs.py` green; v1 chain unchanged |
| V-02 | Pin the Arniko ↔ LOKAHI contract: `arniko.findings/2` in, `Decision` out, WASM ABI checksum drift refusal that names which side moved | W9 | v1.3.1 | drift test fails with a message naming the artifact |
| V-03 | `physical_vla` manifest section, signed, fail-closed on load | W5 | v1.4.0 | tampered section refuses with `unverifiable_manifest` |
| V-04 | Gate: structural, per-element freshness, polyline sweep, envelopes | W5 | v1.4.0 | property tests; keep-in vs keep-out inversion test |
| V-05 | `max_gate_eval_us` refuses on overrun | W5 | v1.4.0 | forced-slow check returns FORBIDDEN `budget_exceeded` |
| V-06 | Determinism replay: same input sequence, identical ledger root | W5 | v1.4.0 | 1,000 replays, one root |
| V-07 | Receipts + `lokahi-vla-verify`, `canon: 2` | W6 | v1.4.0 | one-byte tamper detected offline, no LOKAHI installed |
| V-08 | Latency benchmark with injection 0/50/100/250/500 ms across three execution modes | W5 | v1.4.0 | published with hardware, commit, corpus hash, reproduction command |
| V-09 | Standards mapping matrix | W11 | v1.4.1 | clause-per-rule, reviewed by one external safety engineer |
| V-10 | PAIDS-aligned receipt schema + worked reconstruction | W11 | v1.4.1 | an underwriter names the fields they would price |
| V-11 | PLC hold driver (OPC UA / Modbus) | W5 | v1.5.0 | a real PLC accepts the hold and reports it |
| V-12 | ROS 2 interlock node in Rust, tested against recorded fixtures | W5 | v1.5.0 | fixture replay in CI; no live rclpy required |
| V-13 | Reference architecture + non-claim statement | W5 | v1.5.0 | names the safety-rated controller and LOKAHI's position outside it |
| V-14 | Circle and cylinder keep-out shapes | W5 | v1.4.0-rc2 | false-clamp count falls and the Car cost reduction survives |
| V-15 | Per-step dynamic keep-outs, capped by the manifest | W5 | v1.4.0-rc2 | Button cost falls; receipt records the caller as the source |
| V-16 | Bench driver with real violation headroom | W11 | v1.4.0-rc2 | ungated arm has non-trivial cost on all three tasks |
| V-17 | Per-obstacle attribution in the harness | W11 | v1.4.0-rc2 | every charged step names the hazard that caused it |
| V-18 | `safe-control-gym` filter comparison | W11 | v1.4.1 | LOKAHI in the MPSC/CBF column, same seeds and horizon |
| V-19 | Implement or remove `execution_mode` | W5 | v1.4.0-rc2 | behaviour differs per mode and is tested, or the field is gone |

### Deferred, with the reason

| Item | Reason |
|---|---|
| Registry / virtual servers (W9) | Nobody has asked; the category consolidates into gateways |
| Go SDK, Terraform provider (W9) | Python and TypeScript cover the buyers in front of us |
| OPA / Cedar export (W9) | Different buyer, different budget, and the mapping is lossy for Rules 13–15 |
| Policy studio, simulation/replay (W8) | Valuable, but after the evidence and interlock work |
| IdP group resolution | Local group mapping satisfies the acceptance test |
| Managed / cloud SKU | An on-prem product whose pitch is that nothing leaves the building should not ship a hosted SKU. The contradiction costs more in diligence than the revenue is worth. |

---

## 5. Benchmarks we will publish (and be judged on)

| Metric | Target (now) | Target (v1.6) |
|---|---|---|
| Decision p50 / p95 / p99 (Rust host) | < 0.5 / 1 / 5 ms | < 0.3 / 0.8 / 2 ms |
| Gateway API p99 (HTTP, decision included) | < 25 ms | < 15 ms |
| Throughput per core | 5k decisions/s | 20k decisions/s |
| False-refusal rate, benign corpus | < 2% | < 1% |
| MCP attack detection (corpus v2) | ≥ 90% | ≥ 98% |
| Chain verification, 1M entries | < 5 s | < 2 s |
| VLA gate overhead (`gate_eval_us` p99) | — | < 2,000 µs |
| VLA false-clamp rate, benign chunks | — | < 2% |
| Approval round-trip (human present) | < 60 s | < 30 s |
| Air-gapped install to first decision | < 10 min | < 5 min |

All published with hardware, commit hash, corpus hash, and reproduction script.
No benchmark without a reproduction command.

---

## 6. Definition of done per release

A release ships only when all hold:

1. Every task in the release has an acceptance test that fails before and passes
   after, in the same commit as the code.
2. Public docs updated; the CHANGELOG entry states what is **not** done.
3. Threat model updated for any new surface.
4. Benchmarks re-run and published with reproduction script.
5. No open critical or high security finding.
6. Backwards compatibility statement covering manifest format, ledger format,
   canonical version, and the Arniko findings contract.
7. Air-gapped install verified in CI (container, no network).
8. One external user has run the quickstart on a clean machine.

---

## 7. Resourcing and sequencing reality

| Phase | Duration | Minimum team | Notes |
|---|---|---|---|
| v1.3.1 | 1 week | 1 | this release |
| v1.4.0 | 4 weeks | 1–2 | systems + Python; no GPU, no ROS |
| v1.4.1 | 2 weeks | 1 | safety/standards writing, not code |
| v1.5.0 | 6 weeks | 2 | embedded/safety and one integrator partner |
| v1.6.0 | 10–14 weeks | 4 | +1 SRE; SOC 2 is where a company either hires or stalls |
| v2.0.0 | 16 weeks | 4–5 | +1 crypto, +1 standards |

One person can reach v1.4.1. Two can reach v1.5.0 only with an integrator. The
v1.6 gate is the first one that requires a team, which is deliberate: it is the
first release that requires a buyer who is already paying.
