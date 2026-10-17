"""Configure a private, source-restricted phone gateway."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import GatewayClient, GatewayError
from .const import DOMAIN
from .model import gateway_url

SCHEMA = vol.Schema({vol.Required("url"): str, vol.Required("token"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})


class CiscoPhoneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            try:
                url = gateway_url(user_input["url"])
                token = user_input["token"].strip()
                if not token or "\n" in token or "\r" in token:
                    raise ValueError("Invalid token")
                await GatewayClient(async_get_clientsession(self.hass), url, token).snapshot()
            except ValueError:
                errors["base"] = "invalid_url"
            except GatewayError as err:
                errors["base"] = "invalid_auth" if err.status == "authentication rejected" else "cannot_connect"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Cisco phone controls", data={"url": url, "token": token})
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        errors = {}
        if user_input:
            try:
                url = gateway_url(user_input["url"])
                token = user_input["token"].strip()
                if not token or "\n" in token or "\r" in token:
                    raise ValueError("Invalid token")
                await GatewayClient(async_get_clientsession(self.hass), url, token).snapshot()
            except ValueError:
                errors["base"] = "invalid_url"
            except GatewayError as err:
                errors["base"] = "invalid_auth" if err.status == "authentication rejected" else "cannot_connect"
            else:
                entry = self._get_reconfigure_entry()
                if url != entry.unique_id:
                    return self.async_abort(reason="gateway_changed")
                return self.async_update_reload_and_abort(entry, data_updates={"url": url, "token": token})
        return self.async_show_form(step_id="reconfigure", data_schema=SCHEMA, errors=errors)
