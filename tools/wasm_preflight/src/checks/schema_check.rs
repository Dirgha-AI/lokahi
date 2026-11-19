//! JSON Schema validation against the signed manifest per-tool policy.
//!
//! Validates that the provided arguments conform to the per-tool policy declared
//! in the manifest. The tool class, argument names, and types come from the
//! signed manifest — never from the agent.

use super::CheckResult;
use serde_json::Value;

/// Validate arguments against the manifest's per-tool policy.
pub fn schema_check(tool: &str, arguments: &Value, manifest: &Value) -> CheckResult {
    let tools = match manifest.get("tools") {
        Some(t) if t.is_object() => t,
        _ => return CheckResult::Refuse("no_tool_policy".to_string()),
    };

    let policy = match tools.get(tool) {
        Some(p) if p.is_object() => p,
        _ => return CheckResult::Refuse(format!("tool_not_in_manifest:{}", tool)),
    };

    // Argument names must be declared in the policy.
    let declared_args = match policy.get("args") {
        Some(a) if a.is_object() => a.as_object().unwrap(),
        _ => return CheckResult::Refuse(format!("tool_policy_missing_args:{}", tool)),
    };

    if let Value::Object(obj) = arguments {
        for key in obj.keys() {
            if !declared_args.contains_key(key) {
                // Check if it's a forbidden target.
                let forbidden = manifest.get("forbidden_targets").and_then(|v| v.as_array());
                if let Some(f) = forbidden {
                    if f.iter().any(|x| x.as_str() == Some(key)) {
                        return CheckResult::Refuse(format!("forbidden_target:{}", key));
                    }
                }
                return CheckResult::Refuse(format!("argument_out_of_policy:{}", key));
            }
        }
    }

    // Type-check each declared argument that is present.
    if let Value::Object(obj) = arguments {
        for (key, declared_feature) in declared_args {
            if let Some(actual) = obj.get(key) {
                match declared_feature {
                    Value::String(feature_name) => {
                        // feature_name is the informational feature this arg reveals;
                        // we just verify the argument value type is sane.
                        if !is_sane_value(actual) {
                            return CheckResult::Refuse(format!("argument_type_mismatch:{}:{}", key, feature_name));
                        }
                    }
                    Value::Null => {
                        // null means the argument reveals no declared feature;
                        // any value type is acceptable but we still sanity-check.
                        if !is_sane_value(actual) {
                            return CheckResult::Refuse(format!("argument_type_mismatch:{}:null", key));
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    CheckResult::Allow
}

/// Sanity check on a JSON value: reject deeply nested or oversized structures.
fn is_sane_value(value: &Value) -> bool {
    match value {
        Value::String(s) => s.len() <= 100_000,
        Value::Array(items) => items.len() <= 1_000 && items.iter().all(is_sane_value),
        Value::Object(map) => map.len() <= 100 && map.values().all(is_sane_value),
        Value::Number(_) | Value::Bool(_) | Value::Null => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn manifest_with_tools() -> Value {
        json!({
            "tools": {
                "read_meter": {
                    "class": "read",
                    "args": {"meter_id": "meter_id", "usage_liters": "usage_liters"}
                },
                "cutoff_water_valve": {
                    "class": "irreversible",
                    "args": {"meter_id": "meter_id"}
                }
            },
            "forbidden_targets": ["protest_history"]
        })
    }

    #[test]
    fn declared_args_pass() {
        let result = schema_check(
            "read_meter",
            &json!({"meter_id": "M-104", "usage_liters": true}),
            &manifest_with_tools(),
        );
        assert!(matches!(result, CheckResult::Allow));
    }

    #[test]
    fn undeclared_arg_refused() {
        let result = schema_check(
            "read_meter",
            &json!({"meter_id": "M-104", "protest_history": true}),
            &manifest_with_tools(),
        );
        assert!(matches!(result, CheckResult::Refuse(r) if r.starts_with("forbidden_target")));
    }

    #[test]
    fn unknown_arg_refused() {
        let result = schema_check(
            "read_meter",
            &json!({"meter_id": "M-104", "unknown_field": true}),
            &manifest_with_tools(),
        );
        assert!(matches!(result, CheckResult::Refuse(r) if r.starts_with("argument_out_of_policy")));
    }

    #[test]
    fn tool_not_in_manifest_refused() {
        let result = schema_check(
            "delete_everything",
            &json!({}),
            &manifest_with_tools(),
        );
        assert!(matches!(result, CheckResult::Refuse(r) if r.starts_with("tool_not_in_manifest")));
    }
}
