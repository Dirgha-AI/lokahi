# LOKAHI WebXR Teleoperation Fallback Runbook

When the LOKAHI cascade cannot reach a secure automated decision (timeout, exception, or explicit human-escalation trigger), the system enters **fail-secure state** and triggers a **WebXR teleoperation session**. A human operator dons a WebXR headset and takes manual control of the actuator/agent through an audited telepresence stream. All teleoperation actions are logged to the same tamper-evident hash-chained ledger.

## Overview

The WebXR fallback is the final safety net in the LOKAHI cascade:

```
Exception / Timeout / No Quorum
         │
         ▼
   Fail-Secure State
   (privileges revoked)
         │
         ▼
   WebXR Teleoperation
   (human manual control)
         │
         ▼
   Human Re-Arm
   (cryptographic signature)
         │
         ▼
   Operational Again
```

## When WebXR Teleoperation Is Triggered

| Trigger | Condition |
|---------|-----------|
| WASM sandbox crash | Layer 1 pre-flight module panics or becomes unresponsive |
| SLM judge timeout | Layer 2 inference exceeds `--slm-timeout` (default 5s) |
| SLM judge crash | Model returns unknown or unparseable output |
| Consent window expires | Fewer than `m` validator signatures before the deadline |
| Validator network unavailable | Cannot gather threshold signatures |
| Uncaught exception | Any layer raises an unexpected exception |
| Manual override | Operator explicitly requests fail-secure via WebXR console |

## Session Lifecycle

### 1. Initiation

When a fail-secure condition is detected, `WebXRTeleopBroker.trigger_teleoperation()` is called automatically by the MCP gateway's exception boundary. Alternatively, an operator can initiate a session manually via `initiate_session(operator)`.

**CLI:**
```bash
python tools/mcp_stdio_proxy.py \
    --manifest manifest.json \
    --key-file manifest.key \
    --validator-keys validators/approvers.json \
    --webxr-endpoint wss://webxr.example.com/teleop
```

**Session details returned:**
```json
{
  "session_id": "a1b2c3d4e5f6",
  "operator": "ops:alice",
  "webxr_endpoint": "wss://webxr.example.com/teleop",
  "started_at": 1800000000.0,
  "status": "awaiting_operator",
  "ledger_entry": { ... },
  "message": "WebXR teleoperation session initiated..."
}
```

### 2. Operator Connection

The human operator connects their WebXR headset to the endpoint URL. The session enters `active` state when the operator is connected and ready.

### 3. Audited Teleoperation Actions

Every operator action is recorded via `record_teleop_action(action_type, target, payload, signature)`:

| Action Type | Description | Example Payload |
|-------------|-------------|-----------------|
| `pose` | Operator updates actuator pose/position | `{"x": 1.0, "y": 2.0, "z": 3.0}` |
| `command` | Operator issues a direct command | `{"command": "close", "force_n": 5.0}` |
| `override` | Operator overrides an automated decision | `{"command": "safe_hold"}` |
| `rearm` | Operator requests system re-arm | `{"reason": "manual_clear"}` |
| `release` | Operator ends the teleop session | `{}` |

Each action is:
- Signed by the operator's key (optional but recommended)
- Recorded to the tamper-evident hash-chained ledger
- Bound to the session ID and operator identity

### 4. Re-Arm

When the human operator is ready to return the system to automated operation, they request re-arm via `request_rearm(rearm_signature)` from the WebXR console. The actual re-arm is completed by `FailSecureManager.rearm(rearm_signature)`, which:
1. Clears the secure state flag
2. Deactivates the teleoperation session
3. Issues a new execution privilege token
4. Records the re-arm to the ledger

The system **never auto-re-arms** — a human must explicitly authorize it.

### 5. Session Release

When teleoperation is complete, `release_session()` ends the session, records the duration and action count to the ledger, and clears the active session.

## Security Properties

- **No auto-re-arm:** The system stays in secure state until a human explicitly re-arms.
- **Cryptographic re-arm:** Re-arm requires a signature that is recorded to the ledger.
- **Audited telepresence:** Every teleop action is ledgered with operator identity, timestamp, and signature.
- **Privilege revocation:** On entering secure state, the execution privilege token is revoked immediately; no tool calls can proceed until re-arm.
- **Ledger integrity:** All WebXR events are recorded in the same hash-chained ledger as all other LOKAHI decisions, making the entire decision trail tamper-evident.

## Integration Points

### With FailSecureManager

```python
from lokahi_failsecure import FailSecureManager
from lokahi_webxr import WebXRTeleopBroker
from lokahi import Ledger

ledger = Ledger()
fsm = FailSecureManager(ledger, "node:actuator-01")
broker = WebXRTeleopBroker(
    endpoint="wss://webxr.example.com/teleop",
    ledger=ledger,
    manifest_node="node:actuator-01",
)

# On exception:
decision = fsm.handle_exception(exc, "slm_judge")
teleop = broker.trigger_teleoperation("wss://webxr.example.com/teleop")

# On human re-arm:
decision = fsm.rearm("operator-rearm-signature")
```

### With MCP Gateway

The gateway (`tools/mcp_stdio_proxy.py`) wraps all cascade layers in a try/except:
```python
try:
    return self._intercept_inner(rpc_msg, now)
except Exception as exc:
    decision = self.failsecure.handle_exception(exc, "cascade")
    if self.webxr is not None and not self.webxr.session:
        self.webxr.initiate_session(operator=self.default_operator, now=now)
    return self._record(rpc_msg, decision, ...)
```

CLI flag: `--webxr-endpoint wss://webxr.example.com/teleop`

## Runbook Checklist

### Pre-Deployment
- [ ] WebXR endpoint URL is configured and reachable
- [ ] Operator identities and keys are provisioned
- [ ] Re-arm authorization policy is defined (who may re-arm)
- [ ] Ledger storage is configured with sufficient retention
- [ ] Fail-secure behavior is tested in staging

### During Incident
- [ ] Confirm fail-secure state was entered (privileges revoked)
- [ ] Confirm WebXR session was initiated
- [ ] Operator connects WebXR headset and assumes control
- [ ] All teleop actions are being ledgered
- [ ] Resolve the underlying issue (SLM model, network, etc.)
- [ ] Operator requests re-arm with cryptographic signature
- [ ] System re-arms and resumes automated operation

### Post-Incident
- [ ] Review ledger entries for the incident window
- [ ] Verify all teleop actions are accounted for
- [ ] Confirm re-arm signature is valid and recorded
- [ ] Update runbook based on lessons learned
