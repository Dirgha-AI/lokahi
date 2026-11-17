# Changelog

## v0.1.3 (2026-11-18): trust-boundary hardening
This release closes the gaps that let an agent influence its own approval.

### Security

- **The agent's request is data, never control.** The MCP filter refuses any `tools/call` that carries LOKAHI control metadata (`_lokahi`: consents, timeouts, reversibility, jurisdiction, storage), with reason `agent_supplied_control_metadata`. In v0.1.2 that metadata was trusted, so an agent could approve itself.
- **Signature checks are mandatory.** `evaluate()` treats a missing verification key as `unverifiable_manifest`. The CLI refuses to start without `--key-file` or `LOKAHI_MANIFEST_KEY`, and `LokahiMCPGateway` refuses to construct without a key. In v0.1.2 standalone mode skipped Rule 2.
- **Votes are bound to one action by default.** A consent counts only if it carries the digest of the exact (manifest, action, round). Unbound votes are ignored unless the caller passes `require_bound_consent=False`. `bind_consents()` is for trusted harnesses that collect votes themselves.
- **Approvals arrive out of band.** New `Approval` records are signed by each approver for one action digest, carry an expiry and a nonce, and are read from a file the agent cannot write to. Forged, expired, replayed, or duplicate-nonce approvals are ignored. `tools/lokahi_approve.py` signs approvals.
- **The gateway's clock owns the timeout.** An irreversible call opens an approval window (`approval_window_seconds`, default 900). Retrying doesn't reset it. When it closes without enough approvals, the call is refused with `non_response`.
- **Default-deny tool policy.** Manifests gain an optional `tools` table. Each tool declares its class (`read`, `write`, `irreversible`), the arguments it may take and the data feature each reveals, and where it stores data. The filter denies unknown tools (`tool_not_in_manifest:<name>`) and unknown arguments (`argument_out_of_policy:<name>`, or `forbidden_target:<name>` for a barred field). Reversibility, storage location, and jurisdiction now come from the signed policy instead of tool-name guesses or the agent's claims. A manifest without a tools table gets `no_tool_policy` for every call.
- **Approvals bind to the exact arguments.** An approval for `meter_id=M-105` doesn't unlock `meter_id=M-108`.

### Compatibility

- Manifests without the new optional fields keep the same canonical bytes, so v0.1.2 signatures still verify.
- Callers of `evaluate()` must pass a `signer`, and must bind consents (or opt out explicitly).
- The 50-scenario set, 200 benign tasks, Ghostproof, kinetic, and wargame runners all still pass. Their harnesses now bind the votes they collect.

### Other

- `tools/lokahi_sign.py` creates keys (`keygen`, owner-only permissions on POSIX, never silently overwrites) and validates and signs manifests (`manifest`). `examples/editorial-desk.manifest.json` and `examples/approvers.example.json` make the README how-to runnable as written.
- `examples/editorial_desk_demo.py` is a pilot example for a small publication's editorial desk, run end to end through the gateway.
- Unit tests went from 44 to 70. New tests cover forged and agent-supplied approvals, gateway-owned timeouts, default-deny tools and arguments, argument binding, the CLI key requirement, an end-to-end CLI run with a ledger, and the signing and approval CLIs.
- The README was rewritten around what runs today. It now explains the name (from the Hawaiian *lōkahi*, unity), says plainly that the scenarios come from fiction and that the baselines are stand-ins, and moves research prototypes into their own section.
- The `Signer` docstring no longer claims Ed25519 support. Ed25519 isn't implemented yet.

### Still open

- HMAC-SHA256 stand-in for signatures. The gateway holds approvers' keys, so it could forge approvals. Ed25519 is next.
- This is a decision filter, not a forwarding proxy. It doesn't launch or relay to the tool server and doesn't inspect tool results or tool descriptions.
- The approval count is fixed at three distinct operators.
- Approval windows live in memory, so a restart opens a new window.
- Emergency exceptions (Rule 8) are engine-only; the MCP filter doesn't accept emergency declarations.
- Disjointness is asserted, not attested. There has been no external security review.

## v0.1.2 (2026-10-29)

Reference core, 15-rule specification, MCP JSON-RPC decision filter, hash-chained ledger, 50-scenario set, and Ghostproof and kinetic research runners.
