# -*- coding: utf-8 -*-
"""Signed episode receipts for gated VLA rollouts.

One receipt is the evidence artifact for one episode: every gated step, in
order, in the engine's own hash chain, plus the aggregate the operator is held
to — how many steps ran, how often the gate intervened, and what the latency
tail looked like.

Three properties are the whole point:

1. **The chain is the record.** Steps go through `Ledger(canon=CANON_JCS)` with
   `append_event`, the same chain `lokahi_evidence` verifies for tool
   decisions. Each entry's hash covers its own fields and links to the one
   before it, so an edited step verdict, latency or margins value is detectable
   from the receipt alone, without the signing key.
2. **The aggregate is derived, never declared.** Percentiles and verdict counts
   are recomputed from the entries by the verifier. If the summary and the
   chain disagree, the receipt is refused.
3. **It is signed.** Ed25519 when `cryptography` is installed (the public key
   travels in the receipt), HMAC-SHA256 via `lokahi.Signer` otherwise. What
   that signature proves depends on the key you hold: an embedded public key
   proves the receipt was not edited after signing, and only a pinned key
   proves *who* signed it. A verifier that wants identity must pass `pubkey`.

`lokahi-vla-verify` is the standalone verifier: exit 0 intact, 1 tampered,
2 cannot evaluate (unreadable file, missing key).
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from lokahi import (
    CANON_JCS,
    CANON_PY,
    GENESIS,
    Ledger,
    Signer,
    canonical,
    canonical_jcs,
    digest,
)

from .physical_manifest import EXECUTION_MODES
from .vla_gate import VERDICT_CLAMPED, VERDICT_COMPLIANT, VERDICT_FORBIDDEN

RECEIPT_FORMAT = "lokahi-vla-receipt/1"
VERDICTS = (VERDICT_COMPLIANT, VERDICT_CLAMPED, VERDICT_FORBIDDEN)
# Where the keep-out set a step was checked against came from. A receipt must
# not let a caller's moving obstacle pass as a static manifest shape.
KEEP_OUT_SOURCES = ("manifest", "caller")

ALGORITHM_ED25519 = "ed25519"
ALGORITHM_HMAC = "hmac-sha256"

STATUS_VERIFIED = "verified"
STATUS_TAMPERED = "tampered"
STATUS_UNVERIFIABLE = "unverifiable"


class ReceiptError(ValueError):
    """A receipt that cannot be built or read. Never a silent default."""


# ---------------------------------------------------------------------------
# percentiles
# ---------------------------------------------------------------------------

def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile, no interpolation.

    Nearest rank is the only method that always reports a value that actually
    happened, which is what a latency claim has to mean: `rank = ceil(p/100 * n)`
    over the ascending values, clamped to `[1, n]`. Returns `None` for no data
    rather than inventing a zero.
    """
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = max(1, math.ceil((p / 100.0) * len(ordered)))
    return ordered[rank - 1]


def _latency_block(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "p50": percentile(values, 50.0),
        "p95": percentile(values, 95.0),
        "p99": percentile(values, 99.0),
    }


def _canonical_bytes(canon_version: int, obj: Mapping[str, Any]) -> bytes:
    if canon_version == CANON_JCS:
        return canonical_jcs(dict(obj))
    if canon_version == CANON_PY:
        return canonical(dict(obj))
    raise ReceiptError(f"unknown canonical version: {canon_version}")


def _is_hex_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _entry_hash(entry: Mapping[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "hash"}
    return digest(int(entry.get("canon", CANON_PY)), body)


def _root_of(entries: Sequence[Mapping[str, Any]]) -> str:
    return entries[-1]["hash"] if entries else GENESIS


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------

def _algorithm_of(signer: Any) -> str:
    """Ed25519 signers expose raw public bytes; an HMAC `Signer` does not."""
    return ALGORITHM_ED25519 if callable(getattr(signer, "public_bytes", None)) else ALGORITHM_HMAC


def _default_signer() -> Any:
    """An ephemeral Ed25519 key when `cryptography` is installed, else nothing.

    Nothing is the honest answer for HMAC: the verifier has to hold the secret,
    so an invented one would make the receipt unverifiable by anyone else.
    """
    try:
        from lokahi_consent import Ed25519Signer
    except ImportError:  # pragma: no cover - depends on the install
        return None
    try:
        return Ed25519Signer.generate()
    except ImportError:  # pragma: no cover
        return None


def _ed25519_verifier(public_key_hex: str):
    from lokahi_consent import Ed25519Signer

    return Ed25519Signer.from_public_bytes(bytes.fromhex(public_key_hex))


def _signature_block(signer: Any, canon_version: int, body: Mapping[str, Any]) -> dict[str, Any]:
    algorithm = _algorithm_of(signer)
    payload = _canonical_bytes(canon_version, body)
    block: dict[str, Any] = {"algorithm": algorithm, "value": signer.sign(payload)}
    if algorithm == ALGORITHM_ED25519:
        public_key = signer.public_bytes()
        block["public_key"] = public_key.hex()
        block["key_id"] = hashlib.sha256(public_key).hexdigest()[:32]
    return block


# ---------------------------------------------------------------------------
# receipt
# ---------------------------------------------------------------------------

class RolloutReceipt:
    """Accumulates gated steps for one episode and emits JSON.

    `policy_id` and `manifest_hash` name the policy and the exact manifest the
    gate was loaded from; `execution_mode` is the manifest's declared mode, so a
    receipt that claims `sync` cannot carry steps that were decided out of band.
    """

    def __init__(
        self,
        policy_id: str,
        manifest_hash: str,
        execution_mode: str,
        *,
        episode_id: str | None = None,
        node: str = "vla",
        signer: Any | None = None,
        canon: int = CANON_JCS,
        created_ts_ns: int | None = None,
    ) -> None:
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ReceiptError("policy_id must be a non-empty string")
        if not _is_hex_digest(manifest_hash):
            raise ReceiptError("manifest_hash must be a 64-character hex digest")
        if execution_mode not in EXECUTION_MODES:
            raise ReceiptError(f"execution_mode must be one of {EXECUTION_MODES}")
        if canon not in (CANON_PY, CANON_JCS):
            raise ReceiptError(f"unknown canonical version: {canon}")

        self.policy_id = policy_id
        self.manifest_hash = manifest_hash.lower()
        self.execution_mode = execution_mode
        self.episode_id = episode_id or uuid.uuid4().hex
        self.node = node
        self.canon = canon
        self.created_ts_ns = int(created_ts_ns if created_ts_ns is not None else time.time_ns())
        self._signer = signer
        self._ledger = Ledger(canon=canon)
        self._obs_to_action_ms: list[float] = []
        self._gate_eval_us: list[float] = []
        self._verdict_counts = {verdict: 0 for verdict in VERDICTS}
        self._interventions = 0

    # -- accumulation -------------------------------------------------------

    @property
    def entries(self) -> list[dict]:
        return self._ledger.entries

    @property
    def step_count(self) -> int:
        return len(self._ledger.entries)

    @property
    def intervention_count(self) -> int:
        return self._interventions

    @property
    def ledger_root(self) -> str:
        return _root_of(self._ledger.entries)

    def record_step(
        self,
        decision: Any,
        obs_ts_ns: int,
        now_ts_ns: int,
        *,
        source: str = "vla",
        context: Mapping[str, Any] | None = None,
    ) -> dict:
        """Append one gated step to the chain and fold it into the aggregate.

        `obs_to_action_ms` is the observation-to-decision latency of this step,
        `(now_ts_ns - obs_ts_ns) / 1e6`. A negative value is recorded rather
        than rejected: an observation from the future is evidence, not an error.
        """
        verdict = getattr(decision, "verdict", None)
        if not isinstance(verdict, str) or not verdict:
            raise ReceiptError("decision must carry a verdict string")

        obs_to_action_ms = (int(now_ts_ns) - int(obs_ts_ns)) / 1e6
        gate_eval_us = float(getattr(decision, "gate_eval_us", 0.0) or 0.0)
        safe_action = getattr(decision, "safe_action", None)
        keep_out_source = getattr(decision, "keep_out_source", "manifest")
        if keep_out_source not in KEEP_OUT_SOURCES:
            raise ReceiptError(f"decision names an unknown keep_out_source: {keep_out_source!r}")
        dynamic_keep_outs = getattr(decision, "dynamic_keep_out_count", 0)
        if isinstance(dynamic_keep_outs, bool) or not isinstance(dynamic_keep_outs, int) or dynamic_keep_outs < 0:
            raise ReceiptError(f"decision declares an invalid dynamic keep-out count: {dynamic_keep_outs!r}")
        step = {
            "step": len(self._ledger.entries),
            "record": "vla_step",
            "verdict": verdict,
            "state": getattr(decision, "state", None),
            "allowed": bool(getattr(decision, "allowed", False)),
            "interlock": getattr(decision, "interlock", None),
            "clamped_from_index": getattr(decision, "clamped_from_index", None),
            "safe_action_mode": safe_action.get("mode") if isinstance(safe_action, Mapping) else None,
            "source": source,
            "obs_ts_ns": int(obs_ts_ns),
            "now_ts_ns": int(now_ts_ns),
            "obs_to_action_ms": obs_to_action_ms,
            "gate_eval_us": gate_eval_us,
            # Provenance of the obstacle set this step was gated against: a
            # caller that moves a hazard must not read as a static manifest.
            "keep_out_source": keep_out_source,
            "dynamic_keep_outs": dynamic_keep_outs,
            "margins": dict(getattr(decision, "margins", None) or {}),
        }
        if context:
            step["context"] = dict(context)
        try:
            entry = self._ledger.append_event(
                verdict=verdict,
                reasons=list(getattr(decision, "violated_invariants", None) or []),
                node=self.node,
                extra=step,
                ts=int(now_ts_ns) / 1e9,
            )
        except (TypeError, ValueError) as exc:
            raise ReceiptError(f"step {step['step']} is not canonically serializable: {exc}") from exc

        self._obs_to_action_ms.append(obs_to_action_ms)
        self._gate_eval_us.append(gate_eval_us)
        self._verdict_counts[verdict] = self._verdict_counts.get(verdict, 0) + 1
        if verdict != VERDICT_COMPLIANT:
            self._interventions += 1
        return entry

    # -- emission -----------------------------------------------------------

    def body(self) -> dict:
        """Everything the signature covers, and nothing that depends on it."""
        return {
            "format": RECEIPT_FORMAT,
            "policy_id": self.policy_id,
            "manifest_hash": self.manifest_hash,
            "execution_mode": self.execution_mode,
            "episode_id": self.episode_id,
            "node": self.node,
            "step_count": self.step_count,
            "intervention_count": self._interventions,
            "verdict_counts": dict(sorted(self._verdict_counts.items())),
            "latency_percentiles": {
                "obs_to_action_ms": _latency_block(self._obs_to_action_ms),
                "gate_eval_us": _latency_block(self._gate_eval_us),
            },
            "ledger_root": self.ledger_root,
            "canon": self.canon,
            "created_ts_ns": self.created_ts_ns,
            "entries": [dict(entry) for entry in self._ledger.entries],
        }

    def to_dict(self) -> dict:
        signer = self._signer
        if signer is None:
            signer = _default_signer()
            if signer is None:
                raise ReceiptError(
                    "no signer available: install 'cryptography' for Ed25519 "
                    "or pass signer=Signer(key)"
                )
            self._signer = signer
        body = self.body()
        body["signature"] = _signature_block(signer, self.canon, body)
        return body

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"

    def write(self, path: str, *, indent: int = 2) -> str:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.to_json(indent=indent))
        return path


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

@dataclass
class ReceiptVerification:
    ok: bool = True
    status: str = STATUS_VERIFIED
    checks: list[dict] = field(default_factory=list)
    reason: str | None = None

    def add(self, name: str, passed: bool, detail: str = "", *, fatal: bool = False) -> None:
        self.checks.append({"check": name, "passed": bool(passed), "detail": detail})
        if passed:
            return
        self.ok = False
        self.status = STATUS_UNVERIFIABLE if fatal else STATUS_TAMPERED
        if self.reason is None:
            self.reason = f"{name}: {detail}" if detail else name

    def failed(self) -> list[str]:
        return [check["check"] for check in self.checks if not check["passed"]]

    def render(self) -> str:
        lines = []
        for check in self.checks:
            mark = "PASS" if check["passed"] else "FAIL"
            line = f"[{mark}] {check['check']}"
            if check["detail"]:
                line += f" - {check['detail']}"
            lines.append(line)
        lines.append("")
        if self.ok:
            lines.append("RECEIPT VERIFIED")
        elif self.status == STATUS_UNVERIFIABLE:
            lines.append(f"RECEIPT UNVERIFIABLE: {self.reason}")
        else:
            lines.append(f"RECEIPT FAILED VERIFICATION: {self.reason}")
        return "\n".join(lines)


def _keep_out_provenance(entries: Sequence[Mapping[str, Any]]) -> tuple[bool, str]:
    """Every step must name where its obstacle set came from, consistently.

    The chain already makes an edited value detectable; this check is about the
    field's own meaning, so a receipt that was rebuilt and re-signed from a
    tampered entry still fails on a manifest source carrying dynamic shapes.
    """
    for index, entry in enumerate(entries):
        extra = entry.get("extra")
        if not isinstance(extra, Mapping):
            return False, f"entry {index} carries no step fields"
        source = extra.get("keep_out_source")
        if source not in KEEP_OUT_SOURCES:
            return False, f"entry {index} names keep-out source {source!r}"
        count = extra.get("dynamic_keep_outs")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return False, f"entry {index} dynamic_keep_outs is {count!r}"
        if source == "manifest" and count != 0:
            return False, f"entry {index} claims the manifest while carrying {count} dynamic keep-outs"
    return True, f"{len(entries)} entries"


def _expected_summary(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    obs_to_action = [entry.get("extra", {}).get("obs_to_action_ms", 0.0) for entry in entries]
    gate_eval = [entry.get("extra", {}).get("gate_eval_us", 0.0) for entry in entries]
    counts = {verdict: 0 for verdict in VERDICTS}
    for entry in entries:
        verdict = entry.get("verdict")
        counts[verdict] = counts.get(verdict, 0) + 1
    return {
        "step_count": len(entries),
        "intervention_count": sum(count for verdict, count in counts.items() if verdict != VERDICT_COMPLIANT),
        "verdict_counts": dict(sorted(counts.items())),
        "latency_percentiles": {
            "obs_to_action_ms": _latency_block(obs_to_action),
            "gate_eval_us": _latency_block(gate_eval),
        },
        "ledger_root": _root_of(entries),
    }


def verify_receipt(
    receipt: Mapping[str, Any],
    *,
    key: str | bytes | None = None,
    pubkey: str | None = None,
) -> ReceiptVerification:
    """Verify a receipt offline, recomputing everything derived from the chain.

    `pubkey` pins the Ed25519 key that must have signed this receipt; `key` is
    the HMAC-SHA256 secret for a receipt signed with `lokahi.Signer`. With no
    key at all an Ed25519 receipt still verifies against the public key it
    carries — that proves integrity, not identity, and says so in the checks.
    An HMAC receipt with no key is unverifiable, never "probably fine".
    """
    report = ReceiptVerification()

    if not isinstance(receipt, Mapping):
        report.add("format", False, "receipt is not an object", fatal=True)
        return report
    if receipt.get("format") != RECEIPT_FORMAT:
        report.add("format", False, f"expected {RECEIPT_FORMAT}", fatal=True)
        return report
    report.add("format", True, RECEIPT_FORMAT)

    canon = receipt.get("canon", CANON_PY)
    if canon not in (CANON_PY, CANON_JCS):
        report.add("canon", False, f"unknown canonical version {canon!r}", fatal=True)
        return report
    report.add("canon", True, str(canon))

    required = (
        "policy_id",
        "manifest_hash",
        "execution_mode",
        "episode_id",
        "step_count",
        "intervention_count",
        "verdict_counts",
        "latency_percentiles",
        "ledger_root",
        "created_ts_ns",
        "entries",
        "signature",
    )
    missing = [name for name in required if name not in receipt]
    if missing:
        report.add("structure", False, f"missing fields: {missing}", fatal=True)
        return report
    report.add("structure", True, f"{len(required)} fields present")

    if not _is_hex_digest(receipt.get("manifest_hash")):
        report.add("manifest_hash", False, "manifest_hash is not a 64-character hex digest")
    else:
        report.add("manifest_hash", True, receipt["manifest_hash"])

    entries = receipt.get("entries")
    if not isinstance(entries, list) or any(not isinstance(entry, Mapping) for entry in entries):
        report.add("ledger_chain", False, "entries is not a list of objects", fatal=True)
        return report

    previous = GENESIS
    chain_ok = True
    chain_detail = f"{len(entries)} entries"
    for index, entry in enumerate(entries):
        try:
            if int(entry.get("seq", -1)) != index:
                chain_ok, chain_detail = False, f"entry {index} has seq {entry.get('seq')!r}"
                break
            if int(entry.get("canon", CANON_PY)) != int(canon):
                chain_ok, chain_detail = False, f"entry {index} declares canon {entry.get('canon')!r}"
                break
            if entry.get("prev") != previous:
                chain_ok, chain_detail = False, f"entry {index} does not link to its predecessor"
                break
            recomputed = _entry_hash(entry)
            if recomputed != entry.get("hash"):
                chain_ok = False
                chain_detail = f"entry {index} hash does not recompute"
                break
            previous = entry["hash"]
        except (TypeError, ValueError) as exc:
            chain_ok, chain_detail = False, f"entry {index} is not verifiable: {exc}"
            break
    report.add("ledger_chain", chain_ok, chain_detail)

    provenance_ok, provenance_detail = _keep_out_provenance(entries)
    report.add("keep_out_provenance", provenance_ok, provenance_detail)

    expected = _expected_summary(entries)
    report.add(
        "ledger_root",
        receipt.get("ledger_root") == expected["ledger_root"],
        f"stored {str(receipt.get('ledger_root'))[:12]} recomputed {expected['ledger_root'][:12]}",
    )
    report.add(
        "step_count",
        receipt.get("step_count") == expected["step_count"],
        f"stored {receipt.get('step_count')!r} recomputed {expected['step_count']}",
    )
    report.add(
        "intervention_count",
        receipt.get("intervention_count") == expected["intervention_count"],
        f"stored {receipt.get('intervention_count')!r} recomputed {expected['intervention_count']}",
    )
    report.add(
        "verdict_counts",
        receipt.get("verdict_counts") == expected["verdict_counts"],
        f"stored {receipt.get('verdict_counts')!r} recomputed {expected['verdict_counts']}",
    )
    stored_latency = receipt.get("latency_percentiles")
    latency_ok = stored_latency == expected["latency_percentiles"]
    report.add(
        "latency_percentiles",
        latency_ok,
        "recomputed from per-step latencies"
        if latency_ok
        else f"stored {stored_latency!r} recomputed {expected['latency_percentiles']!r}",
    )

    signature = receipt.get("signature")
    if not isinstance(signature, Mapping):
        report.add("signature", False, "signature block missing", fatal=True)
        return report
    body = {name: value for name, value in receipt.items() if name != "signature"}
    algorithm = signature.get("algorithm")
    value = signature.get("value")
    try:
        payload = _canonical_bytes(int(canon), body)
    except (ReceiptError, TypeError) as exc:
        report.add("signature", False, f"receipt is not canonically serializable: {exc}")
        return report

    if algorithm == ALGORITHM_ED25519:
        embedded = signature.get("public_key")
        if not isinstance(embedded, str) or not _is_hex_digest(embedded):
            report.add("signature", False, "ed25519 signature carries no public key", fatal=True)
            return report
        expected_key = (pubkey or embedded).strip().lower()
        if expected_key != embedded.lower():
            report.add(
                "signature",
                False,
                f"signed by {embedded[:16]} but {expected_key[:16]} was expected",
            )
            return report
        try:
            verifier = _ed25519_verifier(embedded)
        except Exception as exc:  # noqa: BLE001 - cryptography missing or bad key bytes
            report.add("signature", False, f"cannot load ed25519 key: {exc}", fatal=True)
            return report
        report.add(
            "signature",
            bool(verifier.verify(payload, value)),
            "ed25519 signature matches the body"
            if pubkey
            else "ed25519 signature matches the body (pinned: no)",
        )
        return report

    if algorithm == ALGORITHM_HMAC:
        if key is None:
            report.add("signature", False, "hmac receipt needs the shared key", fatal=True)
            return report
        secret = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        try:
            verifier = Signer(secret)
        except ValueError as exc:
            report.add("signature", False, f"unusable hmac key: {exc}", fatal=True)
            return report
        report.add("signature", bool(verifier.verify(payload, value)), "hmac-sha256 signature matches the body")
        return report

    report.add("signature", False, f"unknown signature algorithm {algorithm!r}", fatal=True)
    return report


def load_receipt(path: str) -> dict:
    """Parse a receipt file. A parse failure is fatal, never a skip."""
    with open(path, "r", encoding="utf-8") as handle:
        try:
            document = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ReceiptError(f"receipt is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ReceiptError("receipt must be a JSON object")
    return document


def _resolve_key_text(args) -> tuple[bytes | None, str | None]:
    if args.pubkey and args.key:
        raise ReceiptError("use --pubkey or --key, not both")
    if args.pubkey:
        return None, args.pubkey.strip()
    if args.key:
        return args.key.encode("utf-8"), None
    if args.key_file:
        with open(args.key_file, "rb") as handle:
            material = handle.read().strip()
        text = material.decode("utf-8", errors="strict").strip()
        if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text):
            return None, text.lower()
        return material, None
    return None, None


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="lokahi-vla-verify",
        description="Verify a signed LOKAHI VLA rollout receipt offline.",
    )
    parser.add_argument("receipt", help="path to the receipt JSON")
    parser.add_argument("--pubkey", help="pinned Ed25519 public key, 64 hex characters")
    parser.add_argument("--key", help="HMAC-SHA256 secret text for an hmac-sha256 receipt")
    parser.add_argument("--key-file", help="file holding the HMAC secret, or a 64-hex Ed25519 public key")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)

    try:
        document = load_receipt(args.receipt)
        key, pubkey = _resolve_key_text(args)
    except (OSError, ReceiptError, UnicodeDecodeError) as exc:
        print(f"RECEIPT UNVERIFIABLE: {exc}", file=sys.stderr)
        return 2

    report = verify_receipt(document, key=key, pubkey=pubkey)
    if args.json:
        print(json.dumps(
            {"ok": report.ok, "status": report.status, "reason": report.reason, "checks": report.checks},
            indent=2,
            sort_keys=True,
        ))
    else:
        print(report.render())
    if report.ok:
        return 0
    label = "UNVERIFIABLE" if report.status == STATUS_UNVERIFIABLE else "FAILED VERIFICATION"
    print(f"RECEIPT {label}: {report.reason}", file=sys.stderr)
    return 2 if report.status == STATUS_UNVERIFIABLE else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
