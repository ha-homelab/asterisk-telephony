"""Persistent countdown duration."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError

from .entity import PhoneEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [TimerMinutes(c, p, "timer_minutes", "Timer minutes")])


class TimerMinutes(PhoneEntity, NumberEntity):
    local = True
    _attr_native_min_value = 1
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self):
        return self.settings["timer_minutes"]

    async def async_set_native_value(self, value):
        if not 1 <= value <= 1440 or value != int(value):
            raise HomeAssistantError("Choose a whole number of minutes from 1 to 1440")
        await self.coordinator.configure(self.primary, "timer_minutes", int(value))
