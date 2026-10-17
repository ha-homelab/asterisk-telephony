"""Only destinations and lines actually advertised by the gateway."""
import re

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from .const import NUMBER_LABELS
from .entity import PhoneEntity, setup_entities


def label(number, data=None):
    """Use bounded gateway labels without changing the advertised choices."""
    names = []
    data = data if isinstance(data, dict) else {}
    phones = data.get("phones", [])
    local = {line for phone in phones if isinstance(phone, dict)
             for line in phone.get("lines", []) if isinstance(line, str)}
    services = data.get("services", [])
    if isinstance(services, list) and number not in local:
        for item in services:
            if (isinstance(item, dict) and item.get("number") == number
                    and number in data.get("dial_allowlist", [])
                    and isinstance(item.get("label"), str)
                    and re.fullmatch(r"[A-Za-z0-9][ -~]{0,79}", item["label"])
                    and item["label"] == item["label"].strip()):
                names.append(item["label"])
    for key in ("phones", "softphones"):
        entries = data.get(key, [])
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            lines = item.get("lines", []) if key == "phones" else [item.get("extension")]
            room = item.get("room")
            if (isinstance(lines, list) and number in lines and isinstance(room, str)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _'()-]{0,47}", room)
                    and room == room.strip()):
                names.append(room)
    name = names[0] if len(names) == 1 else NUMBER_LABELS.get(number)
    return f"{number} — {name}" if name else number


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
        return [label(number, self.coordinator.data) for number in self.numbers]

    @property
    def current_option(self):
        selected = self.settings[self.key]
        return label(selected, self.coordinator.data) if selected in self.numbers else None

    async def async_select_option(self, option):
        choices = {label(number, self.coordinator.data): number for number in self.numbers}
        if option not in choices:
            raise HomeAssistantError("Choose an advertised gateway destination")
        await self.coordinator.configure(self.primary, self.key, choices[option])
