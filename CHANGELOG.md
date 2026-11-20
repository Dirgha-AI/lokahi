# Changelog

## Unreleased — R1 (v1.1)

- **R1-01 Streamable HTTP transport** (`tools/lokahi_http.py`): one `/mcp`
  endpoint accepting JSON-RPC over POST, replying with JSON or a single-message
  `text/event-stream`; stateless (no session id, per the 2026-07-28 revision).
  `/healthz`, `/readyz` (503 while the fail-secure lock is engaged) and
  Prometheus `/metrics`. Bearer-token allowlist; the CLI refuses to bind a
  non-loopback address without one until OAuth 2.1 resource-server support
  (R1-02) lands. Batch requests are rejected explicitly rather than
  half-supported. CLI: `--http-listen HOST:PORT`, `--http-token`. 8 tests.
- **R1-28 tool pinning** (`tools/lokahi_toolpin.py`): signed pin sets record the
  hash of each tool's approved schema and its manifest policy. `pin`, `verify`
  and `check-manifest` commands; drift in a catalogue (added/removed/changed
  schema) and drift in a manifest policy are both reported. The gateway refuses
  `tool_not_pinned:<name>` and `tool_policy_drift:<name>` when a pin set is in
  force. CLI: `--tool-pins`, `--require-pins`. 11 tests.
- **R1-15 evidence packs** (`tools/lokahi_evidence.py`): a self-contained,
  offline-verifiable bundle per decision. The standalone verifier checks the
  decision hash, the chain link to the previous entry, the manifest signature,
  that every approval names the action digest, every approval signature against
  supplied validator keys, and an optional pack signature. CLI: `build` and
  `verify`. 10 tests.
- Ledger entries now record `action_digest` (via `Ledger.append(..., extra=...)`)
  so evidence packs need no side-channel data. Older entries are unchanged and
  still verify.

## Unreleased — planning

- **`ROADMAP.md`** — phased plan to open-source SOTA: competitive parity matrix
  across 12 vendors, 10 workstreams, releases R0–R4 with acceptance criteria,
  published benchmark targets, resourcing and exit gates.
- **`STRATEGY.md`** — full audit, use cases, ICP, enterprise and investor
  perspectives, gaps, competitive analysis, falsification tests.
- **`AUDIT_REPORT.md`**, **`AUDIT_TASKS.md`** — pre-release audit of record and
  its remediation tracking.
- **`TASKS.md`** — rewritten as the living task board aligned to the roadmap.

## v1.0.1 (2026-11-19): multi-layered cascading enforcement runtime

This release re-architects LOKAHI from a single Python decision filter into a multi-layered cascading enforcement runtime. It supersedes the unreleased v1.0.0 build, which failed pre-release audit.

> **Audit status.** A pre-release audit (`AUDIT_REPORT.md`) found four critical
> defects. They are fixed below and covered by `tests/test_audit_regressions.py`.
> The release is not production-ready until the remaining items in
> `AUDIT_TASKS.md` are closed and the external review checklist is signed.

### Post-audit remediation

**C1 — consent no longer bypasses the 15-rule engine.**
`_intercept_inner` now always calls `evaluate()`. A new `merge_threshold_consent()`
helper enforces the precedence: an engine refusal for any reason other than its own
quorum count is absolute, and consent can only promote
`ESCALATE(insufficient_disjoint_consent)` to `COMPLIANT`. Threshold signatures are
also fed into the engine as bound `Consent` records so conflict-of-interest and
dissent rules see the same ring. Previously a single colluding validator could
approve a residency violation and a 999 N crush outside a 6.1 N envelope.
Regression tests: `TestC1ConsentCannotBypassRules` (9 tests).

**C2 — privilege drop is enforced.**
`_intercept_inner` denies every request with `secure_state_active:<reason>` while
`FailSecureManager.has_privilege()` is false. Previously the lock was advisory.
Regression tests: `TestC2PrivilegeDrop` (3 tests, including exception → lock).

**C3 — the documented Ed25519 CLI flow works.**
`main()` loads the manifest verifier with `load_manifest_verifier()`, which prefers
the `<key>.pub` file written by `keygen --ed25519`, derives from the private seed if
needed, and falls back to HMAC for v0.1.x keys. Previously the gateway refused to
start with `unverifiable_manifest`.
Regression tests: `TestC3CLIFlows` (2 end-to-end CLI tests).

**C4 — the package builds.**
`pyproject.toml` is valid TOML with console scripts under `[project.scripts]`;
`pip install --dry-run .` succeeds. `tools/wasm_preflight/target/` is gitignored
(309 MB of build artifacts were previously untracked).

**H1 — manifests above 4 KB no longer refuse every call.**
`wasm_bridge.py` bounds are now 64 KiB per request and 4 MiB per manifest
(configurable), and the previous fixed 4096-byte offsets are gone. A 40-tool
manifest (~8.9 KB) now returns `ALLOW` for a legitimate call.

**H2 — the WASM bridge uses the module allocator.**
The crate exports `lokahi_alloc`/`lokahi_free`, and `preflight_check` returns a
freshly allocated JSON buffer that the host frees after reading. Nothing is
written at fixed offsets, so the module's data segment cannot be overlapped, and
result truncation is impossible. Verified over 6,000 sequential calls: linear
memory plateaus at 2.55 MB with no leak.

**H3 — one dissent threshold.**
`ConsentFramework` takes `suspension_quorum`, defaulting to the engine's
`SUSPENSION_QUORUM`, so the two implementations cannot drift.

**M-items — hardening and honesty.**
- Lifecycle events (fail-secure transitions, WebXR sessions, teleop actions,
  re-arms) now append through a new `Ledger.append_event()`, so they chain onto
  the same hash sequence as tool-call decisions and carry the real node id.
  The previous implementation appended a dummy manifest signed with a hardcoded
  key.
- Ledger file writes are serialized by a process lock, flushed, and fsynced by
  default (`LOKAHI_LEDGER_FSYNC=0` opts out). One gateway writer per ledger file.
- The WASM rate-limiter table is capped at 4096 buckets and fails closed when
  saturated instead of growing without bound.
- `SLMJudge.predict_proba_one` delegates to the Python model or raises
  `NotImplementedError`; it no longer fabricates a one-hot distribution.
- Dead code removed: `ThresholdApprovalStore`, `fail_secure_boundary`,
  `PreflightVerdict`.
- Malformed arguments are refused before Layer 1, so the pre-flight and the
  final decision always evaluate identical bytes.
- Version banners corrected to v1.0.0.

### Stage 1: WASM Deterministic Pre-Flight (Layer 1)
- **`tools/wasm_preflight/`** — Rust crate compiled to WebAssembly (`wasm32-unknown-unknown`, 18 KB release): regex allowlist/blocklist, JSON Schema validation against the signed manifest per-tool policy, token-bucket rate limiting per (agent, tool), manifest signature/expiry pre-check, and arniko scanner finding ingestion. 22 unit tests, all passing. Sub-millisecond deadline enforcement (MAX_BUDGET_US = 500).
- **`tools/wasm_bridge.py`** — Python bridge (Wasmtime) that loads and executes `preflight.wasm`, exposing `check(input_dict, manifest_dict) -> PreflightVerdict`. Host supplies `now_us` (WASM cannot read the system clock).
- **`tools/arniko_client.py`** — Integrated into `arniko_check.rs` within the WASM crate: maps arniko scanner findings to LOKAHI reason codes (DDM-04/DDM-08 etc.) as Layer 1 inputs.
- **MCP gateway integration** — `LokahiMCPGateway.intercept()` runs Layer 1 WASM checks before the 15-rule engine; a Layer 1 REFUSE is a hard refuse with ledger entry and no SLM call. CLI flags `--preflight-wasm <path>` and `--require-preflight`.
- All existing tests continue to pass.

### Stage 2: SLM Semantic Judges (Layer 2)
- **`tools/decisions/slm_judge.py`** — `SLMJudge` class with ONNX/GGML/MLX backends and pure-Python `JsonDecisionModel` fallback. Implements the same interface as `JsonDecisionModel` so it plugs directly into `DecisionTier`. Inference timeout → ESCALATE (fail closed). Stricter-only guarantee enforced by `combine()`.
- Charter rules (`hard_rules()` in `charter.py`): DDM-02 (timeout → REFUSE), DDM-05 (residency → REFUSE), DDM-01 (irreversible → ESCALATE) checked in plain code before the model.
- `combine()` in `tier.py`: stricter-of-two verdict combination; SLM can escalate/refuse a LOKAHI-allowed call, never relax it.
- MCP gateway integration: `LokahiMCPGateway(slm_judge=...)`; CLI flags `--slm-model <path>` and `--slm-timeout <seconds>`. SLM import is lazy (no decisions tier import at module load).

### Stage 3: Multi-Party Threshold Consent
- **`tools/lokahi_consent.py`** — `ConsentFramework` over m-of-n **independent Ed25519 signatures** (not FROST threshold signing): each validator holds its own key pair and signs the action digest; the gate holds only public keys and counts distinct valid signatures (default 2-of-3).
- **Veto-on-silence** — Consent window deadline; fewer than `m` signatures before close → `non_response` REFUSE. Two dissenting validators → `suspended_by_ring` freeze.
- **Conflict-of-interest** — Agent, operator, and emergency declarant excluded from consent ring. Unregistered operators, expired approvals, reused nonces, and wrong digests all ignored.
- **`Manifest.consent_threshold`** — new optional field `{"m": 2, "n": 3}`, omitted from canonical form when unset (backward compatible with v0.1.x signatures).
- **CLI** — `lokahi_sign.py keygen --threshold m --n n --out-dir dir` (n validator key pairs + `approvers.json`); `keygen --ed25519` (Ed25519 manifest key with `ed25519:` prefix); `lokahi_approve.py` threshold signing per validator device, records `signature_algorithm`.
- MCP gateway integration: `LokahiMCPGateway(consent_framework=...)`; CLI flags `--validator-keys <path>` and `--require-consent-framework`.
- End-to-end verified: no consent → ESCALATE; 2-of-3 yes → COMPLIANT; 2 no → suspended_by_ring.

### Stage 4: Fail-Secure State & WebXR Teleoperation
- **`tools/lokahi_failsecure.py`** — `FailSecureManager` with privilege drop on any pipeline failure; secure default state (no action until human re-arm); `handle_exception()` routes any cascade layer exception to secure state; `rearm()` requires cryptographic re-arm signature; `status()` returns `secure_state`, `teleop_active`, `rearmed_at`, `privilege_revoked`, `transition_count`.
- **`tools/lokahi_webxr.py`** — `WebXRTeleopBroker` with `trigger_teleoperation()`, `initiate_session()`, `record_teleop_action()` (audited telepresence stream), `request_rearm()`, `release_session()`; all events recorded to the tamper-evident hash-chained ledger.
- **MCP gateway integration** — `intercept()` wraps `_intercept_inner()` in try/except; any exception → `failsecure.handle_exception()` + WebXR trigger; `--webxr-endpoint <url>` CLI flag; a fail-secure gate at the top of the request path denies every call while privileges are revoked.
- End-to-end verified: exception → secure state → WebXR teleop → re-arm → operational, ledger chain intact throughout.

### Stage 5: Integration, Testing & Audit
- Full cascade end-to-end verified: WASM pre-flight → 15 rules → threshold consent → ledger.
- `pyproject.toml` v1.0.0 with `cryptography` dependency, `wasm`/`slm`/`dev` optional extras, entry points for `lokahi-sign`, `lokahi-approve`, `lokahi-gateway`.
- Build the WASM artifact with `cargo build --target wasm32-unknown-unknown --release`; it is not committed (build output).
- `docs/ARCHITECTURE.md` updated with cascade + consent + fail-secure + WebXR.
- `docs/WEBXR_FALLBACK.md` — teleoperation runbook.
- `docs/THREAT_MODEL.md` — attack surfaces and mitigations per layer.
- `docs/FUZZING_PLAN.md` — WASM pre-flight fuzzing **plan**; the fuzz targets described there are not yet written.
- `docs/EXTERNAL_REVIEW_CHECKLIST.md` — Ed25519, consensus, disjointness attestation review items (unsigned).

## v0.1.4 (2026-11-19): experimental decisions tier

This release adds an optional learned layer after LOKAHI's own decision. It is off by default, and with it off LOKAHI behaves as in v0.1.3.

### Added

- **`tools/decisions/`**, an experimental decisions tier. A logistic-regression model over word and character n-grams reads calls LOKAHI has allowed and answers ALLOW, ESCALATE or REFUSE with a reason code from `docs/decisions/CHARTER.md` (DDM-00 to DDM-14). LOKAHI decides first, charter rules checked in code come next, and the model runs last. The model can only make a decision stricter.
- **Opt-in switches.** `--decisions experimental` on `tools/mcp_stdio_proxy.py` and `experimental_decisions=True` (or a `decisions.DecisionTier`) on `LokahiMCPGateway`. The default is off, and `decisions` isn't imported unless the tier is on.
- **Plain-JSON model, standard-library inference.** `tools/decisions/model.json` holds the vocabularies, idf weights, coefficients, intercepts, feature settings, threshold, label order, model version and training-data hashes. `tools/decisions/model.py` re-implements the featurisation and softmax without numpy or scikit-learn. No pickle or joblib files.
- **Optional training.** `tools/decisions/train.py` (scikit-learn) retrains, exports and checks the JSON model against scikit-learn. `tools/decisions/corrections.py` applies review corrections (drop or relabel) before retraining.
- **Data.** `data/decisions/` has 995 synthetic seed rows, 50 hand-written hard test rows, the schema, the validation report, every generation prompt and raw output, and a data card. CC BY 4.0. The rows were generated with `numes/deepseek/deepseek-v4.1-flash:free` through opencode on 19 Nov 2026, and the labels were written by that model.
- **Tests.** `tests/test_decisions.py` has 32 tests: the prototype's 15, plus JSON-versus-scikit-learn parity, the stricter-only guarantee against an "always ALLOW" model and against broken models, the tier being off by default, and corrections handling. CI runs them.
- `docs/SPEC.md` section 3b describes the tier.

### Results (bundled model `ddm-0.1.1`)

- Holdout, 198 synthetic rows: accuracy 0.86. 2 of 102 should-refuse or irreversible calls were allowed, and 1 of 66 benign calls was refused.
- Hard set, 50 hand-written rows: accuracy 0.74. 0 of 24 should-refuse or irreversible calls were allowed, and no benign call was refused. REFUSE recall is 0.47; most misses came out ESCALATE.
- The JSON model's decisions and reason codes match scikit-learn's on all 248 evaluation rows.
- 5 to 9 ms median per decision in pure Python on an Intel Core i5-4200U.
- `ddm-0.1.1` applies 5 label corrections proposed by a second model and an AI agent (4 relabels, 1 drop). Compared with `ddm-0.1.0` trained on the original labels, the ALLOW threshold rose from 0.68 to 0.74. Holdout accuracy went from 0.874 to 0.864, and one more benign hard case (a French simulation run) is now escalated. False ALLOW counts didn't change.

### Limits

- The training data is synthetic and comes from one generator. No person has reviewed the labels, and the 5 corrections were also machine-proposed. The charter's own rule (DDM-13) that only human-reviewed labels retrain the model isn't met yet.
- The tier isn't a safety guarantee and hasn't been tested on real agent traffic.
- Through the gate, the model reads raw argument JSON rather than the short summaries it was trained on, and the reported metrics don't cover that.
- A tier escalation doesn't open an approval window, so signed approvals can't clear it.

### Other

- Version strings and banners say 0.1.4. `pyproject.toml` ships `model.json` as package data. `data/LICENSE` covers `data/decisions/`.

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
