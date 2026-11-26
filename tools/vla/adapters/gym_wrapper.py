"""Put the LOKAHI VLA gate in front of a Gym-style environment.

`VLAGatedEnv` wraps anything with `.reset()` and `.step(action)` — a real
`gymnasium.Env`, one element of a Gymnasium `VectorEnv`, or a three-method stub
in a test harness — and never imports gymnasium to do it. Only those two
methods are required; every other attribute is delegated to the wrapped env, so
`action_space`, `observation_space`, `spec`, `render()` and `close()` keep
working.

Contract for callers
--------------------
`reset(**kwargs)` returns `(observation, info)`. The wrapped env may return a
bare observation (old Gym API) or an `(observation, info)` pair; both are
accepted, and the observation is remembered as "the previous observation"
together with the clock reading at which it arrived.

`step(action)` runs the gate before the robot is touched:

    1. `to_chunk(action)` turns the caller's action into the gate's chunk
       format, `[{"ee_pos", "ee_quat", "joint_pos", "duration_ms"}, ...]`.
       The default translator accepts exactly that format and nothing else.
    2. `state_from_obs(observation)` and `wrench_from_obs(observation)` read
       the robot state and the contact wrench out of the observation the gate
       is judging against. The defaults use `observation["robot_state"]` (or
       the observation itself when it carries `ee_pos`) and
       `observation["wrench"]`.
    3. The gate decides, and only then is the environment stepped:
         - allowed: `env.step(from_chunk(allowed_chunk, result))`, where
           `allowed_chunk` is the clamped prefix when the verdict is `CLAMPED`.
           The result is always returned as the five-element Gymnasium tuple
           `(observation, reward, terminated, truncated, info)`. An env that
           returns the four-element Gym API tuple `(obs, reward, done, info)`
           is normalised with `terminated=done, truncated=False`.
         - refused: the wrapped env is not called at all. The previous
           observation is returned with `reward=0.0`, `terminated=False`,
           `truncated=False`.
    4. Both paths set `info["lokahi_vla"]` to the full decision — verdict,
       state, interlock, violated invariants, margins, recovery hint, gate
       evaluation time, clamped index — so a training loop can log
       interventions without importing the gate.

Cost-reporting envs (Safety-Gymnasium and the rest of the constrained-RL
family) return six values, `(obs, reward, cost, terminated, truncated, info)`,
where `cost` is that step's constraint violation. The cost is never dropped:
the caller always gets the five-element tuple back, and the step's cost is
surfaced as `info["lokahi_cost"]`, beside `info["lokahi_vla"]`. A refused step
on such an env reports `lokahi_cost: 0.0` — the env was not stepped, so no
constraint was violated — but only once the wrapped env has been seen to
report a cost at all, so an ordinary four- or five-value env never grows a
cost key it does not have.

A refusal is not an exception. Crashing the training loop on every
intervention pushes people to remove the gate; returning the previous
observation with the verdict in `info` keeps the loop running and keeps the
intervention visible. What it never does is execute an action the gate
refused.

Translation failures fail closed too. When `to_chunk`, `state_from_obs` or
`wrench_from_obs` raises `TypeError`, `ValueError` or `KeyError`, the step is
refused with `adapters.translation_failed:<detail>` in `violated_invariants`
and the environment is not stepped. An exception raised by the gate itself is
not caught: that is a gate bug, and nothing has run when it surfaces.

The gate is stateful. An `E_STOP` latch survives every later step until a
named human calls `human_reset(operator)`, which this class forwards rather
than reimplementing.

The wrench is part of the contract. A manifest that declares
`physical_envelope.max_contact_force_n` or `.max_joint_torque_nm` configures a
ceiling the gate must enforce, and the gate refuses a step it has no measurement
for (`wrench:wrench_unverifiable`). `wrench_from_obs` is therefore not optional
when the envelope has a ceiling: an observation with no wrench, or one whose
wrench is unreadable, is refused exactly like any other missing input.

Example
-------
    gate = LokahiVLAGate.from_manifest(manifest, verifier=verify_raw_document)
    env = VLAGatedEnv(
        make_env(), gate,
        to_chunk=policy_output_to_chunk,
        wrench_from_obs=lambda observation: observation["wrench"],
    )
    observation, info = env.reset()
    observation, reward, terminated, truncated, info = env.step(policy(observation))
    if not info["lokahi_vla"]["allowed"]:
        log_refusal(info["lokahi_vla"])
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from ..vla_gate import (
    SOFT_STOP,
    STATE_INTERLOCK,
    VERDICT_CLAMPED,
    VERDICT_FORBIDDEN,
    VLADecisionResult,
)

LOCAL_INFO_KEY = "lokahi_vla"
LOCAL_COST_KEY = "lokahi_cost"

Clock = Callable[[], int]
Translator = Callable[[Any], list]
StateReader = Callable[[Any], Mapping[str, Any]]
WrenchReader = Callable[[Any], Mapping[str, Any] | None]


class VLAGatedEnv:
    """A Gym-style env whose actions are gated before they reach the robot."""

    def __init__(
        self,
        env: Any,
        gate: Any,
        clock: Clock | None = None,
        *,
        to_chunk: Translator | None = None,
        from_chunk: Callable[[list, VLADecisionResult], Any] | None = None,
        state_from_obs: StateReader | None = None,
        wrench_from_obs: WrenchReader | None = None,
        source: str = "vla",
    ) -> None:
        for method in ("reset", "step"):
            if not callable(getattr(env, method, None)):
                raise TypeError(f"wrapped env must have a callable .{method}()")
        if gate is None:
            raise ValueError("VLAGatedEnv requires a gate; an ungated env is the thing this wrapper exists to prevent")
        self.env = env
        self.gate = gate
        self.clock = clock if clock is not None else time.time_ns
        self.to_chunk = to_chunk if to_chunk is not None else _identity_chunk
        self.from_chunk = from_chunk if from_chunk is not None else _identity_action
        self.state_from_obs = state_from_obs if state_from_obs is not None else _observation_state
        self.wrench_from_obs = wrench_from_obs if wrench_from_obs is not None else _observation_wrench
        self.source = source
        self.observation: Any = None
        self.obs_ts_ns: int = 0
        self.last_result: VLADecisionResult | None = None
        self.steps = 0
        self.refusals = 0
        self.reports_cost = False

    # -- gym surface --------------------------------------------------------

    @property
    def unwrapped(self) -> Any:
        return getattr(self.env, "unwrapped", self.env)

    def reset(self, **kwargs: Any) -> tuple[Any, dict]:
        produced = self.env.reset(**kwargs)
        if isinstance(produced, (tuple, list)) and len(produced) == 2:
            observation, info = produced
        else:
            observation, info = produced, {}
        self.observation = observation
        self.obs_ts_ns = int(self.clock())
        self.last_result = None
        return observation, dict(info or {})

    def step(self, action: Any) -> tuple[Any, float, bool, bool, dict]:
        if self.observation is None:
            raise RuntimeError("VLAGatedEnv.step() called before reset()")

        now_ns = int(self.clock())
        try:
            chunk = list(self.to_chunk(action))
            robot_state = self.state_from_obs(self.observation)
            wrench = self.wrench_from_obs(self.observation)
        except (TypeError, ValueError, KeyError) as exc:
            result = _refused(f"adapters.translation_failed:{exc}")
            chunk = None
        else:
            result = self.gate.evaluate_step(
                obs_ts_ns=self.obs_ts_ns,
                now_ts_ns=now_ns,
                robot_state=robot_state,
                action_chunk=chunk,
                wrench=wrench,
                source=self.source,
            )

        self.steps += 1
        self.last_result = result

        if not result.allowed or chunk is None:
            self.refusals += 1
            info: dict = {}
            observation, reward, terminated, truncated = self.observation, 0.0, False, False
            cost = 0.0 if self.reports_cost else None
        else:
            allowed_chunk = _allowed_chunk(chunk, result)
            produced = self.env.step(self.from_chunk(allowed_chunk, result))
            observation, reward, terminated, truncated, info, cost = _normalise_step_result(produced)
            self.observation = observation
            self.obs_ts_ns = int(self.clock())
            if cost is not None:
                self.reports_cost = True

        info = dict(info or {})
        if cost is not None:
            info[LOCAL_COST_KEY] = cost
        info[LOCAL_INFO_KEY] = decision_info(result)
        return observation, reward, terminated, truncated, info

    def human_reset(self, operator: str, now_ts_ns: int | None = None) -> dict:
        """Clear a latch through the gate's own named-human path."""
        return self.gate.human_reset(operator, now_ts_ns=now_ts_ns)

    def close(self) -> Any:
        closer = getattr(self.env, "close", None)
        return closer() if callable(closer) else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.env, name)

    def __repr__(self) -> str:
        return f"VLAGatedEnv({self.env!r}, refusals={self.refusals}/{self.steps})"


# ---------------------------------------------------------------------------
# result handling
# ---------------------------------------------------------------------------

def decision_info(result: VLADecisionResult) -> dict:
    """The loggable form of a decision. `safe_action` is left out: it is large,
    and its content is already summarised by the verdict and the clamped index."""
    return {
        "allowed": result.allowed,
        "verdict": result.verdict,
        "state": result.state,
        "interlock": result.interlock,
        "violated_invariants": list(result.violated_invariants),
        "margins": dict(result.margins),
        "recovery_hint": dict(result.recovery_hint),
        "gate_eval_us": result.gate_eval_us,
        "clamped_from_index": result.clamped_from_index,
    }


def _allowed_chunk(chunk: list, result: VLADecisionResult) -> list:
    if result.verdict == VERDICT_CLAMPED and isinstance(result.safe_action, Mapping):
        actions = result.safe_action.get("actions")
        if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes)):
            return list(actions)
    return chunk


def _normalise_step_result(produced: Any) -> tuple[Any, float, bool, bool, dict, float | None]:
    if not isinstance(produced, (tuple, list)):
        raise TypeError(
            f"wrapped env .step() must return a tuple, got {type(produced).__name__}"
        )
    parts = list(produced)
    if len(parts) == 6:
        observation, reward, cost, terminated, truncated, info = parts
        cost = float(cost)
    elif len(parts) == 5:
        observation, reward, terminated, truncated, info = parts
        cost = None
    elif len(parts) == 4:
        observation, reward, terminated, info = parts
        truncated = False
        cost = None
    else:
        raise ValueError(
            f"wrapped env .step() returned {len(parts)} values; expected the 4-element "
            "(obs, reward, done, info), 5-element (obs, reward, terminated, truncated, info) "
            "or 6-element (obs, reward, cost, terminated, truncated, info) form"
        )
    return observation, float(reward), bool(terminated), bool(truncated), info, cost


def _refused(reason: str) -> VLADecisionResult:
    """A refusal the gate never saw, because the input never became a chunk."""
    return VLADecisionResult(
        allowed=False,
        verdict=VERDICT_FORBIDDEN,
        state=SOFT_STOP,
        safe_action=None,
        violated_invariants=[reason],
        margins={},
        recovery_hint={
            "state": SOFT_STOP,
            "violated_classes": ["structural"],
            "requires_human_reset": False,
            "action": "hold_and_replan",
        },
        gate_eval_us=0.0,
        interlock=STATE_INTERLOCK[SOFT_STOP],
        clamped_from_index=None,
    )


# ---------------------------------------------------------------------------
# default translators
# ---------------------------------------------------------------------------

def _identity_chunk(action: Any) -> list:
    if isinstance(action, Mapping) and "actions" in action:
        action = action["actions"]
    if not isinstance(action, (list, tuple)):
        raise TypeError(
            "default to_chunk expects a list of gate-format action dicts; "
            "pass to_chunk= for any other policy output format"
        )
    return list(action)


def _identity_action(chunk: list, result: VLADecisionResult) -> list:
    return chunk


def _observation_state(observation: Any) -> Mapping[str, Any]:
    if isinstance(observation, Mapping):
        nested = observation.get("robot_state")
        if isinstance(nested, Mapping):
            return nested
        if "ee_pos" in observation:
            return observation
    raise ValueError(
        "observation carries no robot_state or ee_pos; pass state_from_obs= to build one"
    )


def _observation_wrench(observation: Any) -> Mapping[str, Any] | None:
    if isinstance(observation, Mapping):
        wrench = observation.get("wrench")
        if isinstance(wrench, Mapping):
            return wrench
    return None
