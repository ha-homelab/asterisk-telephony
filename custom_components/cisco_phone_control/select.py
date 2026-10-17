"""Only destinations and lines actually advertised by the gateway."""
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from .const import NUMBER_LABELS
from .entity import PhoneEntity, setup_entities


def label(number):
    return f"{number} — {NUMBER_LABELS[number]}" if number in NUMBER_LABELS else number


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [PhoneSelect(c, p, k, n) for k, n in (("line", "Line"), ("dial_number", "Dial destination"))])


class PhoneSelect(PhoneEntity, SelectEntity):
    local = True

    @property
    def numbers(self):
        if self.key == "line":
            return self.phone["lines"]
        return self.coordinator.data["dial_allowlist" if self.key == "dial_number" else "ring_allowlist"]

    @property
    def options(self):
        return [label(number) for number in self.numbers]

    @property
    def current_option(self):
        selected = self.settings[self.key]
        return label(selected) if selected in self.numbers else None

    async def async_select_option(self, option):
        choices = {label(number): number for number in self.numbers}
        if option not in choices:
            raise HomeAssistantError("Choose an advertised gateway destination")
        await self.coordinator.configure(self.primary, self.key, choices[option])
