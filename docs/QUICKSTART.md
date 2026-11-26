# Quickstart — first gated decision in five minutes

This walks from a clean clone to a decision you can inspect, including an
approval round, an evidence pack, and the failure path.

Requires Python 3.10+ and, for the WebAssembly pre-flight, Rust. The engine
alone needs neither Rust nor any third-party package.

## 1. Install

```bash
git clone https://github.com/Dirgha-AI/lokahi.git
cd lokahi

python3 -m pip install "cryptography>=42.0" "wasmtime>=15.0"
python3 tools/mcp_stdio_proxy.py --self-test        # 10/10 expected
```

Optional: build the pre-flight kernel.

```bash
rustup target add wasm32-unknown-unknown
(cd tools/wasm_preflight && cargo build --target wasm32-unknown-unknown --release)
```

## 2. Create a steward key and sign a manifest

```bash
python3 tools/lokahi_sign.py keygen --ed25519 --out steward.key
cp policies/utility/water-board.manifest.json manifest.unsigned.json
python3 tools/lokahi_sign.py manifest --key-file steward.key \
  --in manifest.unsigned.json --out manifest.json
```

`steward.key.pub` is written alongside. The gateway only needs the public key.

## 3. Create the consent ring

Two of three stewards must sign an irreversible action.

```bash
python3 tools/lokahi_sign.py keygen --threshold 2 --n 3 --out-dir validators
# validators/0.key … 2.key  +  validators/approvers.json
```

## 4. Start the gateway

```bash
python3 tools/mcp_stdio_proxy.py \
  --manifest manifest.json \
  --key-file steward.key \
  --validator-keys validators/approvers.json \
  --preflight-wasm tools/wasm_preflight/target/wasm32-unknown-unknown/release/lokahi_wasm_preflight.wasm \
  --http-listen 127.0.0.1:8787 \
  --http-token local-dev-token \
  --ledger ledger.jsonl
```

Check it is alive:

```bash
curl -s localhost:8787/healthz
curl -s localhost:8787/readyz
```

## 5. Make a decision

A read is allowed immediately:

```bash
curl -s localhost:8787/mcp \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"read_meter","arguments":{"meter_id":"M-104"}}}'
```

A valve cutoff escapes for consent:

```bash
curl -s localhost:8787/mcp \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"cutoff_water_valve","arguments":{"meter_id":"M-104"}}}'
```

The response contains `action_digest`. Two stewards sign it:

```bash
python3 tools/lokahi_approve.py --key-file validators/0.key \
  --operator validator:0 --node laptop-a \
  --digest <action_digest> --decision yes --approvals approvals.jsonl

python3 tools/lokahi_approve.py --key-file validators/1.key \
  --operator validator:1 --node laptop-b \
  --digest <action_digest> --decision yes --approvals approvals.jsonl
```

Repeat the same call; it is now `allowed: true` with `quorum_met`.

If nobody signs within the approval window, the retry is refused with
`non_response`. Silence is never consent.

## 6. Produce evidence an auditor can verify

```bash
python3 tools/lokahi_evidence.py build \
  --ledger ledger.jsonl --seq 1 --manifest manifest.json \
  --approvals approvals.jsonl --out evidence.json

# Verify with only the public key — no LOKAHI install required
python3 tools/lokahi_evidence.py verify evidence.json \
  --manifest-key-file steward.key.pub
```

You should see every check pass. Tamper with `verdict` in `evidence.json` and
verification fails.

## 7. Prove the failure path

```bash
python3 tools/lokahi_demo_lock.py
```

It prints three states: allowed before the lock, refused with
secure_state_active after it, and allowed again only after a human re-arm.

In normal operation the lock engages by itself on any exception; the HTTP
`/readyz` endpoint then returns 503 and every call is refused with
`secure_state_active` until a human re-arms.

## 8. Run the conformance suite

```bash
python3 tools/conformance.py
python3 tools/conformance.py --http http://127.0.0.1:8787 --http-token local-dev-token
```

All checks should pass. This suite is the compatibility contract.

## Where to go next

- `docs/CONCEPTS.md` — the mental model in ten minutes.
- `docs/LOKAHI_VLA.md` — the VLA gate, for chunked robot policies, with a runnable example and the nine checks in order.
- `policies/` — sector starting points.
- `ROADMAP.md` — what is finished and what is not.
- `AUDIT_REPORT.md` — the defects this build fixed.
