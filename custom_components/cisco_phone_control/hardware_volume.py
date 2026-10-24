"""Physical volume key requests; no absolute level or audio-path inference."""


def supported(phone):
    return (isinstance(phone, dict) and isinstance(phone.get("capabilities"), list)
            and "hardware_volume" in phone["capabilities"])


def request(phone, line, values):
    if not supported(phone):
        raise ValueError("Upgrade the gateway to support physical phone volume")
    if phone.get("online") is not True:
        raise ValueError("The phone is offline")
    if set(values) - {"line", "context", "direction", "steps"}:
        raise ValueError("Use only line, context, direction and steps for physical volume")
    context, direction, steps = values.get("context"), values.get("direction"), values.get("steps", 1)
    if context not in ("ringer", "call") or direction not in ("up", "down"):
        raise ValueError("Choose ringer or call volume, and up or down")
    if type(steps) is not int or not 1 <= steps <= 5:
        raise ValueError("Volume steps must be an integer from 1 to 5")
    if line not in phone["lines"]:
        raise ValueError("Selected line does not belong to this handset")
    result = {"line": line, "context": context, "direction": direction, "steps": steps}
    channels = phone["channels"]
    if context == "ringer":
        if channels or phone.get("alert") and phone["alert"].get("state") != "ended":
            raise ValueError("Ringer volume requires an idle phone; finish its call or alert first")
    else:
        if len(channels) != 1 or channels[0]["line"] != line or channels[0]["state"] != "Up":
            raise ValueError("Select the line with the phone's single connected call; resume a held call first")
        result.update(channel=channels[0]["name"], uniqueid=channels[0]["uniqueid"])
    # Endpoint state, voice ownership and the final call race are checked by
    # the gateway. AMI cannot establish the phone's local hook/output state.
    return result


def acknowledgement(result, requested):
    """Reject partial/ambiguous key acknowledgements without retrying a key."""
    if (not isinstance(result, dict)
            or result.get("context") != requested["context"]
            or result.get("direction") != requested["direction"]
            or type(result.get("requested_steps")) is not int
            or result["requested_steps"] != requested["steps"]
            or type(result.get("acknowledged_steps")) is not int
            or result["acknowledged_steps"] != requested["steps"]
            or result.get("level_known") is not False
            or "volume_level" not in result or result["volume_level"] is not None
            or result.get("audio_output") != "contextual"
            or result.get("physical_context_verified") is not False):
        raise ValueError("Physical volume outcome unknown; some keys may have applied. Do not retry blindly")
    return {key: result[key] for key in (
        "context", "direction", "requested_steps", "acknowledged_steps",
        "level_known", "volume_level", "audio_output", "physical_context_verified")}
