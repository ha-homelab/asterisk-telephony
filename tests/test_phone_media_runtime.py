"""Real HA entity/service tests using only a modeled local gateway."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_phone_media import media_snapshot, playing

READY = importlib.util.find_spec("homeassistant") is not None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(READY, "requires optional pinned Home Assistant runtime")
class PhoneMediaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import HomeAssistant
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant import loader
        from custom_components.cisco_phone_control.coordinator import PhoneCoordinator
        from custom_components.cisco_phone_control.media_player import PhoneMediaPlayer
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        loader.async_setup(self.hass)
        self.entry = ConfigEntry(domain="cisco_phone_control", title="Synthetic gateway",
                                 unique_id="http://127.0.0.1:18084", version=1, minor_version=1,
                                 source="user", options={}, discovery_keys=MappingProxyType({}),
                                 subentries_data=[], data={"url": "http://127.0.0.1:18084", "token": "synthetic"})
        self.data = media_snapshot()
        self.client = SimpleNamespace(snapshot=AsyncMock(side_effect=lambda: copy.deepcopy(self.data)),
                                      action=AsyncMock(return_value={"status": "accepted"}))
        self.c = PhoneCoordinator(self.hass, self.entry, self.client)
        self.c.store = SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
        self.c.prepare_inventory(self.data)
        self.c.async_set_updated_data(copy.deepcopy(self.data))
        self.entry.runtime_data = self.c
        self.player = PhoneMediaPlayer(self.c, "201")

    async def asyncTearDown(self):
        self.c.close()
        await self.hass.async_stop()
        self.tmp.cleanup()

    def publish(self):
        self.c.async_set_updated_data(copy.deepcopy(self.data))

    async def test_shared_device_and_honest_supported_features(self):
        from custom_components.cisco_phone_control.button import PhoneButton
        from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerEntityFeature as F
        button = PhoneButton(self.c, "201", "dial", "Dial")
        self.assertEqual(self.player.device_info, button.device_info)
        self.assertEqual(self.player.unique_id, f"{self.entry.entry_id}:201:media_player")
        self.assertEqual(self.player.device_class, MediaPlayerDeviceClass.SPEAKER)
        self.assertEqual(self.player.supported_features, F.PLAY_MEDIA | F.SELECT_SOURCE | F.BROWSE_MEDIA | F.STOP)

    async def test_catalog_and_no_arbitrary_urls_or_optimistic_playing(self):
        from homeassistant.exceptions import HomeAssistantError
        self.assertEqual(self.player.source_list, ["708 — Toddler music", "709 — Lullabies"])
        catalog = await self.player.async_browse_media()
        self.assertEqual([item.media_content_id for item in catalog.children], ["708", "709"])
        self.assertTrue(all(item.can_play and not item.can_expand for item in catalog.children))
        self.c.settings["201"]["line"] = "202"
        await self.player.async_select_source("708 — Toddler music")
        self.client.action.assert_awaited_once_with("201", "media_play", {"source": "708", "line": "202"}, None)
        self.assertEqual(self.player.state, "idle")
        self.assertIsNone(self.player.media_title)
        self.client.action.reset_mock()
        for media_type, media_id in (("music", "https://example.invalid/music.mp3"), ("url", "708"), ("music", "630")):
            with self.assertRaises(HomeAssistantError):
                await self.player.async_play_media(media_type, media_id)
        for options in ({"enqueue": "add"}, {"announce": True}):
            with self.assertRaises(HomeAssistantError):
                await self.player.async_play_media("music", "708", **options)
        self.client.action.assert_not_awaited()

    async def test_manual_secondary_line_radio_updates_and_clears_metadata(self):
        playing(self.data, line="202")
        self.publish()
        self.assertEqual(self.player.state, "playing")
        self.assertEqual(self.player.media_title, "Toddler music")
        self.assertEqual(self.player.media_content_id, "708")
        self.assertEqual(self.player.extra_state_attributes["active_line"], "202")
        self.assertNotIn("uniqueid", self.player.extra_state_attributes)
        self.assertNotIn("channel", self.player.extra_state_attributes)
        self.data = media_snapshot()
        self.publish()
        self.assertEqual(self.player.state, "idle")
        self.assertIsNone(self.player.source)
        self.assertIsNone(self.player.media_content_id)

    async def test_stop_keeps_observed_secondary_identity_despite_selected_line(self):
        identity = playing(self.data, line="202")
        self.publish()
        self.c.settings["201"]["line"] = "201"
        await self.player.async_media_stop()
        self.client.action.assert_awaited_once_with("201", "media_stop", {
            key: identity[key] for key in ("source", "line", "channel", "uniqueid")}, None)

    async def test_stop_refuses_replacement_radio_before_dispatch(self):
        from homeassistant.exceptions import HomeAssistantError
        playing(self.data, uniqueid="123.4")
        self.publish()
        playing(self.data, uniqueid="123.5")
        with self.assertRaises(HomeAssistantError):
            await self.player.async_media_stop()
        self.client.action.assert_not_awaited()

    async def test_active_source_change_busy_and_unknown_are_refused(self):
        from homeassistant.exceptions import HomeAssistantError
        for state, reason in (("playing", None), ("buffering", None), ("blocked", "call"), ("blocked", "dnd"), ("unknown", "unmarked")):
            self.data = media_snapshot()
            if state in ("playing", "buffering"):
                playing(self.data, state=state)
            else:
                self.data["phones"][0]["media"].update(state=state, reason=reason)
            self.publish()
            if state == "blocked":
                self.assertEqual(self.player.state, "idle")
                self.assertIsNone(self.player.media_title)
            with self.assertRaises(HomeAssistantError):
                await self.player.async_play_media("music", "709")
        self.client.action.assert_not_awaited()
        self.assertIsNone(self.player.state)

    async def test_supervised_voice_handoff_is_left_to_the_gateway(self):
        self.data["phones"][0]["media"].update(state="blocked", reason="voice")
        self.publish()
        await self.player.async_play_media("music", "708")
        self.client.action.assert_awaited_once_with("201", "media_play", {"source": "708", "line": "201"}, None)

    async def test_legacy_offline_and_stale_remain_unavailable(self):
        from homeassistant.exceptions import HomeAssistantError
        self.data["phones"][0].pop("media")
        self.data["phones"][0].pop("capabilities")
        self.publish()
        self.assertFalse(self.player.available)
        self.data = media_snapshot()
        self.data["phones"][0]["online"] = False
        self.publish()
        self.assertFalse(self.player.available)
        with self.assertRaises(HomeAssistantError):
            await self.player.async_play_media("music", "708")
        self.data["phones"][0]["online"] = True
        self.publish()
        self.assertTrue(self.player.available)
        for malformed in (None, "media", {"media": True}):
            self.data["phones"][0]["capabilities"] = malformed
            self.publish()
            self.assertFalse(self.player.available)
        self.data["phones"][0]["capabilities"] = ["media"]
        self.publish()
        self.c.async_set_update_error(RuntimeError("synthetic gateway unavailable"))
        self.assertFalse(self.player.available)
        self.client.action.assert_not_awaited()

    async def test_unknown_action_outcome_is_not_retried(self):
        from homeassistant.exceptions import HomeAssistantError
        from custom_components.cisco_phone_control.api import GatewayError
        self.client.action.side_effect = GatewayError("outcome unknown", "synthetic")
        with self.assertRaises(HomeAssistantError):
            await self.player.async_play_media("music", "708")
        self.client.action.assert_awaited_once()
        self.assertEqual(self.player.state, "idle")

    async def test_unload_during_fresh_read_prevents_dispatch(self):
        from homeassistant.exceptions import HomeAssistantError
        async def closing_read():
            self.c.close()
            return copy.deepcopy(self.data)
        self.client.snapshot.side_effect = closing_read
        with self.assertRaises(HomeAssistantError):
            await self.player.async_play_media("music", "708")
        self.client.action.assert_not_awaited()

    async def test_real_platform_registry_services_and_browse(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.helpers import entity_registry, device_registry, area_registry, floor_registry, label_registry, issue_registry
        template = copy.deepcopy(self.data["phones"][0])
        self.data["phones"] = []
        for index in range(10):
            phone = copy.deepcopy(template)
            phone.update(id=str(201 + 2 * index), lines=[str(201 + 2 * index), str(202 + 2 * index)])
            self.data["phones"].append(phone)
        self.data["phones"][-1]["online"] = False
        self.data["phones"][-1]["media"].update(state="blocked", reason="offline")
        target = Path(self.tmp.name) / "custom_components/cisco_phone_control"
        target.parent.mkdir()
        shutil.copytree(ROOT / "custom_components/cisco_phone_control", target, ignore=shutil.ignore_patterns("__pycache__"))
        device_registry.async_setup(self.hass)
        for registry in (floor_registry, label_registry, area_registry, device_registry, entity_registry, issue_registry):
            await registry.async_load(self.hass, load_empty=True)
        from homeassistant.auth import auth_manager_from_config
        self.hass.auth = await auth_manager_from_config(self.hass, [], [])
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        # Exercise HA's real platform/services without binding a listening port.
        with patch("homeassistant.components.http.HomeAssistantHTTP.async_bind", new_callable=AsyncMock), patch("custom_components.cisco_phone_control.GatewayClient", return_value=self.client), patch("custom_components.cisco_phone_control.async_get_clientsession", return_value=None):
            self.assertTrue(await async_setup_component(self.hass, "cisco_phone_control", {}))
        await self.hass.async_block_till_done()
        registry = entity_registry.async_get(self.hass)
        player_id = registry.async_get_entity_id("media_player", "cisco_phone_control", f"{self.entry.entry_id}:201:media_player")
        dial_id = registry.async_get_entity_id("button", "cisco_phone_control", f"{self.entry.entry_id}:201:dial")
        self.assertIsNotNone(player_id)
        self.assertEqual(registry.async_get(player_id).device_id, registry.async_get(dial_id).device_id)
        records = entity_registry.async_entries_for_config_entry(registry, self.entry.entry_id)
        players = [record for record in records if record.domain == "media_player"]
        self.assertEqual(len(players), 10)
        self.assertEqual(len({record.device_id for record in players}), 10)
        for record in players:
            primary = record.unique_id.split(":")[-2]
            button = registry.async_get_entity_id("button", "cisco_phone_control", f"{self.entry.entry_id}:{primary}:dial")
            self.assertEqual(record.device_id, registry.async_get(button).device_id)
        offline_id = registry.async_get_entity_id("media_player", "cisco_phone_control", f"{self.entry.entry_id}:219:media_player")
        self.assertEqual(self.hass.states.get(offline_id).state, "unavailable")
        self.client.action.assert_not_awaited()
        await self.hass.services.async_call("media_player", "play_media", {
            "entity_id": player_id, "media_content_type": "music", "media_content_id": "708"}, blocking=True)
        self.client.action.assert_awaited_once_with("201", "media_play", {"source": "708", "line": "201"}, None)
        self.assertEqual(self.hass.states.get(player_id).state, "idle")
        response = await self.hass.services.async_call("media_player", "browse_media", {"entity_id": player_id}, blocking=True, return_response=True)
        self.assertEqual([item.media_content_id for item in response[player_id].children], ["708", "709"])
        identity = playing(self.data, line="202")
        await self.entry.runtime_data.async_refresh()
        self.client.action.reset_mock()
        await self.hass.services.async_call("media_player", "media_stop", {"entity_id": player_id}, blocking=True)
        self.client.action.assert_awaited_once_with("201", "media_stop", {
            key: identity[key] for key in ("source", "line", "channel", "uniqueid")}, None)
        self.assertEqual(self.hass.states.get(player_id).state, "playing")
        self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))


if __name__ == "__main__":
    unittest.main()
