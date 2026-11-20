"""LOKAHI: checks what an AI agent is about to do against rules its community wrote, before it runs.

Install with `pip install lokahi` (add `lokahi[signatures]` for Ed25519 keys).
The engine is importable as `lokahi`, for example `from lokahi import Manifest, Signer, evaluate`.
"""
import os as _os
import sys as _sys

# The tools import each other by module name (for example `lokahi_consent`).
# Appending this folder keeps that working when installed, without shadowing
# anything already on the path.
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.append(_HERE)

from .lokahi import *  # noqa: E402,F401,F403
from .lokahi import __version__  # noqa: E402,F401
