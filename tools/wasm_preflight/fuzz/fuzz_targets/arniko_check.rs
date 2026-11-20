#![no_main]
//! Arniko finding ingestion under fuzz: arbitrary scanner payloads.

use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::arniko_check::arniko_check;
use serde_json::Value;

fuzz_target!(|data: &[u8]| {
    let findings: Value = serde_json::from_slice(data).unwrap_or(Value::Null);
    let _ = arniko_check(&findings);
});
