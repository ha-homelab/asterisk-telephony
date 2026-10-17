"""PBX DND, gateway auto-answer and local recurring alarm controls."""
from homeassistant.components.switch import SwitchEntity

from .const import WEEKDAYS
from .entity import PhoneEntity, setup_entities


async def async_setup_entry(hass, entry, async_add_entities):
    def build(c, p):
        return [PhoneSwitch(c, p, k, n) for k, n in (("dnd", "PBX do not disturb"), ("autoanswer", "Gateway auto-answer"), ("alarm_enabled", "Alarm enabled"))] + [PhoneSwitch(c, p, f"weekday_{day}", f"Alarm {name}") for day, name in enumerate(WEEKDAYS)]
    await setup_entities(entry, async_add_entities, build)


class PhoneSwitch(PhoneEntity, SwitchEntity):
    @property
    def local(self):
        return self.key not in ("dnd", "autoanswer")

    @property
    def is_on(self):
        if self.key.startswith("weekday_"):
            return int(self.key[-1]) in self.settings["weekdays"]
        return self.settings[self.key] if self.local else self.phone[self.key]

    @property
    def extra_state_attributes(self):
        if self.key == "dnd":
            return {"pending": self.phone["dnd_pending"]}
        return None

    async def _set(self, enabled):
        if not self.local:
            await self.coordinator.perform(self.primary, self.key, {"enabled": enabled})
        elif self.key.startswith("weekday_"):
            days = set(self.settings["weekdays"])
            day = int(self.key[-1])
            days.add(day) if enabled else days.discard(day)
            await self.coordinator.configure(self.primary, "weekdays", sorted(days))
        else:
            await self.coordinator.configure(self.primary, self.key, enabled)

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
