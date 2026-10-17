"""Explicit actions, never optimistic call state."""
from homeassistant.components.button import ButtonEntity

from .entity import PhoneEntity, setup_entities

ACTIONS = {"dial": "Dial", "answer": "Answer", "hangup": "Hang up", "speaker": "Speaker / dial tone toggle",
           "send_message": "Send message", "send_image": "Send image", "timer_start": "Start timer", "timer_cancel": "Cancel timer",
           "alert_stop": "Stop alert"}


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [PhoneButton(c, p, k, n) for k, n in ACTIONS.items()])


class PhoneButton(PhoneEntity, ButtonEntity):
    @property
    def available(self):
        if self.key == "timer_cancel":
            return self.phone is not None
        return super().available

    async def async_press(self):
        c, p, key = self.coordinator, self.primary, self.key
        if key == "alert_stop":
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
