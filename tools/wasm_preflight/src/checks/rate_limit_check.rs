//! Token-bucket rate limiting per (agent, tool).
//!
//! State lives in WASM linear memory and persists across calls within a single
//! WASM instance. Each (agent, tool) pair has its own bucket.
//!
//! `now_us` (microseconds since Unix epoch) is supplied by the host because
//! WASM cannot read the system clock directly.

use super::CheckResult;
use std::cell::RefCell;
use std::collections::HashMap;

/// Tokens refilled per second per bucket.
const REFILL_RATE_PER_SEC: f64 = 10.0;
/// Maximum burst capacity per bucket.
const BUCKET_CAPACITY: f64 = 20.0;
/// Cost of one tool call (in tokens).
const CALL_COST: f64 = 1.0;
/// Hard cap on tracked buckets. A new (agent, tool) pair beyond the cap is
/// refused rather than allowing unbounded linear-memory growth.
const MAX_BUCKETS: usize = 4096;

/// Per-bucket state.
struct TokenBucket {
    tokens: f64,
    last_refill: u64, // microseconds since epoch
}

/// Global bucket store (in-WASM, per instance).
thread_local! {
    static BUCKETS: RefCell<HashMap<String, TokenBucket>> = RefCell::new(HashMap::new());
}

/// Check rate limit for an (agent, tool) pair.
pub fn rate_limit_check(
    agent: &Option<String>,
    tool: &str,
    _manifest: &serde_json::Value,
    now_us: u64,
) -> CheckResult {
    let agent_id = agent.as_deref().unwrap_or("agent:unknown");
    let key = format!("{}:{}", agent_id, tool);

    BUCKETS.with(|buckets| {
        let mut b = buckets.borrow_mut();
        if !b.contains_key(&key) && b.len() >= MAX_BUCKETS {
            // Fail closed: an unbounded key space would be a memory-growth
            // vector, so saturation is a refusal rather than an allow.
            return CheckResult::Refuse("rate_limit_table_saturated".to_string());
        }
        let bucket = b.entry(key.clone()).or_insert(TokenBucket {
            tokens: BUCKET_CAPACITY,
            last_refill: now_us,
        });

        // Refill tokens based on elapsed time.
        let elapsed_secs = (now_us as f64 - bucket.last_refill as f64) / 1_000_000.0;
        bucket.tokens = (bucket.tokens + elapsed_secs * REFILL_RATE_PER_SEC).min(BUCKET_CAPACITY);
        bucket.last_refill = now_us;

        if bucket.tokens >= CALL_COST {
            bucket.tokens -= CALL_COST;
            CheckResult::Allow
        } else {
            CheckResult::Refuse(format!("rate_limited:{}:{}", agent_id, tool))
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn first_call_allowed() {
        let result = rate_limit_check(&Some("agent:a".to_string()), "read_meter", &json!({}), 1_000_000_000_000);
        assert!(matches!(result, CheckResult::Allow));
    }

    #[test]
    fn different_agents_separate_buckets() {
        let _ = rate_limit_check(&Some("agent:a".to_string()), "read_meter", &json!({}), 1_000_000_000_000);
        let result = rate_limit_check(&Some("agent:b".to_string()), "read_meter", &json!({}), 1_000_000_000_000);
        assert!(matches!(result, CheckResult::Allow));
    }

    #[test]
    fn no_agent_uses_default() {
        let result = rate_limit_check(&None, "read_meter", &json!({}), 1_000_000_000_000);
        assert!(matches!(result, CheckResult::Allow));
    }
}
