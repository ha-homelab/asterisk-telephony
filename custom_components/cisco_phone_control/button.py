"""Explicit actions, never optimistic call state."""
from homeassistant.components.button import ButtonEntity

from .entity import PhoneEntity, setup_entities
from .hardware_volume import supported as supports_hardware_volume

ACTIONS = {"dial": "Dial", "answer": "Answer", "hangup": "Hang up", "speaker": "Speaker / dial tone toggle",
           "send_message": "Send message", "send_image": "Send image", "timer_start": "Start timer", "timer_cancel": "Cancel timer",
           "alert_stop": "Stop alert"}
HARDWARE_VOLUME = {
    "ringer_volume_up": ("Ringer volume up", "ringer", "up"),
    "ringer_volume_down": ("Ringer volume down", "ringer", "down"),
    "call_volume_up": ("Call volume up", "call", "up"),
    "call_volume_down": ("Call volume down", "call", "down"),
}
ACTIONS.update({key: value[0] for key, value in HARDWARE_VOLUME.items()})


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [PhoneButton(c, p, k, n) for k, n in ACTIONS.items()])


class PhoneButton(PhoneEntity, ButtonEntity):
    @property
    def available(self):
        if self.key in HARDWARE_VOLUME:
            return super().available and supports_hardware_volume(self.phone) and self.phone.get("online") is True
        if self.key == "timer_cancel":
            return self.phone is not None
        return super().available

    async def async_press(self):
        c, p, key = self.coordinator, self.primary, self.key
        if key in HARDWARE_VOLUME:
            _, context, direction = HARDWARE_VOLUME[key]
            await c.perform(p, "hardware_volume", {"context": context, "direction": direction, "steps": 1})
        elif key == "alert_stop":
            await c.stop_alert(p)
        elif key.startswith("timer_"):
            await c.timer(p, key == "timer_start")
        elif key == "send_message":
            inputs = c.inputs[p]
            await c.perform(p, "message", {"title": inputs["title"], "text": inputs["message"]})
        elif key == "send_image":
            inputs = c.inputs[p]
            await c.send_image(p, inputs["title"], inputs["image_path"])
        else:
            values = ({"number": self.settings["dial_number"]} if key == "dial"
                      else {"mode": "toggle"} if key == "speaker" else {})
            await c.perform(p, key, values)
