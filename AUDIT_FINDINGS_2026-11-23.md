# LOKAHI audit findings — 2026-11-23

Independent read-only audit of the shipped v1.3.0 state, followed by remediation.
Each finding carries a verdict: **PASS**, **WARN** (works, but a claim or practice
is weaker than advertised), or **FAIL** (claim is not true).

Audited commit: `c9c1f61` (`main` = tag `v1.3.0`). Remediation landed on top of the
rewritten history (see *Track C*).

## Summary

| Track | Area | Verdict |
|:--|:--|:--|
| A | Package, release and CI integrity | **PASS** (3 notes) |
| B | Runtime behaviour and test claims | **PASS**, 1 **WARN** |
| C | Content of the published repository | **WARN** (remediated) |
| D | Claim accuracy and version consistency | **WARN** (remediated) |

## Track A — package, release, CI: PASS

- CI run `45105576249` succeeded; PyPI publish run `45105397171` succeeded;
  `https://pypi.org/pypi/lokahi/1.3.0/json` returned HTTP 200.
- Wheel contents match `pyproject.toml`; no build artefacts or secrets shipped.

Notes, no action taken:

1. `AUDIT_TASKS.md` carried a stale "commit and push (awaiting confirmation)"
   checkbox long after the work shipped.
2. `release/v1.3.0` is three commits off `main`; harmless, but two release lines
   means two places to check tag state.
3. `__version__` drift (see Track D) would have been caught by a release-time
   assertion comparing `__version__` to the package metadata.

## Track B — runtime behaviour: PASS with one WARN

- `tests/test_core.py`: 70/70 passing.
- `tests/test_decisions.py`: one failure, then five consecutive passes on
  re-run — a flake, not a regression.

**WARN B-1 — flaky receipt comparison (fixed).**
`test_cli_default_is_off_and_flag_turns_it_on` compared two complete CLI receipts
with `assertEqual`. Receipts embed `ts` at one-second resolution, so the test
failed whenever the two runs straddled a second boundary. The test now compares
the receipts with `ts` removed, which is what the assertion was actually about.

## Track C — published repository content: WARN (remediated)

**C-1 — internal strategy documents were recoverable from public history.**
`STRATEGY.md` and `DEVELOPMENT_DIRECTION.md` were added early and deleted in
`398c2ea`. They were absent from every branch tip, but the deleting commit only
removed them going forward: the full text, including the investor section,
remained retrievable from `main`, `release/v1.3.0`, tags `v1.3.0` and
`v1.3.0-rc1`.

Options considered:

| Option | Removes the content | Cost |
|:--|:--|:--|
| Leave as-is, document the exposure | No | Zero, but the text stays public |
| Rewrite published history (chosen) | Yes | All SHAs after the file's introduction change; tag SHAs no longer match existing provenance attestations |
| Delete and recreate the repository | Yes | Loses stars, forks, issues, and every inbound link |

Remediation: history rewritten with `git filter-branch --index-filter` across all
refs, backups purged, objects pruned with `git gc --prune=now`, then
`main`, `release/v1.3.0`, `v1.3.0` and `v1.3.0-rc1` force-pushed behind
`--force-with-lease` guards. Tags `v0.1.3`, `v0.1.4`, `v1.2.0` never contained
the files and were left untouched.

Verification on a fresh clone of the remote: no commit touches either path, no
object in the graph carries the name, and a brute-force scan of every blob for the
document's text found nothing. `main`'s tree is byte-identical to the pre-rewrite
tree (`56cc218`), so no file changed — only history.

Residual risk: GitHub keeps unreferenced objects reachable by direct SHA until
its own garbage collection runs, and any existing clone or fork still holds the
old objects. Neither is under this repository's control.

## Track D — claim accuracy: WARN (remediated)

**D-1 — wrong hash function in the comparison table (fixed).**
`docs/CONCEPTS.md` advertised a "Tamper-evident BLAKE3 hash-chained" ledger. The
implementation has always used SHA-256 (`Ledger.append`, `verify`), as
`ARCHITECTURE.md` and `THREAT_MODEL.md` correctly state. BLAKE3 does appear in the
sibling Dirgha Rust crates (nonce derivation), which is the likely origin of the
mix-up. Corrected to SHA-256.

**D-2 — version drift (fixed).**
`tools/lokahi.py` reported `__version__ = "1.1.0"` and a v0.1.4 module docstring
while the distribution released as 1.3.0. Both now read 1.3.0.

**D-3 — README overclaimed remediation status (fixed).**
"The audit findings of record are resolved" was true of the earlier round but not
of this one. The README now points at this document instead of asserting a clean
sweep. The "270 unit tests" figure was left out rather than updated, because the
count moves with every test added and the number was never load-bearing.

## Not actioned

- Exact unit-test counts in prose. They drift; the CI result is the record.
- `release/v1.3.0` trailing `main`. Intentional during the release window.
