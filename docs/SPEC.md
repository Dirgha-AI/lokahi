# LOKAHI Boundary Manifest and Decision Semantics, v0.1.3

Status: Specification draft for adversarial review. See `CHANGELOG.md` for what changed in v0.1.3.

## 1. Manifest

A node publishes one signed JSON document. The fields below are required unless marked optional. Optional fields are left out of the canonical form when unset, so v0.1.x manifests keep the same signed bytes.

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
| `tools` | object (optional, v0.1.3) | Per-tool policy, required by the MCP filter. Each entry has `class` (`read`, `write`, `irreversible`) and `args` (argument name → the `informational` feature it reveals, or `null`). Optional keys are `stores_in`, `jurisdiction`, `population_affecting`, and `physical` |
| `approval_window_seconds` | integer (optional, v0.1.3) | How long an approval round stays open before Rule 14 refuses the call (default 900) |

Canonical JSON (RFC 8785 JCS subset) requires sorted keys, whitespace stripping (separators `(",", ":")`), and UTF-8 encoding. Any manifest that fails to parse, fails verification, or carries an unreadable or timezone-naive expiry is treated as expired.

## 2. Decision Order (15 Normative Rules)

Denials are ordered by cost and certainty so that no downstream rule can rescue a forged, expired, or out-of-scope manifest.

1. `signature` empty → **forbidden** `unsigned_manifest`
2. `signature` does not verify, **or no verification key is available** (v0.1.3) → **forbidden** `unverifiable_manifest`
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
- **Vote Binding (v0.1.3):** A consent counts only if its `bound_action_digest` equals `compute_action_digest(manifest, action, round_nonce)`. Unbound votes are ignored by default (`require_bound_consent=True`). Trusted harnesses that collect votes themselves use `bind_consents()`.

## 3a. MCP Decision Filter (v0.1.3)

The filter treats the agent's `tools/call` request as data, never as control.

1. **Manifest first.** Rules 1–3 run before anything else. The filter requires a verification key and refuses to start or construct without one.
2. **No control metadata from the agent.** A request whose `params` or `arguments` contain `_lokahi` is refused: **forbidden** `agent_supplied_control_metadata`.
3. **Default-deny tool policy.**
   - No `tools` table: **forbidden** `no_tool_policy`.
   - Tool not listed: **forbidden** `tool_not_in_manifest:<name>`.
   - Argument not listed: **forbidden** `forbidden_target:<name>` if the name is a barred target, otherwise `argument_out_of_policy:<name>`.
4. **Facts come from the signed policy.**
   - `features` are the features mapped from the arguments present.
   - `reversible` is false only for `class: irreversible`.
   - `stores_in`, `jurisdiction`, and `population_affecting` come from the policy entry.
   - Physical parameters are read only for `physical: true` tools.
5. **Approvals are out of band.** Each `Approval` record (`operator`, `node`, `decision`, `action_digest`, `expires`, `nonce`, `signature`) is signed by the approver's own key and read from a store the agent cannot write to. An approval counts only if all of these hold:
   - the approver is registered,
   - the signature verifies,
   - the digest matches this call,
   - it hasn't expired,
   - its nonce is new.

   The round nonce binds approvals to the exact call (tool name plus argument values).
6. **The gateway's clock owns timeouts.** An irreversible or population-affecting call opens a window of `approval_window_seconds` (default 900) the first time it is seen. Retrying doesn't reset it. A call at or after the close, still short of quorum, is **forbidden** `non_response`.
7. **Every decision is recorded,** denials included, in the hash-chained ledger.

Not covered in v0.1.3: forwarding to the tool server, inspecting tool results or tool descriptions, emergency declarations through the filter, and persistence of open windows across restarts.

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
5. **Signature back end:** Swapping the `Signer` class from HMAC-SHA256 (used in the zero-dependency reference so one machine can play both sides; the verifier can also forge) to Ed25519 for manifests and approvals, then RFC 9591 FROST threshold signatures.
6. **Per-tool approval counts:** Moving `QUORUM = 3` into the manifest so a small team can require one approver for an email and more for irreversible public actions.
