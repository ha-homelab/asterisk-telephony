"""Explicit bounded voice operations, independent of AMI inventory refresh."""
import math
import re


STATES = frozenset(("idle", "starting", "running", "stopping", "stopped", "cancelled",
                    "failed", "recovery_required", "external_owner"))
ERRORS = frozenset(("voice_state_unavailable", "voice_owner_unavailable", "voice_start_failed",
                   "voice_start_uncertain", "voice_signal_failed", "voice_session_failed",
                   "voice_stop_unconfirmed"))
FIELDS = frozenset(("schema_version", "phone", "line", "session_id", "state", "active",
                   "remaining_seconds", "stop_requested", "stop_confirmed", "error"))
DRY_FIELDS = frozenset(("schema_version", "phone", "line", "valid", "network_actions",
                       "microphone_started", "duration_seconds"))


class VoiceError(Exception):
    """Only fixed messages are exposed to service callers."""


def require(condition, message="The gateway returned invalid voice status; do not retry blindly"):
    if not condition:
        raise VoiceError(message)


def identifier(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def bounded_seconds(value, minimum=0):
    return (type(value) in (int, float) and minimum <= value <= 30
            and math.isfinite(value))


def validate_response(result, primary, lines, operation, request_id=None, session_id=None, dry_run=False):
    """Keep only a fixed, typed, correlated metadata shape; never return raw data."""
    require(isinstance(result, dict))
    if operation == "status":
        require(result.get("status") == "ok")
    else:
        require(result.get("status") == "accepted" and result.get("action_id") == request_id)
    value = result.get("voice")
    require(isinstance(value, dict))
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1
            and value.get("phone") == primary and value.get("line") in lines)
    if operation == "start" and dry_run is True:
        require(set(value) == DRY_FIELDS,
                "Voice dry-run unconfirmed; read status before any new attempt")
        duration = value["duration_seconds"]
        require(value["valid"] is True
                and type(value["network_actions"]) is int and value["network_actions"] == 0
                and value["microphone_started"] is False
                and bounded_seconds(duration) and duration > 0)
    else:
        require(set(value) == FIELDS)
        state, session = value["state"], value["session_id"]
        require(isinstance(state, str) and state in STATES
                and all(type(value[key]) is bool for key in ("active", "stop_requested", "stop_confirmed"))
                and value["active"] == (state in ("starting", "running", "stopping"))
                and ((state == "idle" and session is None) or
                     (state != "idle" and identifier(session, r"[0-9a-f]{32}")))
                and bounded_seconds(value["remaining_seconds"])
                and (value["error"] is None or isinstance(value["error"], str) and value["error"] in ERRORS))
        if operation == "start":
            require(state != "idle")
        if operation == "stop":
            require(session == session_id)
    safe = {"status": result["status"], "voice": dict(value)}
    if operation != "status":
        safe["action_id"] = request_id
    return safe


async def voice(coordinator, primary, line, operation, **options):
    """Use saved identity only; the gateway owns fresh admission and media checks."""
    phone = coordinator.phone(primary)
    require(phone is not None and identifier(line, r"2[0-9]{2}") and line in phone["lines"],
            "Choose a line belonging to the selected physical handset")
    require(operation in ("status", "start", "stop"), "Choose status, start or stop")
    allowed = {"status": set(), "start": {"request_id", "attended", "exclusive_media", "enable_microphone", "dry_run"},
               "stop": {"request_id", "session_id"}}[operation]
    require(set(options) <= allowed, "Unexpected voice operation fields")
    key = options.get("request_id")
    session = options.get("session_id")
    if operation != "status":
        require(identifier(key, r"[A-Za-z0-9_.:-]{8,128}"), "Provide a stable voice request ID (8 to 128 characters)")
    if operation == "start":
        require(not getattr(coordinator, "_closing", False), "The integration is unloading; do not start a voice session")
        require(all(options.get(flag) is True for flag in ("attended", "exclusive_media", "enable_microphone")),
                "Explicitly confirm attendance, exclusive phone audio and microphone use")
        require(type(options.get("dry_run", False)) is bool, "dry_run must be a boolean")
    if operation == "stop":
        require(identifier(session, r"[0-9a-f]{32}"), "Provide the exact 32-character voice session ID")
    try:
        if operation == "status":
            result = await coordinator.client.voice_status(line)
        else:
            values = {name: value for name, value in options.items() if name != "request_id"}
            result = await coordinator.client.action(line, "voice_" + operation, values, key)
    except Exception:
        # Neither raw HTTP data nor a private backend exception belongs in HA.
        raise VoiceError("Voice request unconfirmed; read status before any new attempt") from None
    return validate_response(result, primary, phone["lines"], operation, key, session, options.get("dry_run", False))
