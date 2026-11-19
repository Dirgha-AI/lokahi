# LOKAHI WASM Pre-Flight Fuzzing Plan

Fuzzing strategy for the Layer 1 WebAssembly deterministic pre-flight checks (`tools/wasm_preflight/`). The goal is to prove that the WASM module never crashes, never leaks memory, and always returns a valid verdict (ALLOW or REFUSE) for any input — including malformed, adversarial, and boundary inputs.

## Fuzzing Targets

| Target | Interface | Language | Fuzzer |
|--------|-----------|----------|--------|
| `preflight_check` C ABI | WASM export (input_ptr, input_len, manifest_ptr, manifest_len, now_us → result_ptr) | Rust | cargo-fuzz + wasm-fuzzer |
| `regex_check` | Rust function (tool, arguments, manifest → CheckResult) | Rust | cargo-fuzz |
| `schema_check` | Rust function (tool, arguments, manifest → CheckResult) | Rust | cargo-fuzz |
| `manifest_check` | Rust function (manifest → CheckResult) | Rust | cargo-fuzz |
| `arniko_check` | Rust function (findings → Option<String>) | Rust | cargo-fuzz |
| Python bridge | `WasmtimePreflight.check(input_dict, manifest_dict)` | Python | atheris / Hypothesis |

## Native Rust Fuzzing (cargo-fuzz)

### Setup

```bash
# Install cargo-fuzz
cargo install cargo-fuzz

# Initialize fuzzing project in tools/wasm_preflight/
cargo fuzz init
```

### Fuzz Target 1: `preflight_check` C ABI

Fuzzes the full WASM entry point with arbitrary input and manifest JSON bytes. The WASM module must never panic and must always return a valid result pointer.

```rust
// fuzz/fuzz_targets/preflight_abi.rs
#![no_main]
use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::*;

fuzz_target!(|data: &[u8]| {
    // Split data into input + manifest
    if data.len() < 2 { return; }
    let mid = data.len() / 2;
    let input_bytes = &data[..mid];
    let manifest_bytes = &data[mid..];
    
    // Parse input as JSON (may be malformed - that's OK)
    let input: Option<PreflightInput> = serde_json::from_slice(input_bytes).ok();
    let manifest: Option<serde_json::Value> = serde_json::from_slice(manifest_bytes).ok();
    
    match (input, manifest) {
        (Some(i), Some(m)) => {
            let result = run_preflight(&i, &m, 0);
            // Assert: verdict is always ALLOW or REFUSE
            assert!(result.verdict == "ALLOW" || result.verdict == "REFUSE");
            // Assert: budget_us is always finite
            assert!(result.budget_us < u64::MAX / 2);
        }
        _ => {} // Malformed JSON is skipped
    }
});
```

### Fuzz Target 2: `regex_check`

Fuzzes the regex allowlist/blocklist with arbitrary tool names and argument values. Must never panic on pathological regex input.

```rust
// fuzz/fuzz_targets/regex_check.rs
#![no_main]
use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::regex_check::regex_check;
use serde_json::json;

fuzz_target!(|data: &[u8]| {
    let tool = String::from_utf8_lossy(data).into_owned();
    let arguments = json!({"fuzz": String::from_utf8_lossy(data)});
    let manifest = json!({});
    
    let result = regex_check(&tool, &arguments, &manifest);
    // Must return one of the three variants without panicking
    match result {
        CheckResult::Allow | CheckResult::Refuse(_) | CheckResult::Escalate(_) => {}
    }
});
```

### Fuzz Target 3: `schema_check`

Fuzzes schema validation with arbitrary tool names, argument structures, and manifest policies. Must handle deeply nested arrays, oversized strings, and malformed types.

```rust
// fuzz/fuzz_targets/schema_check.rs
#![no_main]
use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::schema_check::schema_check;
use serde_json::json;

fuzz_target!(|data: &[u8]| {
    let tool = String::from_utf8_lossy(&data[..data.len().min(64)]).into_owned();
    let arguments: serde_json::Value = serde_json::from_slice(data).unwrap_or(json!({}));
    let manifest: serde_json::Value = serde_json::from_slice(data).unwrap_or(json!({}));
    
    let result = schema_check(&tool, &arguments, &manifest);
    match result {
        CheckResult::Allow | CheckResult::Refuse(_) | CheckResult::Escalate(_) => {}
    }
});
```

### Fuzz Target 4: `manifest_check`

Fuzzes manifest signature/expiry validation with malformed signatures, missing fields, timezone-naive timestamps, and obviously expired dates.

```rust
// fuzz/fuzz_targets/manifest_check.rs
#![no_main]
use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::manifest_check::manifest_check;
use serde_json::json;

fuzz_target!(|data: &[u8]| {
    let manifest: serde_json::Value = serde_json::from_slice(data).unwrap_or(json!({}));
    let result = manifest_check(&manifest);
    match result {
        CheckResult::Allow | CheckResult::Refuse(_) | CheckResult::Escalate(_) => {}
    }
});
```

### Fuzz Target 5: `arniko_check`

Fuzzes arniko finding ingestion with arbitrary scanner names, severities, rule IDs, and descriptions.

```rust
// fuzz/fuzz_targets/arniko_check.rs
#![no_main]
use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::arniko_check::arniko_check;
use serde_json::json;

fuzz_target!(|data: &[u8]| {
    let findings: serde_json::Value = serde_json::from_slice(data).unwrap_or(json!([]));
    let result = arniko_check(&findings);
    // Must return None or Some(String)
    match result {
        None | Some(_) => {}
    }
});
```

## Python Bridge Fuzzing (atheris / Hypothesis)

Fuzzes the `WasmtimePreflight.check()` Python API with arbitrary JSON-serializable inputs. The bridge must never crash and must always return a `PreflightVerdict` with a valid verdict.

```python
# tests/fuzz/test_wasm_bridge_fuzz.py
import sys
import atheris
with atheris.instrument_imports():
    from wasm_bridge import WasmtimePreflight, PreflightVerdict

def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    
    # Generate random input dict
    tool = fdp.ConsumeUnicodeNoSurrogates(64)
    arguments = fdp.ConsumeJson()
    agent = fdp.ConsumeUnicodeNoSurrogates(64)
    
    input_dict = {"tool": tool, "arguments": arguments, "agent": agent}
    
    # Generate random manifest
    manifest = {
        "signature": fdp.ConsumeUnicodeNoSurrogates(128),
        "expires": fdp.ConsumeUnicodeNoSurrogates(64),
        "tools": {},
    }
    
    try:
        verdict = bridge.check(input_dict, manifest)
        assert verdict.verdict in ("ALLOW", "REFUSE", "ESCALATE")
        assert isinstance(verdict.reasons, list)
        assert isinstance(verdict.budget_us, int)
    except WasmtimeUnavailable:
        pass  # WASM not available in fuzz environment

def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()

if __name__ == "__main__":
    main()
```

## Fuzzing Corpus

### Seed Corpus (hand-crafted inputs)

| File | Description |
|------|-------------|
| `seeds/benign_input.json` | Normal tool call with declared arguments |
| `seeds/injection_input.json` | Known injection patterns in arguments |
| `seeds/oversized_input.json` | Argument values exceeding size limits |
| `seeds/deeply_nested.json` | Deeply nested JSON arrays/objects |
| `seeds/malformed_manifest.json` | Missing signature, missing expiry, bad timezone |
| `seeds/expired_manifest.json` | Expired manifest timestamps |
| `seeds/ed25519_signature.json` | Ed25519 signatures (128 hex chars) |
| `seeds/hmac_signature.json` | HMAC-SHA256 signatures (64 hex chars) |
| `seeds/arniko_findings.json` | Arniko scanner findings with various severities |

### Mutation Strategy

- **Bit flipping:** Random byte mutations in input/manifest JSON
- **Structure splicing:** Combine valid JSON fragments from different seed files
- **Dictionary:** JSON keywords (`"tool"`, `"arguments"`, `"signature"`, `"expires"`, `"class"`, `"args"`, `"tools"`)
- **Length extremes:** Empty inputs, maximum-size inputs (8 KiB buffer limit)

## Fuzzing Goals & Coverage Criteria

| Goal | Target | Status |
|------|--------|--------|
| No panics in any fuzz target | 100% of executions | To be measured |
| No memory leaks | WASM memory stable across 1M+ calls | To be measured |
| All verdicts are valid | ALLOW / REFUSE / ESCALATE only | To be measured |
| Budget always reported | budget_us is finite integer | To be measured |
| Malformed input never crashes | Returns REFUSE with reason | To be measured |
| Deep nesting handled | 1000+ levels of nesting | To be measured |
| Oversized strings rejected | >100KB string values | To be measured |

## CI Integration

Add to `.github/workflows/ci.yml`:

```yaml
fuzz:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions-rs/toolchain@v1
      with:
        toolchain: stable
        components: rustfmt, clippy
    - run: cargo install cargo-fuzz
    - run: cargo fuzz run preflight_abi -max_total_time=3600
    - run: cargo fuzz run regex_check -max_total_time=3600
    - run: cargo fuzz run schema_check -max_total_time=3600
    - run: cargo fuzz run manifest_check -max_total_time=3600
    - run: cargo fuzz run arniko_check -max_total_time=3600
```

## Fuzzing Artifacts

- `fuzz/corpus/` — Seed corpus and discovered inputs
- `fuzz/crashes/` — Crash reproductions (should be empty)
- `fuzz/artifacts/` — Fuzzing run artifacts
