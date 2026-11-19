# -*- coding: utf-8 -*-
"""Tool pinning: bind the gateway to the exact tool catalogue it approved.

The MCP specification warns that a server can change a tool's description or
input schema after an operator has reviewed it — the "rug pull". LOKAHI cannot
see a tool's live schema from inside a decision, so pinning is split in two:

* **Pin set** — a signed document recording, for every gated tool, the hash of
  the schema the reviewer approved and the hash of the manifest policy entry.
  Built once from an observed ``tools/list`` snapshot.
* **Drift check** — the host adapter (or CI) compares a fresh ``tools/list``
  against the pin set. Any added, removed or changed tool is reported.
* **Manifest check** — the gateway compares the signed manifest's policy digest
  against the pin set on every call, so a manifest that grew or changed a tool
  policy after pinning is refused.

Nothing here claims to detect a server that lies at call time. It detects a
server that changed after review, which is the failure the spec warns about.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from lokahi import Manifest, Signer, canonical, validate_tool_policy

PIN_FORMAT = "lokahi-toolpins/1"

# Volatile fields that must not affect the digest.
_VOLATILE = {"_meta", "annotations"}


def _canonical_json(value: Any) -> bytes:
    return canonical({"_": value})[4:]  # canonical() wraps a dict; strip the wrapper


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def tool_schema_digest(tool: dict) -> str:
    """Hash the parts of a discovered tool that define its contract.

    Uses ``name``, ``description`` and ``inputSchema`` (falling back to
    ``parameters``). Volatile keys such as ``_meta`` and ``annotations`` are
    excluded; the spec treats them as hints, not contract.
    """
    if not isinstance(tool, dict):
        raise ValueError("tool must be an object")
    body = {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "inputSchema": tool.get("inputSchema", tool.get("parameters", {})),
    }
    return _sha256(body)


def policy_digest(policy: dict) -> str:
    """Hash one manifest tool-policy entry exactly as the manifest signs it."""
    return _sha256(_normalise_policy(policy))


def _normalise_policy(policy: dict) -> dict:
    out = dict(policy)
    if isinstance(out.get("stores_in"), list):
        out["stores_in"] = sorted(out["stores_in"])
    if isinstance(out.get("args"), dict):
        out["args"] = dict(sorted(out["args"].items()))
    return dict(sorted(out.items()))


@dataclass
class DriftEvent:
    tool: str
    kind: str          # added | removed | schema_changed | policy_changed
    detail: str = ""

    def as_dict(self) -> dict:
        return {"tool": self.tool, "kind": self.kind, "detail": self.detail}


# ---------------------------------------------------------------------------
# Pin set construction and signing
# ---------------------------------------------------------------------------

def build_pin_set(
    observed_tools: Iterable[dict],
    manifest: Manifest,
    pinned_by: str,
    signer: Optional[Signer] = None,
    now: Optional[float] = None,
) -> dict:
    """Build a pin set from a ``tools/list`` snapshot and a signed manifest.

    Only tools present in the manifest's policy table are pinned. Tools the
    server advertises but the manifest does not permit are recorded under
    ``unlisted`` for review and are never gated.
    """
    if manifest.tools is None:
        raise ValueError("manifest has no tools policy")
    validate_tool_policy(manifest.tools)

    by_name: dict[str, dict] = {}
    for tool in observed_tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("each observed tool must be an object with a string name")
        if tool["name"] in by_name:
            raise ValueError(f"duplicate tool in catalogue: {tool['name']}")
        by_name[tool["name"]] = tool

    pinned: dict[str, dict] = {}
    unlisted: dict[str, dict] = {}

    for name, policy in sorted(manifest.tools.items()):
        if name.startswith("_"):
            continue
        observed = by_name.get(name)
        if observed is None:
            raise ValueError(f"manifest tool {name!r} absent from observed catalogue")
        pinned[name] = {
            "schema_sha256": tool_schema_digest(observed),
            "policy_sha256": policy_digest(policy),
            "class": policy.get("class", ""),
        }

    for name, tool in sorted(by_name.items()):
        if name not in pinned:
            unlisted[name] = {"schema_sha256": tool_schema_digest(tool)}

    body = {
        "format": PIN_FORMAT,
        "node": manifest.node,
        "pinned_by": str(pinned_by),
        "pinned_at": int(now if now is not None else time.time()),
        "tools": pinned,
        "unlisted": unlisted,
    }
    if signer is not None:
        body["signature"] = signer.sign(canonical(body))
    return body


def verify_pin_set(pins: dict, verifier: Signer) -> bool:
    """Verify a pin set's signature over its canonical body."""
    if not isinstance(pins, dict) or pins.get("format") != PIN_FORMAT:
        return False
    signature = pins.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    body = {k: v for k, v in pins.items() if k != "signature"}
    return verifier.verify(canonical(body), signature)


# ---------------------------------------------------------------------------
# Drift checks
# ---------------------------------------------------------------------------

def check_catalogue(pins: dict, observed_tools: Iterable[dict]) -> list[DriftEvent]:
    """Compare a fresh ``tools/list`` against a pin set.

    Reports tools that appeared, disappeared, or whose contract hash changed.
    """
    if pins.get("format") != PIN_FORMAT:
        raise ValueError("not a LOKAHI pin set")
    pinned = pins.get("tools", {})

    observed: dict[str, dict] = {}
    for tool in observed_tools:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            observed[tool["name"]] = tool

    events: list[DriftEvent] = []
    for name, pin in pinned.items():
        tool = observed.get(name)
        if tool is None:
            events.append(DriftEvent(name, "removed", "pinned tool no longer advertised"))
            continue
        digest = tool_schema_digest(tool)
        if digest != pin.get("schema_sha256"):
            events.append(
                DriftEvent(
                    name,
                    "schema_changed",
                    f"pinned {pin.get('schema_sha256', '')[:12]} observed {digest[:12]}",
                )
            )

    for name in observed:
        if name not in pinned and name not in pins.get("unlisted", {}):
            events.append(DriftEvent(name, "added", "tool appeared after pinning"))

    return events


def check_manifest(pins: dict, manifest: Manifest) -> list[DriftEvent]:
    """Compare the signed manifest's tool policies against the pin set.

    The gateway runs this on every call. A policy that changed after pinning is
    refused, because the reviewer approved the older contract.
    """
    if pins.get("format") != PIN_FORMAT:
        raise ValueError("not a LOKAHI pin set")
    pinned = pins.get("tools", {})
    current = manifest.tools or {}

    events: list[DriftEvent] = []
    for name, pin in pinned.items():
        policy = current.get(name)
        if policy is None:
            events.append(DriftEvent(name, "removed", "pinned tool absent from manifest"))
            continue
        try:
            digest = policy_digest(policy)
        except Exception:  # noqa: BLE001 - malformed policy is drift
            events.append(DriftEvent(name, "policy_changed", "policy unreadable"))
            continue
        if digest != pin.get("policy_sha256"):
            events.append(
                DriftEvent(
                    name,
                    "policy_changed",
                    f"pinned {pin.get('policy_sha256', '')[:12]} current {digest[:12]}",
                )
            )

    for name in current:
        if not name.startswith("_") and name not in pinned:
            events.append(DriftEvent(name, "added", "tool added to manifest after pinning"))

    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import os
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    from lokahi import load_key
    from lokahi_sign import _load_signer

    p = argparse.ArgumentParser(description="LOKAHI tool pinning (rug-pull defence)")
    sub = p.add_subparsers(dest="command", required=True)

    pin = sub.add_parser("pin", help="Build and sign a pin set from a tools/list snapshot")
    pin.add_argument("--tools-list", required=True, help="JSON: array of tools, or {result:{tools:[...]}}")
    pin.add_argument("--manifest", required=True, help="Signed manifest JSON")
    pin.add_argument("--key-file", required=True, help="Pin-set signing key")
    pin.add_argument("--pinned-by", required=True, help="Who approved the catalogue")
    pin.add_argument("--out", required=True, help="Where to write the pin set")

    ver = sub.add_parser("verify", help="Verify a pin set and check for catalogue drift")
    ver.add_argument("--pins", required=True)
    ver.add_argument("--tools-list", required=True)
    ver.add_argument("--key-file", required=True, help="Verification key")

    man = sub.add_parser("check-manifest", help="Check a manifest against a pin set")
    man.add_argument("--pins", required=True)
    man.add_argument("--manifest", required=True)

    a = p.parse_args(argv)

    if a.command == "pin":
        snapshot = _load_json(a.tools_list)
        tools = snapshot.get("result", {}).get("tools") if isinstance(snapshot, dict) else snapshot
        if not isinstance(tools, list):
            print("lokahi: tools list must be an array or an MCP tools/list response", file=sys.stderr)
            return 2
        manifest = Manifest.from_dict(_load_json(a.manifest))
        signer = _load_signer(a.key_file)
        pins = build_pin_set(tools, manifest, a.pinned_by, signer)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"pinned {len(pins['tools'])} tools for {manifest.node} -> {a.out}")
        return 0

    if a.command == "verify":
        pins = _load_json(a.pins)
        snapshot = _load_json(a.tools_list)
        tools = snapshot.get("result", {}).get("tools") if isinstance(snapshot, dict) else snapshot
        verifier = _load_signer(a.key_file)
        if not verify_pin_set(pins, verifier):
            print("lokahi: pin set signature does not verify", file=sys.stderr)
            return 2
        events = check_catalogue(pins, tools or [])
        for e in events:
            print(f"DRIFT {e.kind:14s} {e.tool}  {e.detail}")
        print(f"{len(events)} drift event(s)")
        return 0 if not events else 1

    pins = _load_json(a.pins)
    manifest = Manifest.from_dict(_load_json(a.manifest))
    events = check_manifest(pins, manifest)
    for e in events:
        print(f"DRIFT {e.kind:14s} {e.tool}  {e.detail}")
    print(f"{len(events)} drift event(s)")
    return 0 if not events else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
