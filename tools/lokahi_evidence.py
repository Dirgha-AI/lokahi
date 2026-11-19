# -*- coding: utf-8 -*-
"""Auditor evidence packs.

An evidence pack is a self-contained JSON bundle for one decision. It is
designed to be verified by someone who does not run LOKAHI: an auditor, a
regulator, or an opposing party.

What a pack proves, offline:

1. **The decision record is intact.** The entry's hash recomputes from its own
   fields, and it links to the previous entry's hash.
2. **The manifest is the one that was signed.** The manifest's canonical bytes
   verify against the manifest key the auditor was given out of band.
3. **The approvals are real.** Each approval signature verifies against that
   validator's public key, and every approval names the same action digest.
4. **The pack itself was not edited.** If the pack was signed, its body
   verifies against the pack signer's key.

What it does not prove (stated plainly, because an auditor will ask):

- that the gateway behaved correctly in software (that is the test suite and,
  later, the conformance suite);
- that the human behind a key is a distinct person (disjointness attestation is
  roadmap item R3);
- that a tool server did not lie about its schema (tool pinning covers drift,
  not deception at call time).

Standard library for assembly; `cryptography` only if Ed25519 keys are used.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from lokahi import Approval, Manifest, Signer, canonical, compute_action_digest

EVIDENCE_FORMAT = "lokahi-evidence/1"
LEDGER_ALGORITHM = "sha256(canonical-json(entry without hash))"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


def find_entry(entries: Iterable[dict], seq: int) -> dict:
    for e in entries:
        if int(e.get("seq", -1)) == int(seq):
            return e
    raise KeyError(f"no ledger entry with seq {seq}")


def build_evidence(
    entries: list[dict],
    seq: int,
    manifest: Manifest,
    *,
    approvals: Optional[Iterable[Approval]] = None,
    action_digest: Optional[str] = None,
    pack_signer: Optional[Signer] = None,
    created_at: Optional[float] = None,
) -> dict:
    """Assemble an evidence bundle for one decision.

    `entries` is the full ledger (in order). `approvals` are the signed records
    that were counted for this call, if any; they are included so an auditor can
    verify the signers independently.
    """
    entry = find_entry(entries, seq)
    prev_entry = None
    if int(seq) > 0:
        prev_entry = find_entry(entries, int(seq) - 1)

    manifest_dict = manifest.to_dict()
    if manifest.signature:
        manifest_dict["signature"] = manifest.signature

    digest = action_digest or entry.get("action_digest")
    bundle: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "created_at": int(created_at if created_at is not None else time.time()),
        "node": entry.get("node", manifest.node),
        "decision": entry,
        "chain": {
            "seq": int(seq),
            "prev_hash": entry.get("prev"),
            "prev_entry": prev_entry,
            "algorithm": LEDGER_ALGORITHM,
        },
        "manifest": manifest_dict,
        "action_digest": digest,
        "approvals": [
            a.to_dict() if isinstance(a, Approval) else dict(a) for a in (approvals or [])
        ],
        "claims": {
            "proves": [
                "decision record integrity",
                "decision links to the previous record",
                "manifest signature",
                "approval signatures bind to the action digest",
            ],
            "does_not_prove": [
                "software correctness (see the test suite and conformance suite)",
                "that signing keys belong to distinct people (R3 attestation)",
                "that a tool server described its schema truthfully at call time",
            ],
        },
    }

    if pack_signer is not None:
        bundle["pack_signature"] = pack_signer.sign(canonical(bundle))
    return bundle


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class EvidenceReport:
    ok: bool = True
    checks: list[dict] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "passed": bool(passed), "detail": detail})
        if not passed:
            self.ok = False

    def render(self) -> str:
        lines = []
        for c in self.checks:
            mark = "PASS" if c["passed"] else "FAIL"
            line = f"[{mark}] {c['check']}"
            if c["detail"]:
                line += f" — {c['detail']}"
            lines.append(line)
        lines.append("")
        lines.append("EVIDENCE VERIFIED" if self.ok else "EVIDENCE FAILED VERIFICATION")
        return "\n".join(lines)


def _load_manifest_verifier(text: str):
    """Build a manifest verifier from key text.

    Accepted forms:
      ed25519pub:<64 hex>  public key (preferred for auditors)
      ed25519:<64 hex>     private seed, derives the public key
      anything else        HMAC-SHA256 secret
    """
    text = text.strip()
    if text.startswith("ed25519pub:"):
        try:
            from lokahi_consent import Ed25519Verifier
        except ImportError as exc:  # pragma: no cover
            raise ValueError("Ed25519 verification needs the 'cryptography' package") from exc
        return Ed25519Verifier.from_public_bytes(bytes.fromhex(text[len("ed25519pub:"):]))
    if text.startswith("ed25519:"):
        try:
            from lokahi_consent import Ed25519Signer
        except ImportError as exc:  # pragma: no cover
            raise ValueError("Ed25519 verification needs the 'cryptography' package") from exc
        return Ed25519Signer.from_private_bytes(bytes.fromhex(text[len("ed25519:"):]))
    return Signer(text.encode("utf-8"))


def verify_evidence(
    bundle: dict,
    *,
    manifest_key: Optional[str] = None,
    validators: Optional[dict[str, str]] = None,
    pack_key: Optional[str] = None,
) -> EvidenceReport:
    """Verify an evidence bundle offline.

    Parameters
    ----------
    bundle: the pack as loaded from JSON.
    manifest_key: key text for the manifest signature (see `_load_manifest_verifier`).
    validators: optional map of operator id -> Ed25519 public key hex. When
        supplied, every included approval is checked.
    pack_key: optional key text for the pack signature.
    """
    report = EvidenceReport()

    if not isinstance(bundle, dict) or bundle.get("format") != EVIDENCE_FORMAT:
        report.add("format", False, f"expected {EVIDENCE_FORMAT}")
        return report
    report.add("format", True)

    entry = bundle.get("decision") or {}
    chain = bundle.get("chain") or {}

    # 1. Entry integrity.
    try:
        recomputed = _entry_hash(entry)
        report.add(
            "decision hash",
            recomputed == entry.get("hash"),
            f"stored {str(entry.get('hash'))[:12]} recomputed {recomputed[:12]}",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("decision hash", False, str(exc))
        return report

    # 2. Chain link.
    prev_entry = chain.get("prev_entry")
    if prev_entry is not None:
        try:
            prev_ok = _entry_hash(prev_entry) == prev_entry.get("hash")
            linked = entry.get("prev") == prev_entry.get("hash")
            report.add(
                "chain link",
                bool(prev_ok and linked),
                "previous entry intact and referenced by this entry"
                if prev_ok and linked
                else "previous entry does not match the recorded prev hash",
            )
        except Exception as exc:  # noqa: BLE001
            report.add("chain link", False, str(exc))
    else:
        report.add(
            "chain link",
            True,
            "genesis entry (no previous record in pack)",
        )

    # 3. Manifest signature.
    if manifest_key is None:
        report.add("manifest signature", False, "no manifest key supplied")
    else:
        try:
            verifier = _load_manifest_verifier(manifest_key)
            m = dict(bundle.get("manifest") or {})
            signature = m.pop("signature", "")
            m.pop("signature_algorithm", None)
            if not signature:
                report.add("manifest signature", False, "manifest carries no signature")
            else:
                report.add(
                    "manifest signature",
                    bool(verifier.verify(canonical(m), signature)),
                )
        except Exception as exc:  # noqa: BLE001
            report.add("manifest signature", False, str(exc))

    # 4. Approvals bind to the action digest.
    approvals = bundle.get("approvals") or []
    digest = bundle.get("action_digest")
    if approvals:
        if not digest:
            report.add("approval binding", False, "approvals present but no action digest")
        else:
            mismatched = [
                a.get("operator", "?") for a in approvals if a.get("action_digest") != digest
            ]
            report.add(
                "approval binding",
                not mismatched,
                f"{len(approvals)} approval(s) name this digest"
                if not mismatched
                else f"approvals name a different digest: {mismatched}",
            )

        if validators:
            bad = []
            for a in approvals:
                operator = a.get("operator", "")
                pub = validators.get(operator)
                if not pub:
                    bad.append(f"{operator}: no key")
                    continue
                try:
                    from lokahi_consent import Ed25519Verifier

                    verifier = Ed25519Verifier.from_public_bytes(bytes.fromhex(pub))
                    body = {k: a.get(k, "") for k in Approval.FIELDS}
                    if not verifier.verify(canonical(body), a.get("signature", "")):
                        bad.append(f"{operator}: bad signature")
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"{operator}: {type(exc).__name__}")
            report.add(
                "approval signatures",
                not bad,
                f"{len(approvals)} signature(s) verified" if not bad else "; ".join(bad),
            )
    else:
        report.add("approval binding", True, "no approvals in pack (fast-path decision)")

    # 5. Pack signature.
    if pack_key is not None:
        try:
            signature = bundle.get("pack_signature", "")
            body = {k: v for k, v in bundle.items() if k != "pack_signature"}
            verifier = _load_manifest_verifier(pack_key)
            report.add("pack signature", bool(verifier.verify(canonical(body), signature)))
        except Exception as exc:  # noqa: BLE001
            report.add("pack signature", False, str(exc))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_jsonl(path: str) -> list[dict]:
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import os
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    from lokahi_sign import _load_signer

    p = argparse.ArgumentParser(description="LOKAHI auditor evidence packs")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Build a pack for one ledger entry")
    b.add_argument("--ledger", required=True, help="Ledger JSONL file")
    b.add_argument("--seq", required=True, type=int)
    b.add_argument("--manifest", required=True, help="Signed manifest JSON")
    b.add_argument("--out", required=True)
    b.add_argument("--approvals", help="Approvals JSONL file (optional)")
    b.add_argument("--sign-key", help="Key to sign the pack itself (optional)")

    v = sub.add_parser("verify", help="Verify a pack offline")
    v.add_argument("pack")
    v.add_argument("--manifest-key", help="Key text or file for the manifest signature")
    v.add_argument("--manifest-key-file", help="File containing the manifest key text")
    v.add_argument("--validators", help="JSON: {operator: ed25519 public key hex}")
    v.add_argument("--pack-key", help="Key text or file for the pack signature")
    v.add_argument("--pack-key-file")

    a = p.parse_args(argv)

    def _key_value(inline: Optional[str], path: Optional[str]) -> Optional[str]:
        if path:
            with open(path, encoding="ascii") as f:
                return f.read().strip()
        return inline

    if a.command == "build":
        entries = _read_jsonl(a.ledger)
        with open(a.manifest, encoding="utf-8") as f:
            manifest = Manifest.from_dict(json.load(f))
        approvals = []
        if a.approvals and os.path.exists(a.approvals):
            approvals = [Approval.from_dict(d) for d in _read_jsonl(a.approvals)]
        pack_signer = _load_signer(a.sign_key) if a.sign_key else None
        bundle = build_evidence(
            entries, a.seq, manifest, approvals=approvals, pack_signer=pack_signer
        )
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote evidence pack for seq {a.seq} -> {a.out}")
        return 0

    with open(a.pack, encoding="utf-8") as f:
        bundle = json.load(f)
    validators = None
    if a.validators:
        with open(a.validators, encoding="utf-8") as f:
            validators = json.load(f)
    report = verify_evidence(
        bundle,
        manifest_key=_key_value(a.manifest_key, a.manifest_key_file),
        validators=validators,
        pack_key=_key_value(a.pack_key, a.pack_key_file),
    )
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
