#![no_main]
//! Schema validation under fuzz: arbitrary arguments and manifest policies.

use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::schema_check::schema_check;
use serde_json::Value;

fuzz_target!(|data: &[u8]| {
    let value: Value = serde_json::from_slice(data).unwrap_or(Value::Null);
    let tool = value
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("read_meter");
    let arguments = value.get("arguments").cloned().unwrap_or(Value::Null);
    let manifest = value.get("manifest").cloned().unwrap_or(Value::Null);
    let _ = schema_check(tool, &arguments, &manifest);
});
