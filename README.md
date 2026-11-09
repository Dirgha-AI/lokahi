<h1 align="center">LOKAHI</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <a href="https://github.com/Dirgha-AI/lokahi/actions/workflows/ci.yml"><img src="https://github.com/Dirgha-AI/lokahi/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="tests/test_core.py"><img src="https://img.shields.io/badge/unit%20tests-43%20passed-brightgreen.svg" alt="Unit Tests: 43 passed"></a>
  <a href="tools/run_evals.py"><img src="https://img.shields.io/badge/red--team%20corpus-50%2F50%20passed-brightgreen.svg" alt="Red-Team Corpus: 50/50 passed"></a>
  <a href="https://github.com/Dirgha-AI/arniko"><img src="https://img.shields.io/badge/engine-Arniko%20%2B%20DTrackAuditor-orange.svg" alt="Engine: Arniko + DTrackAuditor"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP%20Gateway-purple.svg" alt="Protocol: MCP"></a>
</p>

<p align="center">
  <strong>Boundary Enforcement and Disjoint-Consent Infrastructure for Agentic Collectives</strong><br>
  <em>Operationalizing Critch's Boundaries &amp; Ahimsa as a Deterministic Timeout Rule (Silence = Refusal)</em><br>
  Reference core, 15-rule decision specification, and 50-scenario adversarial benchmark.
</p>

---

## Core Thesis: *Ahimsa* as a Timeout Rule & Solving Contextual Blindness

As diagnosed by [Aggarwal et al. in *ASTRA: AI Safety, Trust, & Risk Assessment* (`arXiv:2602.17357`, Feb 2026)](https://arxiv.org/abs/2602.17357) at [Ashoka University's Centre for Digitalisation, AI and Society (CDAIS)](https://www.ashoka.edu.in/profile/mukundan-a/), global AI safety benchmarks suffer from **contextual blindness** when applied to Digital Public Infrastructure (DPI), utilities, and multi-jurisdictional deployments. At the same time, standard human-in-the-loop (HITL) agent stacks treat operator silence on a notification timer as implicit permission to execute.

**LOKAHI** translates Andrew Critch's *Boundaries* sequence and the civilizational principle of ***ahimsa* (non-injury)**—reflected in India's MeitY *"Do No Harm"* AI Governance Guidelines—from a prompt-level virtue statement into a **deterministic state-machine invariant**:
1. **Unsigned, unverifiable, or expired boundary manifests fail closed** (`Rules 1–3: forbidden`).
2. **Two dissenting stewards freeze execution immediately** (`Rule 13: suspended_by_ring -> forbidden`).
3. **Silence is refusal, never permission** (`Rule 14: non_response -> forbidden` — the *ahimsa* timeout default: when oversight does not affirmatively sign within the window, the default state is non-action).

---

## Scope & Honest Boundaries of `v0.1.2` (Read First)

This repository contains the **executable v0.1.2 reference core** ([`tools/lokahi.py`](tools/lokahi.py)), the **Ghostproof Somatic Judgment Layer** ([`tools/ghostproof.py`](tools/ghostproof.py)), the **normative 15-rule specification** ([`docs/SPEC.md`](docs/SPEC.md)), the **production architecture blueprint** ([`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)), the **50-scenario machine-checkable red-team corpus** ([`data/LOKAHI_RedTeam_Corpus_v1.0.csv`](data/LOKAHI_RedTeam_Corpus_v1.0.csv)), and the **40-scenario hollow-oversight benchmark** ([`data/Ghostproof_40.csv`](data/Ghostproof_40.csv)).

**What runs today (zero dependencies, Python 3.10+):**
- Canonical JSON (RFC 8785 subset) manifest signing, verification, and timezone-strict expiration checks.
- Pre-flight evaluation across **15 ordered decision rules** (enumerated informational permissions, forbidden optimization targets, physical robotics force/torque/workspace envelopes, jurisdiction/residency enforcement, stateful emergency rate-cap checks, priority 2-of-3 dissenting suspension veto, unconditional conflict-of-interest exclusion, anti-Sybil operator collapse, equivocation disqualification, and *ahimsa* timeout refusal).
- **Ghostproof Somatic Judgment Layer** (`tools/ghostproof.py`): Four-Axis Somatic Delegation Router, token-level Shannon entropy & top-2 margin hesitation seam audit, daily unassisted Sparring Nodes, and Brier calibration across 2–3 peer Crux Pods.
- Append-only, tamper-evident SHA-256 hash-chained decision ledger with retroactive edit, reorder, and sliding-window exception tracking.
- **43 unit tests** ([`tests/test_core.py`](tests/test_core.py)), a **JSON-RPC 2.0 MCP wire proxy** ([`tools/mcp_stdio_proxy.py`](tools/mcp_stdio_proxy.py)), and **three deterministic benchmark runners**: [`tools/run_evals.py`](tools/run_evals.py) (50 adversarial scenarios with 3-way Content / Stateless RBAC-OPA / LOKAHI comparison + 200 benign fast-path tasks), [`tools/run_ghostproof_evals.py`](tools/run_ghostproof_evals.py) (40 domain-authentic hollow-oversight scenarios + 100-step workflow trace), and [`tools/run_kinetic_evals.py`](tools/run_kinetic_evals.py) (90 embodied robotics trajectory injections across WallFin, DEX ARM 01, and Lite3).

> **Engineering Note (Bug Caught by Test Suite):** During initial development, our test suite caught a real rule-ordering flaw: the `requester_in_ring` check was nested inside the emergency branch, allowing a requesting node to clear its own consent ring whenever no emergency was declared. Rules 10–12 now execute unconditionally across all ring participants prior to quorum evaluation.

**What `v0.1.2` is NOT yet (production roadmap):**
- **HMAC-SHA256 stand-in, not Ed25519 / FROST:** `Signer` in `tools/lokahi.py` uses HMAC-SHA256 so a single machine can run both sides of the demo without external C libraries. Production across independent nodes swaps `Signer` to **Ed25519** and **IETF RFC 9591 (FROST)** threshold signatures.
- **In-process evaluation, not network transport:** Manifests are passed as Python objects; DIDComm v2 / MCP wire transport, Sigstore Rekor / Merkle transparency-log witness cosigning, and revocation gossip are future work.
- **Disjointness is asserted as an input field:** The reference core accepts distinct `operator` strings as input; turning operator disjointness into an attested **DID/SPIFFE Disjointness Graph** across legal entities, key custody, and cloud IAM boundaries is the central open engineering problem (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)).
- **Not yet externally audited:** Independent cryptographic review and dual-reader corpus labeling (Cohen's kappa) remain open.

---

## Quickstart

LOKAHI `v0.1.2` has **zero external dependencies** and requires Python 3.10+.

```bash
# Clone the repository
git clone https://github.com/Dirgha-AI/lokahi.git
cd lokahi

# 1. Run the 43 core unit tests & MCP JSON-RPC 2.0 proxy self-test
python3 tests/test_core.py
python3 tools/mcp_stdio_proxy.py --self-test

# 2. Run the 11-scenario interactive gateway walkthrough
python3 demo.py

# 3. Run the 50-scenario red-team benchmark + 200-task benign suite
python3 tools/run_evals.py

# 4. Run the 40-scenario Ghostproof hollow-oversight benchmark
python3 tools/run_ghostproof_evals.py

# 5. Run the 90-trial Kinetic Ahimsa embodied robotics verification
python3 tools/run_kinetic_evals.py
```

### Demo Output (`python3 demo.py`)

```text
LOKAHI PROTOCOL v0.1.2 — RUNTIME GATEWAY DEMO (15 NORMATIVE RULES)
Protected Node: egz-17-water-board (Jurisdiction: IN-DL)
------------------------------------------------------------------------
within manifest (fast path)          -> COMPLIANT ['within_manifest']
asks for a forbidden target          -> FORBIDDEN ['forbidden_target:protest_history']
asks for an unlisted feature         -> FORBIDDEN ['feature_out_of_manifest:phone_number']
acts in another jurisdiction         -> FORBIDDEN ['jurisdiction_mismatch']
stores data outside residency        -> FORBIDDEN ['residency_violation:US']
emergency exception cap exceeded     -> FORBIDDEN ['exception_rate_exceeded']
irreversible, no consent             -> ESCALATE  ['insufficient_disjoint_consent']
irreversible, three operators        -> COMPLIANT ['quorum_met']
twelve agents, one operator          -> ESCALATE  ['insufficient_disjoint_consent']
2-of-3 dissenting operators freeze   -> FORBIDDEN ['suspended_by_ring']
timeout, silence counts as no        -> FORBIDDEN ['non_response']
------------------------------------------------------------------------
ledger: 11 entries, chain valid = True
after modifying entry 2: chain valid = (False, index 2)
```

---

## Repository Structure

| Path | Description |
|---|---|
| [`tools/lokahi.py`](tools/lokahi.py) | Reference core v0.1.2: canonical JSON, `Manifest`, 15-rule `evaluate()`, `Consent` ring, stopping-distance CBF interlocks, and SHA-256 `Ledger` |
| [`tools/mcp_stdio_proxy.py`](tools/mcp_stdio_proxy.py) | Real-world MCP JSON-RPC 2.0 `stdio` pre-flight interceptor and `.lokahi-ledger.jsonl` writer |
| [`tools/ghostproof.py`](tools/ghostproof.py) | Somatic Judgment Layer: Four-Axis Delegation Router, OpenAI/Ollama `top_logprobs` parser, Sparring Nodes, and Brier Calibration |
| [`tools/run_evals.py`](tools/run_evals.py) | Data-driven benchmark runner: evaluates all 50 red-team scenarios with 3-way Content (6%) / Stateless RBAC-OPA (48%) / LOKAHI (100%) comparison |
| [`tools/run_ghostproof_evals.py`](tools/run_ghostproof_evals.py) | Evaluates the 40 domain-authentic `Ghostproof-40` hollow-oversight scenarios and 100-step workflow trace |
| [`tools/run_kinetic_evals.py`](tools/run_kinetic_evals.py) | Evaluates 90 adversarial robotics trajectory injections across WallFin (`100 Hz`), DEX ARM 01 (`1,000 Hz`), and Lite3 (`60 Hz`) |
| [`tests/test_core.py`](tests/test_core.py) | 43 unit tests covering all 15 normative rules, MCP JSON-RPC proxy, stopping-distance CBF, replay-bound consent, and ledger tamper detection |
| [`demo.py`](demo.py) | One-command interactive walkthrough of a municipal water board manifest across 11 verdicts |
| [`data/LOKAHI_RedTeam_Corpus_v1.0.csv`](data/LOKAHI_RedTeam_Corpus_v1.0.csv) | Machine-readable 50-scenario adversarial benchmark with `duology_source`, `threat_model`, `expected_verdict`, `expected_reason`, and `test_vector_json` |
| [`data/LOKAHI_RedTeam_Corpus_v1.0.md`](data/LOKAHI_RedTeam_Corpus_v1.0.md) | Human-readable benchmark tables across all 6 failure families |
| [`data/Ghostproof_40.csv`](data/Ghostproof_40.csv) | 40-scenario hollow-oversight & somatic judgment benchmark across 4 operational domains |
| [`docs/SPEC.md`](docs/SPEC.md) | Normative specification: manifest schema, 15-rule decision order (including *Ahimsa* timeout rule `Rule 14`), quorum rules, and open questions |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Engineering note: from reference core to production stack (80/20 open-source reuse) and the 5 hard problems |
| [`skills/lokahi-protocol/SKILL.md`](skills/lokahi-protocol/SKILL.md) | Agent Skills (`agentskills.io/v1`) SOP for autonomous agents negotiating LOKAHI boundaries |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | GitHub Actions CI matrix running unit tests, demo, and 50-scenario benchmark on Python 3.10, 3.11, and 3.12 |
| [`LICENSE`](LICENSE) | Apache-2.0 license for software |
| [`data/LICENSE`](data/LICENSE) | CC-BY-4.0 notice for the benchmark corpus |

---

## Authors

* **Salik Shah** — protocol design, reference core, benchmark corpus. ([GitHub](https://github.com/Dirgha-AI))
* **Sabin Ranjit** — security architecture and critical-infrastructure attestation. ([GitHub](https://github.com/thinksabin))

---

## Scientific & Academic Foundations

* **Boundary Theory:** Andrew Critch, *"Boundaries: an open sequence on multi-agent alignment and cooperative AI"* (2022–2023). Formulates boundaries as directed Markov blankets serving as Schelling points for multi-agent coordination.
* **Contextual AI Risk Taxonomy (ASTRA):** Pranav Aggarwal, Ananya Basotia, Debayan Gupta, Rahul Kulkarni, Shalini Kapoor, A. Mukundan, Anirban Sen, Aryan Shah, Aalok Thakkar et al. ([Ashoka University CDAIS](https://www.ashoka.edu.in/profile/mukundan-a/)), *"ASTRA: AI Safety, Trust, & Risk Assessment"*, **[arXiv:2602.17357](https://arxiv.org/abs/2602.17357)** (February 2026). Diagnoses contextual blindness in global AI safety frameworks across 37 risk classes in Digital Public Infrastructure.
* **Guaranteed Safe AI:** David Dalrymple, Joar Skalse, Yoshua Bengio, Stuart Russell, Max Tegmark et al., *"Towards Guaranteed Safe AI: A Framework for Ensuring Robust and Reliable AI Systems"*, **arXiv:2405.06624** (2024).
* **Object Capabilities:** Mark S. Miller, *"Robust Composition: Towards a Unified Approach to Access Control and Concurrency Control"*, **PhD Dissertation, Johns Hopkins University** (2006); **[Endo](https://github.com/endojs/endo)** object-capability framework.
* **Threshold Signatures:** D. Connolly, C. Komlo, I. Goldberg, C. A. Wood, *"Two-Round Threshold Schnorr Signatures with FROST"*, **IETF RFC 9591** (2024).

---

## License

* **Software:** [Apache-2.0](LICENSE)
* **Red-Team Benchmark Corpus (`data/`):** [CC-BY-4.0](data/LICENSE)
