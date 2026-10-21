"""Explicit persistent intent; independent of the strict finite voice contract."""
import math
import re


STATES = frozenset(("disabled", "waiting_idle", "starting", "running", "stopping", "backoff", "faulted"))
ERRORS = frozenset(("hands_free_state_unavailable", "hands_free_owner_unavailable",
    "hands_free_recovery_required", "hands_free_observation_failed",
    "hands_free_retry_exhausted", "hands_free_start_failed"))
FIELDS = frozenset(("schema_version", "phone", "line", "enabled", "state", "session_id",
    "session_seconds", "consecutive_failures", "retry_after_seconds", "error"))
DRY_FIELDS = frozenset(("schema_version", "phone", "line", "valid", "network_actions",
    "microphone_started", "duration_seconds"))
STATUS_OPTIONS = ("off", "waiting_for_idle", "starting", "running_not_ready", "ready",
    "listening", "thinking", "speaking", "stopping", "retry_wait", "failed", "recovery_required")


class HandsFreeError(Exception):
    """A fixed message without gateway response bodies or private exceptions."""


def require(condition, message="Invalid hands-free response; read status before another change"):
    if not condition:
        raise HandsFreeError(message)


def identifier(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def seconds(value, minimum=0, maximum=300):
    return type(value) in (int, float) and minimum <= value <= maximum and math.isfinite(value)


def validate_response(result, primary, lines, operation, request_id=None, dry_run=False):
    require(type(result) is dict)
    if operation == "status":
        require(result.get("status") == "ok")
    else:
        require(result.get("status") == "accepted" and result.get("action_id") == request_id)
    value = result.get("hands_free")
    require(type(value) is dict and type(value.get("schema_version")) is int
            and value["schema_version"] == 1 and value.get("phone") == primary
            and value.get("line") in lines)
    if operation == "enable" and dry_run:
        require(set(value) == DRY_FIELDS and value["valid"] is True
                and type(value["network_actions"]) is int and value["network_actions"] == 0
                and value["microphone_started"] is False and seconds(value["duration_seconds"], 30))
    else:
        require(set(value) == FIELDS)
        require(type(value["enabled"]) is bool and type(value["state"]) is str
                and value["state"] in STATES
                and (value["session_id"] is None or identifier(value["session_id"], r"[0-9a-f]{32}"))
                and seconds(value["session_seconds"], minimum=0) and value["session_seconds"] > 0
                and type(value["consecutive_failures"]) is int and 0 <= value["consecutive_failures"] <= 3
                and seconds(value["retry_after_seconds"], maximum=10)
                and (value["error"] is None or type(value["error"]) is str and value["error"] in ERRORS))
        require(value["state"] != "disabled" or value["enabled"] is False)
        require(value["state"] not in ("waiting_idle", "starting", "running", "backoff") or value["enabled"] is True)
        require(value["state"] != "running" or value["session_id"] is not None)
        require(value["state"] == "backoff" or value["retry_after_seconds"] == 0)
    safe = {"status": result["status"], "hands_free": dict(value)}
    if operation != "status":
        safe["action_id"] = request_id
    return safe


async def hands_free(coordinator, primary, line, operation, **options):
    phone = coordinator.phone(primary)
    require(phone is not None and identifier(line, r"2[0-9]{2}") and line in phone["lines"],
            "Choose a line belonging to the selected physical handset")
    require(operation in ("status", "enable", "disable"), "Choose status, enable or disable")
    allowed = {"status": set(), "enable": {"request_id", "enable_microphone", "exclusive_media", "dry_run"},
               "disable": {"request_id"}}[operation]
    require(set(options) <= allowed, "Unexpected hands-free operation fields")
    key = options.get("request_id")
    if operation != "status":
        require(identifier(key, r"[A-Za-z0-9_.:-]{8,128}"), "Provide a stable hands-free request ID (8 to 128 characters)")
    if operation == "enable":
        require(not getattr(coordinator, "_closing", False), "The integration is unloading; do not enable hands-free")
        require(options.get("enable_microphone") is True and options.get("exclusive_media") is True,
                "Explicitly authorize persistent microphone operation and exclusive phone audio")
        require(type(options.get("dry_run", False)) is bool, "dry_run must be a boolean")
    try:
        if operation == "status":
            result = await coordinator.client.hands_free_status(line)
        else:
            values = {k: v for k, v in options.items() if k != "request_id"}
            result = await coordinator.client.action(line, "voice_hands_free_" + operation, values, key)
    except Exception:
        raise HandsFreeError("Hands-free request unconfirmed; read status before another change") from None
    return validate_response(result, primary, phone["lines"], operation, key, options.get("dry_run", False))


def actual_status(hands, voice=None):
    """Map validated observations only; never infer an ordinary call from idle."""
    state = hands["state"]
    # The core retains the last transient error while waiting for its retry.
    if state == "backoff":
        return "retry_wait"
    if state == "faulted" or hands["error"] is not None:
        return "recovery_required" if hands["error"] in (
            "hands_free_owner_unavailable", "hands_free_recovery_required", "hands_free_state_unavailable") else "failed"
    if state in ("starting", "running", "stopping") and hands["session_id"] is not None:
        if voice is None or voice["session_id"] != hands["session_id"]:
            return None
    if state != "running":
        return {"disabled": "off", "waiting_idle": "waiting_for_idle", "starting": "starting",
                "stopping": "stopping", "backoff": "retry_wait"}[state]
    if not hands["session_id"] or voice is None or voice["session_id"] != hands["session_id"]:
        return None
    if voice["state"] == "recovery_required":
        return "recovery_required"
    if voice["error"] is not None or voice["state"] == "failed":
        return "failed"
    if voice["stop_requested"] or voice["stop_confirmed"] or voice["state"] == "stopping":
        return "stopping"
    if voice["state"] != "running" or voice["active"] is not True:
        return "running_not_ready"
    if voice["remaining_seconds"] <= 0:
        return "running_not_ready"
    phase = voice.get("phase")
    if phase == "armed":
        return "ready" if voice.get("phase_reason") == "wake_ready" else "running_not_ready"
    return {"preparing": "starting", "listening": "listening", "thinking": "thinking",
            "speaking": "speaking", "error": "failed"}.get(phase, "running_not_ready")
