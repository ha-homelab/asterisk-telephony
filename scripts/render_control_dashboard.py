#!/usr/bin/env python3
"""Render a private controls dashboard from actual HA registry IDs; never deploy."""
import argparse
import copy
import json
import os
from pathlib import Path
import re

DOMAIN = "cisco_phone_control"
KEY_DOMAINS = {
    **{key: "button" for key in ("dial", "answer", "hangup", "speaker", "send_message", "send_image", "timer_start", "timer_cancel", "alert_stop")},
    **{key: "switch" for key in ("dnd", "autoanswer", "alarm_enabled", *(f"weekday_{n}" for n in range(7)))},
    **{key: "select" for key in ("line", "dial_number")},
    **{key: "text" for key in ("title", "message", "image_path")},
    "alarm_time": "time", "timer_minutes": "number", "call_status": "sensor",
    "timer_remaining": "sensor", "schedule_status": "sensor",
}
REQUIRED_KEYS = frozenset(KEY_DOMAINS)
HANDS_FREE_KEYS = frozenset(("hands_free_enabled", "voice_status"))
KEY_DOMAINS.update(hands_free_enabled="binary_sensor", voice_status="sensor")


def entity_map(registry, entry_id):
    result = {}
    seen = set()
    for record in registry:
        if record.get("config_entry_id") != entry_id:
            continue
        parts = record.get("unique_id", "").split(":")
        if (record.get("platform") != DOMAIN or len(parts) != 3 or parts[0] != entry_id
                or not re.fullmatch(r"[0-9]{1,12}", parts[1]) or parts[2] not in KEY_DOMAINS
                or record.get("disabled_by") is not None):
            raise ValueError("Unexpected, disabled or foreign controls entity")
        primary, key = parts[1:]
        entity_id = record.get("entity_id", "")
        if not re.fullmatch(KEY_DOMAINS[key] + r"\.[a-z0-9_]+", entity_id) or entity_id in seen:
            raise ValueError("Invalid or duplicate actual entity ID")
        seen.add(entity_id)
        phone = result.setdefault(primary, {})
        if key in phone:
            raise ValueError("Duplicate controls identity")
        phone[key] = entity_id
    if not result or any(not REQUIRED_KEYS <= set(phone)
                         or set(phone) & HANDS_FREE_KEYS not in (set(), HANDS_FREE_KEYS)
                         for phone in result.values()):
        raise ValueError("Wait until every actual controls entity is registered")
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


def render(mapping, baseline=None):
    dashboard = copy.deepcopy(baseline) if baseline is not None else {"title": "Cisco controls", "views": []}
    if not isinstance(dashboard.get("views"), list):
        raise ValueError("Expected a dashboard with a views list")
    existing = {view.get("path") for view in dashboard["views"]}
    privacy = []
    for primary, entities in mapping.items():
        path = "controls-" + primary
        if path in existing:
            raise ValueError("A target handset tab already exists; review instead of overwriting")
        def card(title, keys):
            return {"type": "entities", "title": title, "show_header_toggle": False,
                    "entities": [{"entity": entities[key]} for key in keys]}
        def buttons(keys):
            return {"type": "grid", "columns": 2, "square": False, "cards": [
                {"type": "button", "entity": entities[key], "tap_action": {"action": "perform-action",
                 "perform_action": "button.press", "target": {"entity_id": entities[key]}}} for key in keys]}
        dashboard["views"].append({"title": "Cisco " + primary, "path": path, "icon": "mdi:phone-classic", "cards": [
            {"type": "vertical-stack", "cards": [card("Calls and availability", ("call_status", "line", "dial_number", "dnd", "autoanswer")), buttons(("dial", "answer", "hangup", "speaker"))]},
            {"type": "vertical-stack", "cards": [card("Messages and images", ("title", "message", "image_path")), buttons(("send_message", "send_image")),
                {"type": "markdown", "content": "Private PNG/JPEG files: `/config/phone-images` or `/media/phone-images`. Use the phone **Exit** key to dismiss a page. Speaker is a toggle; PBX DND and gateway auto-answer are separate from native phone settings."}]},
            card("Recurring alarm", ("alarm_enabled", "alarm_time", *(f"weekday_{n}" for n in range(7)))),
            {"type": "vertical-stack", "cards": [card("Countdown timer and alert", ("timer_minutes", "timer_remaining", "schedule_status")), buttons(("timer_start", "timer_cancel", "alert_stop")),
                {"type": "markdown", "content": "Timers and alarms keep ringing until you answer or reject on the phone, or press **Stop alert**. They never auto-answer or play audio. **Cancel timer** also stops its active timer alert; disabling an alarm stops its active alarm alert."}]},
        ]})
        if HANDS_FREE_KEYS <= set(entities):
            dashboard["views"][-1]["cards"].append({"type": "vertical-stack", "cards": [
                card("Hands-free voice", ("hands_free_enabled", "voice_status")),
                {"type": "markdown", "content": "Enabled is the saved preference; Voice status shows current progress. Use the administrator-only `cisco_phone_control.hands_free` action to enable or disable it. Waiting for idle does not prove a call is active. Disabling prevents future sessions; wait for cleanup before treating the phone as stopped."},
            ]})
        privacy.extend(entities[key] for key in ("title", "message", "image_path", "timer_remaining"))
    return {"dashboard.json": dashboard, "cisco_phone_control_privacy.yaml": {"recorder": {"exclude": {"entities": privacy}}}, "entity-map.json": mapping}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True, help="Private JSON result of config/entity_registry/list")
    parser.add_argument("--entry-id", required=True, help="Actual loaded cisco_phone_control config entry")
    parser.add_argument("--baseline-dashboard", type=Path, help="Optional private existing Lovelace config JSON")
    parser.add_argument("--output", type=Path, required=True, help="New private output directory; never overwritten")
    args = parser.parse_args()
    os.umask(0o077)
    if args.output.exists():
        parser.error("Output already exists; choose a new directory for review")
    registry = json.loads(args.registry.read_text())
    baseline = json.loads(args.baseline_dashboard.read_text()) if args.baseline_dashboard else None
    output = render(entity_map(registry, args.entry_id), baseline)
    args.output.mkdir(parents=True, mode=0o700)
    for name, value in output.items():
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
    print("Rendered private dashboard, exact Recorder additions and actual entity map; no live changes.")


if __name__ == "__main__":
    main()
