"""Read-only voice polling independent of AMI freshness and call action locks."""
import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .hands_free_api import hands_free, actual_status
from .voice_api import validate_response as validate_voice_response


POLL_SECONDS = 5
FRESH_SECONDS = 25
POLL_TIMEOUT = 20
REQUEST_TIMEOUT = 5


class HandsFreeCoordinator(DataUpdateCoordinator):
    def __init__(self, parent):
        super().__init__(parent.hass, logging.getLogger(__name__), config_entry=parent.entry,
                         name="cisco_hands_free", update_interval=None)
        self.parent = parent
        self._closing = False
        self._remove_timer = None
        self._refresh_task = None
        self.async_set_updated_data({})

    async def async_start(self):
        await self.async_refresh()
        self._remove_timer = async_track_time_interval(self.hass, self.request_refresh, timedelta(seconds=POLL_SECONDS))

    @callback
    def request_refresh(self, _now=None):
        if not self._closing and (self._refresh_task is None or self._refresh_task.done()):
            self._refresh_task = self.hass.async_create_task(self.async_refresh(), "cisco-hands-free-status")

    @callback
    def close(self):
        self._closing = True
        if self._remove_timer:
            self._remove_timer()
            self._remove_timer = None
        if self._refresh_task is not None:
            self._refresh_task.cancel()

    async def async_close(self):
        self.close()
        if self._refresh_task is not None:
            await asyncio.gather(self._refresh_task, return_exceptions=True)
            self._refresh_task = None

    async def _observe(self, phone):
        primary = phone["id"]
        async with asyncio.timeout(REQUEST_TIMEOUT):
            result = await hands_free(self.parent, primary, phone["lines"][0], "status")
        hands = result["hands_free"]
        observed_at = time.monotonic()
        voice = None
        # Only the current owned session can establish readiness. Old progress
        # remains unusable even if a replacement session is already running.
        if hands["session_id"] is not None and hands["state"] in ("starting", "running", "stopping"):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    reply = await self.parent.client.voice_status(hands["line"])
                candidate = validate_voice_response(reply, primary, phone["lines"], "status")["voice"]
                if candidate["session_id"] == hands["session_id"]:
                    voice = candidate
            except Exception:
                pass
        return {"hands_free": hands, "voice": voice, "actual": actual_status(hands, voice),
                "observed_at": observed_at}

    async def _async_update_data(self):
        phones = list((self.parent.data or {}).get("phones", []))
        result = {phone["id"]: None for phone in phones}
        semaphore = asyncio.Semaphore(4)

        async def observe(phone):
            async with semaphore:
                if self._closing:
                    return
                try:
                    result[phone["id"]] = await self._observe(phone)
                except Exception:
                    # An unavailable, old, or unsupported gateway is not Off.
                    result[phone["id"]] = None

        try:
            async with asyncio.timeout(POLL_TIMEOUT):
                await asyncio.gather(*(observe(phone) for phone in phones))
        except TimeoutError:
            pass
        return result

    def observation(self, primary):
        value = (self.data or {}).get(primary)
        if value is None or time.monotonic() - value["observed_at"] > FRESH_SECONDS:
            return None
        return value
