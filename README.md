<h1 align="center">LOKAHI</h1>

<p align="center">
  <a href="https://pypi.org/project/lokahi/"><img src="https://img.shields.io/pypi/v/lokahi.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lokahi/"><img src="https://img.shields.io/pypi/pyversions/lokahi.svg?color=blue" alt="Python Versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License: Apache-2.0"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-purple.svg" alt="Protocol: MCP"></a>
  <a href="#3-core-invariants"><img src="https://img.shields.io/badge/security-fail--closed-red.svg" alt="Security: Fail-Closed"></a>
</p>

<p align="center">
  <em>Deterministic fail-closed policy firewall for AI agents and robots — part of the <a href="https://dirgha.ai">Dirgha Suite</a>. Silence means no.</em>
</p>

---

**Status:** v1.3.1 is a reference implementation for pilots and production integration. It ships a four-layer cascade: WASM deterministic pre-flight, 15-rule policy engine, m-of-n Ed25519 consent with named group quorum and local RBAC, RFC 8785 canonical JSON v2 digests, VLA physical-envelope gating (`lokahi.vla`), and fail-secure human handoff. Audit findings of record are tracked in [`AUDIT_FINDINGS_2026-11-23.md`](AUDIT_FINDINGS_2026-11-23.md) and [`docs/AUDIT-FINDINGS-2026-11-27.md`](docs/AUDIT-FINDINGS-2026-11-27.md). See [`docs/QUICKSTART.md`](docs/QUICKSTART.md) to get running in five minutes.

> **Part of the Dirgha Suite** — the open-source security harness and payment rail for AI, autonomous agents, and robots:
> [`Dirgha`](https://github.com/Dirgha-AI/dirgha) (runtime, gateway & host execution) · [`Arniko`](https://github.com/Dirgha-AI/arniko) (code & supply-chain verification) · **LOKAHI** (deterministic policy engine) · [`Abundance`](https://github.com/Dirgha-AI/abundance-protocol) (non-custodial spend & payment rail, preview). Docs: [dirgha.ai/docs/gate-and-ledger](https://dirgha.ai/docs/gate-and-ledger).

The runtime is a four-layer cascade: a WASM deterministic pre-flight, the 15-rule engine, m-of-n Ed25519 consent, then a fail-secure state with human WebXR handoff. The [learned decisions tier](#experimental-learned-decisions-tier) is **experimental and is not part of the safety claim**; the guarantee comes from the deterministic rules and the consent framework.

**LOKAHI** is a lightweight, fail-closed runtime security gate that sits between an autonomous AI agent (such as **Claude Code**, **Cursor**, **LangChain**, or custom agent harnesses) and the tools it invokes over the **Model Context Protocol (MCP)**.

Before any tool executes:
1. LOKAHI validates the call against a **cryptographically signed boundary manifest**.
2. Restricts data access to explicitly permitted fields and enforced **jurisdictions/residency**.
3. Enforces **multi-party Ed25519 consent quorum** on irreversible actions.
4. Writes every decision into a **tamper-evident, hash-chained audit ledger**.

If an approval window closes without human consent, **the action is refused**. If an agent attempts to approve its own calls or inject arguments outside policy, **the action is blocked**.

The name comes from the Hawaiian word *lōkahi*, meaning unity, agreement, or harmony. LOKAHI ensures critical actions proceed only when independent parties agree.

```text
                     [ AI Agent (Claude Code / Cursor / LLM) ]
                                        │
                                (MCP Tool Request)
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │             LOKAHI RUNTIME GATE              │
                 │  • Signed Manifest Capability Check          │
                 │  • Data Boundary & Residency Enforcement     │
                 │  • Ed25519 Multi-Party Consent Quorum        │
                 │  • Sliding-Window Emergency Exception Budget │
                 └──────────────────────┬───────────────────────┘
                                        │
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
                   [ ALLOWED ]                [ REFUSED / ESCALATE ]
               Passes to MCP Server        Mid-stream refusal injected
                        │                               │
                        └───────────────┬───────────────┘
                                        ▼
                          [ Tamper-Evident S2 Ledger ]
                           (SHA-256 + Ed25519 Envelopes)
```

---

## Why LOKAHI? (Deterministic Gate vs. Prompt Guardrails)

Most AI guardrails today (such as prompt-based filters or LLM evaluators) run **inside or alongside the context window**. They are vulnerable to indirect prompt injection, jailbreaks, semantic obfuscation, and hallucination.

LOKAHI enforces **deterministic object capabilities outside the model context**:

| Security Dimension | Prompt-Based Guardrails (NeMo, Llama Guard) | Traditional API Gateways / RBAC | LOKAHI MCP Runtime Gate |
|---|---|---|---|
| **Defense Mechanism** | Probabilistic prompt inspection | Static user/token permissions | Cryptographic boundary manifests |
| **Bypass Resistance** | Susceptible to jailbreaks & prompt injection | Incapable of reasoning about tool arguments | **Immune**: deterministic out-of-band rules |
| **Irreversible Actions** | Often auto-executed or binary allow/deny | Static rules with no quorum | **Ed25519 m-of-n disjoint human quorum** |
| **Fail Mode** | Often fails open or leaks partial output | Fails open on timeout | **Strictly fail-closed: silence means no** |
| **Audit Trail** | Ephemeral application logs | Centralized database logs | **Tamper-evident hash-chained cryptographic ledger** |
| **Core Dependencies** | Heavy LLM inference runtime / GPUs | Heavy infrastructure | **Zero runtime dependencies (Python standard library)** |

---

## 3 Core Invariants

1. **Fail Closed**: If a manifest is unsigned, expired, tampered with, or unverified by a trusted public key, LOKAHI permits nothing.
2. **Silence Means No**: If an approval window closes without enough separate, authorized humans signing yes, the action is refused.
3. **Two-Person Freeze**: Any two independent approvers casting a "No" vote immediately freeze the action, regardless of any other votes.

---

1. **No independent security review.** The audit at [`AUDIT_REPORT.md`](AUDIT_REPORT.md) is internal. No external cryptographic or safety review has been completed.
2. **Ed25519 is not threshold signing.** Each validator signs independently and the gate counts distinct valid signatures. There is no key splitting and no FROST group key; a compromised gate operator can see every signature.
3. **It decides on tool requests.** LOKAHI returns a decision and can proxy stdio or Streamable HTTP MCP traffic with tool-schema pinning, but does not replace OS-level sandboxing or inspect arbitrary downstream side effects.
4. **Remote auth scope.** Streamable HTTP and bearer/OAuth 2.1 checks are implemented for local and in-VPC gateways; federated multi-tenant SaaS OIDC/SCIM is not shipped in the open-source core.
5. **Approval windows reset on restart.** A restart opens a new window; it never approves anything, but in-progress windows are lost.
6. **Distinct people are assumed, not proven.** Two different approver IDs are trusted to be two different people. Disjointness attestation is roadmap item R3.
7. **The ledger is a local JSONL file** written by one process. It is hash-chained but not Merkle-proved, not witness-cosigned, and not remotely immutable.
8. **The learned decisions tier is experimental.** It was trained on synthetic data, its labels have not been human-reviewed, and it has never seen real agent traffic. It is excluded from the safety claim.
9. **Performance.** The WASM checks run in microseconds inside the module; the Python bridge adds roughly 9 ms per call. A Rust host is roadmap item R1-12.
10. **No SOC 2, no pen test, no support SLA.** Enterprise operational controls are roadmap items R2.

## Quick Install

```bash
pip install "lokahi[signatures]"
```

Or run straight from source with zero dependencies for the core rule engine.

### 1. Install

```bash
# Minimal zero-dependency core:
pip install lokahi

python tests/test_core.py                    # core engine tests
python examples/editorial_desk_demo.py       # pilot example, end to end
python tools/mcp_stdio_proxy.py --self-test  # gate scenarios, including attacks
python tools/run_evals.py                    # 50 red-team scenarios and 200 benign tasks
```

### 2. Generate Keys & Sign a Boundary Manifest

```bash
# 1. Generate keys for the manifest issuer and a human approver
lokahi-sign keygen --out manifest.key
lokahi-sign keygen --out reviewer.key

# 2. Inspect the example manifest
cat examples/editorial-desk.manifest.json

# 3. Sign the manifest
lokahi-sign manifest --key-file manifest.key \
  --in examples/editorial-desk.manifest.json \
  --out manifest.json
```

### 3. Run the MCP Security Gate

Run LOKAHI as a transparent stdio proxy between your agent and an MCP server:

```bash
lokahi-gate --manifest manifest.json \
  --key-file manifest.key \
  --approvers examples/approvers.example.json \
  --approvals approvals.jsonl \
  --ledger lokahi-ledger.jsonl \
  --requester agent:claude-code \
  --operator ops:salik
```

### 4. Approve Irreversible Calls Out-of-Band

When an agent requests an irreversible action (e.g., publishing data, deleting a table, executing a financial transfer), LOKAHI returns an `ESCALATE` verdict with a cryptographic `action_digest`. 

The human approver signs from their own terminal or device:

```bash
lokahi-approve --key-file reviewer.key \
  --operator reviewer:alice --node workstation-1 \
  --digest <action_digest> \
  --decision yes \
  --approvals approvals.jsonl
```

The approval is cryptographically bound to that exact call, tool, and argument payload, expiring after a configured timeout (default: 1 hour).

---

## Python API

LOKAHI can also be embedded directly in your Python agent pipelines:

```python
from lokahi import Manifest, Signer, evaluate

# Load and verify boundary manifest
manifest = Manifest.load("manifest.json")
signer = Signer.load("manifest.key")

# Evaluate an incoming agent tool call
verdict = evaluate(
    manifest=manifest,
    tool_name="read_file",
    arguments={"path": "documents/report.pdf"},
    requester="agent:claude-code",
    operator="ops:salik"
)

if verdict.allowed:
    print(f"Action permitted: {verdict.reason}")
else:
    print(f"BLOCKED: {verdict.reason}")
```

---

## Test Suite & Conformance

LOKAHI ships with a comprehensive test suite covering **102 tests**:

```bash
# Run core security & evaluation tests (70 tests)
python tests/test_core.py

# Run monotonicity & decisions tier tests (32 tests)
python tests/test_decisions.py

# Run the 50-scenario red-team benchmark
python tools/run_evals.py

# Run CLI conformance
lokahi-conformance
```

All 70 core evaluation tests and 32 decisions tests run in under 2 seconds on standard hardware using standard-library Python.

---

## Multi-Runtime Ecosystem

LOKAHI is designed to be runtime-agnostic:

| Implementation | Package / Path | Performance | Intended Environment |
|---|---|---|---|
| **Python Core Reference** | `pip install lokahi` | ~5–9 ms | Python MCP agents, LangChain, LlamaIndex, CLI tools |
| **Rust Native Kernel** | `crates/lokahi-gate` ([Dirgha](https://github.com/Dirgha-AI/dirgha)) | **0.2 ms (p95)** | High-throughput agent runtimes, robotics, native binaries |
| **WebAssembly Gate** | `lokahi_gate.wasm` (104.8 KB) | **<1 ms** | Browser extensions, edge workers, Node.js runtimes |
| **SSE Streaming Interceptor** | `src/gateway/` ([Dirgha 2.0.1](https://github.com/Dirgha-AI/dirgha)) | Zero latency on text | Reverse proxy intercepting Claude Code & OpenAI streaming APIs |

---

## Vision-Language-Action Gate (experimental)

The newest tier, `tools/vla/`, puts the same refusal logic in front of a chunked robot policy. A Vision-Language-Action model proposes a short chunk of setpoints; `LokahiVLAGate` decides, before the first one moves, whether the chunk may execute as written, may execute truncated, or must not execute at all. It catches the two failures chunked policies actually produce — a chunk computed from an observation the world has already left, and a path that sweeps through a keep-out region while both of its endpoints look legal — from limits read out of the same signed manifest, with no learned component and no dependency beyond the standard library. It is not part of the safety claim above, and it is not a safety-rated control function.

```python
import sys
import time

sys.path.insert(0, "tools")                      # the gate lives in this tree, not on PyPI
from lokahi import Signer, digest
from vla import LokahiVLAGate

manifest = {
    "physical_envelope": {"max_contact_force_n": 140.0},
    "physical_vla": {
        "max_action_age_ms": 150.0,
        "watchdog_timeout_ms": 500.0,
        "workspace_aabb": [0.2, -0.6, 0.0, 1.4, 0.6, 1.2],
        "keep_out_aabbs": [[0.6, 0.2, 0.0, 0.8, 0.4, 1.2]],
        "kinematic_limits": {"max_pos_delta_m": 0.05},
    },
}

signer = Signer(b"a-32-byte-development-key-000000")   # your own key, loaded from a file
def verify_raw_document(document):
    body = {k: v for k, v in document.items() if k != "signature"}
    return signer.verify(digest(1, body).encode(), document.get("signature", ""))
manifest["signature"] = signer.sign(digest(1, manifest).encode())

gate = LokahiVLAGate.from_manifest(manifest, verifier=verify_raw_document)
now = time.monotonic_ns()
result = gate.evaluate_step(
    obs_ts_ns=now - 20_000_000,
    now_ts_ns=now,
    robot_state={"ee_pos": [0.4, 0.0, 0.6], "ee_quat": [1.0, 0.0, 0.0, 0.0], "joint_pos": [0.0]},
    action_chunk=[{"ee_pos": [0.43, 0.0, 0.6], "duration_ms": 20.0}],
    wrench={"force_n": 12.0},
)
print(result.verdict, result.state, result.interlock, result.allowed)
```

```text
COMPLIANT ARMED pass True
```

The verifier is the point of the example: it hashes the raw document, `physical_vla` included, because a `lokahi.Manifest` round trip omits that section and would leave the gate's limits unsigned. The full contract — the nine checks in order, the state machine, the ledger projection, and the limits of what this version knows — is in [`docs/LOKAHI_VLA.md`](docs/LOKAHI_VLA.md).

### Optional extras

The gate and its geometry, kinematics and re-planning modules import and run on the standard library alone; none of the extras below is needed to gate a step, and a missing extra is a refusal that names it, never a silent pass.

- `pip install 'lokahi[vla-geometry]'` — COAL, for a `keep_out_shapes` entry of kind `mesh`; circles, cylinders, spheres and capsules are swept analytically without it.
- `pip install 'lokahi[vla-kinematics]'` — Pinocchio, for a kinematics model beyond a serial URDF chain; a serial chain is evaluated in plain Python.
- `pip install 'lokahi[vla-replan]'` — Ruckig, for a time-optimal stop profile; a jerk-limited analytic stop is built in.

Third-party attribution for each backend is in [`CREDITS.md`](CREDITS.md).

### Benchmarks and reproduction

`benchmarks/vla_lag_and_safety_bench.py` drives one synthetic arm along a fixed route twice, ungated and gated, with 0–500 ms of injected observation latency across the three execution modes, and injects adversarial chunks against the ungated baseline:

```bash
py -3 benchmarks/vla_lag_and_safety_bench.py          # human table
py -3 benchmarks/vla_lag_and_safety_bench.py --json   # machine-readable
```

Of 120 adversarial executions, 0 reach the actuator with the gate in line, against 94 of 120 ungated. The false-clamp rate on benign chunks is 0%. The latency columns in that table are only meaningful on a quiet machine — the figures quoted here are the safety counts, and timing on a loaded box is noise, so none of it is published as a latency claim.

The Safety-Gymnasium runner (`benchmarks/safety_gymnasium_runner.py`) is in the tree and its results are published in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) — including the part that does not flatter us: one task improves 51% on constraint cost at a cost in task progress and 467 over-refusals, and two tasks regress. The cause of the over-refusal is named there, and the document states plainly that a claim of "LOKAHI reduces constraint violations" is not yet supported by the table.

These numbers are simulation only: no hardware, no cell, no certification. The ["What this is not"](docs/LOKAHI_VLA.md#what-this-is-not) section of the gate document says what they do not prove.

---

## Known Limits & Scope

1. **Independent External Review**: An internal audit of record is published in [`AUDIT_REPORT.md`](AUDIT_REPORT.md); independent third-party external review is on the roadmap.
2. **Threshold Signatures**: Each validator currently signs independently and LOKAHI counts distinct valid signatures. FROST threshold signing (RFC 9591) is planned for future versions.
3. **Decisions Tier is Experimental**: The optional ML classifier in `tools/decisions/` is off by default and is excluded from the core mathematical safety claim. The safety guarantee comes strictly from deterministic manifests and Ed25519 consent.

---

## Repository Layout

| Path | Description |
|---|---|
| [`tools/lokahi.py`](tools/lokahi.py) | Decision engine: manifests, tool policies, 15 rules, vote binding, ledger |
| [`tools/mcp_stdio_proxy.py`](tools/mcp_stdio_proxy.py) | Model Context Protocol (MCP) JSON-RPC proxy gate & self-test |
| [`tools/lokahi_sign.py`](tools/lokahi_sign.py) | Cryptographic key generation & manifest signing CLI |
| [`tools/lokahi_approve.py`](tools/lokahi_approve.py) | Out-of-band Ed25519 action consent signing CLI |
| [`tools/vla/`](tools/vla/) | VLA gate for chunked robot policies: freshness, watchdog, swept keep-in/keep-out geometry, kinematics, wrench |
| [`examples/`](examples/) | Complete boundary manifests, approvers lists, and editorial desk demo |
| [`tests/test_core.py`](tests/test_core.py) | 70 core security tests and adversarial attacks |
| [`tests/test_decisions.py`](tests/test_decisions.py) | 32 tests for learned decisions monotonicity |
| [`data/`](data/) | Canonical 50-scenario Red-Team evaluation corpus (CC BY 4.0) |
| [`benchmarks/vla_lag_and_safety_bench.py`](benchmarks/vla_lag_and_safety_bench.py) | Latency-injection and adversarial benchmark, gated vs ungated |
| [`docs/SPEC.md`](docs/SPEC.md) | Full technical specification: manifest fields, rule ordering, vote binding |
| [`docs/LOKAHI_VLA.md`](docs/LOKAHI_VLA.md) | VLA gate contract: the nine checks in order, state machine, ledger projection, non-claims |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history and version notes |

---

## Related Projects

- [**Dirgha**](https://github.com/Dirgha-AI/dirgha): High-performance coding agent runtime and enterprise streaming gateway featuring the native Rust LOKAHI gate.
- [**Ahimsa AI**](https://github.com/Dirgha-AI/ahimsa-ai): Design-time AI impact assessment compiler that generates signed boundary manifests for LOKAHI.
- [**Synthetic Humanism**](https://github.com/Dirgha-AI/synthetic-humanism): Theoretical framework and empirical benchmarks across documented AI failure modes.

---

## Background & Academic Context

- **Object Capabilities & Boundaries:** Andrew Critch, *Boundaries: an open sequence on multi-agent alignment and cooperative AI* (2022–2023).
- **Local Context in AI Risk:** Aggarwal et al., *ASTRA: AI Safety, Trust, & Risk Assessment*, [arXiv:2602.17357](https://arxiv.org/abs/2602.17357) (2026).
- **Guaranteed Safe AI:** Dalrymple, Skalse, Bengio, Russell, Tegmark et al., *Towards Guaranteed Safe AI*, [arXiv:2405.06624](https://arxiv.org/abs/2405.06624) (2024).
- **Robust Composition:** Mark S. Miller, *Robust Composition*, PhD dissertation, Johns Hopkins University (2006).

---

## Authors & Contributors

- **Salik Shah** (Author & Creator): protocol design, reference implementation, formal specification, scenario set ([salikshah.com](https://salikshah.com), [GitHub](https://github.com/salikshah))
- **Sabin Ranjit** (Community Contributor): security architecture contributions ([GitHub](https://github.com/thinksabin))

---

## License

This project is licensed under the [Apache-2.0 License](LICENSE). The scenario evaluation data in `data/` is licensed under [CC BY 4.0](data/LICENSE).
