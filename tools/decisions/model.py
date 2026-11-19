# -*- coding: utf-8 -*-
"""Pure-Python inference for the experimental decisions model (standard library only).

The model is a plain JSON file written by `train.py`. It holds:

  * the feature config (two TF-IDF vectorizers: word 1-2 grams on whitespace
    tokens, and character 2-4 grams inside word boundaries);
  * each vectorizer's vocabulary as a list, where an n-gram's position in the
    list is its feature index, and its idf weights;
  * per-class coefficients and intercepts for two logistic-regression heads
    (decision and reason code);
  * the label order, the ALLOW confidence threshold, the model version and a
    hash of the training data.

This module re-implements scikit-learn's featurisation (lowercase, sublinear
tf, idf, l2 norm per vectorizer) and the multinomial softmax, so predictions
match the scikit-learn model that was exported. tests/test_decisions.py checks
that on the full holdout and hard sets via the parity file train.py writes.
Nothing here imports numpy, scikit-learn, pickle or joblib.
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
from array import array
from collections import Counter
from operator import mul

from .charter import ALLOW, ALLOWED_CODES, ESCALATE, LABELS, outside_residency

FORMAT = "lokahi-decisions-model/1"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_PATH = os.path.join(HERE, "model.json")

_WHITE_SPACES = re.compile(r"\s\s+")


def featurize(row: dict) -> str:
    """One text string per tool call: structured tokens, then the free text."""
    ctx = row.get("requester_context", {})
    purpose = str(ctx.get("purpose", "") or "")
    toks = [
        f"__cls_{row.get('tool_class', 'unknown')}",
        f"__appr_{ctx.get('approval_status', 'not_requested')}",
        f"__out_{int(outside_residency(row.get('destination', {})))}",
        "__purpose_empty" if not purpose.strip() else "__purpose_given",
    ]
    toks += [f"__feat_{f}" for f in row.get("data_features", ["none"])]
    toks += [f"__tool_{p}" for p in str(row.get("tool_name", "")).split("_") if p]
    return " ".join(toks) + " || " + str(row.get("args_summary", "")) + " || " + purpose + " || " + str(ctx.get("role", ""))


def _word_analyzer(cfg: dict):
    pattern = re.compile(cfg["token_pattern"])
    lo, hi = cfg["ngram_range"]
    lower = cfg.get("lowercase", True)

    def analyze(text: str) -> list[str]:
        if lower:
            text = text.lower()
        tokens = pattern.findall(text)
        out = list(tokens) if lo == 1 else []
        start = max(lo, 2)
        for n in range(start, min(hi + 1, len(tokens) + 1)):
            for i in range(len(tokens) - n + 1):
                out.append(" ".join(tokens[i:i + n]))
        return out

    return analyze


def _char_wb_analyzer(cfg: dict):
    lo, hi = cfg["ngram_range"]
    lower = cfg.get("lowercase", True)

    def analyze(text: str) -> list[str]:
        if lower:
            text = text.lower()
        text = _WHITE_SPACES.sub(" ", text)
        out = []
        for w in text.split():
            w = " " + w + " "
            w_len = len(w)
            for n in range(lo, hi + 1):
                offset = 0
                out.append(w[offset:offset + n])
                while offset + n < w_len:
                    offset += 1
                    out.append(w[offset:offset + n])
                if offset == 0:  # a short word is counted once
                    break
        return out

    return analyze


_ANALYZERS = {"word": _word_analyzer, "char_wb": _char_wb_analyzer}


def _softmax(z: list[float]) -> list[float]:
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


class JsonDecisionModel:
    """The exported model. `predict()` returns (decision, reason_code, confidence) per row."""

    def __init__(self, spec: dict):
        if spec.get("format") != FORMAT:
            raise ValueError(f"unsupported model format: {spec.get('format')!r}")
        self.spec = spec
        self.version = spec["model_version"]
        self.allow_min_prob = float(spec["allow_min_prob"])
        self.labels = list(spec["labels"])
        if self.labels != LABELS:
            raise ValueError("model label order must be ALLOW, ESCALATE, REFUSE")
        self.reason_codes = list(spec["reason_head"]["classes"])
        self._parts = []
        offset = 0
        for vec in spec["vectorizers"]:
            vocab = {term: i for i, term in enumerate(vec["vocabulary"])}
            if len(vocab) != len(vec["vocabulary"]) or len(vec["idf"]) != len(vocab):
                raise ValueError(f"vectorizer {vec['name']}: vocabulary and idf disagree")
            self._parts.append((_ANALYZERS[vec["analyzer"]](vec), vocab, array("d", vec["idf"]), vec.get("sublinear_tf", True), offset))
            offset += len(vocab)
        self.n_features = offset
        # array('d') keeps ~1M coefficients in 8 bytes each instead of a float object each.
        self._dW = [array("d", row) for row in spec["decision_head"]["coef"]]
        self._db = list(spec["decision_head"]["intercept"])
        self._rW = [array("d", row) for row in spec["reason_head"]["coef"]]
        self._rb = list(spec["reason_head"]["intercept"])
        if spec["decision_head"]["classes"] != self.labels:
            raise ValueError("decision head classes must follow the label order")
        for W in (self._dW, self._rW):
            if any(len(row) != self.n_features for row in W):
                raise ValueError("coefficient width does not match the vocabulary")

    @classmethod
    def load(cls, path: str = DEFAULT_MODEL_PATH) -> "JsonDecisionModel":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------------
    def vector(self, text: str) -> dict[int, float]:
        """Sparse TF-IDF vector {feature index: weight}, l2-normalised per vectorizer."""
        out: dict[int, float] = {}
        for analyze, vocab, idf, sublinear, offset in self._parts:
            counts = Counter(map(vocab.get, analyze(text)))  # counting in C; None collects unknown n-grams
            counts.pop(None, None)
            if not counts:
                continue
            w = {j: ((1.0 + math.log(c)) if sublinear else float(c)) * idf[j] for j, c in counts.items()}
            norm = math.sqrt(sum(v * v for v in w.values()))
            for j, v in w.items():
                out[j + offset] = v / norm
        return out

    @staticmethod
    def _scores(W, b, x: dict[int, float]) -> list[float]:
        idx, vals = list(x.keys()), list(x.values())
        return [bk + sum(map(mul, map(row.__getitem__, idx), vals)) for row, bk in zip(W, b)]

    def predict_proba_one(self, row: dict) -> tuple[list[float], list[float]]:
        """(decision probabilities in LABELS order, reason-code probabilities)."""
        x = self.vector(featurize(row))
        return _softmax(self._scores(self._dW, self._db, x)), _softmax(self._scores(self._rW, self._rb, x))

    def predict_proba(self, rows: list[dict]) -> list[list[float]]:
        return [self.predict_proba_one(r)[0] for r in rows]

    def predict(self, rows: list[dict], cautious: bool = True) -> list[tuple[str, str, float]]:
        out = []
        for r in rows:
            p, rp = self.predict_proba_one(r)
            k = max(range(len(p)), key=lambda i: p[i])  # first max wins, as numpy argmax does
            label = self.labels[k]
            if cautious and label == ALLOW and p[0] < self.allow_min_prob:
                out.append((ESCALATE, "DDM-09", p[0]))
                continue
            allowed = ALLOWED_CODES[label]
            best = max((c for c in self.reason_codes if c in allowed), key=lambda c: rp[self.reason_codes.index(c)])
            out.append((label, best, max(p)))
        return out


@functools.lru_cache(maxsize=4)
def load_model(path: str = DEFAULT_MODEL_PATH) -> JsonDecisionModel:
    return JsonDecisionModel.load(path)
