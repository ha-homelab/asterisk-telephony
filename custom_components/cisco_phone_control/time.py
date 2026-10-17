"""An alarm time in Home Assistant's configured local timezone."""
from datetime import time

from homeassistant.components.time import TimeEntity

from .entity import PhoneEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [AlarmTime(c, p, "alarm_time", "Alarm time")])


class AlarmTime(PhoneEntity, TimeEntity):
    local = True

    @property
    def native_value(self):
        return time.fromisoformat(self.settings["alarm_time"])

    async def async_set_value(self, value):
        await self.coordinator.configure(self.primary, "alarm_time", value.strftime("%H:%M"))
