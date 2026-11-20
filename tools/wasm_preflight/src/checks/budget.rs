//! Sub-millisecond budget enforcement.

/// Maximum allowed CPU time for the entire Layer 1 pre-flight, in microseconds.
/// Target: well under 1 ms (1000 µs). Set at 500 µs for safety margin.
pub const MAX_BUDGET_US: u64 = 500;

/// Individual check budgets (µs). Sum must be < MAX_BUDGET_US.
pub const REGEX_BUDGET_US: u64 = 100;
pub const SCHEMA_BUDGET_US: u64 = 150;
pub const MANIFEST_BUDGET_US: u64 = 150;
pub const RATE_LIMIT_BUDGET_US: u64 = 50;
pub const ARNIKO_BUDGET_US: u64 = 50;
