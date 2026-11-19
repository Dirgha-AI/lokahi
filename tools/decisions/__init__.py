# -*- coding: utf-8 -*-
"""Experimental learned decisions tier for LOKAHI (v0.1.4). Off by default.

Runtime code here is standard library only. Training (`train.py`) is optional
and is the only file that uses scikit-learn; nothing imports it at runtime.
See docs/decisions/CHARTER.md and the README section "Experimental: learned
decisions tier".
"""
from __future__ import annotations

from .charter import ALLOW, ESCALATE, LABELS, REFUSE, SEVERITY, hard_rules, normalize
from .model import DEFAULT_MODEL_PATH, JsonDecisionModel, featurize, load_model
from .tier import DecisionTier, combine, decide, system_decision

__all__ = [
    "ALLOW", "ESCALATE", "REFUSE", "LABELS", "SEVERITY",
    "DEFAULT_MODEL_PATH", "DecisionTier", "JsonDecisionModel",
    "combine", "decide", "featurize", "hard_rules", "load_model", "normalize", "system_decision",
]
