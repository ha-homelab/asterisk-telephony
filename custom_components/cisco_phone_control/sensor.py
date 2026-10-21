"""Status without exposing caller identities, channels or message contents."""
import math

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.util import dt as dt_util

from .entity import PhoneEntity, setup_entities
from .hands_free_api import STATUS_OPTIONS
from .hands_free_entity import HandsFreeEntity, setup_hands_free_entities


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [PhoneSensor(c, p, k, n) for k, n in (("call_status", "Call status"), ("timer_remaining", "Timer remaining"), ("schedule_status", "Schedule status"))])
    await setup_hands_free_entities(entry, async_add_entities, HandsFreeStatus)


class HandsFreeStatus(HandsFreeEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUS_OPTIONS)
    _attr_translation_key = "voice_status"

    def __init__(self, coordinator, primary):
        super().__init__(coordinator, primary, "voice_status", "Voice status")

    @property
    def available(self):
        return super().available and self.observation["actual"] is not None

    @property
    def native_value(self):
        observation = self.observation
        return observation["actual"] if observation else None

    @property
    def extra_state_attributes(self):
        observation = self.observation
        if observation is None:
            return None
        hands, voice = observation["hands_free"], observation["voice"]
        attributes = {key: hands[key] for key in (
            "enabled", "state", "session_seconds", "consecutive_failures", "retry_after_seconds", "error")}
        if voice is not None:
            attributes.update({key: voice[key] for key in (
                "phase", "phase_reason", "remaining_seconds", "turns_completed") if key in voice})
        return attributes


class PhoneSensor(PhoneEntity, SensorEntity):
    @property
    def local(self):
        return self.key != "call_status"

    @property
    def native_unit_of_measurement(self):
        return "s" if self.key == "timer_remaining" else None

    @property
    def native_value(self):
        if self.key == "timer_remaining":
            deadline = self.settings.get("timer_deadline")
            return max(0, math.ceil(deadline - dt_util.utcnow().timestamp())) if deadline else 0
        if self.key == "schedule_status":
            if not self.coordinator.last_update_success:
                return "Gateway unavailable; alert status unknown"
            alert = self.phone.get("alert")
            if (not alert or alert["state"] == "ended") and self.settings.get("timer_deadline") is not None:
                return self.settings["status"]
            if alert:
                reason = {"call_ended": "call ended", "stopped": "stopped", "not_observed": "not observed"}.get(alert.get("reason"))
                suffix = f": {reason}" if alert["state"] == "ended" and reason else ""
                return f"{alert['kind'].title()} {alert['state']}{suffix}"
            return self.settings["status"]
        if not self.phone["online"]:
            return "Offline"
        channels = self.phone["channels"]
        if not channels:
            return "Idle"
        return "Ringing" if any(c["state"] == "Ringing" for c in channels) else "In use"

    @property
    def extra_state_attributes(self):
        if self.key == "schedule_status":
            attrs = {"alarm_line": self.settings["alarm_line"], "timer_line": self.settings["timer_line"]}
            if self.phone and self.phone.get("alert"):
                attrs.update({key: self.phone["alert"].get(key) for key in ("kind", "state", "reason")})
            return attrs
        return None
