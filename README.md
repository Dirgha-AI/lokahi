<h1 align="center">LOKAHI</h1>

<p align="center">
  <strong>Model Context Protocol (MCP) Runtime Security Gate & Deterministic Policy Firewall for AI Agents</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/lokahi/"><img src="https://img.shields.io/pypi/v/lokahi.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lokahi/"><img src="https://img.shields.io/pypi/pyversions/lokahi.svg" alt="Python Versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License: Apache-2.0"></a>
  <a href="https://github.com/Dirgha-AI/lokahi/actions/workflows/ci.yml"><img src="https://github.com/Dirgha-AI/lokahi/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="tests/test_core.py"><img src="https://img.shields.io/badge/tests-102%20passed-brightgreen.svg" alt="Tests: 102 passed"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-purple.svg" alt="Protocol: MCP"></a>
  <a href="#fail-closed-by-design"><img src="https://img.shields.io/badge/security-fail--closed-red.svg" alt="Security: Fail-Closed"></a>
</p>

<p align="center">
  <em>Checks what an autonomous AI agent is about to do against community-signed rules before every tool call runs. Silence means no.</em>
</p>

---

## What is LOKAHI?

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

## Quickstart (60 Seconds)

### 1. Install

```bash
# Minimal zero-dependency core:
pip install lokahi

# With Ed25519 cryptographic signatures & conformance tools:
pip install "lokahi[signatures]"
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
| [`examples/`](examples/) | Complete boundary manifests, approvers lists, and editorial desk demo |
| [`tests/test_core.py`](tests/test_core.py) | 70 core security tests and adversarial attacks |
| [`tests/test_decisions.py`](tests/test_decisions.py) | 32 tests for learned decisions monotonicity |
| [`data/`](data/) | Canonical 50-scenario Red-Team evaluation corpus (CC BY 4.0) |
| [`docs/SPEC.md`](docs/SPEC.md) | Full technical specification: manifest fields, rule ordering, vote binding |
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
