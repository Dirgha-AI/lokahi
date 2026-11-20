# -*- coding: utf-8 -*-
"""Per-consumer tool access control (R1-10).

A tool ACL limits which consumer may call which tool, independently of the
manifest's own allow-list. The manifest says *what a tool is*; the ACL says
*who may reach it*. Both must permit a call.

Config shape::

    {
      "default": "deny",
      "consumers": {
        "team:water-ops": {"allow": ["read_meter", "cutoff_water_valve"]},
        "team:analytics": {"allow": ["read_meter*"]}
      }
    }

``default`` is ``deny`` or ``allow`` for consumers not listed. Patterns are
exact names or a single trailing ``*`` prefix match. A consumer entry may also
carry ``"deny": [...]`` which is evaluated first.

Standard library only.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Iterable, Optional


class ACLError(ValueError):
    """The ACL configuration is malformed."""


def _matches(patterns: Iterable[str], tool: str) -> bool:
    for pattern in patterns:
        if pattern == tool:
            return True
        if pattern.endswith("*") and fnmatch.fnmatchcase(tool, pattern):
            return True
    return False


@dataclass
class ToolACL:
    default: str = "deny"                      # deny | allow
    consumers: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default not in ("deny", "allow"):
            raise ACLError("default must be 'deny' or 'allow'")
        for consumer, rules in self.consumers.items():
            if not isinstance(consumer, str) or not consumer:
                raise ACLError("consumer ids must be non-empty strings")
            if not isinstance(rules, dict):
                raise ACLError(f"consumer {consumer!r} must map to an object")
            unknown = set(rules) - {"allow", "deny"}
            if unknown:
                raise ACLError(f"consumer {consumer!r} has unknown keys: {sorted(unknown)}")
            for key in ("allow", "deny"):
                if key in rules and not (
                    isinstance(rules[key], list) and all(isinstance(x, str) for x in rules[key])
                ):
                    raise ACLError(f"consumer {consumer!r}: {key} must be a list of strings")

    @classmethod
    def from_dict(cls, data: dict) -> "ToolACL":
        if not isinstance(data, dict):
            raise ACLError("ACL must be an object")
        return cls(
            default=str(data.get("default", "deny")),
            consumers=dict(data.get("consumers", {})),
        )

    @classmethod
    def load(cls, path: str) -> "ToolACL":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def allows(self, consumer: Optional[str], tool: str) -> bool:
        """True if `consumer` may call `tool` under this ACL."""
        if not consumer:
            return self.default == "allow"
        rules = self.consumers.get(consumer)
        if rules is None:
            # Wildcard consumer entry, if present.
            rules = self.consumers.get("*")
        if rules is None:
            return self.default == "allow"

        if _matches(rules.get("deny", []), tool):
            return False
        allow = rules.get("allow")
        if allow is None:
            return self.default == "allow"
        return _matches(allow, tool)
