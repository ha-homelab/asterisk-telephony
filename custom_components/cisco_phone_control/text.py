"""Volatile message fields; refuse writes while Recorder would persist them."""
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.recorder import DATA_INSTANCE

from .entity import PhoneEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [MessageInput(c, p, k, n) for k, n in (("title", "Message title"), ("message", "Message text"), ("image_path", "Image path"))])


class MessageInput(PhoneEntity, TextEntity):
    local = True
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0

    def __init__(self, *args):
        super().__init__(*args)
        self._attr_native_max = 48 if self.key == "title" else 250

    @property
    def native_value(self):
        return self.coordinator.inputs[self.primary][self.key]

    async def async_set_value(self, value):
        if len(value) > self._attr_native_max:
            raise HomeAssistantError("Input is too long")
        recorder = self.hass.data.get(DATA_INSTANCE)
        # Fail closed during Recorder initialization as well. Administrators can
        # use the send_message/send_image actions without stateful text inputs.
        if recorder is None or recorder.entity_filter is None or recorder.entity_filter(self.entity_id):
            raise HomeAssistantError("Exclude this actual text entity from Recorder and restart Home Assistant before entering private content")
        self.coordinator.inputs[self.primary][self.key] = value
        self.async_write_ha_state()
