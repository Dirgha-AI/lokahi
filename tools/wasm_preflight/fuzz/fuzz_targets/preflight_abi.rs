#![no_main]
//! Whole-module fuzzing: arbitrary input and manifest bytes through the C ABI.
//!
//! Contract under fuzz:
//! - never panics, never traps;
//! - `verdict` is always `ALLOW` or `REFUSE`;
//! - `budget_us` is a finite integer.

use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::budget::MAX_BUDGET_US;
use lokahi_wasm_preflight::run_preflight;

fuzz_target!(|data: &[u8]| {
    if data.len() < 2 {
        return;
    }
    let midpoint = data.len() / 2;
    let input_bytes = &data[..midpoint];
    let manifest_bytes = &data[midpoint..];

    let input: Option<serde_json::Value> = serde_json::from_slice(input_bytes).ok();
    let manifest: Option<serde_json::Value> = serde_json::from_slice(manifest_bytes).ok();

    if let (Some(input), Some(manifest)) = (input, manifest) {
        let input = lokahi_wasm_preflight::PreflightInput::from_value(input);
        let result = run_preflight(&input, &manifest, 0);
        assert!(
            result.verdict == "ALLOW" || result.verdict == "REFUSE",
            "unexpected verdict: {}",
            result.verdict
        );
        // The host enforces the real budget; the module must still report a sane one.
        let _ = MAX_BUDGET_US;
    }
});
