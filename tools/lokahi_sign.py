# -*- coding: utf-8 -*-
"""Create keys and sign LOKAHI manifests (v1.0).

    # HMAC-SHA256 (v0.1.4, backward compatible)
    python tools/lokahi_sign.py keygen --out manifest.key
    python tools/lokahi_sign.py manifest --key-file manifest.key \
        --in manifest.unsigned.json --out manifest.json

    # Ed25519 public-key signatures (v1.0, recommended)
    python tools/lokahi_sign.py keygen --ed25519 --out manifest.key
    python tools/lokahi_sign.py manifest --key-file manifest.key \
        --in manifest.unsigned.json --out manifest.json

    # Threshold validator keys (m-of-n consent)
    python tools/lokahi_sign.py keygen --threshold 2 --n 3 \
        --out-dir validators/
    # Writes validators/0.key, validators/1.key, validators/2.key
    # and validators/approvers.json (public keys for the gate)

Keys are 32 random bytes written as hex. v0.1.4 signs with an HMAC-SHA256
stand-in, so whoever verifies a signature also needs the key: keep key files
private and give the gateway only the keys it must check.

Ed25519 keys use the `cryptography` package. The gate holds only public keys,
so no single party can forge validator approvals.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ != "lokahi" and HERE not in sys.path:  # source checkout only
    sys.path.insert(0, HERE)

from lokahi import Manifest, Signer, load_key, validate_tool_policy  # noqa: E402

# Ed25519 (soft dependency via `cryptography`)
try:  # noqa: E402
    from lokahi_consent import Ed25519Signer  # noqa: E402

    _ED25519_AVAILABLE = True
except ImportError:  # pragma: no cover
    Ed25519Signer = None  # type: ignore
    _ED25519_AVAILABLE = False


def keygen(
    out: str,
    force: bool = False,
    ed25519: bool = False,
    threshold: Optional[int] = None,
    n: Optional[int] = None,
    out_dir: Optional[str] = None,
    group: Optional[str] = None,
) -> int:
    """Generate key file(s).

    --ed25519: single Ed25519 manifest signing key.
    --threshold m --n n --out-dir dir: n validator Ed25519 key pairs + approvers.json.
    --group name: include a matching consent group in approvers.json.
    """
    if ed25519:
        if not _ED25519_AVAILABLE:
            print(
                "lokahi: Ed25519 requires the 'cryptography' package; "
                "install it or use HMAC-SHA256 (default)",
                file=sys.stderr,
            )
            return 2
        if not out:
            print("lokahi: --out is required with --ed25519", file=sys.stderr)
            return 2
        signer = Ed25519Signer.generate()
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
        try:
            fd = os.open(out, flags, 0o600)
        except FileExistsError:
            print(f"lokahi: {out} already exists; use --force to replace it", file=sys.stderr)
            return 2
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write("ed25519:" + signer.private_bytes().hex() + "\n")
        # Also write the public key alongside.
        pub_path = out + ".pub"
        try:
            fd = os.open(pub_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        except OSError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(signer.public_bytes().hex() + "\n")
        print(f"wrote Ed25519 manifest signing key to {out}")
        print(f"wrote Ed25519 public key to {pub_path}")
        print("Keep the private key private; distribute the public key to verifiers.")
        return 0

    if threshold is not None or n is not None:
        if threshold is None or n is None:
            print("lokahi: --threshold and --n must both be provided", file=sys.stderr)
            return 2
        if threshold < 1 or n < threshold:
            print("lokahi: require 1 <= threshold <= n", file=sys.stderr)
            return 2
        if group is not None and not group.strip():
            print("lokahi: --group cannot be empty", file=sys.stderr)
            return 2
        if not _ED25519_AVAILABLE:
            print(
                "lokahi: threshold keys require the 'cryptography' package",
                file=sys.stderr,
            )
            return 2
        if not out_dir:
            print("lokahi: --out-dir is required with --threshold/--n", file=sys.stderr)
            return 2
        if group and not group.strip():
            print("lokahi: --group cannot be empty", file=sys.stderr)
            return 2
        os.makedirs(out_dir, exist_ok=True)

        validators = {}
        for i in range(n):
            signer = Ed25519Signer.generate()
            key_path = os.path.join(out_dir, f"{i}.key")
            pub_path = os.path.join(out_dir, f"{i}.key.pub")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            with os.fdopen(os.open(key_path, flags, 0o600), "w", encoding="ascii") as f:
                f.write(signer.private_bytes().hex() + "\n")
            with open(pub_path, "w", encoding="ascii") as f:
                f.write(signer.public_bytes().hex() + "\n")
            # approvers.json maps operator id -> public key hex
            validators[f"validator:{i}"] = signer.public_bytes().hex()

        approvers_path = os.path.join(out_dir, "approvers.json")
        with open(approvers_path, "w", encoding="utf-8") as f:
            config = {
                "threshold": {"m": threshold, "n": n},
                "validators": validators,
            }
            if group:
                config["groups"] = {group: list(validators.keys())}
            json.dump(
                config,
                f,
                indent=2,
                sort_keys=True,
            )
            f.write("\n")
        print(f"wrote {n} Ed25519 validator key pairs to {out_dir}/")
        print(f"wrote threshold config (m={threshold}, n={n}) to {approvers_path}")
        print("Distribute each {i}.key to validator {i}; keep approvers.json for the gate.")
        return 0

    # Default: HMAC-SHA256 (v0.1.4)
    if not out:
        print("lokahi: --out is required", file=sys.stderr)
        return 2
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        fd = os.open(out, flags, 0o600)
    except FileExistsError:
        print(f"lokahi: {out} already exists; use --force to replace it", file=sys.stderr)
        return 2
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(secrets.token_hex(32) + "\n")
    print(f"wrote a new HMAC-SHA256 key to {out}. Keep it private.")
    return 0


def _load_signer(key_file: str) -> Signer:
    """Load a Signer from a key file, auto-detecting HMAC vs Ed25519.

    Ed25519 key files start with the ``ed25519:`` prefix followed by the
    32-byte private key seed as 64 hex chars. HMAC-SHA256 keys are raw hex
    with no prefix. Falls back to HMAC-SHA256 if Ed25519 is unavailable.
    """
    key_bytes = load_key(key_file)
    text = key_bytes.decode("ascii", errors="replace").strip()
    if _ED25519_AVAILABLE and text.startswith("ed25519:"):
        hex_part = text[len("ed25519:"):]
        try:
            seed = bytes.fromhex(hex_part)
            if len(seed) == 32:
                return Ed25519Signer.from_private_bytes(seed)
        except ValueError:
            pass
    return Signer(key_bytes)


def sign_manifest(key_file: str, src: str, out: str) -> int:
    try:
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("manifest must be a JSON object")
        data.pop("signature", None)
        manifest = Manifest.from_dict(data)
        if manifest.tools is not None:
            validate_tool_policy(manifest.tools)
        signer = _load_signer(key_file)
        manifest.sign(signer)
    except (OSError, ValueError) as exc:
        print(f"lokahi: cannot sign {src}: {exc}", file=sys.stderr)
        return 2
    signed = manifest.to_dict()
    signed["signature"] = manifest.signature
    # Record the key type used for transparency.
    if _ED25519_AVAILABLE and isinstance(signer, Ed25519Signer):
        signed["signature_algorithm"] = "ed25519"
    else:
        signed["signature_algorithm"] = "hmac-sha256"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(signed, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"signed manifest for {manifest.node} written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Create LOKAHI keys and sign manifests")
    sub = p.add_subparsers(dest="command", required=True)

    k = sub.add_parser("keygen", help="Write a new key file")
    k.add_argument("--out", default=None, help="Where to write the key (required for single-key mode)")
    k.add_argument("--force", action="store_true", help="Replace an existing key file")
    k.add_argument(
        "--ed25519",
        action="store_true",
        help="Generate an Ed25519 key instead of HMAC-SHA256",
    )
    k.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="Threshold m for m-of-n validator key generation",
    )
    k.add_argument(
        "--n",
        type=int,
        default=None,
        help="Total number n of validators for threshold key generation",
    )
    k.add_argument(
        "--out-dir",
        default=None,
        help="Output directory for threshold validator keys (required with --threshold)",
    )
    k.add_argument(
        "--group",
        default=None,
        help="Optional named consent group to record in approvers.json",
    )

    m = sub.add_parser("manifest", help="Validate and sign a manifest JSON file")
    m.add_argument("--key-file", required=True, help="The manifest signing key")
    m.add_argument("--in", dest="src", required=True, help="Unsigned manifest JSON")
    m.add_argument("--out", required=True, help="Where to write the signed manifest")

    a = p.parse_args(argv)
    if a.command == "keygen":
        return keygen(
            a.out,
            a.force,
            ed25519=a.ed25519,
            threshold=a.threshold,
            n=a.n,
            out_dir=a.out_dir,
            group=a.group,
        )
    return sign_manifest(a.key_file, a.src, a.out)


if __name__ == "__main__":
    sys.exit(main())
