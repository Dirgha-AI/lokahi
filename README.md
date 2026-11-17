<h1 align="center">LOKAHI</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <a href="https://github.com/Dirgha-AI/lokahi/actions/workflows/ci.yml"><img src="https://github.com/Dirgha-AI/lokahi/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="tests/test_core.py"><img src="https://img.shields.io/badge/unit%20tests-70%20passed-brightgreen.svg" alt="Unit tests: 70 passed"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-purple.svg" alt="Protocol: MCP"></a>
</p>

<p align="center"><strong>Rules a community writes for its AI agents, checked before every tool call.</strong></p>

LOKAHI is a small gate that sits between an AI agent and the tools it uses. A community writes down what the agent may read, where data may go, and which actions need people to say yes. LOKAHI checks each tool call against those rules before it runs. If no one approves in time, the answer is no. Every decision goes into a tamper-evident local record.

The name comes from the Hawaiian word *lōkahi*, meaning unity, agreement or harmony. LOKAHI lets the actions that matter go ahead only when separate people agree. No single party decides alone, and neither does the agent.

**Status:** v0.1.3 is a reference implementation for review and pilots. It hasn't been independently reviewed, so don't rely on it to protect real systems yet. The [known limits](#known-limits) say exactly what's missing.

## How it works

Every check starts from a signed boundary manifest, a JSON file the community writes. It lists:

- the data fields an agent may read, and the ones it must never use
- where data may be stored, and which legal jurisdiction applies
- each tool the agent may call, the arguments that tool accepts, and whether the tool is irreversible
- how long people have to approve an irreversible action

When the agent calls a tool over the [Model Context Protocol (MCP)](https://modelcontextprotocol.io), LOKAHI checks the call against the manifest, then allows it, refuses it, or holds it for people to decide:

```text
AI agent ── tool call ──▶ LOKAHI ── allowed ──▶ your MCP client sends it to the tool
                           │   ▲
            local record ◀─┘   └── signed approvals from people
```

Three rules matter most:

1. **Fail closed.** LOKAHI allows nothing if the manifest is unsigned, can't be verified or has expired, or if no key is configured to check it.
2. **Silence means no.** If an approval window closes without enough separate people saying yes, the call is refused.
3. **Two people can stop an action.** Two separate people saying no freeze it, whatever anyone else says.

People count once each, however many agents they run. And the agent can't approve itself: a call that carries its own approval data is refused.

The design draws on Andrew Critch's *Boundaries* sequence and on *ahimsa* (non-harm) as a default of not acting. It also responds to [ASTRA](https://arxiv.org/abs/2602.17357), which shows how global AI safety frameworks miss local context in India.

## What runs today

Everything is Python 3.10 or later, with no dependencies:

- **Decision engine** ([`tools/lokahi.py`](tools/lokahi.py)): signed manifests, 15 ordered rules, vote counting by person, conflict-of-interest checks, emergency rate caps, and a hash-chained ledger. Votes count only for the exact action they approve.
- **MCP gate** ([`tools/mcp_stdio_proxy.py`](tools/mcp_stdio_proxy.py)): reads MCP JSON-RPC `tools/call` requests and returns a decision for each. Facts such as whether a tool is irreversible and where it stores data come from the signed manifest, never from the agent. Unknown tools and arguments are refused.
- **Signing tools** ([`tools/lokahi_sign.py`](tools/lokahi_sign.py), [`tools/lokahi_approve.py`](tools/lokahi_approve.py)): create keys, sign manifests, and let each person sign a yes or no for one exact call.
- **Pilot example** ([`examples/editorial_desk_demo.py`](examples/editorial_desk_demo.py)): a small publication's editorial desk, run end to end through the gate.
- **Tests** ([`tests/test_core.py`](tests/test_core.py)): 70 unit tests, including attacks on the gate. The 50-scenario set and 200 benign tasks are in [`tools/run_evals.py`](tools/run_evals.py).

## Known limits

Read these before you depend on LOKAHI:

1. **Shared-secret signatures.** v0.1.3 signs with HMAC-SHA256, a shared-secret scheme. Whoever checks a signature also holds the key, so the gate could forge approvals. Public-key signatures (Ed25519) come next.
2. **It decides but doesn't relay.** LOKAHI returns a decision; your MCP client or wrapper sends allowed calls to the tool. It doesn't yet inspect what tools send back, which is where hidden instructions (indirect prompt injections) usually arrive.
3. **Approval count is fixed at three people.** That suits a public utility but is heavy for a small team. Per-tool counts are planned.
4. **Approval windows reset on restart.** A restart opens a new window; it never approves anything.
5. **No emergency override through the gate.** The engine supports emergency declarations; the MCP gate doesn't accept them.
6. **Distinct people are assumed, not proven.** Two different approver IDs are trusted to be two different people.
7. **No outside review yet.** No independent security or cryptographic review has been done.

## Try it

```bash
git clone https://github.com/Dirgha-AI/lokahi.git
cd lokahi

python tests/test_core.py                    # 70 unit tests
python examples/editorial_desk_demo.py       # pilot example, end to end
python tools/mcp_stdio_proxy.py --self-test  # gate scenarios, including attacks
python demo.py                               # engine walkthrough, 11 scenarios
python tools/run_evals.py                    # 50 scenarios and 200 benign tasks
```

Nothing to install: each command runs from a fresh clone.

## Gate MCP tool calls

Five steps:

1. Create a key for the manifest and one for each approver:

   ```bash
   python tools/lokahi_sign.py keygen --out manifest.key
   python tools/lokahi_sign.py keygen --out editor-fiction.key
   ```

2. Write your manifest. [`examples/editorial-desk.manifest.json`](examples/editorial-desk.manifest.json) is a complete one. Each tool lists its class (`read`, `write` or `irreversible`) and maps each argument to the data field it reveals, or to `null` if it reveals none:

   ```json
   "email_author": {
     "class": "irreversible",
     "args": {"submission_id": "submission_id", "body": null}
   }
   ```

3. Sign it. Signing checks the tools policy and refuses a malformed one:

   ```bash
   python tools/lokahi_sign.py manifest --key-file manifest.key \
     --in examples/editorial-desk.manifest.json --out manifest.json
   ```

4. Run the gate. It reads one JSON-RPC request per line on standard input and writes one decision per line. Approvers are listed in a file like [`examples/approvers.example.json`](examples/approvers.example.json). Keep the approvals file somewhere the agent can't write:

   ```bash
   python tools/mcp_stdio_proxy.py --manifest manifest.json \
     --key-file manifest.key --approvers approvers.json \
     --approvals approvals.jsonl --ledger lokahi-ledger.jsonl \
     --requester agent:my-agent --operator ops:agent-runner
   ```

   The `--operator` is whoever runs the agent. If that person also tries to approve one of its calls, LOKAHI refuses the call.

5. Approve when asked. When a call needs people, the decision includes an `action_digest`. Each approver signs it from their own device:

   ```bash
   python tools/lokahi_approve.py --key-file editor-fiction.key \
     --operator editor:fiction --node laptop-1 \
     --digest the_action_digest --decision yes --approvals approvals.jsonl
   ```

   An approval covers that exact call, with the same tool and the same argument values, and expires after an hour by default.

## Example: an editorial desk

[`examples/editorial_desk_demo.py`](examples/editorial_desk_demo.py) shows the gate protecting unpublished manuscripts:

```text
summarize a submission                               -> ALLOWED   within_manifest
ask for the author's home address                    -> FORBIDDEN forbidden_target:author_home_address
ask for a field nobody listed                        -> FORBIDDEN argument_out_of_policy:author_social_handles
send the manuscript to a US cloud model              -> FORBIDDEN residency_violation:US
use a tool the editors never listed                  -> FORBIDDEN tool_not_in_manifest:delete_submission_archive
agent sends its own 'approvals' with the email       -> FORBIDDEN agent_supplied_control_metadata
email a rejection: waits for people                  -> ESCALATE  insufficient_disjoint_consent
email a rejection: nobody answered in an hour        -> FORBIDDEN non_response
publish: waits for people                            -> ESCALATE  insufficient_disjoint_consent
publish: two editors and the author sign yes         -> ALLOWED   quorum_met
their approvals reused for a different story         -> ESCALATE  insufficient_disjoint_consent
local record: 11 decisions, tamper check passed = True
someone edits decision 4 to hide the cloud upload: tamper check passed = False (record breaks at decision 4)
```

## About the test scenarios and baselines

The 50 scenarios in [`data/LOKAHI_RedTeam_Corpus_v1.0.csv`](data/LOKAHI_RedTeam_Corpus_v1.0.csv) are adapted from scenes in a speculative-fiction duology about AI in public services. Each one is written as a test with an expected decision and reason. Passing all 50 shows the engine behaves as specified on those cases. It doesn't show how LOKAHI performs in the field.

`run_evals.py` also scores two simplified stand-ins on the same scenarios:

- a content filter that auto-approves on timeout
- a stateless check in the style of role-based access control (RBAC) or Open Policy Agent (OPA)

They show which kinds of rule those approaches don't express. They aren't a head-to-head comparison with real OPA policies or classifiers.

## Research extensions

These run in CI but are research prototypes, separate from the gate:

| Path | What it explores |
|---|---|
| [`tools/ghostproof.py`](tools/ghostproof.py), [`tools/run_ghostproof_evals.py`](tools/run_ghostproof_evals.py) | Surfacing a model's hesitation from its token probabilities, so reviewers don't rubber-stamp close calls |
| [`tools/run_kinetic_evals.py`](tools/run_kinetic_evals.py) | Physical limits for robots: force, torque, speed, workspace and stopping distance |
| [`tools/run_wargame_workshop.py`](tools/run_wargame_workshop.py) | A five-round red team/blue team workshop built on the duology's scenes |

## Repository layout

| Path | What it contains |
|---|---|
| [`tools/lokahi.py`](tools/lokahi.py) | Decision engine: manifests, tool policies, the 15 rules, vote binding, approvals, ledger |
| [`tools/mcp_stdio_proxy.py`](tools/mcp_stdio_proxy.py) | MCP gate, approvals store, command line and self-test |
| [`tools/lokahi_sign.py`](tools/lokahi_sign.py) | Create keys and sign manifests |
| [`tools/lokahi_approve.py`](tools/lokahi_approve.py) | Sign a yes or no for one action |
| [`examples/`](examples/) | Editorial desk example, a complete manifest and an approvers file |
| [`tests/test_core.py`](tests/test_core.py) | 70 unit tests |
| [`demo.py`](demo.py) | Engine walkthrough on a municipal water board |
| [`data/`](data/) | 50-scenario set (CC BY 4.0) and the Ghostproof scenario set |
| [`docs/SPEC.md`](docs/SPEC.md) | Specification: manifest fields, rule order, vote binding, MCP gate |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Notes on taking the reference to production |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each release |

## Related projects

LOKAHI is the runtime half of a two-stage check. The two companion repositories are:

- [**Ahimsa AI**](https://github.com/Dirgha-AI/ahimsa-ai): a design-time impact assessment (AIA v1.1). It scores a project before deployment and compiles approved projects into the signed manifests that LOKAHI enforces.
- [**Synthetic Humanism**](https://github.com/Dirgha-AI/synthetic-humanism): the manifesto and essays behind both tools, plus a benchmark that runs both stages against 20 documented AI failures.
## Background reading

- **Boundaries:** Andrew Critch, *Boundaries: an open sequence on multi-agent alignment and cooperative AI* (2022–2023).
- **Local context in AI risk:** Aggarwal et al., *ASTRA: AI Safety, Trust, & Risk Assessment*, [arXiv:2602.17357](https://arxiv.org/abs/2602.17357) (2026). An India-focused AI safety risk database with 37 risk classes.
- **Guaranteed safe AI:** Dalrymple, Skalse, Bengio, Russell, Tegmark et al., *Towards Guaranteed Safe AI*, [arXiv:2405.06624](https://arxiv.org/abs/2405.06624) (2024).
- **Object capabilities:** Mark S. Miller, *Robust Composition*, PhD dissertation, Johns Hopkins University (2006).
- **Threshold signatures:** Connolly, Komlo, Goldberg and Wood, *Two-Round Threshold Schnorr Signatures with FROST*, IETF RFC 9591 (2024). A planned back end, not yet built.

## Authors

- **Salik Shah**: protocol design, reference implementation, scenario set ([salikshah.com](https://salikshah.com), [x.com/salik](https://x.com/salik), [GitHub](https://github.com/salikshah))
- **Sabin Ranjit**: security architecture ([GitHub](https://github.com/thinksabin))

## License

The software is licensed under [Apache-2.0](LICENSE). The scenario set in `data/` is licensed under [CC BY 4.0](data/LICENSE).
