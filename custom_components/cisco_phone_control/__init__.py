"""Local Cisco controls and schedules; existing Asterisk/Assist setup stays intact."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_admin_service

import voluptuous as vol

from .api import GatewayClient
from .const import DOMAIN, PLATFORMS
from .coordinator import PhoneCoordinator
from .scheduling_api import SCHEMA as SCHEDULE_SCHEMA, schedule
from .voice_api import VoiceError, voice

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
BASE = {vol.Required("entry_id"): str, vol.Required("phone"): str, vol.Optional("title", default="Home Assistant"): vol.All(str, vol.Length(max=48))}


def literal_boolean(value):
    if type(value) is not bool:
        raise vol.Invalid("Use a literal boolean for voice consent")
    return value


VOICE_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str, vol.Required("phone"): str,
    vol.Required("line"): str, vol.Required("operation"): vol.In(("status", "start", "stop")),
    vol.Optional("request_id"): str, vol.Optional("session_id"): str,
    vol.Optional("attended"): literal_boolean,
    vol.Optional("exclusive_media"): literal_boolean,
    vol.Optional("enable_microphone"): literal_boolean,
    vol.Optional("dry_run"): literal_boolean,
})


async def async_setup(hass, config):
    def resolve(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED or not hasattr(entry, "runtime_data"):
            raise HomeAssistantError("Choose a loaded Cisco phone controls config entry")
        coordinator = entry.runtime_data
        primary = call.data.get("phone")
        if primary is None and call.service == "schedule":
            owners = [phone["id"] for phone in (coordinator.data or {}).get("phones", [])
                      if call.data["line"] in phone["lines"]]
            if len(owners) != 1:
                raise HomeAssistantError("The line has no unique handset in the saved gateway inventory")
            primary = owners[0]
        if coordinator.phone(primary) is None:
            raise HomeAssistantError("Choose a primary handset ID from this gateway")
        return coordinator, primary

    async def send(call):
        coordinator, primary = resolve(call)
        if call.service == "send_message":
            await coordinator.perform(primary, "message", {"title": call.data["title"], "text": call.data["text"]})
        else:
            await coordinator.send_image(primary, call.data["title"], call.data["path"])

    async def schedule_service(call):
        coordinator, primary = resolve(call)
        options = {key: value for key, value in call.data.items()
                   if key not in ("entry_id", "phone", "line", "operation")}
        return await schedule(coordinator, primary, call.data["line"], call.data["operation"], **options)

    async def voice_service(call):
        coordinator, primary = resolve(call)
        options = {key: value for key, value in call.data.items()
                   if key not in ("entry_id", "phone", "line", "operation")}
        try:
            return await voice(coordinator, primary, call.data["line"], call.data["operation"], **options)
        except VoiceError as error:
            raise HomeAssistantError(str(error)) from None

    async_register_admin_service(hass, DOMAIN, "voice", voice_service,
                                VOICE_SCHEMA, supports_response=SupportsResponse.ONLY)
    async_register_admin_service(hass, DOMAIN, "send_message", send,
                                vol.Schema({**BASE, vol.Required("text"): vol.All(str, vol.Length(min=1, max=2000))}))
    async_register_admin_service(hass, DOMAIN, "send_image", send,
                                vol.Schema({**BASE, vol.Required("path"): vol.All(str, vol.Length(min=1, max=250))}))
    async_register_admin_service(hass, DOMAIN, "schedule", schedule_service,
                                SCHEDULE_SCHEMA, supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass, entry):
    coordinator = PhoneCoordinator(hass, entry, GatewayClient(async_get_clientsession(hass), entry.data["url"], entry.data["token"]))
    await coordinator.async_initialize()
    entry.runtime_data = coordinator
    # Remove only this entry's obsolete sound selectors, preserving all other
    # registry records and identities. Legacy stored settings remain readable.
    registry = er.async_get(hass)
    obsolete = {f"{entry.entry_id}:{phone['id']}:{key}"
                for phone in coordinator.data["phones"]
                for key in ("alarm_destination", "timer_destination")}
    for record in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if record.platform == DOMAIN and record.domain == "select" and record.unique_id in obsolete:
            registry.async_remove(record.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
