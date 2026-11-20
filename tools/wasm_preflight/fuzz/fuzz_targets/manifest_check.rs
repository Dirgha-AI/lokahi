#![no_main]
//! Manifest integrity checks under fuzz: malformed signatures and expiries.

use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::manifest_check::manifest_check;
use serde_json::Value;

fuzz_target!(|data: &[u8]| {
    let manifest: Value = serde_json::from_slice(data).unwrap_or(Value::Null);
    let _ = manifest_check(&manifest);
});
