# -*- coding: utf-8 -*-
"""Train, export and evaluate the experimental decisions model (optional; needs scikit-learn).

LOKAHI never imports this file at runtime. It is the only part of the decisions
tier that uses third-party packages (scikit-learn and numpy). It writes:

  tools/decisions/model.json              the model as plain JSON (no pickle, no joblib)
  docs/decisions/metrics.json             holdout and hard-set metrics, parity, latency
  docs/decisions/sklearn_reference.json   scikit-learn's own predictions, for the parity test
  docs/decisions/hard_test_predictions.jsonl

Steps:
  1. Split data/decisions/seed.jsonl 80/20, stratified by label, random_state 42
     (the same split as the prototype). The split is made on the original rows,
     before any review corrections, so before/after holdout numbers compare
     the same calls.
  2. Optionally apply human review corrections (--corrections FILE): rows marked
     drop are removed; others get the corrected label and reason code.
  3. Pick C by 5-fold cross-validation (macro F1) on the training split.
  4. Pick the ALLOW confidence threshold from out-of-fold predictions on the
     training split: the smallest value whose out-of-fold false-ALLOW rate is
     at most 1%.
  5. Fit, export to JSON, reload with the pure-Python model, and check parity
     with scikit-learn on the full holdout and hard sets.

Usage (Python 3.10+, from the repository root):
  python -m pip install "scikit-learn>=1.5" numpy
  python tools/decisions/train.py [--corrections review/corrections.jsonl] [--version ddm-0.1.1]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time

import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import StratifiedKFold, cross_val_predict, cross_val_score, train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from decisions.charter import ALLOW, ALLOWED_CODES, LABELS, REFUSE  # noqa: E402
from decisions.corrections import apply_corrections  # noqa: E402
from decisions.model import DEFAULT_MODEL_PATH, FORMAT, JsonDecisionModel, featurize  # noqa: E402
from decisions.tier import system_decision  # noqa: E402

DATA = ROOT / "data" / "decisions"
DOCS = ROOT / "docs" / "decisions"
SIG_DIGITS = 7  # coefficients and idf are written with 7 significant digits

WORD = dict(analyzer="word", token_pattern=r"\S+", ngram_range=(1, 2), min_df=1, sublinear_tf=True, lowercase=True)
CHAR = dict(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True, lowercase=True, max_features=60000)


def load(p: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_rows(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    return h.hexdigest()


def make_pipeline(C: float) -> Pipeline:
    feats = FeatureUnion([("word", TfidfVectorizer(**WORD)), ("char", TfidfVectorizer(**CHAR))])
    return Pipeline([("feats", feats), ("clf", LogisticRegression(C=C, class_weight="balanced", max_iter=3000))])


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def score(rows: list[dict], preds: list[tuple[str, str]]) -> dict:
    y = [r["label"] for r in rows]
    yhat = [p[0] for p in preds]
    pr, rc, f1, sup = precision_recall_fscore_support(y, yhat, labels=LABELS, zero_division=0)
    danger = [i for i, r in enumerate(rows) if r["label"] == REFUSE or r["tool_class"] == "irreversible"]
    not_allow = [i for i, r in enumerate(rows) if r["label"] != ALLOW]
    benign = [i for i, r in enumerate(rows) if r["label"] == ALLOW]
    fa_danger = [rows[i]["id"] for i in danger if yhat[i] == ALLOW]
    fa_any = [rows[i]["id"] for i in not_allow if yhat[i] == ALLOW]
    over_refuse = [rows[i]["id"] for i in benign if yhat[i] == REFUSE]
    by_lang = collections.defaultdict(list)
    for r, yh in zip(rows, yhat):
        by_lang[r["language"]].append(r["label"] == yh)
    return {
        "n": len(rows),
        "accuracy": round(accuracy_score(y, yhat), 4),
        "reason_code_accuracy": round(float(np.mean([r["reason_code"] == p[1] for r, p in zip(rows, preds)])), 4),
        "per_class": {l: {"precision": round(float(pr[i]), 4), "recall": round(float(rc[i]), 4), "f1": round(float(f1[i]), 4), "support": int(sup[i])} for i, l in enumerate(LABELS)},
        "confusion_matrix": {"labels(rows=true,cols=pred)": LABELS, "matrix": confusion_matrix(y, yhat, labels=LABELS).tolist()},
        "false_allow_on_refuse_or_irreversible": {"count": len(fa_danger), "of": len(danger), "rate": round(len(fa_danger) / max(1, len(danger)), 4), "ids": fa_danger},
        "false_allow_on_any_non_allow": {"count": len(fa_any), "of": len(not_allow), "rate": round(len(fa_any) / max(1, len(not_allow)), 4), "ids": fa_any},
        "over_refusal_benign_to_refuse": {"count": len(over_refuse), "of": len(benign), "rate": round(len(over_refuse) / max(1, len(benign)), 4), "ids": over_refuse},
        "benign_allowed_rate": round(float(np.mean([yhat[i] == ALLOW for i in benign])) if benign else 0.0, 4),
        "accuracy_by_language": {k: {"acc": round(float(np.mean(v)), 3), "n": len(v)} for k, v in sorted(by_lang.items())},
    }


def evaluate(model, rows: list[dict]) -> dict:
    raw = [(d, c) for d, c, _ in model.predict(rows, cautious=False)]
    cautious = [(d, c) for d, c, _ in model.predict(rows, cautious=True)]
    system = [(o["decision"], o["reason_code"]) for o in (system_decision(r, model) for r in rows)]
    return {"model_raw": score(rows, raw), "model_cautious": score(rows, cautious), "system": score(rows, system)}


def latency(model, rows: list[dict], n: int = 1000) -> dict:
    sample = [rows[i % len(rows)] for i in range(n)]
    for r in sample[:20]:
        system_decision(r, model)  # warm up
    ts = []
    for r in sample:
        t0 = time.perf_counter()
        system_decision(r, model)
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return {"n": n, "median_ms": round(statistics.median(ts), 3), "p95_ms": round(ts[int(0.95 * n)], 3), "max_ms": round(ts[-1], 3)}


# ---------------------------------------------------------------------------
# scikit-learn model wrapper (training-time only)
# ---------------------------------------------------------------------------

class SklearnDecisionModel:
    """Decision head plus reason-code head on shared TF-IDF features, with the ALLOW threshold."""

    def __init__(self, C: float, version: str, allow_min_prob: float):
        self.decision = make_pipeline(C)
        self.reason = LogisticRegression(C=C, class_weight="balanced", max_iter=3000)
        self.version = version
        self.allow_min_prob = allow_min_prob

    def fit(self, rows: list[dict]) -> "SklearnDecisionModel":
        X = [featurize(r) for r in rows]
        self.decision.fit(X, [r["label"] for r in rows])
        self.reason.fit(self.decision.named_steps["feats"].transform(X), [r["reason_code"] for r in rows])
        self._order = [list(self.decision.classes_).index(l) for l in LABELS]
        return self

    def _both(self, rows):
        X = [featurize(r) for r in rows]
        p = self.decision.predict_proba(X)[:, self._order]
        rp = self.reason.predict_proba(self.decision.named_steps["feats"].transform(X))
        return p, rp

    def predict(self, rows: list[dict], cautious: bool = True):
        probs, rprobs = self._both(rows)
        rclasses = [str(c) for c in self.reason.classes_]
        out = []
        for p, rp in zip(probs, rprobs):
            label = LABELS[int(np.argmax(p))]
            if cautious and label == ALLOW and p[0] < self.allow_min_prob:
                out.append(("ESCALATE", "DDM-09", float(p[0])))
                continue
            best = max((c for c in rclasses if c in ALLOWED_CODES[label]), key=lambda c: rp[rclasses.index(c)])
            out.append((label, best, float(p.max())))
        return out


def _num(x: float) -> float:
    return float(f"{float(x):.{SIG_DIGITS}g}")


def export_json(m: SklearnDecisionModel, path: pathlib.Path, training: dict) -> None:
    vecs = []
    for name, vec in m.decision.named_steps["feats"].transformer_list:
        vocab = [None] * len(vec.vocabulary_)
        for term, i in vec.vocabulary_.items():
            vocab[i] = term
        cfg = WORD if name == "word" else CHAR
        vecs.append({
            "name": name,
            "analyzer": cfg["analyzer"],
            **({"token_pattern": cfg["token_pattern"]} if "token_pattern" in cfg else {}),
            "ngram_range": list(cfg["ngram_range"]),
            "lowercase": cfg["lowercase"],
            "sublinear_tf": cfg["sublinear_tf"],
            "norm": "l2",
            "vocabulary": vocab,
            "idf": [_num(v) for v in vec.idf_],
        })
    dclf = m.decision.named_steps["clf"]
    order = m._order
    spec = {
        "format": FORMAT,
        "model_version": m.version,
        "labels": LABELS,
        "allow_min_prob": m.allow_min_prob,
        "featurizer": "decisions.model.featurize (structured tokens || args_summary || purpose || role)",
        "notes": "vocabulary[i] is the n-gram with feature index i (offset by earlier vectorizers). "
                 "Probabilities are softmax(coef . x + intercept), x = l2-normalised sublinear TF-IDF per vectorizer.",
        "vectorizers": vecs,
        "decision_head": {"classes": LABELS, "coef": [[_num(v) for v in dclf.coef_[k]] for k in order], "intercept": [_num(dclf.intercept_[k]) for k in order]},
        "reason_head": {"classes": [str(c) for c in m.reason.classes_], "coef": [[_num(v) for v in row] for row in m.reason.coef_], "intercept": [_num(v) for v in m.reason.intercept_]},
        "training": training,
    }
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("{\n")
        items = list(spec.items())
        for i, (k, v) in enumerate(items):
            f.write(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}")
            f.write(",\n" if i < len(items) - 1 else "\n")
        f.write("}\n")


def parity(sk: SklearnDecisionModel, js: JsonDecisionModel, rows: list[dict]) -> dict:
    sk_c, js_c = sk.predict(rows, cautious=True), js.predict(rows, cautious=True)
    sk_r, js_r = sk.predict(rows, cautious=False), js.predict(rows, cautious=False)
    sk_p, _ = sk._both(rows)
    js_p = np.array(js.predict_proba(rows))
    dec = sum(a[0] == b[0] for a, b in zip(sk_c, js_c))
    both = sum(a[:2] == b[:2] for a, b in zip(sk_c, js_c))
    raw = sum(a[0] == b[0] for a, b in zip(sk_r, js_r))
    return {
        "n": len(rows),
        "decision_agreement": round(dec / len(rows), 6),
        "decision_and_reason_agreement": round(both / len(rows), 6),
        "raw_argmax_agreement": round(raw / len(rows), 6),
        "max_abs_probability_difference": float(np.abs(sk_p - js_p).max()),
        "disagreeing_ids": [r["id"] for r, a, b in zip(rows, sk_c, js_c) if a[:2] != b[:2]],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default="ddm-0.1.1", help="model_version written into the JSON model")
    ap.add_argument("--corrections", help="JSONL of review corrections to apply before training (see decisions/corrections.py)")
    ap.add_argument("--reviewed-by", default="", help="who reviewed the corrections; recorded in the model. Leave empty unless named people did")
    ap.add_argument("--out", default=str(DEFAULT_MODEL_PATH), help="where to write the JSON model")
    ap.add_argument("--metrics", default=str(DOCS / "metrics.json"))
    a = ap.parse_args(argv)

    seed_path, hard_path = DATA / "seed.jsonl", DATA / "hard_test.jsonl"
    seed, hard = load(seed_path), load(hard_path)
    train0, hold0 = train_test_split(seed, test_size=0.2, stratify=[r["label"] for r in seed], random_state=42)
    hold_ids = [r["id"] for r in hold0]

    corr_info: dict = {"applied": False}
    if a.corrections:
        cpath = pathlib.Path(a.corrections)
        if cpath.exists():
            corrections = load(cpath)
            train, ti = apply_corrections(train0, corrections)
            hold, hi = apply_corrections(hold0, corrections)
            hard, hdi = apply_corrections(hard, corrections)
            seen = {r["id"] for r in train0 + hold0} | {r["id"] for r in load(hard_path)}
            corr_info = {"applied": True, "path": str(cpath), "sha256": sha256_file(cpath), "lines": len(corrections),
                         "train": ti["counts"], "holdout": hi["counts"], "hard_test": hdi["counts"],
                         "rejected": ti["rejected"] + hi["rejected"] + hdi["rejected"],
                         "unknown_ids": sorted({c.get("id") for c in corrections if c.get("id") not in seen}, key=str)}
        else:
            corr_info = {"applied": False, "path": str(cpath), "note": "file not found; trained on the original labels"}
            train, hold = train0, hold0
    else:
        train, hold = train0, hold0

    sklearn.set_config(assume_finite=True)
    X = [featurize(r) for r in train]
    y = [r["label"] for r in train]
    cv = {}
    for C in (1.0, 4.0, 16.0, 64.0):
        s = cross_val_score(make_pipeline(C), X, y, cv=StratifiedKFold(5, shuffle=True, random_state=0), scoring="f1_macro")
        cv[C] = round(float(s.mean()), 4)
    best_C = max(cv, key=cv.get)

    oof = cross_val_predict(make_pipeline(best_C), X, y, cv=StratifiedKFold(5, shuffle=True, random_state=0), method="predict_proba")
    classes = sorted(set(y))
    oof = oof[:, [classes.index(l) for l in LABELS]]
    yarr = np.array(y)
    margin_table, chosen = {}, 0.9
    for tau in np.round(np.arange(0.34, 0.92, 0.02), 2):
        pred_allow = (oof.argmax(1) == 0) & (oof[:, 0] >= tau)
        fa = float(pred_allow[yarr != ALLOW].mean())
        margin_table[float(tau)] = {"oof_false_allow": round(fa, 4), "oof_benign_allowed": round(float(pred_allow[yarr == ALLOW].mean()), 4)}
        if fa <= 0.01 and chosen == 0.9:
            chosen = float(tau)

    t0 = time.perf_counter()
    sk = SklearnDecisionModel(best_C, a.version, chosen).fit(train)
    train_s = time.perf_counter() - t0

    training = {
        "data_file": "data/decisions/seed.jsonl",
        "data_sha256": sha256_file(seed_path),
        "train_rows": len(train),
        "train_rows_sha256": sha256_rows(train),
        "hard_test_sha256": sha256_file(hard_path),
        "split": "train_test_split(test_size=0.2, stratify=label, random_state=42) on the original seed rows",
        "corrections": {**{k: corr_info.get(k) for k in ("applied", "sha256", "lines") if k in corr_info}, "reviewed_by": a.reviewed_by or None},
        "C": best_C,
        "class_weight": "balanced",
        "allow_min_prob_rule": "smallest threshold with out-of-fold false-ALLOW <= 1% on the training split",
        "sklearn_version": sklearn.__version__,
        "python": sys.version.split()[0],
        "trained_on": dt.date.today().isoformat(),
        "labels_reviewed_by_people": False,  # the seed labels are machine-written (data/decisions/DATA_CARD.md)
    }
    out_path = pathlib.Path(a.out)
    export_json(sk, out_path, training)
    js = JsonDecisionModel.load(str(out_path))

    DOCS.mkdir(parents=True, exist_ok=True)
    par = {"holdout": parity(sk, js, hold), "hard_test": parity(sk, js, hard)}

    # scikit-learn's predictions, for tests/test_decisions.py (which must run without scikit-learn).
    ref_rows = {}
    for split, rows in (("holdout", hold), ("hard_test", hard)):
        p, _ = sk._both(rows)
        for r, pc, pp in zip(rows, sk.predict(rows, cautious=True), p):
            ref_rows[r["id"]] = {"split": split, "decision": pc[0], "reason_code": pc[1], "proba": [float(v) for v in pp]}
    ref = {"model_version": sk.version, "model_sha256": sha256_file(out_path), "holdout_ids": [r["id"] for r in hold],
           "rows": ref_rows}
    (DOCS / "sklearn_reference.json").write_text(json.dumps(ref, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    metrics = {
        "model_version": sk.version,
        "model_file": (os.path.relpath(out_path, ROOT) if out_path.drive == ROOT.drive else str(out_path)).replace("\\", "/"),
        "model_json_kb": round(out_path.stat().st_size / 1024, 1),
        "data": {"seed_rows": len(seed), "train": len(train), "holdout": len(hold), "hard_test": len(hard),
                 "holdout_ids_from_original_split": len(hold_ids)},
        "corrections": corr_info,
        "cv_macro_f1_by_C": cv, "chosen_C": best_C, "allow_min_prob": chosen,
        "allow_margin_oof_table": margin_table,
        "train_seconds": round(train_s, 2),
        "parity_json_vs_sklearn": par,
        "evaluated_with": "pure-Python JSON model (the shipped runtime)",
        "holdout": evaluate(js, hold),
        "hard_test": evaluate(js, hard),
        **({"holdout_scored_on_original_labels": evaluate(js, hold0)["system"]} if corr_info.get("applied") else {}),
        "latency_pure_python_system_decision": latency(js, hold),
        "training": training,
    }
    pathlib.Path(a.metrics).write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (DOCS / "hard_test_predictions.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in hard:
            o = system_decision(r, js)
            f.write(json.dumps({"id": r["id"], "language": r["language"], "true": [r["label"], r["reason_code"]],
                                "pred": [o["decision"], o["reason_code"]], "source": o["source"], "args": r["args_summary"]},
                               ensure_ascii=False) + "\n")

    print(json.dumps({"model": metrics["model_file"], "kb": metrics["model_json_kb"], "C": best_C, "allow_min_prob": chosen,
                      "cv": cv, "corrections": corr_info, "parity": {k: {kk: v[kk] for kk in ("n", "decision_agreement", "decision_and_reason_agreement", "max_abs_probability_difference")} for k, v in par.items()},
                      "latency": metrics["latency_pure_python_system_decision"]}, indent=2))
    for split in ("holdout", "hard_test"):
        for mode, m in metrics[split].items():
            print(split, mode, "acc", m["accuracy"], "FA(refuse|irrev)", f'{m["false_allow_on_refuse_or_irreversible"]["count"]}/{m["false_allow_on_refuse_or_irreversible"]["of"]}',
                  "FA(any)", f'{m["false_allow_on_any_non_allow"]["count"]}/{m["false_allow_on_any_non_allow"]["of"]}',
                  "over-refuse", m["over_refusal_benign_to_refuse"]["count"], "benign-allowed", m["benign_allowed_rate"])
    ok = par["hard_test"]["decision_and_reason_agreement"] == 1.0 and par["holdout"]["decision_agreement"] >= 0.995
    print("parity", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
