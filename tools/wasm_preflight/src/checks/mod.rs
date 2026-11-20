//! Layer 1 deterministic checks.

pub mod arniko_check;
pub mod budget;
pub mod manifest_check;
pub mod rate_limit_check;
pub mod regex_check;
pub mod schema_check;

/// Result of a single check.
///
/// `Escalate` is part of the check contract: a check may report something a
/// later layer must look at without refusing outright. No current check
/// returns it, so the variant is marked allow(dead_code) until one does.
#[derive(Debug, Clone, PartialEq)]
pub enum CheckResult {
    /// The check passed; continue to the next check.
    Allow,
    /// The check failed; refuse immediately with the given reason.
    Refuse(String),
    /// The check warrants escalation (not refuse, but note it).
    #[allow(dead_code)]
    Escalate(String),
}
