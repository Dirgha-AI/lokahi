"""Canonical form, digest versioning and evidence verification across versions.

v1.3.1 adds RFC 8785 number serialization. The reason is narrow and specific:
a physical manifest is made of floats (newtons, newton-metres, rad/s, ms), and
under the old serializer `1e-07` and `-0.0` hashed to bytes that a Rust or
JavaScript verifier cannot reproduce from the same document. An offline
verifier would then report tampering on an untouched episode.

Everything written before this release stays canonical version 1 and verifies
unchanged; entries written with `Ledger(canon=CANON_JCS)` declare version 2 in
the entry itself.
"""
import math
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from lokahi import (
    CANON_JCS,
    CANON_PY,
    Action,
    Ledger,
    Manifest,
    Signer,
    canonical,
    canonical_jcs,
    digest,
)
from lokahi_evidence import _entry_hash

KEY = b"canonical-jcs-test-signing-key"
NOW = 1_800_000_000.0

# Values whose IEEE 754 bit pattern is unambiguous, from RFC 8785 Appendix B.
RFC8785_BITS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
    ("41b3de4355555555", "333333333.3333333"),
]

# The same rules stated as decimals, matched against ECMAScript Number::toString.
RFC8785_DECIMALS = [
    (1.0, "1"),
    (50.0, "50"),
    (0.1, "0.1"),
    (1.5, "1.5"),
    (-2.25, "-2.25"),
    (-0.0, "0"),
    (1e20, "100000000000000000000"),
    (1e21, "1e+21"),
    (1e23, "1e+23"),
    (1e-6, "0.000001"),
    (1e-7, "1e-7"),
    (1.5e-10, "1.5e-10"),
    (float(2 ** 53), "9007199254740992"),
]


def _from_bits(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


@pytest.mark.parametrize("bits,expected", RFC8785_BITS)
def test_number_serialization_matches_rfc8785_bit_patterns(bits, expected):
    value = _from_bits(bits)
    assert canonical_jcs({"v": value}) == ('{"v":' + expected + "}").encode("utf-8")


@pytest.mark.parametrize("value,expected", RFC8785_DECIMALS)
def test_number_serialization_matches_ecmascript(value, expected):
    assert canonical_jcs({"v": value}) == ('{"v":' + expected + "}").encode("utf-8")


@pytest.mark.parametrize(
    "value,expected",
    [(1.0, "1"), (1e-7, "1e-7"), (-0.0, "0"), (0.5, "0.5"), (-2.25, "-2.25"), (50.0, "50")],
)
def test_float_divergence_from_version_1(value, expected):
    """The documented divergence: what version 1 gets wrong and version 2 fixes."""
    assert canonical_jcs({"v": value}) == ('{"v":' + expected + "}").encode("utf-8")
    if expected in ("1", "0"):
        assert canonical({"v": value}) != canonical_jcs({"v": value})


def test_document_shape_is_sorted_compact_utf8():
    body = {"b": [1, {"y": None, "x": True}], "a": "né", "c": 2.5}
    assert canonical_jcs(body) == '{"a":"né","b":[1,{"x":true,"y":null}],"c":2.5}'.encode("utf-8")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_refused(bad):
    with pytest.raises(ValueError):
        canonical_jcs({"v": bad})


def test_non_string_keys_are_refused():
    with pytest.raises(TypeError):
        canonical_jcs({1: "a"})


def test_unknown_canonical_version_fails_closed():
    with pytest.raises(ValueError):
        digest(99, {"a": 1})
    with pytest.raises(ValueError):
        Ledger(canon=99)


def _manifest():
    return Manifest(
        node="robot:canon-cell-01",
        informational=["joint_positions"],
        forbidden_targets=["unbounded_open_loop_torque"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 2, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:dirgha.example/canon",
        physical_envelope={"max_contact_force_n": 6.1, "workspace_bounds_m": [0, 2, 0, 2, 0, 2]},
    ).sign(Signer(KEY))


def _decision_chain(canon):
    manifest = _manifest()
    ledger = Ledger(canon=canon)
    for _ in range(3):
        ledger.append_event("compliant", ["within_manifest"], manifest.node, extra={"m": 0.1 + 0.2}, ts=NOW)
    return ledger


def test_version_1_entries_carry_no_canon_field_and_still_verify():
    ledger = _decision_chain(CANON_PY)
    assert all("canon" not in entry for entry in ledger.entries)
    assert ledger.verify() == (True, 3)


def test_version_2_entries_declare_themselves_and_verify():
    ledger = _decision_chain(CANON_JCS)
    assert all(entry["canon"] == CANON_JCS for entry in ledger.entries)
    assert ledger.verify() == (True, 3)


def test_version_2_changes_the_digest_of_a_float_bearing_entry():
    legacy = _decision_chain(CANON_PY)
    current = _decision_chain(CANON_JCS)
    assert legacy.entries[0]["hash"] != current.entries[0]["hash"]


def test_one_byte_tampering_breaks_version_2_chain():
    ledger = _decision_chain(CANON_JCS)
    ledger.entries[1]["m"] = 0.30000000000000004
    ok, index = ledger.verify()
    assert ok is False
    assert index == 1


def test_unreadable_canon_version_is_a_verification_failure_not_a_crash():
    ledger = _decision_chain(CANON_JCS)
    ledger.entries[0]["canon"] = 7
    assert ledger.verify() == (False, 0)


@pytest.mark.parametrize("canon", [CANON_PY, CANON_JCS])
def test_evidence_entry_hash_follows_the_entrys_declared_version(canon):
    ledger = _decision_chain(canon)
    for entry in ledger.entries:
        assert _entry_hash(entry) == entry["hash"]
