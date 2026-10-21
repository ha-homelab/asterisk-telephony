"""Persistent desired state; observing it never enables a microphone."""
from homeassistant.components.binary_sensor import BinarySensorEntity

from .hands_free_entity import HandsFreeEntity, setup_hands_free_entities


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_hands_free_entities(entry, async_add_entities, HandsFreeEnabled)


class HandsFreeEnabled(HandsFreeEntity, BinarySensorEntity):
    _attr_icon = "mdi:microphone-settings"

    def __init__(self, coordinator, primary):
        super().__init__(coordinator, primary, "hands_free_enabled", "Hands-free enabled")

    @property
    def is_on(self):
        observation = self.observation
        return observation["hands_free"]["enabled"] if observation else None
