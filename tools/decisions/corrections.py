# -*- coding: utf-8 -*-
"""Apply human review corrections to labelled rows (standard library only).

A corrections file is JSONL, one object per reviewed row:

    {"id": "seed-0123", "action": "drop"}                                  remove the row
    {"id": "seed-0456", "label": "REFUSE", "reason_code": "DDM-06"}        relabel it
    {"id": "seed-0789", "label": "ALLOW", "reason_code": "DDM-10", "rationale": "..."}

`"drop": true` or `"label": "drop"` also drop a row; `new_label` and
`new_reason_code` are accepted as synonyms. A later line for the same id wins.
A correction that contradicts the charter's hard rules (for example ALLOW on
an irreversible tool) is reported and the row is dropped, not kept with a bad
label. train.py uses this; DDM-13 says only reviewed labels may retrain the model.
"""
from __future__ import annotations

import collections

from .charter import LABELS, charter_problems


def is_drop(c: dict) -> bool:
    action = str(c.get("action", c.get("decision", ""))).lower()
    return bool(c.get("drop")) or action == "drop" or str(c.get("label", "")).lower() == "drop"


def apply_corrections(rows: list[dict], corrections: list[dict]) -> tuple[list[dict], dict]:
    """Return (corrected rows, report). Rows without a correction pass through unchanged."""
    by_id = {str(c["id"]): c for c in corrections if isinstance(c, dict) and "id" in c}
    counts: collections.Counter = collections.Counter()
    out, rejected = [], []
    for r in rows:
        c = by_id.get(r["id"])
        if c is None:
            out.append(r)
            continue
        if is_drop(c):
            counts["dropped"] += 1
            continue
        new = dict(r)
        label = c.get("label", c.get("new_label", r["label"]))
        code = c.get("reason_code", c.get("new_reason_code", r["reason_code"]))
        new["label"], new["reason_code"] = label, code
        if c.get("rationale"):
            new["rationale"] = c["rationale"]
        if label not in LABELS:
            rejected.append({"id": r["id"], "problem": [f"unknown label {label!r}"]})
            counts["rejected_and_dropped"] += 1
            continue
        try:
            problems = charter_problems(new)
        except KeyError as exc:
            problems = [f"bad reason code {exc}"]
        if problems:
            rejected.append({"id": r["id"], "problem": problems})
            counts["rejected_and_dropped"] += 1
            continue
        counts["relabelled" if (label, code) != (r["label"], r["reason_code"]) else "confirmed"] += 1
        out.append(new)
    return out, {"counts": dict(counts), "rejected": rejected}
