"""Validate raw generated rows against data/decisions/schema.json and docs/decisions/CHARTER.md, then dedupe.

Reads data/decisions/raw/*.jsonl.txt, writes data/decisions/seed.jsonl (clean, CC-BY-4.0) and
data/decisions/validation_report.json (counts of every reason a row was dropped).
A row is dropped, never repaired, so the clean set only holds rows the
generator got right by itself.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]  # repository root
DATA = ROOT / "data" / "decisions"
sys.path.insert(0, str(ROOT / "tools"))
from decisions.charter import charter_problems  # noqa: E402,F401  (re-exported for callers)

import jsonschema  # noqa: E402

SCHEMA = json.loads((DATA / "schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

SCRIPT_RANGES = {  # languages whose script we can check cheaply
    "hi": (0x0900, 0x097F), "bn": (0x0980, 0x09FF), "ar": (0x0600, 0x06FF),
}
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[a-z]{2,}", re.I)
SAFE_EMAIL = re.compile(r"@([\w-]+\.)*(example|test|invalid|localhost)(\.[a-z]+)?\b", re.I)
LONG_DIGITS = re.compile(r"\d[\d \-]{9,}\d")


def text_problems(r: dict) -> list[str]:
    p = []
    text = r["args_summary"]
    rng = SCRIPT_RANGES.get(r["language"])
    if rng and not any(rng[0] <= ord(ch) <= rng[1] for ch in text):
        p.append("language_script_mismatch")
    if r["language"] != "en" and r["language"] not in SCRIPT_RANGES:
        ascii_words = re.findall(r"[A-Za-z]+", text)
        common_en = {"the", "and", "to", "of", "for", "with", "all", "from"}
        if ascii_words and sum(w.lower() in common_en for w in ascii_words) / len(ascii_words) > 0.2:
            p.append("looks_english_not_" + r["language"])
    for m in EMAIL.finditer(text):
        if not SAFE_EMAIL.search(m.group(0)):
            p.append("possible_real_email")
            break
    if LONG_DIGITS.search(text):
        p.append("possible_phone_or_id_number")
    return p


def norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def main() -> None:
    drops = collections.Counter()
    kept, seen = [], set()
    hard_args = {norm(json.loads(l)["args_summary"]) for l in (DATA / "hard_test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    total = 0
    for path in sorted((DATA / "raw").glob("*.jsonl.txt")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            total += 1
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                drops["bad_json"] += 1
                continue
            r.setdefault("hard_case", "none")
            errs = list(VALIDATOR.iter_errors(r))
            if errs:
                drops["schema:" + (errs[0].path[0] if errs[0].path else errs[0].validator)] += 1
                continue
            probs = charter_problems(r) + text_problems(r)
            if probs:
                for x in probs:
                    drops[x] += 1
                drops["_rows_dropped_for_charter_or_text"] += 1
                continue
            key = norm(r["args_summary"])
            if key in hard_args:
                drops["overlaps_hard_test"] += 1
                continue
            if key in seen:
                drops["duplicate"] += 1
                continue
            seen.add(key)
            r["id"] = f"seed-{len(kept) + 1:04d}"
            kept.append(r)
    with (DATA / "seed.jsonl").open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = {
        "raw_rows": total,
        "kept": len(kept),
        "dropped_reasons": dict(drops.most_common()),
        "labels": dict(collections.Counter(r["label"] for r in kept)),
        "reason_codes": dict(sorted(collections.Counter(r["reason_code"] for r in kept).items())),
        "languages": dict(collections.Counter(r["language"] for r in kept).most_common()),
        "domains": dict(collections.Counter(r["domain"] for r in kept).most_common()),
        "hard_case": dict(collections.Counter(r["hard_case"] for r in kept).most_common()),
        "tool_class": dict(collections.Counter(r["tool_class"] for r in kept)),
    }
    (DATA / "validation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
