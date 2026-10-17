"""Handset identity and dynamic entity registration."""
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class PhoneEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    local = False

    def __init__(self, coordinator, primary, key, name):
        super().__init__(coordinator)
        self.primary = primary
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}:{primary}:{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}:{primary}")},
            name=f"Cisco {primary}", manufacturer="Cisco", model="7945G",
        )

    @property
    def phone(self):
        return self.coordinator.phone(self.primary)

    @property
    def settings(self):
        return self.coordinator.settings[self.primary]

    @property
    def available(self):
        return self.phone is not None and (self.local or super().available)


async def setup_entities(entry, async_add_entities, factory):
    coordinator = entry.runtime_data
    seen = set()

    @callback
    def discover():
        entities = []
        for phone in coordinator.data["phones"]:
            if phone["id"] not in seen:
                seen.add(phone["id"])
                entities.extend(factory(coordinator, phone["id"]))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))
