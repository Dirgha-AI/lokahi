"""Turning a refused chunk into a stop the arm can actually execute.

A clamp truncates the chunk at the last legal point, which leaves the arm to
absorb whatever velocity it already had. This module produces the stopping
profile instead: a jerk-limited deceleration from the last legal pose to rest,
sampled at the control period.

The core profile is analytic (two phases of jerk-limited ramp with a constant
deceleration between them, or a triangular profile when the current speed is too
low to reach peak deceleration), so it needs no extra package. Ruckig (MIT) is
used when it is installed, for a time-optimal profile computed against
velocity, acceleration and jerk limits at once:

    pip install "lokahi[vla-replan]"

Limits here are per axis. Coupled Cartesian and joint-space limits are what
Ruckig is for.

Credits: the optional backend is Ruckig (https://github.com/pantor/ruckig),
MIT. See CREDITS.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

REPLAN_EXTRA = "lokahi[vla-replan]"


class ReplanError(ValueError):
    """A stop profile cannot be produced for these inputs. Never a silent hold."""


@dataclass(frozen=True)
class Limits:
    max_acceleration: float
    max_jerk: float
    max_velocity: float | None = None

    def __post_init__(self) -> None:
        if self.max_acceleration <= 0.0:
            raise ReplanError("max_acceleration must be positive")
        if self.max_jerk <= 0.0:
            raise ReplanError("max_jerk must be positive")
        if self.max_velocity is not None and self.max_velocity <= 0.0:
            raise ReplanError("max_velocity must be positive when it is given")


@dataclass(frozen=True)
class Waypoint:
    time_s: float
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    acceleration: tuple[float, float, float]


def ruckig_available() -> bool:
    """Whether the optional Ruckig backend can be imported right now."""
    try:  # pragma: no cover - depends on the installed environment
        import ruckig  # noqa: F401
    except Exception:
        return False
    return True


def backends() -> dict[str, bool]:
    return {"analytic": True, "ruckig": ruckig_available()}


def _axis_profile(velocity: float, limits: Limits) -> tuple[float, float, float, float, float]:
    """Phase lengths and peak deceleration for one axis stopping from `velocity`."""
    speed = abs(velocity)
    if speed == 0.0:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    accel = limits.max_acceleration
    jerk = limits.max_jerk
    if speed > accel * accel / jerk:
        ramp = accel / jerk
        cruise = (speed - accel * accel / jerk) / accel
        return (ramp, cruise, ramp, accel, speed)
    peak = math.sqrt(speed * jerk)
    ramp = peak / jerk
    return (ramp, 0.0, ramp, peak, speed)


def _sample_axis(velocity: float, t: float, phases: tuple[float, float, float, float, float],
                 limits: Limits) -> tuple[float, float, float]:
    """(displacement, velocity, acceleration) at time `t` of a stopping profile."""
    ramp1, cruise, ramp3, peak, speed = phases
    if speed == 0.0:
        return (0.0, 0.0, 0.0)
    sign = math.copysign(1.0, velocity)
    jerk = limits.max_jerk
    accel = limits.max_acceleration if cruise > 0.0 else peak
    acceleration = min(accel, peak)

    if t <= 0.0:
        return (0.0, speed, 0.0)

    # Phase 1: jerk up to peak deceleration.
    first = min(t, ramp1)
    distance = speed * first - jerk * first ** 3 / 6.0
    velocity_now = speed - jerk * first ** 2 / 2.0
    acceleration_now = jerk * first
    if t <= ramp1:
        return (sign * distance, sign * velocity_now, -sign * acceleration_now)

    # Phase 2: hold peak deceleration.
    middle = min(t - ramp1, cruise)
    distance += velocity_now * middle - acceleration * middle ** 2 / 2.0
    velocity_now -= acceleration * middle
    acceleration_now = acceleration
    if t <= ramp1 + cruise:
        return (sign * distance, sign * velocity_now, -sign * acceleration_now)

    # Phase 3: release the deceleration to zero exactly at rest.
    last = min(t - ramp1 - cruise, ramp3)
    distance += velocity_now * last - acceleration * last ** 2 / 2.0 + jerk * last ** 3 / 6.0
    velocity_now -= acceleration * last - jerk * last ** 2 / 2.0
    acceleration_now = acceleration - jerk * last
    return (sign * distance, sign * max(0.0, velocity_now), -sign * max(0.0, acceleration_now))


def stop_duration(velocity: Sequence[float], limits: Limits) -> float:
    """Seconds until every axis is at rest."""
    longest = 0.0
    for component in velocity:
        ramp1, cruise, ramp3, _peak, _speed = _axis_profile(float(component), limits)
        longest = max(longest, ramp1 + cruise + ramp3)
    return longest


def stop_profile(position: Sequence[float], velocity: Sequence[float], limits: Limits,
                 control_period_s: float = 0.02,
                 max_duration_s: float = 5.0) -> list[Waypoint]:
    """A sampled deceleration from (position, velocity) to rest, in absolute positions."""
    if control_period_s <= 0.0:
        raise ReplanError("control_period_s must be positive")
    if len(position) != 3 or len(velocity) != 3:
        raise ReplanError("position and velocity must be 3-vectors")
    duration = stop_duration(velocity, limits)
    if duration > max_duration_s:
        raise ReplanError(
            f"a stop from this velocity takes {duration:.3f}s, beyond max_duration_s"
        )
    phases = [_axis_profile(float(component), limits) for component in velocity]
    steps = max(0, int(math.ceil(duration / control_period_s)))
    waypoints: list[Waypoint] = []
    for step in range(steps + 1):
        t = min(duration, step * control_period_s)
        displacements = []
        velocities = []
        accelerations = []
        for axis in range(3):
            displacement, speed, acceleration = _sample_axis(
                float(velocity[axis]), t, phases[axis], limits)
            displacements.append(float(position[axis]) + displacement)
            velocities.append(speed)
            accelerations.append(acceleration)
        waypoints.append(Waypoint(time_s=t, position=tuple(displacements),
                                  velocity=tuple(velocities), acceleration=tuple(accelerations)))
    return waypoints


def ruckig_stop_profile(position: Sequence[float], velocity: Sequence[float], limits: Limits,
                        control_period_s: float = 0.02,
                        max_duration_s: float = 5.0) -> list[Waypoint]:
    """Time-optimal stop through Ruckig. Requires the optional extra."""
    try:  # pragma: no cover - requires the extra
        from ruckig import InputParameter, OutputParameter, Result, Ruckig
    except Exception as exc:  # pragma: no cover
        raise ReplanError(
            f"the Ruckig backend needs ruckig: pip install '{REPLAN_EXTRA}'"
        ) from exc
    try:  # pragma: no cover - requires the extra
        if len(position) != 3 or len(velocity) != 3:
            raise ReplanError("position and velocity must be 3-vectors")
        otg = Ruckig(3, control_period_s)
        input_parameter = InputParameter(3)
        output_parameter = OutputParameter(3)
        input_parameter.current_position = [float(v) for v in position]
        input_parameter.current_velocity = [float(v) for v in velocity]
        input_parameter.current_acceleration = [0.0, 0.0, 0.0]
        input_parameter.target_position = [float(v) for v in position]
        input_parameter.target_velocity = [0.0, 0.0, 0.0]
        input_parameter.target_acceleration = [0.0, 0.0, 0.0]
        input_parameter.max_velocity = [limits.max_velocity or 10.0] * 3
        input_parameter.max_acceleration = [limits.max_acceleration] * 3
        input_parameter.max_jerk = [limits.max_jerk] * 3
        waypoints: list[Waypoint] = []
        elapsed = 0.0
        result = Result.Working
        while result == Result.Working and elapsed <= max_duration_s:
            result = otg.update(input_parameter, output_parameter)
            waypoints.append(Waypoint(
                time_s=elapsed,
                position=tuple(float(v) for v in output_parameter.new_position),
                velocity=tuple(float(v) for v in output_parameter.new_velocity),
                acceleration=tuple(float(v) for v in output_parameter.new_acceleration),
            ))
            output_parameter.pass_to_input(input_parameter)
            elapsed += control_period_s
        if elapsed > max_duration_s:  # pragma: no cover - depends on the limits given
            raise ReplanError("Ruckig did not reach rest within max_duration_s")
        return waypoints
    except ReplanError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ReplanError(f"Ruckig could not produce a stop profile: {exc}") from exc


def make_stop_profile(position: Sequence[float], velocity: Sequence[float], limits: Limits,
                      control_period_s: float = 0.02,
                      backend: str = "auto") -> list[Waypoint]:
    """Produce a stop profile with the best backend this deployment has."""
    if backend == "auto":
        backend = "ruckig" if ruckig_available() else "analytic"
    if backend == "ruckig":
        return ruckig_stop_profile(position, velocity, limits, control_period_s)
    if backend == "analytic":
        return stop_profile(position, velocity, limits, control_period_s)
    raise ReplanError(f"unknown replanning backend: {backend!r}")
