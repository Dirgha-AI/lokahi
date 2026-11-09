# LOKAHI Boundary Manifest and Decision Semantics, v0.1.2

Status: Specification draft for adversarial review. Built on the Arniko security rule engine.

## 1. Manifest

A node publishes one signed JSON document. Every field is required.

| Field | Type | Meaning |
|---|---|---|
| `node` | string | Stable identifier for the node (e.g. `egz-17-water-board`) |
| `informational` | list of strings | Features the node permits others to read |
| `forbidden_targets` | list of strings | Optimization targets barred outright, whatever else is permitted |
| `jurisdiction` | string | Whose rules apply to actions taken on this node's data/actuators |
| `residency` | list of strings | Jurisdictions where derived data or telemetry may be stored |
| `exceptions` | object | Named exception with rate/window caps (e.g. `{"safety_critical": 3, "window_hours": 24}`) |
| `expires` | ISO 8601 string | Expiration instant (with timezone); expired or unreadable manifests deny all requests |
| `steward` | string | Designated steward URI (`did:web`) surviving the principal |
| `signature` | string | Cryptographic signature over canonical JSON |
| `physical_envelope` | object (optional) | Embodied robotics bounds (`max_contact_force_n`, `max_joint_torque_nm`, `max_tendon_tension_n`, `max_cartesian_velocity_ms`, `workspace_bounds_m`, `timeout_interlock_mode`) |

Canonical JSON (RFC 8785 JCS subset) requires sorted keys, whitespace stripping (separators `(",", ":")`), and UTF-8 encoding. Any manifest that fails to parse, fails verification, or carries an unreadable or timezone-naive expiry is treated as expired.

## 2. Decision Order (15 Normative Rules)

Denials are ordered by cost and certainty so that no downstream rule can rescue a forged, expired, or out-of-scope manifest.

1. `signature` empty → **forbidden** `unsigned_manifest`
2. `signature` does not verify → **forbidden** `unverifiable_manifest`
3. `expires` in the past, timezone-naive, or unreadable → **forbidden** `expired_manifest`
4. any requested feature in `forbidden_targets` (or `damping_kd <= 0`) → **forbidden** `forbidden_target:<name>`
5. any requested feature absent from `informational` (or `physical_params` outside `physical_envelope`) → **forbidden** `feature_out_of_manifest:<name>` / `physical_*_exceeded`
6. `action.jurisdiction` differs from `manifest.jurisdiction` → **forbidden** `jurisdiction_mismatch`
7. `action.stores_in` contains any target outside `manifest.residency` → **forbidden** `residency_violation:<name>`
8. `action.declared_by` is set and `max(action.exceptions_used, ledger.count_exceptions_in_window()) >= manifest.exceptions["safety_critical"]` → **forbidden** `exception_rate_exceeded`
9. `action.reversible` is true, `action.population_affecting` is false, and `action.declared_by` is null → **compliant** `within_manifest`
10. **Rule 13 (Evaluated prior to conflict rejection so a compromised caller cannot mask a safety freeze):** two or more distinct, non-conflicted operators vote `"no"` → **forbidden** `suspended_by_ring` (2-of-3 suspension veto)
11. **Rule 10:** any ring participant has `node == action.requester` → **forbidden** `requester_in_ring`
12. **Rule 11:** `action.requester_operator` is set and any ring participant has `operator == action.requester_operator` → **forbidden** `requester_operator_in_ring`
13. **Rule 12:** `action.declared_by` is set and any ring participant has `operator == action.declared_by` → **forbidden** `declaring_party_in_ring`
14. **Rule 14 (*The Ahimsa Timeout Rule*):** `timed_out` is true with fewer than three distinct non-equivocating operators voting `"yes"` → **forbidden** `non_response`
15. **Rule 15:** fewer than three distinct non-equivocating operators voting `"yes"` → **escalate** `insufficient_disjoint_consent`; otherwise → **compliant** `quorum_met`


### 2.1 Normative Design Invariant: *Ahimsa* as a Timeout Rule (`Rule 14`) & Collective Restraint (`Rule 13`)

Global AI safety frameworks frequently exhibit *contextual blindness* when deployed over public infrastructure and municipal utilities ([Aggarwal et al., *ASTRA: AI Safety, Trust, & Risk Assessment*, `arXiv:2602.17357`, 2026](https://arxiv.org/abs/2602.17357)). In LOKAHI, the civilizational principle of ***ahimsa* (non-injury)**—and India's MeitY *"Do No Harm"* AI governance mandate—is operationalized not as a prompt-level instruction, but as three non-bypassable state-machine invariants:
1. **Fail-Closed Verification (`Rules 1–3`):** An unsigned (`unsigned_manifest`), cryptographically unverifiable (`unverifiable_manifest`), or expired (`expired_manifest`) boundary manifest never falls back to a permissive default.
2. **Collective Restraint Veto (`Rule 13: suspended_by_ring`):** Any two distinct, non-conflicted operators voting `"no"` immediately freeze an irreversible or emergency-declared action, overriding unilateral urgency.
3. ***Ahimsa* Timeout Refusal (`Rule 14: non_response`):** If a deliberation window expires (`timed_out == True`) before three distinct, non-equivocating operators affirmatively vote `"yes"`, the gateway must return `forbidden` with reason `non_response`. Silence is never interpreted as consent.

## 3. Quorum, Equivocation, and Disjointness

- **Quorum Requirement:** Three distinct operators with disjoint control (`QUORUM = 3`).
- **Operator Counting (Anti-Sybil):** Agents are not operators. One operator running twelve sub-agents holds one vote. Empty or whitespace-only operator/node strings are ignored.
- **Equivocation Disqualification:** An operator that submits conflicting votes (`"yes"` and `"no"`) within the same consent ring is disqualified from counting toward affirmative `"yes"` quorum, while their dissent still registers.
- **Conflict-of-Interest Exclusion:** Requesters (`requester_in_ring`), requesting operators (`requester_operator_in_ring`), and emergency declarants (`declaring_party_in_ring`) are barred from participating in the consent ring in any capacity.
- **Suspension Veto:** Two distinct non-conflicted operators voting `"no"` triggers an immediate safety freeze (`suspended_by_ring`).
- **Silence Counts as Denial:** Timeouts resolve strictly to `non_response` denial; silence is never permission.

## 4. Hash-Chained Audit Ledger

Each decision commits to the previous entry's SHA-256 hash:
```
seq, ts, node, requester, tool, verdict, reasons,
quorum_required, quorum_met, prev, hash
```
`hash = sha256(canonical(entry without hash))`. Any retroactive edit, deletion, or reordering breaks the chain from that index onward.

## 5. Open Questions

1. **Key rotation and revocation:** Recording key fingerprints inside the hash chain so steward key rotation remains verifiable across historic ledger entries.
2. **Revocation propagation:** Short manifest TTLs paired with an append-only transparency log and witness cosigning so stale caches fail closed within a bounded window.
3. **Liveness and quorum substitution:** Specifying an auditable quorum-substitution path for safety-critical hospital or utility nodes when a steward is unreachable, without allowing silence to become implicit consent.
4. **Disjointness attestation graph:** Moving `"disjoint control"` from a caller-supplied assertion to an attested graph over legal registration, key custody, and infrastructure ownership.
5. **Signature back end:** Swapping the `Signer` class from HMAC-SHA256 (used in the zero-dependency demo so one machine can play both sides) to Ed25519 and RFC 9591 FROST threshold signatures.
