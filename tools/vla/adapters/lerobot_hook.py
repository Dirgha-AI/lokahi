"""One gate evaluation over a LeRobot-style action chunk.

LeRobot policies (ACT, Diffusion Policy, pi0) hand back an action chunk as a
2-D array of shape `(horizon, action_dim)` once the batch dimension is dropped,
and as a plain list of flat rows before that. This module takes such a chunk,
slices each row through a caller-supplied mapping into the gate's dict format,
runs a single gate evaluation over the whole horizon, and returns the rows the
caller may still execute.

Nothing here imports numpy or torch. A row or chunk that carries a callable
`.tolist()` — numpy array, torch tensor, or anything else that provides it — is
converted by calling it; plain lists and tuples are used as they are. A float32
tensor keeps its float32 rounding, which is what the robot would have received
anyway.

Mapping format
--------------
`mapping` says where each gate field lives in a row, as a half-open
`(start, stop)` pair or a `slice`:

    mapping = {
        "ee_pos": (0, 3),        # row[0:3]  -> position
        "ee_quat": (3, 7),       # row[3:7]  -> quaternion (w, x, y, z)
        "joint_pos": (7, 13),    # row[7:13] -> joint positions
        "duration_ms": 20.0,     # a number is a constant for every row;
                                 # a (start, stop) pair instead reads it per row
    }

Omitted keys are omitted from the gate element. `ee_pos` is the only field the
gate cannot do anything without, so a mapping that leaves it out produces a
structural refusal rather than a quiet pass.

Return value
------------
`(allowed, result)`:

- `result` is the `VLADecisionResult` for the whole chunk, so the caller can
  log verdict, margins and the recovery hint.
- `allowed` is a fresh list of plain `list[float]` rows, in the caller's own
  format, covering exactly the part of the chunk that may be executed:
  every row when the verdict is `COMPLIANT`, the prefix the gate clamped to
  when it is `CLAMPED`, and an empty list when the chunk was refused. An empty
  list means *do not execute anything* — it is not "no opinion".

Timing
------
The gate judges freshness against the observation the chunk was conditioned
on. Pass `obs_ts_ns` whenever the chunk was produced before this call; the
default treats the chunk as observed at the moment of the call, which is only
correct in a synchronous loop where the policy just ran on a fresh frame.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from ..vla_gate import VERDICT_COMPLIANT

GATE_FIELDS = ("ee_pos", "ee_quat", "joint_pos")


def filter_action_chunk(
    gate: Any,
    chunk: Any,
    *,
    mapping: Mapping[str, Any],
    robot_state: Mapping[str, Any],
    wrench: Mapping[str, Any] | None = None,
    obs_ts_ns: int | None = None,
    now_ts_ns: int | None = None,
    clock: Any = None,
    default_duration_ms: float | None = None,
    source: str = "lerobot",
) -> tuple[list[list[float]], Any]:
    """Gate one action chunk and return the rows still allowed, plus the result."""
    if not isinstance(robot_state, Mapping):
        raise ValueError(
            "robot_state is required: without it the gate cannot sweep the chunk "
            "through the workspace or hold against the current pose"
        )
    if not isinstance(mapping, Mapping):
        raise ValueError("mapping must be an object of field -> (start, stop)")

    rows = _as_rows(chunk)
    if not rows:
        raise ValueError("chunk is empty; there is nothing to filter")

    elements = [_element(row, mapping, default_duration_ms, index) for index, row in enumerate(rows)]

    now_ns = int(now_ts_ns) if now_ts_ns is not None else int((clock or time.time_ns)())
    observed_ns = int(obs_ts_ns) if obs_ts_ns is not None else now_ns

    result = gate.evaluate_step(
        obs_ts_ns=observed_ns,
        now_ts_ns=now_ns,
        robot_state=robot_state,
        action_chunk=elements,
        wrench=wrench,
        source=source,
    )

    if result.verdict == VERDICT_COMPLIANT:
        keep = len(rows)
    elif result.allowed and result.clamped_from_index is not None:
        keep = int(result.clamped_from_index)
    else:
        keep = 0
    return [list(row) for row in rows[:keep]], result


# ---------------------------------------------------------------------------
# chunk -> rows
# ---------------------------------------------------------------------------

def _as_rows(chunk: Any) -> list[list[float]]:
    value = _tolist(chunk)
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            "chunk must be a list/tuple of rows, or an object exposing .tolist()"
        )
    rows = list(value)
    if (
        len(rows) == 1
        and isinstance(rows[0], (list, tuple))
        and len(rows[0]) > 0
        and isinstance(_tolist(rows[0][0]), (list, tuple))
    ):
        rows = list(rows[0])
    return [_flat_row(row, index) for index, row in enumerate(rows)]


def _tolist(value: Any) -> Any:
    converter = getattr(value, "tolist", None)
    if callable(converter):
        return converter()
    return value


def _flat_row(row: Any, index: int) -> list[float]:
    values = _tolist(row)
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            f"chunk[{index}] must be a flat row of numbers, got {type(values).__name__}"
        )
    out: list[float] = []
    for position, item in enumerate(values):
        if isinstance(_tolist(item), (list, tuple)):
            raise ValueError(
                f"chunk[{index}][{position}] is nested; drop the batch dimension before filtering"
            )
        try:
            out.append(float(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"chunk[{index}][{position}] is not a number") from exc
    return out


# ---------------------------------------------------------------------------
# row -> gate element
# ---------------------------------------------------------------------------

def _element(
    row: Sequence[float],
    mapping: Mapping[str, Any],
    default_duration_ms: float | None,
    index: int,
) -> dict[str, Any]:
    element: dict[str, Any] = {}
    for key in GATE_FIELDS:
        if key in mapping and mapping[key] is not None:
            element[key] = _select(row, mapping[key], index, key)

    duration = mapping.get("duration_ms", default_duration_ms)
    if duration is not None:
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            element["duration_ms"] = float(duration)
        else:
            element["duration_ms"] = _select(row, duration, index, "duration_ms")[0]
    return element


def _select(row: Sequence[float], spec: Any, index: int, key: str) -> list[float]:
    if isinstance(spec, slice):
        values = row[spec]
    elif isinstance(spec, (list, tuple)):
        if not spec or not all(isinstance(bound, int) and not isinstance(bound, bool) for bound in spec):
            raise ValueError(f"mapping[{key}] must be a (start, stop) pair of integers")
        if len(spec) == 2:
            start, stop = spec
            values = row[start:stop]
        else:
            values = [row[position] for position in spec]
    else:
        raise ValueError(f"mapping[{key}] must be a (start, stop) pair or a slice")

    if not values:
        raise ValueError(
            f"mapping[{key}] selected no values from chunk[{index}], which has {len(row)} numbers"
        )
    return [float(value) for value in values]
