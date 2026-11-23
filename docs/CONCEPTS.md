# Concepts — the mental model in ten minutes

LOKAHI answers one question, repeatedly, for one action at a time:

> **May this agent take this specific action, now, with authority from the
> people the rules name?**

Everything else in the system exists to make that answer deterministic,
auditable, and safe when it cannot be given.

---

## 1. The manifest is the community's law

A signed JSON document. It states:

| Field | Meaning |
|---|---|
| `node` | the thing being protected (a desk, a utility, a robot) |
| `informational` | data features the agent may read |
| `forbidden_targets` | features it must never read or optimise for |
| `jurisdiction` | whose rules apply to actions here |
| `residency` | where derived data may be stored |
| `exceptions` | emergency rate cap and window |
| `expires` | when the manifest stops being valid |
| `steward` | the body that survives staff turnover |
| `tools` | per-tool policy: class, arguments, storage, physical flag |
| `consent_threshold` | `{m, n}` — how many distinct people must sign |
| `approval_window_seconds` | how long a consent round stays open |
| `physical_envelope` | force, torque, velocity, workspace bounds |

It is signed with HMAC-SHA256 (legacy) or Ed25519. An unsigned, unverifiable, or
expired manifest refuses everything. That is Rule 1–3 and it is not negotiable.

**Why a manifest and not a prompt?** A prompt is a request. A signed manifest is
an artefact the gate can check, an auditor can read, and a court can rely on.

---

## 2. The layers, and why the order matters

```
fail-secure gate ──────────────────► refuse everything while locked
        │
        ▼
tool ACL ──────────────────────────► may this consumer reach this tool?
        │
        ▼
tool pinning ──────────────────────► did the tool or policy change after review?
        │
        ▼
Layer 1: WASM pre-flight ──────────► blocklists, schema, rate limit, arniko
        │
        ▼
manifest rules (1–3) ──────────────► signed, verifiable, unexpired
        │
        ▼
15-rule engine ────────────────────► targets, features, residency, conflicts
        │
        ▼
threshold consent ─────────────────► m-of-n signatures, silence = refusal
        │
        ▼
merge + ledger + evidence ─────────► stricter wins; everything recorded
```

Each layer fails closed. Nothing downstream can rescue a refusal upstream.

**The merge rule (learned the hard way).** Consent never relaxes a safety
refusal. It may only resolve a verdict that exists *because the quorum was not
met*. The audit found the opposite behaviour in an early build: a single
validator could approve a 999 N crush force outside a 6.1 N envelope. That is now
a property test.

---

## 3. Consent: what "disjoint" actually means

A quorum counts **distinct operators**, not agents. One operator running twelve
agents is one vote.

- Each validator holds their own Ed25519 key. The gate holds only public keys.
- An approval is bound to the exact action digest: tool name plus argument
  values. Approving `meter_id=M-105` does not approve `meter_id=M-108`.
- Approval carries an expiry and a nonce; replays and duplicates do not count.
- Two dissenters freeze the action regardless of how many say yes.
- A closed window with fewer than `m` signatures is a refusal (`non_response`).
- The agent cannot approve itself; the operator running the agent cannot approve
  it; an emergency declarant cannot vote on their own scope.
- Equivocating (yes *and* no in the same ring) disqualifies a "yes".

**What it does not prove.** That the keys belong to different *people*. That is
disjointness attestation, roadmap item R3. Today, distinct IDs are trusted.

---

## 4. Fail-secure, not fail-open

Any exception in any layer revokes the execution privilege. While revoked:

- every call is refused with `secure_state_active:<reason>`
- `/readyz` returns 503
- an actuator command resolves to `safe_mechanical_hold`, not "no answer"

The system stays locked until a human re-arms it. Re-arming requires a signature
recorded in the ledger. Nothing auto-re-arms.

The WebXR broker carries the human handoff: session, audited teleop actions, and
a re-arm request. It does not pretend WebAuthn is implemented where it is not.

---

## 5. Evidence, not just logs

A decision is written to a hash-chained ledger. Each entry commits to the one
before it, so editing entry 4 breaks the chain from 4 onwards.

An **evidence pack** is a self-contained bundle for one decision:

- the decision entry
- the previous entry (to prove the link)
- the signed manifest
- the action digest
- the approvals that counted

Its verifier runs **without LOKAHI installed** and checks six things: the entry
hash, the chain link, the manifest signature, that approvals name the digest,
each approval signature, and an optional pack signature.

It also records what it does **not** prove, because an auditor will ask.

---

## 6. Pinning: the rug-pull problem

A server can change a tool's description after you reviewed it. MCP tells clients
to treat annotations and descriptions as untrusted.

Pinning splits the problem:

1. `lokahi_toolpin.py pin` records the hash of each approved tool schema and its
   manifest policy, in a signed pin set.
2. `lokahi_toolpin.py verify` compares a fresh `tools/list` against the pins and
   reports added, removed, and changed tools.
3. The gateway compares the *manifest* against the pins on every call and refuses
   `tool_policy_drift` if a policy changed after review.

It detects drift, not deception at call time. Say so.

---

## 7. Two transports, one decision engine

- **stdio** — a line of JSON-RPC in, a decision out. For desktop and CI.
- **HTTP** — `POST /mcp`, JSON or SSE, stateless (no session id). Health,
  readiness, Prometheus metrics, RFC 9728 metadata, and OAuth bearer validation.

The engine is transport-agnostic. That is deliberate: the protocol will keep
changing; the decision should not.

---

## 8. What the SLM tier is, and is not

There is an optional classifier that reviews calls the deterministic layers
allowed and may only make them stricter.

It was trained on synthetic data, its labels are not human-reviewed, and it has
never seen a real deployment. It is **excluded from the safety claim**. If it
fails, the call escalates.

Do not describe it as a judge until it has been measured on real traffic.

---

## 9. The one-sentence version

A signed rulebook, checked deterministically, with authority from several
independent people or none, recorded so an outsider can verify it, and locked to
a human hand when the machine cannot be sure.

---

## 10. Architectural comparison: where LOKAHI fits

| Capability | Standard API Proxies (Kong, Envoy) | Single-Approver HITL (Permit, Docker Sandbox, TrueFoundry) | LOKAHI Sovereign Gate |
|:---|:---|:---|:---|
| **Primary Domain** | Network ingress, rate-limiting, CORS | Single human-in-the-loop click | **Autonomous agent consequential tool actions** |
| **Approval Protocol** | None (traffic pass-through) | Single operator timeout-reject | **Disjoint $m$-of-$n$ human quorum** with requester conflict exclusion |
| **Failure Mode** | Fail-open / fallback error | Timeout error | **Fail-closed ("Silence = Refusal")** |
| **Audit Ledger** | Application database log (mutable) | Audit API log | **Tamper-evident SHA-256 hash-chained, Ed25519-signed ledger** |
| **Physical Safety** | None | None | **Physical safety envelopes & safe mechanical holds (stopping barriers)** |
| **Deployment Posture** | Cloud-native / SaaS | Cloud Control Plane | **Air-gapped Public Digital Infrastructure (DPI) & zero-cloud local** |
