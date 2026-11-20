# Policy packs

Starting manifests for three sectors. They are **unsigned templates**: sign
them with your own steward key before use, and review every tool entry against
what your agents can actually reach.

| Pack | Node | Threshold | Irreversible tools | Physical envelope |
|---|---|---|---|---|
| `utility/water-board.manifest.json` | municipal water board | 2 of 3 | valve cutoff, crew dispatch | yes (6.1 N, 0.5 m/s) |
| `hospital/clinical-board.manifest.json` | clinical board | 2 of 3 | care-plan change, discharge, medication | no |
| `editorial/editorial-desk.manifest.json` | publication desk | 2 of 3 | email author, publish | no |

## Using a pack

```bash
python tools/lokahi_sign.py keygen --ed25519 --out steward.key
python tools/lokahi_sign.py manifest --key-file steward.key \
  --in policies/utility/water-board.manifest.json --out manifest.json

python tools/lokahi_sign.py keygen --threshold 2 --n 3 --out-dir validators
python tools/mcp_stdio_proxy.py \
  --manifest manifest.json --key-file steward.key \
  --validator-keys validators/approvers.json \
  --http-listen 127.0.0.1:8787
```

Then pin the tools you actually observed:

```bash
python tools/lokahi_toolpin.py pin \
  --tools-list observed-tools.json --manifest manifest.json \
  --key-file pin.key --pinned-by org:security --out toolpins.json
```

## What each pack gets right

- **Forbidden targets** are fields the agent must never optimise for or read.
  They are refused before any consent window opens.
- **Residency** refuses writes that would land outside the stated jurisdictions.
- **Population-affecting** actions always need a consent quorum, even if a tool
  is technically reversible.
- **The utility pack carries a physical envelope** so an actuator command
  outside force/velocity/workspace bounds is refused and the interlock reports
  `safe_mechanical_hold`.

## What you must change

- `expires` — set a real date, short enough that a stale manifest fails closed.
- `steward` — a `did:web` for the body that survives staff turnover.
- Every `args` entry — confirm the argument really maps to that feature and
  that the feature is one you permit.
- `consent_threshold` — 2-of-3 is a starting point, not a recommendation.
  A small team may want 2-of-2; a public utility may want 3-of-5.
