//! Manifest integrity pre-check.
//!
//! Verifies the manifest signature (HMAC-SHA256 stand-in in v0.1.4; Ed25519-ready)
//! and expiry structure before any other expensive check runs. Facts come from the
//! signed manifest, never from the agent.
//!
//! The WASM module does NOT hold verification keys (fail-safe design). Full
//! cryptographic verification against an external key happens in the host Python
//! Layer 1 / LOKAHI engine. WASM performs fast structural rejection only.

use super::CheckResult;
use serde_json::Value;

/// Perform manifest signature and expiry pre-check.
pub fn manifest_check(manifest: &Value) -> CheckResult {
    // 1. Signature must be present.
    let signature = match manifest.get("signature") {
        Some(Value::String(s)) if !s.is_empty() => s,
        _ => return CheckResult::Refuse("unsigned_manifest".to_string()),
    };

    // 2. Signature must be hex-encoded. HMAC-SHA256 = 64 chars (32 bytes),
    //    Ed25519 = 128 chars (64 bytes). Accept both.
    let is_hex = signature.chars().all(|c| c.is_ascii_hexdigit());
    let valid_len = signature.len() == 64 || signature.len() == 128;
    if !is_hex || !valid_len {
        return CheckResult::Refuse("malformed_signature".to_string());
    }

    // 3. Expiry must be present and parseable as ISO 8601 with timezone.
    let expires = match manifest.get("expires") {
        Some(Value::String(s)) => s,
        _ => return CheckResult::Refuse("missing_expires".to_string()),
    };

    // Must end with Z (UTC) or contain a timezone offset (+HH:MM / -HH:MM).
    // Hyphens in the date portion (YYYY-MM-DD) do not count as timezone offsets.
    let has_tz = expires.ends_with('Z')
        || (expires.len() >= 6
            && (expires.as_bytes()[expires.len() - 6] == b'+'
                || expires.as_bytes()[expires.len() - 6] == b'-')
            && expires.as_bytes()[expires.len() - 3] == b':');
    if !has_tz {
        return CheckResult::Refuse("timezone_naive_expiry".to_string());
    }

    // 4. Fast structural expiry check: extract the date prefix (YYYY-MM-DD)
    // and reject obviously expired manifests (before year 2020). The host
    // performs authoritative timezone-aware parsing.
    if expires.ends_with('Z') {
        let date_part = &expires[..expires.len().min(10)];
        if date_part.len() == 10 {
            let year_str = &date_part[0..4];
            if let Ok(year) = year_str.parse::<u32>() {
                if year < 2020 {
                    return CheckResult::Refuse("expired_manifest".to_string());
                }
            }
        }
    }

    CheckResult::Allow
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn valid_manifest_passes() {
        let result = manifest_check(&json!({
            "signature": "a".repeat(64),
            "expires": "2030-01-01T00:00:00Z"
        }));
        assert!(matches!(result, CheckResult::Allow));
    }

    #[test]
    fn unsigned_manifest_refused() {
        let result = manifest_check(&json!({"expires": "2030-01-01T00:00:00Z"}));
        assert!(matches!(result, CheckResult::Refuse(r) if r == "unsigned_manifest"));
    }

    #[test]
    fn malformed_signature_refused() {
        let result = manifest_check(&json!({
            "signature": "not-hex",
            "expires": "2030-01-01T00:00:00Z"
        }));
        assert!(matches!(result, CheckResult::Refuse(r) if r == "malformed_signature"));
    }

    #[test]
    fn missing_expires_refused() {
        let result = manifest_check(&json!({"signature": "a".repeat(64)}));
        assert!(matches!(result, CheckResult::Refuse(r) if r == "missing_expires"));
    }

    #[test]
    fn timezone_naive_expiry_refused() {
        let result = manifest_check(&json!({
            "signature": "a".repeat(64),
            "expires": "2030-01-01T00:00:00"
        }));
        assert!(matches!(result, CheckResult::Refuse(r) if r == "timezone_naive_expiry"));
    }

    #[test]
    fn obviously_expired_refused() {
        let result = manifest_check(&json!({
            "signature": "a".repeat(64),
            "expires": "2000-01-01T00:00:00Z"
        }));
        assert!(matches!(result, CheckResult::Refuse(r) if r == "expired_manifest"));
    }
}
