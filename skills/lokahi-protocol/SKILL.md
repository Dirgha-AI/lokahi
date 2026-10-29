---
name: lokahi-boundary-protocol
version: 0.1.2
description: "Pre-flight boundary enforcement, manifest verification, and disjoint-consent protocol for autonomous agent tool execution and physical actuation."
author: "Salik Shah / Dirgha-AI"
license: "Apache-2.0"
spec_version: "agentskills.io/v1"
tags: ["ai-safety", "multi-agent", "governance", "mcp-gateway", "boundaries", "cooperative-ai"]
---

# LOKAHI Boundary Enforcement Protocol

Autonomous agent collectives coordinate across data sources, APIs, and physical actuators. When an agent acts without verified boundaries, failures cascade: silence is assumed to be permission, single root keys get compromised, and agents collude against public interest.

This skill provides autonomous agents with a deterministic, machine-checked protocol for **verifying boundary manifests**, **evaluating pre-flight tool calls**, **soliciting disjoint operator consent**, and **committing decisions to an immutable hash-chained ledger**.

---

## 1. Input Contract

Every tool execution evaluated under LOKAHI must supply an `Action` and a counterparty `Manifest`:

### Action Schema
```json
{
  "requester": "string (node identifier initiating the action)",
  "requester_operator": "string (entity controlling the requesting node)",
  "tool": "string (name of the tool, API, or physical actuator)",
  "features": ["string (features or data columns to be read)"],
  "jurisdiction": "string (ISO jurisdiction code where action takes effect)",
  "reversible": "boolean (false if state change cannot be cleanly undone)",
  "population_affecting": "boolean (true if action impacts multiple unrepresented parties)",
  "stores_in": ["string (jurisdictions where output or derived data will be stored)"],
  "declared_by": "string | null (set if emergency exception is claimed)"
}
```

### Manifest Schema
```json
{
  "node": "string (unique identifier of the boundary-declaring node)",
  "informational": ["string (features permitted to be read)"],
  "forbidden_targets": ["string (optimization targets barred outright)"],
  "jurisdiction": "string (governing legal jurisdiction, e.g. 'IN-DL')",
  "residency": ["string (allowed data storage jurisdictions)"],
  "exceptions": { "safety_critical": "integer", "window_hours": "integer" },
  "expires": "string (ISO 8601 timestamp)",
  "steward": "string (DID URI of the surviving steward)",
  "signature": "string (cryptographic signature over canonical JSON)"
}
```

---

## 2. Output Contract

Every evaluation emits a deterministic `Decision` object:

```json
{
  "verdict": "compliant | escalate | forbidden",
  "reasons": ["string (ordered rule failure or success identifiers)"],
  "quorum_required": "integer (number of distinct operators needed)",
  "quorum_met": "integer (distinct operator yes-votes verified)",
  "allowed": "boolean (true strictly if verdict == 'compliant')"
}
```

---

## 3. Standard Operating Procedure (SOP)

Agents operating under LOKAHI must follow this five-step sequence before invoking any external tool or actuator:

### Step 1: Pre-Flight Manifest Verification
1. Resolve the counterparty node's manifest via `did:web` or local node registry.
2. Canonicalize the manifest JSON (keys sorted, whitespace stripped, UTF-8).
3. Verify signature: if signature is absent or invalid, emit `FORBIDDEN: unsigned_manifest` or `unverifiable_manifest`.
4. Check expiration: if `expires <= current_time`, emit `FORBIDDEN: expired_manifest`.

### Step 2: Information & Jurisdiction Scrubbing
1. Check requested features against `manifest.forbidden_targets`. If intersection exists, emit `FORBIDDEN: forbidden_target:<name>`.
2. Check requested features against `manifest.informational`. Any unlisted feature emits `FORBIDDEN: feature_out_of_manifest:<name>`.
3. Check `action.jurisdiction == manifest.jurisdiction`. If mismatched, emit `FORBIDDEN: jurisdiction_mismatch`.
4. Check `action.stores_in` is a subset of `manifest.residency`. Any unlisted residency emits `FORBIDDEN: residency_violation:<name>`.

### Step 3: Quorum & Consequence Analysis
1. Determine if action is high-consequence: `needs_quorum = (!action.reversible || action.population_affecting)`.
2. If `!needs_quorum`, emit `COMPLIANT: within_manifest` and proceed to Step 5.
3. If `needs_quorum`, open a consent ring requiring **3 disjoint operators**.

### Step 4: Consent Ring Adjudication
1. **Suspension Veto Check:** Collect responses. If **>= 2 distinct operators vote "no"**, emit `FORBIDDEN: suspended_by_ring` immediately.
2. **Conflict of Interest Exclusion:** Disqualify any signature where:
   - `consent.node == action.requester` (`FORBIDDEN: requester_in_ring`)
   - `consent.operator == action.requester_operator` (`FORBIDDEN: requester_operator_in_ring`)
   - `consent.operator == action.declared_by` (`FORBIDDEN: declaring_party_in_ring`)
3. **Sybil Filtering:** Collapse all agents controlled by the same operator into exactly one vote (`distinct_operators`).
4. **Timeout Refusal:** If review window expires with `< 3` verified yes-votes, emit `FORBIDDEN: non_response`. Silence is never permission.
5. If `< 3` yes-votes and window active, emit `ESCALATE: insufficient_disjoint_consent`.
6. If `>= 3` distinct yes-votes, emit `COMPLIANT: quorum_met`.

### Step 5: Append to Hash-Chained Ledger
1. Construct ledger entry: `seq`, `ts`, `node`, `requester`, `tool`, `verdict`, `reasons`, `quorum_required`, `quorum_met`, `prev`.
2. Compute `hash = sha256(canonical(entry))`.
3. Commit entry to local transparency log.

---

## 4. Edge Cases and Defensive Rules

| Edge Case | Protocol Behavior | Why It Matters |
|---|---|---|
| **Operator fields 50 sub-agents** | Counted as **1 vote**. | Prevents sybil swarm attacks from manufacturing consensus. |
| **Emergency Declared** | Declaring operator cannot vote on their own emergency grant. | Prevents self-dealing and fabricated crises. |
| **Network Partition / Timeout** | Action evaluates strictly to `FORBIDDEN: non_response`. | Prevents default-to-go behavior when human oversight drops offline. |
| **Physical Actuation >5 N** | Treated as `reversible: false`, triggering mandatory 3-body quorum. | Prevents runaway mechanical actuators from damaging physical hardware. |
| **Dissenting Minority (2 of 3)** | Triggers affirmative suspension (`suspended_by_ring`). | Ensures two vigilant nodes can freeze suspected collusion or runaway agents. |

---

## 5. Verification Command

To verify compliance with this specification:
```bash
python3 tools/run_evals.py
```
Expected output: **50/50 test scenarios passing (100.0%)**.
