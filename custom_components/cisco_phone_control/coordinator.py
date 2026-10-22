"""One fresh handset inventory and a persistent scheduler for each gateway."""
import asyncio
import base64
import copy
from datetime import timedelta
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GatewayError
from .const import DOMAIN
from .model import MEDIA_IDENTITY, media_observation, owned_channel, radio_sources, read_image, supports_media
from .schedule import defaults, pending, start_timer, validate_settings
from .storage import ScheduleStore, async_drain_task

_LOGGER = logging.getLogger(__name__)


class PhoneCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=5))
        self.entry = entry
        self.client = client
        self.settings = {}
        self.inputs = {}
        self.store = ScheduleStore(hass, 1, f"{DOMAIN}.{entry.entry_id}",
                                   private=True, atomic_writes=True)
        self._settings_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self._started_at = dt_util.utcnow().timestamp()
        self._tick_remove = None
        self._closing = False
        self.image_roots = [hass.config.path("phone-images"), "/media/phone-images"]

    async def async_initialize(self):
        saved = await self.store.async_load()
        self.settings = validate_settings((saved or {}).get("phones", {}))
        await self.async_config_entry_first_refresh()
        self._tick_remove = async_track_time_interval(self.hass, self._tick, timedelta(seconds=1))
        self.entry.async_on_unload(self.close)

    def close(self):
        self._closing = True
        if self._tick_remove:
            self._tick_remove()

    async def async_close(self) -> None:
        """Fence new changes and wait for the current persistence owner.

        The config entry must not finish unloading while an old executor write
        can still overwrite the same Store key after reload. Defer cancellation
        until that write settles, keeping HA's outer setup lock held as well.
        """
        self.close()

        async def drain_settings():
            async with self._settings_lock:
                pass

        drain = asyncio.create_task(drain_settings(), name="cisco-schedule-close")
        if await async_drain_task(drain):
            raise asyncio.CancelledError

    async def _async_update_data(self):
        try:
            data = await self.client.snapshot()
        except GatewayError as err:
            raise UpdateFailed(str(err)) from None
        self.prepare_inventory(data)
        return data

    def prepare_inventory(self, data):
        for phone in data["phones"]:
            primary = phone["id"]
            self.settings.setdefault(primary, defaults(primary))
            self.inputs.setdefault(primary, {"title": "Home Assistant", "message": "", "image_path": ""})

    def phone(self, primary):
        return next((p for p in (self.data or {}).get("phones", []) if p["id"] == primary), None)

    async def _persist_settings(self, payload) -> bool:
        """Finish an owned write before returning its deferred-cancel flag.

        Call while holding _settings_lock. A cancelled await cannot stop an
        executor write. Keep the save task shielded, including across repeated
        caller cancellation, so that the write never outlives its lock.
        Candidate callers must publish a successful write in memory before
        propagating the returned cancellation flag.
        """
        if self._closing:
            raise HomeAssistantError("Cisco phone controls are unloading")
        write = asyncio.create_task(
            self.store.async_save(copy.deepcopy(payload)), name="cisco-schedule-save")
        return await async_drain_task(write)

    async def _save(self) -> None:
        if await self._persist_settings({"phones": self.settings}):
            raise asyncio.CancelledError

    async def configure(self, primary, key, value):
        async with self._settings_lock:
            if self._closing:
                raise HomeAssistantError("Cisco phone controls are unloading")
            settings = self.settings[primary]
            candidate = copy.deepcopy(settings)
            # A deliberate time/day edit saves the displayed schedule without
            # enabling it. Capture ownership once; later edits keep that owner.
            saves_alarm = key in ("alarm_time", "weekdays") or key == "alarm_enabled" and value is True
            if saves_alarm and not candidate["alarm_configured"]:
                candidate["alarm_line"] = candidate["line"]
                candidate["alarm_configured"] = True
            candidate[key] = copy.deepcopy(value)
            candidate_settings = copy.deepcopy(self.settings)
            candidate_settings[primary] = candidate
            # Publish only a completed write, preserving the live row's identity.
            # A deferred cancellation may still mean the candidate was saved.
            cancelled = await self._persist_settings({"phones": candidate_settings})
            settings.update(copy.deepcopy(candidate))
        if not self._closing:
            self.async_update_listeners()
        if cancelled:
            raise asyncio.CancelledError
        if self._closing:
            raise HomeAssistantError("Cisco phone controls are unloading")
        if key == "alarm_enabled" and value is False:
            await self.stop_alert(primary, kind="alarm")

    async def timer(self, primary, start):
        async with self._settings_lock:
            if self._closing:
                raise HomeAssistantError("Cisco phone controls are unloading")
            settings = self.settings[primary]
            candidate = copy.deepcopy(settings)
            if start:
                start_timer(candidate, dt_util.utcnow())
            else:
                candidate["timer_deadline"] = None
                candidate["status"] = "Timer cancelled"
            candidate_settings = copy.deepcopy(self.settings)
            candidate_settings[primary] = candidate
            cancelled = await self._persist_settings({"phones": candidate_settings})
            settings.update(copy.deepcopy(candidate))
        if not self._closing:
            self.async_update_listeners()
        if cancelled:
            raise asyncio.CancelledError
        if self._closing:
            raise HomeAssistantError("Cisco phone controls are unloading")
        if not start:
            await self.stop_alert(primary, kind="timer")

    async def stop_alert(self, primary, kind=None):
        """Resolve the latest alert at the gateway; never guess from a selected line."""
        return await self.perform(primary, "alert_stop", {"kind_filter": kind})

    async def _tick(self, _now):
        if self._closing or self._settings_lock.locked():
            return
        async with self._settings_lock:
            now = dt_util.now()
            for primary in list(self.settings):
                if self.phone(primary) is None:
                    continue
                settings = self.settings[primary]
                previous = copy.deepcopy(settings)
                actions = pending(settings, primary, now, self._started_at)
                if settings != previous:
                    try:
                        await self._save()
                    except Exception:  # No persisted claim means no call, even on storage failure.
                        settings["status"] = "Storage unavailable; notification not sent"
                        _LOGGER.error("Cannot persist Cisco schedule claim; no call was sent")
                        continue
                for kind, key, line in actions:
                    try:
                        await self.perform(primary, "alert", {"kind": kind, "line": line}, key)
                        settings["status"] = "Alert accepted"
                    except HomeAssistantError:
                        settings["status"] = "Notification refused or outcome unknown; no retry"
                    if self._closing:
                        return
                    await self._save()
        self.async_update_listeners()

    async def perform(self, primary, action, values=None, action_id=None):
        async with self._action_lock:
            if self._closing:
                raise HomeAssistantError("Cisco phone controls are unloading")
            try:
                # Fresh server evidence, not an old HA state, chooses call ownership.
                fresh = await self.client.snapshot()
                if self._closing:
                    raise HomeAssistantError("Cisco phone controls are unloading")
                self.prepare_inventory(fresh)
                self.async_set_updated_data(fresh)
                phone = self.phone(primary)
                if phone is None:
                    raise HomeAssistantError("Handset is no longer present at the gateway")
                values = dict(values or {})
                line = values.get("line", self.settings[primary]["line"])
                if action != "alert_stop" and line not in phone["lines"]:
                    raise HomeAssistantError("Selected line does not belong to this handset")
                if action in ("media_play", "media_stop"):
                    sources = radio_sources(fresh)
                    media = media_observation(phone, sources)
                    if not supports_media(phone) or media is None:
                        raise HomeAssistantError("Upgrade the gateway to support phone media")
                    if not phone["online"]:
                        raise HomeAssistantError("The phone is offline")
                    if action == "media_play":
                        source = values.get("source")
                        if not isinstance(source, str) or source not in sources:
                            raise HomeAssistantError("Choose an advertised radio station")
                        if media["state"] in ("playing", "buffering"):
                            raise HomeAssistantError("Stop the current radio session before selecting a station")
                        # The gateway may hand off its own supervised voice leg.
                        # No other busy or uncertain state is a music target.
                        if media["state"] != "idle" and not (
                                media["state"] == "blocked" and media["reason"] == "voice"):
                            raise HomeAssistantError("The phone is busy or its media state is uncertain")
                        values = {"source": source, "line": line}
                    else:
                        # Retain the caller's observed identity. Never substitute
                        # a newer radio call, including one on the sibling line.
                        if (set(values) != set(MEDIA_IDENTITY)
                                or media["state"] not in ("playing", "buffering")
                                or any(values[key] != media[key] for key in MEDIA_IDENTITY)):
                            raise HomeAssistantError("The radio session changed; no call was stopped")
                elif action == "alert_stop":
                    if "alert" not in phone:
                        raise HomeAssistantError("Upgrade the gateway to support persistent alerts")
                    alert = phone["alert"]
                    if not alert or alert["state"] == "ended" or values.get("kind_filter") not in (None, alert["kind"]):
                        return {"status": "not_active"}
                    if values.get("line_filter") not in (None, alert["line"]) or values.get("alert_id_filter") not in (None, alert["id"]):
                        raise HomeAssistantError("The active alert changed; no other alert was stopped")
                    values = {"alert_id": alert["id"]}
                elif action == "alert":
                    if "alert" not in phone:
                        raise HomeAssistantError("Upgrade the gateway to support persistent alerts")
                    if phone["alert"] and phone["alert"]["state"] != "ended":
                        raise HomeAssistantError("An alert is already active or its outcome is unknown")
                    if values.get("kind") not in ("timer", "alarm"):
                        raise HomeAssistantError("Invalid scheduled alert kind")
                    values = {"kind": values["kind"], "line": line}
                elif action in ("answer", "hangup"):
                    values = owned_channel(phone, line, answer=action == "answer")
                elif action in ("dial", "ring"):
                    values["line"] = line
                    number = values.get("number" if action == "dial" else "destination")
                    if number not in fresh["dial_allowlist" if action == "dial" else "ring_allowlist"]:
                        raise HomeAssistantError("Destination is not allowed by the gateway")
                result = await self.client.action(primary, action, values, action_id)
            except (GatewayError, ValueError) as err:
                raise HomeAssistantError(str(err)) from None
        await self.async_request_refresh()
        return result

    async def send_image(self, primary, title, path):
        try:
            raw = await self.hass.async_add_executor_job(read_image, path, self.image_roots)
        except (OSError, ValueError):
            raise HomeAssistantError("Choose a regular PNG/JPEG up to 2 MiB in an approved phone-images folder") from None
        return await self.perform(primary, "image", {"title": title, "image_base64": base64.b64encode(raw).decode("ascii")})
