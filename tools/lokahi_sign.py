# -*- coding: utf-8 -*-
"""Create keys and sign LOKAHI manifests (v0.1.3 reference).

    python tools/lokahi_sign.py keygen --out manifest.key
    python tools/lokahi_sign.py manifest --key-file manifest.key \
        --in manifest.unsigned.json --out manifest.json

Keys are 32 random bytes written as hex. v0.1.3 signs with an HMAC-SHA256
stand-in, so whoever verifies a signature also needs the key: keep key files
private and give the gateway only the keys it must check.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lokahi import Manifest, Signer, load_key, validate_tool_policy  # noqa: E402


def keygen(out: str, force: bool = False) -> int:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        fd = os.open(out, flags, 0o600)  # owner-only on POSIX systems
    except FileExistsError:
        print(f"lokahi: {out} already exists; use --force to replace it", file=sys.stderr)
        return 2
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(secrets.token_hex(32) + "\n")
    print(f"wrote a new key to {out}. Keep it private.")
    return 0


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
        manifest.sign(Signer(load_key(key_file)))
    except (OSError, ValueError) as exc:
        print(f"lokahi: cannot sign {src}: {exc}", file=sys.stderr)
        return 2
    signed = manifest.to_dict()
    signed["signature"] = manifest.signature
    with open(out, "w", encoding="utf-8") as f:
        json.dump(signed, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"signed manifest for {manifest.node} written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Create LOKAHI keys and sign manifests")
    sub = p.add_subparsers(dest="command", required=True)

    k = sub.add_parser("keygen", help="Write a new random key file")
    k.add_argument("--out", required=True, help="Where to write the key")
    k.add_argument("--force", action="store_true", help="Replace an existing key file")

    m = sub.add_parser("manifest", help="Validate and sign a manifest JSON file")
    m.add_argument("--key-file", required=True, help="The manifest signing key")
    m.add_argument("--in", dest="src", required=True, help="Unsigned manifest JSON")
    m.add_argument("--out", required=True, help="Where to write the signed manifest")

    a = p.parse_args(argv)
    if a.command == "keygen":
        return keygen(a.out, a.force)
    return sign_manifest(a.key_file, a.src, a.out)


if __name__ == "__main__":
    sys.exit(main())
