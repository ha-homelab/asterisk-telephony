"""Read-only entities sharing existing physical handset device identities."""
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class HandsFreeEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, primary, key, name):
        super().__init__(coordinator)
        self.primary = primary
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.parent.entry.entry_id}:{primary}:{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.parent.entry.entry_id}:{primary}")},
            name=f"Cisco {primary}", manufacturer="Cisco", model="7945G")

    @property
    def observation(self):
        return self.coordinator.observation(self.primary)

    @property
    def available(self):
        return self.observation is not None


async def setup_hands_free_entities(entry, async_add_entities, factory):
    coordinator = getattr(entry.runtime_data, "hands_free", None)
    if coordinator is None:
        return
    seen = set()

    @callback
    def discover():
        entities = []
        for primary, observation in (coordinator.data or {}).items():
            if primary not in seen and observation is not None:
                seen.add(primary)
                entities.append(factory(coordinator, primary))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))
