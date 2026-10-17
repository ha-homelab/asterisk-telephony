"""Atomic, line-owned schedules for administrator clients and automations."""
import copy
from datetime import time
import hashlib
import json
import math
import re

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .api import GatewayError
from .schedule import start_timer

OPERATIONS = ("status", "timer_start", "timer_cancel", "alarm_set", "alarm_enable", "alarm_disable", "toggle_saved_alarm", "alert_stop")
SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Optional("phone"): vol.Match(r"^[0-9]{1,12}$"),
    vol.Required("line"): vol.Match(r"^[0-9]{1,12}$"),
    vol.Required("operation"): vol.In(OPERATIONS),
    vol.Optional("request_id"): vol.Match(r"^[A-Za-z0-9_.:-]{8,80}$"),
    vol.Optional("minutes"): vol.All(int, vol.Range(min=1, max=1440)),
    vol.Optional("time"): vol.Match(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"),
    vol.Optional("weekdays"): vol.All([vol.All(int, vol.Range(min=0, max=6))], vol.Length(min=1, max=7)),
    vol.Optional("enabled", default=False): bool,
    vol.Optional("replace", default=False): bool,
})


def status(coordinator, primary, line, result="applied", gateway_fresh=True):
    settings = coordinator.settings[primary]
    deadline = settings["timer_deadline"]
    return {
        "schema_version": 1, "phone": primary, "line": line,
        "lines": list(coordinator.phone(primary)["lines"]),
        "timezone": coordinator.hass.config.time_zone, "gateway_fresh": gateway_fresh,
        "timer": {"line": settings["timer_line"], "id": settings["timer_id"],
                  "active": deadline is not None, "deadline": deadline,
                  "remaining_seconds": max(0, math.ceil(deadline - dt_util.utcnow().timestamp())) if deadline is not None else None},
        "alarm": {"line": settings["alarm_line"], "time": settings["alarm_time"],
                  "weekdays": list(settings["weekdays"]), "enabled": settings["alarm_enabled"],
                  "configured": settings["alarm_configured"]},
        "alert": copy.deepcopy(coordinator.phone(primary).get("alert")) if gateway_fresh else None,
        "status": settings["status"], "operation_result": result,
    }


async def schedule(coordinator, primary, line, operation, **options):
    """Persist each request before external I/O; ambiguous operations never replay."""
    if operation not in OPERATIONS:
        raise HomeAssistantError("Unsupported schedule operation")
    request_id = options.get("request_id")
    if operation != "status" and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,80}", request_id)):
        raise HomeAssistantError("A stable request_id is required for schedule changes")
    allowed = {"request_id", "enabled", "replace"}
    if operation == "timer_start":
        allowed.add("minutes")
        if type(options.get("minutes")) is not int or not 1 <= options["minutes"] <= 1440:
            raise HomeAssistantError("Timer minutes must be an integer from 1 to 1440")
    if operation == "alarm_set":
        allowed.update(("time", "weekdays"))
        value = options.get("time")
        days = options.get("weekdays")
        if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise HomeAssistantError("Alarm time must be HH:MM")
        time.fromisoformat(value)
        if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise HomeAssistantError("Weekdays must use Monday=0 through Sunday=6")
        options["weekdays"] = sorted(set(days))
    if set(options) - allowed:
        raise HomeAssistantError("Unexpected fields for this schedule operation")
    if options.get("replace", False) and operation not in ("timer_start", "alarm_set"):
        raise HomeAssistantError("Replacement is only available when setting a schedule")
    fingerprint = hashlib.sha256(json.dumps({"line": line, "operation": operation,
        **{key: value for key, value in options.items() if key != "request_id"}}, sort_keys=True).encode()).hexdigest()
    async with coordinator._settings_lock:
        if coordinator._closing:
            raise HomeAssistantError("Cisco phone controls are unloading")
        fresh = True
        try:
            data = await coordinator.client.snapshot()
            coordinator.prepare_inventory(data)
            coordinator.async_set_updated_data(data)
        except GatewayError:
            fresh = False
        phone = coordinator.phone(primary)
        if phone is None or line not in phone["lines"]:
            raise HomeAssistantError("The selected line does not belong to this handset")
        settings = coordinator.settings[primary]
        if operation == "status":
            return status(coordinator, primary, line, gateway_fresh=fresh)
        requests = settings["schedule_requests"]
        previous = requests.get(request_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise HomeAssistantError("request_id was already used for different schedule inputs")
            replay = "replayed" if previous["state"] == "complete" else "outcome_unknown"
            if previous.get("result") == "not_configured" and previous["state"] == "complete":
                replay = "not_configured"
            return status(coordinator, primary, line, replay, fresh)
        if not fresh and operation not in ("timer_cancel", "alarm_disable"):
            raise HomeAssistantError("Gateway unavailable; schedule change refused")
        alert = phone.get("alert") if fresh else None
        active = alert if alert and alert["state"] != "ended" else None
        replace = options.get("replace", False)
        stop = None
        stop_line = line
        result = "applied"
        candidate = copy.deepcopy(settings)
        if operation == "timer_start":
            if active and active["kind"] == "timer":
                raise HomeAssistantError("Stop the active timer alert before starting another countdown")
            if settings["timer_deadline"] is not None and not replace:
                raise HomeAssistantError("A timer is already pending; explicit replace is required")
            candidate["timer_minutes"] = options["minutes"]
            start_timer(candidate, dt_util.utcnow(), line)
        elif operation == "alarm_set":
            if settings["alarm_configured"] and settings["alarm_line"] != line and not replace:
                raise HomeAssistantError("The alarm belongs to the other line; explicit replace is required")
            if active and active["kind"] == "alarm":
                raise HomeAssistantError("Stop the active alarm before changing its schedule")
            candidate.update(alarm_line=line, alarm_time=options["time"], weekdays=options["weekdays"],
                             alarm_enabled=options.get("enabled", False), alarm_configured=True)
        elif operation in ("alarm_enable", "alarm_disable"):
            if settings["alarm_line"] != line:
                raise HomeAssistantError("The alarm belongs to the other line")
            candidate["alarm_enabled"] = operation == "alarm_enable"
            candidate["alarm_configured"] = True
            if operation == "alarm_disable":
                stop = active if active and active["kind"] == "alarm" else None
        elif operation == "toggle_saved_alarm":
            # A dialled service identifies the handset, not a new owner for its
            # saved alarm. Either sibling line may toggle the same saved alarm.
            if not settings["alarm_configured"]:
                result = "not_configured"
            else:
                if settings["alarm_line"] not in phone["lines"]:
                    raise HomeAssistantError("The saved alarm line is no longer on this handset")
                candidate["alarm_enabled"] = not settings["alarm_enabled"]
                stop_line = settings["alarm_line"]
                if not candidate["alarm_enabled"]:
                    stop = active if active and active["kind"] == "alarm" else None
        elif operation == "timer_cancel":
            if settings["timer_line"] != line:
                raise HomeAssistantError("The timer belongs to the other line")
            candidate.update(timer_deadline=None, status="Timer cancelled")
            stop = active if active and active["kind"] == "timer" else None
        elif operation == "alert_stop":
            stop = active
        if stop and stop["line"] != stop_line:
            raise HomeAssistantError("The active alert belongs to the other line")
        candidate["schedule_requests"][request_id] = {"fingerprint": fingerprint, "state": "claimed"}
        if result == "not_configured":
            candidate["schedule_requests"][request_id]["result"] = result
        coordinator.settings[primary] = candidate
        try:
            await coordinator._save()
        except Exception:
            coordinator.settings[primary] = settings
            raise HomeAssistantError("Schedule storage failed; no operation was sent") from None
        if stop:
            try:
                await coordinator.perform(primary, "alert_stop", {"line_filter": stop_line,
                    "kind_filter": stop["kind"], "alert_id_filter": stop["id"]},
                    "ha-stop-" + hashlib.sha256(f"{primary}:{request_id}".encode()).hexdigest()[:40])
            except HomeAssistantError:
                result = "outcome_unknown"
        elif not fresh:
            result = "outcome_unknown"  # Local cancellation persisted; the gateway alert is unknown.
        elif operation == "alert_stop":
            result = "not_active"
        if result != "outcome_unknown":
            candidate["schedule_requests"][request_id]["state"] = "complete"
            try:
                await coordinator._save()
            except Exception:
                candidate["schedule_requests"][request_id]["state"] = "claimed"
                result = "outcome_unknown"
        coordinator.async_update_listeners()
        return status(coordinator, primary, line, result, fresh)
