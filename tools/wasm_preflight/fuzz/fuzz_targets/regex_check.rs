#![no_main]
//! Regex allowlist/blocklist under fuzz: arbitrary tool names and values.

use libfuzzer_sys::fuzz_target;
use lokahi_wasm_preflight::checks::regex_check::regex_check;
use serde_json::json;

fuzz_target!(|data: &[u8]| {
    let text = String::from_utf8_lossy(data).into_owned();
    let tool = text.chars().take(64).collect::<String>();
    let arguments = json!({ "value": text });
    let manifest = json!({});
    let _ = regex_check(&tool, &arguments, &manifest);
});
