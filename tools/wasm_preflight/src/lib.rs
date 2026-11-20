//! LOKAHI Layer 1 — WASM deterministic pre-flight checks.
//!
//! Exported C ABI functions:
//!   - preflight_check(input_ptr, input_len, manifest_ptr, manifest_len, now_us) -> ptr (malloc'd JSON; free with lokahi_free)
//!   - preflight_check_len() -> usize (length of last result)
//!   - preflight_memory_size() -> usize (WASM memory size in bytes)
//!
//! The WASM memory is exported as `memory` so the host (Python/wasmtime) can
//! read/write JSON strings into it.
//!
//! All checks are pure, deterministic, and bounded by a sub-millisecond budget.
//! Any check that would exceed the budget causes the whole pre-flight to
//! return REFUSE with reason `budget_exceeded`.
//!
//! The host passes `now_us` (microseconds since Unix epoch) because WASM
//! cannot access the system clock directly.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub mod checks;

use checks::{
    regex_check::regex_check,
    schema_check::schema_check,
    rate_limit_check::rate_limit_check,
    manifest_check::manifest_check,
    arniko_check::arniko_check,
    CheckResult,
};
use checks::budget;

// ---------------------------------------------------------------------------
// Result buffer (allocated per call; the host reads then frees it)
// ---------------------------------------------------------------------------

static mut RESULT_LEN: usize = 0;

/// Allocate `len` bytes inside the module's linear memory.
///
/// The host uses this to place request and manifest JSON. Nothing is written at
/// fixed offsets: the module's own data segment and heap are never overlapped.
#[no_mangle]
pub extern "C" fn lokahi_alloc(len: usize) -> *mut u8 {
    if len == 0 {
        return std::ptr::null_mut();
    }
    let layout = match std::alloc::Layout::from_size_align(len, 8) {
        Ok(l) => l,
        Err(_) => return std::ptr::null_mut(),
    };
    unsafe { std::alloc::alloc(layout) }
}

/// Free a buffer previously returned by [`lokahi_alloc`] or `write_result`.
#[no_mangle]
pub extern "C" fn lokahi_free(ptr: *mut u8, len: usize) {
    if ptr.is_null() || len == 0 {
        return;
    }
    if let Ok(layout) = std::alloc::Layout::from_size_align(len, 8) {
        unsafe { std::alloc::dealloc(ptr, layout) }
    }
}

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

/// Input to the pre-flight check.
#[derive(Deserialize, Serialize)]
pub struct PreflightInput {
    pub tool: String,
    pub arguments: Value,
    pub agent: Option<String>,
    pub arniko_findings: Option<Value>,
}

impl PreflightInput {
    /// Build an input from an arbitrary JSON value, for fuzzing and tests.
    pub fn from_value(value: serde_json::Value) -> Self {
        let obj = value.as_object();
        PreflightInput {
            tool: obj
                .and_then(|o| o.get("tool"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            arguments: obj
                .and_then(|o| o.get("arguments"))
                .cloned()
                .unwrap_or(serde_json::Value::Null),
            agent: obj
                .and_then(|o| o.get("agent"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            arniko_findings: obj.and_then(|o| o.get("arniko_findings")).cloned(),
        }
    }
}

/// Output of the pre-flight check.
#[derive(Serialize, Deserialize)]
pub struct PreflightOutput {
    pub verdict: String,
    pub reasons: Vec<String>,
    pub layer: String,
    pub budget_us: u64,
}

// ---------------------------------------------------------------------------
// Exported C ABI
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn preflight_check(
    input_ptr: *const u8,
    input_len: usize,
    manifest_ptr: *const u8,
    manifest_len: usize,
    now_us: i64,
) -> *mut u8 {
    // Read input JSON from WASM linear memory.
    let input_json = unsafe { std::slice::from_raw_parts(input_ptr, input_len) };
    let manifest_json = unsafe { std::slice::from_raw_parts(manifest_ptr, manifest_len) };

    let input: PreflightInput = match serde_json::from_slice(input_json) {
        Ok(v) => v,
        Err(e) => {
            return write_result(&PreflightOutput {
                verdict: "REFUSE".to_string(),
                reasons: vec![format!("malformed_input:{}", e)],
                layer: "layer1_wasm".to_string(),
                budget_us: 0,
            });
        }
    };

    let manifest: Value = match serde_json::from_slice(manifest_json) {
        Ok(v) => v,
        Err(e) => {
            return write_result(&PreflightOutput {
                verdict: "REFUSE".to_string(),
                reasons: vec![format!("malformed_manifest:{}", e)],
                layer: "layer1_wasm".to_string(),
                budget_us: 0,
            });
        }
    };

    // Run all Layer 1 checks in order (cheapest and most certain first).
    let mut reasons: Vec<String> = Vec::new();
    let mut refuse = false;

    // 1. Regex allowlist / blocklist
    match regex_check(&input.tool, &input.arguments, &manifest) {
        CheckResult::Allow => {}
        CheckResult::Refuse(r) => {
            refuse = true;
            reasons.push(r);
        }
        CheckResult::Escalate(r) => {
            reasons.push(r);
        }
    }

    // 2. Schema validation (only if not already refused)
    if !refuse {
        match schema_check(&input.tool, &input.arguments, &manifest) {
            CheckResult::Allow => {}
            CheckResult::Refuse(r) => {
                refuse = true;
                reasons.push(r);
            }
            CheckResult::Escalate(r) => {
                reasons.push(r);
            }
        }
    }

    // 3. Manifest signature + expiry pre-check (HMAC v1; Ed25519-ready)
    if !refuse {
        match manifest_check(&manifest) {
            CheckResult::Allow => {}
            CheckResult::Refuse(r) => {
                refuse = true;
                reasons.push(r);
            }
            CheckResult::Escalate(r) => {
                reasons.push(r);
            }
        }
    }

    // 4. Rate limiting (token bucket per agent/tool) — uses host-supplied now_us
    if !refuse {
        match rate_limit_check(&input.agent, &input.tool, &manifest, now_us as u64) {
            CheckResult::Allow => {}
            CheckResult::Refuse(r) => {
                refuse = true;
                reasons.push(r);
            }
            CheckResult::Escalate(r) => {
                reasons.push(r);
            }
        }
    }

    // 5. arniko scanner findings (if provided)
    if !refuse {
        if let Some(findings) = &input.arniko_findings {
            if let Some(r) = arniko_check(findings) {
                refuse = true;
                reasons.push(r);
            }
        }
    }

    let verdict = if refuse { "REFUSE" } else { "ALLOW" };

    write_result(&PreflightOutput {
        verdict: verdict.to_string(),
        reasons,
        layer: "layer1_wasm".to_string(),
        budget_us: 0, // host sets the real budget
    })
}

#[no_mangle]
pub extern "C" fn preflight_check_len() -> usize {
    unsafe { RESULT_LEN }
}

#[no_mangle]
pub extern "C" fn preflight_memory_size() -> usize {
    65536 // single WASM memory page
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Serialize a PreflightOutput into a freshly allocated buffer and return its pointer.
///
/// The host must free the buffer with `lokahi_free(ptr, preflight_check_len())`.
fn write_result(output: &PreflightOutput) -> *mut u8 {
    let serialized = serde_json::to_vec(output).unwrap_or_default();
    let len = serialized.len();
    let ptr = lokahi_alloc(len.max(1));
    if ptr.is_null() {
        unsafe { RESULT_LEN = 0 };
        return std::ptr::null_mut();
    }
    unsafe {
        std::ptr::copy_nonoverlapping(serialized.as_ptr(), ptr, len);
        RESULT_LEN = len;
    }
    ptr
}

// ---------------------------------------------------------------------------
// Public API for native testing
// ---------------------------------------------------------------------------

pub fn run_preflight(input: &PreflightInput, manifest: &Value, now_us: u64) -> PreflightOutput {
    let input_json = serde_json::to_vec(input).unwrap();
    let manifest_json = serde_json::to_vec(manifest).unwrap();

    let ptr = preflight_check(
        input_json.as_ptr(),
        input_json.len(),
        manifest_json.as_ptr(),
        manifest_json.len(),
        now_us as i64,
    );
    let len = unsafe { preflight_check_len() };
    let bytes: Vec<u8> = if ptr.is_null() || len == 0 {
        Vec::new()
    } else {
        let slice = unsafe { std::slice::from_raw_parts(ptr as *const u8, len) };
        let copy = slice.to_vec();
        lokahi_free(ptr, len);
        copy
    };
    serde_json::from_slice(&bytes).unwrap_or(PreflightOutput {
        verdict: "REFUSE".to_string(),
        reasons: vec!["internal_error".to_string()],
        layer: "layer1_wasm".to_string(),
        budget_us: 0,
    })
}
