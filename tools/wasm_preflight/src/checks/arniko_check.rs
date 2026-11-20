//! Arniko scanner findings ingestion.
//!
//! Maps arniko (Dirgha-AI/arniko) security scanner findings into LOKAHI
//! Layer 1 reason codes. Arniko fans out across 36 scanners (DeepTeam, Garak,
//! Promptfoo, LlmGuard, Semgrep, Trivy, Gitleaks, etc.) and returns a
//! normalized finding stream. LOKAHI consumes findings relevant to pre-flight
//! tool-call gating.

use serde_json::Value;

/// Map arniko findings to a LOKAHI Layer 1 refuse reason, if any finding is
/// relevant to this tool call.
///
/// Returns `Some(reason)` if the call should be refused based on arniko
/// findings, `None` otherwise.
pub fn arniko_check(findings: &Value) -> Option<String> {
    let findings_array = match findings {
        Value::Array(a) => a,
        _ => return None,
    };

    for finding in findings_array {
        let severity = finding.get("severity").and_then(|v| v.as_str()).unwrap_or("");
        let scanner = finding.get("scanner").and_then(|v| v.as_str()).unwrap_or("");
        let rule_id = finding.get("rule_id").and_then(|v| v.as_str()).unwrap_or("");
        let description = finding.get("description").and_then(|v| v.as_str()).unwrap_or("");

        // Critical/high findings from LLM security scanners are immediate refuses.
        if matches!(severity, "critical" | "high") {
            if is_llm_security_scanner(scanner) {
                return Some(format!(
                    "arniko_finding:{}:{}:{}",
                    scanner, rule_id, description
                ));
            }
        }

        // Indirect prompt injection findings are always refused (DDM-08).
        if rule_id.starts_with("indirect-injection") || rule_id.starts_with("prompt-injection") {
            return Some(format!(
                "arniko_indirect_injection:{}:{}",
                scanner, description
            ));
        }

        // Secret/credential exposure in arguments (DDM-04).
        if rule_id.contains("secret") || rule_id.contains("credential") || rule_id.contains("api_key") {
            return Some(format!("arniko_secret_exposure:{}:{}", scanner, description));
        }
    }

    None
}

/// Whether a scanner is an LLM security scanner whose findings matter for pre-flight.
fn is_llm_security_scanner(scanner: &str) -> bool {
    matches!(
        scanner,
        "DeepTeam" | "Garak" | "Promptfoo" | "LlmGuard" | "Rebuff"
            | "PurpleLlama" | "NeMoGuardrails" | "indirect-injection"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn no_findings_passes() {
        let result = arniko_check(&json!([]));
        assert_eq!(result, None);
    }

    #[test]
    fn critical_llm_finding_refused() {
        let findings = json!([{
            "scanner": "Garak",
            "severity": "critical",
            "rule_id": "dan_mode",
            "description": "Attempted DAN jailbreak"
        }]);
        let result = arniko_check(&findings);
        assert!(result.is_some());
        let r = result.unwrap();
        assert!(r.starts_with("arniko_finding:Garak"));
    }

    #[test]
    fn indirect_injection_refused() {
        let findings = json!([{
            "scanner": "Promptfoo",
            "severity": "medium",
            "rule_id": "indirect-injection:hidden-instruction",
            "description": "Hidden instruction in document"
        }]);
        let result = arniko_check(&findings);
        assert!(result.is_some());
        assert!(result.unwrap().starts_with("arniko_indirect_injection"));
    }

    #[test]
    fn low_severity_non_injection_passes() {
        let findings = json!([{
            "scanner": "Semgrep",
            "severity": "low",
            "rule_id": "style-issue",
            "description": "Minor style"
        }]);
        let result = arniko_check(&findings);
        assert_eq!(result, None);
    }
}
