"""Verify that the built wheel actually contains the modules this tree claims.

`pyproject.toml` maps the package `lokahi` onto `tools/`, so a module that runs
from a checkout under `tools/vla/` only ships if the wheel carries it at
`lokahi/vla/`. This script builds the wheel, opens it as a zip and checks the
membership of every module the VLA gate depends on, listing what it found
rather than only reporting a failure. Exit code is zero only when nothing is
missing.

Usage:
    py -3 scripts/verify_wheel.py [wheel output directory]
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "tools"
DEFAULT_WHEEL_DIR = Path("D:/tmp/w4")

GATE_MODULES = (
    "lokahi/vla/geometry.py",
    "lokahi/vla/kinematics.py",
    "lokahi/vla/replanning.py",
    "lokahi/vla/vla_gate.py",
    "lokahi/vla/physical_manifest.py",
    "lokahi/vla/rollout_receipt.py",
)


def adapter_modules() -> list[str]:
    """Every adapter module in the tree, at the path the wheel must carry it."""
    adapters = sorted((PACKAGE_ROOT / "vla" / "adapters").glob("*.py"))
    if not adapters:
        raise SystemExit(f"no adapter modules found under {PACKAGE_ROOT / 'vla' / 'adapters'}")
    return [f"lokahi/vla/adapters/{path.name}" for path in adapters]


def build(wheel_dir: Path) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        ".",
        "--no-deps",
        "--no-build-isolation",
        "-w",
        str(wheel_dir),
    ]
    print("Building:", " ".join(command))
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        raise SystemExit(f"pip wheel exited {completed.returncode}; nothing to inspect")
    built = sorted(wheel_dir.glob("lokahi-*.whl"), key=lambda p: p.stat().st_mtime)
    if not built:
        raise SystemExit(f"no lokahi-*.whl appeared in {wheel_dir}")
    return built[-1]


def check(wheel: Path, required: list[str]) -> list[str]:
    """Print what the wheel holds for each required module; return what is missing."""
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    missing = [name for name in required if name not in names]

    print()
    print(f"wheel   {wheel.name}")
    print(f"size    {wheel.stat().st_size} bytes ({wheel.stat().st_size / 1024:.1f} KiB)")
    print(f"entries {len(names)}")
    print(f"checked {len(required)} modules")
    print()
    for name in required:
        print(f"  {'ok     ' if name in names else 'MISSING'} {name}")
    print()

    if missing:
        print(f"FAILED: {len(missing)} of {len(required)} modules are absent from the wheel:")
        for name in missing:
            print(f"  - {name}")
    else:
        print(f"OK: all {len(required)} modules are present in {wheel.name}")
    return missing


def main(argv: list[str]) -> int:
    wheel_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_WHEEL_DIR
    required = list(GATE_MODULES) + adapter_modules()
    wheel = build(wheel_dir)
    return 1 if check(wheel, required) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
