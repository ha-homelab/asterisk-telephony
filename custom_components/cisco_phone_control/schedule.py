"""Persistent, at-most-once schedules; no replay of missed notifications."""
from datetime import time
import math
import re
import hashlib
import uuid


def defaults(primary):
    return {"line": primary, "dial_number": "630", "alarm_enabled": False,
            "alarm_line": primary, "alarm_configured": False, "schedule_requests": {},
            "alarm_time": "07:00", "weekdays": [0, 1, 2, 3, 4], "alarm_destination": "640",
            "timer_minutes": 5, "timer_destination": "640", "timer_deadline": None,
            "timer_id": None, "timer_line": primary, "last_alarm": None, "status": "Ready"}


def action_key(primary, occurrence):
    return "ha-schedule-" + hashlib.sha256(f"{primary}:{occurrence}".encode()).hexdigest()[:40]


def start_timer(settings, now, line=None):
    settings["timer_deadline"] = now.timestamp() + settings["timer_minutes"] * 60
    settings["timer_id"] = uuid.uuid4().hex
    settings["timer_line"] = line if line is not None else settings["line"]
    settings["status"] = "Timer running"


def pending(settings, primary, now, started_at):
    """Claim before I/O. Caller MUST persist changed settings before any action."""
    due = []
    deadline = settings.get("timer_deadline")
    if deadline is not None and now.timestamp() >= deadline:
        occurrence = "timer:" + settings["timer_id"]
        settings["timer_deadline"] = None
        if now.timestamp() - deadline <= 20 and deadline >= started_at:
            due.append(("timer", action_key(primary, occurrence), settings["timer_line"]))
            settings["status"] = "Timer claimed"
        else:
            settings["status"] = "Timer missed while unavailable"
    minute = now.strftime("%H:%M")
    occurrence = now.date().isoformat() + ":" + minute
    minute_start = now.replace(second=0, microsecond=0).timestamp()
    if (settings["alarm_enabled"] and now.weekday() in settings["weekdays"]
            and minute == settings["alarm_time"] and settings.get("last_alarm") != occurrence):
        settings["last_alarm"] = occurrence
        if now.second <= 20 and minute_start >= started_at:
            due.append(("alarm", action_key(primary, "alarm:" + occurrence), settings["alarm_line"]))
            settings["status"] = "Alarm claimed"
        else:
            settings["status"] = "Alarm missed while unavailable"
    return due


def validate_settings(saved):
    """Corrupt local schedule storage must fail closed, never enable a call."""
    if not isinstance(saved, dict):
        raise ValueError("Invalid schedule storage")
    result = {}
    for primary, values in saved.items():
        if not isinstance(primary, str) or not re.fullmatch(r"[0-9]{1,12}", primary) or not isinstance(values, dict):
            raise ValueError("Invalid schedule identity")
        clean = {**defaults(primary), **values}
        if "alarm_line" not in values:
            clean["alarm_line"] = clean["line"]
        if "alarm_configured" not in values:
            clean["alarm_configured"] = bool(clean["alarm_enabled"] or clean["alarm_time"] != "07:00" or clean["weekdays"] != [0, 1, 2, 3, 4])
        for key in ("line", "dial_number", "alarm_destination", "timer_destination", "timer_line", "alarm_line"):
            if not isinstance(clean[key], str) or not re.fullmatch(r"[0-9]{1,12}", clean[key]):
                raise ValueError("Invalid stored destination")
        if type(clean["alarm_enabled"]) is not bool:
            raise ValueError("Invalid stored alarm")
        if type(clean["alarm_configured"]) is not bool or not isinstance(clean["schedule_requests"], dict):
            raise ValueError("Invalid stored schedule operations")
        for key, request in clean["schedule_requests"].items():
            if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,80}", key)
                    or not isinstance(request, dict) or not {"fingerprint", "state"} <= set(request)
                    or set(request) - {"fingerprint", "state", "result"}
                    or request.get("result") not in (None, "not_configured")
                    or not re.fullmatch(r"[a-f0-9]{64}", str(request["fingerprint"]))
                    or request["state"] not in ("claimed", "complete")):
                raise ValueError("Invalid stored schedule request")
        days = clean["weekdays"]
        if not isinstance(days, list) or any(type(n) is not int or not 0 <= n <= 6 for n in days):
            raise ValueError("Invalid stored weekdays")
        if not isinstance(clean["alarm_time"], str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", clean["alarm_time"]):
            raise ValueError("Invalid stored alarm time")
        time.fromisoformat(clean["alarm_time"])
        if type(clean["timer_minutes"]) is not int or not 1 <= clean["timer_minutes"] <= 1440:
            raise ValueError("Invalid stored duration")
        deadline = clean["timer_deadline"]
        if deadline is not None:
            if type(deadline) not in (float, int) or not math.isfinite(deadline) or not isinstance(clean["timer_id"], str):
                raise ValueError("Invalid stored timer")
        result[primary] = clean
    return result
