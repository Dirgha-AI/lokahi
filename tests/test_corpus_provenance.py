"""The red-team corpus states one provenance per scenario, in two artefacts.

`data/LOKAHI_RedTeam_Corpus_v1.0.md` carries a prose contract (34 scenarios
verbatim from the manuscript, 16 generalised and flagged for author review) and a
per-row table that names which is which. `data/LOKAHI_RedTeam_Corpus_v1.0.csv` is
the machine-readable copy that `tools/run_evals.py` and every downstream reader
loads, and it carries the same fact in its `verified` column.

Two artefacts holding one fact drift. This test is the interlock: the markdown
table is the authored source, the CSV is the derived copy, and any row where they
disagree fails here rather than in a provenance claim nobody can trace. A row the
markdown does not unambiguously mark `yes` is required to say
`needs-author-review` in the CSV, so an ambiguity fails closed.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "LOKAHI_RedTeam_Corpus_v1.0.csv"
MD_PATH = ROOT / "data" / "LOKAHI_RedTeam_Corpus_v1.0.md"

PROVENANCE = ("yes", "needs-author-review")
FALLBACK = "needs-author-review"


def markdown_provenance():
    """Scenario ID -> provenance, read from the last column of the markdown table."""
    rows = {}
    for line in MD_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `AE-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        scenario_id = cells[0].strip("`")
        provenance = cells[-1].strip("`").strip()
        rows[scenario_id] = provenance if provenance in PROVENANCE else FALLBACK
    return rows


def csv_provenance():
    """Scenario ID -> `verified` cell, in file order."""
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        return {row["id"]: row["verified"] for row in csv.DictReader(handle)}


def markdown_counts():
    """The two counts the markdown prose claims for itself."""
    prose = MD_PATH.read_text(encoding="utf-8")
    found = {
        label: int(count)
        for count, label in re.findall(r"(\d+) scenarios \(`verified = ([a-z-]+)`\)", prose)
    }
    return found


def test_markdown_and_csv_cover_the_same_scenarios_in_the_same_order():
    assert list(markdown_provenance()) == list(csv_provenance())
    assert len(markdown_provenance()) == 50


def test_every_csv_row_matches_the_markdown_provenance():
    from_markdown = markdown_provenance()
    from_csv = csv_provenance()
    disagreeing = {
        scenario: (from_markdown[scenario], from_csv[scenario])
        for scenario in from_markdown
        if from_csv.get(scenario) != from_markdown[scenario]
    }
    assert not disagreeing, (
        "CSV `verified` disagrees with the markdown provenance table "
        f"(markdown, csv): {disagreeing}"
    )


def test_the_csv_carries_the_counts_the_markdown_claims():
    counts = {label: 0 for label in PROVENANCE}
    for provenance in csv_provenance().values():
        counts[provenance] = counts.get(provenance, 0) + 1
    assert counts == markdown_counts()
