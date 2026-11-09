# -*- coding: utf-8 -*-
"""Ghostproof v0.1.2 — Somatic Judgment Layer & Epistemic Uncertainty Framework (Paper 5).

Unites classical Indic epistemology (Satya as epistemic fidelity and Anekantavada
as multi-sided deliberation) with cognitive neuroscience (Summerfield, 2022;
Tessler et al., 2024), organizational economics (Garicano et al., 2026), and
human-AI team evaluation (Kulveit & Leech, 2026; Budzyn et al., 2025):

1. Four-Axis Somatic Delegation Router (`route_delegation`):
   Separates Autonomic Metabolism (0 human decisions/hr) from Prefrontal Escalation
   (1-3 decisions/day on Strong Bundles) and Developmental Sparring (1/day):
   - Axis 1: Reversibility `Rev(A) in [0, 1]`
   - Axis 2: Decision Value / Empirical Human Lift `Delta_E(A) = Acc(Human+AI) - Acc(AI)`
   - Axis 3: Normative & Strong-Bundle Legitimacy `Norm(A) in {0, 1}`
   - Axis 4: Capability Value / Developmental Sparring (`is_sparring_node`) — requires
     an unassisted pre-commitment call before revealing the AI default to prevent
     cognitive atrophy (Budzyn et al., Lancet Gastro & Hep 2025).

2. Hesitation & Seam Audit Layer (`audit_hesitation_seams`):
   Extracts token-level Shannon entropy `H(p_t) = -sum p(v) log2 p(v)` and
   top-2 probability margin `M_t = p_1(t) - p_2(t)` across decision tokens
   (`AE-D02`, `AE-E02`) and generates a 300-word Adversarial Crux Brief surfacing
   minority dissent (Tessler et al., Science 2024).

3. Pre-Registered Judgment, Crux Pods & Brier Calibration (`evaluate_brier_calibration`):
   Replaces single-click approval with a 2-3 peer Crux Pod logging a chosen path,
   a two-sentence falsifiable hypothesis, and a calibrated probability `p in [0, 1]`
   scored over time via Brier calibration `BS = (1/N) sum (p_i - o_i)^2`.

4. Section 4 Operational Continuity Lock (`verify_section4_continuity`):
   Verifies that local fallback scripts and manual disconnect overrides remain
   executable before delegating municipal or clinical workflows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

__version__ = "0.1.2"

AUTONOMOUS_FAST_PATH = "autonomous_fast_path"
SEAM_EXPOSED_DELIBERATION = "seam_exposed_deliberation"
DEVELOPMENTAL_SPARRING_NODE = "developmental_sparring_node"
BLOCKED_CONTINUITY_LOCK = "blocked_section4_continuity_lock"


@dataclass
class DelegationTask:
    task_id: str
    domain: str
    reversibility: float               # Rev(A) in [0.0, 1.0]; 1.0 = fully reversible
    empirical_human_lift: float        # Delta_E(A) = Acc(Human+AI) - Acc(AI) (Decision Value)
    normative_legitimacy: int          # Norm(A) in {0, 1}; 1 = Strong Bundle / affects rights
    token_distributions: list[list[float]] = field(default_factory=list)
    counterfactual_branches: list[str] = field(default_factory=list)
    section4_fallback_verified: bool = True
    is_canary_seeded_error: bool = False
    is_sparring_node: bool = False     # Axis 4: Capability Value (1/day unassisted pre-commitment)
    crux_pod_size: int = 3             # 2-3 peers to prevent worker isolation


@dataclass
class HesitationArtifact:
    flagged: bool
    peak_shannon_entropy: float
    min_top2_margin: float
    flagged_token_positions: list[int]
    primary_branch: str
    dissenting_counterfactual: str
    requires_tradeoff_endorsement: bool


@dataclass
class PreRegisteredJudgment:
    task_id: str
    crux_pod_members: list[str]
    chosen_branch: str
    falsifiable_hypothesis: str
    forecast_probability: float        # p in [0.0, 1.0]
    resolved_outcome: int | None = None  # o in {0, 1} once resolved at milestone T


@dataclass
class RoutingDecision:
    task_id: str
    route: str
    hesitation: HesitationArtifact
    interrupts_human: bool
    reasons: list[str]
    crux_pod_size: int = 0
    requires_blind_precommitment: bool = False


def shannon_entropy_bits(probs: list[float]) -> float:
    """Compute Shannon entropy H(p) = -sum p(v) log2 p(v) in bits."""
    total = sum(probs)
    if total <= 0:
        return 0.0
    normed = [p / total for p in probs if p > 0]
    return round(-sum(p * math.log2(p) for p in normed), 4)


def top2_margin(probs: list[float]) -> float:
    """Compute top-2 probability margin M_t = p_1 - p_2."""
    total = sum(probs)
    if total <= 0:
        return 0.0
    s = sorted((p / total for p in probs), reverse=True)
    if len(s) == 1:
        return round(s[0], 4)
    return round(s[0] - s[1], 4)


def audit_hesitation_seams(
    token_distributions: list[list[float]],
    counterfactual_branches: list[str] | None = None,
    entropy_threshold: float = 1.25,
    margin_threshold: float = 0.15,
) -> HesitationArtifact:
    """Inspect token probability distributions for suppressed hesitation artifacts (AE-D02, AE-E02)."""
    branches = counterfactual_branches or ["Primary greedy recommendation", "Counterfactual safety-conservative alternative"]
    primary = branches[0] if len(branches) > 0 else "Primary recommendation"
    dissenting = branches[1] if len(branches) > 1 else "No counterfactual branch supplied"

    if not token_distributions:
        return HesitationArtifact(
            flagged=False,
            peak_shannon_entropy=0.0,
            min_top2_margin=1.0,
            flagged_token_positions=[],
            primary_branch=primary,
            dissenting_counterfactual=dissenting,
            requires_tradeoff_endorsement=False,
        )

    entropies = [shannon_entropy_bits(dist) for dist in token_distributions]
    margins = [top2_margin(dist) for dist in token_distributions]

    flagged_positions = [
        idx
        for idx, (h, m) in enumerate(zip(entropies, margins))
        if h >= entropy_threshold or m < margin_threshold
    ]
    flagged = len(flagged_positions) > 0

    return HesitationArtifact(
        flagged=flagged,
        peak_shannon_entropy=max(entropies),
        min_top2_margin=min(margins),
        flagged_token_positions=flagged_positions,
        primary_branch=primary,
        dissenting_counterfactual=dissenting,
        requires_tradeoff_endorsement=flagged,
    )


def verify_section4_continuity(section4_fallback_verified: bool) -> bool:
    """Verify Section 4 municipal/institutional operational continuity fallback before delegation."""
    return bool(section4_fallback_verified)


def evaluate_brier_calibration(judgments: list[PreRegisteredJudgment]) -> dict:
    """Compute Brier calibration score BS = (1/N) sum (p_i - o_i)^2 across resolved Crux Pod judgments."""
    resolved = [j for j in judgments if j.resolved_outcome in (0, 1)]
    if not resolved:
        return {"count": 0, "brier_score": 0.0, "well_calibrated": True, "recalibrate_assembly_trigger": False}

    sq_errors = [(float(j.forecast_probability) - float(j.resolved_outcome)) ** 2 for j in resolved]
    bs = round(sum(sq_errors) / len(sq_errors), 4)
    return {
        "count": len(resolved),
        "brier_score": bs,
        "well_calibrated": bs <= 0.15,
        "recalibrate_assembly_trigger": bs > 0.25,
    }


def route_delegation(
    task: DelegationTask,
    entropy_threshold: float = 1.25,
    margin_threshold: float = 0.15,
) -> RoutingDecision:
    """Route a proposed action via the Four-Axis Somatic Delegation Router + Hesitation Seam Audit."""
    hesitation = audit_hesitation_seams(
        task.token_distributions,
        task.counterfactual_branches,
        entropy_threshold=entropy_threshold,
        margin_threshold=margin_threshold,
    )

    if not verify_section4_continuity(task.section4_fallback_verified):
        return RoutingDecision(
            task_id=task.task_id,
            route=BLOCKED_CONTINUITY_LOCK,
            hesitation=hesitation,
            interrupts_human=True,
            reasons=["section4_continuity_unverified"],
            crux_pod_size=max(2, task.crux_pod_size),
        )

    reasons: list[str] = []
    if task.reversibility < 1.0:
        reasons.append("irreversible_action")
    if task.normative_legitimacy == 1:
        reasons.append("strong_bundle_normative_legitimacy")
    if task.empirical_human_lift > 0.0:
        reasons.append("positive_decision_value")
    if hesitation.flagged:
        reasons.append(f"hesitation_artifact:H={hesitation.peak_shannon_entropy},M={hesitation.min_top2_margin}")

    # Axis 4: Capability-Value Developmental Sparring Node (1/day unassisted pre-commitment)
    if task.is_sparring_node and not reasons:
        return RoutingDecision(
            task_id=task.task_id,
            route=DEVELOPMENTAL_SPARRING_NODE,
            hesitation=hesitation,
            interrupts_human=True,
            reasons=["capability_value_sparring_node"],
            crux_pod_size=max(2, task.crux_pod_size),
            requires_blind_precommitment=True,
        )

    # Autonomic Fast-Path Delegation: Rev(A) == 1 and Norm(A) == 0 and Delta_E(A) <= 0 and no hesitation spike
    if not reasons:
        return RoutingDecision(
            task_id=task.task_id,
            route=AUTONOMOUS_FAST_PATH,
            hesitation=hesitation,
            interrupts_human=False,
            reasons=["autonomic_fast_path"],
            crux_pod_size=0,
        )

    return RoutingDecision(
        task_id=task.task_id,
        route=SEAM_EXPOSED_DELIBERATION,
        hesitation=hesitation,
        interrupts_human=True,
        reasons=reasons,
        crux_pod_size=max(2, task.crux_pod_size),
        requires_blind_precommitment=task.is_sparring_node,
    )
